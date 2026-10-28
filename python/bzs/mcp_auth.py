"""Per-client bearer tokens for the MCP HTTP transport (groundwork for remote agents, ADR-0004).

Only SHA-256 digests are stored (``$BZS_HOME/mcp_clients.json``); a token is shown once when
created. Each client has a name (shown in logs) and can be revoked on its own.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken

from . import config, process_control

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def clients_path() -> Path:
    return config.home() / "mcp_clients.json"


def _clients_lock(clients_file: Path) -> Path:
    # Sibling of the clients file: the default lands in $BZS_HOME/run, and a
    # test's temporary file gets a temporary lock instead of touching a real
    # installation.
    return clients_file.parent / "run" / "mcp-clients.lock"


def load_clients(path: Path | None = None) -> list[dict[str, Any]]:
    """Load and strictly validate client entries; malformed input fails closed.

    Every entry must have a well-formed name and sha256, and optional fields
    must have their declared types (scopes: list of str, disabled: bool).
    A malformed file raises ValueError: the verifier rejects every request
    until an operator repairs it, instead of silently keeping a partial
    allow-list or turning a string scope into per-character scopes.
    """
    path = path or clients_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    clients: list[dict[str, Any]] = []
    for index, entry in enumerate(data.get("clients", [])):
        if not isinstance(entry, dict):
            raise ValueError(f"mcp client entry {index}: expected an object")
        name = entry.get("name")
        digest = entry.get("sha256")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"mcp client entry {index}: invalid name")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"mcp client {name!r}: invalid sha256 digest")
        scopes = entry.get("scopes", ["search"])
        if not isinstance(scopes, list) or not all(isinstance(s, str) and s for s in scopes):
            raise ValueError(f"mcp client {name!r}: scopes must be a list of names")
        if "disabled" in entry and not isinstance(entry["disabled"], bool):
            raise ValueError(f"mcp client {name!r}: disabled must be a boolean")
        clients.append(entry)
    return clients


def _save(clients: list[dict[str, Any]], path: Path) -> None:
    config._private_write(path, json.dumps({"clients": clients}, indent=2) + "\n")


def add_client(name: str, path: Path | None = None) -> str:
    if not _NAME.match(name):
        raise ValueError("client name: 1-64 letters, digits, '.', '_' or '-'")
    path = path or clients_path()
    # Locked read-modify-write: two concurrent `bzs mcp-token add` runs must
    # serialize (or fail loudly at the deadline) instead of silently dropping
    # whichever write lands first.
    with process_control.control_lock(_clients_lock(path), timeout_s=30):
        clients = load_clients(path)
        if any(c["name"] == name for c in clients):
            raise ValueError(f"client {name!r} already exists; revoke it first")
        token = "bzsmcp_" + secrets.token_urlsafe(32)
        clients.append({"name": name, "sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "scopes": ["search"]})
        _save(clients, path)
    return token


def revoke_client(name: str, path: Path | None = None) -> bool:
    path = path or clients_path()
    with process_control.control_lock(_clients_lock(path), timeout_s=30):
        clients = load_clients(path)
        kept = [c for c in clients if c["name"] != name]
        if len(kept) == len(clients):
            return False
        _save(kept, path)
        return True


class StaticTokenVerifier:
    """MCP SDK verifier over a file of static tokens, reloaded for each authenticated request."""

    def __init__(self, path: Path | None = None):
        self._path = path or clients_path()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            clients = await asyncio.to_thread(load_clients, self._path)
        except (OSError, ValueError, TypeError, AttributeError):
            return None  # Deleted or invalid token files fail closed; never retain a stale allow-list.
        digest = hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()
        match = None
        for client in clients:
            stored = client.get("sha256")
            if client.get("disabled") or not isinstance(stored, str) or not re.fullmatch(r"[0-9a-f]{64}", stored):
                continue
            if hmac.compare_digest(stored, digest):
                match = client
        if match is None:
            return None
        return AccessToken(token=token, client_id=match["name"], scopes=list(match.get("scopes", ["search"])))
