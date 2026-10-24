"""The body-crawl path performs one physical hop under the existing admission."""
import asyncio
import gzip
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bzs.worker.netguard import NetPolicy
from bzs.worker.robots import CRAWLER_USER_AGENT
from bzs.worker.server import Worker
from tests.test_map_worker import ROBOTS, cached_rules, grant, site_spec


BODY = "Public body text about bounded crawling, restartable queues and accountable requests. " * 10
PAGE = ('<html><head><base href="/docs/"><title>Public crawl title</title></head><body><main>'
        f'<p>{BODY}<span data-nosnippet>SECRETPHRASE directly readable only</span> Public tail.</p>'
        '<a href="next/%61?x=%2f&amp;x=%2F">Next page</a><a href="/private/target">Restricted path</a>'
        '<a href="/skip" rel="nofollow">Skip link</a></main></body></html>').encode()


@pytest.fixture
async def crawl_worker(tmp_path):
    calls = []
    state = {"body": PAGE, "headers": [("Content-Type", "text/html")], "status": 200}
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        calls.append({"path": request.path, "raw_path": request.raw_path, "ua": request.headers.get("User-Agent")})
        if request.path == "/robots.txt":
            return web.Response(body=ROBOTS, content_type="text/plain")
        if request.path == "/redirect":
            return web.Response(status=302, body=b"jump", headers={"Location": "/page/%61?x=%2f&x=%2F"})
        if request.path == "/redirect-first":
            return web.Response(status=302, body=b"first", headers={"Location": "/redirect-restricted"})
        if request.path == "/redirect-restricted":
            return web.Response(status=302, body=b"jump", headers=[
                ("Location", state.get("redirect_target", "/page/a")),
                *state.get("redirect_headers", [("X-Robots-Tag", "BzsBot: noindex")])])
        if request.path == "/soft-restricted":
            html = ('<html><head><meta http-equiv="refresh" content="0; url=' +
                    state.get("soft_target", "/page/a") + '">' +
                    state.get("soft_policy_html", '<meta name="BzsBot" content="noindex">') +
                    '</head><body></body></html>')
            return web.Response(text=html, content_type="text/html")
        if request.path == "/redirect-private":
            return web.Response(status=302, body=b"jump", headers={"Location": "/private/target"})
        if request.path == "/redirect-external":
            return web.Response(status=302, body=b"jump", headers={"Location": "https://outside.example/page"})
        if request.path == "/soft":
            return web.Response(body=b'<html><head><meta http-equiv="refresh" content="0; url=/private/target"></head></html>',
                                content_type="text/html")
        if request.path == "/gzip":
            return web.Response(body=gzip.compress(PAGE), content_type="text/html", headers={"Content-Encoding": "gzip"})
        if request.path in ("/exact", "/extra"):
            response = web.StreamResponse(headers={"Content-Type": "text/html"})
            await response.prepare(request)
            await response.write(PAGE)
            await asyncio.sleep(0.08)
            try:
                if request.path == "/extra":
                    await response.write(b"!")
                await response.write_eof()
            except ConnectionError:
                pass
            return response
        if request.path == "/hold":
            started.set()
            await release.wait()
        if request.path == "/timeout":
            response = web.StreamResponse(headers={"Content-Type": "text/plain", "X-Robots-Tag": "BzsBot: noindex"})
            await response.prepare(request)
            await response.write(b"first bytes")
            started.set()
            await release.wait()
            try:
                await response.write_eof()
            except ConnectionError:
                pass
            return response
        if request.path == "/disconnect":
            request.transport.close()
            return web.Response(body=PAGE, headers={"X-Robots-Tag": "noindex"})
        if request.path == "/xml-safe":
            return web.Response(body=b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/later</loc></url></urlset>',
                                content_type="application/xml")
        if request.path == "/xml-dtd":
            body = f'<!DOCTYPE urlset [<!ENTITY x SYSTEM "http://127.0.0.1:{request.url.port}/secret">]><urlset><url><loc>&x;</loc></url></urlset>'
            return web.Response(body=body.encode(), content_type="application/xml")
        if request.path == "/long-content-type":
            return web.Response(body=PAGE, headers={"Content-Type": "text/html; " + "x" * 1200})
        return web.Response(status=state["status"], body=state["body"], headers=state["headers"])

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
            yield worker, server, calls, state, started, release
        finally:
            release.set()
            await worker.close()


def page_grant(worker, server, path="/page", **kwargs):
    return grant(worker, server, path, kind="page", **kwargs)


