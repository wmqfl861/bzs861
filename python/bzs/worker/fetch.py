"""Guarded HTTP fetching: manual redirects, size/time limits, charset decoding."""
from __future__ import annotations

import asyncio
import codecs
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlsplit

import aiohttp
from yarl import URL

from .netguard import GuardedResolver, NetPolicy, PolicyError
from .publication import restriction_observation

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_META_CHARSET = re.compile(rb"<meta[^>]{0,200}?charset=[\"']?\s*([A-Za-z0-9_\-]{2,40})", re.I)
_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "big5": "big5hkscs", "utf8": "utf-8"}
_CROSS_ORIGIN_HEADERS = frozenset(("user-agent", "accept", "accept-language"))


class FetchStopped(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FetchAdmissionDenied(Exception):
    """A synchronous, connection-adjacent admission check refused dispatch."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class FetchBudget:
    """One durable grant shared by all HTTP and soft redirects of one read attempt.

    Bytes mean decompressed response-body bytes consumed by this application, not
    socket/TLS overhead or data the HTTP stack may already have buffered. The core
    reserves the whole grant before invoking the worker and reconciles only a
    complete response; a lost response therefore cannot create free requests.
    """
    max_requests: int
    max_content_bytes: int
    external_requests: int = 0
    content_bytes: int = 0
    cancelled: bool = False

    def check(self) -> None:
        if self.cancelled:
            raise FetchStopped("E_CANCELLED", "read attempt was cancelled")

    def dispatch(self) -> None:
        self.check()
        if self.external_requests >= self.max_requests:
            raise FetchStopped("E_REQUEST_BUDGET", "HTTP request grant exhausted")
        if self.content_bytes >= self.max_content_bytes:
            raise FetchStopped("E_BYTE_BUDGET", "response-body byte grant exhausted")
        # Charge immediately before dispatch, including DNS/connection failures.
        self.external_requests += 1


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
    redirect_publication_observations: list[dict] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0
    external_requests: int = 0
    content_bytes: int = 0
    error_code: str = ""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def header_charset(self) -> str | None:
        m = re.search(r"(?:^|;)\s*charset\s*=\s*['\"]?([a-z0-9_-]{1,40})", self.headers.get("content-type", ""), re.I)
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
        # aiohttp 3.10+ otherwise retries idempotent requests on a disconnected
        # persistent connection inside a single request() call. A dispatched
        # attempt must correspond to at most one HTTP request in our ledger.
        self._session._retry_connection = False

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _sem(self, host: str) -> asyncio.Semaphore:
        if host not in self._host_locks:
            self._host_locks[host] = asyncio.Semaphore(self.per_host)
        return self._host_locks[host]

    async def fetch(self, url: str | URL, *, timeout_s: float = 20.0, method: str = "GET",
                    params: dict | None = None, data: dict | None = None, headers: dict | None = None,
                    max_bytes: int | None = None, accept: str | None = None,
                    budget: FetchBudget | None = None, follow_redirects: bool = True,
                    before_dispatch: Callable[[URL], None] | None = None) -> FetchResult:
        assert self._session is not None, "Fetcher.start() not called"
        started = time.monotonic()
        deadline = started + timeout_s
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        result = FetchResult(status="network_error", url=str(url))
        current = url
        req_headers = {"User-Agent": self.user_agent, "Accept-Language": self.accept_language,
                       "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if headers:
            req_headers.update(headers)
        try:
            for hop in range((self.max_redirects if follow_redirects else 0) + 1):
                if budget:
                    budget.check()
                checked = self.policy.prepare_url(str(current))
                # The map scheduler supplies an encoded URL only after checking
                # raw path scope. Validate its entire URI again, pin the same
                # public authority, then keep its exact path/query octets in the
                # object passed to aiohttp. Ordinary reads retain normalization.
                if not follow_redirects and isinstance(current, URL):
                    if (current.scheme, current.raw_host, current.port) != (checked.scheme, checked.raw_host, checked.port):
                        raise PolicyError("prepared URL differs from its validated authority")
                    target = current
                else:
                    target = checked
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
                    if budget:
                        budget.check()
                    # This callback is deliberately synchronous: an admission
                    # refresh must happen outside the host slot, and must use its
                    # own durable single-hop grant. Recheck after waiting so an
                    # expired policy record can never authorize a later request.
                    if before_dispatch is not None:
                        before_dispatch(target)
                    if budget:
                        budget.dispatch()
                    result.external_requests += 1
                    async with self._session.request(method, target, params=params if hop == 0 else None,
                                                     data=data if method == "POST" else None, headers=req_headers,
                                                     allow_redirects=False, timeout=timeout) as resp:
                        result.http_status = resp.status
                        # Bind policy seen before a body timeout to the actual
                        # HTTP response, including later redirect-chain hops.
                        result.final_url = str(resp.url)
                        result.headers = {k.lower(): v for k, v in resp.headers.items()}
                        # Preserve individual fields so a later directive cannot erase an earlier noindex,
                        # and an agent-specific field does not change the scope of another field.
                        robots = resp.headers.getall("X-Robots-Tag", ())
                        if robots:
                            result.headers["x-robots-tag"] = "\n".join(robots)
                        if not follow_redirects:
                            cache_control = resp.headers.getall("Cache-Control", ())
                            if cache_control:
                                result.headers["cache-control"] = ",".join(cache_control)
                            retry_after = resp.headers.getall("Retry-After", ())
                            if retry_after:
                                result.headers["retry-after"] = "\n".join(retry_after)
                            for name in ("Age", "Date", "Expires", "Vary"):
                                fields = resp.headers.getall(name, ())
                                if fields:
                                    result.headers[name.lower()] = ("," if name == "Vary" else "\n").join(fields)
                        redirect = resp.status in (301, 302, 303, 307, 308) and "location" in result.headers
                        if redirect and follow_redirects:
                            observation = restriction_observation(result.headers)
                            if observation is not None:
                                # An intermediate URL can restrict itself without
                                # changing the final resource's publication policy.
                                # Capture headers before body/budget failures too.
                                result.redirect_publication_observations.append({
                                    "url": str(resp.url), "http_status": resp.status,
                                    "publication": observation})
                        chunks: list[bytes] = []
                        size = 0
                        # Bounded reads also consume/count redirect bodies. Ordinary
                        # single reads keep their existing redirect/truncation policy.
                        if budget:
                            while not resp.content.at_eof():
                                budget.check()
                                available = min(limit - size, budget.max_content_bytes - budget.content_bytes)
                                if available <= 0:
                                    # A chunked response may deliver its empty EOF
                                    # after the last allowed body byte. Wait for EOF
                                    # or evidence of buffered extra bytes without
                                    # consuming a probe byte beyond the grant.
                                    while not resp.content.is_eof() and resp.content.total_bytes <= size:
                                        budget.check()
                                        remaining = deadline - time.monotonic()
                                        if remaining <= 0:
                                            raise asyncio.TimeoutError()
                                        try:
                                            await asyncio.wait_for(resp.content.wait_eof(), min(0.05, remaining))
                                        except asyncio.TimeoutError:
                                            pass
                                    if resp.content.at_eof():
                                        break
                                    result.truncated = True
                                    code = "E_BYTE_BUDGET" if budget.content_bytes >= budget.max_content_bytes else "E_CONTENT_LIMIT"
                                    raise FetchStopped(code, "response body exceeds its byte grant")
                                chunk = await resp.content.read(min(65536, available))
                                if not chunk:
                                    break
                                budget.content_bytes += len(chunk)
                                result.content_bytes += len(chunk)
                                size += len(chunk)
                                if not redirect:
                                    chunks.append(chunk)
                        elif not redirect:
                            async for chunk in resp.content.iter_chunked(65536):
                                result.content_bytes += len(chunk)
                                size += len(chunk)
                                if size > limit:
                                    chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                                    result.truncated = True
                                    break
                                chunks.append(chunk)
                        if redirect:
                            nxt = self.policy.prepare_url(urljoin(str(resp.url), result.headers["location"]))
                            result.redirects.append(str(nxt))
                            if not follow_redirects:
                                result.final_url = str(resp.url)
                                result.status = "redirect"
                                return result
                            if target.origin() != nxt.origin():
                                # Manual redirects must not forward API keys, bearer tokens, cookies, or
                                # future custom credential headers to another origin or a downgraded URL.
                                req_headers = {k: v for k, v in req_headers.items() if k.lower() in _CROSS_ORIGIN_HEADERS}
                            if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                                method, data = "GET", None
                            current = str(nxt)
                            continue
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
        except FetchAdmissionDenied as exc:
            result.status, result.error = "blocked_by_policy", str(exc)
            result.error_code = exc.code
        except FetchStopped as exc:
            result.status = "cancelled" if exc.code == "E_CANCELLED" else "budget_exhausted"
            result.error_code, result.error = exc.code, str(exc)
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
