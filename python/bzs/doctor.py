"""CL-08 doctor: verify local secret files are not exposed to other accounts.

Windows: every secret's DACL is read via ``GetNamedSecurityInfoW`` (see
``bzs.winsecurity``) and reported as an error when Everyone, BUILTIN\\Users or
Authenticated Users hold read access, when the DACL is missing/NULL, or when it
cannot be read at all (fail closed: unverifiable is never healthy).
POSIX: the existing mode rule - no group/world permission bits, as the core
enforces for auth.json at startup - is checked for every secret file.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from . import config

SECRET_FILES = ("api.token", "admin.token", "owner.token", "worker.token", "auth.json", "mcp_clients.json")


def mode_problems(mode: int) -> list[str]:
    """POSIX rule shared with the core's auth-file check: no group/world access."""
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return [f"group/world permission bits set ({oct(stat.S_IMODE(mode))}); expected 0600"]
    return []


def _check_target(path: Path, label: str, problems: list[str]) -> None:
    if os.name == "nt":
        from . import winsecurity
        protected, found = winsecurity.audit_path(path)
        state = {True: "DACL protected", False: "DACL inherited"}.get(protected, "DACL unreadable")
    else:
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            found, state = [f"cannot stat: {exc}"], "unavailable"
        else:
            found, state = mode_problems(mode), f"mode {oct(stat.S_IMODE(mode))}"
    if found:
        # One account can appear in several ACEs (direct plus inherit-only);
        # report each distinct problem once per target.
        for problem in dict.fromkeys(found):
            print(f"ERROR {label}: {problem}")
            problems.append(f"{label}: {problem}")
    else:
        print(f"ok {label} ({state})")


def check(home: Path | None = None) -> list[str]:
    """Print one line per secret target and return the problems (empty = healthy)."""
    h = home if home is not None else config.home()
    problems: list[str] = []
    if not h.exists():
        message = f"home directory {h} does not exist; run `bzs init` first"
        print(f"ERROR {message}")
        return [message]
    _check_target(h, f"home directory {h}", problems)
    for name in SECRET_FILES:
        if (h / name).exists():
            _check_target(h / name, name, problems)
    if not problems:
        print("secrets: no other local account can read them")
    return problems
