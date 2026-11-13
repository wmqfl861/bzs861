"""Adapter registry built from config/sources.json (the same manifest searchd reads)."""
from __future__ import annotations

import asyncio
import json
import hashlib
import time
from pathlib import Path
from typing import Any

from ..fetch import Fetcher
from .apis import Arxiv, GitHubRepos, HackerNews, Marginalia, StackExchange, Wikipedia
from .base import Adapter, SourceError, SOURCE_RETRY_AFTER_LIMIT_MS
from .review import SourceReviewError, require_review, validate_spec
from .engines import BaiduHtml, BingHtml, DuckDuckGoHtml, MojeekHtml, So360Html, SogouHtml

ADAPTERS: dict[str, type[Adapter]] = {
    "duckduckgo_html": DuckDuckGoHtml, "bing_html": BingHtml, "mojeek_html": MojeekHtml,
    "baidu_html": BaiduHtml, "sogou_html": SogouHtml, "so360_html": So360Html,
    "wikipedia_api": Wikipedia, "arxiv_api": Arxiv, "stackexchange_api": StackExchange,
    "hackernews_api": HackerNews, "github_api": GitHubRepos, "marginalia_api": Marginalia,
}


class ManifestSpecs(list):
    def __init__(self, items, digest: str):
        super().__init__(items)
        self.digest = digest


