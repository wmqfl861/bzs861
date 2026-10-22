"""Conditional HTTP uses local fixtures, exact private grants and one-hop budgets."""
import asyncio
import copy
import json
import secrets
import time
from email.utils import formatdate

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict

from bzs.worker.netguard import NetPolicy
from bzs.worker.revalidation import CACHE_FIELDS, VERSION, validate_conditional
from bzs.worker.server import Worker
from tests.test_map_worker import cached_rules, grant, site_spec


MODIFIED = "Wed, 21 Oct 2015 07:28:00 GMT"
BODY = b"<html><head><title>Conditional fixture</title></head><body><main><p>" + (
    b"A complete local article about bounded requests and persistent refresh records. " * 20
) + b'</p><a href="/next">Next</a></main></body></html>'
ROBOTS = b"User-agent: BzsBot\nDisallow: /private\nAllow: /private/public\n"


@pytest.fixture
async def conditional_worker(tmp_path):
    calls = []
    state = {"page": {"status": None, "body": BODY,
                      "headers": [("ETag", 'W/"page-v1"'), ("Last-Modified", MODIFIED), ("Cache-Control", "max-age=60")]},
             "robots": {"status": None, "body": ROBOTS,
                        "headers": [("ETag", '"robots-v1"'), ("Cache-Control", "max-age=60")]}}

    async def handle(request):
        calls.append({"path": request.path, "raw_path": request.raw_path,
                      "headers": {key.lower(): value for key, value in request.headers.items()}})
        if request.path == "/redirect":
            return web.Response(status=302, body=b"jump", headers={"Location": "/page", "X-Robots-Tag": "noindex"})
        if request.path == "/soft":
            return web.Response(body=b'<html><head><meta name="robots" content="noindex">'
                                     b'<meta http-equiv="refresh" content="0; url=/page"></head><body>Jump</body></html>',
                                content_type="text/html")
        metadata = request.path == "/robots.txt"
        selected = state["robots" if metadata else "page"]
        headers = CIMultiDict(selected["headers"])
        headers.setdefault("Content-Type", "text/plain" if metadata else "text/html")
        status = selected["status"]
        if status is None:
            matches_tag = request.headers.get("If-None-Match") == headers.get("ETag") and "If-None-Match" in request.headers
            matches_date = ("If-None-Match" not in request.headers and "If-Modified-Since" in request.headers and
                            request.headers["If-Modified-Since"] == headers.get("Last-Modified"))
            status = 304 if matches_tag or matches_date else 200
        return web.Response(status=status, body=b"" if status == 304 else selected["body"], headers=headers)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as server:
        sources, sites = tmp_path / "sources.json", tmp_path / "sites.json"
        sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
        spec = site_spec(str(server.make_url("/")).rstrip("/"))
        spec["review"]["redistribution_policy"] = "allowed_with_limits"
        sites.write_text(json.dumps({"version": 1, "sites": [spec]}), encoding="utf-8")
        worker = Worker(str(sources), NetPolicy(["127.0.0.1/32"], test_mode=True), sites_path=str(sites))
        await worker.start()
        try:
            yield worker, server, calls, state
        finally:
            await worker.close()


def condition(cache, **changes):
    result = {key: cache[key] for key in ("version", "url", "etag", "last_modified", "request_profile")}
    result["binding_id"] = "b" * 64
    result.update(changes)
    return result


def read_grant(server, path="/page", **changes):
    result = {"url": str(server.make_url(path)), "attempt_id": secrets.token_hex(24),
              "max_requests": 1, "max_content_bytes": 100000, "timeout_ms": 2000}
    result.update(changes)
    return result


def old_robots_base(response, *, seconds=120):
    base = {"record": copy.deepcopy(response["robots"]), "http_cache": copy.deepcopy(response["http_cache"])}
    base["record"]["fetched_at_ms"] -= seconds * 1000
    base["record"]["expires_at_ms"] -= seconds * 1000
    base["http_cache"]["date"] = formatdate(base["record"]["fetched_at_ms"] / 1000, usegmt=True)
    return base


