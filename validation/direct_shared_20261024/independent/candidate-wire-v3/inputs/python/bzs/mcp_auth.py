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

from . import config

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def clients_path() -> Path:
    return config.home() / "mcp_clients.json"


def load_clients(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or clients_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [c for c in data.get("clients", []) if isinstance(c, dict) and c.get("sha256") and c.get("name")]


def _save(clients: list[dict[str, Any]], path: Path) -> None:
    config._private_write(path, json.dumps({"clients": clients}, indent=2) + "\n")


def add_client(name: str, path: Path | None = None) -> str:
    if not _NAME.match(name):
        raise ValueError("client name: 1-64 letters, digits, '.', '_' or '-'")
    path = path or clients_path()
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
