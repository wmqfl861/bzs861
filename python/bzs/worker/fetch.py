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
from .revalidation import conditional_headers, request_profile
from .freshness import raw_response_freshness
from .redirect_cache import make_chain

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_META_CHARSET = re.compile(rb"<meta[^>]{0,200}?charset=[\"']?\s*([A-Za-z0-9_\-]{2,40})", re.I)
_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "big5": "big5hkscs", "utf8": "utf-8"}
_CROSS_ORIGIN_HEADERS = frozenset(("user-agent", "accept", "accept-language", "accept-encoding"))
_RESOURCE_CONDITIONS = frozenset(("if-none-match", "if-modified-since", "if-match",
                                  "if-unmodified-since", "if-range"))


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
class DirectReadControl:
    """Cancellation and receipts for an ordinary read, without durable grants.

    Only the network child task is interrupted. Fetcher catches that interruption
    beside its counters and returns the usual artifact/usage envelope; the RPC
    and extraction task are never cancelled by this control.
    """
    cancelled: bool = False
    external_requests: int = 0
    content_bytes: int = 0
    _task: asyncio.Task | None = field(default=None, repr=False)

    def check(self) -> None:
        if self.cancelled:
            raise FetchStopped("E_CANCELLED", "direct read was cancelled")

    def cancel(self) -> None:
        if not self.cancelled:
            self.cancelled = True
            if self._task is not None:
                self._task.cancel()


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
    request_profile: str = ""
    http_redirect_chain: dict | None = None
    http_redirect_observations: list[dict] = field(default_factory=list)
    response_content_bytes: int = 0
    conditioned_binding_id: str = ""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def header_charset(self) -> str | None:
        m = re.search(r"(?:^|;)\s*charset\s*=\s*['\"]?([a-z0-9_-]{1,40})", self.headers.get("content-type", ""), re.I)
        return m.group(1) if m else None


GZIP_MAGIC = b"\x1f\x8b"


def gunzip_archive(body: bytes, max_bytes: int) -> bytes:
    """Decode exactly one complete gzip archive member, bounded on output.

    Sitemaps are commonly published as .xml.gz archives delivered as the entity
    body (not transport Content-Encoding, which the HTTP client already
    removes). Output is capped at max_bytes before any caller sees it; a
    damaged, truncated or multi-member archive is refused instead of partially
    decoded. Raises ValueError with "too large" for budget overflow.
    """
    import zlib
    if not body.startswith(GZIP_MAGIC):
        raise ValueError("not a gzip archive")
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = bytearray()
        chunk = body
        while True:
            piece = d.decompress(chunk, max_bytes - len(out) + 1)
            out += piece
            if len(out) > max_bytes:
                raise ValueError("decompressed archive too large")
            if d.eof:
                break
            tail = d.unconsumed_tail
            if not tail:
                raise ValueError("damaged gzip archive")  # input ended mid-stream
            if not piece and tail is chunk:
                raise ValueError("damaged gzip archive")  # no forward progress
            chunk = tail
        if d.unused_data.strip(b"\x00"):
            raise ValueError("trailing data after the gzip member")
        return bytes(out)
    except zlib.error as exc:
        raise ValueError(f"damaged gzip archive: {exc}") from exc


def decode_body(body: bytes, header_charset: str | None) -> tuple[str, str]:
    """Honor BOM or a usable declared text encoding, replacing damaged bytes.

    Guess UTF-8 / GB18030 / CP1252 only when no usable declaration exists.
    This display decoder is separate from strict raw robots/XML parsing.
    """
    if body.startswith(codecs.BOM_UTF8):
        return body.decode("utf-8-sig", errors="replace"), "utf-8-sig"
    declared: list[str] = [header_charset] if header_charset else []
    m = _META_CHARSET.search(body[:4096])
    if m:
        declared.append(m.group(1).decode("ascii"))
    for name in declared:
        name = _ALIASES.get(name.lower(), name.lower())
        try:
            # bytes.decode rejects unknown and non-text codecs; some known
            # codecs (for example IDNA) also cannot use replacement decoding.
            return body.decode(name, errors="replace"), name
        except (LookupError, UnicodeError, TypeError):
            continue
    for name in ("utf-8", "gb18030", "cp1252"):
        try:
            return body.decode(name), name
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace"), "utf-8-replace"


