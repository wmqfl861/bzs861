"""Bounded local process lifecycle. Persistent stops require an OS birth identity.

Linux uses pidfds; Windows uses a typed process handle. Other platforms fail
closed for persisted process control rather than trusting a recycled PID.
"""
from __future__ import annotations
import contextlib
import json
import math
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import time
from typing import Iterator
from . import __version__, config

MAX_PID = 2**31 - 1
MAX_READY = 4096


def valid_pid(pid: object) -> bool:
    return type(pid) is int and 1 < pid <= MAX_PID


def _kernel():
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CloseHandle.restype = w.BOOL
    k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    k.GetProcessTimes.restype = w.BOOL
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    k.TerminateProcess.restype = w.BOOL
    return k


def _win_birth(k, handle) -> str | None:
    import ctypes
    from ctypes import wintypes as w
    waited = k.WaitForSingleObject(handle, 0)
    if waited == 0:
        return None
    if waited != 258:
        raise OSError("cannot query process wait state")
    fields = [w.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(ctypes.byref(f) for f in fields)):
        raise OSError("cannot query process birth time")
    return f"win:{(fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime}"


def _win_creation_of(birth: object) -> int | None:
    """Creation FILETIME encoded in a birth identity, or None."""
    if isinstance(birth, str) and birth.startswith("win:") and birth[4:].isdigit():
        return int(birth[4:])
    return None


class _ProcessGone(Exception):
    """The pid no longer names any process (OpenProcess error 87)."""


class _ProcessDenied(Exception):
    """The process exists but this launcher may not open it (error 5)."""


