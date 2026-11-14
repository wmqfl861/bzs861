"""CL-06 offline tests: mwmbl_api adapter, manifest sync and source review records.

Everything here runs without network access. The JSON fixtures under
tests/fixtures/sources/mwmbl/ were hand-recorded from the documented v2
response shape (developer.mwmbl.org: a JSON array of objects with title, url
and extract members). No live capture was made in this batch, so the recorded
shape must be confirmed by the user's S0 probe before the source is enabled.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bzs.worker.sources import ADAPTERS, SourceSet, load_manifest
from bzs.worker.sources.apis import Mwmbl
from bzs.worker.sources.base import API_USER_AGENT, SourceError
from bzs.worker.sources.review import validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "sources.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sources" / "mwmbl"
MWMBL_V2_ENDPOINT = "https://api.mwmbl.org/api/v2/search/"


def res(body, status=200, ctype="application/json", url=MWMBL_V2_ENDPOINT, headers=None):
    from bzs.worker.fetch import FetchResult
    h = {"content-type": ctype, **(headers or {})}
    data = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(status="ok" if status < 400 else "http_error", url=url, final_url=url,
                       http_status=status, headers=h, body=data)


def run(adapter_cls, body, **kw):
    a = adapter_cls({"id": "t", "params": kw.pop("params", {})}, None)
    r = res(body, **kw)
    a.check(r)
    return a.parse(r, "q", 10)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def mwmbl_spec() -> dict:
    specs = load_manifest(MANIFEST)
    return next(s for s in specs if s["id"] == "mwmbl")


def test_mwmbl_manifest_uses_the_registered_v2_adapter():
    spec = mwmbl_spec()
    assert spec["params"]["adapter"] == "mwmbl_api"
    assert spec["params"]["base_url"] == MWMBL_V2_ENDPOINT
    assert spec["contract"]["adapter_id"] == "mwmbl_api"
    assert spec["contract"]["endpoint"] == MWMBL_V2_ENDPOINT
    # The registry rejects mismatched adapter ids even while disabled
    # (review.py identity check); loading the spec again proves consistency.
    validate_spec(spec)
    from bzs.worker.sources.apis import Mwmbl
    assert ADAPTERS.get("mwmbl_api") is Mwmbl


def test_manifest_stays_disabled_with_consistent_contracts():
    specs = load_manifest(MANIFEST)
    assert specs, "the shipped manifest must not be empty"
    for spec in specs:
        # CL-06 is offline preparation only: nothing may become executable.
        assert spec["enabled"] is False, spec["id"]
        assert spec["contract"]["enabled"] is False, spec["id"]
        validate_spec(spec)
        adapter = spec["params"]["adapter"]
        if spec["enabled"] or adapter in ADAPTERS:
            assert adapter in ADAPTERS, f"adapter not implemented: {adapter}"
        else:
            # Unimplemented entries are only tolerated as explicit placeholders.
            assert adapter.endswith("_planned"), adapter
    assert SourceSet(specs, None).adapters == {}


def test_source_review_records_exist_and_are_bounded():
    records = REPO_ROOT / "docs" / "sources"
    for name in ("marginalia.md", "mwmbl.md", "wikipedia.md"):
        path = records / name
        assert path.is_file(), f"missing source review record: {path}"
        size = path.stat().st_size
        assert size <= 20 * 1024, f"{name} is {size} bytes (limit 20480)"
        text = path.read_text(encoding="utf-8")
        # Review date, an expiry date, and the not-enabled approver placeholder
        # are mandatory in every record; approval stays with the user (S0).
        assert "2026-11-13" in text, name
        assert "2026-12-13" in text, name
        assert "NOT-ENABLED" in text, name


def test_mwmbl_parses_the_searxng_shaped_v2_response():
    hits = run(Mwmbl, fixture("search_ok.json"))
    assert [(h.url, h.title, h.rank) for h in hits] == [
        ("https://mwmbl.org/", "Mwmbl - the free, open, non-profit search engine", 1),
        ("https://developer.mwmbl.org/", "Mwmbl API documentation", 2),
        ("http://mwmbl.org/contribute", "Crawl and contribute", 3),
    ]
    # Markup and whitespace collapse in content; non-web schemes drop out.
    assert hits[1].snippet == "Search endpoint: GET /api/v2/search/?q= with extra spacing."


def test_mwmbl_empty_results_is_an_empty_search():
    assert run(Mwmbl, fixture("search_empty.json")) == []


@pytest.mark.parametrize("body", [
    "{}",                                     # v2 answers with an array; an object is a shape change
    "mwmbl",                                  # not JSON at all
    '[{"url": "https://mwmbl.org/"}]',        # missing required title
    '[{"title": "T", "url": 12}]',            # non-string url
    '[{"title": "", "url": "https://mwmbl.org/"}]',  # blank title fails required
    '["str"]',                                # array members must be objects
    '[{"title": "T", "title": "U", "url": "https://mwmbl.org/"}]',  # duplicate key
    '[{"title": "T", "url": "https://mwmbl.org/", "extract": NaN}]',  # non-finite value
])
def test_mwmbl_shape_failures_are_parse_errors(body):
    with pytest.raises(SourceError) as exc:
        run(Mwmbl, body)
    assert exc.value.status == "parse_error"


def test_mwmbl_array_longer_than_1000_members_is_rejected():
    # Same guard family as JsonApi.json: the top-level array is capped at 1000
    # members; the fixture packs 1001 well-formed rows and must fail fast as
    # parse_error instead of being indexed.
    with pytest.raises(SourceError) as exc:
        run(Mwmbl, fixture("search_too_long.json"))
    assert exc.value.status == "parse_error"


def test_mwmbl_http_429_is_rate_limited_with_retry_after():
    # 429 is the documented quota-exceeded signal (developer.mwmbl.org).
    with pytest.raises(SourceError) as exc:
        run(Mwmbl, fixture("rate_limited.txt"), status=429, ctype="text/plain",
            headers={"retry-after": "3600"})
    assert exc.value.status == "rate_limited"
    assert exc.value.http_status == 429 and exc.value.retry_after_ms == 3_600_000


class RecordingFetcher:
    def __init__(self, body):
        self.body = body
        self.calls = []

    async def fetch(self, url, params=None, timeout_s=None, accept=None, headers=None, **kw):
        self.calls.append({"url": url, "params": params, "accept": accept, "headers": headers})
        return res(self.body)


async def test_mwmbl_request_shape_anonymous_then_keyed(monkeypatch):
    monkeypatch.delenv("BZS_MWMBL_KEY", raising=False)
    fetcher = RecordingFetcher(fixture("search_ok.json"))
    adapter = Mwmbl({"id": "mwmbl", "params": {}}, fetcher)
    hits, r = await adapter.search("fixture query", 5, None, 5)
    assert r.http_status == 200 and len(hits) == 3
    call = fetcher.calls[0]
    assert call["url"] == MWMBL_V2_ENDPOINT
    # Only the q parameter is documented for v2; no invented count parameter.
    assert call["params"] == {"q": "fixture query"}
    assert call["accept"] == "application/json"
    assert call["headers"]["User-Agent"] == API_USER_AGENT
    # No env key: the anonymous S0 path (D15) must not carry credentials.
    assert "Authorization" not in call["headers"] and "X-API-Key" not in call["headers"]
    monkeypatch.setenv("BZS_MWMBL_KEY", "fixture-free-key")
    hits, _ = await adapter.search("fixture query", 5, None, 5)
    assert len(hits) == 3 and fetcher.calls[1]["headers"]["X-API-Key"] == "fixture-free-key"


async def test_mwmbl_rate_limit_fences_only_its_own_budget(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("bzs.worker.sources.time", SimpleNamespace(monotonic=lambda: now[0]))
    # A copy of the shipped mwmbl spec with an offline-only approved review and
    # zero interval: the real config stays disabled; this exercises the worker
    # state machine around the mwmbl budget key without any network.
    spec = json.loads(json.dumps(mwmbl_spec()))
    spec["enabled"] = spec["contract"]["enabled"] = True
    spec["min_interval_ms"] = spec["contract"]["limits"]["min_interval_ms"] = 0
    spec["contract"]["review"].update(
        status="approved", payment_scheme="no_payment", automated_access="approved",
        checked_at="1970-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        terms_url="https://developer.mwmbl.org/", cache_policy="allowed_with_limits",
        redistribution_policy="links_only", approved_by="offline-fixture")
    sources = SourceSet([spec], None)
    calls = []

    async def limited(query, limit, language, timeout_s, conditions=None):
        calls.append("limited")
        raise SourceError("rate_limited", "HTTP 429", 429, 3_600_000)

    async def recovered(query, limit, language, timeout_s, conditions=None):
        calls.append("recovered")
        return [], res(fixture("search_empty.json"))

    monkeypatch.setattr(sources.adapters["mwmbl"], "search", limited)
    out = await sources.run_one("mwmbl", "fixture", 5, None, 1)
    assert out["status"] == "rate_limited" and out["retry_after_ms"] == 3_600_000
    # The 429 fences the mwmbl budget key until the exact Retry-After expiry.
    assert sources.cooldown_until["mwmbl"] == 4600.0
    assert (await sources.run_one("mwmbl", "fixture", 5, None, 1))["status"] == "skipped"
    now[0] = 4599.999
    assert (await sources.run_one("mwmbl", "fixture", 5, None, 1))["status"] == "skipped"
    now[0] = 4600.0
    monkeypatch.setattr(sources.adapters["mwmbl"], "search", recovered)
    assert (await sources.run_one("mwmbl", "fixture", 5, None, 1))["status"] == "empty"
    assert calls == ["limited", "recovered"]
