"""Automatic discovery admission tested against local, in-process HTTP only."""
import asyncio
import copy
import gzip
import hashlib
import json
import secrets
import time
from email.utils import formatdate

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bzs.worker.netguard import NetPolicy, PolicyError
from bzs.worker.robots import CRAWLER_USER_AGENT, MAX_ROBOTS_BYTES, response_record
from bzs.worker.server import Worker
from bzs.worker.sites import EMPTY_MANIFEST, SiteRegistry, canonical_map_url


ROBOTS = b"User-agent: BzsBot\nDisallow: /private\nSitemap: /not-an-absolute-sitemap\n"
PAGE = b'<html><body><a href="/one">One</a><a href="/two#part">Two</a></body></html>'


def site_spec(origin):
    return {"id": "fixture-site", "enabled": True, "origin": origin, "path_prefixes": ["/"],
            "review": {"status": "approved", "payment_scheme": "no_payment", "automated_access": "approved",
                       "checked_at": "1970-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                       "terms_url": "https://example.com/terms", "cache_policy": "allowed_with_limits",
                       "redistribution_policy": "links_only", "approved_by": "offline-fixture"},
            "limits": {"max_concurrency": 1, "min_interval_ms": 0, "request_quota": 1000, "quota_window_seconds": 86400}}


@pytest.fixture
async def map_site():
    calls = []
    state = {"robots_status": 200, "robots_body": ROBOTS, "robots_headers": {"Content-Type": "text/plain"}}
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        calls.append({"path": request.path, "raw_path": request.raw_path, "ua": request.headers.get("User-Agent"),
                      "time": time.monotonic()})
        if request.path == "/page.gz":
            origin = f"http://{request.host}"
            page = (f'<html><body><a href="{origin}/gz/one">link</a></body></html>').encode()
            return web.Response(body=gzip.compress(page), content_type="application/gzip")
        if request.path == "/sitemap.xml.gz":
            origin = f"http://{request.host}"
            xml = (f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   f'<url><loc>{origin}/gz/one</loc></url>'
                   f'<url><loc>{origin}/gz/two</loc></url></urlset>').encode()
            return web.Response(body=gzip.compress(xml), content_type="application/gzip")
        if request.path == "/sitemap-huge.xml.gz":
            origin = f"http://{request.host}"
            xml = (f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   f'<url><loc>{origin}/gz/one</loc></url></urlset>').encode() + b" " * (600 * 1024)
            return web.Response(body=gzip.compress(xml), content_type="application/gzip")
        if request.path == "/sitemap-bad.xml.gz":
            return web.Response(body=gzip.compress(b"<urlset") + b"garbage", content_type="application/gzip")
        if request.path == "/robots.txt":
            return web.Response(status=state["robots_status"], body=state["robots_body"], headers=state["robots_headers"])
        if request.path == "/jump":
            return web.Response(status=302, body=b"jump", headers={"Location": "/map/%61?x=%62"})
        if request.path == "/jump-private":
            return web.Response(status=302, body=b"jump", headers={"Location": "/private/leaf"})
        if request.path == "/jump-offsite":
            return web.Response(status=302, body=b"jump", headers={"Location": "https://other.test/map.xml"})
        if request.path == "/jump-local":
            return web.Response(status=302, body=b"jump", headers={"Location": "http://10.0.0.1/robots.txt"})
        if request.path == "/robots-final":
            return web.Response(body=ROBOTS, content_type="text/plain")
        if request.path == "/soft":
            return web.Response(body=b'<html><head><meta http-equiv="refresh" content="0; url=/private/leaf"></head>' + PAGE,
                                content_type="text/html")
        if request.path == "/hold":
            started.set()
            await release.wait()
            return web.Response(body=PAGE, content_type="text/html")
        if request.path == "/rate":
            return web.Response(status=429, body=b"rate limited", headers={"Retry-After": "3600"})
        if request.path == "/disconnect":
            request.transport.close()
            return web.Response(text="unreachable")
        if request.path == "/xml-bad":
            return web.Response(body=b'<!DOCTYPE r [<!ENTITY x SYSTEM "http://other.test/secret">]><r>&x;</r>',
                                content_type="application/xml")
        if request.path == "/unsupported":
            return web.Response(body=b"binary", content_type="application/octet-stream")
        if request.path == "/charset":
            return web.Response(body='<html><body><a href="/привет">Link</a></body></html>'.encode("cp1251"),
                                headers={"Content-Type": "text/html; charset=windows-1251"})
        return web.Response(body=PAGE, content_type="text/html")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as server:
        yield server, calls, state, started, release