class _RealWinApi:
    """ctypes-backed process operations; the tree stopper's only Windows surface."""

    # step 1 (identity): query limited + synchronize
    _QUERY_MASK = 0x1000 | 0x100000
    # step 2 (signalling): terminate, opened by PID while the query handle is held
    _TERMINATE_MASK = 1
    _SYSTEM_PROCESS_INFORMATION = 5  # NtQuerySystemInformation class
    _STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

    def __init__(self, kernel=None):
        import ctypes
        from ctypes import wintypes as w
        self._ctypes, self._w = ctypes, w
        self._k = kernel or _kernel()
        k = self._k
        k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        k.CreateToolhelp32Snapshot.restype = w.HANDLE
        k.Process32First.argtypes = [w.HANDLE, ctypes.c_void_p]
        k.Process32First.restype = w.BOOL
        k.Process32Next.argtypes = [w.HANDLE, ctypes.c_void_p]
        k.Process32Next.restype = w.BOOL
        k.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(w.FILETIME)]
        self._ntdll = ctypes.WinDLL("ntdll")
        self._ntdll.NtQuerySystemInformation.argtypes = [w.UINT, w.LPVOID, w.ULONG,
                                                         ctypes.POINTER(w.ULONG)]
        self._ntdll.NtQuerySystemInformation.restype = w.LONG  # NTSTATUS

    def snapshot(self):
        """Return (pid -> (parent pid, image name), completion FILETIME) or raise OSError.

        INVALID_HANDLE_VALUE and mid-table enumeration errors are failures:
        a silently partial table must never be treated as the whole truth.
        """
        ctypes, k = self._ctypes, self._k
        invalid = ctypes.c_void_p(-1).value
        snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == invalid:
            raise OSError("cannot snapshot the process table")
        entries = {}

        class Entry(ctypes.Structure):
            _fields_ = [("size", self._w.DWORD), ("usage", self._w.DWORD), ("pid", self._w.DWORD),
                        ("heap", ctypes.c_size_t), ("module", self._w.DWORD), ("threads", self._w.DWORD),
                        ("ppid", self._w.DWORD), ("priority", ctypes.c_long), ("flags", self._w.DWORD),
                        ("name", ctypes.c_char * 260)]
        try:
            e = Entry()
            e.size = ctypes.sizeof(Entry)
            more = k.Process32First(snap, ctypes.byref(e))
            if not more and ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise OSError("process enumeration failed")
            while more:
                entries[e.pid] = (e.ppid, e.name.split(b"\0", 1)[0].decode("mbcs", "replace"))
                more = k.Process32Next(snap, ctypes.byref(e))
                if not more and ctypes.get_last_error() != 18:
                    raise OSError("process enumeration failed mid-table")
        finally:
            k.CloseHandle(snap)
        ft = self._w.FILETIME()
        k.GetSystemTimeAsFileTime(ctypes.byref(ft))
        return entries, (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    def _open(self, pid, mask):
        ctypes = self._ctypes
        h = self._k.OpenProcess(mask, False, pid)
        if h:
            return h
        error = ctypes.get_last_error()
        if error == 87:
            raise _ProcessGone(pid)
        if error == 5:
            raise _ProcessDenied(pid)
        raise OSError(f"cannot open pid {pid} (error {error})")

    def open(self, pid):
        """Query+synchronize handle: enough to read identity, never to signal."""
        return self._open(pid, self._QUERY_MASK)

    def open_terminate(self, pid):
        """Terminate handle, requested by PID only after membership is verified."""
        return self._open(pid, self._TERMINATE_MASK)

    def creation_without_handle(self, pid):
        """Creation FILETIME for a pid this launcher cannot open, read from the
        system process-information table without opening anything.

        Returns None whenever the table cannot answer. The caller may use the
        value ONLY to exclude a process as obviously older than its nominal
        parent; this information never authorizes signalling.
        """
        ctypes, w = self._ctypes, self._w

        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", w.USHORT), ("max_length", w.USHORT),
                        ("buffer", ctypes.c_void_p)]

        class SystemProcessInformation(ctypes.Structure):
            # winternl.h SYSTEM_PROCESS_INFORMATION through the parent pid.
            # HANDLE fields use c_void_p so the layout tracks pointer width.
            _fields_ = [("next_offset", w.ULONG), ("thread_count", w.ULONG),
                        ("working_set_private", ctypes.c_ulonglong),
                        ("hard_fault_count", w.ULONG), ("thread_count_high_water", w.ULONG),
                        ("cycle_time", ctypes.c_ulonglong), ("creation_time", ctypes.c_ulonglong),
                        ("user_time", ctypes.c_ulonglong), ("kernel_time", ctypes.c_ulonglong),
                        ("image_name", UnicodeString), ("base_priority", w.LONG),
                        ("pid", ctypes.c_void_p), ("parent_pid", ctypes.c_void_p)]

        if not valid_pid(pid):
            return None
        ntdll, size = self._ntdll, 1 << 16
        for _ in range(8):  # bounded buffer-growth retries
            buffer = ctypes.create_string_buffer(size)
            status = ntdll.NtQuerySystemInformation(self._SYSTEM_PROCESS_INFORMATION,
                                                    buffer, size, None)
            if status == 0:
                offset = 0
                while True:
                    entry = ctypes.cast(ctypes.byref(buffer, offset),
                                        ctypes.POINTER(SystemProcessInformation)).contents
                    # A zeroed/hidden creation time (protected processes) is
                    # not evidence of age: treat it as unavailable.
                    if entry.pid == pid:
                        return int(entry.creation_time) if entry.creation_time > 0 else None
                    if not entry.next_offset:
                        return None
                    offset += entry.next_offset
            if status & 0xFFFFFFFF != self._STATUS_INFO_LENGTH_MISMATCH:
                return None
            size *= 2
        return None

    def creation(self, handle):
        import ctypes
        from ctypes import wintypes as w
        fields = [w.FILETIME() for _ in range(4)]
        if not self._k.GetProcessTimes(handle, *(ctypes.byref(f) for f in fields)):
            raise OSError("cannot query process creation time")
        return (fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime

    def exited(self, handle) -> bool:
        waited = self._k.WaitForSingleObject(handle, 0)
        if waited == 0:
            return True
        if waited == 258:
            return False
        raise OSError("cannot query process wait state")

    def terminate(self, handle) -> bool:
        return bool(self._k.TerminateProcess(handle, 0))

    def wait(self, handle, ms):
        waited = self._k.WaitForSingleObject(handle, int(ms))
        if waited == 0:
            return "signaled"
        if waited == 258:
            return "timeout"
        raise OSError("wait failed")

    def close(self, handle):
        self._k.CloseHandle(handle)


def _win_terminate_tree(root_pid: int, root_creation: int, *, api=None, deadline_s: float = 10.0) -> list[str]:
    """Stop a verified tree without treating a stale PID snapshot as identity.

    Pin the trusted root first, open candidate handles, then take a fresh table
    while those handles remain open. Every ancestry edge must still exist in
    that table and each child's creation must follow its verified parent's.
    Windows retains process IDs while handles exist; signals use handles opened
    by PID while the query handle is held, so reuse cannot redirect them. A
    snapshot completion timestamp alone cannot exclude reuse DURING
    enumeration. Parent-PID spoofing by an equally privileged process is outside
    this local operator contract; this is not a hostile-process sandbox.

    A snapshot entry created strictly before its nominal parent cannot be that
    parent's descendant (a true descendant's creation FILETIME is never earlier
    than its parent's, but the creation clock has finite granularity, so
    "later" can collapse into an equal tick); Windows never updates a child's
    recorded ppid, so strictly-older entries are strangers that reused a dead
    parent's PID and are excluded outright: not signalled and not unconfirmed.
    When the query handle is denied, the system process table may supply the
    creation time for this exclusion only - it never authorizes signalling.
    Equal-tick entries are never signalled: each keeps its original query
    handle, and only that same continuously-held handle entering the signaled
    state after the stop resolves that one member's exit doubt - final-table
    absence alone never does, and a resolved exit is never recorded as
    membership confirmation. Candidate descendants of an equal-tick entry are
    retained and recursively checked from the first snapshot the same way; any
    surviving, unknown or failing one keeps the stop unconfirmed, and the final
    table re-checks those parent PIDs for newcomers. A refused TerminateProcess
    (or a terminate-handle open failure) likewise proves nothing: the member's
    exit is confirmed only when the SAME still-held query handle is signaled;
    timeouts, wait failures and errors keep it unconfirmed.

    Missing/denied roots and descendants are never inferred to be safely gone.
    A final table check reports children born during the stop without signalling
    unverified newcomers. Orphans behind an intermediate process that vanished
    before inspection cannot be discovered by this mechanism; do not advertise
    a general process supervisor or zero-orphan guarantee.
    """
    if (not valid_pid(root_pid) or type(root_creation) is not int or root_creation <= 0
            or type(deadline_s) not in (int, float) or not math.isfinite(deadline_s)
            or not 0 < deadline_s <= 60):
        raise ValueError("invalid process identity or tree-stop deadline")
    api = api or _RealWinApi()
    deadline = time.monotonic() + deadline_s
    unconfirmed: list[str] = []
    opened: list = []
    # pid -> (parent PID, creation, depth, pinned query handle)
    candidates: dict[int, tuple[int, int, int, object]] = {}
    names: dict[int, str] = {}
    creations: dict[int, int] = {root_pid: root_creation}
    # Equal-tick members (creation == nominal parent's creation):
    # pid -> (nominal parent, parent creation, own creation, retained query
    # handle or None, scan-time message to defer when no handle was retained).
    equal_tick: dict[int, tuple[int, int, int, object, "str | None"]] = {}
    # Candidate descendants of equal-tick members from the FIRST snapshot:
    # pid -> (parent PID inside the equal-tick subtree, retained handle or None).
    equal_descendants: dict[int, tuple[int, object]] = {}

    def expired() -> bool:
        return time.monotonic() >= deadline

    def confirmed_by_pinned_handle(handle) -> bool:
        # 终止请求失败不代表进程仍存活；仅同一已固定身份的进程句柄进入 signaled 状态，才确认该成员退出；否则仍计入未确认。
        # WaitForSingleObject(handle, 0) semantics are subsumed: the call
        # returns immediately when the pinned handle is already signaled, and a
        # refused TerminateProcess often means the member is tearing itself
        # down (Windows fails the request on an already-terminating process
        # before it reaches the signaled state), so the SAME handle is given
        # the remaining budget. WAIT_TIMEOUT, WAIT_FAILED and every error are
        # NOT an exit.
        remaining = deadline - time.monotonic()
        try:
            return remaining > 0 and api.wait(handle, remaining * 1000) == "signaled"
        except OSError:
            return False

    try:
        try:
            root = api.open(root_pid)
            opened.append(root)
            if api.creation(root) != root_creation:
                raise RuntimeError(f"pid {root_pid}: identity changed; refusing termination")
            if api.exited(root):
                # Preserve the existing fail-closed contract for an already
                # exited root, even if another process still holds its handle.
                return [f"pid {root_pid}: root had already exited; record retained"]
        except (_ProcessGone, _ProcessDenied, OSError) as exc:
            return [f"pid {root_pid}: root identity unavailable ({type(exc).__name__}); record retained"]
        try:
            entries, _ = api.snapshot()
        except OSError:
            return ["process table unavailable; nothing signalled"]
        children: dict[int, list[int]] = {}
        for pid, (parent, name) in entries.items():
            names[pid] = name
            if valid_pid(pid) and pid != parent:
                children.setdefault(parent, []).append(pid)
        from collections import deque
        queue = deque([(root_pid, root_creation, 0)])
        seen = {root_pid}
        while queue:
            parent, parent_creation, depth = queue.popleft()
            for pid in sorted(children.get(parent, [])):
                if pid in seen:
                    unconfirmed.append(f"pid {pid} ({names.get(pid, '?')}): cyclic ancestry; not signalled")
                    continue
                seen.add(pid)
                if expired():
                    return [*unconfirmed, "tree verification deadline exceeded; nothing signalled"]
                name = names.get(pid, "?")
                try:
                    handle = api.open(pid)
                    opened.append(handle)
                    creation = api.creation(handle)
                except (_ProcessGone, _ProcessDenied, OSError) as exc:
                    # No handle: the system process table can still answer for
                    # pids this launcher may not open, but only to exclude an
                    # entry as obviously older than its nominal parent.
                    table_creation = api.creation_without_handle(pid)
                    if table_creation is not None and table_creation < parent_creation:
                        continue
                    own = f"created {table_creation}" if table_creation is not None else "creation unknown"
                    message = (f"pid {pid} ({name}): identity unavailable"
                               f" ({type(exc).__name__}); {own}; nominal parent {parent}"
                               f" created {parent_creation}; not signalled")
                    if table_creation == parent_creation:
                        # Equal tick without a handle: no pinned handle can
                        # ever confirm this pid, so the verdict is deferred to
                        # the post-stop recheck and can never be released.
                        equal_tick[pid] = (parent, parent_creation, table_creation, None, message)
                    else:
                        unconfirmed.append(message)
                    continue
                if creation < parent_creation:
                    # Strictly older than its nominal parent: PID reuse put a
                    # stranger in the snapshot. Not signalled, not unconfirmed.
                    continue
                if creation == parent_creation:
                    # 等时间戳不授权终止；同一持续持有的查询句柄确认退出，只解除该进程的退出疑义，不证明其后代已退出。
                    # The entry keeps its original query handle; its verdict is
                    # deferred to the post-stop recheck below.
                    equal_tick[pid] = (parent, parent_creation, creation, handle, None)
                    continue
                candidates[pid] = (parent, creation, depth + 1, handle)
                creations[pid] = creation
                # An exited but pinned parent can still anchor descendants.
                queue.append((pid, creation, depth + 1))
        # Revised-B companion: retain and recursively check candidate
        # descendants of equal-tick members, discovered from the FIRST
        # snapshot. Nothing below an equal-tick edge ever enters the signal
        # set; each member can only be proven gone by its own retained query
        # handle, and the final table re-checks these parent PIDs.
        sub_queue = deque(sorted(equal_tick))
        while sub_queue:
            member = sub_queue.popleft()
            for pid in sorted(children.get(member, [])):
                if pid in seen:
                    unconfirmed.append(f"pid {pid} ({names.get(pid, '?')}): cyclic ancestry"
                                       f" below equal-tick pid {member}; not signalled")
                    continue
                seen.add(pid)
                if expired():
                    return [*unconfirmed, "tree verification deadline exceeded; nothing signalled"]
                try:
                    handle = api.open(pid)
                    opened.append(handle)
                except (_ProcessGone, _ProcessDenied, OSError):
                    handle = None  # unknown: no pinned handle can confirm it
                equal_descendants[pid] = (member, handle)
                sub_queue.append(pid)
        # Revalidate membership AFTER opening every handle, not against the old
        # snapshot. PID reuse before OpenProcess cannot redirect this check.
        try:
            current, _ = api.snapshot()
        except OSError:
            return [*unconfirmed, "membership recheck failed; nothing signalled"]
        verified = {root_pid}
        targets = [(0, root_pid, root)]
        for pid, (parent, creation, depth, handle) in candidates.items():
            if parent not in verified or current.get(pid, (None, None))[0] != parent:
                unconfirmed.append(f"pid {pid} ({names.get(pid, '?')}) created {creation}:"
                                   f" parent chain changed or disappeared; nominal parent {parent}"
                                   f" created {creations.get(parent, '?')}; not signalled")
                continue
            verified.add(pid)
            targets.append((depth, pid, handle))
        confirmed = set()
        for depth, pid, handle in sorted(targets, key=lambda t: -t[0]):
            identity = f"pid {pid} ({names.get(pid, '?')}) created {creations.get(pid, '?')}"
            try:
                if api.exited(handle):
                    confirmed.add(pid)
                    continue
                if expired():
                    unconfirmed.append(f"{identity}: tree-stop deadline exceeded")
                    continue
                try:
                    # Termination access is requested by PID only after
                    # membership is verified, while the query handle is still
                    # held, so the PID cannot have been reused in between.
                    terminate_handle = api.open_terminate(pid)
                    opened.append(terminate_handle)
                except (_ProcessGone, _ProcessDenied, OSError) as exc:
                    # The request itself failed: re-verify the exit, if at all,
                    # on the SAME still-held query handle before counting the
                    # member as anything.
                    if confirmed_by_pinned_handle(handle):
                        confirmed.add(pid)
                        continue
                    unconfirmed.append(f"{identity}: termination handle unavailable"
                                       f" ({type(exc).__name__}); not signalled")
                    continue
                if not api.terminate(terminate_handle):
                    # TerminateProcess FALSE: the member may have exited (or
                    # begun exiting) on its own between the exited() check and
                    # the request; the pinned query handle decides.
                    if confirmed_by_pinned_handle(handle):
                        confirmed.add(pid)
                        continue
                    unconfirmed.append(f"{identity}: terminate refused")
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0 or api.wait(handle, remaining * 1000) != "signaled":
                    unconfirmed.append(f"{identity}: exit not confirmed within budget")
                else:
                    confirmed.add(pid)
            except OSError:
                unconfirmed.append(f"{identity}: exit verification failed")
        # Post-stop recheck of the equal-tick subtree: only the SAME
        # continuously-held query handle entering the signaled state resolves
        # one member's exit doubt; final-table absence never does, and a
        # resolved exit is never membership confirmation (these pids were
        # never signalled and never join the verified set).
        for pid in sorted(equal_tick):
            parent, parent_creation, creation, handle, message = equal_tick[pid]
            if handle is not None and confirmed_by_pinned_handle(handle):
                continue
            unconfirmed.append(message or (f"pid {pid} ({names.get(pid, '?')}): creation {creation}"
                                           f" does not bind to parent {parent} created {parent_creation};"
                                           f" not signalled"))
        for pid in sorted(equal_descendants):
            parent, handle = equal_descendants[pid]
            if handle is not None and confirmed_by_pinned_handle(handle):
                continue
            reason = "exit unknown (no handle)" if handle is None else "still alive or exit unconfirmed"
            unconfirmed.append(f"pid {pid} ({names.get(pid, '?')}): descendant of equal-tick"
                               f" pid {parent}; {reason}; not signalled")
        # No second signalling pass: newcomers are not part of the verified set.
        # The final table also re-checks the equal-tick subtree's parent PIDs:
        # anything new below an equal-tick member is a residual descendant and
        # keeps the record.
        try:
            if expired():
                unconfirmed.append("final tree check not completed within budget")
            else:
                final, _ = api.snapshot()
                watched = verified | set(equal_tick) | set(equal_descendants)
                for pid, (parent, name) in final.items():
                    if parent in watched and pid not in seen and pid != parent:
                        unconfirmed.append(f"pid {pid} ({name}): residual or new descendant; record retained")
        except OSError:
            unconfirmed.append("final tree check unavailable; record retained")
        return unconfirmed
    finally:
        for handle in opened:
            try:
                api.close(handle)
            except Exception:
                pass


def birth_identity(pid: int) -> str | None:
    if not valid_pid(pid):
        return None
    if os.name == "nt":
        import ctypes
        k = _kernel()
        h = k.OpenProcess(0x1000 | 0x100000, False, pid)  # query + synchronize
        if not h:
            if ctypes.get_last_error() == 87:  # no such PID
                return None
            raise OSError("cannot open process identity handle")
        try:
            return _win_birth(k, h)
        finally:
            k.CloseHandle(h)
    if not Path("/proc/sys/kernel/random/boot_id").exists():
        raise RuntimeError("persistent process identity is unsupported on this platform")
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        if fields[0] in {"Z", "X"}:
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"linux:{boot}:{int(fields[19])}"
    except FileNotFoundError:
        return None


def record_path(name: str) -> Path:
    if name not in {"worker", "searchd"}:
        raise ValueError("unknown component")
    return config.home() / "run" / f"{name}.pid"


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate key")
        result[k] = v
    return result


def read_record(name: str) -> dict | None:
    p = record_path(name)
    try:
        if p.is_symlink():
            raise ValueError("symlink")
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("non-regular PID record")
            raw = f.read(MAX_READY + 1)
        if len(raw) > MAX_READY:
            raise ValueError("oversize")
        r = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if (not isinstance(r, dict) or set(r) != {"version", "name", "pid", "birth", "port", "state"}
                or type(r["version"]) is not int or r["version"] != 1 or r["name"] != name
                or not valid_pid(r["pid"]) or not isinstance(r["birth"], str) or not 1 <= len(r["birth"]) <= 128
                or r["state"] not in {"starting", "ready"}
                or type(r["port"]) is not int or not (0 if r["state"] == "starting" else 1) <= r["port"] <= 65535):
            raise ValueError("invalid record")
        return r
    except FileNotFoundError:
        return None
    except (TypeError, ValueError, UnicodeError, OSError, RecursionError) as exc:
        raise RuntimeError(f"{name}: unverified/legacy PID record; refusing automatic control") from exc


def owned_pid(name: str) -> int | None:
    r = read_record(name)
    if r is None:
        return None
    birth = birth_identity(r["pid"])
    if birth is None:
        if r["birth"].startswith("win:"):
            raise RuntimeError(f"{name}: managed root exited; descendant state requires operator review")
        return None
    if birth != r["birth"]:
        raise RuntimeError(f"{name}: PID has been reused; refusing automatic control")
    return r["pid"]


def _forget(name: str, record: dict) -> None:
    if read_record(name) == record:
        record_path(name).unlink(missing_ok=True)


@contextlib.contextmanager
def control_lock(path: "os.PathLike[str] | None" = None, *, timeout_s: float = 0.0) -> Iterator[None]:
    """Exclusive lock over one launcher state directory (default launcher.lock).

    timeout_s=0 keeps the historical fail-fast behavior ("another launcher
    operation is running"); a positive timeout retries until the deadline for
    short critical sections such as client-token read-modify-writes.
    """
    if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 <= timeout_s <= 60):
        raise ValueError("lock timeout must be finite and in 0..60 seconds")
    p = Path(path) if path is not None else config.home() / "run" / "launcher.lock"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("launcher lock is not a regular file")
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, 0)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("another launcher operation is running") from exc
                    time.sleep(0.05)
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("another launcher operation is running") from exc
                    time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # keep the lock inode: unlinking permits split locks


