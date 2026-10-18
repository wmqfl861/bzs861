"""Durable-read grants are tested only against an in-process loopback fixture."""
import asyncio
import gzip
import json
import secrets

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bzs.worker.fetch import FetchBudget, Fetcher
from bzs.worker.netguard import NetPolicy
from bzs.worker.server import Worker
from tests import fakeweb


BODY = b"A readable bounded document about checkpoints and durable work. " * 8


@pytest.fixture
async def budget_site():
    calls = []
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        path = request.path
        calls.append(path)
        if path == "/body":
            return web.Response(body=BODY, content_type="text/plain")
        if path == "/gzip":
            return web.Response(body=gzip.compress(BODY), content_type="text/plain", headers={"Content-Encoding": "gzip"})
        if path.startswith("/chain/"):
            group, hop = map(int, path.split("/")[2:])
            if hop < 5:
                return web.Response(status=302, text="jump", headers={"Location": f"/chain/{group}/{hop + 1}"})
            if group < 2:
                return web.Response(text=f'<html><head><meta http-equiv="refresh" content="0; url=/chain/{group + 1}/0"></head></html>',
                                    content_type="text/html")
            return web.Response(body=BODY, content_type="text/plain")
        if path == "/soft":
            return web.Response(text='<html><head><meta http-equiv="refresh" content="0; url=/body"></head></html>',
                                content_type="text/html")
        if path in ("/exact", "/extra"):
            response = web.StreamResponse(headers={"Content-Type": "text/plain"})
            await response.prepare(request)
            await response.write(BODY)
            await asyncio.sleep(0.08)  # EOF arrives after the last allowed body byte.
            try:
                if path == "/extra":
                    await response.write(b"!")
                await response.write_eof()
            except ConnectionError:
                pass
            return response
        if path == "/hold":
            started.set()
            await release.wait()
            return web.Response(status=302, text="jump", headers={"Location": "/body"})
        if path == "/disconnect":
            request.transport.close()
            return web.Response(text="unreachable")
        return web.Response(status=404, text="missing")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as server:
        yield server, calls, started, release


@pytest.fixture
async def bounded_worker(tmp_path, budget_site):
    server, calls, started, release = budget_site
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps(fakeweb.manifest(server.port)), encoding="utf-8")
    worker = Worker(str(manifest), NetPolicy(["127.0.0.1/32"], test_mode=True))
    await worker.start()
    try:
        yield worker, server, calls, started, release
    finally:
        release.set()
        await worker.close()


def grant(server, path, *, requests=18, byte_limit=8 * 1024 * 1024, attempt=None, timeout=3000):
    return {"url": str(server.make_url(path)), "attempt_id": attempt or secrets.token_hex(24),
            "max_requests": requests, "max_content_bytes": byte_limit, "timeout_ms": timeout}