@pytest.mark.parametrize("bounded", [False, True])
async def test_full_read_establishes_metadata_and_matching_304_has_no_body(conditional_worker, bounded):
    worker, server, calls, _ = conditional_worker
    params = read_grant(server) if bounded else {"url": str(server.make_url("/page"))}
    first = await worker.fetch_extract(params)
    assert first["status"] == "ok" and first["text"]
    cache = first["http_cache"]
    assert set(cache) == CACHE_FIELDS and cache["version"] == VERSION
    assert cache["etag"] == 'W/"page-v1"' and cache["last_modified"] == MODIFIED
    params = read_grant(server) if bounded else {"url": str(server.make_url("/page"))}
    params["conditional"] = condition(cache)
    calls.clear()
    result = await worker.fetch_extract(params)
    assert result["status"] == "not_modified" and result["http_status"] == 304
    assert result["binding_id"] == "b" * 64 and result["final_url"] == cache["url"]
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0} and len(calls) == 1
    assert calls[0]["headers"]["if-none-match"] == cache["etag"]
    assert calls[0]["headers"]["if-modified-since"] == MODIFIED
    assert calls[0]["headers"]["accept-encoding"] == "gzip, deflate"
    assert not {"html", "text", "page", "discovery", "publication", "index_allowed"}.intersection(result)
    assert "publication_observation" not in result


async def test_last_modified_alone_is_sent_without_an_etag(conditional_worker):
    worker, server, calls, state = conditional_worker
    state["page"]["headers"] = [("Last-Modified", MODIFIED)]
    first = await worker.fetch_extract(read_grant(server))
    result = await worker.fetch_extract(read_grant(server, conditional=condition(first["http_cache"])))
    assert result["status"] == "not_modified"
    assert "if-none-match" not in calls[-1]["headers"] and calls[-1]["headers"]["if-modified-since"] == MODIFIED


@pytest.mark.parametrize("key,value", [
    ("etag", None), ("etag", True), ("etag", "*"), ("etag", "unquoted"),
    ("etag", '"one", "two"'), ("etag", '"one"\n"two"'), ("etag", '"one"\r\nX-Injected: value'),
    ("etag", '"é"'), ("etag", '"\x7f"'), ("etag", '"' + "x" * 1023 + '"'),
    ("last_modified", "Tuesday, 21-Oct-15 07:28:00 GMT"),
    ("last_modified", "Tue, 21 Oct 2015 07:28:00 GMT"),
    ("last_modified", "Wed, 32 Oct 2015 07:28:00 GMT"),
    ("last_modified", "Sat, 01 Jan 1600 00:00:00 GMT"),
    ("last_modified", MODIFIED + "\r\nInjected: yes"),
    ("last_modified", 0), ("request_profile", "X" * 64), ("binding_id", ""),
    ("url", ["https://example.test/"]), ("version", "unversioned"), ("extra", "ignored"),
])
async def test_malformed_conditional_is_rejected_before_network_and_attempt_retention(conditional_worker, key, value):
    worker, server, calls, _ = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    valid = condition(first["http_cache"])
    params = read_grant(server, conditional={**valid, key: value})
    calls.clear()
    with pytest.raises(ValueError):
        await worker.fetch_extract(params)
    assert not calls and params["attempt_id"] not in worker._retired_attempts
    params["conditional"] = valid
    assert (await worker.fetch_extract(params))["status"] == "not_modified" and len(calls) == 1


@pytest.mark.parametrize("change", [{"conditional": None}, {"headers": {"If-None-Match": '"override"'}},
                                      {"timeout_ms": True}, {"url": 123}])
async def test_new_conditional_read_fields_are_strict(conditional_worker, change):
    worker, server, calls, _ = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    params = read_grant(server, conditional=condition(first["http_cache"]))
    params.update(change)
    calls.clear()
    with pytest.raises(ValueError):
        await worker.fetch_extract(params)
    assert not calls


@pytest.mark.parametrize("headers", [
    [("ETag", '"one"'), ("ETag", '"two"')],
    [("Last-Modified", MODIFIED), ("Last-Modified", MODIFIED)],
    [("ETag", '"one", "two"')], [("Vary", "*")], [("Vary", "Accept-Language")],
    [("Vary", "Accept"), ("Vary", "Cookie")], [("Cache-Control", "no-store")],
    [("Cache-Control", "no-cache")], [("Cache-Control", "private, max-age=60")],
    [("Cache-Control", "max-age=0")], [("Cache-Control", "max-age=60, max-age=60")],
    [("Cache-Control", "max-age=60"), ("Cache-Control", "no-store")],
    [("Age", "0"), ("Age", "0")], [("Age", "-1")], [("Expires", "invalid")],
    [("Last-Modified", "Sat, 01 Jan 1600 00:00:00 GMT")],
    [("ETag", "")], [("Last-Modified", "")], [("Age", "")], [("Date", "")], [("Expires", "")],
    [("Date", MODIFIED), ("Date", MODIFIED)], [("Cache-Control", "x=" + "a" * 4097)],
])
async def test_full_200_remains_readable_but_unsafe_cache_headers_cannot_be_retained(conditional_worker, headers):
    worker, server, calls, state = conditional_worker
    names = {name.lower() for name, _ in headers}
    state["page"]["headers"] = headers + ([] if "etag" in names else [("ETag", '"v1"')])
    result = await worker.fetch_extract(read_grant(server))
    assert result["status"] == "ok" and result["text"] and "http_cache" not in result
    assert len(calls) == 1


