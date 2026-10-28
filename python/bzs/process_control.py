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
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
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
        return None
    if birth != r["birth"]:
        raise RuntimeError(f"{name}: PID has been reused; refusing automatic control")
    return r["pid"]


def _forget(name: str, record: dict) -> None:
    if read_record(name) == record:
        record_path(name).unlink(missing_ok=True)


@contextlib.contextmanager
def control_lock() -> Iterator[None]:
    p = config.home() / "run" / "launcher.lock"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("launcher lock is not a regular file")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, 0)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another launcher operation is running") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another launcher operation is running") from exc
        yield
    finally:
        os.close(fd)  # keep the lock inode: unlinking permits split locks


def cleanup_child(name: str, proc: subprocess.Popen, *, grace: float = 2.0) -> None:
    """Only an unreaped child handle of this invocation may be force-cleaned."""
    try:
        if proc.poll() is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # This unreaped owned child exited between poll and signal.
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=grace)
    r = read_record(name)
    if r and r["pid"] == proc.pid and r["birth"] == getattr(proc, "_bzs_birth", None):
        # No PID reuse before the owned child is reaped; caller holds control lock.
        _forget(name, r)


def cleanup_children(children: "list[tuple[str, subprocess.Popen]]", *, lock: bool = True) -> None:
    """Best-effort cleanup of every child of this invocation.

    A failure while cleaning one detached child must not skip the remaining
    ones. When the control lock is busy, cleanup still proceeds: only Popen
    handles of this invocation are terminated and record removal stays guarded
    by the compare-and-unlink in _forget.
    """
    with contextlib.ExitStack() as stack:
        if lock:
            try:
                stack.enter_context(control_lock())
            except (OSError, RuntimeError):
                pass  # Leaving this invocation's children running is worse.
        failures: list[str] = []
        for name, proc in reversed(list(children)):
            try:
                cleanup_child(name, proc)
            except Exception as exc:  # Keep cleaning the remaining children.
                failures.append(f"{name}: {exc}")
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
    if birth_identity(pid) is None:
        _forget(name, r)
        return False
    if os.name == "nt":
        k = _kernel()
        h = k.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not h:
            import ctypes
            if ctypes.get_last_error() == 87:  # exited after the identity probe
                _forget(name, r)
                return False
            raise RuntimeError(f"{name}: cannot obtain termination handle; record retained")
        try:
            birth = _win_birth(k, h)
            if birth is None:  # exited between the identity probe and this handle
                _forget(name, r)
                return False
            if birth != r["birth"]:
                raise RuntimeError(f"{name}: process identity changed; refusing termination")
            if not k.TerminateProcess(h, 0) or k.WaitForSingleObject(h, int(timeout * 1000)) != 0:
                raise RuntimeError(f"{name}: stop not confirmed; record retained")
        finally:
            k.CloseHandle(h)
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
