"""P2-2: actual local HTTP proves each hop; a route never skips the first URI."""
import copy
import json
import secrets

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict

from bzs.worker.netguard import NetPolicy
from bzs.worker.redirect_cache import (
    CHAIN_VERSION, CONDITIONAL_VERSION, MAX_CHAIN_BYTES, encoded_size, make_chain,
    validate_redirect_conditional,
)
from bzs.worker.server import Worker
from bzs.worker.sites import SiteRegistry


BODY = b"An explicit local text representation for redirect caching. " * 30


@pytest.fixture
async def redirect_worker(tmp_path):
    calls = []
    state = {"hops": 1, "prefix_control": "max-age=10", "final_control": "max-age=60",
             "forced_304": False, "target": None, "duplicate_location": False, "soft": False, "final_redirect": False, "final_robots": None}

    async def handle(request):
        calls.append({"path": request.path, "headers": {key.lower(): value for key, value in request.headers.items()}})
        if request.path in ("/start", "/middle"):
            target = state["target"] or ("/middle" if request.path == "/start" and state["hops"] == 2 else "/page")
            if state["soft"] and request.path == "/start":
                target = "/soft"
            headers = CIMultiDict({"Location": target, "Cache-Control": state["prefix_control"]})
            if state["duplicate_location"]:
                headers.add("Location", "/page")
            return web.Response(status=301, body=b"jump", headers=headers)
        if request.path == "/soft":
            return web.Response(text='<html><head><meta http-equiv="refresh" content="0; url=/page"></head><body>Jump</body></html>',
                                content_type="text/html")
        if request.path == "/page" and state["final_redirect"]:
            return web.Response(status=302, body=b"next", headers={"Location": "/other", "Cache-Control": "max-age=10"})
        headers = {"ETag": '"redirect-v1"', "Cache-Control": state["final_control"], "Content-Type": "text/plain"}
        if state["final_robots"] is not None:
            headers["X-Robots-Tag"] = state["final_robots"]
        modified = not state["forced_304"] and request.headers.get("If-None-Match") != headers["ETag"]
        return web.Response(status=200 if modified else 304, body=BODY if modified else b"", headers=headers)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as server:
        sources = tmp_path / "sources.json"
        sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
        worker = Worker(str(sources), NetPolicy(["127.0.0.1/32"], test_mode=True))
        await worker.start()
        try:
            yield worker, server, calls, state
        finally:
            await worker.close()


def params(server, *, bounded=False, **changes):
    value = {"url": str(server.make_url("/start")), "timeout_ms": 2000}
    if bounded:
        value.update(attempt_id=secrets.token_hex(24), max_requests=4, max_content_bytes=65536)
    value.update(changes)
    return value


def route(first):
    cache, proof = first["http_cache"], first["http_redirect_chain"]
    conditional = {key: cache[key] for key in ("version", "url", "etag", "last_modified", "request_profile")}
    conditional["binding_id"] = "b" * 64
    return {"version": CONDITIONAL_VERSION, "request_url": proof["request_url"],
            "redirects": [hop["target_url"] for hop in proof["hops"]], "conditional": conditional}


def test_private_encoded_size_matches_core_slashes_backslashes_and_utf8():
    # The literal's Core json-c canonical encoding is 65 UTF-8 bytes. Plain
    # json.dumps omits six slash escapes and must not define this protocol cap.
    value = {"path/key": ["https://host.example/a/b", "a\\b/c", "中文"]}
    assert encoded_size(value) == 65


@pytest.mark.parametrize("length,core_bytes", [(3825, 8190), (3826, 8192), (3827, 8194), (3833, 8206), (3834, 8208)])
def test_optional_proof_boundary_uses_core_canonical_bytes(length, core_bytes):
    original = "https://bounds.example/start"
    prefix = "https://bounds.example/"
    final = prefix + "x" * (length - len(prefix))
    profile = "a" * 64
    freshness = {"version": "bzs-http-freshness-1", "url": original, "request_profile": profile,
                 "cache_control": "max-age=60", "vary": "", "date": "", "age": "", "expires": "", "pragma": "",
                 "received_at_ms": 1700000000000, "response_delay_ms": 0, "invalid": False}
    hops = [{"url": original, "target_url": final, "http_status": 301,
             "content_bytes": 0, "http_freshness": freshness}]
    expected = {"version": CHAIN_VERSION, "request_url": original, "final_url": final, "hops": hops}
    assert encoded_size(expected) == core_bytes
    result = make_chain(original, final, hops, profile)
    assert (result is not None) is (core_bytes <= MAX_CHAIN_BYTES)
    if result is not None:
        assert result == expected


