"""Client for searchd's loopback API (POST /api/v0/call)."""
from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

import aiohttp


class CoreError(Exception):
    def __init__(self, code: str, message: str, http_status: int, details: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = {} if details is None else details


def _unwrap(status: int, body: dict[str, Any]) -> dict[str, Any]:
    if status >= 400 or body.get("status") == "failed":
        err = (body.get("errors") or [{}])[0]
        details = {key: body[key] for key in ("usage", "shared_read") if isinstance(body.get(key), dict)}
        retry_ms = err.get("retry_after_ms")
        if type(retry_ms) is int and 0 <= retry_ms <= 10000:
            details["retry_after_ms"] = retry_ms
        raise CoreError(err.get("code", "E_CORE"), err.get("message", f"HTTP {status}"), status, details)
    return body.get("result", {})


class CoreClient:
    def __init__(self, url: str, token: str, timeout_s: float = 90.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    @staticmethod
    def _retry_delay(error: CoreError, retries: int, remaining: float) -> float | None:
        # Only transport admission refusal proves that Service was never entered.
        # A timeout, disconnected reply, E_STORAGE_BUSY or generic 503 is NOT safe
        # evidence for replaying a mutating request.
        if error.http_status != 503 or error.code != "E_REQUEST_BACKPRESSURE" or retries >= 3:
            return None
        hint = error.details.get("retry_after_ms", 1000)
        if type(hint) is not int or not 0 <= hint <= 10000:
            hint = 1000
        delay = max(float(1 << retries), hint / 1000) + random.uniform(0.0, 0.2)
        return delay if delay < remaining else None

    async def call(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"operation": operation, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        started = time.monotonic()
        retry_until = started + min(10.0, self.timeout)
        deadline = started + self.timeout
        retries = 0
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                while True:
                    try:
                        timeout = aiohttp.ClientTimeout(total=max(0.001, deadline - time.monotonic()))
                        async with session.post(f"{self.url}/api/v0/call", data=payload,
                                                headers=self._headers(), timeout=timeout) as resp:
                            body = await resp.json(content_type=None)
                            return _unwrap(resp.status, body)
                    except CoreError as exc:
                        delay = self._retry_delay(exc, retries, retry_until - time.monotonic())
                        if delay is None:
                            raise
                        await asyncio.sleep(delay)
                        if time.monotonic() >= retry_until:
                            raise
                        retries += 1
        except aiohttp.ClientConnectorError as exc:
            raise CoreError("E_CORE_UNAVAILABLE", f"searchd is not reachable at {self.url}; run `bzs start`", 503) from exc

    def call_sync(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"operation": operation, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/v0/call", payload, self._headers(), method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started = time.monotonic()
        retry_until = started + min(10.0, self.timeout)
        deadline = started + self.timeout
        retries = 0
        while True:
            try:
                try:
                    with opener.open(req, timeout=max(0.001, deadline - time.monotonic())) as resp:
                        return _unwrap(resp.status, json.load(resp))
                except urllib.error.HTTPError as resp:
                    with resp:
                        return _unwrap(resp.code, json.load(resp))
                except urllib.error.URLError as exc:
                    raise CoreError("E_CORE_UNAVAILABLE", f"searchd is not reachable at {self.url}; run `bzs start`", 503) from exc
            except CoreError as exc:
                delay = self._retry_delay(exc, retries, retry_until - time.monotonic())
                if delay is None:
                    raise
                time.sleep(delay)
                if time.monotonic() >= retry_until:
                    raise
                retries += 1

    def healthy(self) -> bool:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"{self.url}/healthz", timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False
