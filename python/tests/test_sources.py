import base64
import asyncio
import datetime as dt
import hashlib
import json
import time
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bzs.worker.fetch import FetchResult
from bzs.worker.sources import SourceSet, load_manifest
from bzs.worker.sources.apis import Arxiv, GitHubRepos, HackerNews, Marginalia, StackExchange, Wikipedia
from bzs.worker.sources.base import SourceError, decode_bing_url, decode_ddg_url, retry_after_ms
from bzs.worker.sources.engines import BaiduHtml, BingHtml, DuckDuckGoHtml, MojeekHtml, So360Html, SogouHtml
from bzs.worker.sources.review import SourceReviewError, validate_spec
from tests import fakeweb


def res(body, status=200, ctype="text/html; charset=utf-8", url="https://engine.example/search", headers=None):
    h = {"content-type": ctype, **(headers or {})}
    data = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(status="ok" if status < 400 else "http_error", url=url, final_url=url, http_status=status,
                       headers=h, body=data)


def run(adapter_cls, body, **kw):
    a = adapter_cls({"id": "t", "params": kw.pop("params", {})}, None)
    r = res(body, **kw)
    a.check(r)
    return a.parse(r, "q", 10)


def test_redirect_decoders():
    target = "https://example.org/b?x=1"
    enc = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert decode_bing_url(f"https://www.bing.com/ck/a?!&&p=1&u=a1{enc}&ntb=1") == target
    assert decode_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"
    assert decode_bing_url("https://direct.example/") == "https://direct.example/"


def test_duckduckgo():
    html = """<div class="result result--ad"><a class="result__a" href="https://ads/">ad</a></div>
    <div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Title A</a></h2>
    <a class="result__snippet">Snippet A</a></div>
    <div class="result"><a class="result__a" href="https://example.com/b">Title B</a></div>"""
    hits = run(DuckDuckGoHtml, html)
    assert [(h.url, h.title, h.rank) for h in hits] == [("https://example.com/a", "Title A", 1), ("https://example.com/b", "Title B", 2)]
    assert hits[0].snippet == "Snippet A"
    with pytest.raises(SourceError) as e:
        run(DuckDuckGoHtml, "<div class='anomaly-modal'>bots use DuckDuckGo too</div>")
    assert e.value.status == "blocked"


def test_bing_and_block_page():
    enc = base64.urlsafe_b64encode(b"https://example.org/x").decode().rstrip("=")
    html = f"""<ol><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&&u=a1{enc}&ntb=1">X page</a></h2>
    <div class="b_caption"><p>About x.</p></div></li></ol>"""
    hits = run(BingHtml, html)
    assert hits[0].url == "https://example.org/x" and hits[0].snippet == "About x."
    with pytest.raises(SourceError) as e:
        run(BingHtml, "<html><body><form action='/turing/captcha/challenge'></form></body></html>")
    assert e.value.status == "blocked"


def test_rate_limit_status():
    with pytest.raises(SourceError) as e:
        run(BingHtml, "slow down", status=429, headers={"retry-after": "120"})
    assert e.value.status == "rate_limited" and e.value.retry_after_ms == 120000


def test_rate_limit_and_block_signals_survive_truncated_response_bodies():
    response = res("partial rate-limit response", status=429, headers={"retry-after": "3600"})
    response.status, response.truncated = "too_large", True
    with pytest.raises(SourceError) as limited:
        Wikipedia({"id": "wiki", "params": {}}, None).check(response)
    assert limited.value.status == "rate_limited" and limited.value.retry_after_ms == 3_600_000
    blocked = res("<div class='anomaly-modal'>bots use DuckDuckGo too</div>")
    blocked.truncated = True
    with pytest.raises(SourceError) as error:
        DuckDuckGoHtml({"id": "ddg", "params": {}}, None).check(blocked)
    assert error.value.status == "blocked"


@pytest.mark.parametrize("body,header,expected", [
    ({"error_id": 502, "error_name": "throttle_violation", "backoff": 3600}, "20", 3_600_000),
    ({"error_id": 502, "error_name": "throttle_violation", "backoff": 20}, "7200", 7_200_000),
    ({"items": [], "backoff": 20}, "7200", 7_200_000),
])
def test_api_application_backoff_never_shortens_upstream_wait(body, header, expected):
    with pytest.raises(SourceError) as error:
        run(StackExchange, json.dumps(body), ctype="application/json", headers={"retry-after": header})
    assert error.value.status == "rate_limited" and error.value.retry_after_ms == expected