async def test_url_mismatch_does_not_dispatch(conditional_worker):
    worker, server, calls, _ = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    calls.clear()
    result = await worker.fetch_extract(read_grant(server, "/different", conditional=condition(first["http_cache"])))
    assert result["error_code"] == "E_CONDITIONAL_MISMATCH"
    assert result["usage"] == {"external_requests": 0, "content_bytes": 0} and not calls


@pytest.mark.parametrize("profile_field", ["user_agent", "accept_language"])
async def test_profile_change_uses_the_original_request_for_a_full_200(conditional_worker, profile_field):
    worker, server, calls, _ = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    setattr(worker.fetcher, profile_field, "changed-local-profile")
    calls.clear()
    result = await worker.fetch_extract(read_grant(server, conditional=condition(first["http_cache"])))
    assert result["status"] == "ok" and result["http_status"] == 200 and result["text"]
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(BODY)} and len(calls) == 1
    assert not {"if-none-match", "if-modified-since"}.intersection(calls[0]["headers"])
    assert result["http_cache"]["request_profile"] != first["http_cache"]["request_profile"]


@pytest.mark.parametrize("profile_change", [False, True])
async def test_unsolicited_304_is_never_retried_or_treated_as_content(conditional_worker, profile_change):
    worker, server, calls, state = conditional_worker
    params = read_grant(server)
    if profile_change:
        first = await worker.fetch_extract(params)
        params = read_grant(server, conditional=condition(first["http_cache"]))
        worker.fetcher.user_agent = "new-profile"
    state["page"]["status"] = 304
    state["page"]["headers"].append(("X-Robots-Tag", "noindex, nosnippet"))
    calls.clear()
    result = await worker.fetch_extract(params)
    assert result["error_code"] == "E_HTTP_REVALIDATION" and len(calls) == 1
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0}
    assert result["publication_observation"]["index_allowed"] is False
    assert result["publication_observation"]["snippet_allowed"] is False
    assert not {"http_cache", "html", "text", "binding_id"}.intersection(result)


@pytest.mark.parametrize("headers", [[("ETag", '"different"')], [("ETag", '"a"'), ("ETag", '"a"')],
                                      [("Last-Modified", "Thu, 22 Oct 2015 07:28:00 GMT")],
                                      [("Vary", "User-Agent")], [("Cache-Control", "no-store")],
                                      [("Cache-Control", "no-cache")], [("Cache-Control", "max-age=0")],
                                      [("ETag", "")], [("Last-Modified", "")], [("Age", "")]])
async def test_ambiguous_304_preserves_restrictions_without_retry(conditional_worker, headers):
    worker, server, calls, state = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    state["page"].update(status=304, headers=headers + [("X-Robots-Tag", "nosnippet")])
    calls.clear()
    result = await worker.fetch_extract(read_grant(server, conditional=condition(first["http_cache"])))
    assert result["error_code"] == "E_HTTP_REVALIDATION" and len(calls) == 1
    assert result["publication_observation"]["snippet_allowed"] is False
    assert "http_cache" not in result and "text" not in result


async def test_304_inherits_missing_validator_fields_and_weak_etags_compare(conditional_worker):
    worker, server, _, state = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    state["page"].update(status=304, headers=[("ETag", '"page-v1"')])
    result = await worker.fetch_extract(read_grant(server, conditional=condition(first["http_cache"])))
    assert result["status"] == "not_modified" and result["http_cache"]["etag"] == '"page-v1"'
    assert result["http_cache"]["last_modified"] == MODIFIED and result["http_cache"]["cache_control"] == ""


@pytest.mark.parametrize("path", ["/redirect", "/soft"])
async def test_redirect_304_cannot_revalidate_the_initial_resource(conditional_worker, path):
    worker, server, calls, state = conditional_worker
    first = await worker.fetch_extract(read_grant(server))
    old = condition(first["http_cache"], url=str(server.make_url(path)))
    state["page"]["status"] = 304
    calls.clear()
    result = await worker.fetch_extract(read_grant(server, path, max_requests=2, conditional=old))
    assert result["error_code"] == "E_HTTP_REVALIDATION" and len(calls) == 2
    assert calls[0]["headers"]["if-none-match"] == old["etag"]
    assert not {"if-none-match", "if-modified-since"}.intersection(calls[1]["headers"])
    assert result["redirect_publication_observations"][0]["publication"]["index_allowed"] is False
    assert "http_cache" not in result and "text" not in result


