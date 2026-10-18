"""Common pieces for search-source adapters.

Adapters translate one query into one upstream request and parse the response into
ranked hits. They never decide policy (which sources, how often): searchd does that.
Blocking is reported, never circumvented (ADR-0003).
"""
from __future__ import annotations

import asyncio
import base64
import html as htmllib
import re
import time
import math
from email.utils import parsedate_to_datetime
from datetime import timezone
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import lxml.html

from ..extract import looks_like_challenge
from ..fetch import FetchResult, Fetcher, decode_body

API_USER_AGENT = "bzs861/0.1 (personal search tool; https://github.com/wmqfl861/bzs861)"
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass
class Hit:
    url: str
    title: str
    snippet: str
    rank: int

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "snippet": self.snippet, "rank": self.rank}


class SourceError(Exception):
    def __init__(self, status: str, message: str, http_status: int | None = None, retry_after_ms: int = 0):
        super().__init__(message)
        self.status = status
        self.http_status = http_status
        self.retry_after_ms = retry_after_ms
        self.result: FetchResult | None = None


def clean_text(value: str | None, limit: int = 1024) -> str:
    if not value:
        return ""
    text = htmllib.unescape(_TAGS.sub("", value))
    return _SPACE.sub(" ", text).strip()[:limit]


def node_text(el) -> str:
    return clean_text(el.text_content() if el is not None else "")


def is_cjk_query(query: str) -> bool:
    return any("぀" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in query)


def retry_after_ms(headers: dict[str, str], now: float | None = None) -> int:
    value = headers.get("retry-after", "").strip()
    # Clamp only at the representable safety boundary, not at the local cooldown.
    maximum = (1 << 62) - 1
    if value.isascii() and value.isdigit():
        return min(int(value[:20]) * 1000, maximum) if len(value) <= 20 else maximum
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        seconds = date.timestamp() - (time.time() if now is None else now)
        return min(max(0, math.ceil(seconds * 1000)), maximum)
    except (ValueError, TypeError, OverflowError):
        return 0


def decode_bing_url(href: str) -> str:
    """Bing wraps results as /ck/a?...&u=a1<base64url>; return the target when present."""
    try:
        parts = urlsplit(href)
        if parts.hostname and parts.hostname.endswith("bing.com") and parts.path.startswith("/ck/a"):
            u = parse_qs(parts.query).get("u", [""])[0]
            if u.startswith("a1"):
                u = u[2:]
            decoded = base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "replace")
            if decoded.startswith(("http://", "https://")):
                return decoded
    except (ValueError, UnicodeError):
        pass
    return href


def decode_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urlsplit(href)
        if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
            target = parse_qs(parts.query).get("uddg", [""])[0]
            if target.startswith(("http://", "https://")):
                return target
    except ValueError:
        pass
    return href


class Adapter:
    """One upstream search endpoint. Subclasses implement ``request`` and ``parse``."""

    kind = "scrape"
    default_base = ""

    def __init__(self, spec: dict[str, Any], fetcher: Fetcher):
        self.spec = spec
        self.id = spec["id"]
        self.params: dict[str, Any] = dict(spec.get("params") or {})
        self.base_url = self.params.get("base_url", self.default_base)
        self.fetcher = fetcher

    async def search(self, query: str, limit: int, language: str | None, timeout_s: float) -> tuple[list[Hit], FetchResult]:
        res = await self.request(query, limit, language, timeout_s)
        try:
            self.check(res)
            hits = await asyncio.to_thread(self.parse, res, query, limit)  # lxml parsing off the event loop
        except SourceError as exc:
            exc.result = res  # lets `bzs probe` keep the raw page as a parser fixture
            raise
        return hits[:limit], res

    async def request(self, query: str, limit: int, language: str | None, timeout_s: float) -> FetchResult:
        raise NotImplementedError

    def parse(self, res: FetchResult, query: str, limit: int) -> list[Hit]:
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------
    def text(self, res: FetchResult) -> str:
        return decode_body(res.body, res.header_charset)[0]

    def check(self, res: FetchResult) -> None:
        if res.http_status == 429:
            raise SourceError("rate_limited", "HTTP 429", 429, retry_after_ms(res.headers))
        if res.status in ("timeout", "network_error", "blocked_by_policy", "invalid_url"):
            raise SourceError("timeout" if res.status == "timeout" else "network_error", res.error, res.http_status)
        body = self.text(res) if res.body else ""
        if self.kind == "scrape" and (self.blocked(res, body) or looks_like_challenge(res.http_status, body)):
            raise SourceError("blocked", "upstream presented a bot check or block page", res.http_status)
        if res.truncated or res.status == "too_large":
            raise SourceError("too_large", "upstream response exceeded the byte limit", res.http_status)
        if res.status == "http_error":
            raise SourceError("http_error", f"HTTP {res.http_status}", res.http_status)

    def blocked(self, res: FetchResult, body: str) -> bool:
        return False

    def doc(self, res: FetchResult):
        body = self.text(res)
        if not body.strip():
            raise SourceError("parse_error", "empty response body", res.http_status)
        try:
            return lxml.html.document_fromstring(body)
        except Exception as exc:  # lxml raises several types
            raise SourceError("parse_error", f"unparseable HTML: {exc}", res.http_status) from exc

    @staticmethod
    def absolute(base: str, href: str) -> str:
        return urljoin(base, href) if href else ""


def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