def test_http_date_retry_after_is_preserved_for_source_cooldown(monkeypatch):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    retry_at = format_datetime(now + dt.timedelta(seconds=120), usegmt=True)
    monkeypatch.setattr("bzs.worker.sources.base.time.time", lambda: now.timestamp())
    with pytest.raises(SourceError) as error:
        run(BingHtml, "slow down", status=429, headers={"retry-after": retry_at})
    assert error.value.status == "rate_limited" and error.value.retry_after_ms == 120000
    assert retry_after_ms({"retry-after": retry_at}, now=(now + dt.timedelta(minutes=3)).timestamp()) == 0
    assert retry_after_ms({"retry-after": "invalid date"}, now=now.timestamp()) == 0
    assert retry_after_ms({"retry-after": "3600"}, now=now.timestamp()) == 3600000


@pytest.mark.parametrize("header,expected,clamped", [
    ("", 0, False), ("0", 0, False), ("120", 120000, False),
    ("86400", 86400000, False), ("86401", 86400000, True),
    ("999999999", 86400000, True), pytest.param("9" * 20000, 86400000, True, id="huge-decimal"),
    ("-1", 0, False), ("1.5", 0, False), ("１２３", 0, False),
])
def test_search_retry_after_seconds_are_bounded_and_explicit(header, expected, clamped):
    with pytest.raises(SourceError) as caught:
        run(BingHtml, "limited", status=429, headers={"retry-after": header})
    assert caught.value.retry_after_ms == expected
    assert caught.value.retry_after_clamped is clamped
    assert caught.value.status == "rate_limited"


@pytest.mark.parametrize("seconds", [-1, 120, 86400, 86401, 999999999])
def test_search_retry_after_http_dates_use_the_same_bound(monkeypatch, seconds):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("bzs.worker.sources.base.time.time", lambda: now.timestamp())
    header = format_datetime(now + dt.timedelta(seconds=seconds), usegmt=True)
    with pytest.raises(SourceError) as caught:
        run(Wikipedia, "limited", status=429, headers={"retry-after": header})
    assert caught.value.retry_after_ms == min(86400000, max(0, seconds * 1000))
    assert caught.value.retry_after_clamped is (seconds > 86400)


@pytest.mark.parametrize("body,header", [
    ({"items": [], "backoff": 999999999}, "120"),
    ({"items": [], "backoff": 120}, "999999999"),
    ({"error_id": 502, "error_name": "throttle_violation", "backoff": 999999999}, "120"),
])
def test_search_api_backoff_and_headers_cannot_extend_the_source_bound(body, header):
    with pytest.raises(SourceError) as caught:
        run(StackExchange, json.dumps(body), ctype="application/json", headers={"retry-after": header})
    assert caught.value.status == "rate_limited"
    assert caught.value.retry_after_ms == 86400000 and caught.value.retry_after_clamped


def test_search_backoff_cap_does_not_change_robots_retry_after():
    from bzs.worker.robots import response_record

    # The common parser remains faithful to the site policy's longer delay.
    assert retry_after_ms({"retry-after": "172800"}) == 172800000
    record, retry = response_record(origin="https://fixture.example", status="http_error", http_status=429,
                                   headers={"retry-after": "172800"}, body=b"limited", truncated=False,
                                   now_ms=1000000)
    assert retry == 172800000 and record["state"] == "deny_all"
    assert record["expires_at_ms"] == 1000000 + 86400000


@pytest.mark.parametrize("key", ["cooldown_blocked_ms", "cooldown_rate_ms", "cooldown_error_ms"])
@pytest.mark.parametrize("value", [-1, 86400001, True, "1000", 1.5, None])
def test_worker_rejects_invalid_source_cooldown_configuration(key, value):
    spec = fakeweb.manifest(1)["sources"][0]
    spec[key] = value
    with pytest.raises(SourceReviewError, match="invalid source cooldown"):
        SourceSet([spec], None)


def test_default_manifest_has_no_enabled_or_executable_sources():
    specs = load_manifest(Path(__file__).resolve().parents[2] / "config" / "sources.json")
    assert specs and all(spec["enabled"] is False and spec["contract"]["enabled"] is False for spec in specs)
    assert SourceSet(specs, None).adapters == {}


