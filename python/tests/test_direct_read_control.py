"""Ordinary cancellation preserves ordinary fetch semantics and actual receipts."""
import asyncio
import secrets
import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bzs.worker.fetch import DirectReadControl, FetchBudget
from bzs.worker.netguard import NetPolicy
from bzs.worker.server import Worker, make_app
from bzs.worker import server as worker_module
from tests.test_http_revalidation import condition, conditional_worker
from tests.test_http_redirect_cache import redirect_worker, route


BODY = b"An ordinary controlled reading retains its independent text contract. " * 16


@pytest.fixture
async def direct_worker(tmp_path):
    calls = []
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        calls.append(request.path)
        if request.path == "/hold":
            started.set()
            await release.wait()
            return web.Response(status=302, text="unread redirect body", headers={"Location": "/body"})
        if request.path == "/stream":
            response = web.StreamResponse(headers={"Content-Type": "text/plain", "X-Robots-Tag": "noindex"})
            await response.prepare(request)
            await response.write(BODY)
            started.set()
            await release.wait()
            try:
                await response.write(BODY)
                await response.write_eof()
            except ConnectionError:
                pass
            return response
        if request.path == "/redirect":
            return web.Response(status=302, text="unread redirect body", headers={"Location": "/body"})
        if request.path in ("/soft", "/html"):
            refresh = '<meta http-equiv="refresh" content="0;url=/body">' if request.path == "/soft" else ""
            return web.Response(text='<html><head><meta name="robots" content="noindex">' + refresh +
                                     '</head><body><main><p>' + BODY.decode() + '</p></main></body></html>',
                                content_type="text/html")
        return web.Response(body=BODY, content_type="text/plain")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as site:
        sources = tmp_path / "sources.json"
        sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
        worker = Worker(str(sources), NetPolicy(["127.0.0.1/32"], test_mode=True))
        await worker.start()
        try:
            yield worker, site, calls, started, release
        finally:
            release.set()
            await worker.close()


def params(site, path="/body", **changes):
    return {"url": str(site.make_url(path)), "control_id": secrets.token_hex(24),
            "timeout_ms": 60000, **changes}


async def consumed(control):
    while not control.content_bytes:
        await asyncio.sleep(0.001)


async def test_cancel_before_start_and_replay_have_known_zero_usage(direct_worker):
    worker, site, calls, *_ = direct_worker
    request = params(site)
    assert await worker.fetch_cancel_direct({"control_id": request["control_id"]}) == {"cancel_requested": True}
    for _ in range(2):
        result = await worker.fetch_extract_direct(request)
        assert result["status"] == "cancelled" and result["error_code"] == "E_CANCELLED"
        assert result["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert calls == []


async def test_cancel_between_registration_and_network_child_start(direct_worker):
    worker, site, calls, *_ = direct_worker
    request = params(site)
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    await asyncio.sleep(0)
    control = worker._direct_controls[request["control_id"]]
    assert control._task is None  # Child is queued; no dispatch has begun.
    await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "cancelled" and result["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert calls == []


async def test_cancel_waiting_headers_interrupts_only_child_and_blocks_redirect(direct_worker):
    worker, site, calls, started, _ = direct_worker
    request = params(site, "/hold")
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    await asyncio.wait_for(started.wait(), 2)
    for _ in range(3):
        await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    result = await asyncio.wait_for(task, 1)
    assert not task.cancelled()
    assert result["status"] == "cancelled" and result["error_code"] == "E_CANCELLED"
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0}
    assert calls == ["/hold"]
    assert (await worker.fetch_extract_direct(request))["usage"] == result["usage"]


async def test_cancel_stream_retains_consumed_bytes_and_publication_observation(direct_worker):
    worker, site, calls, started, _ = direct_worker
    request = params(site, "/stream")
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    await asyncio.wait_for(started.wait(), 2)
    control = worker._direct_controls[request["control_id"]]
    await asyncio.wait_for(consumed(control), 2)
    await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "cancelled" and result["http_status"] == 200
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(BODY)}
    assert result["publication_observation"]["index_allowed"] is False
    assert "text" not in result and "html" not in result and calls == ["/stream"]


async def test_host_queue_cancel_preserves_semaphore_and_zero_dispatch(direct_worker):
    worker, site, calls, *_ = direct_worker
    sem = worker.fetcher._sem("127.0.0.1")
    for _ in range(worker.fetcher.per_host):
        await sem.acquire()
    try:
        request = params(site)
        task = asyncio.create_task(worker.fetch_extract_direct(request))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await worker.fetch_cancel_direct({"control_id": request["control_id"]})
        result = await asyncio.wait_for(task, 1)
        assert result["status"] == "cancelled"
        assert result["usage"] == {"external_requests": 0, "content_bytes": 0}
        assert calls == [] and sem._value == 0
    finally:
        for _ in range(worker.fetcher.per_host):
            sem.release()
    assert sem._value == worker.fetcher.per_host
    assert (await worker.fetch_extract_direct(params(site)))["status"] == "ok"


