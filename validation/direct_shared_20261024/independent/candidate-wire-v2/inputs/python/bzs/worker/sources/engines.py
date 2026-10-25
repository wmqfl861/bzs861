"""Result-page adapters for general web engines (accepted under ADR-0003).

Selectors are based on the engines' public result pages as of 2026 and are marked
``unverified`` in config/sources.json until ``bzs probe`` passes on the user's machine.
Each parser has a generic fallback (heading links) for small markup changes.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .base import Adapter, Hit, SourceError, clean_text, decode_bing_url, decode_ddg_url, is_cjk_query, node_text


def _first_text(el, selectors: list[str]) -> str:
    for sel in selectors:
        found = el.cssselect(sel)
        for f in found:
            text = node_text(f)
            if text:
                return text
    return ""


def _heading_fallback(doc, base: str, own_hosts: tuple[str, ...], limit: int) -> list[Hit]:
    hits: list[Hit] = []
    seen: set[str] = set()
    for a in doc.cssselect("h2 a[href], h3 a[href]"):
        href = Adapter.absolute(base, a.get("href", ""))
        host = (urlsplit(href).hostname or "").lower()
        if not href.startswith(("http://", "https://")) or any(host.endswith(h) for h in own_hosts) or href in seen:
            continue
        seen.add(href)
        hits.append(Hit(href, node_text(a), "", len(hits) + 1))
        if len(hits) >= limit:
            break
    return hits


class DuckDuckGoHtml(Adapter):
    default_base = "https://html.duckduckgo.com/html/"

    async def request(self, query, limit, language, timeout_s):
        region = self.params.get("region") or ("cn-zh" if (language or "").startswith("zh") or is_cjk_query(query) else "wt-wt")
        return await self.fetcher.fetch(self.base_url, method="POST", data={"q": query, "kl": region, "b": ""},
                                        timeout_s=timeout_s, headers={"Referer": "https://html.duckduckgo.com/"})

    def blocked(self, res, body):
        lower = body.lower()
        return "anomaly-modal" in lower or "bots use duckduckgo too" in lower or res.http_status in (202, 403, 418)

    def parse(self, res, query, limit):
        doc = self.doc(res)
        hits: list[Hit] = []
        for r in doc.cssselect("div.result"):
            if "result--ad" in (r.get("class") or ""):
                continue
            links = r.cssselect("a.result__a")
            if not links:
                continue
            url = decode_ddg_url(links[0].get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            hits.append(Hit(url, node_text(links[0]), _first_text(r, [".result__snippet"]), len(hits) + 1))
        if not hits and "no results" in node_text(doc).lower()[:4000]:
            return []
        if not hits:
            hits = _heading_fallback(doc, res.final_url or self.base_url, ("duckduckgo.com",), limit)
            if not hits:
                raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits


class BingHtml(Adapter):
    default_base = "https://www.bing.com/search"

    async def request(self, query, limit, language, timeout_s):
        zh = (language or "").startswith("zh") or is_cjk_query(query)
        params = {"q": query, "count": str(min(limit, 30)), "form": "QBLH"}
        params.update({"setlang": "zh-Hans", "mkt": "zh-CN"} if zh else {"setlang": "en", "mkt": "en-US"})
        return await self.fetcher.fetch(self.base_url, params=params, timeout_s=timeout_s)

    def blocked(self, res, body):
        lower = body.lower()
        return "/turing/captcha" in lower or 'id="b_captcha"' in lower or "detected unusual traffic" in lower

    def parse(self, res, query, limit):
        doc = self.doc(res)
        hits: list[Hit] = []
        for li in doc.cssselect("li.b_algo"):
            links = li.cssselect("h2 a[href]")
            if not links:
                continue
            url = decode_bing_url(links[0].get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            snippet = _first_text(li, ["div.b_caption p", "p.b_lineclamp2", "p.b_lineclamp3", "p.b_lineclamp4",
                                       ".b_paractl", "p.b_algoSlug", ".b_caption"])
            hits.append(Hit(url, node_text(links[0]), snippet, len(hits) + 1))
        if not hits and doc.cssselect("li.b_no, .b_no"):
            return []
        if not hits:
            hits = [Hit(decode_bing_url(h.url), h.title, h.snippet, h.rank)
                    for h in _heading_fallback(doc, res.final_url or self.base_url, ("bing.com", "microsoft.com"), limit)]
            if not hits:
                raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits


class MojeekHtml(Adapter):
    default_base = "https://www.mojeek.com/search"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"q": query}, timeout_s=timeout_s)

    def blocked(self, res, body):
        return res.http_status == 403 or "automated queries" in body.lower()

    def parse(self, res, query, limit):
        doc = self.doc(res)
        hits: list[Hit] = []
        for li in doc.cssselect("ul.results-standard li"):
            links = li.cssselect("a.ob[href]") or li.cssselect("h2 a[href]")
            if not links:
                continue
            url = links[0].get("href", "")
            if not url.startswith(("http://", "https://")):
                continue
            title = node_text(li.cssselect("h2")[0]) if li.cssselect("h2") else node_text(links[0])
            hits.append(Hit(url, title, _first_text(li, ["p.s"]), len(hits) + 1))
        if not hits and "no pages found" in node_text(doc).lower()[:4000]:
            return []
        if not hits:
            hits = _heading_fallback(doc, res.final_url or self.base_url, ("mojeek.com",), limit)
            if not hits:
                raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits


class BaiduHtml(Adapter):
    default_base = "https://www.baidu.com/s"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"wd": query, "rn": str(min(limit, 50)), "ie": "utf-8"},
                                        timeout_s=timeout_s, headers={"Accept-Language": "zh-CN,zh;q=0.9"})

    def blocked(self, res, body):
        return ("wappass.baidu.com" in (res.final_url or "") or "wappass.baidu.com" in body[:20000]
                or "百度安全验证" in body or "网络不给力" in body)

    def parse(self, res, query, limit):
        doc = self.doc(res)
        hits: list[Hit] = []
        for el in doc.cssselect("#content_left > div.result, #content_left > div.c-container, #content_left > div.result-op"):
            if el.get("data-tuiguang") or "ec_tuiguang" in (el.get("class") or ""):
                continue
            heading = el.cssselect("h3 a[href]")
            if not heading:
                continue
            mu = el.get("mu") or ""
            url = mu if mu.startswith(("http://", "https://")) else self.absolute(res.final_url or self.base_url, heading[0].get("href", ""))
            title = node_text(heading[0])
            snippet = _first_text(el, [".c-abstract", "span[class*='content-right']", ".c-span-last", "div[class*='abstract']"])
            if not snippet:
                snippet = clean_text(node_text(el).replace(title, "", 1), 300)
            hits.append(Hit(url, title, snippet, len(hits) + 1))
        if not hits and ("没有找到" in node_text(doc)[:4000] or "抱歉" in node_text(doc)[:2000]):
            return []
        if not hits:
            raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits


class SogouHtml(Adapter):
    default_base = "https://www.sogou.com/web"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"query": query}, timeout_s=timeout_s,
                                        headers={"Accept-Language": "zh-CN,zh;q=0.9"})

    def blocked(self, res, body):
        return "antispider" in (res.final_url or "") or "antispider" in body[:20000] or "异常访问请求" in body

    def parse(self, res, query, limit):
        doc = self.doc(res)
        base = res.final_url or self.base_url
        hits: list[Hit] = []
        for el in doc.cssselect("div.vrwrap, div.rb"):
            heading = el.cssselect("h3 a[href]")
            if not heading:
                continue
            url = el.get("data-url") or self.absolute(base, heading[0].get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            title = node_text(heading[0])
            snippet = _first_text(el, [".space-txt", ".str-text-info", ".text-layout", "p.str_info", ".star-wiki"])
            if not snippet:
                snippet = clean_text(node_text(el).replace(title, "", 1), 300)
            hits.append(Hit(url, title, snippet, len(hits) + 1))
        if not hits:
            raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits


class So360Html(Adapter):
    default_base = "https://www.so.com/s"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"q": query}, timeout_s=timeout_s,
                                        headers={"Accept-Language": "zh-CN,zh;q=0.9"})

    def blocked(self, res, body):
        return "qcaptcha" in body[:20000] or "安全验证" in body[:5000]

    def parse(self, res, query, limit):
        doc = self.doc(res)
        base = res.final_url or self.base_url
        hits: list[Hit] = []
        for li in doc.cssselect("li.res-list"):
            heading = li.cssselect("h3 a[href]")
            if not heading:
                continue
            a = heading[0]
            url = a.get("data-mdurl") or a.get("data-url") or self.absolute(base, a.get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            hits.append(Hit(url, node_text(a), _first_text(li, ["p.res-desc", ".res-comm-con", ".res-rich"]), len(hits) + 1))
        if not hits:
            raise SourceError("parse_error", "no result blocks recognised", res.http_status)
        return hits