async def test_candidate_probe_selection_does_not_approve_regular_search(monkeypatch):
    spec = fakeweb.manifest(1)["sources"][0]
    spec["enabled"] = spec["contract"]["enabled"] = False
    spec["contract"]["review"].update(status="candidate", payment_scheme="unverified", automated_access="unverified")
    assert SourceSet([spec], None).adapters == {}
    with pytest.raises(ValueError, match="nonempty --only"):
        SourceSet([spec], None, include_candidates=True)
    sources = SourceSet([spec], None, include_candidates=True, candidate_ids=[spec["id"]])
    calls = []

    async def unexpected_search(*args):
        calls.append(args)
        return [], res("")

    monkeypatch.setattr(sources.adapters[spec["id"]], "search", unexpected_search)
    assert (await sources.run_one(spec["id"], "fixture", 5, None, 1))["status"] == "review_required"
    spec["enabled"] = spec["contract"]["enabled"] = True
    assert (await sources.run_one(spec["id"], "fixture", 5, None, 1))["status"] == "review_required"
    assert calls == [] and spec["contract"]["review"]["status"] == "candidate"


async def test_approved_review_expiry_is_rechecked_by_the_same_source_set(monkeypatch):
    spec = fakeweb.manifest(1)["sources"][0]
    spec["contract"]["review"]["expires_at"] = "2026-01-01T00:00:01Z"
    now = [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()]
    monkeypatch.setattr("bzs.worker.sources.review.time.time", lambda: now[0])
    sources = SourceSet([spec], None)
    calls = []

    async def recorded_search(*args):
        calls.append(args)
        return [], res("")

    monkeypatch.setattr(sources.adapters[spec["id"]], "search", recorded_search)
    assert (await sources.run_one(spec["id"], "fixture", 5, None, 1))["status"] == "empty"
    now[0] += 1
    assert (await sources.run_one(spec["id"], "fixture", 5, None, 1))["status"] == "review_required"
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("provider_id", "other"), ("index_family", "other"),
    ("adapter_id", "bing_html"), ("endpoint", "https://unreviewed.example/search"),
    ("capability", "code"), ("enabled", False),
])
def test_source_contract_identity_must_match_runtime_configuration(field, value):
    spec = fakeweb.manifest(1)["sources"][0]
    spec["contract"][field] = value
    with pytest.raises(SourceReviewError, match="identity mismatch"):
        validate_spec(spec)


