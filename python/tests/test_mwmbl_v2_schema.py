"""CL-27 regressions: the real mwmbl v2 API answers in SearXNG-shaped
objects (SearchResponse.results[].title/content strings, per upstream
cce49fa), authenticates with an X-API-Key header, and rejects bad keys
with HTTP 401. The pre-CL-27 adapter assumed a top-level array with
`extract` fields and a Bearer header, so every real response parsed as
parse_error and keys were silently ignored. These tests are written
against the upstream schema and must fail before the fix.
"""
import pytest

from bzs.worker.sources.apis import Mwmbl, SourceError
from bzs.worker.sources.base import API_USER_AGENT

from tests.test_sources_mwmbl import MWMBL_V2_ENDPOINT, RecordingFetcher, fixture, res, run

SEARXNG_OK = """{
  "query": "fixture",
  "number_of_results": 3,
  "results": [
    {"url": "https://mwmbl.org/", "title": "Mwmbl - the free, open, non-profit search engine",
     "content": "Mwmbl is a community-driven, non-profit search engine.", "engine": "mwmbl", "score": 1.5},
    {"url": "https://developer.mwmbl.org/", "title": "Mwmbl API   <b>documentation</b>",
     "content": "Search endpoint: GET /api/v2/search/?q=   with   extra   spacing.", "engine": "mwmbl", "score": 1.1},
    {"url": "https://en.wikipedia.org/wiki/Search", "title": "Wikipedia overlap row",
     "content": "mwmbl mixes in wikipedia results; dedup handles the overlap.", "engine": "wikipedia", "score": 0.9},
    {"url": "app://deeplink/x", "title": "App deeplink row",
     "content": "Non-web scheme rows are dropped.", "engine": "mwmbl", "score": 0.8}
  ],
  "images": [],
  "monthly_usage": 3,
  "monthly_limit": 1000
}"""


def test_mwmbl_parses_the_real_searxng_shaped_v2_response():
    hits = run(Mwmbl, SEARXNG_OK)
    assert [(h.url, h.title, h.rank) for h in hits] == [
        ("https://mwmbl.org/", "Mwmbl - the free, open, non-profit search engine", 1),
        ("https://developer.mwmbl.org/", "Mwmbl API documentation", 2),
        ("https://en.wikipedia.org/wiki/Search", "Wikipedia overlap row", 3),
    ]
    # content (not extract) is the snippet; markup and whitespace collapse.
    assert hits[1].snippet == "Search endpoint: GET /api/v2/search/?q= with extra spacing."


def test_mwmbl_content_may_be_omitted_or_null():
    body = ('{"query":"q","results":['
            '{"url":"https://a.example/","title":"A"},'
            '{"url":"https://b.example/","title":"B","content":null}]}')
    hits = run(Mwmbl, body)
    assert [(h.url, h.snippet) for h in hits] == [("https://a.example/", ""), ("https://b.example/", "")]


@pytest.mark.parametrize("body", [
    '[{"title":"T","url":"https://mwmbl.org/"}]',     # old top-level array must now be rejected
    '{"query":"q"}',                                   # missing results array
    '{"results":{}}',                                  # results must be an array
    '{"results":[{"url":"https://mwmbl.org/"}]}',       # missing required title
    '{"results":[{"title":"T","url":12}]}',            # non-string url
    '{"results":[{"title":"","url":"https://mwmbl.org/"}]}',  # blank title fails required
    '{"results":[{"title":"T","url":"https://mwmbl.org/","content":5}]}',  # content must be a string
    '{"results":["str"]}',                             # members must be objects
    '{"results":[{"title":"T","title":"U","url":"https://mwmbl.org/"}]}',  # duplicate key
    'mwmbl',                                           # not JSON at all
])
def test_mwmbl_shape_failures_are_parse_errors_upstream_schema(body):
    with pytest.raises(SourceError) as exc:
        run(Mwmbl, body)
    assert exc.value.status == "parse_error"


def test_mwmbl_http_401_is_auth_failed():
    r = res('{"error":"invalid api key"}', status=401)
    adapter = Mwmbl({"id": "mwmbl", "params": {}}, None)
    with pytest.raises(SourceError) as exc:
        adapter.parse(r, "q", 5)
    assert exc.value.status == "auth_failed"
    assert exc.value.http_status == 401


async def test_mwmbl_key_travels_in_the_x_api_key_header(monkeypatch):
    monkeypatch.delenv("BZS_MWMBL_KEY", raising=False)
    fetcher = RecordingFetcher(SEARXNG_OK)
    adapter = Mwmbl({"id": "mwmbl", "params": {}}, fetcher)
    hits, r = await adapter.search("fixture query", 5, None, 5)
    assert r.http_status == 200 and len(hits) == 3
    headers = fetcher.calls[0]["headers"]
    assert headers["User-Agent"] == API_USER_AGENT
    # Anonymous S0 path: no credentials at all.
    assert "Authorization" not in headers and "X-API-Key" not in headers
    monkeypatch.setenv("BZS_MWMBL_KEY", "fixture-free-key")
    await adapter.search("fixture query", 5, None, 5)
    assert fetcher.calls[1]["headers"]["X-API-Key"] == "fixture-free-key"
    assert "Authorization" not in fetcher.calls[1]["headers"]


class _StatusFetcher(RecordingFetcher):
    """RecordingFetcher always answers 200; this one lets a test choose the
    HTTP status so the 4xx FetchResult is built the way fetch.py builds it
    (status="http_error")."""

    def __init__(self, body, status):
        super().__init__(body)
        self.http_status = status

    async def fetch(self, url, params=None, timeout_s=None, accept=None, headers=None, **kw):
        self.calls.append({"url": url, "params": params, "accept": accept, "headers": headers})
        return res(self.body, status=self.http_status)


async def test_mwmbl_401_is_auth_failed_through_the_real_search_path(monkeypatch):
    # F6: fetch marks every >=400 response status="http_error", and the old
    # auth_failed branch lived in parse(), which Adapter.search never reaches
    # once check() raises - so a real 401 surfaced as http_error. The
    # classification must hold on the check path, end to end.
    monkeypatch.setenv("BZS_MWMBL_KEY", "fixture-bad-key")
    fetcher = _StatusFetcher('{"error":"invalid api key"}', 401)
    adapter = Mwmbl({"id": "mwmbl", "params": {}}, fetcher)
    with pytest.raises(SourceError) as exc:
        await adapter.search("fixture query", 5, None, 5)
    assert exc.value.status == "auth_failed"
    assert exc.value.http_status == 401
    # search() attaches the raw result so probe evidence survives the failure.
    assert exc.value.result is not None and exc.value.result.http_status == 401


def test_mwmbl_401_via_check_plus_parse_is_auth_failed():
    # Same real fetch construction through the run() helper (check + parse),
    # which the pre-F6 code turned into http_error.
    with pytest.raises(SourceError) as exc:
        run(Mwmbl, '{"error":"invalid api key"}', status=401)
    assert exc.value.status == "auth_failed" and exc.value.http_status == 401
