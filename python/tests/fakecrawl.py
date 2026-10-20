"""Controlled static crawl origins with observable requests and publication changes.

All text, approvals and endpoints are synthetic. The process only binds loopback;
the production resolver is never relaxed outside the explicit .test fixtures.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, timedelta
import html
import json
import sys
from urllib.parse import urlsplit

from aiohttp import web


HOSTS = ("bzs-crawl.test", "bzs-links.test", "bzs-deny.test", "bzs-budget.test",
         "bzs-fair.test", "bzs-unreachable.test", "bzs-limited.test")
PARAGRAPH = ("This synthetic static page supplies repeatable body text for the bounded crawler. "
             "Its references describe fixture pages, request accounting, durable results, and source rules. "
             "No remote service or real article is represented by this controlled document. ") * 3


def manifest(port: int) -> dict:
    return {"version": 1, "sites": [{
        "id": host.split(".")[0], "enabled": True, "origin": f"http://{host}:{port}",
        "path_prefixes": ["/crawl/"],
        "review": {"status": "approved", "payment_scheme": "no_payment", "automated_access": "approved",
                   "checked_at": "1970-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                   "terms_url": f"https://{host}/terms", "cache_policy": "allowed_with_limits",
                   "redistribution_policy": "links_only" if host == "bzs-links.test" else "allowed_with_limits",
                   "approved_by": "synthetic-crawl-fixture"},
        "limits": {"max_concurrency": 1, "min_interval_ms": 0,
                   "request_quota": 1000, "quota_window_seconds": 86400}}
        for host in HOSTS]}


def make_app() -> web.Application:
    counts: Counter = Counter()
    order: list[dict] = []
    policies: dict[str, str] = {}
    gates: dict[str, asyncio.Event] = {}

    @web.middleware
    async def record(request, handler):
        if not request.path.startswith("/_test/"):
            host = urlsplit("http://" + request.host).hostname
            counts[(host, request.path_qs)] += 1
            order.append({"host": host, "path": request.path_qs})
        return await handler(request)

    app = web.Application(middlewares=[record])

    async def stats(request):
        return web.json_response({"requests": [{"host": host, "path": path, "count": value}
                                               for (host, path), value in sorted(counts.items())],
                                  "order": order})

    async def control(request):
        data = await request.json()
        policies.update(data.get("policies", {}))
        for name in data.get("close_gates", []):
            gates[name] = asyncio.Event()
        for name in data.get("release_gates", []):
            gates.setdefault(name, asyncio.Event()).set()
        return web.json_response({"ok": True})

    async def robots(request):
        host = urlsplit("http://" + request.host).hostname
        if host == "bzs-unreachable.test":
            return web.Response(status=503, text="synthetic robots outage")
        if host == "bzs-limited.test":
            return web.Response(status=429, text="synthetic robots limit", headers={"Retry-After": "60"})
        rules = "Disallow: /" if host == "bzs-deny.test" else "Disallow: /crawl/private/"
        return web.Response(text=f"User-agent: BzsBot\n{rules}\n", content_type="text/plain",
                            headers={"Cache-Control": "max-age=3600"})

    def document(title: str, text: str = "", links: tuple[str, ...] = (), head: str = "") -> str:
        anchors = "".join(f'<a href="{html.escape(link, quote=True)}">Fixture reference</a>' for link in links)
        return (f'<!doctype html><html><head><title>{title}</title>{head}</head><body><main>'
                f'<h1>{title}</h1><p>{text}</p><p>{PARAGRAPH}</p>{anchors}</main></body></html>')

    async def page(request):
        path = request.path
        headers = []
        if path.startswith("/crawl/gate/"):
            name = path.removeprefix("/crawl/gate/")
            await gates.setdefault(name, asyncio.Event()).wait()
        if path == "/crawl/root":
            content = document("Frontier fixture", "frontierfixtureunique", (
                "a#one", "a#two", "b", "deep/path/leaf", "root", "../outside/ignored"))
            content = content.replace("</main>", '<a rel="nofollow" href="nofollow-target">Do not follow</a></main>')
        elif path in ("/crawl/a", "/crawl/b"):
            content = document("Parent " + path[-1], "frontierfixtureunique", ("shared", "root"))
        elif path == "/crawl/deep/path/leaf":
            content = document("Deep URL, shallow discovery", "frontierfixtureunique")
        elif path == "/crawl/trap":
            content = document("Query fixture", "variantfixtureunique", tuple("query?n=" + str(i) for i in range(8)))
        elif path == "/crawl/calendar":
            content = document("Calendar fixture", "calendarfixtureunique", ("calendar/2026/10/18",))
        elif path.startswith("/crawl/calendar/"):
            content = document("Calendar next", "calendarfixtureunique", (path + "/next",))
        elif path.startswith("/crawl/date-sequence/"):
            current = date.fromisoformat(path.removeprefix("/crawl/date-sequence/").replace("/", "-"))
            following = (current + timedelta(days=1)).strftime("/crawl/date-sequence/%Y/%m/%d")
            content = document("Daily calendar", "datesequencefixtureunique", (following,))
        elif path == "/crawl/date-family":
            content = document("Calendar archive", "datefamilyfixtureunique", tuple(
                f"/crawl/date-family/2026/10/{day:02d}" for day in range(1, 21)))
        elif path == "/crawl/trap-combinations":
            content = document("Filter combinations", "combinationfixtureunique", tuple(
                f"/crawl/catalog/page/{n}?color={n}&size=large" for n in range(1, 11)))
        elif path == "/crawl/trap-query-shapes":
            content = document("Equivalent query ordering", "queryshapefixtureunique", (
                "/crawl/query-shape?a=1&b=2&c=3", "/crawl/query-shape?b=2&c=3&a=1",
                "/crawl/query-shape?c=3&a=1&b=2",
                "/crawl/query-dimensions?" + "&".join(f"k{n}=v" for n in range(9)),
                "/crawl/query-pairs?" + "&".join(f"key={n}" for n in range(17))))
        elif path.startswith("/crawl/repeated/page/"):
            number = int(path.rsplit("/", 1)[-1])
            content = document("Repeated pagination", "repeatedpaginationfixtureunique",
                               (f"/crawl/repeated/page/{number + 1}",))
        elif path == "/crawl/finite-documents":
            content = document("Finite articles and versions", "finitedocumentsfixtureunique", tuple(
                [f"/crawl/articles/2026/10/{n + 1:02d}/article-{n}" for n in range(20)] +
                [f"/crawl/items/{n}" for n in range(100, 105)] +
                [f"/crawl/docs/v1.{n}" for n in range(5)]))
        elif path == "/crawl/recovery":
            content = document("Recovery fixture", "recoveryfixtureunique", ("gate/recovery", "recovery-tail"))
        elif path.startswith("/crawl/policy/"):
            name = path.removeprefix("/crawl/policy/")
            policy = policies.get(name, "allow")
            marker = "policy" + name.replace("-", "") + "unique"
            hidden = '<span' + (' data-nosnippet=""' if policy == "data" else '') + '>restrictedexcerptmarker</span>'
            meta = '<meta name="googlebot" content="noindex,nosnippet,nofollow">' if name == "other-agent" else ""
            content = document("Publication fixture " + name, marker + " " + hidden, head=meta)
            if policy in ("noindex", "nosnippet"):
                # Repeated fields preserve independent agent scopes. The final
                # unrelated field must not erase this crawler's restriction.
                headers = [("X-Robots-Tag", "BzsBot: " + policy), ("X-Robots-Tag", "googlebot: index, follow")]
            elif policy == "empty-noindex":
                content, headers = "", [("X-Robots-Tag", "BzsBot: noindex")]
            elif policy == "empty-meta-noindex":
                content = '<html><head><meta name="BzsBot" content="noindex"></head><body></body></html>'
            elif policy in ("malformed-noindex", "malformed-allow"):
                content = "<html><body>" + "<div>" * 140 + PARAGRAPH + "</div>" * 140 + "</body></html>"
                headers = [("X-Robots-Tag", "BzsBot: noindex,nosnippet" if policy.endswith("noindex") else "BzsBot: index,follow")]
        elif path == "/crawl/nofollow":
            content = document("Nofollow fixture", "nofollowfixtureunique", ("nofollow-target",),
                               '<meta name="BzsBot" content="nofollow">')
        elif path == "/crawl/nosnippet-title":
            content = document("Public title", "titlefixtureunique")
            content = content.replace("<title>Public title</title>",
                                      '<title data-nosnippet="">restrictedtitlemarker</title>')
        elif path == "/crawl/maproot":
            content = document("Map discovery fixture", "", ("map-candidate",))
        elif path.startswith("/crawl/redirect/"):
            kind = request.match_info["tail"].split("/")[-1]
            if kind.startswith("historic-"):
                # The logical URL changes its representation after the old
                # search session has frozen the previous response.
                target_name = kind + ("-new" if policies.get(kind) == "new" else "-old")
                return web.Response(status=302, headers={"Location": "/crawl/policy/" + target_name})
            if kind.startswith("denied-"):
                if policies.get(kind) == "deny":
                    return web.Response(status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/",
                                                             "X-Robots-Tag": "BzsBot: noindex"})
                return web.Response(status=302, headers={"Location": "/crawl/policy/" + kind})
            if kind == "chain-policy":
                redirect_headers = {"Location": "/crawl/policy/chain"}
                if policies.get("chain-redirect") == "noindex":
                    redirect_headers["X-Robots-Tag"] = "BzsBot: noindex"
                return web.Response(status=302, headers=redirect_headers, body=b"counted redirect body")
            target = {"allowed": "/crawl/redirect-target", "scope": "/outside/redirect-target",
                      "private": "http://169.254.169.254/latest/meta-data/",
                      "robots": "/crawl/private/redirect-target", "policy": "/crawl/policy/redirect"}[request.match_info["tail"].split("/")[-1]]
            return web.Response(status=302, headers={"Location": target}, body=b"counted redirect body")
        elif path == "/crawl/large":
            content = document("Large fixture", "largefixtureunique " * 100000)
        else:
            content = document("Static page " + path.rsplit("/", 1)[-1], "frontierfixtureunique")
        return web.Response(text=content, content_type="text/html", headers=headers)

    app.router.add_get("/_test/stats", stats)
    app.router.add_post("/_test/control", control)
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/{tail:.*}", page)
    return app


async def start(port: int = 0) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(make_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


if __name__ == "__main__":
    async def main():
        runner, port = await start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
        print(json.dumps({"event": "ready", "component": "fakecrawl", "port": port}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
