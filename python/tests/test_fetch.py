import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

from bzs.worker.fetch import Fetcher
from bzs.worker.netguard import NetPolicy
from bzs.worker.server import Worker, make_app
from tests import fakeweb


@pytest.fixture
async def web_port():
    runner, port = await fakeweb.start()
    yield port
    await runner.cleanup()


@pytest.fixture
async def fetcher():
    f = Fetcher(NetPolicy(["127.0.0.1/32"], test_mode=True), test_hosts={fakeweb.HOST: "127.0.0.1"})
    await f.start()
    yield f
    await f.close()


async def test_ok_and_redirect(fetcher, web_port):
    r = await fetcher.fetch(f"{fakeweb.base(web_port)}/pages/redirect")
    assert r.status == "ok" and r.final_url.endswith("/pages/article.html")
    assert r.redirects and b"Resumable crawling guide" in r.body


async def test_redirect_to_metadata_blocked(fetcher, web_port):
    r = await fetcher.fetch(f"{fakeweb.base(web_port)}/pages/redirect-private")
    assert r.status == "blocked_by_policy"


async def test_redirect_to_other_loopback_blocked(fetcher, web_port):
    r = await fetcher.fetch(f"{fakeweb.base(web_port)}/pages/redirect-loopback")
    assert r.status == "blocked_by_policy"


async def test_loopback_blocked_without_test_allowance(web_port):
    f = Fetcher(NetPolicy())
    await f.start()
    try:
        assert (await f.fetch(f"http://127.0.0.1:{web_port}/pages/article.html")).status == "blocked_by_policy"
        # A hostname resolving to loopback is refused at connect time as well.
        f2 = Fetcher(NetPolicy(test_mode=True), test_hosts={"rebind.test": "127.0.0.1"})
        await f2.start()
        try:
            assert (await f2.fetch(f"http://rebind.test:{web_port}/pages/article.html")).status == "blocked_by_policy"
        finally:
            await f2.close()
    finally:
        await f.close()


async def test_size_limit_and_timeout(web_port):
    f = Fetcher(NetPolicy(["127.0.0.1/32"], test_mode=True), max_bytes=1000)
    await f.start()
    try:
        r = await f.fetch(f"http://127.0.0.1:{web_port}/pages/big")
        assert r.status == "ok" and r.truncated and len(r.body) == 1000
        slow = await f.fetch(f"http://127.0.0.1:{web_port}/pages/slow", timeout_s=1.0)
        assert slow.status == "timeout"
    finally:
        await f.close()


@pytest.fixture
async def worker(tmp_path, web_port):
    path = tmp_path / "sources.json"
    import json
    path.write_text(json.dumps(fakeweb.manifest(web_port)), encoding="utf-8")
    w = Worker(str(path), NetPolicy(["127.0.0.1/32"], test_mode=True), test_hosts={fakeweb.HOST: "127.0.0.1"})
    await w.start()
    yield w, web_port
    await w.close()


async def test_worker_fetch_extract_states(worker):
    w, port = worker
    b = fakeweb.base(port)
    ok = await w.fetch_extract({"url": f"{b}/pages/article.html"})
    assert ok["status"] == "ok" and "resumable crawling" in ok["text"] and "IGNORE PREVIOUS" not in ok["text"]
    zh = await w.fetch_extract({"url": f"{b}/pages/zh.html"})
    assert zh["status"] == "ok" and "断点续爬" in zh["text"] and zh["charset"] == "gb18030"
    assert (await w.fetch_extract({"url": f"{b}/pages/challenge"}))["status"] == "challenge"
    assert (await w.fetch_extract({"url": f"{b}/pages/paper.pdf"}))["status"] == "unsupported_content"
    assert (await w.fetch_extract({"url": f"{b}/pages/redirect-private"}))["status"] == "blocked_by_policy"
    assert (await w.fetch_extract({"url": f"{b}/pages/other.html"}))["index_allowed"] is False


async def test_worker_search_multi(worker):
    w, port = worker
    out = (await w.search_multi({"query": "resumable", "sources": ["ddg", "bing", "blocked", "wiki", "missing"], "limit": 5,
                                "manifest_sha256": w.sources.manifest_digest}))["results"]
    assert out["ddg"]["status"] == "ok" and out["ddg"]["hits"][0]["url"] == f"{fakeweb.base(port)}/pages/article.html"
    assert out["bing"]["hits"][0]["url"] == f"{fakeweb.base(port)}/pages/article.html/"
    assert out["blocked"]["status"] == "blocked"
    assert out["wiki"]["hits"][0]["title"] == "Web crawler"
    assert out["missing"]["status"] == "error"


