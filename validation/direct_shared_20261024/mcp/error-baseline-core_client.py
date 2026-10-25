"""Client for searchd's loopback API (POST /api/v0/call)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import aiohttp


class CoreError(Exception):
    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


def _unwrap(status: int, body: dict[str, Any]) -> dict[str, Any]:
    if status >= 400 or body.get("status") == "failed":
        err = (body.get("errors") or [{}])[0]
        raise CoreError(err.get("code", "E_CORE"), err.get("message", f"HTTP {status}"), status)
    return body.get("result", {})


class CoreClient:
    def __init__(self, url: str, token: str, timeout_s: float = 90.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def call(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"operation": operation, "params": params or {}}, ensure_ascii=False)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.post(f"{self.url}/api/v0/call", data=payload.encode("utf-8"), headers=self._headers()) as resp:
                    body = await resp.json(content_type=None)
                    return _unwrap(resp.status, body)
        except aiohttp.ClientConnectorError as exc:
            raise CoreError("E_CORE_UNAVAILABLE", f"searchd is not reachable at {self.url}; run `bzs start`", 503) from exc

    def call_sync(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"operation": operation, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/v0/call", payload, self._headers(), method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return _unwrap(resp.status, json.load(resp))
        except urllib.error.HTTPError as resp:
            with resp:
                return _unwrap(resp.code, json.load(resp))
        except urllib.error.URLError as exc:
            raise CoreError("E_CORE_UNAVAILABLE", f"searchd is not reachable at {self.url}; run `bzs start`", 503) from exc

    def healthy(self) -> bool:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"{self.url}/healthz", timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False
