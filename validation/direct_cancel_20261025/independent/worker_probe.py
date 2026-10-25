"""Independent loopback RPC probes; no public source or paid request is used."""
from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bzs.worker.netguard import NetPolicy
from bzs.worker.server import Worker, make_app

checks = 0
results: list[dict] = []


def require(condition, message):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(message)


async def main():
    calls: list[str] = []
    release = asyncio.Event()
    streamed = asyncio.Event()
    prefix = b"independent second-hop stream bytes!!!"
    soft = (b'<html><head><meta name="robots" content="noindex">'
            b'<meta http-equiv="refresh" content="0;url=/stream"></head>'
            b'<body><main><p>Independent source restriction before the second hop.</p></main></body></html>')

    async def origin(request):
        calls.append(request.path)
        if request.path == "/soft":
            return web.Response(body=soft, content_type="text/html")
        if request.path == "/stream":
            response = web.StreamResponse(headers={"Content-Type": "text/plain", "X-Robots-Tag": "nosnippet", "Cache-Control": "no-store"})
            await response.prepare(request)
            await response.write(prefix)
            streamed.set()
            await release.wait()
            try:
                await response.write_eof()
            except ConnectionError:
                pass
            return response
        if request.path == "/hold":
            await release.wait()
        return web.Response(text="Independent ordinary response body.", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/{path:.*}", origin)
    with tempfile.TemporaryDirectory(prefix="bzs-direct-review-") as temporary:
        manifest = Path(temporary) / "sources.json"
        manifest.write_text('{"version":1,"sources":[]}', encoding="utf-8")
        async with TestServer(app) as site:
            worker = Worker(str(manifest), NetPolicy(["127.0.0.1/32"], test_mode=True))
            async with TestClient(TestServer(make_app(worker, "independent-test-only-token"))) as client:
                async def rpc(method, params, *, authenticated=True):
                    headers = {"Authorization": "Bearer independent-test-only-token"} if authenticated else {}
                    async with client.post("/rpc", headers=headers, json={"method": method, "params": params}) as response:
                        return response.status, await response.json()

                def request(path="/body", control=None, timeout=60000):
                    return {"url": str(site.make_url(path)), "control_id": control or secrets.token_hex(24), "timeout_ms": timeout}

                try:
                    # Exercise two actual HTTP hops and cancellation over a
                    # separate authenticated RPC while the read RPC is active.
                    p = request("/soft")
                    task = asyncio.create_task(rpc("fetch.extract_direct", p))
                    await asyncio.wait_for(streamed.wait(), 3)
                    async def consumed():
                        while worker._direct_controls[p["control_id"]].content_bytes < len(soft) + len(prefix):
                            await asyncio.sleep(0)
                    await asyncio.wait_for(consumed(), 3)
                    status, ack = await rpc("fetch.cancel_direct", {"control_id": p["control_id"]})
                    require(status == 200 and ack["result"]["cancel_requested"], "active RPC cancel acknowledgement")
                    _, answer = await asyncio.wait_for(task, 3)
                    out = answer["result"]
                    require(out["status"] == "cancelled" and out["error_code"] == "E_CANCELLED", "cancelled second-hop response")
                    expected_usage = {"external_requests": 2, "content_bytes": len(soft) + len(prefix)}
                    require(out["usage"] == expected_usage, "both hops retain exact consumed counters")
                    require("text" not in out and "html" not in out, "cancelled artifact omitted")
                    require(out["http_freshness"]["cache_control"] == "no-store", "final cache restriction survives")
                    require(out["publication_observation"]["snippet_allowed"] is False, "final snippet restriction survives")
                    require(out["redirect_publication_observations"][0]["publication"]["index_allowed"] is False, "prior soft-hop index restriction survives")
                    require(calls == ["/soft", "/stream"], "no implicit retry or follow after cancellation")
                    _, again = await rpc("fetch.extract_direct", {**p, "url": str(site.make_url("/body"))})
                    require(again["result"]["status"] == "cancelled" and again["result"]["usage"] == expected_usage, "replay cannot retarget a retired control")
                    require(calls == ["/soft", "/stream"], "retargeted replay does not dispatch")
                    results.append({"case": "two_hop_authenticated_rpc_cancel", "passed": True, "usage": expected_usage})

                    # A real timeout has an ordinary, known one-request receipt
                    # and cannot masquerade as cancellation or be re-dispatched.
                    p = request("/hold", timeout=1000)
                    _, answer = await rpc("fetch.extract_direct", p)
                    out = answer["result"]
                    require(out["status"] == "timeout", "ordinary deadline remains timeout")
                    require(out["usage"] == {"external_requests": 1, "content_bytes": 0}, "timeout retains dispatch charge")
                    _, again = await rpc("fetch.extract_direct", p)
                    require(again["result"]["status"] == "attempt_replayed", "timeout execution is retired")
                    require(again["result"]["usage"] == out["usage"], "timeout replay retains usage")
                    require(calls.count("/hold") == 1, "timeout replay does not dispatch")
                    results.append({"case": "ordinary_rpc_timeout_retirement", "passed": True})

                    # Numeric identity equality across Worker namespaces does
                    # not give cancellation authority over the other operation.
                    token = secrets.token_hex(24)
                    await rpc("fetch.cancel", {"attempt_id": token})
                    _, answer = await rpc("fetch.extract_direct", request(control=token))
                    require(answer["result"]["status"] == "ok", "durable cancel does not affect ordinary control")
                    token = secrets.token_hex(24)
                    await rpc("fetch.cancel_direct", {"control_id": token})
                    _, answer = await rpc("fetch.extract", {"url": str(site.make_url("/body")), "attempt_id": token,
                        "max_requests": 1, "max_content_bytes": 1024, "timeout_ms": 1000})
                    require(answer["result"]["status"] == "ok", "ordinary cancel does not affect durable grant")
                    results.append({"case": "authenticated_rpc_namespace_isolation", "passed": True})

                    # Invalid grants are rejected before registering an ordinary
                    # execution; missing credentials create no cancellation state.
                    token = secrets.token_hex(24)
                    before = len(calls)
                    _, answer = await rpc("fetch.extract_direct", {**request(control=token), "max_requests": 1})
                    require(answer["error"]["code"] == "E_PARAMS", "durable grant fields refused")
                    require(token not in worker._direct_controls and token not in worker._retired_direct_controls, "invalid grant does not occupy direct identity")
                    status, _ = await rpc("fetch.cancel_direct", {"control_id": token}, authenticated=False)
                    require(status == 401 and token not in worker._retired_direct_controls, "unauthenticated cancellation cannot create a tombstone")
                    require(len(calls) == before, "validation and authorization dispatch nothing")
                    results.append({"case": "validation_and_authentication", "passed": True})
                finally:
                    release.set()
    print(json.dumps({"cases": results, "checks": checks, "failed": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
