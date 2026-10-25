"""Predeclared comparisons of externally ranked, fixed source candidates.

This module groups provenance and subtracts already-scored metrics. It never
implements fusion, reconstructs a missing ranking, or authenticates a provider.
"""
from __future__ import annotations

from collections import Counter
import math

from .evaluation_format import MAX_PAIRS, MAX_SMALL_BYTES, MAX_SYSTEMS, digest, fields, file_bytes, hash_json, identifier, integer, need

SCHEMA_VERSION = 1
METRICS = ("ndcg_at_10", "mrr_at_10", "precision_at_10")
METHOD = {
    "version": "bzs-source-ablation-1",
    "ranking": "externally_supplied_no_python_fusion",
    "pairing": "predeclared_source_removal_same_code_parameters_environment",
    "idcg_scope": "entire_original_frozen_query_pool",
    "delta": "base_minus_ablation",
    "unknown_family": "provider_capability_logical_service_bucket_not_independence",
    "missing_source_or_external_run": "block_not_empty",
    "unjudged": "block_not_zero",
    "independent_evidence_count": "not_established",
}


def contribution_identity(source: dict) -> dict:
    """Describe contribution grouping only; this is not a ranking operation."""
    if source["index_family"] != "unknown":
        return {"kind": "index_family", "index_family": source["index_family"], "capability": source["capability"]}
    return {"kind": "service", "provider": source["provider"], "capability": source["capability"],
            "logical_service_id": source["logical_service_id"]}


def contribution_bucket(source: dict) -> str:
    # A structured hash avoids ambiguous concatenation when an identifier contains ':'.
    return "b_" + hash_json(contribution_identity(source))


def external_run_digest(pool: dict, system_id: str, source_ids: list[str]) -> str:
    """Bind complete selected-query outcomes, including explicit missing rows.

    Callers use this to write a manifest for an existing external output. The
    digest records missing data; it cannot make that data complete or authentic.
    """
    recorded = {row["query_id"]: row for row in pool["input"]["runs"] if row["system_id"] == system_id}
    return hash_json({"system_id": system_id, "source_ids": source_ids,
                      "query_runs": [{"query_id": q["id"], "run": recorded.get(q["id"])} for q in pool["selection"]]})


def _external(value: dict, source_ids: list[str], pool: dict) -> None:
    fields(value, {"system_id", "source_ids", "implementation", "code_sha256", "parameters_sha256",
                   "environment_sha256", "run_sha256"}, "external ranking")
    identifier(value["system_id"], "external system ID")
    need(value["source_ids"] == source_ids, "E_EVAL_ABLATION", "external ranking source set/order differs from its removal plan")
    need(value["implementation"] in ("cpp", "synthetic_fixture"), "E_EVAL_ABLATION", "unsupported external ranking implementation")
    need(pool["input"]["origin"] == "synthetic" or value["implementation"] == "cpp",
         "E_EVAL_ABLATION", "recorded production rankings must declare the C++ implementation")
    for key in ("code_sha256", "parameters_sha256", "environment_sha256", "run_sha256"):
        digest(value[key], key, nullable=True)
    need(value["run_sha256"] is None or value["run_sha256"] == external_run_digest(pool, value["system_id"], source_ids),
         "E_EVAL_IDENTITY", "external ranking digest does not bind its complete selected-query runs")


