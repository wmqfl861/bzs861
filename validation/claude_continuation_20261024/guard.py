"""Local validation launcher: repair only a frozen native ELF's execute mode.

The program is exec'ed, not supervised or retried after it starts. Test argv,
environment, working directory, signals and exit status remain the original
CTest/subprocess contract. Product CMake and CI never invoke this launcher.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from run import NATIVE, ROOT


def verify_and_prepare(path: Path, expected: str, attempt: int) -> None:
    if (not path.is_absolute() or path.parent not in (ROOT / "build", ROOT / "build-asan")
            or path.name not in NATIVE or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("Only a named, hash-bound local native build output is allowed")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Native target is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            if stream.read(4) != b"\x7fELF":
                raise ValueError("Native target is not an ELF build output")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise ValueError("Native target differs from its frozen SHA256")
        before = stat.S_IMODE(info.st_mode)
        if not before & stat.S_IXUSR:
            os.fchmod(fd, before | 0o111)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)
                or not stat.S_ISREG(current.st_mode) or not current.st_mode & stat.S_IXUSR):
            raise ValueError("Native target identity or execute mode changed during preparation")
        print("GUARD_EXEC " + json.dumps({"target": path.name, "sha256": expected,
            "attempt": attempt, "mode_before": oct(before),
            "mode_after": oct(stat.S_IMODE(after.st_mode))}), file=sys.stderr, flush=True)
    finally:
        os.close(fd)


def main() -> int:
    if len(sys.argv) < 3:
        print("GUARD_ERROR expected SHA256, executable and original arguments", file=sys.stderr)
        return 125
    expected, path = sys.argv[1], Path(sys.argv[2])
    argv = [str(path), *sys.argv[3:]]
    for attempt in (1, 2):
        try:
            verify_and_prepare(path, expected, attempt)
        except (OSError, ValueError) as error:
            print("GUARD_ERROR " + str(error), file=sys.stderr, flush=True)
            return 125
        try:
            os.execv(path, argv)
        except OSError as error:
            # exec never started a test. A second preparation is allowed only
            # for this particular kernel launch refusal, never a test failure.
            if error.errno == errno.EACCES and attempt == 1:
                print("GUARD_PREEXEC_EACCES retry_mode_preparation_once", file=sys.stderr, flush=True)
                continue
            print("GUARD_EXEC_ERROR " + str(error), file=sys.stderr, flush=True)
            return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