def cleanup_child(name: str, proc: subprocess.Popen, *, grace: float = 5.0, forget: bool = True) -> None:
    """Reclaim a child of this invocation and, when allowed, its record.

    On Windows the launcher-stub tree is stopped through the verified-tree
    stopper before the Popen itself is reaped, all under one shared deadline.
    Any exit that cannot be confirmed is raised with the identity kept in the
    message; the shared PID record is only removed when the caller holds the
    control lock and everything is confirmed gone.
    """
    unconfirmed: list[str] = []
    deadline = time.monotonic() + grace
    if os.name == "nt":
        creation = _win_creation_of(getattr(proc, "_bzs_birth", None))
        if creation is not None:
            unconfirmed = _win_terminate_tree(proc.pid, creation, deadline_s=grace)
    try:
        if proc.poll() is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # This unreaped owned child exited between poll and signal.
        remaining = deadline - time.monotonic()
        if remaining > 0:
            proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.kill()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            proc.wait(timeout=remaining)
    if unconfirmed or proc.poll() is None:
        detail = "; ".join(unconfirmed) if unconfirmed else f"pid {proc.pid}: exit not confirmed"
        raise RuntimeError(f"{name}: child cleanup incomplete: {detail}")
    if forget:
        r = read_record(name)
        if r and r["pid"] == proc.pid and r["birth"] == getattr(proc, "_bzs_birth", None):
            # The re-read comparison only narrows the window; the unlink is
            # not atomic against an unlocked writer, so this runs only under
            # the control lock (see cleanup_children).
            _forget(name, r)