@pytest.fixture
async def map_worker(tmp_path, map_site):
    server, calls, state, started, release = map_site
    sources, sites = tmp_path / "sources.json", tmp_path / "sites.json"
    sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
    sites.write_text(json.dumps({"version": 1, "sites": [site_spec(str(server.make_url("/")).rstrip("/"))]}), encoding="utf-8")
    worker = Worker(str(sources), NetPolicy(["127.0.0.1/32"], test_mode=True), sites_path=str(sites))
    await worker.start()
    try:
        yield worker, server, calls, state, started, release
    finally:
        release.set()
        await worker.close()


def cached_rules(worker, body=ROBOTS):
    origin = worker.sites.sites["fixture-site"]["origin"]
    return response_record(origin=origin, status="ok", http_status=200, headers={"content-type": "text/plain"},
                           body=body, truncated=False, now_ms=int(time.time() * 1000) - 1)[0]


def grant(worker, server, path, *, kind="auto", record=None, attempt=None, timeout=3000, **changes):
    # TestServer.make_url() constructs a default yarl URL and decodes unreserved
    # percent escapes. Preserve the raw test input until the worker receives it.
    result = {"url": worker.sites.sites["fixture-site"]["origin"] + path, "kind": kind, "site_id": "fixture-site",
              "sites_manifest_sha256": worker.sites.digest, "attempt_id": attempt or secrets.token_hex(24),
              "max_requests": 1, "max_content_bytes": 2 * 1024 * 1024, "timeout_ms": timeout,
              "deadline_ms": int(time.time() * 1000) + timeout, "depth": 0, "max_entries": 1000, "redirect_hop": 0}
    if kind == "robots":
        result["robots_origin"] = worker.sites.sites["fixture-site"]["origin"]
    else:
        result["robots"] = copy.deepcopy(record if record is not None else cached_rules(worker))
    result.update(changes)
    return result


async def test_real_robots_then_discovery_has_two_grants_and_no_content_ingestion(map_worker):
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert robots["status"] == "ok" and robots["robots"]["state"] == "rules"
    assert robots["usage"] == {"external_requests": 1, "content_bytes": len(ROBOTS)}
    result = await worker.map_fetch(grant(worker, server, "/map", record=robots["robots"]))
    assert result["status"] == "ok" and result["usage"] == {"external_requests": 1, "content_bytes": len(PAGE)}
    assert len(result["discovery"]["candidates"]) == 2
    assert "html" not in result and "text" not in result
    assert [call["path"] for call in calls] == ["/robots.txt", "/map"]
    assert all(call["ua"] == CRAWLER_USER_AGENT for call in calls)
    proof = result["robots_decision"]
    assert proof["allowed"] is True and proof["target_url"] == result["final_url"]
    assert proof["body_sha256"] == robots["robots"]["body_sha256"]


async def test_disallowed_content_is_physically_zero_requests(map_worker):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, "/private/leaf"))
    assert result["status"] == "error" and result["error_code"] == "E_ROBOTS_DISALLOWED"
    assert result["http_status"] is None
    assert result["usage"] == {"external_requests": 0, "content_bytes": 0} and not calls
    assert result["robots_decision"]["allowed"] is False