@pytest.mark.parametrize("prefix_control", ["max-age=60", "no-store"])
async def test_oversized_optional_proof_keeps_complete_body_and_prefix_observations(redirect_worker, prefix_control):
    worker, server, calls, state = redirect_worker
    target = "/" + "p" * 4000
    assert len(str(server.make_url(target))) <= 4096
    state.update(target=target, prefix_control=prefix_control)
    result = await worker.fetch_extract(params(server, bounded=True))
    assert result["status"] == "ok" and result["http_status"] == 200 and result["text"]
    assert result["final_url"] == str(server.make_url(target))
    assert "http_redirect_chain" not in result and "representation_publication" not in result
    observations = result["http_redirect_observations"]
    assert len(observations) == 1 and observations[0]["url"] == str(server.make_url("/start"))
    assert observations[0]["http_freshness"]["cache_control"] == prefix_control
    assert [item["path"] for item in calls] == ["/start", target]
    assert result["usage"] == {"external_requests": 2, "content_bytes": 4 + len(BODY)}


def links_only_site(worker, server, tmp_path, prefix):
    site = {"id": "redirect-scope", "enabled": True, "origin": str(server.make_url("/")).rstrip("/"),
            "path_prefixes": [prefix], "review": {
                "status": "approved", "payment_scheme": "no_payment", "automated_access": "approved",
                "checked_at": "1970-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                "terms_url": "https://example.org/local-fixture-terms", "cache_policy": "allowed_with_limits",
                "redistribution_policy": "links_only", "approved_by": "local-redirect-fixture"},
            "limits": {"max_concurrency": 1, "min_interval_ms": 0, "request_quota": 1000, "quota_window_seconds": 86400}}
    path = tmp_path / "sites-scope.json"
    path.write_text(json.dumps({"version": 1, "sites": [site]}))
    worker.sites = SiteRegistry(path, worker.fetcher.policy)


@pytest.mark.parametrize("restricted_path", ["/start", "/page"])
async def test_http_chain_final_publication_excludes_original_only_site_rules(redirect_worker, tmp_path, restricted_path):
    worker, server, calls, _ = redirect_worker
    links_only_site(worker, server, tmp_path, restricted_path)
    first = await worker.fetch_extract(params(server))
    final_restricted = restricted_path == "/page"
    assert first["status"] == "ok" and first["http_redirect_chain"]
    # The existing Worker publication contract always includes every matching
    # site. Only the explicit private final record separates original scope.
    assert not first["publication"]["snippet_allowed"]
    assert first["publication"]["snippet_text"] == first["publication"]["snippet_title"] == ""
    assert first["index_allowed"] is first["publication"]["index_allowed"]
    wrapper = first.get("representation_publication")
    if final_restricted:
        assert wrapper is None  # Equal final/aggregate policies need no duplicate.
    else:
        assert set(wrapper) == {"version", "url", "publication"}
        assert wrapper["version"] == "bzs-http-representation-publication-1"
        assert wrapper["url"] == first["final_url"] == str(server.make_url("/page"))
        assert wrapper["publication"]["snippet_allowed"] and wrapper["publication"]["snippet_text"]
        assert wrapper["publication"]["snippet_title"]
    calls.clear()
    renewed = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert renewed["status"] == "not_modified" and renewed["redirect_revalidation"]
    assert "representation_publication" not in renewed
    observed = renewed.get("publication_observation")
    assert (observed is not None and not observed["snippet_allowed"]) is final_restricted
    assert [item["path"] for item in calls] == ["/start", "/page"]
    assert "if-none-match" not in calls[0]["headers"] and calls[1]["headers"]["if-none-match"] == '"redirect-v1"'


async def test_rejected_routed_304_does_not_attribute_original_site_rule_to_final(redirect_worker, tmp_path):
    worker, server, calls, state = redirect_worker
    links_only_site(worker, server, tmp_path, "/start")
    first = await worker.fetch_extract(params(server))
    state.update(hops=2, forced_304=True)
    calls.clear()
    failed = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert failed["status"] == "http_error" and failed["error_code"] == "E_HTTP_REVALIDATION"
    assert "publication_observation" not in failed
    assert len(calls) == 3 and all("if-none-match" not in item["headers"] for item in calls)