def cleanup_children(children: "list[tuple[str, subprocess.Popen]]", *, acquire_lock: bool = True, forget: bool = True) -> None:
    """Best-effort cleanup of every child of this invocation.

    A failure while cleaning one child must not skip the remaining ones. When
    the control lock cannot be taken, processes are still reclaimed through
    this invocation's own handles, but shared PID records are left untouched:
    mutating them without the lock races a concurrent launcher's writes.
    """
    with contextlib.ExitStack() as stack:
        locked = not acquire_lock  # caller already holds the lock
        if acquire_lock:
            try:
                stack.enter_context(control_lock())
                locked = True
            except (OSError, RuntimeError):
                pass  # Leaving this invocation's children running is worse.
        failures: list[str] = []
        for name, proc in reversed(list(children)):
            try:
                cleanup_child(name, proc, forget=forget and locked)
            except Exception as exc:  # Keep cleaning the remaining children.
                failures.append(str(exc))
        if failures:
            raise RuntimeError("child cleanup incomplete: " + "; ".join(failures))


def spawn(name: str, command: list[str], *, timeout: float = 30.0) -> subprocess.Popen:
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("startup timeout must be finite and in (0, 120]")
    record_path(name)  # validate component before creating anything
    if owned_pid(name) is not None:
        raise RuntimeError(f"{name} already has a managed process")
    logs = config.home() / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = logs / f"{name}.stdout.log"
    stderr_path = logs / f"{name}.log"
    options: dict = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        options["start_new_session"] = True
    def logfile(p):
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0), 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise RuntimeError("launcher log is not a regular file")
        return os.fdopen(fd, "ab", buffering=0)
    # Regular stdout log, not a PIPE: no unbounded readline, leaked reader thread
    # or pipe that fills after the launcher exits.
    with logfile(stdout_path) as out, logfile(stderr_path) as err:
        offset = out.seek(0, os.SEEK_END)
        proc = subprocess.Popen(command, stdout=out, stderr=err, **options)
    try:
        birth = birth_identity(proc.pid)
        if birth is None:
            raise RuntimeError(f"{name} exited before readiness")
        proc._bzs_birth = birth
        expected_port = int(command[command.index("--port") + 1]) if "--port" in command else 0
        record = {"version": 1, "name": name, "pid": proc.pid, "birth": birth, "port": expected_port, "state": "starting"}
        config._private_write(record_path(name), json.dumps(record))
        end = time.monotonic() + timeout
        data = b""
        with stdout_path.open("rb") as reader:
            reader.seek(offset)
            while b"\n" not in data:
                data += reader.read(MAX_READY + 1 - len(data))
                newline = data.find(b"\n")
                if newline >= 0:
                    break  # Later log lines do not count against the first-line limit.
                if len(data) > MAX_READY:
                    raise RuntimeError(f"{name}: oversized readiness record")
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} exited before readiness")
                if time.monotonic() >= end:
                    raise RuntimeError(f"{name}: readiness deadline exceeded")
                time.sleep(min(0.02, max(0, end - time.monotonic())))
        ready = json.loads(data.split(b"\n", 1)[0], object_pairs_hook=_unique)
        component = "bzs-worker" if name == "worker" else "searchd"
        if (not isinstance(ready, dict) or ready.get("event") != "ready" or ready.get("host") != "127.0.0.1"
                or ready.get("component", "searchd") != component or ready.get("version") != __version__
                or type(ready.get("port")) is not int or not 1 <= ready["port"] <= 65535):
            raise RuntimeError(f"{name}: invalid readiness identity")
        if "--port" in command:
            expected = int(command[command.index("--port") + 1])
            if expected and ready["port"] != expected:
                raise RuntimeError(f"{name}: readiness port mismatch")
        if proc.poll() is not None or birth_identity(proc.pid) != birth:
            raise RuntimeError(f"{name} exited during readiness")
        record = {"version": 1, "name": name, "pid": proc.pid, "birth": birth, "port": ready["port"], "state": "ready"}
        config._private_write(record_path(name), json.dumps(record))
        return proc
    except BaseException:
        cleanup_child(name, proc)
        raise


