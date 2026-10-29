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

    # query limited + synchronize + terminate
    _OPEN_MASK = 0x1000 | 0x100000 | 1

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

    def snapshot(self):
        """Return (pid -> parent pid, completion FILETIME) or raise OSError.

        INVALID_HANDLE_VALUE and mid-table enumeration errors are failures:
        a silently partial table must never be treated as the whole truth.
        """
        ctypes, k = self._ctypes, self._k
        invalid = ctypes.c_void_p(-1).value
        snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == invalid:
            raise OSError("cannot snapshot the process table")
        parents = {}

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
                parents[e.pid] = e.ppid
                more = k.Process32Next(snap, ctypes.byref(e))
                if not more and ctypes.get_last_error() != 18:
                    raise OSError("process enumeration failed mid-table")
        finally:
            k.CloseHandle(snap)
        ft = self._w.FILETIME()
        k.GetSystemTimeAsFileTime(ctypes.byref(ft))
        return parents, (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    def open(self, pid):
        ctypes = self._ctypes
        h = self._k.OpenProcess(self._OPEN_MASK, False, pid)
        if h:
            return h
        error = ctypes.get_last_error()
        if error == 87:
            raise _ProcessGone(pid)
        if error == 5:
            raise _ProcessDenied(pid)
        raise OSError(f"cannot open pid {pid} (error {error})")

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
    Windows retains process IDs while handles exist; signals use those same
    handles. A snapshot completion timestamp alone cannot exclude reuse DURING
    enumeration. Parent-PID spoofing by an equally privileged process is outside
    this local operator contract; this is not a hostile-process sandbox.

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
    # pid -> (parent PID, creation, depth, pinned handle)
    candidates: dict[int, tuple[int, int, int, object]] = {}

    def expired() -> bool:
        return time.monotonic() >= deadline

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
            parents, _ = api.snapshot()
        except OSError:
            return ["process table unavailable; nothing signalled"]
        children: dict[int, list[int]] = {}
        for pid, parent in parents.items():
            if valid_pid(pid) and pid != parent:
                children.setdefault(parent, []).append(pid)
        from collections import deque
        queue = deque([(root_pid, root_creation, 0)])
        seen = {root_pid}
        while queue:
            parent, parent_creation, depth = queue.popleft()
            for pid in sorted(children.get(parent, [])):
                if pid in seen:
                    unconfirmed.append(f"pid {pid}: cyclic ancestry; not signalled")
                    continue
                seen.add(pid)
                if expired():
                    return [*unconfirmed, "tree verification deadline exceeded; nothing signalled"]
                try:
                    handle = api.open(pid)
                    opened.append(handle)
                    creation = api.creation(handle)
                    if creation <= parent_creation:
                        unconfirmed.append(f"pid {pid}: creation does not bind to parent {parent}; not signalled")
                        continue
                    candidates[pid] = (parent, creation, depth + 1, handle)
                    # An exited but pinned parent can still anchor descendants.
                    queue.append((pid, creation, depth + 1))
                except (_ProcessGone, _ProcessDenied, OSError) as exc:
                    unconfirmed.append(f"pid {pid}: identity unavailable ({type(exc).__name__}); not signalled")
        # Revalidate membership AFTER opening every handle, not against the old
        # snapshot. PID reuse before OpenProcess cannot redirect this check.
        try:
            current, _ = api.snapshot()
        except OSError:
            return [*unconfirmed, "membership recheck failed; nothing signalled"]
        verified = {root_pid}
        targets = [(0, root_pid, root)]
        for pid, (parent, creation, depth, handle) in candidates.items():
            if parent not in verified or current.get(pid) != parent:
                unconfirmed.append(f"pid {pid}: parent chain changed or disappeared; not signalled")
                continue
            verified.add(pid)
            targets.append((depth, pid, handle))
        confirmed = set()
        for depth, pid, handle in sorted(targets, key=lambda t: -t[0]):
            try:
                if api.exited(handle):
                    confirmed.add(pid)
                    continue
                if expired():
                    unconfirmed.append(f"pid {pid}: tree-stop deadline exceeded")
                    continue
                if not api.terminate(handle):
                    unconfirmed.append(f"pid {pid}: terminate refused")
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0 or api.wait(handle, remaining * 1000) != "signaled":
                    unconfirmed.append(f"pid {pid}: exit not confirmed within budget")
                else:
                    confirmed.add(pid)
            except OSError:
                unconfirmed.append(f"pid {pid}: exit verification failed")
        # No second signalling pass: newcomers are not part of the verified set.
        try:
            if expired():
                unconfirmed.append("final tree check not completed within budget")
            else:
                final, _ = api.snapshot()
                for pid, parent in final.items():
                    if parent in verified and pid not in seen and pid != parent:
                        unconfirmed.append(f"pid {pid}: residual or new descendant; record retained")
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
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
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
