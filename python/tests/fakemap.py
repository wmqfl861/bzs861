"""Controlled discovery origins. Every response is synthetic and loopback-only."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import gzip
import sys
from urllib.parse import urlsplit

from aiohttp import web

HOSTS = ("bzs-map.test", "bzs-deny.test", "bzs-unreachable.test", "bzs-limited.test")


def manifest(port: int) -> dict:
    """Authorization applies only to synthetic fixture origins, never public sites."""
    return {"version": 1, "sites": [{
        "id": host.split(".")[0], "enabled": True, "origin": f"http://{host}:{port}",
        "path_prefixes": ["/map/"],
        "review": {"status": "approved", "payment_scheme": "no_payment", "automated_access": "approved",
                   "checked_at": "1970-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                   "terms_url": f"https://{host}/terms", "cache_policy": "allowed_with_limits",
                   "redistribution_policy": "links_only", "approved_by": "offline-fixture"},
        "limits": {"max_concurrency": 1, "min_interval_ms": 0,
                   "request_quota": 1000, "quota_window_seconds": 86400}}
        for host in HOSTS]}


def make_app(port_ref: dict) -> web.Application:
    counts: Counter = Counter()
    app = web.Application()

    @web.middleware
    async def count(request, handler):
        if request.path != "/_test/stats":
            host = urlsplit("http://" + request.host).hostname
            counts[(host, request.path)] += 1
        return await handler(request)

    app.middlewares.append(count)

    async def stats(request):
        return web.json_response({"requests": [{"host": host, "path": path, "count": value}
                                               for (host, path), value in sorted(counts.items())]})

    def origin(request):
        return f"http://{request.host}"

    async def robots(request):
        host = urlsplit("http://" + request.host).hostname
        if host == "bzs-unreachable.test":
            return web.Response(status=503, text="temporary fixture outage")
        if host == "bzs-limited.test":
            return web.Response(status=429, text="fixture rate limit", headers={"Retry-After": "60"})
        text = "User-agent: BzsBot\nDisallow: /\n" if host == "bzs-deny.test" else (
            "User-agent: *\nDisallow: /\n\nUser-agent: BzsBot\nDisallow: /map/private\n"
            "Allow: /map/private/public\nSitemap: " + origin(request) + "/map/index.xml\n")
        return web.Response(text=text, content_type="text/plain", headers={"Cache-Control": "max-age=3600"})

    async def index(request):
        b = origin(request)
        return web.Response(text=(
            '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{b}/map/child.xml</loc></sitemap>"
            f"<sitemap><loc>{b}/map/nested.xml</loc></sitemap>"
            f"<sitemap><loc>{b}/outside/sitemap.xml</loc></sitemap></sitemapindex>"), content_type="application/xml")

    async def nested(request):
        b = origin(request)
        return web.Response(text=(
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{b}/map/index.xml</loc></sitemap>"
            f"<sitemap><loc>{b}/map/child.xml</loc></sitemap></sitemapindex>"), content_type="application/xml")

    async def child(request):
        b = origin(request)
        return web.Response(text=(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{b}/map/candidates/sitemap-a#one</loc></url>"
            f"<url><loc>{b}/map/candidates/sitemap-a#two</loc></url>"
            f"<url><loc>{b}/map/deep/path/candidate-b</loc></url>"
            f"<url><loc>{b}/outside/not-in-scope</loc></url></urlset>"), content_type="application/xml")

    async def rss(request):
        b = origin(request)
        return web.Response(text=(
            '<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture feed</title>'
            f"<item><title>RSS candidate</title><link>{b}/map/candidates/rss</link></item>"
            "</channel></rss>"), content_type="application/rss+xml")

    async def atom(request):
        b = origin(request)
        return web.Response(text=(
            '<feed xmlns="http://www.w3.org/2005/Atom" xml:base="' + b + '/map/">'
            '<title>Fixture feed</title><entry><title>Atom candidate</title>'
            '<link rel="alternate" href="candidates/atom"/>'
            '<link rel="self" href="feed.atom"/></entry></feed>'), content_type="application/atom+xml")

    async def html(request):
        return web.Response(text=(
            '<!doctype html><html><head><title>Discovery fixture</title></head><body>'
            '<a href="candidates/html#section">HTML candidate</a>'
            '<a href="candidates/html">same candidate</a><a href="../outside/link">outside</a>'
            '<a href="javascript:alert(1)">invalid</a>'
            '<a href="http://169.254.169.254/latest/meta-data/">metadata</a>'
            '<a href="query?a=1">one</a><a href="query?a=2">two</a><a href="query?a=3">three</a>'
            '</body></html>'), content_type="text/html")

    async def candidate(request):
        return web.Response(text=(
            '<html><head><title>History fixture</title></head><body><main>'
            '<p>This synthetic historyfixturekeyword page can be explicitly read before a map job.</p>'
            '</main></body></html>'), content_type="text/html")

    async def redirect(request):
        target = {"scope": "/outside/redirect-target", "private": "http://169.254.169.254/latest/meta-data/",
                  "robots": "/map/private/redirect-target", "allowed": "/map/index.html"}[request.match_info["kind"]]
        return web.Response(status=302, headers={"Location": target}, body=b"counted redirect body")

    async def entity(request):
        return web.Response(text=(
            '<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY xxe SYSTEM "http://127.0.0.1:'
            + str(port_ref["port"]) + '/_test/entity-target">]>'
            '<urlset><url><loc>&xxe;</loc></url></urlset>'), content_type="application/xml")

    async def compressed(request):
        b = origin(request)
        name = request.match_info["name"]
        if name == "index":
            xml = (f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   f'<sitemap><loc>{b}/map/gzip/leaf.xml.gz</loc></sitemap>'
                   f'<sitemap><loc>{b}/map/gzip/leaf.xml.gz</loc></sitemap>'
                   f'<sitemap><loc>{b}/map/gzip/index.xml.gz</loc></sitemap>'
                   f'<sitemap><loc>{b}/outside/never.xml.gz</loc></sitemap></sitemapindex>')
        else:
            xml = (f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   f'<url><loc>{b}/map/gzip/candidate-one</loc></url>'
                   f'<url><loc>{b}/map/gzip/candidate-one#duplicate</loc></url>'
                   f'<url><loc>{b}/map/private/never</loc></url></urlset>')
        body = gzip.compress(xml.encode("utf-8"), mtime=0)
        if name == "multimember":
            body += gzip.compress(b"<urlset/>", mtime=0)
        if name == "truncated":
            body = body[:-4]
        return web.Response(body=body, content_type="application/gzip")

    app.router.add_get("/map/gzip/{name}.xml.gz", compressed)
    app.router.add_get("/_test/stats", stats)
    app.router.add_get("/robots.txt", robots)
    for path, handler in (("/map/index.xml", index), ("/map/nested.xml", nested), ("/map/child.xml", child),
                          ("/map/feed.xml", rss), ("/map/feed.atom", atom), ("/map/index.html", html),
                          ("/map/entity.xml", entity), ("/map/redirect/{kind}", redirect)):
        app.router.add_get(path, handler)
    app.router.add_get("/{path:.*}", candidate)
    return app


async def start(port: int = 0) -> tuple[web.AppRunner, int]:
    ref: dict = {}
    runner = web.AppRunner(make_app(ref), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    ref["port"] = site._server.sockets[0].getsockname()[1]
    return runner, ref["port"]


if __name__ == "__main__":
    async def main():
        runner, port = await start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
        print(json.dumps({"event": "ready", "component": "fakemap", "port": port}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
