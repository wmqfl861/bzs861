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


def _private_write(path: Path, text: str) -> None:
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
    cfg_path = h / "config.json"
    if cfg_path.exists() and not force:
        return cfg_path
    api_token = secrets.token_hex(32)
    worker_token = secrets.token_hex(32)
    _private_write(h / "api.token", api_token + "\n")
    _private_write(h / "worker.token", worker_token + "\n")
    auth = {"tokens": [{"sha256": hashlib.sha256(api_token.encode()).hexdigest(), "subject": "owner", "admin": True}]}
    _private_write(h / "auth.json", json.dumps(auth))
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