@pytest.mark.parametrize("hops", [1, 2])
async def test_complete_http_chain_reports_each_actual_cache_policy(redirect_worker, hops):
    worker, server, calls, state = redirect_worker
    state["hops"] = hops
    result = await worker.fetch_extract(params(server, bounded=True))
    assert result["status"] == "ok" and result["text"]
    proof = result["http_redirect_chain"]
    assert proof["version"] == "bzs-http-redirect-chain-1"
    assert proof["request_url"] == str(server.make_url("/start")) and proof["final_url"] == str(server.make_url("/page"))
    assert len(proof["hops"]) == len(result["http_redirect_observations"]) == hops
    assert len(calls) == result["usage"]["external_requests"] == hops + 1
    assert result["usage"]["content_bytes"] == len(BODY) + 4 * hops
    previous = proof["request_url"]
    for hop, observed in zip(proof["hops"], result["http_redirect_observations"]):
        assert hop["url"] == previous and hop["http_status"] == 301 and hop["content_bytes"] == 4
        assert observed["http_freshness"] == hop["http_freshness"]
        assert hop["http_freshness"]["url"] == hop["url"]
        assert hop["http_freshness"]["request_profile"] == result["http_freshness"]["request_profile"]
        assert hop["http_freshness"]["received_at_ms"] <= result["http_freshness"]["received_at_ms"]
        previous = hop["target_url"]
    assert previous == result["final_url"]


@pytest.mark.parametrize("hops", [1, 2])
@pytest.mark.parametrize("bounded", [False, True])
async def test_redirect_304_conditions_only_the_exact_final_uri_and_charges_prefix(redirect_worker, hops, bounded):
    worker, server, calls, state = redirect_worker
    state["hops"] = hops
    first = await worker.fetch_extract(params(server, bounded=bounded))
    calls.clear()
    result = await worker.fetch_extract(params(server, bounded=bounded, redirect_conditional=route(first)))
    assert result["status"] == "not_modified" and result["http_status"] == 304
    assert result["binding_id"] == "b" * 64 and result["redirect_revalidation"]["version"] == CONDITIONAL_VERSION
    assert len(calls) == result["usage"]["external_requests"] == hops + 1
    assert result["usage"]["content_bytes"] == (4 * hops if bounded else 0)
    assert calls[0]["path"] == "/start" and calls[-1]["path"] == "/page"
    assert all("if-none-match" not in item["headers"] and "if-modified-since" not in item["headers"] for item in calls[:-1])
    assert calls[-1]["headers"]["if-none-match"] == '"redirect-v1"'
    assert not {"html", "text", "page", "discovery", "publication", "representation_publication"}.intersection(result)


async def test_changed_prefix_rejoining_old_final_is_one_full_authorized_attempt(redirect_worker):
    worker, server, calls, state = redirect_worker
    first = await worker.fetch_extract(params(server))
    state["hops"] = 2
    calls.clear()
    result = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert result["status"] == "ok" and result["http_status"] == 200
    assert len(calls) == result["usage"]["external_requests"] == 3
    assert all("if-none-match" not in item["headers"] for item in calls)
    assert "redirect_revalidation" not in result


async def test_new_redirect_from_conditioned_final_never_forwards_its_validator(redirect_worker):
    worker, server, calls, state = redirect_worker
    first = await worker.fetch_extract(params(server))
    state["final_redirect"] = True
    calls.clear()
    result = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert result["status"] == "ok" and result["final_url"] == str(server.make_url("/other"))
    assert [item["path"] for item in calls] == ["/start", "/page", "/other"]
    assert "if-none-match" not in calls[0]["headers"]
    assert calls[1]["headers"]["if-none-match"] == '"redirect-v1"'
    assert "if-none-match" not in calls[2]["headers"] and "if-modified-since" not in calls[2]["headers"]
    assert len(result["http_redirect_chain"]["hops"]) == 2


async def test_changed_read_profile_disables_old_conditions_without_retry(redirect_worker):
    worker, server, calls, _ = redirect_worker
    first = await worker.fetch_extract(params(server))
    worker.fetcher.accept_language = "en-US"
    calls.clear()
    result = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert result["status"] == "ok" and len(calls) == 2
    assert all("if-none-match" not in item["headers"] for item in calls)
    assert result["http_freshness"]["request_profile"] != first["http_freshness"]["request_profile"]


async def test_304_after_changed_prefix_is_rejected_without_implicit_full_get(redirect_worker):
    worker, server, calls, state = redirect_worker
    first = await worker.fetch_extract(params(server))
    state.update(hops=2, forced_304=True)
    calls.clear()
    result = await worker.fetch_extract(params(server, redirect_conditional=route(first)))
    assert result["status"] == "http_error" and result["error_code"] == "E_HTTP_REVALIDATION"
    assert len(calls) == result["usage"]["external_requests"] == 3
    assert all("if-none-match" not in item["headers"] for item in calls)