async def test_worker_rpc_refuses_mismatched_source_manifest_before_search(tmp_path, monkeypatch):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(fakeweb.manifest(1)), encoding="utf-8")
    w = Worker(str(path), NetPolicy())
    calls = []

    async def record_search(*args):
        calls.append(args)
        return {}

    async with TestClient(TestServer(make_app(w, "fixture-worker-token"))) as client:
        monkeypatch.setattr(w.sources, "run_many", record_search)
        for digest in (None, "", "0" * 64, "é" * 64):
            payload = {"method": "search.multi", "params": {
                "query": "resumable", "sources": ["ddg"], "manifest_sha256": digest}}
            async with client.post("/rpc", json=payload, headers={"Authorization": "Bearer fixture-worker-token"}) as response:
                assert response.status == 400
                assert (await response.json())["error"]["code"] == "E_MANIFEST_MISMATCH"
        assert calls == []
        payload["params"]["manifest_sha256"] = w.sources.manifest_digest
        async with client.post("/rpc", json=payload, headers={"Authorization": "Bearer fixture-worker-token"}) as response:
            assert response.status == 200 and (await response.json())["result"] == {"results": {}}
        assert len(calls) == 1


@pytest.mark.parametrize("host", ["１２７.０.０.１", "127。0。0。1", "127．0．0．1"])
async def test_unicode_private_literals_are_blocked_before_connection(web_port, host):
    f = Fetcher(NetPolicy())
    await f.start()
    try:
        result = await f.fetch(f"http://{host}:{web_port}/pages/article.html")
        assert result.status == "blocked_by_policy" and result.body == b""
    finally:
        await f.close()


async def test_soft_redirect_cannot_hide_a_private_host_with_unicode(worker):
    w, port = worker
    result = await w.fetch_extract({"url": f"{fakeweb.base(port)}/pages/soft-redirect-unicode"})
    assert result["status"] == "blocked_by_policy"


@pytest.mark.parametrize("kind", ["plain", "markdown", "json", "html"])
async def test_repeated_robots_headers_apply_to_every_supported_document_type(worker, kind):
    w, port = worker
    result = await w.fetch_extract({"url": f"{fakeweb.base(port)}/pages/robots/{kind}"})
    assert result["status"] == "ok" and result["text"]
    assert result["index_allowed"] is False


async def test_other_robot_header_does_not_apply_to_this_reader(worker):
    w, port = worker
    result = await w.fetch_extract({"url": f"{fakeweb.base(port)}/pages/robots/scoped"})
    assert result["status"] == "ok" and result["index_allowed"] is True


async def test_redirects_keep_credentials_only_on_the_same_origin(fetcher, web_port):
    headers = {"Authorization": "Bearer fixture-only", "API-Key": "fixture-api-key",
               "X-Future-Credential": "fixture-future-key", "Cookie": "fixture=only"}
    same = await fetcher.fetch(f"{fakeweb.base(web_port)}/headers/redirect/same-origin", headers=headers)
    same_headers = json.loads(same.body)
    assert all(same_headers[key.lower()] == value for key, value in headers.items())
    other = await fetcher.fetch(f"{fakeweb.base(web_port)}/headers/redirect/cross-origin", headers=headers)
    other_headers = json.loads(other.body)
    assert other.status == "ok" and "accept" in other_headers
    assert not any(key.lower() in other_headers for key in headers)


class _MockResponse:
    def __init__(self, url, status=200, headers=(), enter=None):
        self.url = url
        self.status = status
        self.headers = CIMultiDict(headers)
        self.content = self
        self.enter = enter

    async def __aenter__(self):
        if self.enter:
            await self.enter()
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        yield b"fixture body"


async def test_https_downgrade_drops_every_custom_header_without_network():
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((str(url), dict(kwargs["headers"])))
            if len(calls) == 1:
                return _MockResponse(url, 302, [("Location", "http://public.example/downgraded")])
            return _MockResponse(url)

    f = Fetcher(NetPolicy())
    f._session = Session()
    result = await f.fetch("https://public.example/start", headers={"Authorization": "Bearer fixture-only",
                                                                     "API-Key": "fixture-api-key"})
    assert result.status == "ok" and len(calls) == 2
    assert "Authorization" in calls[0][1]
    assert not {"authorization", "api-key"}.intersection(key.lower() for key in calls[1][1])