@pytest.mark.parametrize("change,code", [
    ({"sites_manifest_sha256": "0" * 64}, "E_SITE_MANIFEST_MISMATCH"),
    ({"site_id": "unknown-site"}, "E_SITE_REVIEW"),
    ({"robots": None}, "E_ROBOTS_RECORD"),
    ({"deadline_ms": 1}, "E_TIMEOUT"),
])
async def test_invalid_admission_and_deadline_do_not_dispatch(map_worker, change, code):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, "/map", **change))
    assert result["error_code"] == code and result["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert not calls


@pytest.mark.parametrize("field,value", [("version", "old"), ("product_token", "OtherBot"),
                                         ("origin", "https://other.test"), ("body_sha256", "x" * 64),
                                         ("expires_at_ms", 1), ("state", "unknown"),
                                         ("rules", [{"pattern": "/", "allow": "true"}])])
async def test_stale_or_malformed_record_cannot_dispatch(map_worker, field, value):
    worker, server, calls, *_ = map_worker
    record = cached_rules(worker)
    record[field] = value
    result = await worker.map_fetch(grant(worker, server, "/map", record=record))
    assert result["error_code"] == "E_ROBOTS_RECORD" and result["usage"]["external_requests"] == 0
    assert not calls and "robots_decision" not in result


@pytest.mark.parametrize("change", [{"enabled": False}, {"path_prefixes": ["/allowed"]},
                                    {"review": {"expires_at": "2000-01-01T00:00:00Z"}}])
async def test_site_review_and_segment_scope_are_checked_for_every_request(map_worker, change):
    worker, server, calls, *_ = map_worker
    site = worker.sites.sites["fixture-site"]
    if "review" in change:
        site["review"].update(change["review"])
    else:
        site.update(change)
    result = await worker.map_fetch(grant(worker, server, "/allowedevil/map"))
    assert result["error_code"] in ("E_SITE_REVIEW", "E_SITE_SCOPE")
    assert result["usage"]["external_requests"] == 0 and not calls


async def test_http_redirect_is_returned_without_following_or_losing_encoded_location(map_worker):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, "/jump"))
    assert result["status"] == "redirect" and result["redirect_url"].endswith("/map/%61?x=%62")
    assert result["usage"] == {"external_requests": 1, "content_bytes": 4}
    assert [call["path"] for call in calls] == ["/jump"]
    follow = await worker.map_fetch(grant(worker, server, "/map/%61?x=%62", redirect_hop=1))
    assert follow["status"] == "ok"
    assert calls[-1]["raw_path"] == "/map/%61?x=%62"


async def test_each_content_redirect_target_needs_its_own_robots_admission(map_worker):
    worker, server, calls, *_ = map_worker
    redirect = await worker.map_fetch(grant(worker, server, "/jump-private"))
    assert redirect["status"] == "redirect"
    denied = await worker.map_fetch(grant(worker, server, "/private/leaf", redirect_hop=1))
    assert denied["error_code"] == "E_ROBOTS_DISALLOWED" and denied["usage"]["external_requests"] == 0
    assert [call["path"] for call in calls] == ["/jump-private"]


@pytest.mark.parametrize("path,code", [("/jump-offsite", "E_SITE_SCOPE"), ("/jump-local", "E_NETWORK_POLICY")])
async def test_redirect_to_other_origin_or_private_network_never_requests_target(map_worker, path, code):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, path))
    assert result["status"] == "error" and result["error_code"] == code
    assert result["usage"]["external_requests"] == 1 and len(calls) == 1


async def test_robots_redirects_are_single_hops_and_metadata_cannot_start_elsewhere(map_worker):
    worker, server, calls, state, *_ = map_worker
    refused = await worker.map_fetch(grant(worker, server, "/map", kind="robots"))
    assert refused["error_code"] == "E_SITE_SCOPE" and refused["usage"]["external_requests"] == 0
    state.update(robots_status=302, robots_body=b"jump", robots_headers={"Location": "/robots-final"})
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert first["status"] == "redirect" and "robots" not in first
    second = await worker.map_fetch(grant(worker, server, "/robots-final", kind="robots", redirect_hop=1))
    assert second["status"] == "ok" and second["robots"]["state"] == "rules"
    assert [call["path"] for call in calls] == ["/robots.txt", "/robots-final"]
    assert first["usage"]["external_requests"] == second["usage"]["external_requests"] == 1