def validate_manifest(wrapper: dict, pool: dict) -> dict:
    fields(wrapper, {"manifest_sha256", "manifest"}, "source ablation binding")
    digest(wrapper["manifest_sha256"], "source ablation manifest")
    value = wrapper["manifest"]
    fields(value, {"schema_version", "kind", "input_sha256", "method", "sources", "base", "ablations"}, "source ablation manifest")
    need(len(file_bytes(value)) <= MAX_SMALL_BYTES, "E_EVAL_LIMIT", "embedded ablation manifest exceeds its byte limit")
    integer(value["schema_version"], "ablation schema version", SCHEMA_VERSION, SCHEMA_VERSION)
    need(value["kind"] == "bzs-source-ablation-manifest", "E_EVAL_SCHEMA", "unsupported ablation manifest")
    need(value["input_sha256"] == pool["input_sha256"], "E_EVAL_IDENTITY", "ablation manifest binds a different candidate input")
    need(hash_json(value["method"]) == hash_json(METHOD), "E_EVAL_METHOD", "unsupported or changed ablation method")
    sources = value["sources"]
    need(isinstance(sources, list) and 1 <= len(sources) <= MAX_SYSTEMS - 2, "E_EVAL_LIMIT", "invalid source identity collection")
    source_ids, source_systems, services = [], set(), {}
    for source in sources:
        fields(source, {"source_id", "system_id", "provider", "capability", "logical_service_id", "index_family",
                        "configuration_sha256"}, "ablation source")
        for key in ("source_id", "system_id", "provider", "capability", "logical_service_id", "index_family"):
            identifier(source[key], "source " + key)
        digest(source["configuration_sha256"], "source configuration", nullable=True)
        need(source["source_id"] not in source_ids and source["system_id"] not in source_systems,
             "E_EVAL_DUPLICATE", "source IDs and source system IDs must be unique")
        source_ids.append(source["source_id"])
        source_systems.add(source["system_id"])
        service = (source["provider"], source["capability"], source["logical_service_id"])
        need(service not in services or services[service] == source["index_family"],
             "E_EVAL_IDENTITY", "one logical service cannot declare conflicting index families")
        services[service] = source["index_family"]
    base = value["base"]
    _external(base, source_ids, pool)
    for input_key, external_key in (("code_sha256", "code_sha256"), ("config_sha256", "parameters_sha256")):
        declared = pool["input"]["ranking_identity"][input_key]
        need(declared is None or base[external_key] is None or declared == base[external_key],
             "E_EVAL_IDENTITY", "base ranking differs from candidate ranking identity")
    systems = set(source_systems)
    need(base["system_id"] not in systems, "E_EVAL_DUPLICATE", "base cannot reuse a source system")
    systems.add(base["system_id"])
    ablations = value["ablations"]
    need(isinstance(ablations, list) and 1 <= len(ablations) <= MAX_SYSTEMS - len(systems),
         "E_EVAL_LIMIT", "invalid ablation comparison collection")
    seen, plans = set(), set()
    by_source = {source["source_id"]: source for source in sources}
    for comparison in ablations:
        fields(comparison, {"ablation_id", "removal_kind", "removed_source_ids", "system"}, "ablation comparison")
        ident = identifier(comparison["ablation_id"], "ablation ID")
        need(ident not in seen, "E_EVAL_DUPLICATE", "duplicate ablation ID")
        seen.add(ident)
        removed = comparison["removed_source_ids"]
        need(isinstance(removed, list) and bool(removed) and all(isinstance(s, str) and s in by_source for s in removed),
             "E_EVAL_ABLATION", "invalid removed source set")
        need(removed == [s for s in source_ids if s in removed], "E_EVAL_ABLATION", "removed sources must be unique and in source order")
        kind = comparison["removal_kind"]
        need(kind in ("source", "contribution_bucket"), "E_EVAL_ABLATION", "unsupported source removal kind")
        if kind == "source":
            need(len(removed) == 1, "E_EVAL_ABLATION", "source ablation removes exactly one source")
        else:
            bucket = contribution_bucket(by_source[removed[0]])
            need(removed == [s for s in source_ids if contribution_bucket(by_source[s]) == bucket],
                 "E_EVAL_ABLATION", "bucket ablation must remove all and only members of one contribution bucket")
        plan = (kind, tuple(removed))
        need(plan not in plans, "E_EVAL_DUPLICATE", "duplicate removal plan")
        plans.add(plan)
        system = comparison["system"]
        _external(system, [s for s in source_ids if s not in removed], pool)
        need(system["system_id"] not in systems, "E_EVAL_DUPLICATE", "comparison systems must be distinct")
        systems.add(system["system_id"])
        need(system["implementation"] == base["implementation"], "E_EVAL_ABLATION", "paired ranking implementations differ")
        for key in ("code_sha256", "parameters_sha256", "environment_sha256"):
            need(base[key] is None or system[key] is None or base[key] == system[key],
                 "E_EVAL_ABLATION", "paired ranking code, parameters and environment must match")
    need({run["system_id"] for run in pool["input"]["runs"]} <= systems,
         "E_EVAL_ABLATION", "manifest must account for every supplied candidate system")
    need(len(systems) * len(pool["selection"]) <= MAX_PAIRS, "E_EVAL_LIMIT", "too many planned source/comparison query pairs")
    need(sum(len(run["document_ids"]) for run in pool["input"]["runs"] if run["system_id"] in source_systems) <= MAX_PAIRS,
         "E_EVAL_LIMIT", "too many candidate provenance memberships")
    # An externally supplied ranking may use only its surviving source evidence.
    # Incomplete source outcomes are unknown, not proof that a result is invalid.
    recorded = {(row["system_id"], row["query_id"]): row for row in pool["input"]["runs"]}
    for query in pool["selection"]:
        qid = query["id"]
        source_rows = {s["source_id"]: recorded.get((s["system_id"], qid)) for s in sources}
        for system in [base, *(c["system"] for c in ablations)]:
            active = [source_rows[s] for s in system["source_ids"]]
            if all(row is not None and row["status"] in ("ok", "empty") for row in active):
                allowed = {doc for row in active for doc in row["document_ids"]}
                run = recorded.get((system["system_id"], qid))
                need(run is None or set(run["document_ids"]) <= allowed,
                     "E_EVAL_ABLATION", "external ranking contains evidence absent from its surviving sources")
    return value


