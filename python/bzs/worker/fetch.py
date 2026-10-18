"""Guarded HTTP fetching: manual redirects, size/time limits, charset decoding."""
from __future__ import annotations

import asyncio
import codecs
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import aiohttp

from .netguard import GuardedResolver, NetPolicy, PolicyError

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_META_CHARSET = re.compile(rb"<meta[^>]{0,200}?charset=[\"']?\s*([A-Za-z0-9_\-]{2,40})", re.I)
_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "big5": "big5hkscs", "utf8": "utf-8"}
_CROSS_ORIGIN_HEADERS = frozenset(("user-agent", "accept", "accept-language"))


@dataclass
class FetchResult:
    status: str                      # ok | http_error | blocked_by_policy | timeout | network_error | too_large | invalid_url
    url: str
    final_url: str = ""
    http_status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def header_charset(self) -> str | None:
        m = re.search(r"charset=([\w\-]+)", self.headers.get("content-type", ""), re.I)
        return m.group(1) if m else None


def decode_body(body: bytes, header_charset: str | None) -> tuple[str, str]:
    """Decode with header charset, BOM, <meta charset>, then UTF-8 / GB18030 / CP1252."""
    candidates: list[str] = []
    if header_charset:
        candidates.append(header_charset)
    if body.startswith(codecs.BOM_UTF8):
        candidates.insert(0, "utf-8-sig")
    m = _META_CHARSET.search(body[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "gb18030", "cp1252"]
    for name in candidates:
        name = _ALIASES.get(name.lower(), name.lower())
        try:
            codecs.lookup(name)
            return body.decode(name), name
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8-replace"


class Fetcher:
    def __init__(self, policy: NetPolicy, *, user_agent: str = DEFAULT_UA,
                 accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8", max_bytes: int = 8 * 1024 * 1024,
                 max_redirects: int = 5, per_host: int = 2, test_hosts: dict[str, str] | None = None,
                 proxy: str | None = None, proxy_remote_dns: bool = False):
        self.policy = policy
        self.test_hosts = test_hosts or {}
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.per_host = per_host
        if proxy or proxy_remote_dns:
            raise ValueError("outbound proxies are disabled until proxy connections can be bound to validated target IPs")
        self.proxy = None
        self.proxy_remote_dns = False
        self._resolver: GuardedResolver | None = None
        self._session: aiohttp.ClientSession | None = None
        self._host_locks: dict[str, asyncio.Semaphore] = {}

    async def start(self) -> None:
        self._resolver = GuardedResolver(self.policy, self.test_hosts)
        connector = aiohttp.TCPConnector(resolver=self._resolver, use_dns_cache=False, limit=64, limit_per_host=4)
        self._session = aiohttp.ClientSession(connector=connector, trust_env=False,
                                              cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=True)

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _sem(self, host: str) -> asyncio.Semaphore:
        if host not in self._host_locks:
            self._host_locks[host] = asyncio.Semaphore(self.per_host)
        return self._host_locks[host]

    async def fetch(self, url: str, *, timeout_s: float = 20.0, method: str = "GET",
                    params: dict | None = None, data: dict | None = None, headers: dict | None = None,
                    max_bytes: int | None = None, accept: str | None = None) -> FetchResult:
        assert self._session is not None, "Fetcher.start() not called"
        started = time.monotonic()
        deadline = started + timeout_s
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        result = FetchResult(status="network_error", url=url)
        current = url
        req_headers = {"User-Agent": self.user_agent, "Accept-Language": self.accept_language,
                       "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if headers:
            req_headers.update(headers)
        try:
            for hop in range(self.max_redirects + 1):
                target = self.policy.prepare_url(current)
                current = str(target)
                host = target.raw_host
                assert host is not None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                sem = self._sem(host)
                await asyncio.wait_for(sem.acquire(), remaining)
                try:
                    # Waiting for another request to this host is part of the same deadline.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    timeout = aiohttp.ClientTimeout(total=remaining, sock_connect=min(10.0, remaining))
                    async with self._session.request(method, target, params=params if hop == 0 else None,
                                                     data=data if method == "POST" else None, headers=req_headers,
                                                     allow_redirects=False, timeout=timeout) as resp:
                        result.http_status = resp.status
                        result.headers = {k.lower(): v for k, v in resp.headers.items()}
                        # Preserve individual fields so a later directive cannot erase an earlier noindex,
                        # and an agent-specific field does not change the scope of another field.
                        robots = resp.headers.getall("X-Robots-Tag", ())
                        if robots:
                            result.headers["x-robots-tag"] = "\n".join(robots)
                        if resp.status in (301, 302, 303, 307, 308) and "location" in result.headers:
                            nxt = self.policy.prepare_url(urljoin(str(resp.url), result.headers["location"]))
                            result.redirects.append(str(nxt))
                            if target.origin() != nxt.origin():
                                # Manual redirects must not forward API keys, bearer tokens, cookies, or
                                # future custom credential headers to another origin or a downgraded URL.
                                req_headers = {k: v for k, v in req_headers.items() if k.lower() in _CROSS_ORIGIN_HEADERS}
                            if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                                method, data = "GET", None
                            current = str(nxt)
                            continue
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > limit:
                                chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                                result.truncated = True
                                break
                            chunks.append(chunk)
                        result.body = b"".join(chunks)
                        result.final_url = str(resp.url)
                        result.status = "ok" if resp.status < 400 else "http_error"
                        return result
                finally:
                    sem.release()
            result.status = "network_error"
            result.error = "too many redirects"
        except PolicyError as exc:
            result.status, result.error = "blocked_by_policy", str(exc)
        except asyncio.TimeoutError:
            result.status, result.error = "timeout", "request timed out"
        except aiohttp.InvalidURL as exc:
            result.status, result.error = "invalid_url", str(exc)
        except (aiohttp.ClientError, OSError) as exc:
            result.status, result.error = "network_error", f"{type(exc).__name__}: {exc}"[:300]
        finally:
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
