"""bzs-worker: loopback JSON RPC used by searchd (POST /rpc, Bearer token).

Methods include health, search.multi, fetch.extract, fetch.cancel, the ordinary
fetch.extract_direct / fetch.cancel_direct pair, and map.fetch. The worker never writes the database and
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
import time
from pathlib import Path
from urllib.parse import urljoin

from aiohttp import web

from .. import __version__
from .extract import EXTRACTOR, extract_html, extract_text, looks_like_challenge
from .freshness import VERSION as FRESHNESS_VERSION, response_freshness
from .fetch import (DEFAULT_UA, DirectReadControl, FetchAdmissionDenied, FetchBudget,
                    FetchStopped, Fetcher, decode_body, host_of)
from .netguard import NetPolicy, PolicyError
from .publication import (PUBLICATION_VERSION, apply_redistribution, empty_extraction_observation,
                          extraction_restriction_observation, incomplete_publication,
                          publication_rules, restriction_observation)
from .revalidation import (VERSION as REVALIDATION_VERSION, ConditionalMismatch, conditional_headers,
                           request_profile, response_cache, validate_conditional)
from .redirect_cache import (CONDITIONAL_VERSION as REDIRECT_CONDITIONAL_VERSION,
                             MAX_OBSERVATIONS, MAX_OBSERVATION_BYTES, encoded_size,
                             validate_redirect_conditional)
from .robots import (CRAWLER_USER_AGENT, MAX_ROBOTS_BYTES, POLICY_VERSION, PRODUCT_TOKEN, require_record,
                     response_record, revalidated_record, rule_decision, validate_revalidation_base)
from .sites import SiteRegistry, canonical_map_url
from .sources import SourceSet, load_manifest
from .sources.base import retry_after_ms

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
                 proxy: str | None = None, proxy_remote_dns: bool = False, sites_path: str | None = None):
        self.policy = policy
        self.fetcher = Fetcher(policy, user_agent=user_agent, accept_language=accept_language, test_hosts=test_hosts,
                               proxy=proxy, proxy_remote_dns=proxy_remote_dns)
        self.specs = load_manifest(sources_path)
        self.sources: SourceSet | None = None
        self.sites = SiteRegistry(sites_path, policy)
        self._read_attempts: dict[str, FetchBudget] = {}
        # expiry, cancelled, cumulative dispatched requests, cumulative body bytes.
        # A replay's usage describes the whole attempt, never just its latest RPC.
        self._retired_attempts: dict[str, tuple[float, bool, int, int]] = {}
        # An independent namespace: ordinary reads retain their own timeout,
        # redirect-body and prefix/truncation contracts, without durable grants.
        self._direct_controls: dict[str, DirectReadControl] = {}
        self._retired_direct_controls: dict[str, tuple[float, bool, int, int]] = {}

    async def start(self) -> None:
        await self.fetcher.start()
        self.sources = SourceSet(self.specs, self.fetcher)

    async def close(self) -> None:
        await self.fetcher.close()

    async def health(self, params: dict) -> dict:
        return {"ok": True, "version": __version__, "extractor": EXTRACTOR, "proxy": bool(self.fetcher.proxy),
                "adapters": sorted(self.sources.adapters) if self.sources else [],
                "robots_policy": POLICY_VERSION, "publication_policy": PUBLICATION_VERSION,
                "http_revalidation": REVALIDATION_VERSION, "http_freshness": FRESHNESS_VERSION,
                "sites_manifest_sha256": self.sites.digest}

    async def fetch_profile(self, params: dict) -> dict:
        if not isinstance(params, dict) or params:
            raise ValueError("fetch.profile does not accept parameters")
        return {"version": FRESHNESS_VERSION,
                "request_profile": request_profile(self.fetcher.request_headers())}

    async def search_multi(self, params: dict) -> dict:
        assert self.sources is not None
        digest = params.get("manifest_sha256")
        expected = self.sources.manifest_digest
        if not expected or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not hmac.compare_digest(digest, expected):
            raise ManifestMismatch("source manifest mismatch between core and worker")
        query = str(params.get("query", ""))[:1000]
        ids = [str(i) for i in params.get("sources", [])][:20]
        limit = max(1, min(int(params.get("limit", 10)), 30))
        timeout_ms = params.get("timeout_ms", 8000)
        # The core sends the remaining monotonic budget. Rounding a remainder
        # below one second up to a full second would enlarge its deadline.
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
            raise ValueError("invalid search.multi timeout_ms")
        timeout_s = timeout_ms / 1000.0
        if not query or not ids:
            raise ValueError("query and sources are required")
        return {"results": await self.sources.run_many(ids, query, limit, params.get("language"), timeout_s)}

    async def fetch_extract(self, params: dict, *, _direct_control: DirectReadControl | None = None) -> dict:
        if not isinstance(params, dict):
            raise ValueError("fetch.extract parameters must be an object")
        conditional = None
        redirect_conditional = None
        if "conditional" in params and "redirect_conditional" in params:
            raise ValueError("direct and redirect conditions are mutually exclusive")
        if "redirect_conditional" in params:
            if set(params) - {"url", "timeout_ms", "attempt_id", "max_requests", "max_content_bytes", "redirect_conditional"}:
                raise ValueError("unknown redirect conditional fetch.extract parameters")
            redirect_conditional = validate_redirect_conditional(params["redirect_conditional"], params.get("url"))
            timeout = params.get("timeout_ms", 20000)
            if type(timeout) is not int or not 1 <= timeout <= 60000:
                raise ValueError("invalid redirect conditional timeout")
        if "conditional" in params:
            if set(params) - {"url", "timeout_ms", "attempt_id", "max_requests", "max_content_bytes", "conditional"}:
                raise ValueError("unknown conditional fetch.extract parameters")
            conditional = validate_conditional(params["conditional"])
            timeout = params.get("timeout_ms", 20000)
            if (not isinstance(params.get("url"), str) or not 1 <= len(params["url"]) <= 4096 or
                    type(timeout) is not int or not 1 <= timeout <= 60000):
                raise ValueError("invalid conditional read identity or timeout")
        bounded = any(key in params for key in ("max_requests", "max_content_bytes", "attempt_id"))
        budget = None
        attempt_id = ""
        if bounded:
            attempt_id = self._attempt_id(params)
            requests, byte_limit = params.get("max_requests"), params.get("max_content_bytes")
            timeout = params.get("timeout_ms", 20000)
            if (type(requests) is not int or not 1 <= requests <= 18 or
                    type(byte_limit) is not int or not 1 <= byte_limit <= 8 * 1024 * 1024 or
                    type(timeout) is not int or not 1 <= timeout <= 55000):
                raise ValueError("invalid durable read grant")
            self._prune_attempts()
            if attempt_id in self._read_attempts:
                raise ValueError("read attempt is already running")
            if attempt_id in self._retired_attempts:
                _, cancelled, used_requests, used_bytes = self._retired_attempts[attempt_id]
                return {"status": "cancelled" if cancelled else "attempt_replayed",
                        "error_code": "E_CANCELLED" if cancelled else "E_ATTEMPT_REPLAY",
                        "error": "read attempt is no longer dispatchable",
                        "usage": {"external_requests": used_requests, "content_bytes": used_bytes}}
            if len(self._read_attempts) + len(self._retired_attempts) >= 2048:
                raise ValueError("read attempt retention capacity reached")
            budget = FetchBudget(requests, byte_limit)
            self._read_attempts[attempt_id] = budget
        try:
            out = await self._fetch_extract(params, budget, conditional, redirect_conditional,
                                            **({"control": _direct_control} if _direct_control is not None else {}))
            if out.get("http_status") == 304:
                # A bodyless result cannot provide new snippet text. Current
                # known-site ceilings can still restrict the old snapshot.
                # This observation belongs to the actual final response. Core
                # separately binds the original/intermediate site ceilings.
                observed = out.get("publication_observation", publication_rules({}))
                identities = ([out.get("final_url", "")] if redirect_conditional is not None or "http_redirect_chain" in out else
                              [params["url"], out.get("final_url", "")])
                ceiling = self.sites.publication_ceiling(observed, identities)
                observation = extraction_restriction_observation(ceiling, "conditional_publication_ceiling")
                if observation is not None:
                    out["publication_observation"] = observation
            if "publication" in out:
                # Preserve the public Worker record's existing whole-read site
                # ceiling. Only a complete HTTP receipt can distinguish the
                # final representation's own policy from that aggregate.
                final_publication = self.sites.publication_ceiling(
                    out["publication"], [out.get("final_url", "")])
                identities = [str(params.get("url", "")), out.get("final_url", ""), *out.get("redirects", [])]
                out["publication"] = self.sites.publication_ceiling(
                    out["publication"], identities)
                if (out.get("status") == "ok" and out.get("http_status") == 200 and
                        "http_redirect_chain" in out and "download_truncated" not in out.get("warnings", []) and
                        final_publication != out["publication"]):
                    out["representation_publication"] = {
                        "version": "bzs-http-representation-publication-1", "url": out["final_url"],
                        "publication": final_publication}
                if "download_truncated" in out.get("warnings", []):
                    # Keep the owner's existing readable-prefix behavior, while
                    # refusing grants whose restrictive tail was not received.
                    out["publication"] = incomplete_publication(out["publication"])
                    out["index_allowed"] = False
                    out["publication_observation"] = out["publication"]
                if out.get("status") == "empty_content":
                    observation = empty_extraction_observation(out["publication"])
                    if observation is not None:
                        out["publication"] = {**out["publication"], "snippet_allowed": observation["snippet_allowed"], "snippet_text": ""}
                        out["publication_observation"] = observation
            if budget:
                out["usage"] = {"external_requests": budget.external_requests, "content_bytes": budget.content_bytes}
            return out
        finally:
            if budget:
                self._read_attempts.pop(attempt_id, None)
                self._retired_attempts[attempt_id] = (asyncio.get_running_loop().time() + 120, budget.cancelled,
                                                    budget.external_requests, budget.content_bytes)

    @staticmethod
    def _direct_control_id(params: dict) -> str:
        if not isinstance(params, dict):
            raise ValueError("direct read parameters must be an object")
        value = params.get("control_id")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{48}", value):
            raise ValueError("invalid direct read control id")
        return value

    def _prune_direct_controls(self) -> None:
        now = asyncio.get_running_loop().time()
        # Running controls are never aged out while their network task exists.
        self._retired_direct_controls = {
            key: value for key, value in self._retired_direct_controls.items() if value[0] > now}

    async def fetch_extract_direct(self, params: dict) -> dict:
        control_id = self._direct_control_id(params)
        if set(params) - {"control_id", "url", "timeout_ms", "conditional", "redirect_conditional"}:
            raise ValueError("unknown ordinary controlled read parameters")
        timeout = params.get("timeout_ms", 20000)
        if (not isinstance(params.get("url"), str) or not 1 <= len(params["url"]) <= 4096 or
                type(timeout) is not int or not 1000 <= timeout <= 60000):
            raise ValueError("invalid ordinary controlled read URL or timeout")
        self._prune_direct_controls()
        if control_id in self._direct_controls:
            raise ValueError("direct read control is already running")
        if control_id in self._retired_direct_controls:
            _, cancelled, requests, byte_count = self._retired_direct_controls[control_id]
            return {"status": "cancelled" if cancelled else "attempt_replayed",
                    "error_code": "E_CANCELLED" if cancelled else "E_ATTEMPT_REPLAY",
                    "error": "direct read control is no longer dispatchable",
                    "usage": {"external_requests": requests, "content_bytes": byte_count}}
        if len(self._direct_controls) + len(self._retired_direct_controls) >= 2048:
            raise ValueError("direct read retention capacity reached")
        control = DirectReadControl()
        self._direct_controls[control_id] = control
        try:
            ordinary = {key: value for key, value in params.items() if key != "control_id"}
            out = await self.fetch_extract(ordinary, _direct_control=control)
            if control.cancelled:
                # A cancel during CPU extraction must not publish a successful
                # body, but already observed cache/publication restrictions are
                # still useful to the core's independent publication fences.
                if "publication" in out:
                    observation = extraction_restriction_observation(
                        out["publication"], "cancelled_direct_read")
                    if observation is not None:
                        out["publication_observation"] = observation
                out = {key: value for key, value in out.items() if key in {
                    "http_status", "final_url", "redirects", "http_freshness",
                    "http_redirect_observations", "redirect_publication_observations", "publication_observation"}}
                out.update(status="cancelled", error_code="E_CANCELLED", error="direct read was cancelled")
            out["usage"] = {"external_requests": control.external_requests, "content_bytes": control.content_bytes}
            return out
        finally:
            self._direct_controls.pop(control_id, None)
            self._retired_direct_controls[control_id] = (
                asyncio.get_running_loop().time() + 120, control.cancelled,
                control.external_requests, control.content_bytes)

    async def fetch_cancel_direct(self, params: dict) -> dict:
        control_id = self._direct_control_id(params)
        if set(params) != {"control_id"}:
            raise ValueError("unknown direct read cancellation parameters")
        self._prune_direct_controls()
        control = self._direct_controls.get(control_id)
        if control is not None:
            control.cancel()
        else:
            if (control_id not in self._retired_direct_controls and
                    len(self._direct_controls) + len(self._retired_direct_controls) >= 2048):
                raise ValueError("direct read retention capacity reached")
            previous = self._retired_direct_controls.get(control_id)
            self._retired_direct_controls[control_id] = (
                previous[0] if previous else asyncio.get_running_loop().time() + 120, True,
                previous[2] if previous else 0, previous[3] if previous else 0)
        return {"cancel_requested": True}

    @staticmethod
    def _attempt_id(params: dict) -> str:
        value = params.get("attempt_id")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{48}", value):
            raise ValueError("invalid read attempt id")
        return value

    def _prune_attempts(self) -> None:
        now = asyncio.get_running_loop().time()
        self._retired_attempts = {key: value for key, value in self._retired_attempts.items() if value[0] > now}

    async def fetch_cancel(self, params: dict) -> dict:
        attempt_id = self._attempt_id(params)
        self._prune_attempts()
        budget = self._read_attempts.get(attempt_id)
        if budget:
            budget.cancelled = True
        else:
            # Remember cancel-before-start without an unbounded attacker-controlled
            # set. A capacity error cannot remove the core's persistent epoch fence.
            if attempt_id not in self._retired_attempts and len(self._read_attempts) + len(self._retired_attempts) >= 2048:
                raise ValueError("read attempt retention capacity reached")
            previous = self._retired_attempts.get(attempt_id)
            self._retired_attempts[attempt_id] = (asyncio.get_running_loop().time() + 120, True,
                                                previous[2] if previous else 0, previous[3] if previous else 0)
        return {"cancel_requested": True}

    async def map_fetch(self, params: dict) -> dict:
        """Execute exactly one durable metadata, discovery or page HTTP hop.

        The core alone schedules robots phases and redirects. No automatic
        request from this method can fall through the interactive-read path.
        """
        allowed = {"url", "kind", "attempt_id", "site_id", "sites_manifest_sha256", "deadline_ms", "timeout_ms",
                   "max_content_bytes", "max_requests", "depth", "max_entries", "robots", "robots_origin", "redirect_hop",
                   "conditional", "robots_revalidation_base"}
        if not isinstance(params, dict) or set(params) - allowed:
            raise ValueError("unknown map.fetch parameters")
        attempt_id = self._attempt_id(params)
        if (params.get("kind") not in ("robots", "auto", "sitemap", "page") or
                not isinstance(params.get("url"), str) or not 1 <= len(params["url"]) <= 4096 or
                not isinstance(params.get("site_id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", params["site_id"]) or
                not isinstance(params.get("sites_manifest_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", params["sites_manifest_sha256"])):
            raise ValueError("invalid map.fetch identity")
        for key, minimum, maximum, default in (("max_requests", 1, 1, None),
                                              ("max_content_bytes", 1, 2 * 1024 * 1024, None),
                                              ("timeout_ms", 1, 55000, None),
                                              ("deadline_ms", 1, (1 << 63) - 1, None),
                                              ("depth", 0, 8, 0), ("max_entries", 1, 1000, 1000),
                                              ("redirect_hop", 0, 5, 0)):
            value = params.get(key, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("invalid map.fetch " + key)
        if params["kind"] == "robots" and "robots" in params:
            raise ValueError("robots metadata fetch cannot supply a content admission record")
        conditional = validate_conditional(params["conditional"]) if "conditional" in params else None
        if conditional is not None and params["kind"] not in ("page", "robots"):
            raise ValueError("conditional map.fetch only supports page and robots representations")
        if "robots_revalidation_base" in params:
            if conditional is None or params["kind"] != "robots":
                raise ValueError("robots_revalidation_base requires a conditional robots request")
            base = params["robots_revalidation_base"]
            record = base.get("record") if isinstance(base, dict) else None
            origin = record.get("origin", "") if isinstance(record, dict) else ""
            validate_revalidation_base(base, conditional, origin=origin, now_ms=int(time.time() * 1000))
        elif conditional is not None and params["kind"] == "robots":
            raise ValueError("conditional robots request requires its bound complete representation")
        self._prune_attempts()
        if attempt_id in self._read_attempts:
            raise ValueError("read attempt is already running")
        if attempt_id in self._retired_attempts:
            _, cancelled, used_requests, used_bytes = self._retired_attempts[attempt_id]
            return {"status": "error", "url": params["url"], "final_url": params["url"], "http_status": None,
                    "error_code": "E_CANCELLED" if cancelled else "E_ATTEMPT_REPLAY",
                    "usage": {"external_requests": used_requests, "content_bytes": used_bytes}}
        if len(self._read_attempts) + len(self._retired_attempts) >= 2048:
            raise ValueError("read attempt retention capacity reached")
        budget = FetchBudget(1, params["max_content_bytes"])
        self._read_attempts[attempt_id] = budget
        try:
            out = await self._map_fetch(params, budget, conditional)
            out["usage"] = {"external_requests": budget.external_requests, "content_bytes": budget.content_bytes}
            return out
        finally:
            self._read_attempts.pop(attempt_id, None)
            self._retired_attempts[attempt_id] = (asyncio.get_running_loop().time() + 120, budget.cancelled,
                                                budget.external_requests, budget.content_bytes)

    async def _map_fetch(self, params: dict, budget: FetchBudget, conditional: dict | None = None) -> dict:
        from .discovery import discover

        out = {"status": "error", "url": params["url"], "final_url": params["url"], "http_status": None}
        site_id = params["site_id"]
        metadata = params["kind"] == "robots"
        mono_deadline = time.monotonic() + min(params["timeout_ms"] / 1000,
                                               (params["deadline_ms"] - int(time.time() * 1000)) / 1000)
        lane = None
        acquired = False

        def remaining() -> float:
            budget.check()
            left = min(mono_deadline - time.monotonic(),
                       (params["deadline_ms"] - int(time.time() * 1000)) / 1000)
            if left <= 0:
                raise asyncio.TimeoutError()
            return left

        def admit_robots(actual, *, dispatch: bool = False) -> None:
            out.pop("robots_decision", None)
            record = params.get("robots")
            try:
                decision = require_record(record, actual, now_ms=int(time.time() * 1000))
            except FetchAdmissionDenied as exc:
                if exc.code == "E_ROBOTS_DISALLOWED":
                    decision = rule_decision(record, actual)
                    out["robots_decision"] = {"version": POLICY_VERSION, "product_token": PRODUCT_TOKEN,
                                              "origin": record["origin"], "target_url": str(actual),
                                              "body_sha256": record["body_sha256"], "allowed": False,
                                              "reason": decision["reason"]}
                raise
            if dispatch:
                out["robots_decision"] = {"version": POLICY_VERSION, "product_token": PRODUCT_TOKEN,
                                          "origin": record["origin"], "target_url": str(actual),
                                          "body_sha256": record["body_sha256"], "allowed": True,
                                          "reason": decision["reason"]}

        try:
            remaining()
            target = canonical_map_url(params["url"], self.policy)
            out["final_url"] = str(target)
            site = self.sites.require(site_id, params["sites_manifest_sha256"], target, metadata=metadata)
            if metadata:
                if params.get("robots_origin", site["origin"]) != site["origin"]:
                    raise FetchAdmissionDenied("E_SITE_SCOPE", "robots chain belongs to another reviewed origin")
                if params.get("redirect_hop", 0) == 0 and str(target) != site["origin"] + "/robots.txt":
                    raise FetchAdmissionDenied("E_SITE_SCOPE", "robots chain must start at the origin's /robots.txt")
            else:
                admit_robots(target)
            headers = self.fetcher.request_headers(
                headers={"User-Agent": CRAWLER_USER_AGENT},
                accept=("text/plain" if metadata else
                        "text/html,application/xhtml+xml,application/xml,text/xml,application/rss+xml,application/atom+xml,text/plain,text/markdown,application/json"))
            if conditional is not None:
                if conditional["url"] != str(target):
                    raise ConditionalMismatch("conditional URL differs from this map target")
                if metadata:
                    try:
                        validate_revalidation_base(params["robots_revalidation_base"], conditional,
                                                   origin=site["origin"], now_ms=int(time.time() * 1000))
                    except ValueError as exc:
                        raise ConditionalMismatch(str(exc)) from exc
                # Configuration/profile changes use this grant's original GET
                # to obtain a complete representation. This is not a retry.
                if conditional["request_profile"] != request_profile(headers):
                    conditional = None
                headers.update(conditional_headers(conditional))
            lane = self.sites.lanes[site_id]
            await asyncio.wait_for(lane.acquire(), remaining())
            acquired = True
            # Pacing and robots metadata share this lane. A cancellation never
            # leaves an uncharged background metadata fetch running behind it.
            while True:
                remaining()
                self.sites.require(site_id, params["sites_manifest_sha256"], target, metadata=metadata)
                gap = site["limits"]["min_interval_ms"] / 1000 - (time.monotonic() - self.sites.last_dispatch.get(site_id, 0))
                if gap <= 0:
                    break
                await asyncio.sleep(min(gap, remaining(), 0.05))

            def before_dispatch(actual):
                remaining()
                if str(actual) != str(target):
                    raise FetchAdmissionDenied("E_SITE_SCOPE", "actual request differs from its admitted map target")
                self.sites.require(site_id, params["sites_manifest_sha256"], actual, metadata=metadata)
                if not metadata:
                    admit_robots(actual, dispatch=True)
                elif conditional is not None:
                    try:
                        validate_revalidation_base(params["robots_revalidation_base"], conditional,
                                                   origin=site["origin"], now_ms=int(time.time() * 1000))
                    except ValueError as exc:
                        raise FetchAdmissionDenied("E_CONDITIONAL_MISMATCH", str(exc)) from exc
                self.sites.last_dispatch[site_id] = time.monotonic()

            response = await self.fetcher.fetch(target, timeout_s=remaining(), budget=budget,
                                                max_bytes=min(params["max_content_bytes"], MAX_ROBOTS_BYTES if metadata else 2 * 1024 * 1024),
                                                headers=headers,
                                                follow_redirects=False, before_dispatch=before_dispatch)
            out.update(final_url=response.final_url or str(target), http_status=response.http_status)
            # Preserve observed cache fields before extraction or conditional
            # validation can fail. All response classes may tighten old reuse.
            if response.http_status is not None:
                out["http_freshness"] = response_freshness(response)
            if not metadata and response.http_status is not None:
                observation = restriction_observation(response.headers)
                if response.http_status == 304:
                    observation = extraction_restriction_observation(
                        apply_redistribution(publication_rules(response.headers), site["review"]),
                        "conditional_publication_ceiling")
                if observation is not None:
                    # Keep this on failures as well as successes. It is a
                    # monotonic restriction, never a successful read artifact.
                    out["publication_observation"] = observation
            retry = max((retry_after_ms({"retry-after": value})
                         for value in response.headers.get("retry-after", "").split("\n")), default=0)
            if response.http_status == 429:
                retry = max(retry, 10 * 60 * 1000)
            if response.http_status in (401, 403):
                retry = max(retry, 30 * 60 * 1000)
            if retry:
                out["retry_after_ms"] = retry
                # Install this fence before releasing the site lane. A queued
                # discovery or robots hop cannot slip past a newly seen 429.
                self.sites.cooldown_until[site_id] = max(self.sites.cooldown_until.get(site_id, 0), time.monotonic() + retry / 1000)
            budget.check()
            if response.error_code.startswith(("E_SITE_", "E_ROBOTS_", "E_CONDITIONAL_")):
                out["error_code"] = response.error_code
                return out
            if response.http_status == 304:
                try:
                    if (conditional is None or response.status != "not_modified" or response.redirects or
                            response.truncated or response.body or response.content_bytes):
                        raise ValueError("304 response has no matching complete representation grant")
                    cache = response_cache(response.headers, url=out["final_url"],
                                           profile=response.request_profile, conditional=conditional)
                    if metadata:
                        out["robots"] = revalidated_record(params["robots_revalidation_base"], cache,
                                                            now_ms=int(time.time() * 1000),
                                                            response_delay_ms=response.elapsed_ms)
                    out.update(status="not_modified", binding_id=conditional["binding_id"], http_cache=cache)
                except (ValueError, ConditionalMismatch, FetchAdmissionDenied):
                    out["error_code"] = "E_HTTP_REVALIDATION"
                    if metadata:
                        # Unbound/invalid 304 is an unreachable policy, never
                        # an implicit renewal of the old permitting record.
                        out["robots"], _ = response_record(origin=site["origin"], status="not_modified",
                                                          http_status=304, headers=response.headers, body=b"",
                                                          truncated=False, now_ms=int(time.time() * 1000))
                return out
            if response.status == "redirect":
                if params.get("redirect_hop", 0) >= 5:
                    out["error_code"] = "E_REDIRECT_LIMIT"
                    if metadata:
                        out["robots"], _ = response_record(origin=site["origin"], status="redirect_limit", http_status=response.http_status,
                                                          headers=response.headers, body=b"", truncated=False, now_ms=int(time.time() * 1000))
                    return out
                redirect = canonical_map_url(urljoin(out["final_url"], response.headers["location"]), self.policy)
                self.sites.require(site_id, params["sites_manifest_sha256"], redirect, metadata=metadata)
                out.update(status="redirect", redirect_url=str(redirect))
                return out
            if metadata and response.status != "cancelled" and (response.http_status in (401, 403, 429) or
                    not (response.status == "budget_exhausted" and response.error_code == "E_BYTE_BUDGET")):
                record, metadata_retry = response_record(origin=site["origin"], status=response.status, http_status=response.http_status,
                                                         headers=response.headers, body=response.body, truncated=response.truncated,
                                                         now_ms=int(time.time() * 1000), response_delay_ms=response.elapsed_ms)
                out["robots"] = record
                if metadata_retry:
                    out["retry_after_ms"] = max(out.get("retry_after_ms", 0), metadata_retry)
                if record["state"] != "deny_all":
                    out["status"] = "ok"
                    if record["state"] == "rules" and response.http_status == 200 and not response.truncated:
                        out.update(self._cache_metadata(response))
                else:
                    out["error_code"] = ("E_RATE_LIMITED" if response.http_status == 429 else
                                         "E_ROBOTS_UNREACHABLE" if response.status != "ok" else "E_ROBOTS_PARSE")
                return out
            if response.status != "ok" or response.http_status is None or not 200 <= response.http_status < 300:
                out["error_code"] = response.error_code or {
                    "timeout": "E_TIMEOUT", "blocked_by_policy": "E_NETWORK_POLICY", "invalid_url": "E_URL",
                    "http_error": "E_RATE_LIMITED" if response.http_status == 429 else "E_HTTP",
                }.get(response.status, "E_NETWORK")
                return out
            if metadata:
                out["error_code"] = response.error_code or "E_ROBOTS_UNREACHABLE"
                return out
            if response.http_status == 206 or response.truncated:
                out["error_code"] = "E_CONTENT_LIMIT"
                return out
            if response.content_type in HTML_TYPES:
                text, _ = decode_body(response.body, response.header_charset)
                if looks_like_challenge(response.http_status, text):
                    out["error_code"] = "E_CHALLENGE"
                    self.sites.cooldown_until[site_id] = time.monotonic() + 30 * 60
                    return out
            if len(response.headers.get("content-type", "")) > 1024:
                out["error_code"] = "E_UNSUPPORTED_CONTENT"
                return out
            if params["kind"] == "page":
                page_result = await asyncio.wait_for(asyncio.to_thread(
                    self._extract_crawl_page, response.body, out["final_url"], response.headers,
                    params.get("depth", 0), params.get("max_entries", 1000), params["max_content_bytes"]), remaining())
                remaining()
                if "error_code" in page_result:
                    out["error_code"] = page_result["error_code"]
                    if page_result["error_code"] == "E_CHALLENGE":
                        self.sites.cooldown_until[site_id] = time.monotonic() + 30 * 60
                    return out
                page = page_result["page"]
                page.update(final_url=out["final_url"], http_status=response.http_status, redirects=[])
                page["publication"] = apply_redistribution(page["publication"], site["review"])
                if page["status"] == "empty_content":
                    observation = empty_extraction_observation(page["publication"])
                    if observation is not None:
                        page["publication"] = {**page["publication"], "snippet_allowed": observation["snippet_allowed"], "snippet_text": ""}
                        out["publication_observation"] = observation
                out.update(status="ok", page=page, publication=page["publication"],
                           discovery=page_result["discovery"])
                if page["status"] == "ok" and response.http_status == 200:
                    out.update(self._cache_metadata(response))
                return out
            parsed = await asyncio.wait_for(asyncio.to_thread(discover, response.body, out["final_url"],
                                                              content_type=response.headers.get("content-type", response.content_type), kind=params["kind"],
                                                              depth=params.get("depth", 0), max_entries=params.get("max_entries", 1000),
                                                              max_input_bytes=params["max_content_bytes"]), remaining())
            remaining()
            if parsed["parse_status"] != "ok":
                out["error_code"] = "E_UNSUPPORTED_CONTENT" if parsed["parse_status"] == "unsupported" else "E_DISCOVERY_PARSE"
                return out
            self._restrict_discovery(parsed, publication_rules(response.headers))
            out.update(status="ok", discovery=parsed)
            return out
        except FetchAdmissionDenied as exc:
            out["error_code"] = exc.code
        except ConditionalMismatch:
            out["error_code"] = "E_CONDITIONAL_MISMATCH"
        except FetchStopped as exc:
            out["error_code"] = exc.code
        except PolicyError:
            out["error_code"] = "E_NETWORK_POLICY"
        except asyncio.TimeoutError:
            out["error_code"] = "E_TIMEOUT"
        finally:
            if acquired and lane is not None:
                lane.release()
        return out

    @staticmethod
    def _cache_metadata(response) -> dict:
        if response.status != "ok" or response.http_status != 200 or response.truncated:
            return {}
        # Freshness was captured immediately after fetch; parsing time must not
        # reset the response receipt clock or erase Age's resident time.
        result = {}
        try:
            result["http_cache"] = response_cache(response.headers, url=response.final_url,
                                                   profile=response.request_profile)
        except ValueError:
            pass  # The independent raw cache fields are already on the response.
        return result

    @staticmethod
    def _restrict_discovery(parsed: dict, publication: dict) -> None:
        if not publication["follow_allowed"]:
            parsed["diagnostics"]["nofollow"] += len(parsed["candidates"])
            parsed["candidates"] = []

    @staticmethod
    def _extract_crawl_page(body: bytes, url: str, headers: dict[str, str],
                            depth: int, max_entries: int, max_input_bytes: int) -> dict:
        """Bounded CPU work only. Every outbound request remains in _map_fetch."""
        from .discovery import discover

        content_type = headers.get("content-type", "")
        ctype = content_type.split(";", 1)[0].strip().lower()
        if not ctype and body[:512].lstrip().lower().startswith((b"<!doctype html", b"<html")):
            ctype = "text/html"
        charset_match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
        text, charset = decode_body(body, charset_match[1] if charset_match else None)
        parsed = {"parse_status": "ok", "document_url": url, "document_type": "text", "candidates": [],
                  "truncated": False,
                  "diagnostics": {"errors": [], "invalid": 0, "invalid_reasons": {}, "invalid_bases": 0,
                                  "duplicates": 0, "self_links": 0, "nofollow": 0, "ignored_links": 0,
                                  "examined": 0, "limits": []}}
        xml = ctype in ("text/xml", "application/xml") or ctype.endswith("+xml")
        if (ctype in HTML_TYPES or xml) and body.strip():
            parsed = discover(body, url, content_type=content_type or ctype, kind="auto", depth=depth,
                              max_entries=max_entries, max_input_bytes=max_input_bytes)
            if parsed["parse_status"] != "ok":
                return {"error_code": "E_UNSUPPORTED_CONTENT" if parsed["parse_status"] == "unsupported" else "E_DISCOVERY_PARSE"}
        if ctype in HTML_TYPES or (xml and parsed["document_type"] == "html"):
            if looks_like_challenge(200, text):
                return {"error_code": "E_CHALLENGE"}
            if not body.strip():
                parsed["document_type"] = "html"
            had_nul = "\x00" in text
            text = text.replace("\x00", "\ufffd")
            page = extract_html(text, url, headers)
            if had_nul:
                page["warnings"].append("nul_replaced")
            page.update(content_type="text/html", html=text)
        elif ctype in TEXT_TYPES or ctype.startswith("text/") or xml:
            # Sitemap/feed XML is validated in full by discover before retaining
            # its literal text. External entities are never expanded or loaded.
            page = extract_text(text, url, ctype, headers)
            page.update(content_type="text/plain", html="")
        else:
            return {"error_code": "E_UNSUPPORTED_CONTENT"}
        page.update(status="empty_content" if page["extract_status"] == "empty" else "ok", charset=charset)
        Worker._restrict_discovery(parsed, page["publication"])
        # Metadata links and page links share byte-preserving base resolution,
        # rel=nofollow and page-level publication admission.
        page["links"] = [item["url"] for item in parsed["candidates"]][:512]
        return {"page": page, "discovery": parsed}

    async def _fetch_extract(self, params: dict, budget: FetchBudget | None, conditional: dict | None = None,
                             redirect_conditional: dict | None = None,
                             control: DirectReadControl | None = None) -> dict:
        url = str(params.get("url", ""))
        timeout_s = max(0.001 if budget else 1.0, min(float(params.get("timeout_ms", 20000)) / 1000.0, 60.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        headers = self.fetcher.request_headers()
        if conditional is not None:
            failed = {"http_status": None, "final_url": url, "redirects": [],
                      "usage": {"external_requests": 0, "content_bytes": 0}}
            try:
                target = self.policy.prepare_url(url)
            except PolicyError as exc:
                return {**failed, "status": "blocked_by_policy", "error_code": "E_NETWORK_POLICY", "error": str(exc)}
            if conditional["url"] != str(target):
                return {**failed, "status": "error", "error_code": "E_CONDITIONAL_MISMATCH",
                        "error": "conditional URL differs from this read target"}
            if conditional["request_profile"] != request_profile(headers):
                conditional = None
            headers.update(conditional_headers(conditional))
        if redirect_conditional is not None:
            # URI normalization must not silently retarget a retained condition.
            try:
                initial = self.policy.prepare_url(url)
            except PolicyError as exc:
                return {"status": "blocked_by_policy", "error_code": "E_NETWORK_POLICY", "error": str(exc),
                        "http_status": None, "final_url": url, "redirects": [],
                        "usage": {"external_requests": 0, "content_bytes": 0}}
            if str(initial) != redirect_conditional["request_url"]:
                return {"status": "error", "error_code": "E_CONDITIONAL_MISMATCH",
                        "error": "redirect conditional initial URI differs from actual GET",
                        "http_status": None, "final_url": url, "redirects": [],
                        "usage": {"external_requests": 0, "content_bytes": 0}}
            if redirect_conditional["conditional"]["request_profile"] != request_profile(headers):
                # Configuration changes turn this same authorized attempt into a
                # full GET; they never trigger a second request or reuse a body.
                redirect_conditional = None
        async def fetch(target, **kwargs):
            if control is not None:
                # Fetcher catches explicit cancellation beside its live usage
                # counters. Keep that interruptible task separate from this RPC
                # and from CPU extraction dispatched through to_thread.
                return await asyncio.create_task(self.fetcher.fetch(target, control=control, **kwargs))
            return await self.fetcher.fetch(target, **kwargs)

        res = await fetch(url, timeout_s=timeout_s, headers=headers,
                          collect_redirect_cache=True, redirect_conditional=redirect_conditional,
                          **({"budget": budget} if budget else {}))
        used_requests, used_bytes = res.external_requests, res.content_bytes
        follows = 0
        while True:
            base = {"http_status": res.http_status, "final_url": res.final_url or url, "redirects": res.redirects,
                    "usage": {"external_requests": used_requests, "content_bytes": used_bytes}}
            if res.http_status is not None:
                base["http_freshness"] = response_freshness(res)
            if res.http_redirect_observations:
                if (len(res.http_redirect_observations) > MAX_OBSERVATIONS or
                        encoded_size(res.http_redirect_observations) > MAX_OBSERVATION_BYTES):
                    return {**base, "status": "http_error", "error_code": "E_HTTP_REVALIDATION",
                            "error": "redirect cache observation bound exceeded"}
                base["http_redirect_observations"] = res.http_redirect_observations
            if not follows and res.http_redirect_chain is not None:
                base["http_redirect_chain"] = res.http_redirect_chain
            if res.redirect_publication_observations:
                base["redirect_publication_observations"] = res.redirect_publication_observations
            if res.http_status is not None:
                observation = restriction_observation(res.headers)
                if observation is not None:
                    base["publication_observation"] = observation
                if res.http_status == 206:
                    base["publication_observation"] = incomplete_publication(publication_rules(res.headers))
            if res.http_status == 304:
                try:
                    selected = conditional
                    routed = redirect_conditional is not None
                    if routed:
                        selected = redirect_conditional["conditional"]
                        if (follows or res.http_redirect_chain is None or
                                res.redirects != redirect_conditional["redirects"] or
                                res.conditioned_binding_id != selected["binding_id"] or
                                res.final_url != selected["url"] or res.response_content_bytes):
                            raise ValueError("redirect 304 lacks an exact final-URI condition and complete HTTP prefix")
                    elif res.redirects or follows or res.content_bytes:
                        raise ValueError("direct 304 cannot follow a redirect or consume a body")
                    if selected is None or res.status != "not_modified" or res.truncated or res.body:
                        raise ValueError("304 response has no matching complete representation grant")
                    cache = response_cache(res.headers, url=res.final_url,
                                           profile=res.request_profile, conditional=selected)
                    result = {**base, "status": "not_modified", "binding_id": selected["binding_id"], "http_cache": cache}
                    if routed:
                        result["redirect_revalidation"] = {"version": REDIRECT_CONDITIONAL_VERSION,
                                                           "request_url": redirect_conditional["request_url"],
                                                           "final_url": selected["url"],
                                                           "binding_id": selected["binding_id"]}
                    return result
                except (ValueError, ConditionalMismatch) as exc:
                    return {**base, "status": "http_error", "error_code": "E_HTTP_REVALIDATION", "error": str(exc)}
            if res.status != "ok":
                if res.status == "http_error" and res.body:
                    text, _ = decode_body(res.body, res.header_charset)
                    if looks_like_challenge(res.http_status, text):
                        return {**base, "status": "challenge", "error": "site presented a bot check (not bypassed)"}
                return {**base, "status": res.status, "error": res.error or f"HTTP {res.http_status}",
                        "error_code": res.error_code}
            if res.http_status == 206:
                return {**base, "status": "http_error", "error_code": "E_CONTENT_LIMIT",
                        "error": "partial HTTP 206 response cannot provide a complete page"}
            ctype = res.content_type
            body_head = res.body[:512].lstrip().lower()
            if not ctype and (body_head.startswith(b"<!doctype html") or body_head.startswith(b"<html")):
                ctype = "text/html"
            warnings = ["download_truncated"] if res.truncated else []
            if ctype in HTML_TYPES:
                html, charset = decode_body(res.body, res.header_charset)
                if "\x00" in html:
                    html = html.replace("\x00", "\ufffd")
                    warnings.append("nul_replaced")
                if looks_like_challenge(res.http_status, html):
                    return {**base, "status": "challenge", "error": "site presented a bot check (not bypassed)"}
                target = self._soft_redirect(html, res.final_url or url)
                remaining = deadline - loop.time()
                if (target and follows < 2 and remaining > 1 and not res.truncated and
                        res.http_status is not None and 200 <= res.http_status < 300 and res.http_status != 206):
                    follows += 1
                    # A soft redirect still delivered a complete HTML response.
                    # Extract its bounded policy before replacing it with the
                    # target, but leave the target's own publication unchanged.
                    prior = await asyncio.to_thread(extract_html, html, res.final_url or url, res.headers)
                    observation = extraction_restriction_observation(
                        prior["publication"], "soft_redirect_restriction")
                    observed = res.redirect_publication_observations
                    if observation is not None:
                        observed = [*observed, {"url": res.final_url or url,
                                               "http_status": res.http_status, "publication": observation}]
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return {**base, "status": "timeout", "error": "request timed out",
                                "redirect_publication_observations": observed}
                    nxt = await fetch(target, timeout_s=remaining, collect_redirect_cache=True,
                                      **({"budget": budget} if budget else {}))
                    conditional = None
                    redirect_conditional = None
                    used_requests += nxt.external_requests
                    used_bytes += nxt.content_bytes
                    nxt.redirects = res.redirects + [target] + nxt.redirects
                    nxt.redirect_publication_observations = observed + nxt.redirect_publication_observations
                    nxt.http_redirect_observations = res.http_redirect_observations + nxt.http_redirect_observations
                    # A complete HTTP proof never spans an HTML/JavaScript redirect.
                    nxt.http_redirect_chain = None
                    if not nxt.final_url:
                        # No headers arrived from the next attempt. The last
                        # actual response may be an intermediate soft redirect,
                        # rather than the original input URL.
                        nxt.final_url = res.final_url or url
                    res = nxt
                    continue
                # CPU-bound: keep the event loop free for concurrent searches.
                out = await asyncio.to_thread(extract_html, html, res.final_url or url, res.headers)
                out["warnings"] = warnings + out["warnings"]
                if out["extract_status"] == "empty":
                    return {**base, **out, "status": "empty_content", "error": "no readable text",
                            "content_type": "text/html", "charset": charset, "html": html}
                return {**base, **out, "status": "ok", "content_type": "text/html", "charset": charset, "html": html,
                        **self._cache_metadata(res)}
            if ctype in TEXT_TYPES or ctype.startswith("text/"):
                text, charset = decode_body(res.body, res.header_charset)
                out = extract_text(text, res.final_url or url, ctype, res.headers)
                if out["extract_status"] == "empty":
                    return {**base, **out, "status": "empty_content", "error": "empty document",
                            "content_type": "text/plain", "charset": charset, "html": ""}
                return {**base, **out, "status": "ok", "content_type": "text/plain", "charset": charset,
                        "html": "", "warnings": warnings, **self._cache_metadata(res)}
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
    methods = {"health": worker.health, "search.multi": worker.search_multi, "fetch.extract": worker.fetch_extract,
               "fetch.cancel": worker.fetch_cancel, "map.fetch": worker.map_fetch, "fetch.profile": worker.fetch_profile,
               "fetch.extract_direct": worker.fetch_extract_direct, "fetch.cancel_direct": worker.fetch_cancel_direct}

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
    ap.add_argument("--sites", default=None, help="independent reviewed site manifest; defaults to an empty registry")
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
                        proxy=args.proxy, proxy_remote_dns=args.proxy_remote_dns, sites_path=args.sites)
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
