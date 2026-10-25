"""Explicit S0 probes with durable attempts, immutable evidence and offline replay inputs."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
import math
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import __version__
from .golden import load_golden, select_queries
from .probe_format import MAX_HITS, SCHEMA_VERSION, canonical_json, parser_identity, safe_response_metadata, source_metadata
from .probe_state import atomic_write, output_guard, read_body, read_report, record_body, write_report
from .worker.fetch import Fetcher
from .worker.netguard import NetPolicy
from .worker.sources import SourceSet, load_manifest
from .worker.sources.review import SourceReviewError, require_review
from .worker.sources.base import SourceError, SOURCE_RETRY_AFTER_LIMIT_MS


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _checkpoint(out: Path, report: dict) -> None:
    report["updated_at"] = _utc_now()
    report["rows"].sort(key=lambda row: row["item_id"])
    report["summary"] = summarize(report["rows"], report["plan"], report["sources"])
    write_report(out, report)
    atomic_write(out / "probe_report.md", markdown(report["summary"], report).encode("utf-8"))


def _not_started_reason(budget: dict, cap: int) -> str | None:
    if budget["stopped"]:
        return "service_stopped:" + budget["stop_reason"]
    if budget["cooldown_until_ms"] > _now_ms():
        return "service_cooldown"
    if budget["used"] >= cap:
        return "run_quota_exhausted"
    return None


def _mark_uncertain(report: dict, item: dict, row: dict, reason: str) -> None:
    row.update(attempt_state="uncertain", status="uncertain", error=reason, finished_at=_utc_now())
    item.update(state="uncertain", reason=reason)
    budget = report["budgets"][report["sources"][item["source"]]["budget_key"]]
    budget.update(stopped=True, stop_reason="uncertain_attempt")
    budget["cooldown_until_ms"] = max(budget["cooldown_until_ms"], _now_ms() + 5 * 60 * 1000)


def _recover_reservations(report: dict) -> None:
    items = {item["item_id"]: item for item in report["plan"]}
    for row in report["rows"]:
        if row["attempt_state"] == "reserved":
            _mark_uncertain(report, items[row["item_id"]], row, "interrupted_after_reservation")


def _resume_report(out: Path, identity: dict, options: dict, sources: dict, plan: list) -> dict:
    report = read_report(out)

    def need(condition, message):
        if not condition:
            raise ValueError("cannot resume probe: " + message)

    def integer(value, minimum=0):
        return type(value) is int and value >= minimum

    need(type(report.get("schema_version")) is int and report["schema_version"] == SCHEMA_VERSION,
         "unsupported report schema")
    need(isinstance(report.get("run_id"), str) and bool(re.fullmatch(r"[0-9a-f]{32}", report["run_id"])),
         "invalid run id")
    need(report.get("status") in ("running", "interrupted", "completed"), "invalid run status")
    for key in ("created_at", "updated_at"):
        value = report.get(key)
        need(isinstance(value, str) and value.endswith("Z"), "invalid run timestamp")
        try:
            dt.datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("cannot resume probe: invalid run timestamp") from exc
    # These fields bind exact input bytes, execution options, selection and parser
    # code/dependencies. A changed environment must create a new audit run.
    for key, expected in (("identity", identity), ("options", options), ("sources", sources)):
        need(canonical_json(report.get(key)) == canonical_json(expected), key + " mismatch")
    saved_plan = report.get("plan")
    need(isinstance(saved_plan, list) and len(saved_plan) == len(plan), "request plan mismatch")
    plan_keys = set(plan[0]) - {"state", "reason"} if plan else set()
    states = ("pending", "reserved", "completed", "uncertain", "skipped")
    for saved, expected in zip(saved_plan, plan):
        need(isinstance(saved, dict) and saved.keys() == expected.keys(), "invalid plan item")
        need(canonical_json({key: saved[key] for key in plan_keys}) ==
             canonical_json({key: expected[key] for key in plan_keys}), "request plan mismatch")
        need(isinstance(saved["state"], str) and saved["state"] in states, "invalid plan state")
        reason = saved["reason"]
        need((reason is None and saved["state"] in ("pending", "reserved", "completed")) or
             (isinstance(reason, str) and 0 < len(reason) <= 300 and saved["state"] in ("uncertain", "skipped")),
             "invalid plan reason")
    items = {item["item_id"]: item for item in saved_plan}
    rows = report.get("rows")
    need(isinstance(rows, list) and len(rows) <= len(items), "invalid attempt records")
    recorded = set()
    used = Counter()
    failures = set()
    for row in rows:
        need(isinstance(row, dict) and isinstance(row.get("item_id"), str) and row["item_id"] in items,
             "attempt has no plan item")
        item = items[row["item_id"]]
        need(row["item_id"] not in recorded, "duplicate attempt")
        recorded.add(row["item_id"])
        need(row.get("attempt_id") == "a" + item["item_id"][1:] and
             all(row.get(key) == item[key] for key in ("source", "query_id", "query")), "attempt identity mismatch")
        state = row.get("attempt_state")
        need(isinstance(state, str) and state in ("reserved", "completed", "uncertain") and
             item["state"] == state, "attempt state does not match its plan")
        status = row.get("status")
        need(isinstance(status, str) and bool(re.fullmatch(r"[a-z_]{1,40}", status)), "invalid attempt status")
        if state != "completed":
            need(status == state, "invalid unfinished attempt status")
        else:
            need(status not in ("reserved", "uncertain"), "invalid completed attempt status")
        need(integer(row.get("elapsed_ms")) and integer(row.get("queue_ms")), "invalid attempt timing")
        results = row.get("results")
        need(isinstance(results, list) and len(results) <= MAX_HITS and
             integer(row.get("hits")) and row["hits"] == len(results), "invalid recorded hit count")
        for hit in results:
            need(isinstance(hit, dict) and hit.keys() == {"url", "title", "snippet", "rank"} and
                 all(isinstance(hit[k], str) for k in ("url", "title", "snippet")) and
                 type(hit["rank"]) is int and hit["rank"] >= 1, "invalid recorded hit")
        need(row.get("top") == [{"title": hit["title"], "url": hit["url"]} for hit in results[:3]],
             "recorded hit preview mismatch")
        need(status != "ok" or bool(results), "successful attempt has no hits")
        need(status != "empty" or not results, "empty attempt has hits")
        if "response" in row:
            need(isinstance(row["response"], dict), "invalid response metadata")
            read_body(out, row["response"])
            need(row.get("raw") == row["response"].get("raw_path"), "response artifact alias mismatch")
        else:
            need("raw" not in row, "raw body has no response metadata")
        key = sources[item["source"]]["budget_key"]
        used[key] += 1
        if (state == "completed" and status not in ("ok", "empty")) or state == "uncertain":
            failures.add(key)
    for item in saved_plan:
        need((item["item_id"] in recorded) == (item["state"] in ("reserved", "completed", "uncertain")),
             "plan and attempt records disagree")
    budgets = report.get("budgets")
    keys = {source["budget_key"] for source in sources.values()}
    need(isinstance(budgets, dict) and budgets.keys() == keys, "invalid budget ledger")
    for key, budget in budgets.items():
        need(isinstance(budget, dict) and budget.keys() == {
             "used", "last_started_at_ms", "stopped", "stop_reason", "cooldown_until_ms"}, "invalid budget entry")
        need(integer(budget["used"]) and budget["used"] == used[key], "reservation count mismatch; no refunds allowed")
        cap = next(source["limits"]["request_quota"] for source in sources.values() if source["budget_key"] == key)
        need(budget["used"] <= cap, "reservation count exceeds the bound quota")
        need(integer(budget["last_started_at_ms"]) and integer(budget["cooldown_until_ms"]), "invalid budget timing")
        need((budget["used"] == 0) == (budget["last_started_at_ms"] == 0), "budget start time mismatch")
        need(type(budget["stopped"]) is bool and
             ((not budget["stopped"] and budget["stop_reason"] is None) or
              (budget["stopped"] and isinstance(budget["stop_reason"], str) and bool(budget["stop_reason"]))),
             "invalid budget stop state")
        need(key not in failures or budget["stopped"], "failed service lost its stop state")
    need(report["status"] != "completed" or all(item["state"] not in ("pending", "reserved") for item in saved_plan),
         "completed report contains unfinished plan items")
    resumes = report.get("resume_count", 0)
    need(integer(resumes), "invalid resume count")
    _recover_reservations(report)
    report.update(status="running", resumed_at=_utc_now(), resume_count=resumes + 1)
    return report


async def probe(sources_path: str, out_dir: str, queries_path: str, only: list[str] | None = None,
                per_source: int = 8, delay_s: float = 4.0, timeout_s: float = 12.0,
                allow_private: list[str] | None = None, proxy: str | None = None,
                test_hosts: dict[str, str] | None = None, proxy_remote_dns: bool = False,
                include_candidates: bool = False, resume: bool = False) -> dict[str, Any]:
    if include_candidates and not only:
        raise ValueError("--include-candidates requires an explicit nonempty --only source list")
    if type(per_source) is not int or not 1 <= per_source <= 40:
        raise ValueError("per_source must be in 1..40")
    if not math.isfinite(delay_s) or delay_s < 0 or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 60:
        raise ValueError("invalid probe delay or timeout")
    specs = load_manifest(sources_path)
    if only and any(i not in {s["id"] for s in specs} for i in only):
        raise ValueError("unknown probe source in --only")
    golden = load_golden(queries_path)
    fetcher = Fetcher(NetPolicy(allow_private or [], test_mode=bool(test_hosts)), proxy=proxy, test_hosts=test_hosts,
                      proxy_remote_dns=proxy_remote_dns)
    sources = SourceSet(specs, fetcher, include_candidates=include_candidates, candidate_ids=only)
    ids = list(dict.fromkeys(only)) if only else list(sources.adapters)
    if any(i not in sources.adapters for i in ids):
        raise ValueError("probe source is disabled or has no adapter; candidates require --include-candidates")
    if not include_candidates:
        for i in ids:
            require_review(sources.specs[i])
    options = {"source_ids": ids, "per_source": per_source, "delay_s": float(delay_s), "timeout_s": float(timeout_s),
               "include_candidates": include_candidates, "test_hosts": dict(sorted((test_hosts or {}).items())),
               "allow_private": sorted(allow_private or []), "proxy": False, "proxy_remote_dns": False,
               "user_agent": fetcher.user_agent, "accept_language": fetcher.accept_language}
    identity = {"manifest_sha256": specs.digest, "queries_sha256": golden.digest,
                "golden_manifest_sha256": golden.manifest_digest,
                "options_sha256": hashlib.sha256(canonical_json(options)).hexdigest(), "parser": parser_identity()}
    source_info = {i: source_metadata(sources.specs[i]) for i in ids}
    plan = []
    for i in ids:
        for query in select_queries(golden.queries, sources.specs[i]["capability"], per_source):
            plan.append({"item_id": f"p{len(plan) + 1:06d}", "source": i, "query_id": query["id"],
                         "query": query["query"], "language": query.get("language"),
                         "category": query.get("category", "general"), "split": query.get("split", "evaluation"),
                         "state": "pending", "reason": None})
    out = Path(out_dir)
    with output_guard(out, resume):
        if resume:
            report = _resume_report(out, identity, options, source_info, plan)
        else:
            now = _utc_now()
            report = {"schema_version": SCHEMA_VERSION, "run_id": uuid.uuid4().hex, "status": "running",
                      "created_at": now, "updated_at": now, "identity": identity, "options": options,
                      "sources": source_info, "plan": plan, "rows": [],
                      "budgets": {s["budget_key"]: {"used": 0, "last_started_at_ms": 0,
                                   "stopped": False, "stop_reason": None, "cooldown_until_ms": 0}
                                  for s in source_info.values()},
                      "environment": {"bzs861": __version__, "proxy": False, "proxy_remote_dns": False,
                          "include_candidates": include_candidates, "review_changed": False,
                          "live_source_approval": False, "quota_scope": "this_probe_run",
                          "quota_unit": "reserved_source_query_attempts", "test_fixture": bool(test_hosts),
                          "golden_version": golden.version, "golden_frozen": golden.frozen}}
        _checkpoint(out, report)
        locks = {key: asyncio.Lock() for key in report["budgets"]}

        async def run_source(source_id: str) -> None:
            spec, adapter = sources.specs[source_id], sources.adapters[source_id]
            key = spec["contract"]["limits"]["shared_budget_key"]
            budget = report["budgets"][key]
            gap_ms = int(max(delay_s * 1000, spec.get("min_interval_ms", 1000)))
            cap = spec.get("daily_cap", 500)
            for item in (p for p in report["plan"] if p["source"] == source_id):
                if item["state"] != "pending":
                    continue
                queued_at = time.monotonic()
                async with sources.semaphores[key]:
                    async with locks[key]:
                        reason = _not_started_reason(budget, cap)
                        if reason:
                            item.update(state="skipped", reason=reason)
                            _checkpoint(out, report)
                            continue
                        wait_ms = gap_ms - (_now_ms() - budget["last_started_at_ms"])
                        if wait_ms > 0:
                            await asyncio.sleep(wait_ms / 1000.0)
                        reason = _not_started_reason(budget, cap)
                        if reason:
                            item.update(state="skipped", reason=reason)
                            _checkpoint(out, report)
                            continue
                        try:
                            if not include_candidates or spec["contract"]["review"]["status"] == "approved":
                                require_review(spec)
                        except SourceReviewError:
                            item.update(state="skipped", reason="review_required")
                            _checkpoint(out, report)
                            continue
                        # Persist reservation before entering adapter code. An interrupted
                        # reservation is uncertain, remains charged and is never retried.
                        row = {"item_id": item["item_id"], "attempt_id": "a" + item["item_id"][1:],
                               "source": source_id, "query_id": item["query_id"], "query": item["query"],
                               "attempt_state": "reserved", "status": "reserved", "hits": 0,
                               "top": [], "results": [], "started_at": _utc_now(),
                               "queue_ms": int((time.monotonic() - queued_at) * 1000), "elapsed_ms": 0}
                        item.update(state="reserved", reason=None)
                        budget["used"] += 1
                        budget["last_started_at_ms"] = _now_ms()
                        report["rows"].append(row)
                        _checkpoint(out, report)
                    started = time.monotonic()
                    response = None
                    retry_ms = 0
                    try:
                        hits, response = await asyncio.wait_for(
                            adapter.search(item["query"], MAX_HITS, item["language"], timeout_s), timeout_s)
                        results = [hit.as_dict() for hit in hits[:MAX_HITS]]
                        row.update(status="ok" if results else "empty", hits=len(results), results=results,
                                   top=[{"title": hit["title"], "url": hit["url"]} for hit in results[:3]])
                    except SourceError as exc:
                        response, retry_ms = exc.result, exc.retry_after_ms
                        row.update(status=exc.status, error=str(exc)[:300], retry_after_ms=retry_ms)
                        if exc.retry_after_clamped:
                            row.update(retry_after_clamped=True, retry_after_limit_ms=SOURCE_RETRY_AFTER_LIMIT_MS)
                    except asyncio.TimeoutError:
                        row.update(status="timeout", error="source timed out")
                    except asyncio.CancelledError:
                        _mark_uncertain(report, item, row, "interrupted_after_reservation")
                        _checkpoint(out, report)
                        raise
                    except Exception as exc:
                        row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
                    row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                    if response is not None:
                        metadata = safe_response_metadata(response)
                        metadata.update(record_body(out, row["attempt_id"], response.body))
                        row["response"] = metadata
                        row["http_status"] = response.http_status
                        if "raw_path" in metadata:
                            row["raw"] = metadata["raw_path"]
                    row.update(attempt_state="completed", finished_at=_utc_now())
                    item.update(state="completed", reason=None)
                    if row["status"] not in ("ok", "empty"):
                        budget.update(stopped=True, stop_reason=row["status"])
                        cooldown = spec.get("cooldown_error_ms", 5 * 60 * 1000)
                        if row["status"] in ("blocked", "challenge"):
                            cooldown = spec.get("cooldown_blocked_ms", 30 * 60 * 1000)
                        elif row["status"] == "rate_limited":
                            cooldown = min(SOURCE_RETRY_AFTER_LIMIT_MS,
                                           max(spec.get("cooldown_rate_ms", 10 * 60 * 1000), retry_ms))
                        budget["cooldown_until_ms"] = max(budget["cooldown_until_ms"], _now_ms() + cooldown)
                    # The response and stop decision are durable before another queued
                    # adapter in the same service obtains its concurrency slot.
                    _checkpoint(out, report)
                    print(f"[{source_id}] {item['query_id']}: {row['status']} hits={row['hits']} {row['elapsed_ms']}ms", flush=True)

        tasks = []
        try:
            await fetcher.start()
            tasks = [asyncio.create_task(run_source(i)) for i in ids]
            await asyncio.gather(*tasks)
            report["status"] = "completed"
            _checkpoint(out, report)
        except BaseException:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            _recover_reservations(report)
            report["status"] = "interrupted"
            _checkpoint(out, report)
            raise
        finally:
            await fetcher.close()
        return report


def summarize(rows: list[dict[str, Any]], plan: list[dict] | None = None,
              sources: dict | None = None) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[r["source"]].append(r)
    planned: dict[str, list[dict]] = defaultdict(list)
    for item in plan or []:
        planned[item["source"]].append(item)
    summary = {}
    for source in sorted(set(by) | set(planned) | set(sources or {})):
        items = by[source]
        counts = Counter(r["status"] for r in items)
        ok = [r for r in items if r["status"] == "ok"]
        answered = [r for r in items if r["status"] in ("ok", "empty")]
        completed = [r for r in items if r.get("attempt_state", "completed") == "completed"]
        reasons = Counter(p["reason"] for p in planned[source] if p["state"] == "skipped")
        uncertain = Counter(p["reason"] for p in planned[source] if p["state"] == "uncertain")
        states = Counter(p["state"] for p in planned[source])
        # A service's later block/backoff cannot disappear behind earlier successes.
        if counts.get("blocked") or counts.get("challenge") or counts.get("rate_limited"):
            verdict = "blocked"
        elif counts.get("uncertain") or counts.get("reserved") or states.get("pending"):
            verdict = "needs_work"
        elif len(answered) >= max(1, len(items) * 0.7):
            verdict = "reachable"
        else:
            verdict = "needs_work"
        summary[source] = {"requests": len(items), "statuses": dict(counts),
                           "planned": len(planned[source]) if plan is not None else len(items),
                           "completed": len(completed), "plan_states": dict(states),
                           "not_started_reasons": dict(reasons), "uncertain_reasons": dict(uncertain),
                           "reason": "no_eligible_queries" if plan is not None and not planned[source] else None,
                           "ok_rate": round(len(ok) / len(items), 2) if items else 0.0,
                           "answered_rate": round(len(answered) / len(items), 2) if items else 0.0,
                           "avg_hits_when_ok": round(sum(r["hits"] for r in ok) / len(ok), 1) if ok else 0.0,
                           "avg_ms": int(sum(r["elapsed_ms"] for r in completed) / len(completed)) if completed else 0,
                           "verdict": verdict}
    return summary


def markdown(summary: dict[str, Any], report: dict | None = None) -> str:
    lines = ["# bzs861 source probe", ""]
    if report is not None:
        lines += [f"Run `{report['run_id']}` — **{report['status']}**; schema {SCHEMA_VERSION}.",
                  f"Started {report['created_at']}; checkpoint {report['updated_at']}.", ""]
    lines += ["| source | planned | attempts | answered rate | avg hits | avg ms | statuses | verdict |",
              "|---|---:|---:|---:|---:|---:|---|---|"]
    for source, s in summary.items():
        statuses = ", ".join(f"{k}:{v}" for k, v in sorted(s["statuses"].items()))
        lines.append(f"| {source} | {s['planned']} | {s['requests']} | {s['answered_rate']} | "
                     f"{s['avg_hits_when_ok']} | {s['avg_ms']} | {statuses} | {s['verdict']} |")
    lines += ["", "`reachable` means at least 70% of reserved attempts produced valid parsed results or a valid empty result. "
              "A bot check or rate limit always yields `blocked`, even after earlier successes. "
              "`needs_work` includes failures, incomplete attempts and sources with no eligible queries. "
              "These are availability observations; they do not approve a source or measure ranking relevance.",
              "", "Attempts are reserved in the checkpoint before adapter execution. Quota, stop and cooldown state "
              "belong only to this run and survive `--resume`; completed and uncertain attempts are never automatically "
              "resent or refunded. Separate output directories and searchd have separate ledgers. "
              "Attempt counts are source-query reservations, not exact redirect/HTTP exchange counts. "
              "`response.external_requests` records available HTTP usage separately. Queue time is excluded from average elapsed time.",
              "", "## Unexecuted and uncertain work", ""]
    for source, s in summary.items():
        reasons = dict(s["not_started_reasons"])
        reasons.update(s["uncertain_reasons"])
        if s["reason"]:
            reasons[s["reason"]] = 1
        if s["plan_states"].get("pending"):
            reasons["pending"] = s["plan_states"]["pending"]
        if reasons:
            lines.append(f"- {source}: " + ", ".join(f"{key} ({count})" for key, count in sorted(reasons.items())))
    lines += ["", "`probe_report.json` contains the complete request plan, exact input and parser identities, "
              "safe response metadata and up to ten full parsed hits per attempt. Files under `raw/` are immutable "
              "gzip responses bound by decompressed length and SHA-256. Request credential headers are not recorded. "
              "Raw responses retain their original content for offline parser replay; review them before sharing."]
    return "\n".join(lines) + "\n"