MAX_HOST_LOCKS = 4096


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
            if len(self._host_locks) >= MAX_HOST_LOCKS:
                # Bound long-run growth: retire idle per-host semaphores. Busy
                # ones keep their entry; the registry can only exceed the cap
                # by the number of concurrently busy distinct hosts, which the
                # bounded pools already limit.
                for idle in [h for h, s in self._host_locks.items() if not s.locked()]:
                    if len(self._host_locks) < MAX_HOST_LOCKS:
                        break
                    del self._host_locks[idle]
            self._host_locks[host] = asyncio.Semaphore(self.per_host)
        return self._host_locks[host]

    def request_headers(self, *, accept: str | None = None, headers: dict | None = None) -> dict[str, str]:
        # Do not let the installed optional decoder set change a cached
        # representation's request identity across worker restarts.
        result = {"User-Agent": self.user_agent, "Accept-Language": self.accept_language,
                  "Accept-Encoding": "gzip, deflate",
                  "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        for key, value in (headers or {}).items():
            result = {old: item for old, item in result.items() if old.lower() != key.lower()}
            result[key] = value
        return result

    async def fetch(self, url: str | URL, *, timeout_s: float = 20.0, method: str = "GET",
                    params: dict | None = None, data: dict | None = None, headers: dict | None = None,
                    max_bytes: int | None = None, accept: str | None = None,
                    budget: FetchBudget | None = None, follow_redirects: bool = True,
                    before_dispatch: Callable[[URL], None] | None = None,
                    collect_redirect_cache: bool = False,
                    redirect_conditional: dict | None = None,
                    control: DirectReadControl | None = None) -> FetchResult:
        assert self._session is not None, "Fetcher.start() not called"
        if budget is not None and control is not None:
            raise ValueError("ordinary cancellation cannot carry a durable grant")
        started = time.monotonic()
        deadline = started + timeout_s
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        result = FetchResult(status="network_error", url=str(url))
        current = url
        req_headers = self.request_headers(accept=accept, headers=headers)
        # The original URI is always dispatched. A route only selects final-URI
        # validators after every preceding live HTTP response matched its prefix.
        expected_route = ([redirect_conditional["request_url"], *redirect_conditional["redirects"]]
                          if redirect_conditional is not None else [])
        actual_route: list[str] = []
        route_live = bool(expected_route)
        cache_hops: list[dict] = []
        cache_chain_eligible = method == "GET" and params is None and data is None
        try:
            if control is not None:
                # Registration is inside the try block, before the first await.
                # A cancellation before this child starts is caught by check();
                # one during I/O is caught below with the counters intact.
                control._task = asyncio.current_task()
                control.check()
            for hop in range((self.max_redirects if follow_redirects else 0) + 1):
                if budget:
                    budget.check()
                if control is not None:
                    control.check()
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
                    if control is not None:
                        control.check()
                    # This callback is deliberately synchronous: an admission
                    # refresh must happen outside the host slot, and must use its
                    # own durable single-hop grant. Recheck after waiting so an
                    # expired policy record can never authorize a later request.
                    if before_dispatch is not None:
                        before_dispatch(target)
                    if budget:
                        budget.dispatch()
                    result.external_requests += 1
                    if control is not None:
                        control.external_requests += 1
                    result.request_profile = request_profile(req_headers)
                    result.conditioned_binding_id = ""
                    actual_route.append(str(target))
                    route_live = (route_live and len(actual_route) <= len(expected_route) and
                                  actual_route[-1] == expected_route[len(actual_route) - 1])
                    dispatch_headers = dict(req_headers)
                    if route_live and len(actual_route) == len(expected_route):
                        condition = redirect_conditional["conditional"]
                        if (method == "GET" and condition["url"] == str(target) and
                                condition["request_profile"] == result.request_profile):
                            dispatch_headers.update(conditional_headers(condition))
                            result.conditioned_binding_id = condition["binding_id"]
                    sent_at = time.monotonic()
                    async with self._session.request(method, target, params=params if hop == 0 else None,
                                                     data=data if method == "POST" else None, headers=dispatch_headers,
                                                     allow_redirects=False, timeout=timeout) as resp:
                        result.http_status = resp.status
                        result.response_content_bytes = 0
                        received_at_ms = int(time.time() * 1000)
                        response_delay_ms = int((time.monotonic() - sent_at) * 1000)
                        # Bind policy seen before a body timeout to the actual
                        # HTTP response, including later redirect-chain hops.
                        result.final_url = str(resp.url)
                        result.headers = {k.lower(): v for k, v in resp.headers.items()}
                        # Preserve individual fields so a later directive cannot erase an earlier noindex,
                        # and an agent-specific field does not change the scope of another field.
                        robots = resp.headers.getall("X-Robots-Tag", ())
                        if robots:
                            result.headers["x-robots-tag"] = "\n".join(robots)
                        # Combining list-valued fields is safe; a repeated
                        # singleton must remain detectable by the cache parser.
                        # Apply the same rule to interactive and scheduled reads.
                        for name in ("Cache-Control", "Pragma", "Vary", "Retry-After", "Age", "Date", "Expires",
                                     "ETag", "Last-Modified", "Content-Location"):
                            fields = resp.headers.getall(name, ())
                            if fields:
                                separator = "," if name in ("Cache-Control", "Pragma", "Vary") else "\n"
                                result.headers[name.lower()] = separator.join(fields)
                        if collect_redirect_cache:
                            hop_freshness = raw_response_freshness(
                                result.headers, str(resp.url), result.request_profile,
                                received_at_ms, response_delay_ms)
                        redirect = resp.status in (301, 302, 303, 307, 308) and "location" in result.headers
                        if redirect and collect_redirect_cache:
                            # Following the existing response is separate from
                            # proving an unambiguous reusable Location selection.
                            if len(resp.headers.getall("Location", ())) != 1:
                                cache_chain_eligible = False
                            result.http_redirect_observations.append({
                                "url": str(resp.url), "http_status": resp.status,
                                "http_freshness": dict(hop_freshness)})
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
                                result.response_content_bytes += len(chunk)
                                size += len(chunk)
                                if not redirect:
                                    chunks.append(chunk)
                        elif not redirect:
                            async for chunk in resp.content.iter_chunked(65536):
                                result.content_bytes += len(chunk)
                                result.response_content_bytes += len(chunk)
                                if control is not None:
                                    control.content_bytes += len(chunk)
                                size += len(chunk)
                                if size > limit:
                                    chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                                    result.truncated = True
                                    break
                                chunks.append(chunk)
                        if redirect:
                            nxt = self.policy.prepare_url(urljoin(str(resp.url), result.headers["location"]))
                            result.redirects.append(str(nxt))
                            if collect_redirect_cache:
                                cache_hops.append({"url": str(resp.url), "target_url": str(nxt),
                                                   "http_status": resp.status,
                                                   "content_bytes": result.response_content_bytes,
                                                   "http_freshness": dict(hop_freshness)})
                            if not follow_redirects:
                                result.final_url = str(resp.url)
                                result.status = "redirect"
                                return result
                            # Validators name one selected representation at
                            # one exact URI. Even a same-origin redirect is a
                            # new resource and must not inherit those conditions.
                            req_headers = {k: v for k, v in req_headers.items()
                                           if k.lower() not in _RESOURCE_CONDITIONS}
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
                        if collect_redirect_cache and cache_chain_eligible and not result.truncated:
                            result.http_redirect_chain = make_chain(str(url), result.final_url, cache_hops, result.request_profile)
                        result.status = ("not_modified" if resp.status == 304 else
                                         "ok" if resp.status < 400 else "http_error")
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
        except asyncio.CancelledError:
            if control is None or not control.cancelled:
                raise
            # Only a cancellation explicitly requested through this control is
            # converted into an accounted result. Shutdown/client cancellation
            # without that request still propagates normally.
            result.status, result.error_code = "cancelled", "E_CANCELLED"
            result.error = "direct read was cancelled"
        except asyncio.TimeoutError:
            result.status, result.error = "timeout", "request timed out"
        except aiohttp.InvalidURL as exc:
            result.status, result.error = "invalid_url", str(exc)
        except (aiohttp.ClientError, OSError) as exc:
            result.status, result.error = "network_error", f"{type(exc).__name__}: {exc}"[:300]
        finally:
            if control is not None and control._task is asyncio.current_task():
                control._task = None
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