def load_manifest(path: str | Path) -> ManifestSpecs:
    raw = Path(path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("source manifest exceeds 1 MiB")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate manifest key: " + key)
            out[key] = value
        return out
    data = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported source manifest version")
    specs = data.get("sources")
    if not isinstance(specs, list) or len(specs) > 200:
        raise ValueError("sources must be an array with at most 200 entries")
    seen = set()
    for spec in specs:
        validate_spec(spec)
        if spec["id"] in seen:
            raise ValueError("duplicate source id: " + spec["id"])
        seen.add(spec["id"])
    return ManifestSpecs(specs, hashlib.sha256(raw).hexdigest())


class SourceSet:
    def __init__(self, specs: list[dict[str, Any]], fetcher: Fetcher, *,
                 include_candidates: bool = False, candidate_ids: list[str] | None = None):
        if include_candidates and not candidate_ids:
            raise ValueError("candidate probes require an explicit nonempty --only source list")
        self.manifest_digest = getattr(specs, "digest", "")
        self.adapters: dict[str, Adapter] = {}
        self.specs = {s["id"]: s for s in specs}
        self.include_candidates = include_candidates
        self.semaphores: dict[str, asyncio.Semaphore] = {}
        budget_limits = {}
        self.start_locks: dict[str, asyncio.Lock] = {}
        self.last_start: dict[str, float] = {}
        self.cooldown_until: dict[str, float] = {}
        for spec in specs:
            validate_spec(spec)
            limits = spec["contract"]["limits"]
            key = limits["shared_budget_key"]
            shape = (limits["max_concurrency"], limits["min_interval_ms"], limits["request_quota"])
            if key in budget_limits and budget_limits[key] != shape:
                raise ValueError("sources sharing a budget must share its limits")
            budget_limits[key] = shape
            self.semaphores.setdefault(key, asyncio.Semaphore(limits["max_concurrency"]))
            self.start_locks.setdefault(key, asyncio.Lock())
            impl = spec["params"]["adapter"]
            candidate = include_candidates and spec["id"] in candidate_ids
            if candidate:
                review = spec["contract"]["review"]
                if review["status"] == "approved":
                    require_review(spec)
                if review["status"] in ("rejected", "expired") or review["payment_scheme"] == "paid_or_trial" or review["automated_access"] in ("restricted", "denied"):
                    raise ValueError("candidate probe is not allowed by the source review")
            if spec["enabled"] or candidate:
                if impl not in ADAPTERS:
                    raise ValueError("source has no implemented adapter: " + spec["id"])
                self.adapters[spec["id"]] = ADAPTERS[impl](spec, fetcher)
        if include_candidates and any(i not in self.adapters for i in candidate_ids):
            raise ValueError("candidate list contains an unknown or unimplemented source")

    async def run_one(self, source_id: str, query: str, limit: int, language: str | None, timeout_s: float,
                      conditions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        adapter = self.adapters.get(source_id)
        if adapter is None:
            return {"status": "error", "error": "no adapter for source", "hits": [], "elapsed_ms": 0}
        # Only condition kinds this adapter truly translates upstream are passed
        # on; adapters without native support keep their unchanged request path.
        native_kinds = getattr(adapter, "native_filter_kinds", ())
        applied = [dict(condition) for condition in (conditions or []) if condition.get("kind") in native_kinds]
        applied = applied or None
        dispatched = False
        try:
            # Review validity is checked for every call, including after the lane wait.
            spec = self.specs[source_id]
            require_review(spec)
            key = spec["contract"]["limits"]["shared_budget_key"]
            semaphore = self.semaphores[key]
            async def execute():
                nonlocal dispatched
                async with semaphore:
                    async with self.start_locks[key]:
                        if self.cooldown_until.get(key, 0) > time.monotonic():
                            raise SourceError("skipped", "shared service is cooling down")
                        gap = spec.get("min_interval_ms", 1000) / 1000.0
                        wait = gap - (time.monotonic() - self.last_start.get(key, 0))
                        if wait > 0:
                            await asyncio.sleep(wait)
                        if self.cooldown_until.get(key, 0) > time.monotonic():
                            raise SourceError("skipped", "shared service is cooling down")
                        require_review(spec)
                        remaining = timeout_s - (time.monotonic() - started)
                        if remaining <= 0:
                            raise asyncio.TimeoutError()
                        self.last_start[key] = time.monotonic()
                    dispatched = True
                    try:
                        if applied is None:
                            # Unchanged adapters keep their exact old call shape.
                            return await adapter.search(query, limit, language, remaining)
                        return await adapter.search(query, limit, language, remaining, conditions=applied)
                    except SourceError as exc:
                        if exc.status in ("blocked", "challenge"):
                            cooldown = spec.get("cooldown_blocked_ms", 30 * 60 * 1000)
                        elif exc.status == "rate_limited":
                            cooldown = min(SOURCE_RETRY_AFTER_LIMIT_MS,
                                           max(spec.get("cooldown_rate_ms", 10 * 60 * 1000), exc.retry_after_ms))
                        else:
                            cooldown = spec.get("cooldown_error_ms", 5 * 60 * 1000)
                        # Set the worker fence before releasing its semaphore, so queued
                        # adapters from the same service cannot start after a block.
                        self.cooldown_until[key] = max(self.cooldown_until.get(key, 0), time.monotonic() + cooldown / 1000.0)
                        raise
            hits, res = await asyncio.wait_for(execute(), timeout_s)
            out = {"status": "ok" if hits else "empty", "hits": [h.as_dict() for h in hits], "http_status": res.http_status}
        except SourceReviewError as exc:
            out = {"status": "review_required", "error": str(exc)[:300], "hits": []}
        except SourceError as exc:
            out = {"status": exc.status, "error": str(exc)[:300], "hits": []}
            if exc.http_status is not None:
                out["http_status"] = exc.http_status
            if exc.retry_after_ms:
                out["retry_after_ms"] = exc.retry_after_ms
            if exc.retry_after_clamped:
                out["retry_after_clamped"] = True
                out["retry_after_limit_ms"] = SOURCE_RETRY_AFTER_LIMIT_MS
        except asyncio.TimeoutError:
            out = {"status": "timeout", "error": "source timed out", "hits": []}
        except Exception as exc:  # adapter bugs must not take the worker down
            out = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "hits": []}
        if conditions is not None:
            # Per-condition execution report. The core treats any condition this
            # report does not explicitly mark "native" as not executed.
            translated = {(condition["kind"], condition["value"]) for condition in (applied or [])}
            # Native execution is claimed only when the adapter call actually
            # started: cooldown, review refusal or a pre-dispatch timeout leave
            # the condition unexecuted, never natively satisfied.
            def execution_of(condition):
                if (condition["kind"], condition["value"]) not in translated:
                    return "unsupported"
                return "native" if dispatched else "not_executed"
            out["conditions"] = [{"kind": condition["kind"], "value": condition["value"],
                                  "execution": execution_of(condition)}
                                 for condition in conditions]
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out

    async def run_many(self, ids: list[str], query: str, limit: int, language: str | None, timeout_s: float,
                       conditions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        results = await asyncio.gather(*(self.run_one(i, query, limit, language, timeout_s, conditions)
                                         for i in ids))
        return dict(zip(ids, results))
