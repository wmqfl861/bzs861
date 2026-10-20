"""Prepare, freeze and score fixed candidate evidence without network or models.

Human judgments are supplied by an assessor, never inferred by this program.
The limited fixed-candidate gate is separate from the still-blocked FT-042 gate.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import platform

from .evaluation_format import (
    DROP_THRESHOLDS, MAX_SMALL_BYTES, MAX_SYSTEMS, METHOD, THRESHOLDS, VERSION,
    corpus_digest, digest, fields, file_bytes, gate_template, golden_projection_identity, hash_json, identifier, integer,
    label_bindings, load_package, need, read_json, selected_golden, text, validate_gate,
    validate_input, validate_labels, verify_golden_projection, write_directory,
)

METRICS = ("ndcg_at_10", "mrr_at_10", "precision_at_10")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_identity() -> dict:
    from . import __version__
    root = Path(__file__).resolve().parent
    names = ("evaluation.py", "evaluation_format.py", "golden.py", "probe_format.py", "probe_state.py", "replay.py")
    return {"files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names},
            "bzs_version": __version__, "python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": platform.system(), "architecture": platform.machine()}


def _probe_input(path: str, golden, selected: list[dict]) -> tuple[dict, str, dict]:
    """Import complete parser-verified source lists, never fuse or fetch them."""
    from .probe_format import MAX_HITS, SCHEMA_VERSION, canonical_json, parser_identity
    from .probe_state import read_report_with_digest
    from .replay import _identity, _response, _results
    from .worker.sources import ADAPTERS
    from .worker.sources.base import SourceError

    report_path = Path(path)
    need(report_path.name == "probe_report.json", "E_EVAL_SCHEMA", "expected probe_report.json")
    report, report_sha = read_report_with_digest(report_path.parent)
    integer(report.get("schema_version"), "probe schema", SCHEMA_VERSION, SCHEMA_VERSION)
    identity = report.get("identity")
    need(isinstance(identity, dict), "E_EVAL_IDENTITY", "probe identity is missing")
    need(identity.get("queries_sha256") == golden.digest and identity.get("golden_manifest_sha256") == golden.manifest_digest,
         "E_EVAL_IDENTITY", "probe and golden input identities differ")
    digest(identity.get("manifest_sha256"), "source manifest")
    recorded_parser = _identity(identity.get("parser"))
    need(recorded_parser == parser_identity(), "E_EVAL_IDENTITY", "probe parser identity changed; record an explicit new input")
    options, sources, plans, rows = (report.get(key) for key in ("options", "sources", "plan", "rows"))
    need(isinstance(options, dict) and identity.get("options_sha256") == hashlib.sha256(canonical_json(options)).hexdigest(),
         "E_EVAL_IDENTITY", "probe options identity mismatch")
    source_ids = options.get("source_ids")
    need(isinstance(source_ids, list) and 1 <= len(source_ids) <= MAX_SYSTEMS and
         all(isinstance(i, str) for i in source_ids) and len(source_ids) == len(set(source_ids)),
         "E_EVAL_SCHEMA", "invalid probe source order")
    need(isinstance(sources, dict) and sources.keys() == set(source_ids), "E_EVAL_IDENTITY", "probe registry does not match its plan")
    need(isinstance(plans, list) and len(plans) <= 8000 and isinstance(rows, list) and len(rows) <= 8000,
         "E_EVAL_LIMIT", "invalid probe plan/rows")
    queries = {q["id"]: q for q in golden.queries}
    chosen = {q["id"] for q in selected}
    plan_by_id, plan_by_pair = {}, {}
    for item in plans:
        need(isinstance(item, dict), "E_EVAL_SCHEMA", "invalid probe plan item")
        qid, source = item.get("query_id"), item.get("source")
        need(isinstance(qid, str) and qid in queries, "E_EVAL_IDENTITY", "probe has an unknown query")
        need(queries[qid]["split"] != "holdout" and not qid.lower().startswith("h"),
             "E_EVAL_HOLDOUT", "probe contains reserved holdout work")
        need(source in source_ids and item.get("query") == queries[qid]["query"],
             "E_EVAL_IDENTITY", "probe query/source differs from the frozen plan")
        for name in ("category", "split", "language"):
            need(item.get(name) == queries[qid].get(name), "E_EVAL_IDENTITY", "probe query metadata changed")
        ident = identifier(item.get("item_id"), "probe item ID")
        need(ident not in plan_by_id and (source, qid) not in plan_by_pair,
             "E_EVAL_DUPLICATE", "duplicate probe work")
        need(item.get("state") in ("pending", "reserved", "completed", "uncertain", "skipped"),
             "E_EVAL_SCHEMA", "invalid probe plan state")
        plan_by_id[ident] = item
        plan_by_pair[(source, qid)] = item
    by_item = {}
    parsers = {}
    for source in source_ids:
        identifier(source, "source ID")
        spec = sources[source]
        need(isinstance(spec, dict) and spec.get("id") == source, "E_EVAL_IDENTITY", "probe source identity mismatch")
        adapter, params = spec.get("adapter_id"), spec.get("params")
        need(isinstance(adapter, str) and adapter in ADAPTERS and isinstance(params, dict) and
             not params.keys() - {"adapter", "base_url", "lang", "region", "site", "site_url"},
             "E_EVAL_SCHEMA", "invalid recorded parser configuration")
        for key, value in params.items():
            text(value, "parser parameter", 8192)
        need(params.get("adapter", adapter) == adapter, "E_EVAL_IDENTITY", "probe adapter mismatch")
        parsers[source] = ADAPTERS[adapter]({"id": source, "params": params}, None)
    documents, pool_ids, runs = {}, {q["id"]: [] for q in selected}, []
    for row in rows:
        need(isinstance(row, dict) and isinstance(row.get("item_id"), str) and row["item_id"] in plan_by_id,
             "E_EVAL_IDENTITY", "probe row has no bound plan item")
        item = plan_by_id[row["item_id"]]
        need(row["item_id"] not in by_item, "E_EVAL_DUPLICATE", "duplicate probe attempt")
        need(all(row.get(key) == item[key] for key in ("source", "query_id", "query")) and
             row.get("attempt_state") == item["state"], "E_EVAL_IDENTITY", "probe row disagrees with its plan")
        state = row.get("attempt_state")
        need(state in ("reserved", "completed", "uncertain"), "E_EVAL_SCHEMA", "invalid recorded attempt state")
        by_item[row["item_id"]] = row
        if item["query_id"] not in chosen or state != "completed":
            continue
        status = text(row.get("status"), "probe status", 80)
        expected = _results(row.get("results"))
        integer(row.get("hits"), "hit count", 0, MAX_HITS)
        need(row["hits"] == len(expected) and ((status == "ok" and bool(expected)) or (status != "ok" and not expected)),
             "E_EVAL_SCHEMA", "probe status/hit count mismatch")
        need(row.get("response") is not None or status in ("timeout", "network_error", "error"),
             "E_EVAL_EVIDENCE", "completed parser result is missing its response")
        if row.get("response") is not None:
            response = _response(report_path.parent, row["response"], row)
            actual, actual_status = [], "error"
            try:
                parser = parsers[item["source"]]
                parser.check(response)
                actual = [hit.as_dict() for hit in parser.parse(response, item["query"], MAX_HITS)[:MAX_HITS]]
                actual_status = "ok" if actual else "empty"
            except SourceError as exc:
                actual_status = exc.status
            except Exception:
                pass  # Do not expose third-party exception text or source response bytes.
            need(actual_status == status and actual == expected, "E_EVAL_REPLAY_MISMATCH", "recorded parser result no longer reproduces")
        ids = []
        prior_rank, seen_urls = 0, set()
        for hit in expected:
            need(hit["rank"] == prior_rank + 1 and hit["url"] not in seen_urls,
                 "E_EVAL_DUPLICATE", "recorded source list has duplicate/unordered ranks or URLs")
            prior_rank = hit["rank"]
            seen_urls.add(hit["url"])
            evidence = {"url": hit["url"], "title": hit["title"], "text": hit["snippet"]}
            sha = hash_json(evidence)
            ident = "d_" + sha
            documents[ident] = {"document_id": ident, **evidence, "evidence_sha256": sha}
            ids.append(ident)
            if ident not in pool_ids[item["query_id"]]:
                pool_ids[item["query_id"]].append(ident)
        runs.append({"system_id": item["source"], "query_id": item["query_id"],
                     "status": status if status in ("ok", "empty") else "failed", "document_ids": ids,
                     "reason": None if status in ("ok", "empty") else status})
    for ident, item in plan_by_id.items():
        need((ident in by_item) == (item["state"] in ("reserved", "completed", "uncertain")),
             "E_EVAL_IDENTITY", "probe plan and attempt coverage disagree")
    present = {(r["system_id"], r["query_id"]) for r in runs}
    for source in source_ids:
        for query in selected:
            key = (source, query["id"])
            if key not in present:
                item = plan_by_pair.get(key)
                reason = "not_in_recorded_plan" if item is None else "probe_" + item["state"]
                runs.append({"system_id": source, "query_id": query["id"], "status": "unavailable", "document_ids": [], "reason": reason})
    environment = report.get("environment")
    need(isinstance(environment, dict) and type(environment.get("test_fixture")) is bool,
         "E_EVAL_SCHEMA", "probe must declare its fixture provenance")
    value = {"schema_version": VERSION, "kind": "bzs-fixed-candidate-input", "queries_sha256": golden.digest,
             "golden_manifest_sha256": golden.manifest_digest, "origin": "synthetic" if environment["test_fixture"] else "recorded",
             "ranking_identity": {"name": "recorded_source_order", "code_sha256": recorded_parser["code_sha256"],
                                  "config_sha256": identity["manifest_sha256"]},
             "documents": list(documents.values()), "pools": [{"query_id": q["id"], "document_ids": pool_ids[q["id"]]} for q in selected],
             "runs": runs}
    provenance = {"kind": "probe_source_top10", "report_sha256": report_sha, "parser": recorded_parser,
                  "document_identity": "exact_url_title_snippet_representation", "body_labels": False,
                  "source_approval": False, "source_fusion": False}
    return value, report_sha, provenance


def prepare(queries_path: str, out_dir: str, *, candidates_path: str | None = None,
            probe_path: str | None = None, splits: list[str] | None = None) -> dict:
    need((candidates_path is None) != (probe_path is None), "E_EVAL_SCHEMA", "provide exactly one candidate input or probe report")
    need(splits is None or isinstance(splits, list), "E_EVAL_SCHEMA", "splits must be an explicit list")
    golden, selected = selected_golden(queries_path, splits if splits is not None else ["development", "regression"])
    if candidates_path is not None:
        value, input_sha = read_json(candidates_path)
        provenance = {"kind": "operator_supplied_fixed_candidates", "authenticity": "not_verified"}
    else:
        value, input_sha, provenance = _probe_input(probe_path, golden, selected)
    validate_input(value, golden, selected)
    selection = [{**q, "language": q.get("language")} for q in selected]
    pool = {"schema_version": VERSION, "kind": "bzs-evaluation-pool",
            "dataset": {"queries_sha256": golden.digest, "golden_manifest_sha256": golden.manifest_digest,
                        "frozen": golden.frozen, "version": golden.version},
            "selection": selection, "input_sha256": input_sha, "input": value, "provenance": provenance}
    pool_raw, method_raw = file_bytes(pool), file_bytes(METHOD)
    manifest = {"schema_version": VERSION, "kind": "bzs-evaluation-package",
                "pool_sha256": hashlib.sha256(pool_raw).hexdigest(), "method_sha256": hashlib.sha256(method_raw).hexdigest()}
    manifest_raw = file_bytes(manifest)
    bindings = {"package_sha256": hashlib.sha256(manifest_raw).hexdigest(), "pool_sha256": manifest["pool_sha256"],
                "method_sha256": manifest["method_sha256"], "corpus_sha256": corpus_digest(pool)}
    docs = {doc["document_id"]: doc for doc in value["documents"]}
    judgments = [{"query_id": p["query_id"], "document_id": doc, "evidence_sha256": docs[doc]["evidence_sha256"],
                  "grade": None, "origin": "unjudged", "assessor_id": None, "assessed_at": None, "note": ""}
                 for p in value["pools"] for doc in p["document_ids"]]
    labels = {"schema_version": VERSION, "kind": "bzs-relevance-judgments", "bindings": label_bindings(bindings), "judgments": judgments}
    gate = gate_template(bindings)
    observed = runtime_identity()
    gate["environment"]["software_sha256"] = hash_json(observed)
    summary = {"schema_version": VERSION, "kind": "bzs-evaluation-preparation", "status": "prepared",
               "quality_gate": "blocked", "reason": "human_labels_and_frozen_gate_required", "network_requests": 0,
               "query_count": len(selected), "document_count": len(docs), "judgment_count": len(judgments),
               "splits": dict(Counter(q["split"] for q in selected)), "holdout_included": False, "bindings": bindings}
    write_directory(out_dir, {"package.json": manifest_raw, "pool.json": pool_raw, "method.json": method_raw,
                             "labels.template.json": file_bytes(labels), "gate.template.json": file_bytes(gate),
                             "observed_environment.json": file_bytes(observed), "preparation.json": file_bytes(summary)})
    return summary


def freeze(package_dir: str, gate_path: str, out_dir: str, *, queries_path: str | None = None) -> dict:
    pool, _, _, bindings = load_package(package_dir)
    golden_verification = verify_golden_projection(pool, queries_path)
    gate, gate_sha = read_json(gate_path, MAX_SMALL_BYTES)
    missing = validate_gate(gate, bindings)
    if golden_verification["status"] != "verified":
        missing.append("original_golden_not_verified")
    runtime = runtime_identity()
    recorded = gate["environment"]["software_sha256"]
    need(recorded is None or recorded == hash_json(runtime), "E_EVAL_IDENTITY", "gate software identity differs from this evaluator environment")
    result = {"schema_version": VERSION, "kind": "bzs-evaluation-freeze", "bindings": bindings,
              "gate": gate, "gate_input_sha256": gate_sha, "gate_canonical_sha256": hash_json(gate),
              "runtime": runtime, "golden_verification": golden_verification,
              "frozen_at": utc_now(), "status": "blocked" if missing else "frozen",
              "blocked_reasons": missing, "network_requests": 0,
              "holdout_supported": False, "review_authenticity": "operator_assertion_not_authenticated"}
    write_directory(out_dir, {"freeze.json": file_bytes(result)})
    return result


def score_ranking(grades: dict[str, int], ranked: list[str], *, cutoff: int = 10) -> dict:
    """A complete, already-judged pool. Missing labels are an input error, never zero."""
    integer(cutoff, "metric cutoff", 1, 1000)
    need(isinstance(grades, dict) and isinstance(ranked, list), "E_EVAL_SCHEMA", "invalid metric arguments")
    for grade in grades.values():
        integer(grade, "relevance grade", 0, 3)
    need(all(isinstance(doc, str) and doc in grades for doc in ranked), "E_EVAL_MISSING_JUDGMENT", "ranked result lacks a pool judgment")
    need(len(ranked) == len(set(ranked)), "E_EVAL_DUPLICATE", "duplicate ranked document")

    def dcg(values):
        return math.fsum((2 ** value - 1) / math.log2(rank + 1) for rank, value in enumerate(values, 1))

    top = [grades[doc] for doc in ranked[:cutoff]]
    ideal = dcg(sorted(grades.values(), reverse=True)[:cutoff])
    rr = next((1.0 / rank for rank, value in enumerate(top, 1) if value >= 1), 0.0)
    return {"ndcg": dcg(top) / ideal if ideal else 0.0, "precision": sum(value >= 1 for value in top) / cutoff,
            "reciprocal_rank": rr, "no_relevant": not any(value >= 1 for value in grades.values())}


def _aggregate(rows: list[dict]) -> dict:
    complete = [row for row in rows if row["metrics"] is not None]
    answered = sum(row["run_status"] == "ok" for row in rows)
    empty = sum(row["run_status"] == "empty" for row in rows)
    executed = answered + empty
    macro = {key: math.fsum(row["metrics"][key] for row in complete) / len(complete) if complete else None for key in METRICS}
    return {"planned_queries": len(rows), "executed_queries": executed, "answered_queries": answered,
            "empty_queries": empty, "missing_or_failed_queries": len(rows) - executed,
            "scored_queries": len(complete), "query_coverage": executed / len(rows) if rows else None,
            "answer_coverage": answered / len(rows) if rows else None, "abstention_rate": empty / len(rows) if rows else None,
            "no_relevant_queries": sum(row["no_relevant"] is True for row in rows),
            "metrics": macro if len(complete) == len(rows) and rows else {key: None for key in METRICS},
            "diagnostic_scored_subset": {"denominator": len(complete), "metrics": macro}}


def _score_systems(pool: dict, labels: dict) -> tuple[list[dict], dict]:
    data = pool["input"]
    pools = {row["query_id"]: row["document_ids"] for row in data["pools"]}
    recorded = {(run["system_id"], run["query_id"]): run for run in data["runs"]}
    expected = [(qid, doc) for qid, ids in pools.items() for doc in ids]
    missing = [(qid, doc) for qid, doc in expected if labels.get((qid, doc), {}).get("grade") is None]
    counts = Counter(labels[key]["origin"] if key in labels else "missing" for key in expected)
    labeled = len(expected) - len(missing)
    coverage = {"expected_judgments": len(expected), "judged": labeled,
                "human": counts["human"], "synthetic": counts["synthetic"], "unjudged": counts["unjudged"],
                "absent": counts["missing"], "judgment_coverage": labeled / len(expected) if expected else None,
                "missing_judgments": [{"query_id": qid, "document_id": doc} for qid, doc in missing],
                "assessor_authenticity": "declared_not_authenticated"}
    systems = []
    for system in dict.fromkeys(run["system_id"] for run in data["runs"]):
        rows = []
        for query in pool["selection"]:
            qid = query["id"]
            run = recorded.get((system, qid))
            status = run["status"] if run else "unavailable"
            grades = {doc: labels[(qid, doc)]["grade"] for doc in pools[qid]
                      if (qid, doc) in labels and labels[(qid, doc)]["grade"] is not None}
            missing_count = len(pools[qid]) - len(grades)
            metrics, no_relevant = None, None
            if status in ("ok", "empty") and not missing_count:
                scored = score_ranking(grades, run["document_ids"])
                metrics = dict(zip(METRICS, (scored["ndcg"], scored["reciprocal_rank"], scored["precision"])))
                no_relevant = scored["no_relevant"]
            rows.append({"query_id": qid, "split": query["split"], "category": query["category"],
                         "language": query["language"], "run_status": status,
                         "run_reason": run["reason"] if run else "missing_system_query_run",
                         "missing_judgments": missing_count, "metrics": metrics, "no_relevant": no_relevant})
        groups = []
        for split in dict.fromkeys(q["split"] for q in pool["selection"]):
            split_rows = [row for row in rows if row["split"] == split]
            groups.append({"split": split, "dimension": "all", "value": "all", **_aggregate(split_rows)})
            for dimension in ("category", "language"):
                for value in sorted({str(row[dimension]) if row[dimension] is not None else "unknown" for row in split_rows}):
                    chosen = [row for row in split_rows if (str(row[dimension]) if row[dimension] is not None else "unknown") == value]
                    groups.append({"split": split, "dimension": dimension, "value": value, **_aggregate(chosen)})
        systems.append({"system_id": system, "groups": groups, "queries": rows})
    return systems, coverage


def _baseline(path: str | None, expected_sha: str | None, bindings: dict, labels_sha: str,
              runtime: dict, environment: dict) -> tuple[dict | None, list[str]]:
    if expected_sha is None:
        need(path is None, "E_EVAL_BASELINE", "baseline comparison was not frozen")
        return None, []
    if path is None:
        return None, ["missing_frozen_baseline_report"]
    value, sha = read_json(path)
    need(sha == expected_sha, "E_EVAL_IDENTITY", "baseline report SHA-256 mismatch")
    integer(value.get("schema_version"), "baseline version", VERSION, VERSION)
    need(value.get("kind") == "bzs-fixed-candidate-evaluation",
         "E_EVAL_BASELINE", "unsupported baseline report")
    need(value.get("runtime") == runtime and value.get("gate_environment") == environment,
         "E_EVAL_IDENTITY", "baseline evaluator or declared environment differs")
    need(value.get("labels_sha256") == labels_sha and isinstance(value.get("bindings"), dict) and
         label_bindings(value["bindings"]) == label_bindings(bindings),
         "E_EVAL_IDENTITY", "baseline method, corpus or labels changed")
    need(isinstance(value.get("systems"), list) and 1 <= len(value["systems"]) <= MAX_SYSTEMS,
         "E_EVAL_BASELINE", "baseline has no bounded systems")
    system_ids = set()
    for system in value["systems"]:
        need(isinstance(system, dict), "E_EVAL_BASELINE", "invalid baseline system")
        ident = identifier(system.get("system_id"), "baseline system ID")
        need(ident not in system_ids, "E_EVAL_BASELINE", "duplicate baseline system")
        system_ids.add(ident)
    return value, [] if value.get("status") == "passed" else ["baseline_quality_gate_not_passed"]


def run(package_dir: str, freeze_path: str, labels_path: str, out_dir: str, *, baseline_path: str | None = None,
        queries_path: str | None = None) -> dict:
    pool, _, _, bindings = load_package(package_dir)
    golden_verification = verify_golden_projection(pool, queries_path)
    frozen, freeze_sha = read_json(freeze_path, MAX_SMALL_BYTES)
    fields(frozen, {"schema_version", "kind", "bindings", "gate", "gate_input_sha256", "gate_canonical_sha256",
                    "runtime", "golden_verification", "frozen_at", "status", "blocked_reasons", "network_requests", "holdout_supported", "review_authenticity"}, "freeze")
    integer(frozen["schema_version"], "freeze version", VERSION, VERSION)
    need(frozen["kind"] == "bzs-evaluation-freeze" and frozen["bindings"] == bindings,
         "E_EVAL_IDENTITY", "freeze does not bind this exact package")
    need(frozen["gate_canonical_sha256"] == hash_json(frozen["gate"]), "E_EVAL_IDENTITY", "frozen gate content changed")
    digest(frozen["gate_input_sha256"], "gate input")
    need(type(frozen["network_requests"]) is int and frozen["network_requests"] == 0 and frozen["holdout_supported"] is False,
         "E_EVAL_SCHEMA", "invalid freeze scope")
    try:
        stamp = datetime.fromisoformat(text(frozen["frozen_at"], "freeze timestamp", 100).replace("Z", "+00:00"))
        need(stamp.utcoffset() is not None, "E_EVAL_SCHEMA", "freeze timestamp needs a timezone")
    except ValueError as exc:
        raise ValueError("E_EVAL_SCHEMA: invalid freeze timestamp") from exc
    runtime = runtime_identity()
    need(frozen["runtime"] == runtime, "E_EVAL_IDENTITY", "evaluator source/runtime changed after freeze")
    gate = frozen["gate"]
    blocked = validate_gate(gate, bindings)
    need(hash_json(frozen["golden_verification"]) in
         (hash_json({"status": "not_supplied"}), hash_json(golden_projection_identity(pool))),
         "E_EVAL_IDENTITY", "frozen golden verification identity changed")
    if frozen["golden_verification"]["status"] != "verified":
        blocked.append("original_golden_not_verified_at_freeze")
    if golden_verification["status"] != "verified":
        blocked.append("original_golden_not_verified")
    declared_software = gate["environment"]["software_sha256"]
    need(declared_software is None or declared_software == hash_json(runtime), "E_EVAL_IDENTITY", "frozen software declaration changed")
    labels_value, labels_sha = read_json(labels_path)
    labels = validate_labels(labels_value, pool, bindings)
    systems, coverage = _score_systems(pool, labels)
    if not pool["dataset"]["frozen"]:
        blocked.append("unfrozen_query_dataset")
    if pool["input"]["origin"] != "recorded":
        blocked.append("synthetic_candidate_evidence")
    if coverage["expected_judgments"] == 0:
        blocked.append("no_judged_candidate_universe")
    if coverage["judged"] != coverage["expected_judgments"]:
        blocked.append("missing_judgments")
    if coverage["human"] != coverage["expected_judgments"] or coverage["human"] == 0:
        blocked.append("human_judgments_required")
    if not any(row["grade"] is not None and row["grade"] > 0 for row in labels.values()):
        blocked.append("no_positive_relevance_judgments")
    for key in ("code_sha256", "config_sha256"):
        if pool["input"]["ranking_identity"][key] is None:
            blocked.append("missing_ranking_" + key)
    baseline, baseline_missing = _baseline(baseline_path, gate["non_regression"]["baseline_report_sha256"],
                                            bindings, labels_sha, runtime, gate["environment"])
    blocked.extend(baseline_missing)
    baseline_groups = {}
    if baseline is not None:
        need({s.get("system_id") for s in baseline["systems"]} == {s["system_id"] for s in systems},
             "E_EVAL_IDENTITY", "baseline system set differs")
        for system in baseline["systems"]:
            need(isinstance(system.get("groups"), list), "E_EVAL_BASELINE", "invalid baseline groups")
            for group in system["groups"]:
                need(isinstance(group, dict), "E_EVAL_BASELINE", "invalid baseline group")
                if group.get("dimension") == "all":
                    key = (system["system_id"], group.get("split"))
                    need(key not in baseline_groups, "E_EVAL_BASELINE", "duplicate baseline group")
                    baseline_groups[key] = group
    checks = []

    def check(name: str, measured, threshold, *, maximum: bool, system: str, split: str):
        if measured is None or threshold is None:
            state = "blocked"
        else:
            need(type(measured) in (int, float) and math.isfinite(measured), "E_EVAL_BASELINE", "invalid metric value")
            state = "passed" if (measured <= threshold if maximum else measured >= threshold) else "failed"
        checks.append({"system_id": system, "split": split, "check": name, "observed": measured,
                       "threshold": threshold, "status": state})

    for system in systems:
        for group in system["groups"]:
            if group["dimension"] != "all":
                continue
            sid, split = system["system_id"], group["split"]
            if group["missing_or_failed_queries"]:
                blocked.append("incomplete_query_execution")
            if not group["answered_queries"]:
                blocked.append("no_answered_queries")
            measured = {**group["metrics"], "judgment_coverage": coverage["judgment_coverage"],
                        "query_coverage": group["query_coverage"], "answer_coverage": group["answer_coverage"],
                        "abstention_rate": group["abstention_rate"]}
            for key in THRESHOLDS:
                check(key, measured[key.rsplit("_", 1)[0]], gate["thresholds"][key], maximum=key.endswith("_max"), system=sid, split=split)
            if baseline is not None:
                prior = baseline_groups.get((sid, split))
                need(prior is not None and isinstance(prior.get("metrics"), dict), "E_EVAL_BASELINE", "missing baseline split")
                for key in DROP_THRESHOLDS:
                    metric = key.removesuffix("_max_drop")
                    before, after = prior["metrics"].get(metric), group["metrics"][metric]
                    if before is not None:
                        need(type(before) in (int, float) and math.isfinite(before) and 0 <= before <= 1,
                             "E_EVAL_BASELINE", "invalid baseline metric")
                    delta = before - after if before is not None and after is not None else None
                    check(key, delta, gate["non_regression"][key], maximum=True, system=sid, split=split)
    if any(item["status"] == "blocked" for item in checks):
        blocked.append("unevaluated_gate_checks")
    failed = any(item["status"] == "failed" for item in checks)
    status = "failed" if failed else "blocked" if blocked else "passed"
    result = {"schema_version": VERSION, "kind": "bzs-fixed-candidate-evaluation", "status": status,
              "created_at": utc_now(), "bindings": bindings, "freeze_sha256": freeze_sha,
              "labels_sha256": labels_sha, "runtime": runtime, "gate_environment": gate["environment"],
              "golden_verification": golden_verification,
              "ranking_identity": pool["input"]["ranking_identity"], "input_sha256": pool["input_sha256"],
              "scope": METHOD["scope"], "network_requests": 0, "model_calls": 0,
              "quality_gate": {"status": status, "blocked_reasons": sorted(set(blocked)), "checks": checks},
              "release_gate": {"status": "blocked", "reasons": ["holdout_evaluation_not_implemented", "source_ablation_not_implemented",
                                                                    "hard_constraint_and_citation_evidence_not_implemented", "independent_human_review_not_authenticated"]},
              "source_ablation": {"status": "not_implemented"}, "holdout": {"status": "not_evaluated", "included": False},
              "constraint_failures": None, "citation_failures": None, "coverage": coverage, "systems": systems,
              "baseline_report_sha256": gate["non_regression"]["baseline_report_sha256"]}
    markdown = ("# Fixed-candidate relevance evaluation\n\n"
                f"Evaluation status: **{status}**. Full FT-042/C-14 release gate: **blocked**.\n\n"
                f"Judged pairs: {coverage['judged']}/{coverage['expected_judgments']}; human declarations: {coverage['human']}; "
                f"synthetic: {coverage['synthetic']}. Network requests: 0.\n\n"
                "The JSON records each split, language/category group, missing label and gate check. "
                "Diagnostic subset means use only the explicitly reported denominator. Human identity and label truth are not authenticated. "
                "Source ablation, holdout evaluation, hard constraints and citation correctness remain unimplemented here.\n")
    write_directory(out_dir, {"evaluation_report.json": file_bytes(result), "evaluation_report.md": markdown.encode("utf-8")})
    return result


if __name__ == "__main__":
    import sys
    from .cli import main
    raise SystemExit(main(["evaluation", *sys.argv[1:]]))