async def test_real_robots_then_page_returns_private_artifact_safe_publication_and_candidates(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    robots = await worker.map_fetch(grant(worker, server, "/robots.txt", kind="robots"))
    result = await worker.map_fetch(page_grant(worker, server, record=robots["robots"], depth=2))
    assert result["status"] == "ok" and result["page"]["status"] == "ok"
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(PAGE)}
    assert result["robots_decision"]["allowed"] is True
    assert result["robots_decision"]["target_url"] == result["page"]["final_url"] == result["final_url"]
    page = result["page"]
    assert page["html"] == PAGE.decode() and "SECRETPHRASE" in page["text"]
    assert page["publication"] == result["publication"]
    assert page["publication"]["snippet_text"] and "SECRETPHRASE" not in page["publication"]["snippet_text"]
    assert page["publication"]["snippet_title"] == "Public crawl title"
    candidates = result["discovery"]["candidates"]
    assert len(candidates) == 2 and all(item["depth"] == 3 for item in candidates)
    assert candidates[0]["url"].endswith("/docs/next/%61?x=%2f&x=%2F")
    assert candidates[0]["path_depth"] == 3 and candidates[1]["path_depth"] == 2
    # Candidate URLs have not been dispatched: core makes the next admission.
    assert [item["path"] for item in calls] == ["/robots.txt", "/page"]
    assert all(item["ua"] == CRAWLER_USER_AGENT for item in calls)