async def test_soft_redirect_never_runs_an_implicit_second_request(map_worker):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, "/soft"))
    assert result["status"] == "ok" and result["usage"]["external_requests"] == 1
    assert [call["path"] for call in calls] == ["/soft"]


async def test_encoded_path_query_order_and_percent_case_reach_the_wire_unchanged(map_worker):
    worker, server, calls, *_ = map_worker
    path = "/map/%61/%7e?x=%62&x=%2f&x=%2F"
    params = grant(worker, server, path)
    result = await worker.map_fetch(params)
    assert result["status"] == "ok" and result["url"] == result["final_url"] == params["url"]
    assert result["robots_decision"]["target_url"] == params["url"]
    assert calls[0]["raw_path"] == path


@pytest.mark.parametrize("path", ["/map/%2e/secret", "/map/%2Fsecret", "/map/%5Csecret", "/map/%252e/secret",
                                 "/map/../private", "/map/%zz", "/map/\\private"])
async def test_ambiguous_raw_paths_are_refused_before_yarl_can_rewrite_them(map_worker, path):
    worker, server, calls, *_ = map_worker
    params = grant(worker, server, "/map")
    params["url"] = worker.sites.sites["fixture-site"]["origin"] + path
    result = await worker.map_fetch(params)
    assert result["status"] == "error" and result["usage"]["external_requests"] == 0 and not calls


@pytest.mark.parametrize("status,state", [(404, "allow_all"), (410, "allow_all"), (401, "deny_all"),
                                         (403, "deny_all"), (429, "deny_all"), (500, "deny_all"), (304, "deny_all")])
async def test_robots_http_policy_returns_a_complete_cache_record(map_worker, status, state):
    worker, server, calls, settings, *_ = map_worker
    settings.update(robots_status=status, robots_body=b"", robots_headers={"Content-Type": "text/plain"})
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    record = result["robots"]
    assert record["state"] == state and record["origin"] == worker.sites.sites["fixture-site"]["origin"]
    assert 0 < record["expires_at_ms"] - record["fetched_at_ms"] <= 24 * 60 * 60 * 1000
    assert result["usage"]["external_requests"] == len(calls) == 1
    assert result["status"] == ("error" if state == "deny_all" else "ok")


async def test_truncated_429_retains_backoff_and_never_becomes_an_allow_record(map_worker):
    worker, server, calls, settings, *_ = map_worker
    settings.update(robots_status=429, robots_body=b"too many requests", robots_headers={"Retry-After": "3600"})
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots", max_content_bytes=1))
    assert result["error_code"] == "E_RATE_LIMITED" and result["robots"]["state"] == "deny_all"
    assert result["retry_after_ms"] == 3600000 and result["usage"] == {"external_requests": 1, "content_bytes": 1}


@pytest.mark.parametrize("headers", [
    {"Cache-Control": "max-age=60", "Age": "120"},
    {"Expires": "Thu, 01 Jan 1970 00:00:00 GMT"},
    {"Age": "invalid"}, {"Cache-Control": "max-age=invalid"}, {"Cache-Control": "max-age=-1"},
    {"Cache-Control": "max-age=0"}, {"Vary": "*"}, {"Vary": "Accept-Language"},
    {"Expires": "invalid"}, {"Date": "invalid"}, {"Cache-Control": "no-cache"}, {"Cache-Control": "no-store"},
])
async def test_stale_or_unusable_cache_metadata_never_authorizes_a_content_request(map_worker, headers):
    worker, server, calls, settings, *_ = map_worker
    settings["robots_headers"] = {"Content-Type": "text/plain", **headers}
    response = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert response["status"] == "error"
    record = response["robots"]
    assert record["state"] == "deny_all" and record["diagnostics"] == ["robots_cache_revalidation_required"]
    refused = await worker.map_fetch(grant(worker, server, "/map", record=record))
    assert refused["error_code"] == "E_ROBOTS_DISALLOWED"
    assert refused["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert [call["path"] for call in calls] == ["/robots.txt"]


async def test_fresh_cache_ttl_is_shortened_by_age_and_absolute_expires(map_worker):
    worker, server, _, settings, *_ = map_worker
    settings["robots_headers"] = {"Content-Type": "text/plain", "Cache-Control": "max-age=60", "Age": "10",
                                  "Expires": formatdate(time.time() + 40, usegmt=True)}
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    record = result["robots"]
    assert result["status"] == "ok" and 0 < record["expires_at_ms"] - record["fetched_at_ms"] <= 40000


async def test_discovery_receives_the_complete_http_content_type_with_charset(map_worker):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, "/charset"))
    assert result["status"] == "ok", result
    assert result["discovery"]["candidates"][0]["url"].endswith("/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82")
    assert len(calls) == 1