async def test_success_replay_and_late_cancel_keep_original_usage(direct_worker):
    worker, site, calls, *_ = direct_worker
    request = params(site)
    first = await worker.fetch_extract_direct(request)
    assert first["status"] == "ok" and first["text"]
    replay = await worker.fetch_extract_direct(request)
    assert replay["status"] == "attempt_replayed" and replay["error_code"] == "E_ATTEMPT_REPLAY"
    assert replay["usage"] == first["usage"] == {"external_requests": 1, "content_bytes": len(BODY)}
    await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    late = await worker.fetch_extract_direct(request)
    assert late["status"] == "cancelled" and late["usage"] == first["usage"]
    assert calls == ["/body"]


@pytest.mark.parametrize("completed", [False, True])
async def test_repeated_cancel_preserves_original_retirement_deadline_and_usage(direct_worker, completed):
    worker, site, calls, *_ = direct_worker
    request = params(site)
    control_id = request["control_id"]
    if completed:
        assert (await worker.fetch_extract_direct(request))["status"] == "ok"
    else:
        await worker.fetch_cancel_direct({"control_id": control_id})
    original = worker._retired_direct_controls[control_id]
    for _ in range(3):
        await worker.fetch_cancel_direct({"control_id": control_id})
        retired = worker._retired_direct_controls[control_id]
        assert retired[0] == original[0]
        assert retired[1] is True and retired[2:] == original[2:]
    replay = await worker.fetch_extract_direct(request)
    assert replay["status"] == "cancelled"
    assert replay["usage"] == {"external_requests": original[2], "content_bytes": original[3]}
    assert calls == (["/body"] if completed else [])


async def test_duplicate_running_control_and_unrelated_control_isolation(direct_worker):
    worker, site, calls, started, _ = direct_worker
    request = params(site, "/hold")
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(ValueError, match="already running"):
        await worker.fetch_extract_direct(request)
    other = await worker.fetch_extract_direct(params(site))
    assert other["status"] == "ok"
    await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    assert (await asyncio.wait_for(task, 1))["status"] == "cancelled"
    assert calls == ["/hold", "/body"]


async def test_direct_and_durable_cancel_namespaces_are_separate(direct_worker):
    worker, site, calls, *_ = direct_worker
    request = params(site)
    await worker.fetch_cancel({"attempt_id": request["control_id"]})
    result = await worker.fetch_extract_direct(request)
    assert result["status"] == "ok" and calls == ["/body"]
    second = params(site)
    await worker.fetch_cancel_direct({"control_id": second["control_id"]})
    durable = await worker.fetch_extract({"url": second["url"], "attempt_id": second["control_id"],
                                         "timeout_ms": 1000, "max_requests": 1, "max_content_bytes": 10000})
    assert durable["status"] == "ok" and calls == ["/body", "/body"]


async def test_ordinary_60000_timeout_truncation_and_redirect_body_contract_remain(direct_worker):
    worker, site, calls, *_ = direct_worker
    worker.fetcher.max_bytes = 80
    ordinary = await worker.fetch_extract({"url": str(site.make_url("/redirect")), "timeout_ms": 60000})
    controlled = await worker.fetch_extract_direct(params(site, "/redirect"))
    assert ordinary["status"] == controlled["status"] == "ok"
    assert ordinary["text"] == controlled["text"] and "download_truncated" in controlled["warnings"]
    assert ordinary["usage"] == controlled["usage"] == {"external_requests": 2, "content_bytes": len(BODY)}
    assert controlled["index_allowed"] is False
    assert "http_cache" not in controlled and "http_redirect_chain" not in controlled
    assert calls == ["/redirect", "/body", "/redirect", "/body"]


async def test_controlled_conditional_304_preserves_exact_ordinary_contract(conditional_worker):
    worker, site, calls, _ = conditional_worker
    first = await worker.fetch_extract_direct(params(site, "/page"))
    calls.clear()
    result = await worker.fetch_extract_direct(params(site, "/page", conditional=condition(first["http_cache"])))
    assert result["status"] == "not_modified" and result["http_status"] == 304
    assert result["binding_id"] == "b" * 64
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0}
    assert len(calls) == 1 and calls[0]["headers"]["if-none-match"] == first["http_cache"]["etag"]


async def test_controlled_redirect_conditional_still_dispatches_original_uri(redirect_worker):
    worker, site, calls, _ = redirect_worker
    first = await worker.fetch_extract_direct(params(site, "/start"))
    calls.clear()
    result = await worker.fetch_extract_direct(params(site, "/start", redirect_conditional=route(first)))
    assert result["status"] == "not_modified" and result["http_status"] == 304
    assert result["usage"] == {"external_requests": 2, "content_bytes": 0}
    assert [call["path"] for call in calls] == ["/start", "/page"]
    assert "if-none-match" not in calls[0]["headers"]
    assert calls[1]["headers"]["if-none-match"] == first["http_cache"]["etag"]
    assert result["redirect_revalidation"]["binding_id"] == "b" * 64