async def test_page_304_keeps_single_hop_usage_and_actual_robots_decision(conditional_worker):
    worker, server, calls, _ = conditional_worker
    path = "/page/%61?x=%2f&x=%2F"
    first = await worker.map_fetch(grant(worker, server, path, kind="page"))
    assert first["http_cache"]["url"].endswith(path)
    calls.clear()
    result = await worker.map_fetch(grant(worker, server, path, kind="page", conditional=condition(first["http_cache"])))
    assert result["status"] == "not_modified" and result["binding_id"] == "b" * 64
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0} and len(calls) == 1
    assert result["robots_decision"]["allowed"] is True and result["robots_decision"]["target_url"].endswith(path)
    assert calls[0]["raw_path"] == path
    assert not {"page", "discovery", "html", "text", "publication"}.intersection(result)


@pytest.mark.parametrize("held", ["site", "host"])
async def test_conditional_page_rechecks_expiring_robots_at_actual_dispatch(conditional_worker, held):
    worker, server, calls, _ = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/page", kind="page"))
    record = cached_rules(worker)
    record["expires_at_ms"] = int(time.time() * 1000) + 50
    guard = worker.sites.lanes["fixture-site"] if held == "site" else worker.fetcher._sem("127.0.0.1")
    slots = 1 if held == "site" else worker.fetcher.per_host
    for _ in range(slots):
        await guard.acquire()
    calls.clear()
    task = asyncio.create_task(worker.map_fetch(grant(worker, server, "/page", kind="page", record=record,
                                                     conditional=condition(first["http_cache"]))))
    try:
        await asyncio.sleep(0.08)
    finally:
        for _ in range(slots):
            guard.release()
    result = await task
    assert result["error_code"] == "E_ROBOTS_RECORD" and not calls
    assert result["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert "robots_decision" not in result


async def test_map_redirect_has_no_implicit_conditional_follow(conditional_worker):
    worker, server, calls, _ = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/page", kind="page"))
    old = condition(first["http_cache"], url=str(server.make_url("/redirect")))
    calls.clear()
    result = await worker.map_fetch(grant(worker, server, "/redirect", kind="page", conditional=old))
    assert result["status"] == "redirect" and result["usage"]["external_requests"] == len(calls) == 1
    assert result["publication_observation"]["index_allowed"] is False
    assert "http_cache" not in result and "page" not in result
    mismatch = await worker.map_fetch(grant(worker, server, "/page", kind="page", conditional=old))
    assert mismatch["error_code"] == "E_CONDITIONAL_MISMATCH" and len(calls) == 1


async def test_304_preserves_current_site_links_only_ceiling(conditional_worker):
    worker, server, _, _ = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/page", kind="page"))
    worker.sites.sites["fixture-site"]["review"]["redistribution_policy"] = "links_only"
    result = await worker.map_fetch(grant(worker, server, "/page", kind="page", conditional=condition(first["http_cache"])))
    assert result["status"] == "not_modified" and result["publication_observation"]["snippet_allowed"] is False
    assert result["publication_observation"]["snippet_text"] == ""


async def test_expired_robots_base_only_becomes_fresh_after_a_bound_304(conditional_worker):
    worker, server, calls, state = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    base = old_robots_base(first)
    assert base["record"]["expires_at_ms"] < int(time.time() * 1000)
    calls.clear()
    denied = await worker.map_fetch(grant(worker, server, "/page", kind="page", record=base["record"]))
    assert denied["error_code"] == "E_ROBOTS_RECORD" and not calls
    state["robots"]["headers"] = [("ETag", '"robots-v1"'), ("Cache-Control", "max-age=3")]
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots",
                                          conditional=condition(base["http_cache"]), robots_revalidation_base=base))
    assert result["status"] == "not_modified" and result["usage"] == {"external_requests": 1, "content_bytes": 0}
    assert len(calls) == 1 and calls[0]["headers"]["if-none-match"] == '"robots-v1"'
    record = result["robots"]
    assert record["rules"] == base["record"]["rules"] and record["body_sha256"] == base["record"]["body_sha256"]
    assert 0 < record["expires_at_ms"] - record["fetched_at_ms"] <= 3000
    assert record["expires_at_ms"] > int(time.time() * 1000)
    forbidden = await worker.map_fetch(grant(worker, server, "/private/secret", kind="page", record=record))
    assert forbidden["error_code"] == "E_ROBOTS_DISALLOWED" and len(calls) == 1
    allowed = await worker.map_fetch(grant(worker, server, "/private/public", kind="page", record=record))
    assert allowed["status"] == "ok" and len(calls) == 2


