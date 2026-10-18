"""Free, documented APIs (no credits, no paid tiers required)."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote

from lxml import etree

from .base import API_USER_AGENT, Adapter, Hit, SourceError, clean_text


class JsonApi(Adapter):
    kind = "api"

    def json(self, res):
        try:
            return json.loads(self.text(res))
        except ValueError as exc:
            raise SourceError("parse_error", f"invalid JSON: {exc}", res.http_status) from exc


class Wikipedia(JsonApi):
    async def request(self, query, limit, language, timeout_s):
        lang = self.params.get("lang", "en")
        base = self.params.get("base_url", f"https://{lang}.wikipedia.org/w/api.php")
        params = {"action": "query", "list": "search", "srsearch": query, "srlimit": str(min(limit, 20)),
                  "format": "json", "utf8": "1", "srprop": "snippet"}
        return await self.fetcher.fetch(base, params=params, timeout_s=timeout_s, accept="application/json",
                                        headers={"User-Agent": API_USER_AGENT})

    def parse(self, res, query, limit):
        data = self.json(res)
        lang = self.params.get("lang", "en")
        items = data.get("query", {}).get("search", [])
        hits = []
        for item in items:
            title = item.get("title", "")
            if not title:
                continue
            url = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,')}"
            hits.append(Hit(url, title, clean_text(item.get("snippet")), len(hits) + 1))
        return hits


class Arxiv(Adapter):
    kind = "api"
    default_base = "https://export.arxiv.org/api/query"
    _ns = {"a": "http://www.w3.org/2005/Atom"}

    async def request(self, query, limit, language, timeout_s):
        words = [w for w in re.split(r"\s+", query.replace('"', " ")) if w][:8]
        expr = " AND ".join(f"all:{w}" for w in words) or "all:*"
        return await self.fetcher.fetch(self.base_url, params={"search_query": expr, "start": "0",
                                        "max_results": str(min(limit, 30)), "sortBy": "relevance"},
                                        timeout_s=timeout_s, accept="application/atom+xml",
                                        headers={"User-Agent": API_USER_AGENT})

    def parse(self, res, query, limit):
        try:
            root = etree.fromstring(res.body, parser=etree.XMLParser(resolve_entities=False, no_network=True))
        except etree.XMLSyntaxError as exc:
            raise SourceError("parse_error", f"invalid Atom: {exc}", res.http_status) from exc
        hits = []
        for entry in root.findall("a:entry", self._ns):
            url = (entry.findtext("a:id", default="", namespaces=self._ns) or "").strip().replace("http://", "https://", 1)
            title = clean_text(entry.findtext("a:title", default="", namespaces=self._ns))
            summary = clean_text(entry.findtext("a:summary", default="", namespaces=self._ns), 400)
            if url.startswith("https://") and title:
                hits.append(Hit(url, title, summary, len(hits) + 1))
        return hits


class StackExchange(JsonApi):
    default_base = "https://api.stackexchange.com/2.3/search/excerpts"

    async def request(self, query, limit, language, timeout_s):
        params = {"order": "desc", "sort": "relevance", "q": query, "site": self.params.get("site", "stackoverflow"),
                  "pagesize": str(min(limit, 30))}
        return await self.fetcher.fetch(self.base_url, params=params, timeout_s=timeout_s, accept="application/json",
                                        headers={"User-Agent": API_USER_AGENT})

    def parse(self, res, query, limit):
        data = self.json(res)
        if data.get("backoff"):
            raise SourceError("rate_limited", "Stack Exchange asked to back off", res.http_status, int(data["backoff"]) * 1000)
        site = self.params.get("site_url", "https://stackoverflow.com")
        hits = []
        for item in data.get("items", []):
            if item.get("item_type") == "answer" and item.get("answer_id"):
                url = f"{site}/a/{item['answer_id']}"
            elif item.get("question_id"):
                url = f"{site}/questions/{item['question_id']}"
            else:
                continue
            hits.append(Hit(url, clean_text(item.get("title")), clean_text(item.get("excerpt"), 400), len(hits) + 1))
        return hits


class HackerNews(JsonApi):
    default_base = "https://hn.algolia.com/api/v1/search"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"query": query, "hitsPerPage": str(min(limit, 30)),
                                        "tags": "story"}, timeout_s=timeout_s, accept="application/json",
                                        headers={"User-Agent": API_USER_AGENT})

    def parse(self, res, query, limit):
        hits = []
        for item in self.json(res).get("hits", []):
            url = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('objectID')}"
            snippet = clean_text(item.get("story_text"), 300) or f"{item.get('points', 0)} points, {item.get('num_comments', 0)} comments"
            if item.get("title"):
                hits.append(Hit(url, clean_text(item["title"]), snippet, len(hits) + 1))
        return hits


class GitHubRepos(JsonApi):
    default_base = "https://api.github.com/search/repositories"

    async def request(self, query, limit, language, timeout_s):
        headers = {"User-Agent": API_USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get(self.params.get("token_env", "BZS_GITHUB_TOKEN"), "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return await self.fetcher.fetch(self.base_url, params={"q": query, "per_page": str(min(limit, 30))},
                                        timeout_s=timeout_s, accept="application/vnd.github+json", headers=headers)

    def check(self, res):
        if res.http_status == 403 and res.headers.get("x-ratelimit-remaining") == "0":
            raise SourceError("rate_limited", "GitHub rate limit", 403, 60000)
        super().check(res)

    def parse(self, res, query, limit):
        hits = []
        for item in self.json(res).get("items", []):
            desc = clean_text(item.get("description"), 300)
            extra = ", ".join(x for x in (item.get("language") or "", f"★{item.get('stargazers_count', 0)}") if x)
            hits.append(Hit(item.get("html_url", ""), item.get("full_name", ""), f"{desc} ({extra})".strip(), len(hits) + 1))
        return [h for h in hits if h.url.startswith("https://")]


class Marginalia(JsonApi):
    default_base = "https://api2.marginalia-search.com/search"

    async def request(self, query, limit, language, timeout_s):
        key = os.environ.get(self.params.get("key_env", "BZS_MARGINALIA_KEY"), "") or "public"
        return await self.fetcher.fetch(self.base_url, params={"query": query, "count": str(min(limit, 30))},
                                        timeout_s=timeout_s, accept="application/json",
                                        headers={"API-Key": key, "User-Agent": API_USER_AGENT})

    def check(self, res):
        if res.http_status == 503:
            raise SourceError("rate_limited", "shared public key is rate limited", 503, 600000)
        super().check(res)

    def parse(self, res, query, limit):
        hits = []
        for item in self.json(res).get("results", []):
            if item.get("url", "").startswith(("http://", "https://")):
                hits.append(Hit(item["url"], clean_text(item.get("title")), clean_text(item.get("description"), 400), len(hits) + 1))
        return hits