async def test_all_18_http_and_soft_redirect_dispatches_share_one_grant(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    result = await worker.fetch_extract(grant(server, "/chain/0/0"))
    assert result["status"] == "ok"
    assert result["usage"]["external_requests"] == len(calls) == 18
    assert len(result["redirects"]) == 17
    assert result["usage"]["content_bytes"] > len(BODY) + 15 * 4  # includes both soft-redirect bodies
    assert result["text"]
    calls.clear()
    stopped = await worker.fetch_extract(grant(server, "/chain/0/0", requests=17))
    assert stopped["status"] == "budget_exhausted" and stopped["error_code"] == "E_REQUEST_BUDGET"
    assert stopped["usage"]["external_requests"] == len(calls) == 17
    assert "/chain/2/5" not in calls


async def test_soft_redirect_cannot_start_a_second_request_after_request_budget(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    result = await worker.fetch_extract(grant(server, "/soft", requests=1))
    assert result["error_code"] == "E_REQUEST_BUDGET"
    assert result["usage"]["external_requests"] == 1 and result["usage"]["content_bytes"] > 0
    assert calls == ["/soft"]


async def test_byte_budget_accumulates_across_redirect_bodies(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    soft = b'<html><head><meta http-equiv="refresh" content="0; url=/body"></head></html>'
    byte_limit = len(soft) + len(BODY) - 1
    result = await worker.fetch_extract(grant(server, "/soft", byte_limit=byte_limit))
    assert calls == ["/soft", "/body"] and result["error_code"] == "E_BYTE_BUDGET"
    assert result["usage"] == {"external_requests": 2, "content_bytes": byte_limit}
    assert "text" not in result  # incomplete pages never become successful artifacts


@pytest.mark.parametrize("path", ["/body", "/gzip", "/exact"])
async def test_exact_decompressed_byte_limit_is_accepted_including_delayed_chunked_eof(bounded_worker, path):
    worker, server, calls, *_ = bounded_worker
    result = await worker.fetch_extract(grant(server, path, byte_limit=len(BODY)))
    assert result["status"] == "ok", result
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(BODY)}
    assert len(calls) == 1


@pytest.mark.parametrize("path", ["/body", "/gzip", "/extra"])
async def test_excess_decompressed_bytes_are_refused_without_consuming_a_probe_byte(bounded_worker, path):
    worker, server, calls, *_ = bounded_worker
    limit = len(BODY) if path == "/extra" else len(BODY) - 1
    result = await worker.fetch_extract(grant(server, path, byte_limit=limit))
    assert result["status"] == "budget_exhausted" and result["error_code"] == "E_BYTE_BUDGET"
    assert result["usage"] == {"external_requests": 1, "content_bytes": limit}
    assert "text" not in result and len(calls) == 1


async def test_cancel_before_start_blocks_every_dispatch_and_replay(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    params = grant(server, "/body")
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    for _ in range(2):
        result = await worker.fetch_extract(params)
        assert result["status"] == "cancelled" and result["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert not calls


async def test_cancel_during_request_prevents_redirect_and_preserves_usage(bounded_worker):
    worker, server, calls, started, release = bounded_worker
    params = grant(server, "/hold")
    task = asyncio.create_task(worker.fetch_extract(params))
    await asyncio.wait_for(started.wait(), 2)
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    release.set()
    result = await task
    assert result["status"] == "cancelled" and result["usage"]["external_requests"] == 1
    assert calls == ["/hold"]
    replay = await worker.fetch_extract(params)
    assert replay["usage"] == result["usage"] and calls == ["/hold"]


async def test_success_replay_and_late_cancel_keep_original_attempt_usage(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    params = grant(server, "/body")
    original = await worker.fetch_extract(params)
    replay = await worker.fetch_extract(params)
    assert replay["status"] == "attempt_replayed"
    assert replay["usage"] == original["usage"] == {"external_requests": 1, "content_bytes": len(BODY)}
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    cancelled = await worker.fetch_extract(params)
    assert cancelled["status"] == "cancelled" and cancelled["usage"] == original["usage"]
    assert calls == ["/body"]


async def test_same_attempt_cannot_dispatch_twice_concurrently(bounded_worker):
    worker, server, calls, started, release = bounded_worker
    params = grant(server, "/hold")
    task = asyncio.create_task(worker.fetch_extract(params))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(ValueError, match="already running"):
        await worker.fetch_extract(params)
    release.set()
    result = await task
    assert result["status"] == "ok" and result["usage"]["external_requests"] == 2
    assert calls == ["/hold", "/body"]


async def test_waiting_for_host_slot_rechecks_cancel_before_dispatch(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    sem = worker.fetcher._sem("127.0.0.1")
    for _ in range(worker.fetcher.per_host):
        await sem.acquire()
    params = grant(server, "/body")
    task = asyncio.create_task(worker.fetch_extract(params))
    await asyncio.sleep(0)
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    for _ in range(worker.fetcher.per_host):
        sem.release()
    result = await task
    assert result["status"] == "cancelled" and result["usage"]["external_requests"] == 0
    assert calls == []


async def test_client_does_not_hide_a_disconnected_connection_retry(bounded_worker):
    worker, server, calls, *_ = bounded_worker
    # Prime a reusable connection, then close it before a response. aiohttp's
    # default retries GET internally; the grant must allow just one dispatch.
    await worker.fetch_extract(grant(server, "/body"))
    calls.clear()
    result = await worker.fetch_extract(grant(server, "/disconnect", requests=1))
    assert result["status"] == "network_error" and result["usage"]["external_requests"] == 1
    assert calls == ["/disconnect"]


@pytest.mark.parametrize("name,value", [("max_requests", 0), ("max_requests", True), ("max_requests", 19),
                                         ("max_content_bytes", 0), ("timeout_ms", 0), ("attempt_id", "bad")])
async def test_invalid_grants_fail_before_network(bounded_worker, name, value):
    worker, server, calls, *_ = bounded_worker
    params = grant(server, "/body")
    params[name] = value
    with pytest.raises(ValueError):
        await worker.fetch_extract(params)
    assert not calls


async def test_default_unbounded_read_retains_truncated_success_policy(budget_site):
    server, calls, *_ = budget_site
    fetcher = Fetcher(NetPolicy(["127.0.0.1/32"], test_mode=True), max_bytes=100)
    await fetcher.start()
    try:
        result = await fetcher.fetch(str(server.make_url("/body")))
        assert result.status == "ok" and result.truncated and len(result.body) == 100
        assert result.external_requests == 1 and result.content_bytes >= 100
        budget = FetchBudget(1, 100)
        bounded = await fetcher.fetch(str(server.make_url("/body")), budget=budget)
        assert bounded.status == "budget_exhausted" and budget.content_bytes == 100
    finally:
        await fetcher.close()
