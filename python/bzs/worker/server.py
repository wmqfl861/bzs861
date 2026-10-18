"""bzs-worker: loopback JSON RPC used by searchd (POST /rpc, Bearer token).

Methods: health, search.multi, fetch.extract. The worker never writes the database and
never decides which sources to query; it executes what searchd asks within its own
network policy (SSRF guard), which it enforces independently.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from aiohttp import web

from .. import __version__
from .extract import EXTRACTOR, extract_html, extract_text, looks_like_challenge
from .fetch import DEFAULT_UA, Fetcher, decode_body, host_of
from .netguard import NetPolicy
from .sources import SourceSet, load_manifest

_META_REFRESH = re.compile(r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["']?\s*(\d+)\s*;\s*url\s*=\s*['"]?([^'">\s]+)""", re.I)
_JS_REPLACE = re.compile(r"""window\.location\.(?:replace\(|href\s*=\s*)["']([^"']+)["']""")
_JS_REDIRECTORS = ("www.sogou.com", "m.sogou.com", "www.so.com")
HTML_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = ("text/plain", "text/markdown", "application/json")


class ManifestMismatch(ValueError):
    pass


class Worker:
    def __init__(self, sources_path: str, policy: NetPolicy, user_agent: str = DEFAULT_UA,
                 accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8", test_hosts: dict[str, str] | None = None,
                 proxy: str | None = None, proxy_remote_dns: bool = False):
        self.policy = policy
        self.fetcher = Fetcher(policy, user_agent=user_agent, accept_language=accept_language, test_hosts=test_hosts,
                               proxy=proxy, proxy_remote_dns=proxy_remote_dns)
        self.specs = load_manifest(sources_path)
        self.sources: SourceSet | None = None

    async def start(self) -> None:
        await self.fetcher.start()
        self.sources = SourceSet(self.specs, self.fetcher)

    async def close(self) -> None:
        await self.fetcher.close()

    async def health(self, params: dict) -> dict:
        return {"ok": True, "version": __version__, "extractor": EXTRACTOR, "proxy": bool(self.fetcher.proxy),
                "adapters": sorted(self.sources.adapters) if self.sources else []}

    async def search_multi(self, params: dict) -> dict:
        assert self.sources is not None
        digest = params.get("manifest_sha256")
        expected = self.sources.manifest_digest
        if not expected or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not hmac.compare_digest(digest, expected):
            raise ManifestMismatch("source manifest mismatch between core and worker")
        query = str(params.get("query", ""))[:1000]
        ids = [str(i) for i in params.get("sources", [])][:20]
        limit = max(1, min(int(params.get("limit", 10)), 30))
        timeout_s = max(1.0, min(float(params.get("timeout_ms", 8000)) / 1000.0, 30.0))
        if not query or not ids:
            raise ValueError("query and sources are required")
        return {"results": await self.sources.run_many(ids, query, limit, params.get("language"), timeout_s)}

    async def fetch_extract(self, params: dict) -> dict:
        url = str(params.get("url", ""))
        timeout_s = max(1.0, min(float(params.get("timeout_ms", 20000)) / 1000.0, 60.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        res = await self.fetcher.fetch(url, timeout_s=timeout_s)
        follows = 0
        while True:
            base = {"http_status": res.http_status, "final_url": res.final_url or url, "redirects": res.redirects}
            if res.status != "ok":
                if res.status == "http_error" and res.body:
                    text, _ = decode_body(res.body, res.header_charset)
                    if looks_like_challenge(res.http_status, text):
                        return {**base, "status": "challenge", "error": "site presented a bot check (not bypassed)"}
                return {**base, "status": res.status, "error": res.error or f"HTTP {res.http_status}"}
            ctype = res.content_type
            body_head = res.body[:512].lstrip().lower()
            if not ctype and (body_head.startswith(b"<!doctype html") or body_head.startswith(b"<html")):
                ctype = "text/html"
            warnings = ["download_truncated"] if res.truncated else []
            if ctype in HTML_TYPES:
                html, charset = decode_body(res.body, res.header_charset)
                if looks_like_challenge(res.http_status, html):
                    return {**base, "status": "challenge", "error": "site presented a bot check (not bypassed)"}
                target = self._soft_redirect(html, res.final_url or url)
                remaining = deadline - loop.time()
                if target and follows < 2 and remaining > 1:
                    follows += 1
                    nxt = await self.fetcher.fetch(target, timeout_s=remaining)
                    nxt.redirects = res.redirects + [target] + nxt.redirects
                    res = nxt
                    continue
                # CPU-bound: keep the event loop free for concurrent searches.
                out = await asyncio.to_thread(extract_html, html, res.final_url or url, res.headers)
                if out["extract_status"] == "empty":
                    return {**base, "status": "empty_content", "error": "no readable text", "warnings": out["warnings"]}
                out["warnings"] = warnings + out["warnings"]
                return {**base, **out, "status": "ok", "content_type": "text/html", "charset": charset, "html": html}
            if ctype in TEXT_TYPES or ctype.startswith("text/"):
                text, charset = decode_body(res.body, res.header_charset)
                out = extract_text(text, res.final_url or url, ctype, res.headers)
                if out["extract_status"] == "empty":
                    return {**base, "status": "empty_content", "error": "empty document"}
                return {**base, **out, "status": "ok", "content_type": "text/plain", "charset": charset,
                        "html": "", "warnings": warnings}
            if ctype == "application/pdf":
                return {**base, "status": "unsupported_content", "error": "PDF reading arrives in S3 (DOC-01)"}
            return {**base, "status": "unsupported_content", "error": f"content type not supported: {ctype or 'unknown'}"}

    @staticmethod
    def _soft_redirect(html: str, base_url: str) -> str | None:
        head = html[:8192]
        m = _META_REFRESH.search(head)
        if m and int(m.group(1)) <= 5 and len(html) < 20000:
            return urljoin(base_url, m.group(2))
        if host_of(base_url) in _JS_REDIRECTORS:
            j = _JS_REPLACE.search(head)
            if j:
                return urljoin(base_url, j.group(1))
        return None


def make_app(worker: Worker, token: str) -> web.Application:
    methods = {"health": worker.health, "search.multi": worker.search_multi, "fetch.extract": worker.fetch_extract}

    async def rpc(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
            return web.json_response({"error": {"code": "E_AUTH", "message": "invalid worker token"}}, status=401)
        if request.headers.get("Origin"):
            return web.json_response({"error": {"code": "E_ORIGIN", "message": "browser origins are refused"}}, status=403)
        try:
            payload = await request.json()
            method = methods[payload["method"]]
            result = await method(payload.get("params") or {})
            return web.json_response({"result": result}, dumps=lambda o: json.dumps(o, ensure_ascii=False))
        except KeyError:
            return web.json_response({"error": {"code": "E_METHOD", "message": "unknown method"}})
        except ManifestMismatch as exc:
            return web.json_response({"error": {"code": "E_MANIFEST_MISMATCH", "message": str(exc)}}, status=400)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": {"code": "E_PARAMS", "message": str(exc)[:300]}})
        except Exception as exc:  # report, never crash the worker
            return web.json_response({"error": {"code": "E_INTERNAL", "message": f"{type(exc).__name__}: {exc}"[:300]}})

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/rpc", rpc)

    async def on_start(_app):
        await worker.start()

    async def on_stop(_app):
        await worker.close()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bzs-worker")
    ap.add_argument("--port", type=int, default=8611)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("--accept-language", default="zh-CN,zh;q=0.9,en;q=0.8")
    ap.add_argument("--proxy", default=None, help="reserved; production proxies are currently rejected")
    ap.add_argument("--proxy-remote-dns", action="store_true",
                    help="reserved; unbound proxy DNS is currently rejected")
    ap.add_argument("--test-mode", action="store_true", help="TEST ONLY: allow explicit fakeweb loopback fixtures")
    ap.add_argument("--allow-cidr", "--allow-private", dest="allow_cidr", action="append", default=[],
                    help="TEST ONLY with --test-mode: permits 127.0.0.1/32; all production allowances are refused")
    ap.add_argument("--test-host", action="append", default=[],
                    help="TEST ONLY: NAME=IP fixed resolution (e.g. bzs-e2e.test=127.0.0.1)")
    args = ap.parse_args(argv)
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        print("E_CONFIG: worker token must be at least 32 characters", file=sys.stderr)
        return 2
    try:
        test_hosts = dict(item.split("=", 1) for item in args.test_host)
        if test_hosts and (not args.test_mode or any(
                not host.endswith(".test") or ip != "127.0.0.1" for host, ip in test_hosts.items())):
            raise ValueError("--test-host requires --test-mode and .test=127.0.0.1 fixtures")
        worker = Worker(args.sources, NetPolicy(args.allow_cidr, test_mode=args.test_mode), args.user_agent, args.accept_language, test_hosts,
                        proxy=args.proxy, proxy_remote_dns=args.proxy_remote_dns)
    except ValueError as exc:
        print(f"E_CONFIG: {exc}", file=sys.stderr)
        return 2
    app = make_app(worker, token)

    async def run() -> None:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", args.port)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        print(json.dumps({"event": "ready", "component": "bzs-worker", "host": "127.0.0.1", "port": port,
                          "version": __version__, "proxy": bool(args.proxy),
                          "allow_cidr": args.allow_cidr}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