def readiness(pool: dict) -> list[str]:
    """Requirements knowable before labels; used at freeze as well as at run."""
    if "source_ablation" not in pool:
        return ["missing_ablation_manifest"]
    value = validate_manifest(pool["source_ablation"], pool)
    missing = []
    recorded = {(row["system_id"], row["query_id"]): row for row in pool["input"]["runs"]}
    for source in value["sources"]:
        if source["configuration_sha256"] is None:
            missing.append("missing_source_configuration_identity")
    for system in [value["base"], *(c["system"] for c in value["ablations"])]:
        for key in ("code_sha256", "parameters_sha256", "environment_sha256", "run_sha256"):
            if system[key] is None:
                missing.append("missing_external_" + key)
        for query in pool["selection"]:
            run = recorded.get((system["system_id"], query["id"]))
            if run is None:
                missing.append("missing_external_query_run")
            elif run["status"] in ("failed", "unavailable"):
                missing.append("incomplete_external_query_run")
    for candidate_pool in pool["input"]["pools"]:
        qid = candidate_pool["query_id"]
        members = set()
        for source in value["sources"]:
            run = recorded.get((source["system_id"], qid))
            if run is None or run["status"] not in ("ok", "empty"):
                missing.append("incomplete_source_query_run")
            if run is not None:
                members.update(run["document_ids"])
        if set(candidate_pool["document_ids"]) - members:
            missing.append("unattributed_pool_candidates")
    return sorted(set(missing))


def _contributions(pool: dict, value: dict, labels: dict) -> list[dict]:
    recorded = {(row["system_id"], row["query_id"]): row for row in pool["input"]["runs"]}
    sources = value["sources"]
    rows = []
    for candidate_pool in pool["input"]["pools"]:
        qid, ids = candidate_pool["query_id"], candidate_pool["document_ids"]
        membership = {doc: [] for doc in ids}
        outcomes = []
        for source in sources:
            run = recorded.get((source["system_id"], qid))
            status = run["status"] if run else "unavailable"
            for doc in run["document_ids"] if run else []:
                membership[doc].append(source["source_id"])
            outcomes.append({"source_id": source["source_id"], "status": status,
                             "reason": run["reason"] if run else "missing_source_query_run"})
        source_buckets = {source["source_id"]: contribution_bucket(source) for source in sources}
        bucket_membership = {doc: sorted({source_buckets[source] for source in members}) for doc, members in membership.items()}
        unresolved = [doc for doc, members in membership.items() if not members]
        complete = not unresolved and all(row["status"] in ("ok", "empty") for row in outcomes)
        judged = all(labels.get((qid, doc), {}).get("grade") is not None for doc in ids)

        def counts(key: str, members: dict) -> dict:
            returned = [doc for doc in ids if key in members[doc]]
            exclusive = [doc for doc in returned if members[doc] == [key]]
            return {"returned_documents": len(returned),
                    "exclusive_documents": len(exclusive) if complete else None,
                    "exclusive_relevant_documents": sum(labels[(qid, doc)]["grade"] > 0 for doc in exclusive) if complete and judged else None}

        rows.append({"query_id": qid, "pool_documents": len(ids), "attribution_complete": complete,
                     "unattributed_document_ids": unresolved, "source_outcomes": outcomes,
                     "documents": [{"document_id": doc, "source_ids": membership[doc],
                                    "contribution_buckets": bucket_membership[doc]} for doc in ids],
                     "sources": [{"source_id": source["source_id"], "contribution_bucket": source_buckets[source["source_id"]],
                                  **counts(source["source_id"], membership)} for source in sources],
                     "buckets": [{"bucket_id": bucket, **counts(bucket, bucket_membership)} for bucket in dict.fromkeys(source_buckets.values())]})
    return rows