async def test_repeated_robot_headers_preserve_applicability_on_the_actual_wire(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["headers"] = [("Content-Type", "text/html"), ("X-Robots-Tag", "googlebot: noindex"),
                        ("X-Robots-Tag", "BzsBot: nosnippet"), ("X-Robots-Tag", "nofollow")]
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["publication"]["index_allowed"] is True
    assert result["publication"]["snippet_allowed"] is False
    assert result["publication_observation"]["snippet_text"] == result["publication_observation"]["snippet_title"] == ""
    assert result["publication_observation"]["index_allowed"] is True
    assert result["publication"]["follow_allowed"] is False
    assert result["publication"]["snippet_text"] == ""
    assert result["page"]["links"] == result["discovery"]["candidates"] == []
    assert result["page"]["text"] and len(calls) == 1
    # MAP discovery also honors an applicable HTTP nofollow, not just HTML meta.
    mapped = await worker.map_fetch(grant(worker, server, "/page"))
    assert mapped["status"] == "ok" and mapped["discovery"]["candidates"] == []


async def test_links_only_site_review_withholds_text_and_title_without_losing_canonical_read(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    worker.sites.sites["fixture-site"]["review"]["redistribution_policy"] = "links_only"
    result = await worker.map_fetch(page_grant(worker, server))
    policy = result["publication"]
    assert policy["index_allowed"] is True and policy["snippet_allowed"] is False
    assert policy["snippet_text"] == policy["snippet_title"] == ""
    assert policy["diagnostics"] == ["site_links_only"]
    assert result["page"]["text"] and result["page"]["title"] == "Public crawl title"
    assert result["usage"]["external_requests"] == len(calls) == 1


@pytest.mark.parametrize("bounded", [False, True])
async def test_explicit_read_and_read_many_refresh_cannot_lift_known_links_only_ceiling(crawl_worker, bounded):
    worker, server, calls, *_ = crawl_worker
    site = worker.sites.sites["fixture-site"]
    site["review"]["redistribution_policy"] = "links_only"
    # Explicit read admission is independent from auto-crawl admission. Even a
    # disabled/expired site review must not be mistaken for a new snippet grant.
    site["enabled"] = False
    site["review"]["expires_at"] = "2000-01-01T00:00:00Z"
    params = {"url": str(server.make_url("/page"))}
    if bounded:
        crawl = page_grant(worker, server)
        params.update({key: crawl[key] for key in ("attempt_id", "max_requests", "max_content_bytes", "timeout_ms")})
    result = await worker.fetch_extract(params)
    assert result["status"] == "ok" and result["text"]
    assert result["publication"]["index_allowed"] is True
    assert result["publication"]["snippet_allowed"] is False
    assert result["publication"]["snippet_text"] == result["publication"]["snippet_title"] == ""
    assert result["publication"]["diagnostics"] == ["site_links_only"] and len(calls) == 1


@pytest.mark.parametrize("prefix", ["/redirect", "/page"])
async def test_explicit_read_ceiling_matches_both_original_and_final_redirect_scopes(crawl_worker, prefix):
    worker, server, calls, *_ = crawl_worker
    site = worker.sites.sites["fixture-site"]
    site["review"]["redistribution_policy"] = "links_only"
    site["path_prefixes"] = [prefix]
    result = await worker.fetch_extract({"url": str(server.make_url("/redirect"))})
    assert result["status"] == "ok" and result["text"]
    assert result["publication"]["snippet_text"] == result["publication"]["snippet_title"] == ""
    assert [item["path"] for item in calls] == ["/redirect", "/page/a"]


async def test_explicit_read_outside_known_site_scope_is_not_mislabeled_links_only(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    site = worker.sites.sites["fixture-site"]
    site["review"]["redistribution_policy"] = "links_only"
    site["path_prefixes"] = ["/separate"]
    result = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert result["status"] == "ok" and result["publication"]["snippet_text"]
    assert result["publication"]["snippet_title"] == "Public crawl title" and len(calls) == 1


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("start", ["/redirect-restricted", "/redirect-first"])
async def test_explicit_redirect_restrictions_are_bound_to_the_hop_without_restricting_target(crawl_worker, bounded, start):
    worker, server, calls, *_ = crawl_worker
    params = {"url": str(server.make_url(start))}
    if bounded:
        params.update(attempt_id="a" * 48, max_requests=4, max_content_bytes=100000, timeout_ms=5000)
    result = await worker.fetch_extract(params)
    assert result["status"] == "ok" and result["final_url"] == str(server.make_url("/page/a"))
    assert result["publication"]["index_allowed"] is True
    assert "noindex" not in result["publication"]["directives"]
    assert "publication_observation" not in result
    observations = result["redirect_publication_observations"]
    assert len(observations) == 1
    observed = observations[0]
    assert observed["url"] == str(server.make_url("/redirect-restricted")) and observed["http_status"] == 302
    assert observed["publication"]["index_allowed"] is False
    assert observed["publication"]["snippet_text"] == observed["publication"]["snippet_title"] == ""
    assert [item["path"] for item in calls] == (["/redirect-first"] if start.endswith("first") else []) + ["/redirect-restricted", "/page/a"]
    assert result["usage"]["external_requests"] == len(calls)
    if bounded:
        assert result["usage"]["content_bytes"] == len(PAGE) + 4 + (5 if start.endswith("first") else 0)
    else:
        assert result["usage"]["content_bytes"] == len(PAGE)
    independent = await worker.fetch_extract({"url": str(server.make_url("/page/a"))})
    assert independent["publication"]["index_allowed"] is True and "redirect_publication_observations" not in independent


@pytest.mark.parametrize("header", ["all,index,follow", "googlebot: noindex,nosnippet"])
async def test_allowing_and_other_agent_redirect_headers_do_not_create_observations(crawl_worker, header):
    worker, server, _, state, *_ = crawl_worker
    state["redirect_headers"] = [("X-Robots-Tag", header)]
    result = await worker.fetch_extract({"url": str(server.make_url("/redirect-restricted"))})
    assert result["publication"]["index_allowed"] is True and "redirect_publication_observations" not in result


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("markup,index,snippet", [
    ('<meta name="BzsBot" content="noindex">', False, False),
    ('<meta name="robots" content="nosnippet">', True, False),
    ('<title data-nosnippet>PROTECTED REDIRECT TITLE</title>', True, False),
])
async def test_soft_redirects_preserve_complete_html_restrictions_before_following(crawl_worker, bounded, markup, index, snippet):
    worker, server, calls, state, *_ = crawl_worker
    state["soft_policy_html"] = markup
    params = {"url": str(server.make_url("/soft-restricted"))}
    if bounded:
        params.update(attempt_id="b" * 48, max_requests=2, max_content_bytes=100000, timeout_ms=5000)
    result = await worker.fetch_extract(params)
    assert result["status"] == "ok" and result["publication"]["index_allowed"] is True
    assert result["publication"]["snippet_allowed"] is True
    assert "publication_observation" not in result
    observed = result["redirect_publication_observations"][0]
    assert observed["url"] == str(server.make_url("/soft-restricted")) and observed["http_status"] == 200
    assert observed["publication"]["index_allowed"] is index and observed["publication"]["snippet_allowed"] is snippet
    assert observed["publication"]["snippet_text"] == observed["publication"]["snippet_title"] == ""
    assert "soft_redirect_restriction" in observed["publication"]["diagnostics"]
    assert [item["path"] for item in calls] == ["/soft-restricted", "/page/a"]
    assert result["usage"]["external_requests"] == 2 and result["usage"]["content_bytes"] > len(PAGE)


async def test_http_and_soft_redirect_records_keep_their_individual_urls(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["redirect_target"] = "/soft-restricted"
    state["redirect_headers"] = [("X-Robots-Tag", "BzsBot: nosnippet"), ("X-Robots-Tag", "googlebot: noindex")]
    result = await worker.fetch_extract({"url": str(server.make_url("/redirect-restricted"))})
    observations = result["redirect_publication_observations"]
    assert [(item["url"], item["http_status"]) for item in observations] == [
        (str(server.make_url("/redirect-restricted")), 302), (str(server.make_url("/soft-restricted")), 200)]
    assert observations[0]["publication"]["index_allowed"] is True
    assert observations[0]["publication"]["snippet_allowed"] is False
    assert observations[1]["publication"]["index_allowed"] is False
    assert result["publication"]["index_allowed"] is True
    assert len(calls) == 3
    assert result["usage"]["external_requests"] == 3


@pytest.mark.parametrize("request_limit,byte_limit,expected_bytes,code", [(1, 100000, 4, "E_REQUEST_BUDGET"), (2, 2, 2, "E_BYTE_BUDGET")])
async def test_restricted_redirect_observation_survives_an_exhausted_read_grant(crawl_worker, request_limit, byte_limit, expected_bytes, code):
    worker, server, calls, *_ = crawl_worker
    url = str(server.make_url("/redirect-restricted"))
    result = await worker.fetch_extract({"url": url, "attempt_id": "c" * 48,
        "max_requests": request_limit, "max_content_bytes": byte_limit, "timeout_ms": 5000})
    assert result["status"] == "budget_exhausted" and result["error_code"] == code
    assert result["usage"] == {"external_requests": 1, "content_bytes": expected_bytes}
    assert result["final_url"] == url and result["http_status"] == 302
    assert result["redirect_publication_observations"][0]["url"] == url
    assert result["redirect_publication_observations"][0]["publication"]["index_allowed"] is False
    assert [item["path"] for item in calls] == ["/redirect-restricted"]


async def test_redirect_then_timeout_binds_each_policy_to_its_actual_http_response(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["redirect_target"] = "/timeout"
    state["redirect_headers"] = [("X-Robots-Tag", "BzsBot: nosnippet")]
    result = await worker.fetch_extract({"url": str(server.make_url("/redirect-restricted")), "attempt_id": "d" * 48,
        "max_requests": 2, "max_content_bytes": 100000, "timeout_ms": 50})
    assert result["status"] == "timeout" and result["http_status"] == 200
    assert result["final_url"] == str(server.make_url("/timeout"))
    assert result["publication_observation"]["index_allowed"] is False
    assert result["redirect_publication_observations"][0]["url"] == str(server.make_url("/redirect-restricted"))
    assert result["redirect_publication_observations"][0]["publication"]["index_allowed"] is True
    assert result["usage"] == {"external_requests": 2, "content_bytes": 4 + len(b"first bytes")}
    assert [item["path"] for item in calls] == ["/redirect-restricted", "/timeout"]


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("soft", [False, True])
async def test_denied_private_redirect_keeps_only_the_actual_response_observation(crawl_worker, bounded, soft):
    worker, server, calls, state, *_ = crawl_worker
    state["soft_target" if soft else "redirect_target"] = "http://10.0.0.1/private"
    path = "/soft-restricted" if soft else "/redirect-restricted"
    url = str(server.make_url(path))
    params = {"url": url}
    if bounded:
        params.update(attempt_id="e" * 48, max_requests=3, max_content_bytes=100000, timeout_ms=5000)
    result = await worker.fetch_extract(params)
    assert result["status"] == "blocked_by_policy"
    observed = result["redirect_publication_observations"]
    assert len(observed) == 1 and observed[0]["url"] == url
    assert observed[0]["http_status"] == (200 if soft else 302)
    assert observed[0]["publication"]["index_allowed"] is False
    assert result["final_url"] == url
    # The HTTP redirect is refused while preparing Location. A soft redirect
    # records its candidate, but it never becomes an observed HTTP response.
    assert result["redirects"] == (["http://10.0.0.1/private"] if soft else [])
    assert result["http_status"] == (None if soft else 302)
    assert [item["path"] for item in calls] == [path]
    assert result["usage"]["external_requests"] == 1


async def test_redirect_observation_uses_actual_first_http_url_after_ordinary_uri_normalization(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    url = str(server.make_url("/")) + "redirect-%72estricted"
    result = await worker.fetch_extract({"url": url})
    assert result["status"] == "ok"
    observed = result["redirect_publication_observations"][0]
    assert observed["url"] == str(server.make_url("/redirect-restricted"))
    assert observed["url"] != url and calls[0]["raw_path"] == "/redirect-restricted"
    assert observed["publication"]["index_allowed"] is False


async def test_private_soft_redirect_tail_does_not_erase_an_intermediate_actual_response_url(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["redirect_target"] = "/soft-restricted"
    state["redirect_headers"] = [("X-Robots-Tag", "BzsBot: all")]
    state["soft_target"] = "http://10.0.0.1/private"
    result = await worker.fetch_extract({"url": str(server.make_url("/redirect-restricted")), "attempt_id": "f" * 48,
        "max_requests": 3, "max_content_bytes": 100000, "timeout_ms": 5000})
    assert result["status"] == "blocked_by_policy" and result["http_status"] is None
    actual = str(server.make_url("/soft-restricted"))
    assert result["final_url"] == actual
    assert result["redirect_publication_observations"][0]["url"] == actual
    assert result["redirect_publication_observations"][0]["http_status"] == 200
    assert result["redirects"] == [actual, "http://10.0.0.1/private"]
    assert result["usage"]["external_requests"] == 2
    assert [item["path"] for item in calls] == ["/redirect-restricted", "/soft-restricted"]


@pytest.mark.parametrize("content_type", ["text/html", "text/plain"])
async def test_ordinary_truncated_preview_cannot_grant_publication_and_complete_refresh_can(crawl_worker, content_type):
    worker, server, calls, state, *_ = crawl_worker
    prefix = ("Readable preview marker and ordinary page material. " * 30).encode()
    state["body"] = ((b"<html><body><main><p>" + prefix + b"</p></main>" +
                      b'<meta name="BzsBot" content="noindex"></body></html>')
                     if content_type == "text/html" else prefix)
    state["headers"] = [("Content-Type", content_type)]
    worker.fetcher.max_bytes = 400
    first = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert first["status"] == "ok" and "Readable preview" in first["text"]
    assert "download_truncated" in first["warnings"] and first["index_allowed"] is False
    assert all(first["publication"][flag] is False for flag in ("index_allowed", "snippet_allowed", "follow_allowed"))
    assert first["publication"]["snippet_text"] == first["publication"]["snippet_title"] == ""
    assert "incomplete_http_body" in first["publication"]["diagnostics"]
    assert first["publication_observation"] == first["publication"]
    state["body"] = b"Complete readable response permits indexing and snippets again."
    state["headers"] = [("Content-Type", "text/plain")]
    complete = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert complete["status"] == "ok" and complete["index_allowed"] is True
    assert complete["publication"]["index_allowed"] is True and complete["publication"]["snippet_allowed"] is True
    assert "download_truncated" not in complete["warnings"] and "publication_observation" not in complete
    assert len(calls) == 2


async def test_truncated_html_cannot_follow_a_soft_redirect_before_policy_is_complete(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["body"] = b'<html><head><meta http-equiv="refresh" content="0; url=/private/target"></head><body>' + b"Visible body. " * 100
    state["headers"] = [("Content-Type", "text/html")]
    worker.fetcher.max_bytes = 300
    result = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert result["publication"]["index_allowed"] is False
    assert "incomplete_http_body" in result["publication"]["diagnostics"]
    assert [item["path"] for item in calls] == ["/page"]


@pytest.mark.parametrize("bounded", [False, True])
async def test_partial_http_response_returns_a_normal_fetch_failure_with_restrictive_observation(crawl_worker, bounded):
    worker, server, calls, state, *_ = crawl_worker
    state["status"] = 206
    params = {"url": str(server.make_url("/page"))}
    if bounded:
        params.update(attempt_id="7" * 48, max_requests=1, max_content_bytes=100000, timeout_ms=5000)
    result = await worker.fetch_extract(params)
    assert result["status"] == "http_error" and result["http_status"] == 206
    assert result["error_code"] == "E_CONTENT_LIMIT" and "publication" not in result
    observed = result["publication_observation"]
    assert all(observed[flag] is False for flag in ("index_allowed", "snippet_allowed", "follow_allowed"))
    assert observed["snippet_text"] == observed["snippet_title"] == ""
    assert "incomplete_http_body" in observed["diagnostics"]
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(PAGE)}
    assert [item["path"] for item in calls] == ["/page"]


@pytest.mark.parametrize("ctype,body,header,allowed", [
    ("text/html", b'<html><head><meta name="BzsBot" content="noindex"></head><body></body></html>', "", False),
    ("text/html", b"", "BzsBot: noindex", False),
    ("text/plain", b"", "noindex", False),
    ("text/plain", b" \n", "nosnippet", True),
])
async def test_empty_body_still_returns_the_policy_for_immediate_restrictive_refresh(crawl_worker, ctype, body, header, allowed):
    worker, server, calls, state, *_ = crawl_worker
    state.update(body=body, headers=[("Content-Type", ctype), ("X-Robots-Tag", header)])
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["status"] == "ok" and result["page"]["status"] == "empty_content"
    assert result["page"]["text"] == "" and result["publication"]["index_allowed"] is allowed
    assert result["publication"]["snippet_allowed"] is False
    assert result["publication_observation"]["snippet_text"] == result["publication_observation"]["snippet_title"] == ""
    assert result["publication_observation"]["index_allowed"] is allowed
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(body)}
    # Interactive refresh reports the same policy; it cannot drop noindex merely
    # because no readable body survives extraction.
    direct = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert direct["status"] == "empty_content" and direct["publication"] == result["publication"]
    assert direct["publication_observation"] == result["publication_observation"]
    assert len(calls) == 2


@pytest.mark.parametrize("kind,markup,index,snippet", [
    ("noindex", '<meta name="BzsBot" content="noindex">', False, False),
    ("nosnippet", '<meta name="robots" content="nosnippet">', True, False),
    ("regions", "", True, False),
])
async def test_meta_only_empty_direct_and_bounded_reads_emit_restriction_without_a_snapshot(crawl_worker, kind, markup, index, snippet):
    worker, server, calls, state, *_ = crawl_worker
    body = '<body data-nosnippet></body>' if kind == "regions" else "<body></body>"
    state["body"] = f"<html><head>{markup}</head>{body}</html>".encode()
    for bounded in (False, True):
        params = {"url": str(server.make_url("/page"))}
        if bounded:
            crawl = page_grant(worker, server)
            params.update({key: crawl[key] for key in ("attempt_id", "max_requests", "max_content_bytes", "timeout_ms")})
        result = await worker.fetch_extract(params)
        assert result["status"] == "empty_content" and result["text"] == ""
        observation = result["publication_observation"]
        assert observation["index_allowed"] is index and observation["snippet_allowed"] is snippet
        assert observation["snippet_text"] == observation["snippet_title"] == ""
        assert observation["diagnostics"] == ["empty_extraction_restriction"]
    assert len(calls) == 2


async def test_permissive_empty_read_never_manufactures_an_allowing_observation(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["body"] = b'<html><head><meta name="googlebot" content="noindex,nosnippet"></head><body></body></html>'
    result = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert result["status"] == "empty_content" and "publication_observation" not in result
    assert result["publication"]["index_allowed"] and result["publication"]["snippet_allowed"] and len(calls) == 1


async def test_page_http_redirect_has_no_implicit_follow_and_preserves_target_bytes(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    first = await worker.map_fetch(page_grant(worker, server, "/redirect"))
    assert first["status"] == "redirect" and first["usage"] == {"external_requests": 1, "content_bytes": 4}
    assert "page" not in first and [item["path"] for item in calls] == ["/redirect"]
    assert first["redirect_url"].endswith("/page/%61?x=%2f&x=%2F")
    second = await worker.map_fetch(page_grant(worker, server, "/page/%61?x=%2f&x=%2F", redirect_hop=1))
    assert second["status"] == "ok" and calls[-1]["raw_path"] == "/page/%61?x=%2f&x=%2F"
    assert second["robots_decision"]["target_url"] == first["redirect_url"]


async def test_redirected_page_requires_robots_for_its_actual_target(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    first = await worker.map_fetch(page_grant(worker, server, "/redirect-private"))
    assert first["status"] == "redirect"
    stopped = await worker.map_fetch(page_grant(worker, server, "/private/target", redirect_hop=1))
    assert stopped["error_code"] == "E_ROBOTS_DISALLOWED" and stopped["http_status"] is None
    assert stopped["usage"] == {"external_requests": 0, "content_bytes": 0}
    assert [item["path"] for item in calls] == ["/redirect-private"]


async def test_page_scope_checks_reject_cross_origin_redirect_before_next_request(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    result = await worker.map_fetch(page_grant(worker, server, "/redirect-external"))
    assert result["error_code"] == "E_SITE_SCOPE" and "page" not in result
    assert result["usage"]["external_requests"] == len(calls) == 1


async def test_page_soft_redirect_is_never_used_as_an_outbound_shortcut(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    result = await worker.map_fetch(page_grant(worker, server, "/soft"))
    assert result["status"] == "ok" and result["page"]["status"] == "empty_content"
    assert result["usage"]["external_requests"] == 1
    assert [item["path"] for item in calls] == ["/soft"]


@pytest.mark.parametrize("path", ["/page", "/gzip", "/exact"])
async def test_page_byte_grant_counts_exact_decompressed_bytes_even_when_eof_is_delayed(crawl_worker, path):
    worker, server, calls, *_ = crawl_worker
    result = await worker.map_fetch(page_grant(worker, server, path, max_content_bytes=len(PAGE)))
    assert result["status"] == "ok", result
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(PAGE)}
    assert len(calls) == 1


@pytest.mark.parametrize("path", ["/page", "/gzip", "/extra"])
async def test_page_budget_failure_cannot_publish_a_partial_read_artifact(crawl_worker, path):
    worker, server, calls, *_ = crawl_worker
    limit = len(PAGE) if path == "/extra" else len(PAGE) - 1
    result = await worker.map_fetch(page_grant(worker, server, path, max_content_bytes=limit))
    assert result["error_code"] == "E_BYTE_BUDGET" and "page" not in result and "publication" not in result
    assert result["usage"] == {"external_requests": 1, "content_bytes": limit} and len(calls) == 1


async def test_page_cancel_then_replay_preserves_spent_usage_and_emits_no_artifact(crawl_worker):
    worker, server, calls, _, started, release = crawl_worker
    params = page_grant(worker, server, "/hold")
    task = asyncio.create_task(worker.map_fetch(params))
    await asyncio.wait_for(started.wait(), 2)
    await worker.fetch_cancel({"attempt_id": params["attempt_id"]})
    release.set()
    result = await task
    replay = await worker.map_fetch(params)
    assert result["error_code"] == replay["error_code"] == "E_CANCELLED"
    assert "page" not in result and "page" not in replay
    assert replay["usage"] == result["usage"] and result["usage"]["external_requests"] == len(calls) == 1


async def test_queued_page_rechecks_robots_expiration_at_actual_dispatch(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    sem = worker.fetcher._sem("127.0.0.1")
    for _ in range(worker.fetcher.per_host):
        await sem.acquire()
    record = cached_rules(worker)
    record["expires_at_ms"] = int(time.time() * 1000) + 100
    task = asyncio.create_task(worker.map_fetch(page_grant(worker, server, record=record)))
    try:
        await asyncio.sleep(0.15)
    finally:
        for _ in range(worker.fetcher.per_host):
            sem.release()
    result = await task
    assert result["error_code"] == "E_ROBOTS_RECORD" and "page" not in result
    assert result["usage"] == {"external_requests": 0, "content_bytes": 0} and not calls


async def test_page_grant_cannot_override_publication_or_shared_manifest(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    result = await worker.map_fetch(page_grant(worker, server, sites_manifest_sha256="0" * 64))
    assert result["error_code"] == "E_SITE_MANIFEST_MISMATCH" and result["usage"]["external_requests"] == 0
    with pytest.raises(ValueError, match="parameters"):
        await worker.map_fetch(page_grant(worker, server, publication={"index_allowed": True}))
    assert not calls


async def test_page_kind_accepts_safe_xml_but_entities_can_never_dispatch(crawl_worker):
    worker, server, calls, *_ = crawl_worker
    valid = await worker.map_fetch(page_grant(worker, server, "/xml-safe"))
    assert valid["status"] == "ok" and valid["page"]["content_type"] == "text/plain"
    assert "<urlset" in valid["page"]["text"] and valid["discovery"]["document_type"] == "sitemap_urlset"
    assert len(valid["discovery"]["candidates"]) == 1
    bad = await worker.map_fetch(page_grant(worker, server, "/xml-dtd"))
    assert bad["error_code"] == "E_DISCOVERY_PARSE" and "page" not in bad
    assert [item["path"] for item in calls] == ["/xml-safe", "/xml-dtd"]
    assert bad["usage"]["external_requests"] == 1


@pytest.mark.parametrize("ctype,text", [("text/plain", "Direct readable material."),
                                       ("text/markdown", "# A markdown heading\nBody."),
                                       ("application/json", '{"meaning":"Readable JSON"}')])
async def test_page_plain_types_have_no_implicit_html_frontier(crawl_worker, ctype, text):
    worker, server, calls, state, *_ = crawl_worker
    state.update(body=text.encode(), headers=[("Content-Type", ctype), ("X-Robots-Tag", "googlebot: noindex")])
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["status"] == "ok" and result["page"]["text"]
    assert result["discovery"]["document_type"] == "text" and result["discovery"]["candidates"] == []
    assert result["publication"]["index_allowed"] and result["publication"]["snippet_text"]
    assert len(calls) == 1


async def test_page_full_header_charset_is_applied_before_extraction_and_link_resolution(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    body = '<html><head><title>Пример</title></head><body><p>Русский текст об обработке документов.</p><a href="/привет">Ссылка</a></body></html>'
    state.update(body=body.encode("cp1251"), headers=[("Content-Type", 'text/html; charset="windows-1251"')])
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["page"]["title"] == "Пример" and "Русский" in result["page"]["text"]
    assert result["discovery"]["candidates"][0]["url"].endswith("/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82")
    assert result["usage"]["content_bytes"] == len(body.encode("cp1251")) and len(calls) == 1
    direct = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert direct["title"] == result["page"]["title"] and direct["text"] == result["page"]["text"]
    assert direct["publication"] == result["publication"] and len(calls) == 2


async def test_declared_utf8_damage_preserves_page_text_links_and_physical_byte_usage(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    body = ('<html><head><title>UTF-8 中文页面</title></head><body><main><p>'.encode() +
            b'It\x92s still readable material about bounded requests and safe extraction. ' +
            '保留正确的中文正文。</p><a href="/中文">Link</a></main></body></html>'.encode())
    state.update(body=body, headers=[("Content-Type", "text/html; charset=UTF-8")])
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["status"] == "ok" and result["page"]["charset"] == "utf-8"
    assert result["page"]["title"] == "UTF-8 中文页面" and "It\ufffds still readable" in result["page"]["text"]
    assert "保留正确的中文正文" in result["page"]["text"]
    assert result["discovery"]["candidates"][0]["url"].endswith("/%E4%B8%AD%E6%96%87")
    assert result["usage"] == {"external_requests": 1, "content_bytes": len(body)} and len(calls) == 1
    direct = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert direct["status"] == "ok" and direct["charset"] == "utf-8"
    assert direct["title"] == result["page"]["title"] and direct["text"] == result["page"]["text"]
    assert direct["usage"] == {"external_requests": 1, "content_bytes": len(body)} and len(calls) == 2


async def test_page_partial_http_and_oversize_content_type_keep_usage_without_artifacts(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["status"] = 206
    partial = await worker.map_fetch(page_grant(worker, server))
    assert partial["error_code"] == "E_CONTENT_LIMIT" and "page" not in partial
    headers = await worker.map_fetch(page_grant(worker, server, "/long-content-type"))
    assert headers["error_code"] == "E_UNSUPPORTED_CONTENT" and "page" not in headers
    assert partial["usage"]["external_requests"] == headers["usage"]["external_requests"] == 1 and len(calls) == 2


@pytest.mark.parametrize("ctype,body,status,limit,error", [
    ("text/html", ("<html><body>" + "<div>" * 140 + BODY + "</div>" * 140 + "</body></html>").encode(),
     200, 2 * 1024 * 1024, "E_DISCOVERY_PARSE"),
    ("application/xml", b"<urlset><url>", 200, 2 * 1024 * 1024, "E_DISCOVERY_PARSE"),
    ("application/pdf", b"%PDF-1.4 no page reader", 200, 2 * 1024 * 1024, "E_UNSUPPORTED_CONTENT"),
    ("text/plain", b"denied", 403, 2 * 1024 * 1024, "E_HTTP"),
    ("text/plain", b"unavailable", 500, 2 * 1024 * 1024, "E_HTTP"),
    ("text/html", PAGE, 206, 2 * 1024 * 1024, "E_CONTENT_LIMIT"),
    ("text/html", PAGE, 200, 1, "E_BYTE_BUDGET"),
])
async def test_known_http_body_failures_retain_restrictive_header_observations(crawl_worker, ctype, body, status, limit, error):
    worker, server, calls, state, *_ = crawl_worker
    state.update(body=body, status=status, headers=[("Content-Type", ctype), ("X-Robots-Tag", "BzsBot: noindex,nosnippet"),
                                                  ("X-Robots-Tag", "googlebot: all")])
    result = await worker.map_fetch(page_grant(worker, server, max_content_bytes=limit))
    assert result["status"] == "error" and result["error_code"] == error and "page" not in result
    observation = result["publication_observation"]
    assert observation["version"] == "bzs-publication-1" and observation["index_allowed"] is False
    assert observation["snippet_allowed"] is False
    assert observation["snippet_text"] == observation["snippet_title"] == ""
    assert observation["diagnostics"] == ["http_header_restriction"]
    assert result["robots_decision"]["allowed"] is True
    assert result["robots_decision"]["target_url"] == result["final_url"] == result["url"]
    assert result["usage"] == {"external_requests": 1, "content_bytes": min(limit, len(body))} and len(calls) == 1


@pytest.mark.parametrize("header", ["index,follow,all", "googlebot: noindex,nosnippet", ""])
async def test_failed_page_with_allow_or_other_agent_headers_cannot_lift_old_policy(crawl_worker, header):
    worker, server, calls, state, *_ = crawl_worker
    state.update(body=b"<urlset><url>", headers=[("Content-Type", "application/xml"), ("X-Robots-Tag", header)])
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["error_code"] == "E_DISCOVERY_PARSE" and "publication_observation" not in result
    assert "publication" not in result and "page" not in result and len(calls) == 1


async def test_timeout_after_headers_has_an_observation_but_unknown_connection_failure_does_not(crawl_worker):
    worker, server, calls, _, started, release = crawl_worker
    result = await worker.map_fetch(page_grant(worker, server, "/timeout", timeout=100))
    release.set()
    assert started.is_set() and result["error_code"] == "E_TIMEOUT"
    assert result["http_status"] == 200 and result["publication_observation"]["index_allowed"] is False
    assert result["usage"]["external_requests"] == 1
    unknown = await worker.map_fetch(page_grant(worker, server, "/disconnect"))
    assert unknown["error_code"] == "E_NETWORK" and unknown["http_status"] is None
    assert "publication_observation" not in unknown and "page" not in unknown
    assert len(calls) == 2


async def test_failed_ordinary_read_and_bounded_read_keep_header_restrictions(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state.update(body=b"%PDF-1.4 no reader", headers=[("Content-Type", "application/pdf"), ("X-Robots-Tag", "noindex")])
    direct = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert direct["status"] == "unsupported_content" and direct["publication_observation"]["index_allowed"] is False
    params = page_grant(worker, server, max_content_bytes=1)
    bounded = await worker.fetch_extract({key: params[key] for key in ("url", "attempt_id", "max_requests", "max_content_bytes", "timeout_ms")})
    assert bounded["status"] == "budget_exhausted" and bounded["publication_observation"]["index_allowed"] is False
    assert bounded["usage"] == {"external_requests": 1, "content_bytes": 1} and len(calls) == 2


async def test_nul_html_is_explicitly_normalized_before_the_valid_utf8_artifact_boundary(crawl_worker):
    worker, server, calls, state, *_ = crawl_worker
    state["body"] = PAGE.replace(b"Public tail", b"Public\x00tail")
    result = await worker.map_fetch(page_grant(worker, server))
    assert result["status"] == "ok" and "nul_replaced" in result["page"]["warnings"]
    for key in ("html", "text", "title"):
        assert "\x00" not in result["page"][key]
    assert result["usage"]["content_bytes"] == len(state["body"])
    direct = await worker.fetch_extract({"url": str(server.make_url("/page"))})
    assert direct["html"] == result["page"]["html"] and "nul_replaced" in direct["warnings"] and len(calls) == 2