@pytest.mark.parametrize("destination", ["https://public.example/next", "https://another.example/next"])
async def test_resource_conditions_never_follow_http_redirects(destination):
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((str(url), {key.lower(): value for key, value in kwargs["headers"].items()}))
            if len(calls) == 1:
                return _MockResponse(url, 302, [("Location", destination)])
            return _MockResponse(url)

    fetcher = Fetcher(NetPolicy())
    fetcher._session = Session()
    conditions = {"If-None-Match": 'W/"first"', "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
                  "If-Match": '"first"', "If-Unmodified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
                  "If-Range": '"first"'}
    result = await fetcher.fetch("https://public.example/start", headers=conditions)
    assert result.status == "ok" and result.external_requests == 2
    assert all(calls[0][1][name.lower()] == value for name, value in conditions.items())
    assert not {name.lower() for name in conditions}.intersection(calls[1][1])


async def test_duplicate_cache_singletons_remain_detectable_for_normal_reads():
    class Session:
        def request(self, method, url, **kwargs):
            return _MockResponse(url, headers=[("ETag", '"one"'), ("ETag", '"two"'),
                                               ("Last-Modified", "first"), ("Last-Modified", "second"),
                                               ("Cache-Control", "max-age=60"), ("Cache-Control", "no-store"),
                                               ("Vary", "Accept"), ("Vary", "Cookie")])

    fetcher = Fetcher(NetPolicy())
    fetcher._session = Session()
    result = await fetcher.fetch("https://public.example/page")
    assert result.headers["etag"] == '"one"\n"two"'
    assert result.headers["last-modified"] == "first\nsecond"
    assert result.headers["cache-control"] == "max-age=60,no-store"
    assert result.headers["vary"] == "Accept,Cookie"


async def test_waiting_for_host_capacity_consumes_the_same_request_deadline():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def hold_first():
        entered.set()
        await release.wait()

    class Session:
        def request(self, method, url, **kwargs):
            calls.append(str(url))
            return _MockResponse(url, enter=hold_first if len(calls) == 1 else None)

    f = Fetcher(NetPolicy(), per_host=1)
    f._session = Session()
    first = asyncio.create_task(f.fetch("https://public.example/first", timeout_s=2))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        waiting = await asyncio.wait_for(f.fetch("https://public.example/waiting", timeout_s=0.05), 0.5)
        assert waiting.status == "timeout" and len(calls) == 1
    finally:
        release.set()
        await first
    assert (await f.fetch("https://public.example/after")).status == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("options", [
    {"proxy": "http://127.0.0.1:7890"}, {"proxy": "http://proxy.example:8080"},
    {"proxy_remote_dns": True}, {"proxy": "http://127.0.0.1:7890", "proxy_remote_dns": True},
])
def test_unbound_proxy_modes_are_rejected_before_any_connection(options):
    with pytest.raises(ValueError, match="disabled"):
        Fetcher(NetPolicy(), **options)


def test_gunzip_archive_round_trip_bounds_and_refusals():
    import gzip
    from bzs.worker.fetch import gunzip_archive
    payload = b"<urlset>" + b"<url><loc>https://x.test/a</loc></url>" * 8 + b"</urlset>"
    packed = gzip.compress(payload)
    assert gunzip_archive(packed, len(payload)) == payload
    assert gunzip_archive(packed + b"\x00" * 4, len(payload)) == payload  # NUL padding tolerated
    with pytest.raises(ValueError, match="too large"):
        gunzip_archive(packed, len(payload) - 1)
    with pytest.raises(ValueError, match="not a gzip"):
        gunzip_archive(b"<xml/>", 1024)
    with pytest.raises(ValueError, match="damaged"):
        gunzip_archive(packed[:-4], len(payload))  # truncated CRC/size trailer
    with pytest.raises(ValueError, match="trailing"):
        gunzip_archive(packed + b"zz", len(payload))
    with pytest.raises(ValueError, match="trailing"):
        gunzip_archive(packed + gzip.compress(b"<urlset/>"), len(payload))  # second member
