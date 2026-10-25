"""A probe owns one output directory; checkpoints and response bodies survive interruption."""
from __future__ import annotations

from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import zlib

from .probe_format import MAX_RAW_BYTES

MAX_REPORT_BYTES = 64 * 1024 * 1024
REPORT_NAME = "probe_report.json"
_LOCK_NAME = "probe.lock"
MAX_JSON_DEPTH = 64


def _regular_path(out: Path, name: str) -> Path:
    """Resolve a relative evidence path without following links inside its directory."""
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError("recorded evidence path is unsafe")
    root = out.resolve(strict=True)
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError("recorded evidence path must not contain symbolic links")
    if not path.is_relative_to(root) or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("recorded evidence must be a regular file inside the output directory")
    return path


@contextmanager
def _open_regular(path: Path):
    # O_NONBLOCK prevents a replaced FIFO from hanging the reader. O_NOFOLLOW,
    # when available, also checks the final component at open time.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) |
                 getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("recorded evidence must be a regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            yield stream
    finally:
        if fd != -1:
            os.close(fd)


@contextmanager
def output_guard(out: Path, resume: bool):
    if out.is_symlink():
        raise ValueError("probe output must not be a symbolic link")
    if resume:
        if not out.is_dir() or not (out / REPORT_NAME).is_file():
            raise ValueError("--resume requires an existing probe_report.json in --out")
    elif out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("probe output is not empty; choose a new --out or explicitly use --resume")
    out.mkdir(parents=True, exist_ok=True)
    lock_path = out / _LOCK_NAME
    if lock_path.exists() or lock_path.is_symlink():
        _regular_path(out, _LOCK_NAME)
    try:
        connection = sqlite3.connect(lock_path, timeout=0, isolation_level=None)
    except sqlite3.Error as exc:
        raise ValueError("cannot open the probe output lock") from exc
    try:
        try:
            # SQLite releases the lock when a process dies; resume needs no stale-PID override.
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise ValueError("another probe is using this output directory") from exc
            raise ValueError("probe output lock is not a valid SQLite file") from exc
        if not resume and any(path.name not in (_LOCK_NAME, _LOCK_NAME + "-journal") for path in out.iterdir()):
            raise ValueError("probe output became nonempty; choose a new --out or explicitly use --resume")
        yield
    finally:
        connection.rollback()
        connection.close()


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(out: Path, report: dict) -> None:
    data = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    if len(data) > MAX_REPORT_BYTES:
        raise ValueError("probe report exceeds the 64 MiB checkpoint limit")
    atomic_write(out / REPORT_NAME, data)


def read_report_with_digest(out: Path) -> tuple[dict, str]:
    path = _regular_path(out, REPORT_NAME)
    with _open_regular(path) as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("probe report exceeds the 64 MiB checkpoint limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate probe report key: " + key)
            result[key] = value
        return result

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("probe report must contain finite JSON numbers")
        return number

    def invalid(value):
        raise ValueError("invalid JSON constant in probe report: " + value)

    try:
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("probe report must be bounded UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("probe report must be an object")
    stack = [(report, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("probe report JSON nesting exceeds its limit")
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value if isinstance(v, (dict, list)))
    return report, hashlib.sha256(raw).hexdigest()


def read_report(out: Path) -> dict:
    return read_report_with_digest(out)[0]


def read_body(out: Path, response: dict) -> bytes:
    if response.get("encoding") != "gzip":
        raise ValueError("recorded response body encoding must be gzip")
    size, digest = response.get("body_bytes"), response.get("body_sha256")
    if type(size) is not int or not 0 <= size <= MAX_RAW_BYTES or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid recorded response body identity")
    name = response.get("raw_path")
    if size == 0 and name is None:
        body = b""
    else:
        if not isinstance(name, str):
            raise ValueError("recorded response body path is missing")
        path = _regular_path(out, name)
        with _open_regular(path) as compressed:
            if os.fstat(compressed.fileno()).st_size > MAX_RAW_BYTES + 65536:
                raise ValueError("compressed response body exceeds its limit")
            try:
                with gzip.GzipFile(fileobj=compressed, mode="rb") as stream:
                    body = stream.read(MAX_RAW_BYTES + 1)
            except (OSError, EOFError, zlib.error) as exc:
                raise ValueError("recorded response body is not a complete gzip stream") from exc
    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("recorded response body length or SHA-256 does not match")
    return body


def record_body(out: Path, attempt_id: str, body: bytes) -> dict:
    if not re.fullmatch(r"a[0-9]{6}", attempt_id):
        raise ValueError("invalid probe attempt id")
    if len(body) > MAX_RAW_BYTES:
        raise ValueError("response body exceeds the probe evidence limit")
    digest = hashlib.sha256(body).hexdigest()
    result = {"body_sha256": digest, "body_bytes": len(body), "encoding": "gzip"}
    if not body:
        return result
    name = f"raw/{attempt_id}-{digest}.body.gz"
    path = out / name
    if path.parent.is_symlink():
        raise ValueError("raw evidence directory must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    result["raw_path"] = name
    if path.exists() or path.is_symlink():
        read_body(out, result)
        return result
    # Exclusive creation never changes a previously recorded body. The checkpoint only
    # references it after the complete gzip has been flushed. A crash can leave an orphan.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(gzip.compress(body, mtime=0))
        stream.flush()
        os.fsync(stream.fileno())
    return result
