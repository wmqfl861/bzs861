"""Free, documented APIs (no credits, no paid tiers required)."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote

from lxml import etree

from .base import API_USER_AGENT, Adapter, Hit, SourceError, clean_text, retry_after_ms


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate API response key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON value")


class JsonApi(Adapter):
    kind = "api"

    def json(self, res):
        try:
            data = json.loads(self.text(res), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, RecursionError) as exc:
            raise SourceError("parse_error", f"invalid JSON: {exc}", res.http_status) from exc
        if not isinstance(data, dict):
            raise SourceError("parse_error", "API response must be an object", res.http_status)
        # Several APIs return application errors with HTTP 200. Such a response
        # must never be accepted as a successful search with no matches.
        if data.get("error") or data.get("error_id") or data.get("errors"):
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else data.get("error_name")
            if code in ("maxlag", "ratelimited", "throttle_violation"):
                raise SourceError("rate_limited", "upstream API requested backoff", res.http_status,
                                  max(5000, retry_after_ms(res.headers), self.backoff_ms(data, res)))
            raise SourceError("upstream_error", "upstream API returned an application error", res.http_status)
        return data

    def backoff_ms(self, data, res):
        value = data.get("backoff", 0)
        if type(value) is not int or value < 0:
            raise SourceError("parse_error", "invalid API backoff", res.http_status)
        return min(value * 1000, (1 << 62) - 1)

    def items(self, data, key, res):
        items = data.get(key)
        if not isinstance(items, list) or len(items) > 1000 or any(not isinstance(x, dict) for x in items):
            raise SourceError("parse_error", f"API response requires an array of objects: {key}", res.http_status)
        return items

    def text_field(self, item, key, res, *, required=False):
        value = item.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            raise SourceError("parse_error", f"invalid API result field: {key}", res.http_status)
        return value


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
        result = data.get("query")
        if not isinstance(result, dict):
            raise SourceError("parse_error", "Wikipedia response requires a query object", res.http_status)
        items = self.items(result, "search", res)
        hits = []
        for item in items:
            title = self.text_field(item, "title", res, required=True)
            url = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,')}"
            hits.append(Hit(url, title, clean_text(self.text_field(item, "snippet", res)), len(hits) + 1))
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
        backoff = self.backoff_ms(data, res)
        if backoff:
            raise SourceError("rate_limited", "Stack Exchange asked to back off", res.http_status,
                              max(backoff, retry_after_ms(res.headers)))
        site = self.params.get("site_url", "https://stackoverflow.com")
        hits = []
        for item in self.items(data, "items", res):
            if item.get("item_type") == "answer" and item.get("answer_id"):
                url = f"{site}/a/{item['answer_id']}"
            elif item.get("question_id"):
                url = f"{site}/questions/{item['question_id']}"
            else:
                continue
            hits.append(Hit(url, clean_text(self.text_field(item, "title", res)),
                            clean_text(self.text_field(item, "excerpt", res), 400), len(hits) + 1))
        return hits


class HackerNews(JsonApi):
    default_base = "https://hn.algolia.com/api/v1/search"

    async def request(self, query, limit, language, timeout_s):
        return await self.fetcher.fetch(self.base_url, params={"query": query, "hitsPerPage": str(min(limit, 30)),
                                        "tags": "story"}, timeout_s=timeout_s, accept="application/json",
                                        headers={"User-Agent": API_USER_AGENT})

    def parse(self, res, query, limit):
        hits = []
        for item in self.items(self.json(res), "hits", res):
            url = self.text_field(item, "url", res) or f"https://news.ycombinator.com/item?id={item.get('objectID')}"
            snippet = clean_text(self.text_field(item, "story_text", res), 300) or f"{item.get('points', 0)} points, {item.get('num_comments', 0)} comments"
            title = self.text_field(item, "title", res)
            if title:
                hits.append(Hit(url, clean_text(title), snippet, len(hits) + 1))
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
        for item in self.items(self.json(res), "items", res):
            desc = clean_text(self.text_field(item, "description", res), 300)
            extra = ", ".join(x for x in (self.text_field(item, "language", res), f"★{item.get('stargazers_count', 0)}") if x)
            hits.append(Hit(self.text_field(item, "html_url", res, required=True),
                            self.text_field(item, "full_name", res, required=True),
                            f"{desc} ({extra})".strip(), len(hits) + 1))
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
        for item in self.items(self.json(res), "results", res):
            url = self.text_field(item, "url", res, required=True)
            if url.startswith(("http://", "https://")):
                hits.append(Hit(url, clean_text(self.text_field(item, "title", res)),
                                clean_text(self.text_field(item, "description", res), 400), len(hits) + 1))
        return hits