async def test_512_kib_robots_limit_and_byte_grant_remain_hard(map_worker):
    worker, server, calls, settings, *_ = map_worker
    prefix = b"User-agent: BzsBot\nDisallow: /private\n#"
    settings["robots_body"] = prefix + b" " * (MAX_ROBOTS_BYTES - len(prefix))
    accepted = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert accepted["status"] == "ok" and accepted["usage"]["content_bytes"] == MAX_ROBOTS_BYTES
    settings["robots_body"] += b"!"
    denied = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert denied["robots"]["state"] == "deny_all" and denied["usage"]["content_bytes"] == MAX_ROBOTS_BYTES
    small = await worker.map_fetch(grant(worker, server, "/map", max_content_bytes=len(PAGE) - 1))
    assert small["error_code"] == "E_BYTE_BUDGET" and "discovery" not in small
    assert small["usage"] == {"external_requests": 1, "content_bytes": len(PAGE) - 1}


async def test_cancel_replay_and_cross_operation_attempt_ids_preserve_spent_usage(map_worker):
    worker, server, calls, *_ = map_worker
    params = grant(worker, server, "/map")
    first = await worker.map_fetch(params)
    replay = await worker.map_fetch(params)
    assert replay["error_code"] == "E_ATTEMPT_REPLAY" and replay["usage"] == first["usage"]
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    cancelled = await worker.map_fetch(params)
    assert cancelled["error_code"] == "E_CANCELLED" and cancelled["usage"] == first["usage"]
    native = await worker.fetch_extract({key: params[key] for key in
                                         ("url", "attempt_id", "max_requests", "max_content_bytes", "timeout_ms")})
    assert native["usage"] == first["usage"] and len(calls) == 1
    fresh = grant(worker, server, "/map")
    await worker.fetch_cancel({"attempt_id": fresh["attempt_id"]})
    stopped = await worker.map_fetch(fresh)
    assert stopped["usage"] == {"external_requests": 0, "content_bytes": 0} and len(calls) == 1


async def test_cancel_while_request_is_in_flight_keeps_usage_and_no_candidates(map_worker):
    worker, server, calls, _, started, release = map_worker
    params = grant(worker, server, "/hold")
    task = asyncio.create_task(worker.map_fetch(params))
    await asyncio.wait_for(started.wait(), 2)
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    release.set()
    result = await task
    assert result["error_code"] == "E_CANCELLED" and result["usage"]["external_requests"] == 1
    assert "discovery" not in result and len(calls) == 1


async def test_same_attempt_cannot_start_twice_concurrently(map_worker):
    worker, server, calls, _, started, release = map_worker
    params = grant(worker, server, "/hold")
    task = asyncio.create_task(worker.map_fetch(params))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(ValueError, match="already running"):
        await worker.map_fetch(params)
    release.set()
    result = await task
    assert result["usage"]["external_requests"] == len(calls) == 1