async def test_soft_redirects_never_create_http_chain_proof(redirect_worker):
    worker, server, calls, state = redirect_worker
    state["soft"] = True
    result = await worker.fetch_extract(params(server))
    assert result["status"] == "ok" and len(calls) == 3
    assert "http_redirect_chain" not in result and "representation_publication" not in result
    assert len(result["http_redirect_observations"]) == 1


async def test_no_store_prefix_survives_a_later_ssrf_refusal(redirect_worker):
    worker, server, calls, state = redirect_worker
    state.update(prefix_control="no-store", target="http://127.0.0.2/private")
    result = await worker.fetch_extract(params(server, bounded=True))
    assert result["status"] == "blocked_by_policy" and len(calls) == 1
    assert result["usage"]["external_requests"] == 1
    assert result["http_redirect_observations"][0]["http_freshness"]["cache_control"] == "no-store"
    assert "http_redirect_chain" not in result


async def test_request_grant_stops_before_final_without_resetting_for_hops(redirect_worker):
    worker, server, calls, state = redirect_worker
    state["hops"] = 2
    result = await worker.fetch_extract(params(server, bounded=True, max_requests=2))
    assert result["status"] == "budget_exhausted" and result["error_code"] == "E_REQUEST_BUDGET"
    assert [item["path"] for item in calls] == ["/start", "/middle"]
    assert result["usage"] == {"external_requests": 2, "content_bytes": 8}
    assert "http_redirect_chain" not in result


async def test_body_grant_stops_inside_redirect_without_dispatching_final(redirect_worker):
    worker, server, calls, _ = redirect_worker
    result = await worker.fetch_extract(params(server, bounded=True, max_content_bytes=2))
    assert result["status"] == "budget_exhausted" and len(calls) == 1
    assert result["usage"] == {"external_requests": 1, "content_bytes": 2}
    assert len(result["http_redirect_observations"]) == 1 and "http_redirect_chain" not in result


async def test_ambiguous_location_can_never_become_reusable_proof(redirect_worker):
    worker, server, calls, state = redirect_worker
    state["duplicate_location"] = True
    result = await worker.fetch_extract(params(server))
    assert result["status"] == "ok" and len(calls) == 2
    assert "http_redirect_chain" not in result and result["http_redirect_observations"]


@pytest.mark.parametrize("kind", ["unknown", "cycle", "too_many", "wrong_final", "oversized", "both"])
async def test_redirect_condition_is_strict_before_attempt_retention_or_dispatch(redirect_worker, kind):
    worker, server, calls, _ = redirect_worker
    first = await worker.fetch_extract(params(server))
    grant = route(first)
    if kind == "unknown":
        grant["extra"] = True
    elif kind == "cycle":
        grant["redirects"] = [grant["request_url"], grant["conditional"]["url"]]
    elif kind == "too_many":
        grant["redirects"] = [str(server.make_url("/hop-" + str(i))) for i in range(6)]
        grant["conditional"]["url"] = grant["redirects"][-1]
    elif kind == "wrong_final":
        grant["conditional"]["url"] = str(server.make_url("/other"))
    elif kind == "oversized":
        grant["redirects"] = [str(server.make_url("/" + str(i) + "x" * 3000)) for i in range(3)]
        grant["conditional"]["url"] = grant["redirects"][-1]
        assert len(json.dumps(grant)) > MAX_CHAIN_BYTES
    value = params(server, bounded=True, redirect_conditional=grant)
    if kind == "both":
        value["conditional"] = copy.deepcopy(grant["conditional"])
    calls.clear()
    with pytest.raises(ValueError):
        await worker.fetch_extract(value)
    assert not calls and value["attempt_id"] not in worker._retired_attempts


def test_redirect_grant_never_accepts_non_http_or_private_credentials_as_identity():
    grant = {"version": CONDITIONAL_VERSION, "request_url": "https://public.example/start",
             "redirects": ["https://user:secret@public.example/page"],
             "conditional": {"version": "bzs-http-revalidation-1", "url": "https://user:secret@public.example/page",
                             "etag": '"v1"', "last_modified": "", "request_profile": "a" * 64, "binding_id": "b" * 64}}
    with pytest.raises(ValueError):
        validate_redirect_conditional(grant, grant["request_url"])