def test_manifest_digest_binds_exact_file_bytes_and_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "sources.json"
    raw = json.dumps(fakeweb.manifest(1), ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    original = load_manifest(path)
    assert original.digest == hashlib.sha256(raw).hexdigest()
    assert SourceSet(original, None).manifest_digest == original.digest
    path.write_bytes(raw + b"\n")
    changed = load_manifest(path)
    assert changed == original and changed.digest != original.digest
    path.write_bytes(b'{"version":1,"version":1,"sources":[]}')
    with pytest.raises(ValueError, match="duplicate manifest key"):
        load_manifest(path)


def shared_budget_specs(interval_ms=0):
    specs = fakeweb.manifest(1)["sources"][:2]
    for spec in specs:
        spec["min_interval_ms"] = interval_ms
        spec["contract"]["limits"].update(shared_budget_key="fixture-service", min_interval_ms=interval_ms)
        spec["params"]["base_url"] = spec["contract"]["endpoint"] = f"https://{spec['id']}.example/search"
    return specs


async def test_bounded_retry_after_keeps_shared_worker_fence_until_exact_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("bzs.worker.sources.time", SimpleNamespace(monotonic=lambda: now[0]))
    sources = SourceSet(shared_budget_specs(), None)
    calls = []

    async def limited(*args):
        calls.append("ddg")
        raise SourceError("rate_limited", "HTTP 429", 429, 999999999000)

    async def other(*args):
        calls.append("bing")
        return [], res("")

    monkeypatch.setattr(sources.adapters["ddg"], "search", limited)
    monkeypatch.setattr(sources.adapters["bing"], "search", other)
    limited_result = await sources.run_one("ddg", "fixture", 5, None, 1)
    assert limited_result["status"] == "rate_limited"
    assert limited_result["retry_after_ms"] == limited_result["retry_after_limit_ms"] == 86400000
    assert limited_result["retry_after_clamped"] is True
    assert sources.cooldown_until["fixture-service"] == 87400.0
    assert (await sources.run_one("bing", "fixture", 5, None, 1))["status"] == "skipped"
    now[0] = 87399.999
    assert (await sources.run_one("bing", "fixture", 5, None, 1))["status"] == "skipped"
    assert calls == ["ddg"]
    now[0] = 87400.0
    assert (await sources.run_one("bing", "fixture", 5, None, 1))["status"] == "empty"
    assert calls == ["ddg", "bing"]


async def test_shared_budget_spaces_actual_fast_adapter_starts_across_hosts(monkeypatch):
    sources = SourceSet(shared_budget_specs(80), None)
    starts = []

    async def fast_search(*args):
        starts.append(time.monotonic())
        return [], res("")

    for adapter in sources.adapters.values():
        monkeypatch.setattr(adapter, "search", fast_search)
    results = await sources.run_many(["ddg", "bing"], "fixture", 5, None, 1)
    assert all(result["status"] == "empty" for result in results.values())
    assert len(starts) == 2 and starts[1] - starts[0] >= 0.075


async def test_blocked_source_fences_an_already_queued_shared_budget_adapter(monkeypatch):
    sources = SourceSet(shared_budget_specs(), None)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def blocked_search(*args):
        calls.append("ddg")
        entered.set()
        await release.wait()
        raise SourceError("blocked", "fixture CAPTCHA", 403)

    async def unexpected_search(*args):
        calls.append("bing")
        return [], res("")

    monkeypatch.setattr(sources.adapters["ddg"], "search", blocked_search)
    monkeypatch.setattr(sources.adapters["bing"], "search", unexpected_search)
    first = asyncio.create_task(sources.run_one("ddg", "fixture", 5, None, 1))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        second = asyncio.create_task(sources.run_one("bing", "fixture", 5, None, 1))
        await asyncio.sleep(0)
    finally:
        release.set()
        first_result = await first
        second_result = await second if second is not None else None
    assert first_result["status"] == "blocked" and second_result["status"] == "skipped"
    assert calls == ["ddg"]


def test_parse_error_when_markup_unknown():
    with pytest.raises(SourceError) as e:
        run(BaiduHtml, "<html><body><div id='content_left'><p>changed layout</p></div></body></html>")
    assert e.value.status == "parse_error"


def test_baidu_prefers_mu_attribute():
    html = """<div id="content_left"><div class="result c-container" mu="https://real.example/p">
    <h3><a href="http://www.baidu.com/link?url=abc">真实标题</a></h3><div class="c-abstract">摘要内容</div></div>
    <div class="result c-container" data-tuiguang="1"><h3><a href="http://ad/">广告</a></h3></div></div>"""
    hits = run(BaiduHtml, html)
    assert len(hits) == 1 and hits[0].url == "https://real.example/p" and hits[0].snippet == "摘要内容"
    with pytest.raises(SourceError):
        run(BaiduHtml, "<html><title>百度安全验证</title></html>")


def test_sogou_360_mojeek():
    sogou = """<div class="vrwrap"><h3><a href="/link?url=abc">搜狗结果</a></h3><div class="space-txt">说明</div></div>"""
    h = run(SogouHtml, sogou, url="https://www.sogou.com/web?query=q")[0]
    assert h.url == "https://www.sogou.com/link?url=abc" and h.snippet == "说明"
    so = """<ul><li class="res-list"><h3><a href="https://www.so.com/link?m=1" data-mdurl="https://real.example/">360结果</a></h3>
    <p class="res-desc">描述</p></li></ul>"""
    assert run(So360Html, so)[0].url == "https://real.example/"
    mj = """<ul class="results-standard"><li><h2><a class="ob" href="https://m.example/">Mojeek hit</a></h2><p class="s">Text</p></li></ul>"""
    h = run(MojeekHtml, mj)[0]
    assert h.url == "https://m.example/" and h.title == "Mojeek hit" and h.snippet == "Text"


def test_wikipedia():
    body = json.dumps({"query": {"search": [{"title": "搜索 引擎", "snippet": "<span class=\"searchmatch\">搜索</span>引擎"}]}})
    hits = run(Wikipedia, body, ctype="application/json", params={"lang": "zh"})
    assert hits[0].url == "https://zh.wikipedia.org/wiki/%E6%90%9C%E7%B4%A2_%E5%BC%95%E6%93%8E"
    assert hits[0].snippet == "搜索引擎"


def test_arxiv_atom():
    body = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All
     You Need</title><summary>The dominant sequence transduction models...</summary></entry></feed>"""
    h = run(Arxiv, body, ctype="application/atom+xml")[0]
    assert h.url == "https://arxiv.org/abs/1706.03762v7" and h.title == "Attention Is All You Need"


def test_json_apis():
    se = json.dumps({"items": [{"item_type": "question", "question_id": 1, "title": "Q &amp; A", "excerpt": "<b>x</b>"},
                               {"item_type": "answer", "answer_id": 9, "title": "A"}]})
    hits = run(StackExchange, se, ctype="application/json")
    assert [h.url for h in hits] == ["https://stackoverflow.com/questions/1", "https://stackoverflow.com/a/9"]
    assert hits[0].title == "Q & A"
    with pytest.raises(SourceError) as e:
        run(StackExchange, json.dumps({"items": [], "backoff": 10}), ctype="application/json")
    assert e.value.status == "rate_limited"

    hn = json.dumps({"hits": [{"title": "Show HN", "url": None, "objectID": "42", "points": 3, "num_comments": 1}]})
    assert run(HackerNews, hn, ctype="application/json")[0].url == "https://news.ycombinator.com/item?id=42"
    gh = json.dumps({"items": [{"full_name": "a/b", "html_url": "https://github.com/a/b", "description": "d", "language": "C++", "stargazers_count": 5}]})
    assert run(GitHubRepos, gh, ctype="application/json")[0].snippet == "d (C++, ★5)"
    with pytest.raises(SourceError) as e:
        run(GitHubRepos, "{}", status=403, ctype="application/json", headers={"x-ratelimit-remaining": "0"})
    assert e.value.status == "rate_limited"
    mg = json.dumps({"results": [{"url": "https://small.example/", "title": "Small", "description": "web"}]})
    assert run(Marginalia, mg, ctype="application/json")[0].url == "https://small.example/"
    with pytest.raises(SourceError) as e:
        run(Marginalia, "busy", status=503, ctype="text/plain")
    assert e.value.status == "rate_limited"

@pytest.mark.parametrize("adapter,body", [
    (Wikipedia, {}), (Wikipedia, {"query": {}}),
    (Wikipedia, {"query": {"search": {}}}),
    (Wikipedia, {"query": {"search": [{"title": 12}]}}),
    (Marginalia, []), (Marginalia, {}), (Marginalia, {"results": [None]}),
    (Marginalia, {"results": [{"url": True}]}),
    (HackerNews, {"hits": None}), (HackerNews, {"hits": [{"title": []}]}),
    (GitHubRepos, {"items": [{"html_url": "https://example.com/"}]}),
    (StackExchange, {"items": [], "backoff": True}),
    (StackExchange, {"items": [], "backoff": -1}),
    (StackExchange, {"items": [], "backoff": 1.5}),
])
def test_api_response_shape_failures_are_not_empty_results(adapter, body):
    with pytest.raises(SourceError) as exc:
        run(adapter, json.dumps(body), ctype="application/json")
    assert exc.value.status == "parse_error"


def test_native_site_translation_appends_only_missing_terms():
    from bzs.worker.sources.engines import with_native_site_terms
    conditions = [{"kind": "site", "value": "example.com"}]
    assert with_native_site_terms("guide", conditions) == "guide site:example.com"
    # The raw query already carries the directive: no duplication.
    assert with_native_site_terms("guide site:example.com", conditions) == "guide site:example.com"
    # Only site-class conditions translate; anything else is ignored here.
    assert with_native_site_terms("guide", [{"kind": "inbody", "value": "needle"}]) == "guide"
    assert with_native_site_terms("guide", None) == "guide"
    assert with_native_site_terms("guide site:a.example", [{"kind": "site", "value": "b.example"}]) == \
        "guide site:a.example site:b.example"


async def test_run_one_translates_and_reports_native_conditions(monkeypatch):
    spec = fakeweb.manifest(1)["sources"][0]
    sources = SourceSet([spec], None)
    adapter = sources.adapters[spec["id"]]
    seen = []

    async def recording_search(query, limit, language, timeout_s, conditions=None):
        seen.append(conditions)
        return [], res("")

    monkeypatch.setattr(adapter, "search", recording_search)
    conditions = [{"kind": "site", "value": "example.com"}, {"kind": "inbody", "value": "needle"}]
    out = await sources.run_one(spec["id"], "fixture", 5, None, 1, conditions)
    # Only the site condition reaches the engine (DDG-style native kinds); the
    # adapter call itself stays a no-op for conditions it does not translate.
    assert seen == [[{"kind": "site", "value": "example.com"}]]
    assert out["conditions"] == [{"kind": "site", "value": "example.com", "execution": "native"},
                                 {"kind": "inbody", "value": "needle", "execution": "unsupported"}]
    # Without conditions the response carries no report at all: the core reads
    # that absence as "not executed" for every condition.
    plain = await sources.run_one(spec["id"], "fixture", 5, None, 1)
    assert "conditions" not in plain and seen[-1] is None


async def test_run_one_reports_conditions_even_when_the_source_fails(monkeypatch):
    spec = fakeweb.manifest(1)["sources"][0]
    sources = SourceSet([spec], None)
    adapter = sources.adapters[spec["id"]]

    async def failing_search(query, limit, language, timeout_s, conditions=None):
        raise SourceError("blocked", "fixture CAPTCHA", 403)

    monkeypatch.setattr(adapter, "search", failing_search)
    conditions = [{"kind": "site", "value": "example.com"}, {"kind": "inbody", "value": "needle"}]
    out = await sources.run_one(spec["id"], "fixture", 5, None, 1, conditions)
    assert out["status"] == "blocked"
    # The report still says exactly what the request carried; a failed source
    # never masquerades as satisfying anything (the core needs no status here).
    assert out["conditions"] == [{"kind": "site", "value": "example.com", "execution": "native"},
                                 {"kind": "inbody", "value": "needle", "execution": "unsupported"}]


@pytest.mark.parametrize("body", [
    '{"results":[],"results":[]}', '{"results":[],"score":NaN}',
    '{"results":[],"score":Infinity}',
])
def test_api_json_rejects_ambiguous_and_nonstandard_values(body):
    with pytest.raises(SourceError) as exc:
        run(Marginalia, body, ctype="application/json")
    assert exc.value.status == "parse_error"


def test_api_http_200_application_errors_and_backoff_are_failures():
    with pytest.raises(SourceError) as exc:
        run(Wikipedia, json.dumps({"error": {"code": "maxlag"}}), ctype="application/json",
            headers={"retry-after": "90"})
    assert exc.value.status == "rate_limited" and exc.value.retry_after_ms == 90000
    with pytest.raises(SourceError) as exc:
        run(Marginalia, json.dumps({"error": "invalid API key"}), ctype="application/json")
    assert exc.value.status == "upstream_error"
    with pytest.raises(SourceError) as exc:
        run(StackExchange, json.dumps({"error_id": 502, "error_name": "throttle_violation"}),
            ctype="application/json")
    assert exc.value.status == "rate_limited"


@pytest.mark.parametrize("adapter,body", [
    (Wikipedia, {"query": {"search": []}}), (Marginalia, {"results": []}),
    (HackerNews, {"hits": []}), (GitHubRepos, {"items": []}),
    (StackExchange, {"items": [], "backoff": 0}),
])
def test_explicit_empty_api_collections_remain_valid(adapter, body):
    assert run(adapter, json.dumps(body), ctype="application/json") == []


def test_a_truncated_response_is_not_parsed_as_a_complete_search():
    response = res('{"results": []}', ctype="application/json")
    response.truncated = True
    adapter = Marginalia({"id": "fixture", "params": {}}, None)
    with pytest.raises(SourceError) as exc:
        adapter.check(response)
    assert exc.value.status == "too_large"


async def test_run_one_reports_not_executed_when_cooldown_blocks_dispatch(monkeypatch):
    # A planned native condition whose adapter call never started (cooldown,
    # review refusal, pre-dispatch timeout) is reported not_executed: the core
    # must never map it to native execution.
    spec = json.loads(json.dumps(fakeweb.manifest(1)["sources"][0]))
    sources = SourceSet([spec], None)
    adapter = sources.adapters[spec["id"]]
    adapter.native_filter_kinds = ("site",)

    async def never(query, limit, language, timeout_s, conditions=None):
        raise AssertionError("cooldown must prevent dispatch")

    adapter.search = never
    key = spec["contract"]["limits"]["shared_budget_key"]
    sources.cooldown_until[key] = time.monotonic() + 60
    out = await sources.run_one(spec["id"], "fixture", 5, None, 5,
                                [{"kind": "site", "value": "example.com"}])
    assert out["status"] == "skipped"
    assert out["conditions"] == [{"kind": "site", "value": "example.com", "execution": "not_executed"}]