@pytest.mark.parametrize("path", ["/soft", "/html"])
async def test_cancel_during_extraction_blocks_soft_follow_and_success_body(direct_worker, monkeypatch, path):
    worker, site, calls, *_ = direct_worker
    entered, release = threading.Event(), threading.Event()
    original = worker_module.extract_html

    def held_extract(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "extract_html", held_extract)
    request = params(site, path)
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        await worker.fetch_cancel_direct({"control_id": request["control_id"]})
    finally:
        release.set()
    result = await asyncio.wait_for(task, 3)
    assert result["status"] == "cancelled" and result["usage"]["external_requests"] == 1
    assert result["usage"]["content_bytes"] > len(BODY)
    assert "html" not in result and "text" not in result and calls == [path]
    if path == "/html":
        assert result["publication_observation"]["index_allowed"] is False
    else:
        assert result["redirect_publication_observations"][0]["publication"]["index_allowed"] is False


@pytest.mark.parametrize("changes", [{"control_id": "bad"}, {"control_id": "A" * 48},
                                      {"timeout_ms": True}, {"timeout_ms": 999}, {"timeout_ms": 60001},
                                      {"max_requests": 1}, {"max_content_bytes": 1000},
                                      {"attempt_id": "a" * 48}, {"extra": True}, {"url": ""}])
async def test_control_validation_fails_before_network(direct_worker, changes):
    worker, site, calls, *_ = direct_worker
    with pytest.raises(ValueError):
        await worker.fetch_extract_direct(params(site, **changes))
    assert calls == []


async def test_cancel_validation_rejects_unknown_fields(direct_worker):
    worker, _, calls, *_ = direct_worker
    with pytest.raises(ValueError):
        await worker.fetch_cancel_direct({"control_id": "a" * 48, "attempt_id": "b" * 48})
    assert not worker._retired_direct_controls and calls == []


async def test_capacity_and_expiry_never_prune_active_controls(direct_worker):
    worker, site, calls, *_ = direct_worker
    running = "a" * 48
    control = DirectReadControl()
    worker._direct_controls[running] = control
    now = asyncio.get_running_loop().time()
    worker._retired_direct_controls = {f"{index:048x}": (now + 120, True, 0, 0) for index in range(2047)}
    with pytest.raises(ValueError, match="capacity"):
        await worker.fetch_extract_direct(params(site))
    with pytest.raises(ValueError, match="capacity"):
        await worker.fetch_cancel_direct({"control_id": "b" * 48})
    await worker.fetch_cancel_direct({"control_id": running})
    assert control.cancelled and worker._direct_controls[running] is control
    worker._retired_direct_controls["0" * 48] = (now - 1, True, 0, 0)
    result = await worker.fetch_extract_direct(params(site))
    assert result["status"] == "ok" and calls == ["/body"]
    assert worker._direct_controls[running] is control
    assert len(worker._retired_direct_controls) == 2047


async def test_fetcher_refuses_mixing_direct_control_and_durable_budget(direct_worker):
    worker, site, calls, *_ = direct_worker
    with pytest.raises(ValueError, match="durable grant"):
        await worker.fetcher.fetch(str(site.make_url("/body")), budget=FetchBudget(1, 10000), control=DirectReadControl())
    assert calls == []


async def test_external_task_cancellation_is_not_forged_as_controlled_receipt(direct_worker):
    worker, site, calls, started, _ = direct_worker
    request = params(site, "/hold")
    task = asyncio.create_task(worker.fetch_extract_direct(request))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["/hold"]
    assert request["control_id"] not in worker._direct_controls
    replay = await worker.fetch_extract_direct(request)
    assert replay["status"] == "attempt_replayed" and replay["usage"] == {"external_requests": 1, "content_bytes": 0}


async def test_authenticated_rpc_exposes_separate_control_methods(tmp_path):
    sources = tmp_path / "sources.json"
    sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
    worker = Worker(str(sources), NetPolicy())
    async with TestClient(TestServer(make_app(worker, "test-direct-control-token"))) as client:
        control_id = "a" * 48
        denied = await client.post("/rpc", json={"method": "fetch.cancel_direct", "params": {"control_id": control_id}})
        assert denied.status == 401 and not worker._retired_direct_controls
        headers = {"Authorization": "Bearer test-direct-control-token"}
        cancelled = await client.post("/rpc", headers=headers,
                                      json={"method": "fetch.cancel_direct", "params": {"control_id": control_id}})
        assert (await cancelled.json())["result"] == {"cancel_requested": True}
        read = await client.post("/rpc", headers=headers, json={"method": "fetch.extract_direct", "params": {
            "control_id": control_id, "url": "https://example.com/never-dispatched", "timeout_ms": 60000}})
        result = (await read.json())["result"]
        assert result["status"] == "cancelled" and result["usage"] == {"external_requests": 0, "content_bytes": 0}
        invalid = await client.post("/rpc", headers=headers, json={"method": "fetch.cancel_direct", "params": {
            "control_id": control_id, "attempt_id": control_id}})
        assert (await invalid.json())["error"]["code"] == "E_PARAMS"