async def test_expired_record_after_host_queue_wait_is_rechecked_before_dispatch(map_worker):
    worker, server, calls, *_ = map_worker
    sem = worker.fetcher._sem("127.0.0.1")
    for _ in range(worker.fetcher.per_host):
        await sem.acquire()
    record = cached_rules(worker)
    record["expires_at_ms"] = int(time.time() * 1000) + 150
    task = asyncio.create_task(worker.map_fetch(grant(worker, server, "/map", record=record)))
    try:
        await asyncio.sleep(0.2)
    finally:
        for _ in range(worker.fetcher.per_host):
            sem.release()
    result = await task
    assert result["error_code"] == "E_ROBOTS_RECORD" and result["usage"]["external_requests"] == 0
    assert not calls and "robots_decision" not in result


async def test_site_lane_enforces_actual_start_interval_and_new_429_blocks_queued_hop(map_worker):
    worker, server, calls, *_ = map_worker
    worker.sites.sites["fixture-site"]["limits"]["min_interval_ms"] = 60
    first, second = await asyncio.gather(worker.map_fetch(grant(worker, server, "/map")),
                                         worker.map_fetch(grant(worker, server, "/map/again")))
    assert first["status"] == second["status"] == "ok"
    assert calls[1]["time"] - calls[0]["time"] >= 0.05
    calls.clear()
    rate, queued = await asyncio.gather(worker.map_fetch(grant(worker, server, "/rate")),
                                        worker.map_fetch(grant(worker, server, "/map/after-rate")))
    assert rate["error_code"] == "E_RATE_LIMITED" and queued["error_code"] == "E_SITE_COOLDOWN"
    assert [call["path"] for call in calls] == ["/rate"] and queued["usage"]["external_requests"] == 0


@pytest.mark.parametrize("path,code", [("/xml-bad", "E_DISCOVERY_PARSE"), ("/unsupported", "E_UNSUPPORTED_CONTENT")])
async def test_invalid_xml_and_unsupported_bodies_are_not_empty_success(map_worker, path, code):
    worker, server, calls, *_ = map_worker
    result = await worker.map_fetch(grant(worker, server, path))
    assert result["status"] == "error" and result["error_code"] == code and "discovery" not in result
    assert result["usage"]["external_requests"] == len(calls) == 1


async def test_single_hop_does_not_retry_disconnected_keepalive_connection(map_worker):
    worker, server, calls, *_ = map_worker
    await worker.map_fetch(grant(worker, server, "/map"))
    calls.clear()
    result = await worker.map_fetch(grant(worker, server, "/disconnect"))
    assert result["error_code"] == "E_NETWORK" and result["usage"]["external_requests"] == 1
    assert [call["path"] for call in calls] == ["/disconnect"]


@pytest.mark.parametrize("key,value", [("max_requests", 2), ("max_requests", True), ("depth", 9),
                                      ("max_content_bytes", 0), ("deadline_ms", True),
                                      ("redirect_hop", 6), ("max_entries", 1001), ("ignore_robots", True)])
async def test_invalid_private_grants_are_rejected_before_dispatch(map_worker, key, value):
    worker, server, calls, *_ = map_worker
    params = grant(worker, server, "/map")
    params[key] = value
    with pytest.raises(ValueError):
        await worker.map_fetch(params)
    assert not calls


def test_empty_default_site_registry_is_separate_from_search_adapter_approval():
    registry = SiteRegistry(None, NetPolicy())
    assert registry.digest == hashlib.sha256(EMPTY_MANIFEST).hexdigest() and registry.sites == {}


@pytest.mark.parametrize("change", [{"path_prefixes": ["/docs", "/docs/"]}, {"path_prefixes": ["/d%6fcs"]},
                                    {"limits": {"max_concurrency": 2}}, {"review": {"cache_policy": "maybe"}},
                                    {"enabled": 1}])
