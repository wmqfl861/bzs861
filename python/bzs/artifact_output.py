"""Publish immutable offline evaluation/replay artifacts as one directory."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from .probe_state import atomic_write

MAX_OUTPUT_FILE_BYTES = 64 * 1024 * 1024


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing a concurrently created target."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, "destination already exists", str(destination))
    if os.name == "nt":
        # Python's Windows rename uses a non-replacing move.
        os.rename(source, destination)
        return
    if sys_platform_linux():
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result == 0:
            return
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    # Do not silently fall back to POSIX rename semantics, which may replace an
    # empty destination directory after the caller's existence check.
    raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unsupported on this platform")


def sys_platform_linux() -> bool:
    return os.name == "posix" and __import__("sys").platform.startswith("linux")


def _fsync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; Windows lacks a portable Python equivalent."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_component(value: str, *, label: str) -> None:
    if (not value or value in (".", "..") or any(ch in value for ch in ("/", "\\", ":", "\x00"))
            or value.endswith((".", " "))):
        raise ValueError(f"E_OUTPUT_PATH: invalid {label} path component")
    # Reject Windows device names even when the same artifact is prepared on POSIX.
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{i}" for i in range(1, 10))
    reserved.update(f"LPT{i}" for i in range(1, 10))
    if stem in reserved:
        raise ValueError(f"E_OUTPUT_PATH: reserved {label} path component")


def write_new_directory(path: str | Path, files: dict[str, bytes], *, label: str) -> None:
    """Stage all files and publish by one directory rename under a stable lock."""
    out = Path(path)
    _validate_component(out.name, label=label + " output directory")
    if out.exists() or out.is_symlink():
        raise ValueError(f"E_OUTPUT_EXISTS: {label} output directory already exists")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"E_OUTPUT_SCHEMA: {label} output file set is empty")
    for name, raw in files.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"E_OUTPUT_PATH: invalid {label} output filename")
        _validate_component(name, label=label + " output filename")
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_FILE_BYTES:
            raise ValueError(f"E_OUTPUT_LIMIT: invalid {label} output file size")

    parent = out.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / f".{out.name}.publish.sqlite"
    if lock_path.is_symlink():
        raise ValueError(f"E_OUTPUT_PATH: {label} publication lock must not be a symlink")
    if lock_path.exists() and not stat.S_ISREG(lock_path.stat().st_mode):
        raise ValueError(f"E_OUTPUT_PATH: {label} publication lock must be a regular file")
    try:
        connection = sqlite3.connect(lock_path, timeout=0, isolation_level=None)
    except sqlite3.Error as exc:
        raise ValueError(f"E_OUTPUT_LOCK: cannot open {label} publication lock") from exc

    stage: Path | None = None
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise ValueError(f"E_OUTPUT_BUSY: another {label} publisher is active") from exc
            raise ValueError(f"E_OUTPUT_LOCK: invalid {label} publication lock") from exc

        if out.exists() or out.is_symlink():
            raise ValueError(f"E_OUTPUT_EXISTS: {label} output directory already exists")
        stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.stage-", dir=parent))
        for name, raw in files.items():
            atomic_write(stage / name, raw)
        # Persist staged directory entries before publishing the directory.
        _fsync_directory(stage)
        if out.exists() or out.is_symlink():
            raise ValueError(f"E_OUTPUT_EXISTS: {label} output directory was created concurrently")
        _rename_noreplace(stage, out)
        stage = None
        try:
            _fsync_directory(parent)
        except OSError as exc:
            # The directory is already visible and complete, but crash durability
            # could not be confirmed. Never pretend it was rolled back or safe to
            # retry at the same path.
            raise OSError("E_OUTPUT_DURABILITY_UNCONFIRMED: output was published but parent directory flush failed") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        try:
            connection.rollback()
        finally:
            connection.close()
