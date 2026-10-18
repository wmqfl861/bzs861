"""S0 source probe: run golden queries against each source from *your* machine.

Records availability, blocking, latency and hit counts, and keeps every raw response
as a parser fixture. Sends at most one request per source every --delay seconds.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import time
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .worker.fetch import Fetcher
from .worker.netguard import NetPolicy
from .worker.sources import SourceSet, load_manifest
from .worker.sources.review import require_review
from .worker.sources.base import SourceError


async def probe(sources_path: str, out_dir: str, queries_path: str, only: list[str] | None = None,
                per_source: int = 8, delay_s: float = 4.0, timeout_s: float = 12.0,
                allow_private: list[str] | None = None, proxy: str | None = None,
                test_hosts: dict[str, str] | None = None, proxy_remote_dns: bool = False,
                include_candidates: bool = False) -> dict[str, Any]:
    if include_candidates and not only:
        raise ValueError("--include-candidates requires an explicit nonempty --only source list")
    if type(per_source) is not int or not 1 <= per_source <= 40:
        raise ValueError("per_source must be in 1..40")
    if not math.isfinite(delay_s) or delay_s < 0 or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 60:
        raise ValueError("invalid probe delay or timeout")
    specs = load_manifest(sources_path)
    if only and any(i not in {s["id"] for s in specs} for i in only):
        raise ValueError("unknown probe source in --only")
    queries = json.loads(Path(queries_path).read_text(encoding="utf-8"))["queries"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= 1000:
        raise ValueError("probe queries must be a nonempty array with at most 1000 entries")
    seen = set()
    for q in queries:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", q["id"]) or q["id"] in seen:
            raise ValueError("probe query IDs must be unique safe identifiers")
        if not isinstance(q.get("query"), str) or not 1 <= len(q["query"]) <= 1000:
            raise ValueError("invalid probe query")
        seen.add(q["id"])
    out = Path(out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(NetPolicy(allow_private or [], test_mode=bool(test_hosts)), proxy=proxy, test_hosts=test_hosts,
                      proxy_remote_dns=proxy_remote_dns)
    await fetcher.start()
    rows: list[dict[str, Any]] = []
    try:
        sources = SourceSet(specs, fetcher, include_candidates=include_candidates, candidate_ids=only)
        ids = [i for i in sources.adapters if not only or i in only]
        if only and any(i not in sources.adapters for i in only):
            raise ValueError("probe source is disabled or has no adapter; candidates require --include-candidates")
        if not include_candidates:
            for i in ids:
                require_review(sources.specs[i])
        # Shared service budgets also cover concurrently probed language adapters.
        locks: dict[str, asyncio.Lock] = {}
        last_request: dict[str, float] = {}
        used: dict[str, int] = {}
        stopped: set[str] = set()

        async def run_source(source_id: str) -> None:
            adapter = sources.adapters[source_id]
            spec = sources.specs[source_id]
            capability = spec.get("capability", "web")
            budget = spec["contract"]["limits"]["shared_budget_key"]
            lock = locks.setdefault(budget, asyncio.Lock())
            gap = max(delay_s, spec.get("min_interval_ms", 1000) / 1000.0)
            cap = spec.get("daily_cap", 500)
            picked = [q for q in queries if capability in q.get("good_for", ["web"])][:per_source] or queries[:per_source]
            for n, q in enumerate(picked):
                started = time.monotonic()
                row: dict[str, Any] = {"source": source_id, "query_id": q["id"], "query": q["query"]}
                res = None
                try:
                    async with sources.semaphores[budget]:
                        async with lock:
                            if budget in stopped or used.get(budget, 0) >= cap:
                                break
                            wait = gap - (time.monotonic() - last_request.get(budget, 0))
                            if wait > 0:
                                await asyncio.sleep(wait)
                            if budget in stopped:
                                break
                            if not include_candidates or spec["contract"]["review"]["status"] == "approved":
                                require_review(spec)
                            last_request[budget] = time.monotonic()
                            used[budget] = used.get(budget, 0) + 1
                        hits, res = await asyncio.wait_for(adapter.search(q["query"], 10, q.get("language"), timeout_s), timeout_s)
                    row.update(status="ok" if hits else "empty", hits=len(hits),
                               top=[{"title": h.title, "url": h.url} for h in hits[:3]])
                except SourceError as exc:
                    res = exc.result
                    row.update(status=exc.status, hits=0, error=str(exc)[:200])
                except Exception as exc:  # keep probing other sources
                    row.update(status="error", hits=0, error=f"{type(exc).__name__}: {exc}"[:200])
                row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                if res is not None:
                    row["http_status"] = res.http_status
                    if res.body:
                        raw = out / "raw" / source_id / f"{q['id']}.body.gz"
                        raw.parent.mkdir(parents=True, exist_ok=True)
                        raw.write_bytes(gzip.compress(res.body))
                        row["raw"] = str(raw.relative_to(out))
                rows.append(row)
                print(f"[{source_id}] {q['id']}: {row['status']} hits={row.get('hits', 0)} {row['elapsed_ms']}ms", flush=True)
                if row["status"] not in ("ok", "empty"):
                    stopped.add(budget)
                    print(f"[{source_id}] stopping this service budget after {row['status']}", flush=True)
                    break

        await asyncio.gather(*(run_source(i) for i in ids))
    finally:
        await fetcher.close()
    summary = summarize(rows)
    import platform

    import aiohttp

    from . import __version__
    env = {"bzs861": __version__, "python": platform.python_version(), "aiohttp": aiohttp.__version__,
           "platform": platform.platform(terse=True), "proxy": bool(proxy), "proxy_remote_dns": proxy_remote_dns,
           "delay_s": delay_s, "timeout_s": timeout_s, "include_candidates": include_candidates,
           "review_changed": False, "live_source_approval": False, "quota_scope": "this_probe_run"}
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "environment": env, "rows": rows, "summary": summary}
    (out / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "probe_report.md").write_text(markdown(summary), encoding="utf-8")
    return report


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[r["source"]].append(r)
    summary = {}
    for source, items in sorted(by.items()):
        counts = Counter(r["status"] for r in items)
        ok = [r for r in items if r["status"] == "ok"]
        summary[source] = {"requests": len(items), "statuses": dict(counts),
                           "ok_rate": round(len(ok) / len(items), 2) if items else 0.0,
                           "avg_hits_when_ok": round(sum(r["hits"] for r in ok) / len(ok), 1) if ok else 0.0,
                           "avg_ms": int(sum(r["elapsed_ms"] for r in items) / len(items)) if items else 0,
                           "verdict": "reachable" if len(ok) >= max(1, len(items) * 0.7) else
                                      ("blocked" if counts.get("blocked") or counts.get("rate_limited") else "needs_work")}
    return summary


def markdown(summary: dict[str, Any]) -> str:
    lines = ["# bzs861 source probe", "", "| source | requests | ok rate | avg hits | avg ms | statuses | verdict |",
             "|---|---:|---:|---:|---:|---|---|"]
    for source, s in summary.items():
        statuses = ", ".join(f"{k}:{v}" for k, v in sorted(s["statuses"].items()))
        lines.append(f"| {source} | {s['requests']} | {s['ok_rate']} | {s['avg_hits_when_ok']} | {s['avg_ms']} | {statuses} | {s['verdict']} |")
    lines += ["", "Verdicts: reachable = at least 70% of queries answered; this is availability evidence only, not approval. "
              "blocked = bot check / rate limit seen "
              "(probing of that service stopped early); needs_work = answered but not parsed, or failing. "
              "The configured quota limits this probe run; deployed daily usage is accounted for separately by searchd.",
              "", "Send back probe_report.json plus the raw/ folder (e.g. `tar czf probe-out.tgz probe-out`): "
              "the raw result pages become parser regression fixtures. They contain only the public result pages "
              "for the test queries, which may hint at your region."]
    return "\n".join(lines) + "\n"
