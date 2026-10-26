"""Admission refusals only: bounded identical-payload replay; no network retries."""
import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from bzs import core_client as module
from bzs.core_client import CoreClient, CoreError


def refused(code="E_REQUEST_BACKPRESSURE", hint=1):
    return {"status": "failed", "errors": [{"code": code, "retry_after_ms": hint}]}


@pytest.mark.parametrize("refusals", [1, 3, 4])
async def test_async_admission_retry_success_or_exhaustion(monkeypatch, refusals):
    calls = []
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module.random, "uniform", lambda *args: 0)
    # Do not replace asyncio.sleep or the real event loop's process-wide clock.
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    async def sleep(delay):
        clock.now += delay
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=sleep))
    async def handle(request):
        calls.append(await request.read())
        return web.json_response(refused() if len(calls) <= refusals else {"result": {"ok": True}},
                                 status=503 if len(calls) <= refusals else 200)
    app = web.Application(); app.router.add_post("/api/v0/call", handle)
    async with TestServer(app) as server:
        client = CoreClient(str(server.make_url("")), "unit-test-token")
        if refusals >= 4:
            with pytest.raises(CoreError, match="E_REQUEST_BACKPRESSURE"):
                await client.call("document.ingest", {"content": "exact payload"})
        else:
            assert await client.call("document.ingest", {"content": "exact payload"}) == {"ok": True}
    assert len(calls) == min(refusals + 1, 4) and len(set(calls)) == 1
    assert clock.now <= 10


@pytest.mark.parametrize("code", ["E_STORAGE_BUSY", "E_WORKER_UNAVAILABLE", "E_CORE"])
async def test_async_other_503_is_never_retried(code):
    calls = []
    async def handle(request):
        calls.append(await request.read())
        return web.json_response(refused(code), status=503)
    app = web.Application(); app.router.add_post("/api/v0/call", handle)
    async with TestServer(app) as server:
        with pytest.raises(CoreError) as error:
            await CoreClient(str(server.make_url("")), "test").call("document.ingest")
        assert error.value.code == code
    assert len(calls) == 1


@pytest.mark.parametrize("refusals", [1, 3, 4])
def test_sync_admission_retry_success_or_exhaustion(monkeypatch, refusals):
    clock = SimpleNamespace(now=0.0)
    sleeps = []
    def sleep(delay):
        sleeps.append(delay); clock.now += delay
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    monkeypatch.setattr(module.random, "uniform", lambda *args: 0)
    calls = []
    def open_request(request, timeout):
        calls.append(request.data)
        if len(calls) <= refusals:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(json.dumps(refused()).encode()))
        response = io.BytesIO(b'{"result":{"ok":true}}'); response.status = 200
        return response
    opener = SimpleNamespace(open=open_request)
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: opener)
    client = CoreClient("http://127.0.0.1:8610", "test")
    if refusals >= 4:
        with pytest.raises(CoreError): client.call_sync("document.ingest", {"x": 1})
    else:
        assert client.call_sync("document.ingest", {"x": 1}) == {"ok": True}
    assert len(calls) == min(refusals + 1, 4) and len(set(calls)) == 1
    assert sum(sleeps) <= 10


@pytest.mark.parametrize("status,code,remaining,retries,hint", [
    (500, "E_REQUEST_BACKPRESSURE", 10, 0, 1),
    (503, "E_STORAGE_BUSY", 10, 0, 1),
    (503, "E_REQUEST_BACKPRESSURE", 10, 3, 1),
    (503, "E_REQUEST_BACKPRESSURE", 0.5, 0, 1),
    (503, "E_REQUEST_BACKPRESSURE", 10, 0, 10000),
])
def test_retry_budget_and_exact_error_are_required(status, code, remaining, retries, hint):
    error = CoreError(code, "refused", status, {"retry_after_ms": hint})
    assert CoreClient._retry_delay(error, retries, remaining) is None