def test_invalid_site_manifest_shapes_are_rejected(tmp_path, change):
    spec = site_spec("https://fixture.test")
    for key, value in change.items():
        if key in ("limits", "review"):
            spec[key].update(value)
        else:
            spec[key] = value
    path = tmp_path / "sites.json"
    path.write_text(json.dumps({"version": 1, "sites": [spec]}), encoding="utf-8")
    with pytest.raises(ValueError):
        SiteRegistry(path, NetPolicy())


def test_duplicate_canonical_origins_cannot_multiply_site_limits(tmp_path):
    one, two = site_spec("https://fixture.test"), site_spec("https://FIXTURE.test:443")
    two["id"] = "other-site"
    path = tmp_path / "sites.json"
    path.write_text(json.dumps({"version": 1, "sites": [one, two]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate canonical"):
        SiteRegistry(path, NetPolicy())


def test_encoded_url_preservation_does_not_bypass_production_netpolicy():
    policy = NetPolicy()
    for value in ("http://127。0。0。1/map/%61", "http://10.0.0.1/map", "http://user:secret@fixture.test/map"):
        with pytest.raises(PolicyError):
            canonical_map_url(value, policy)


async def test_gzip_sitemap_archive_is_expanded_for_discovery(map_worker):
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    result = await worker.map_fetch(grant(worker, server, "/sitemap.xml.gz", kind="sitemap", record=robots["robots"]))
    assert result["status"] == "ok"
    origin = f"http://{server.host}:{server.port}"
    urls = sorted(c["url"] for c in result["discovery"]["candidates"])
    assert urls == [f"{origin}/gz/one", f"{origin}/gz/two"]
    # The archive bytes, not the expanded XML, are what was charged.
    assert result["usage"]["content_bytes"] < 200


async def test_gzip_sitemap_archive_over_the_hop_budget_is_refused(map_worker):
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    result = await worker.map_fetch(grant(worker, server, "/sitemap-huge.xml.gz", kind="sitemap",
                                          record=robots["robots"], max_content_bytes=512 * 1024))
    assert result["status"] == "error" and result["error_code"] == "E_CONTENT_TOO_LARGE"


async def test_gzip_sitemap_archive_with_trailing_garbage_is_refused(map_worker):
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    result = await worker.map_fetch(grant(worker, server, "/sitemap-bad.xml.gz", kind="sitemap",
                                          record=robots["robots"]))
    assert result["status"] == "error" and result["error_code"] == "E_DISCOVERY_PARSE"


async def test_gzip_body_is_not_expanded_for_page_hops(map_worker):
    # Archive expansion is scoped to sitemap/auto discovery hops: a gzip body
    # on a page hop must fail extraction, never silently decode to links.
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    result = await worker.map_fetch(grant(worker, server, "/page.gz", kind="page", record=robots["robots"]))
    assert result["status"] == "error" or result.get("page", {}).get("status") != "ok"
    assert not result.get("discovery", {}).get("candidates")



async def test_map_sustained_same_origin_hops_keep_admission_bounded_and_cancel_replay_free(map_worker):
    worker, server, calls, *_ = map_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    assert robots["status"] == "ok"

    completed = 0
    cancelled = 0
    for index in range(48):
        attempt = secrets.token_hex(24)
        params = grant(worker, server, f"/map?soak={index}", record=robots["robots"], attempt=attempt)
        if index % 8 == 0:
            await worker.fetch_cancel({"attempt_id": attempt})
            result = await worker.map_fetch(params)
            assert result["error_code"] == "E_CANCELLED"
            assert result["usage"] == {"external_requests": 0, "content_bytes": 0}
            assert "discovery" not in result
            cancelled += 1
        else:
            result = await worker.map_fetch(params)
            assert result["status"] == "ok"
            assert len(result["discovery"]["candidates"]) == 2
            assert result["usage"]["external_requests"] == 1
            completed += 1

    stats = worker.fetcher.admission_stats()
    assert completed == 42 and cancelled == 6
    assert len(calls) == 1 + completed  # robots + actual dispatches; cancelled IDs never hit the wire
    assert stats["entries"] == 1 and stats["holders"] == stats["waiters"] == 0
    assert len(worker._retired_attempts) == 49
