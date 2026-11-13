"""CL-22 regression: a Content-Encoding gzip decompression bomb stays bounded.

D14 (docs/reviews/claude-2026-11-13-phase1_5-rereview.md, phase 4): a loopback
server returns gzip of ~1 GiB of zeros packed into ~1 MiB; the real Fetcher
runs with a small max_bytes. Accepted endings: a clean truncation at
max_bytes, or a clean decompression-error result. Either way the fetch must
return an ordinary FetchResult (no unhandled exception) and Python-heap
growth during the fetch must stay under 64 MiB.

python/pyproject.toml floors aiohttp at 3.13.3, the first release fixing
CVE-2025-69223 (GHSA-6mq8-rvhq-8wgg); tools/requirements-s1-lock.txt pins
3.14.5, whose DeflateBuffer decompresses at most max_decompress_size output
bytes per feed_data call. Against the locked 3.14.5 this test measured a
~1.5 MiB tracemalloc peak; emulating the pre-3.13.3 unbounded per-call
decompression on the same bomb measured ~142 MiB, which the 64 MiB bound
rejects. Red is therefore not achievable against the locked version by
design; the pyproject floor is the guard for unlocked environments.
"""
import asyncio
import tracemalloc
import zlib
from pathlib import Path

from aiohttp import web

import bzs
from bzs.worker.fetch import Fetcher, FetchResult
from bzs.worker.netguard import NetPolicy

MAX_BYTES = 256 * 1024
MEM_LIMIT = 64 * 1024 * 1024


def _gzip_bomb(zeros: int = 1 << 30, block: int = 4 << 20) -> bytes:
    """Compress `zeros` zero bytes as one gzip member without materializing them."""
    comp = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    out = bytearray()
    zero_block = b"\x00" * block
    sent = 0
    while sent < zeros:
        out += comp.compress(zero_block)
        sent += block
    out += comp.flush()
    return bytes(out)


async def test_gzip_bomb_bounded_memory_and_clean_ending(monkeypatch, tmp_path):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    # Import isolation: the shared locked interpreter may carry an editable
    # bzs861 install of another checkout; this test must exercise this tree.
    assert Path(bzs.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1], \
        f"bzs was imported from a foreign tree: {bzs.__file__}"
    print(f"bzs.__file__ = {bzs.__file__}")

    bomb = _gzip_bomb()
    # The scenario requires a real bomb: ~1 GiB of zeros in ~1 MiB of gzip.
    assert 512 * 1024 < len(bomb) < 4 * 1024 * 1024
    ratio = (1 << 30) / len(bomb)

    async def bomb_handler(request):
        resp = web.StreamResponse(headers={
            "Content-Encoding": "gzip",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(bomb))})
        await resp.prepare(request)
        try:
            for i in range(0, len(bomb), 65536):
                await resp.write(bomb[i:i + 65536])
            await resp.write_eof()
        except (ConnectionError, asyncio.CancelledError):
            # The reader stops at max_bytes and drops the connection mid-body;
            # the server-side disconnect is the expected end of this scenario.
            pass
        return resp

    app = web.Application()
    app.router.add_get("/bomb", bomb_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    fetcher = Fetcher(NetPolicy(["127.0.0.1/32"], test_mode=True), max_bytes=MAX_BYTES)
    await fetcher.start()
    try:
        tracemalloc.start(5)
        try:
            baseline = tracemalloc.get_traced_memory()[0]
            tracemalloc.reset_peak()
            result = await fetcher.fetch(f"http://127.0.0.1:{port}/bomb", timeout_s=60.0)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        growth = peak - baseline
        print(f"bomb={len(bomb)} bytes (ratio {ratio:.0f}:1) -> status={result.status} "
              f"truncated={result.truncated} body_len={len(result.body)} "
              f"tracemalloc_peak_growth={growth} ({growth / 1048576:.2f} MiB)")
        # The fetch terminates as an ordinary result: an escaping exception or
        # a crash would fail this test before any assertion below.
        assert isinstance(result, FetchResult)
        assert result.http_status == 200
        assert result.headers.get("content-encoding") == "gzip"
        # D14 accepts two endings on the bounded decompressor: truncation at
        # max_bytes, or a clean decompression-error result (the >=3.13.3,<3.14
        # per-call cap surfaces as ContentEncodingError instead).
        truncated = (result.status == "ok" and result.truncated
                     and len(result.body) == MAX_BYTES)
        decompression_error = (result.status == "network_error"
                               and "contentencodingerror" in result.error.lower())
        assert truncated or decompression_error, (
            f"unexpected bomb ending: status={result.status} "
            f"truncated={result.truncated} body_len={len(result.body)} "
            f"error={result.error!r}")
        # The binding requirement: decompression of the bomb stays bounded.
        assert growth < MEM_LIMIT, (
            f"tracemalloc peak grew {growth} bytes ({growth / 1048576:.2f} MiB) during "
            f"the fetch of a {len(bomb)}-byte gzip bomb (ratio {ratio:.0f}:1); "
            f"the D14 limit is {MEM_LIMIT // 1048576} MiB")
    finally:
        await fetcher.close()
        await runner.cleanup()