@pytest.mark.parametrize("control", ["max-age=10", "no-cache", ""])
async def test_complete_receipt_distinguishes_scope_without_granting_cache_lifetime(redirect_worker, tmp_path, control):
    worker, server, _, state = redirect_worker
    links_only_site(worker, server, tmp_path, "/start")
    state["prefix_control"] = control
    result = await worker.fetch_extract(params(server))
    assert result["status"] == "ok" and result["http_redirect_chain"]
    assert result["http_redirect_chain"]["hops"][0]["http_freshness"]["cache_control"] == control
    assert not result["publication"]["snippet_allowed"]
    assert result["representation_publication"]["publication"]["snippet_allowed"]


async def test_actual_final_noindex_remains_in_both_publication_scopes(redirect_worker, tmp_path):
    worker, server, _, state = redirect_worker
    links_only_site(worker, server, tmp_path, "/start")
    state["final_robots"] = "noindex"
    result = await worker.fetch_extract(params(server))
    assert result["status"] == "ok" and not result["index_allowed"]
    assert not result["publication"]["index_allowed"] and not result["publication"]["snippet_allowed"]
    final = result.get("representation_publication", {}).get("publication", result["publication"])
    assert not final["index_allowed"] and not final["snippet_allowed"]
    assert not result["publication_observation"]["index_allowed"]


@pytest.mark.parametrize("context", ["empty_content", "http_error", "non200", "truncated", "unproven"])
async def test_final_scope_record_is_absent_without_a_complete_successful_200(redirect_worker, tmp_path, monkeypatch, context):
    worker, server, _, _ = redirect_worker
    # Capture a complete internal extraction before the wrapper is installed.
    raw = await worker._fetch_extract(params(server), None)
    links_only_site(worker, server, tmp_path, "/start")
    if context in ("empty_content", "http_error"):
        raw["status"] = context
    elif context == "non200":
        raw["http_status"] = 203
    elif context == "truncated":
        raw["warnings"] = ["download_truncated"]
    else:
        raw.pop("http_redirect_chain")

    async def completed(*_args, **_kwargs):
        return copy.deepcopy(raw)

    monkeypatch.setattr(worker, "_fetch_extract", completed)
    result = await worker.fetch_extract(params(server))
    assert "representation_publication" not in result
    assert not result["publication"]["snippet_allowed"]
    if context == "truncated":
        assert not result["index_allowed"]
        assert all(not result["publication"][key] for key in ("index_allowed", "snippet_allowed", "follow_allowed"))


async def test_final_scope_maximum_ascii_fields_keep_existing_rpc_bound(tmp_path, monkeypatch):
    # This is a completed-extraction boundary test; it opens no origin socket.
    source = tmp_path / "maximum-sources.json"
    source.write_text('{"version":1,"sources":[]}', encoding="utf-8")
    worker = Worker(str(source), NetPolicy())

    class Origin:
        @staticmethod
        def make_url(path):
            return "https://maximum.example" + path

    links_only_site(worker, Origin(), tmp_path, "/start")
    final = {"version": "bzs-publication-1", "index_allowed": True, "snippet_allowed": True,
             "follow_allowed": True, "snippet_text": "s" * (1024 * 1024), "snippet_title": "t" * 1024,
             "directives": [], "diagnostics": []}
    raw = {"status": "ok", "http_status": 200, "html": "h" * (8 * 1024 * 1024),
           "text": "t" * (2 * 1024 * 1024), "title": "t" * 1024, "content_type": "text/plain",
           "extractor": "completed-fixture", "index_allowed": True, "publication": final,
           "final_url": Origin.make_url("/page"), "redirects": [Origin.make_url("/page")],
           "warnings": [], "http_redirect_chain": {"version": CHAIN_VERSION}}

    async def completed(*_args, **_kwargs):
        return copy.deepcopy(raw)

    monkeypatch.setattr(worker, "_fetch_extract", completed)
    result = await worker.fetch_extract({"url": Origin.make_url("/start")})
    assert result["publication"]["snippet_text"] == ""
    assert len(result["representation_publication"]["publication"]["snippet_text"]) == 1024 * 1024
    # The actual server serializer's default ASCII mode; the Core frame and
    # JSON parser retain their original 24 MiB limit. Arbitrary escape-heavy
    # combinations still have to satisfy that existing transport bound.
    assert len(json.dumps({"result": result}).encode("utf-8")) < 24 * 1024 * 1024
    worker.sites = SiteRegistry(None, worker.fetcher.policy)
    equal = await worker.fetch_extract({"url": Origin.make_url("/start")})
    assert "representation_publication" not in equal
    assert equal["publication"] == final
