"""Per-client bearer tokens for the MCP HTTP transport (groundwork for remote agents, ADR-0004).

Only SHA-256 digests are stored (``$BZS_HOME/mcp_clients.json``); a token is shown once when
created. Each client has a name (shown in logs) and can be revoked on its own.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import stat
import re
import secrets
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken

from . import config, process_control

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SCOPE = re.compile(r"[A-Za-z0-9_:./-]{1,128}")
MAX_CLIENTS_BYTES = 1024 * 1024
MAX_CLIENTS = 1024


def clients_path() -> Path:
    return config.home() / "mcp_clients.json"


def _clients_lock(clients_file: Path) -> Path:
    # Sibling of the clients file: the default lands in $BZS_HOME/run, and a
    # test's temporary file gets a temporary lock instead of touching a real
    # installation.
    return clients_file.parent / "run" / "mcp-clients.lock"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate client registry key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite client registry value")


def _validate(data) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or set(data) != {"clients"} or not isinstance(data["clients"], list):
        raise ValueError("client registry must contain one clients array")
    if len(data["clients"]) > MAX_CLIENTS:
        raise ValueError("client registry entry limit exceeded")
    names, digests = set(), set()
    for index, entry in enumerate(data["clients"]):
        if not isinstance(entry, dict) or set(entry) - {"name", "sha256", "scopes", "disabled", "created_at"}:
            raise ValueError(f"mcp client entry {index}: unexpected fields or type")
        name, digest = entry.get("name"), entry.get("sha256")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"mcp client entry {index}: invalid name")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"mcp client entry {index}: invalid sha256 digest")
        if name in names or digest in digests:
            raise ValueError("duplicate client name or digest")
        names.add(name)
        digests.add(digest)
        scopes = entry.get("scopes", ["search"])
        if (not isinstance(scopes, list) or len(scopes) > 32
                or any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope) for scope in scopes)
                or len(set(scopes)) != len(scopes)):
            raise ValueError(f"mcp client entry {index}: invalid or duplicate scopes")
        if "disabled" in entry and type(entry["disabled"]) is not bool:
            raise ValueError(f"mcp client entry {index}: disabled must be a boolean")
        if "created_at" in entry:
            value = entry["created_at"]
            if not isinstance(value, str) or len(value) > 128 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ValueError(f"mcp client entry {index}: invalid creation metadata")
    return data["clients"]


def load_clients(path: Path | None = None) -> list[dict[str, Any]]:
    """Bounded, fail-closed registry; atomic replacement keeps readers coherent.

    Accept existing digest-only entries and optional search scopes. Refuse
    duplicate keys/identities, bad types and non-regular inputs as a whole.
    Missing files mean no authorization; no stale allow-list is retained.
    """
    path = path or clients_path()
    if path.is_symlink():
        raise ValueError("client registry must not be a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("client registry must be a regular file")
        raw = stream.read(MAX_CLIENTS_BYTES + 1)
    if len(raw) > MAX_CLIENTS_BYTES:
        raise ValueError("client registry byte limit exceeded")
    try:
        return _validate(json.loads(raw.decode("utf-8", "strict"),
                                    object_pairs_hook=_unique, parse_constant=_reject_constant))
    except RecursionError as exc:
        raise ValueError("client registry nesting limit exceeded") from exc


def _save(clients: list[dict[str, Any]], path: Path) -> None:
    _validate({"clients": clients})
    text = json.dumps({"clients": clients}, indent=2, allow_nan=False) + "\n"
    if len(text.encode("utf-8")) > MAX_CLIENTS_BYTES:
        raise ValueError("client registry byte limit exceeded")
    config._private_write(path, text)


def add_client(name: str, path: Path | None = None) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
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
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid client name")
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
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            return None
        try:
            digest = hashlib.sha256(token.encode("utf-8", "strict")).hexdigest()
            clients = await asyncio.to_thread(load_clients, self._path)
        except (OSError, ValueError, TypeError, AttributeError):
            return None  # Deleted or invalid token files fail closed; never retain a stale allow-list.
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