def stop_owned(name: str, *, timeout: float = 5.0) -> bool:
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("stop timeout must be finite and in (0, 60]")
    r = read_record(name)
    if r is None:
        return False
    pid = r["pid"]
    if os.name == "nt":
        # Windows venv interpreters are launcher stubs whose real interpreter
        # is a child: stopping means the verified tree, not only the recorded
        # pid, and the record survives until every member is confirmed gone.
        creation = _win_creation_of(r["birth"])
        if creation is None:
            raise RuntimeError(f"{name}: unverified PID record birth; refusing automatic control")
        was_alive = birth_identity(pid) is not None
        unconfirmed = _win_terminate_tree(pid, creation, deadline_s=timeout)
        if unconfirmed or birth_identity(pid) is not None:
            raise RuntimeError(f"{name}: stop not confirmed; record retained"
                               + ("; " + "; ".join(unconfirmed) if unconfirmed else ""))
        _forget(name, r)
        return was_alive
    else:
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RuntimeError("safe persisted stopping requires Linux pidfds")
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            _forget(name, r)
            return False
        try:
            birth = birth_identity(pid)
            if birth is None:  # exited between the identity probe and the pidfd
                _forget(name, r)
                return False
            if birth != r["birth"]:
                raise RuntimeError(f"{name}: process identity changed; refusing termination")
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not select.select([fd], [], [], timeout)[0]:
                raise RuntimeError(f"{name}: stop not confirmed; record retained")
        finally:
            os.close(fd)
    _forget(name, r)
    return True