def _groups(rows: list[dict]) -> list[dict]:
    result = []
    for split in dict.fromkeys(row["split"] for row in rows):
        split_rows = [row for row in rows if row["split"] == split]
        selections = [("all", "all", split_rows)]
        for dimension in ("category", "language"):
            for value in sorted({str(row[dimension]) if row[dimension] is not None else "unknown" for row in split_rows}):
                selections.append((dimension, value, [row for row in split_rows if (str(row[dimension]) if row[dimension] is not None else "unknown") == value]))
        for dimension, value, selected in selections:
            complete = [row for row in selected if row["metric_deltas"] is not None]
            mean = {metric: math.fsum(row["metric_deltas"][metric] for row in complete) / len(complete) if complete else None for metric in METRICS}
            result.append({"split": split, "dimension": dimension, "value": value,
                           "planned_queries": len(selected), "compared_queries": len(complete),
                           "base_status_counts": dict(Counter(row["base_status"] for row in selected)),
                           "ablation_status_counts": dict(Counter(row["ablation_status"] for row in selected)),
                           "metric_deltas": mean if len(complete) == len(selected) else {metric: None for metric in METRICS},
                           "diagnostic_compared_subset": {"denominator": len(complete), "metric_deltas": mean}})
    return result


def report(pool: dict, labels: dict, systems: list[dict]) -> dict:
    missing = readiness(pool)
    if "source_ablation" not in pool:
        return {"status": "blocked", "blocked_reasons": missing, "comparisons": [],
                "production_ranking_replayed": False, "independent_evidence_count": None}
    value = pool["source_ablation"]["manifest"]
    contributions = _contributions(pool, value, labels)
    attributed = {row["query_id"]: row for row in contributions}
    scored = {(system["system_id"], row["query_id"]): row for system in systems for row in system["queries"]}
    expected = [(candidate_pool["query_id"], doc) for candidate_pool in pool["input"]["pools"] for doc in candidate_pool["document_ids"]]
    if pool["input"]["origin"] != "recorded":
        missing.append("synthetic_candidate_evidence")
    if not expected or any(labels.get(key, {}).get("origin") != "human" for key in expected):
        missing.append("human_judgments_required")
    if any(labels.get(key, {}).get("grade") is None for key in expected):
        missing.append("missing_judgments")
    comparisons = []
    for comparison in value["ablations"]:
        rows = []
        for query in pool["selection"]:
            qid = query["id"]
            base = scored.get((value["base"]["system_id"], qid))
            ablated = scored.get((comparison["system"]["system_id"], qid))
            base_status = base["run_status"] if base else "unavailable"
            ablation_status = ablated["run_status"] if ablated else "unavailable"
            reasons = []
            if not attributed[qid]["attribution_complete"]:
                reasons.append("incomplete_candidate_attribution")
            for name, outcome in (("base", base), ("ablation", ablated)):
                if outcome is None or outcome["run_status"] not in ("ok", "empty"):
                    reasons.append("incomplete_" + name + "_query_run")
                if outcome is not None and outcome["missing_judgments"]:
                    reasons.append("missing_judgments")
            if any(system[key] is None for system in (value["base"], comparison["system"])
                   for key in ("code_sha256", "parameters_sha256", "environment_sha256", "run_sha256")):
                reasons.append("incomplete_external_ranking_identity")
            if any(source["configuration_sha256"] is None for source in value["sources"]):
                reasons.append("incomplete_source_identity")
            delta = None
            if not reasons:
                delta = {metric: base["metrics"][metric] - ablated["metrics"][metric] for metric in METRICS}
            rows.append({"query_id": qid, "split": query["split"], "category": query["category"], "language": query["language"],
                         "base_status": base_status, "ablation_status": ablation_status,
                         "base_reason": base["run_reason"] if base else "missing_external_query_run",
                         "ablation_reason": ablated["run_reason"] if ablated else "missing_external_query_run",
                         "pool_documents": attributed[qid]["pool_documents"], "metric_deltas": delta,
                         "blocked_reasons": sorted(set(reasons))})
        comparisons.append({**comparison, "base_system_id": value["base"]["system_id"],
                            "queries": rows, "groups": _groups(rows)})
    bucket_sources = {}
    for source in value["sources"]:
        bucket = contribution_bucket(source)
        bucket_sources.setdefault(bucket, {"bucket_id": bucket, "identity": contribution_identity(source), "source_ids": []})["source_ids"].append(source["source_id"])
    return {"status": "blocked" if missing else "measured", "blocked_reasons": sorted(set(missing)),
            "manifest_sha256": pool["source_ablation"]["manifest_sha256"], "method": METHOD,
            "base": value["base"], "sources": value["sources"], "buckets": list(bucket_sources.values()),
            "contributions": contributions, "comparisons": comparisons, "production_ranking_replayed": False,
            "independent_evidence_count": None, "source_approval": False,
            "authenticity": "operator_declarations_not_authenticated", "threshold_assessment": "no_implicit_ablation_improvement_gate"}
