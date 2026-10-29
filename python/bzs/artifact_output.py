"""Publish immutable offline evaluation/replay artifacts as one directory."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from .probe_state import atomic_write

MAX_OUTPUT_FILE_BYTES = 64 * 1024 * 1024


def write_new_directory(path: str | Path, files: dict[str, bytes], *, label: str) -> None:
    """Stage all files and publish by one directory rename under a stable lock."""
    out = Path(path)
    if not out.name or out.name in (".", ".."):
        raise ValueError(f"E_OUTPUT_PATH: invalid {label} output directory")
    if out.exists() or out.is_symlink():
        raise ValueError(f"E_OUTPUT_EXISTS: {label} output directory already exists")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"E_OUTPUT_SCHEMA: {label} output file set is empty")
    for name, raw in files.items():
        if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
            raise ValueError(f"E_OUTPUT_PATH: invalid {label} output filename")
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
        # Cooperating writers serialize on the persistent SQLite sidecar. The
        # final directory is visible only after every artifact has been flushed.
        if out.exists() or out.is_symlink():
            raise ValueError(f"E_OUTPUT_EXISTS: {label} output directory was created concurrently")
        os.rename(stage, out)
        stage = None
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        try:
            connection.rollback()
        finally:
            connection.close()
