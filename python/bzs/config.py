"""Local installation layout (BZS_HOME, default ~/.bzs861) and secrets handling."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def home() -> Path:
    return Path(os.environ.get("BZS_HOME") or Path.home() / ".bzs861")


@dataclass
class Config:
    core_port: int = 8610
    worker_port: int = 8611
    threads: int = 8
    startup_timeout_s: float = 30.0
    searchd: str = ""
    sources: str = ""
    sites: str = ""
    fixtures: str = ""
    state_dir: str = ""
    # Reserved settings: production currently rejects proxies and private-address allowances.
    proxy: str = ""
    proxy_remote_dns: bool = False
    allow_cidrs: list[str] = field(default_factory=list)
    worker_args: list[str] = field(default_factory=list)

    @property
    def core_url(self) -> str:
        return f"http://127.0.0.1:{self.core_port}"


def _protect_secret(path: Path) -> None:
    """CL-08: on Windows break DACL inheritance; only the current user and SYSTEM.

    Raises on any security-API failure so callers keep the file and report the
    failure honestly (fail closed). POSIX keeps the existing chmod 0600 rule.
    """
    if os.name != "nt":
        return
    from . import winsecurity
    winsecurity.protect_path(path)


def _private_write(path: Path, text: str, *, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        if secret:
            # The secret file is already durably written: a protection failure
            # must surface, never silently leave a permissive DACL behind.
            _protect_secret(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def default_searchd() -> str:
    # Prefer the optimised build; Windows multi-config generators put binaries under Release/ or Debug/.
    for candidate in (REPO_ROOT / "build-release" / "searchd", REPO_ROOT / "build" / "searchd",
                      REPO_ROOT / "build-release" / "Release" / "searchd.exe", REPO_ROOT / "build" / "Release" / "searchd.exe",
                      REPO_ROOT / "build" / "Debug" / "searchd.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("searchd") or str(REPO_ROOT / "build" / "searchd")


def init(force: bool = False) -> Path:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(h, stat.S_IRWXU)
    else:
        # CL-08: the home directory itself gets the protected DACL so later
        # files (state, run records) cannot re-inherit broader access.
        _protect_secret(h)
    cfg_path = h / "config.json"
    if cfg_path.exists() and not force:
        # CL-08: an existing (possibly legacy single-token) home keeps working;
        # re-running init only re-applies the protected DACL to its secrets.
        for name in ("api.token", "admin.token", "owner.token", "worker.token", "auth.json", "mcp_clients.json"):
            if (h / name).exists():
                _protect_secret(h / name)
        return cfg_path
    # CL-08 least privilege: the owner token is non-admin and is the MCP/api
    # default; the admin token is for CLI management commands only. Homes
    # created before CL-08 hold a single admin token in api.token and stay
    # compatible (see mcp_token/admin_token and docs/WINDOWS.md).
    api_token = secrets.token_hex(32)
    admin_token = secrets.token_hex(32)
    worker_token = secrets.token_hex(32)
    _private_write(h / "api.token", api_token + "\n", secret=True)
    _private_write(h / "admin.token", admin_token + "\n", secret=True)
    _private_write(h / "worker.token", worker_token + "\n", secret=True)
    auth = {"tokens": [
        {"sha256": hashlib.sha256(api_token.encode()).hexdigest(), "subject": "owner", "admin": False},
        {"sha256": hashlib.sha256(admin_token.encode()).hexdigest(), "subject": "admin", "admin": True},
    ]}
    _private_write(h / "auth.json", json.dumps(auth), secret=True)
    sources = h / "sources.json"
    if not sources.exists():
        shutil.copyfile(REPO_ROOT / "config" / "sources.json", sources)
    sites = h / "sites.json"
    if not sites.exists():
        shutil.copyfile(REPO_ROOT / "config" / "sites.json", sites)
    cfg = Config(searchd=default_searchd(), sources=str(sources), sites=str(sites), fixtures=str(REPO_ROOT / "tests" / "fixtures"),
                 state_dir=str(h / "state"))
    cfg_path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    return cfg_path


def load() -> Config:
    path = home() / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `bzs init` first")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def api_token() -> str:
    env = os.environ.get("SEARCH_API_TOKEN")
    if env:
        return env.strip()
    return (home() / "api.token").read_text(encoding="utf-8").strip()


def _admin_digests() -> set[str]:
    """sha256 digests marked admin in auth.json (token selection only).

    Unreadable auth.json yields an empty set: the core still authenticates
    every request; this only picks which local token file is preferred.
    """
    try:
        data = json.loads((home() / "auth.json").read_text(encoding="utf-8"))
        return {entry["sha256"] for entry in data["tokens"] if entry.get("admin") is True}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _read_token_file(name: str) -> str | None:
    try:
        text = (home() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def mcp_token() -> str:
    """Default MCP bearer token: prefer a non-admin token when present (CL-08).

    Fresh homes keep the non-admin owner token in api.token. A legacy home has
    only that one admin token; it keeps working unless an operator also stored
    a non-admin token in owner.token, which is then preferred.
    """
    env = os.environ.get("SEARCH_API_TOKEN")
    if env:
        return env.strip()
    token = _read_token_file("api.token")
    if token is None:
        raise FileNotFoundError(f"{home() / 'api.token'} not found; run `bzs init` first")
    admins = _admin_digests()
    if hashlib.sha256(token.encode()).hexdigest() in admins:
        owner = _read_token_file("owner.token")
        if owner is not None and hashlib.sha256(owner.encode()).hexdigest() not in admins:
            return owner
    return token


def admin_token() -> str:
    """Bearer token for CLI management commands (CL-08): the admin token when present.

    Legacy single-token homes keep working: without admin.token the one token
    in api.token is (and remains) the admin token.
    """
    env = os.environ.get("SEARCH_API_TOKEN")
    if env:
        return env.strip()
    for candidate in (_read_token_file("admin.token"), _read_token_file("api.token")):
        if candidate is not None:
            return candidate
    raise FileNotFoundError(f"{home() / 'api.token'} not found; run `bzs init` first")