async def test_robots_304_cannot_erase_omitted_short_cache_lifetime(conditional_worker):
    worker, server, _, state = conditional_worker
    state["robots"]["headers"] = [("ETag", '"robots-v1"'), ("Cache-Control", "max-age=3")]
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    base = old_robots_base(first)
    state["robots"]["headers"] = [("ETag", '"robots-v1"')]
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots",
                                          conditional=condition(base["http_cache"]), robots_revalidation_base=base))
    assert result["status"] == "not_modified" and result["http_cache"]["cache_control"] == ""
    assert 0 < result["robots"]["expires_at_ms"] - result["robots"]["fetched_at_ms"] <= 3000


@pytest.mark.parametrize("headers", [[("Cache-Control", "no-cache")], [("Cache-Control", "no-store")],
                                      [("Cache-Control", "max-age=0")], [("Vary", "Cookie")],
                                      [("Age", "120")], [("Expires", MODIFIED)], [("ETag", '"different"')]])
async def test_robots_304_without_trustworthy_freshness_fails_closed_once(conditional_worker, headers):
    worker, server, calls, state = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    base = old_robots_base(first)
    state["robots"].update(status=304, headers=headers)
    calls.clear()
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots",
                                          conditional=condition(base["http_cache"]), robots_revalidation_base=base))
    assert result["status"] == "error" and result["error_code"] == "E_HTTP_REVALIDATION"
    assert result["robots"]["state"] == "deny_all" and not result["robots"]["rules"]
    assert result["usage"] == {"external_requests": 1, "content_bytes": 0} and len(calls) == 1
    assert "http_cache" not in result and "binding_id" not in result


@pytest.mark.parametrize("mutation", ["missing", "allow_all", "deny_all", "body_hash", "old_version", "unknown_field",
                                       "cache_unknown", "profile", "etag", "no_cache", "future", "invalid_rule"])
async def test_malformed_robots_conditional_base_is_zero_network(conditional_worker, mutation):
    worker, server, calls, _ = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    base = old_robots_base(first)
    conditional = condition(base["http_cache"])
    if mutation in ("allow_all", "deny_all"):
        base["record"].update(state=mutation, rules=[])
    elif mutation == "body_hash":
        base["record"]["body_sha256"] = "not-a-hash"
    elif mutation == "old_version":
        base["record"]["version"] = "old"
    elif mutation == "unknown_field":
        base["permission"] = True
    elif mutation == "cache_unknown":
        base["http_cache"]["extra"] = "x"
    elif mutation == "profile":
        base["http_cache"]["request_profile"] = "0" * 64
    elif mutation == "etag":
        base["http_cache"]["etag"] = '"other"'
    elif mutation == "no_cache":
        base["http_cache"]["cache_control"] = "no-cache"
    elif mutation == "future":
        base["record"]["fetched_at_ms"] = int(time.time() * 1000) + 100000
    elif mutation == "invalid_rule":
        base["record"]["rules"][0]["allow"] = "true"
    params = grant(worker, server, "/robots.txt", kind="robots", conditional=conditional)
    if mutation != "missing":
        params["robots_revalidation_base"] = base
    calls.clear()
    with pytest.raises(ValueError):
        await worker.map_fetch(params)
    assert not calls and params["attempt_id"] not in worker._retired_attempts


async def test_robots_profile_change_uses_one_full_request_and_new_rules(conditional_worker):
    worker, server, calls, state = conditional_worker
    first = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    base = old_robots_base(first)
    worker.fetcher.accept_language = "changed-language"
    state["robots"]["body"] = b"User-agent: BzsBot\nDisallow: /\n"
    calls.clear()
    result = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots",
                                          conditional=condition(base["http_cache"]), robots_revalidation_base=base))
    assert result["status"] == "ok" and result["http_status"] == 200
    assert result["robots"]["rules"] == [{"pattern": "/", "allow": False}]
    assert len(calls) == 1 and "if-none-match" not in calls[0]["headers"]
    assert result["http_cache"]["request_profile"] != base["http_cache"]["request_profile"]


def test_empty_validator_pair_is_not_a_conditional_grant():
    with pytest.raises(ValueError):
        validate_conditional({"version": VERSION, "binding_id": "b" * 64, "url": "https://example.test/",
                              "etag": "", "last_modified": "", "request_profile": "0" * 64})
