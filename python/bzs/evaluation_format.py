"""Bounded, immutable inputs for offline relevance evaluation.

This module validates evidence identities, not the truth of an assessor's claims.
It never fetches a URL, updates the golden manifest, or creates relevance labels.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .golden import GoldenSet, load_golden
from .probe_format import canonical_json
from .artifact_output import write_new_directory
from .probe_state import _open_regular, _regular_path

VERSION = 1
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_SMALL_BYTES = 1024 * 1024
MAX_DOCUMENTS = 20000
MAX_PAIRS = 80000
MAX_SYSTEMS = 200
MAX_DEPTH = 32
SPLITS = ("development", "regression")
SHA = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,100}")

METHOD = {
    "version": "bzs-relevance-2",
    "scope": "fixed_candidate_relevance",
    "cutoff": 10,
    "grades": [0, 1, 2, 3],
    "binary_relevant_at": 1,
    "gain": "2^grade-1",
    "discount": "log2(rank+1)",
    "idcg_scope": "entire_frozen_query_pool",
    "zero_idcg": "zero_and_report_no_relevant",
    "precision_denominator": "fixed_cutoff",
    "mrr": "truncated_at_cutoff",
    "unjudged": "block_not_zero",
    "duplicates": "reject",
    "document_identity": "provided_id_bound_to_exact_url_title_text",
    "duplicate_urls": "reject_within_each_ranking_no_url_normalization",
    "aggregation": "macro_by_query_separate_splits",
    "holdout": "unsupported_and_excluded",
    "source_ablation": "optional_external_manifest_v1",
}

THRESHOLDS = (
    "ndcg_at_10_min", "mrr_at_10_min", "precision_at_10_min",
    "judgment_coverage_min", "query_coverage_min", "answer_coverage_min",
    "abstention_rate_max",
)
DROP_THRESHOLDS = ("ndcg_at_10_max_drop", "mrr_at_10_max_drop", "precision_at_10_max_drop")


def need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ValueError(f"{code}: {message}")


def fields(value: Any, names: set[str], label: str) -> dict:
    need(isinstance(value, dict) and value.keys() == names, "E_EVAL_SCHEMA", f"invalid {label} fields")
    return value


def text(value: Any, label: str, maximum: int = 4096, *, empty: bool = False) -> str:
    need(isinstance(value, str) and (empty or bool(value.strip())), "E_EVAL_SCHEMA", f"invalid {label}")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"E_EVAL_SCHEMA: invalid {label} Unicode") from exc
    need(len(raw) <= maximum and "\x00" not in value, "E_EVAL_LIMIT", f"invalid {label} length")
    return value


def identifier(value: Any, label: str) -> str:
    need(isinstance(value, str) and bool(IDENT.fullmatch(value)), "E_EVAL_SCHEMA", f"invalid {label}")
    return value


def digest(value: Any, label: str, *, nullable: bool = False) -> str | None:
    need((nullable and value is None) or (isinstance(value, str) and bool(SHA.fullmatch(value))),
         "E_EVAL_IDENTITY", f"invalid {label} SHA-256")
    return value


def integer(value: Any, label: str, low: int, high: int) -> int:
    need(type(value) is int and low <= value <= high, "E_EVAL_SCHEMA", f"invalid {label}")
    return value


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def read_json(path: str | Path, maximum: int = MAX_JSON_BYTES) -> tuple[dict, str]:
    path = Path(path)
    need(not path.is_symlink(), "E_EVAL_PATH", "input must not be a symbolic link")
    with _open_regular(path) as stream:
        raw = stream.read(maximum + 1)
    need(len(raw) <= maximum, "E_EVAL_LIMIT", "JSON input exceeds its byte limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "E_EVAL_SCHEMA", "duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("E_EVAL_SCHEMA: non-finite JSON number")

    def finite(value):
        result = float(value)
        need(math.isfinite(result), "E_EVAL_SCHEMA", "non-finite JSON number")
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=invalid, parse_float=finite)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("E_EVAL_SCHEMA: expected bounded UTF-8 JSON") from exc
    need(isinstance(value, dict), "E_EVAL_SCHEMA", "JSON input must be an object")
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        need(depth <= MAX_DEPTH, "E_EVAL_LIMIT", "JSON nesting exceeds its limit")
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        stack.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return value, hashlib.sha256(raw).hexdigest()


def file_bytes(value: dict) -> bytes:
    raw = canonical_json(value) + b"\n"
    need(len(raw) <= MAX_JSON_BYTES, "E_EVAL_LIMIT", "output exceeds its byte limit")
    return raw


def write_directory(path: str | Path, files: dict[str, bytes]) -> None:
    """Publish the complete immutable package atomically; never leave a partial final directory."""
    for name, raw in files.items():
        need(Path(name).name == name and len(raw) <= MAX_JSON_BYTES, "E_EVAL_LIMIT", "invalid output file")
    write_new_directory(path, files, label="evaluation")


def selected_golden(path: str | Path, splits: list[str]) -> tuple[GoldenSet, list[dict]]:
    need(isinstance(splits, list) and bool(splits) and all(isinstance(s, str) for s in splits) and len(splits) == len(set(splits)),
         "E_EVAL_SCHEMA", "splits must be a nonempty unique list")
    need(all(isinstance(split, str) and split in SPLITS for split in splits),
         "E_EVAL_HOLDOUT", "only development/regression evaluation is supported")
    golden = load_golden(path)
    selected = [q for q in golden.queries if q["split"] in splits]
    need(bool(selected), "E_EVAL_EMPTY_SELECTION", "the requested splits contain no queries")
    return golden, selected


def validate_input(value: dict, golden: GoldenSet, selected: list[dict]) -> dict:
    fields(value, {"schema_version", "kind", "queries_sha256", "golden_manifest_sha256", "origin",
                   "ranking_identity", "documents", "pools", "runs"}, "candidate input")
    integer(value["schema_version"], "schema version", VERSION, VERSION)
    need(value["kind"] == "bzs-fixed-candidate-input", "E_EVAL_SCHEMA", "unsupported candidate input")
    need(value["queries_sha256"] == golden.digest and value["golden_manifest_sha256"] == golden.manifest_digest,
         "E_EVAL_IDENTITY", "candidate input does not match the exact golden input")
    need(value["origin"] in ("synthetic", "recorded"), "E_EVAL_SCHEMA", "invalid candidate origin")
    ranker = value["ranking_identity"]
    fields(ranker, {"name", "code_sha256", "config_sha256"}, "ranking identity")
    text(ranker["name"], "ranker name", 200)
    digest(ranker["code_sha256"], "ranker code", nullable=True)
    digest(ranker["config_sha256"], "ranker config", nullable=True)
    known = {q["id"]: q for q in golden.queries}
    selected_ids = {q["id"] for q in selected}
    documents = value["documents"]
    need(isinstance(documents, list) and len(documents) <= MAX_DOCUMENTS,
         "E_EVAL_LIMIT", "invalid document collection")
    docs, evidence_ids = {}, set()
    for doc in documents:
        fields(doc, {"document_id", "url", "title", "text", "evidence_sha256"}, "document")
        ident = identifier(doc["document_id"], "document ID")
        need(ident not in docs, "E_EVAL_DUPLICATE", "duplicate document ID")
        for field, limit in (("url", 8192), ("title", 8192), ("text", 2 * 1024 * 1024)):
            text(doc[field], field, limit, empty=field != "url")
        expected = hash_json({key: doc[key] for key in ("url", "title", "text")})
        need(doc["evidence_sha256"] == expected, "E_EVAL_IDENTITY", "document evidence hash mismatch")
        need(expected not in evidence_ids, "E_EVAL_DUPLICATE", "identical evidence must reuse one document ID")
        evidence_ids.add(expected)
        docs[ident] = doc
    pools = value["pools"]
    need(isinstance(pools, list) and len(pools) <= 1000, "E_EVAL_LIMIT", "invalid query pools")
    by_query = {}
    total = 0
    for pool in pools:
        fields(pool, {"query_id", "document_ids"}, "query pool")
        qid = identifier(pool["query_id"], "query ID")
        need(qid in known, "E_EVAL_IDENTITY", "pool has an unknown query ID")
        need(known[qid]["split"] != "holdout" and not qid.lower().startswith("h"),
             "E_EVAL_HOLDOUT", "candidate input contains a reserved holdout query")
        need(qid in selected_ids and qid not in by_query, "E_EVAL_IDENTITY", "duplicate or unselected query pool")
        ids = pool["document_ids"]
        need(isinstance(ids, list) and len(ids) <= 2000 and all(isinstance(i, str) and i in docs for i in ids),
             "E_EVAL_SCHEMA", "invalid pool document IDs")
        need(len(ids) == len(set(ids)), "E_EVAL_DUPLICATE", "duplicate pool document ID")
        total += len(ids)
        need(total <= MAX_PAIRS, "E_EVAL_LIMIT", "too many query/document pairs")
        by_query[qid] = set(ids)
    need(set(by_query) == selected_ids, "E_EVAL_IDENTITY", "every selected query requires an explicit pool")
    used = set().union(*by_query.values())
    need(used == docs.keys(), "E_EVAL_IDENTITY", "unreferenced document evidence")
    runs = value["runs"]
    need(isinstance(runs, list) and len(runs) <= MAX_PAIRS, "E_EVAL_LIMIT", "invalid run collection")
    seen = set()
    systems = set()
    for run in runs:
        fields(run, {"system_id", "query_id", "status", "document_ids", "reason"}, "run")
        system = identifier(run["system_id"], "system ID")
        qid = identifier(run["query_id"], "query ID")
        need(qid in selected_ids, "E_EVAL_IDENTITY", "run query is outside the selected pool")
        key = (system, qid)
        need(key not in seen, "E_EVAL_DUPLICATE", "duplicate system/query run")
        seen.add(key)
        systems.add(system)
        need(len(systems) <= MAX_SYSTEMS, "E_EVAL_LIMIT", "too many systems")
        status = run["status"]
        need(status in ("ok", "empty", "failed", "unavailable"), "E_EVAL_SCHEMA", "invalid run status")
        ids = run["document_ids"]
        need(isinstance(ids, list) and len(ids) <= 2000 and all(isinstance(i, str) and i in by_query[qid] for i in ids),
             "E_EVAL_IDENTITY", "run result is outside its frozen pool")
        need(len(ids) == len(set(ids)), "E_EVAL_DUPLICATE", "duplicate ranked document")
        need(len(ids) == len({docs[ident]["url"] for ident in ids}), "E_EVAL_DUPLICATE", "duplicate exact URL in a ranking")
        need((status == "ok" and bool(ids)) or (status != "ok" and not ids),
             "E_EVAL_SCHEMA", "run status and results disagree")
        if status in ("failed", "unavailable"):
            text(run["reason"], "run failure reason", 300)
        else:
            need(run["reason"] is None, "E_EVAL_SCHEMA", "successful run cannot have a failure reason")
    need(bool(systems), "E_EVAL_SCHEMA", "at least one explicitly recorded system is required")
    return value


def load_package(path: str | Path) -> tuple[dict, dict, dict, dict]:
    root = Path(path)
    need(root.is_dir() and not root.is_symlink(), "E_EVAL_PATH", "package must be a regular directory")
    manifest, manifest_sha = read_json(_regular_path(root, "package.json"), MAX_SMALL_BYTES)
    fields(manifest, {"schema_version", "kind", "pool_sha256", "method_sha256"}, "package manifest")
    integer(manifest["schema_version"], "schema version", VERSION, VERSION)
    need(manifest["kind"] == "bzs-evaluation-package", "E_EVAL_SCHEMA", "unsupported package")
    pool, pool_sha = read_json(_regular_path(root, "pool.json"))
    method, method_sha = read_json(_regular_path(root, "method.json"), MAX_SMALL_BYTES)
    need(manifest["pool_sha256"] == pool_sha and manifest["method_sha256"] == method_sha,
         "E_EVAL_IDENTITY", "package file identity mismatch")
    need(canonical_json(method) == canonical_json(METHOD), "E_EVAL_METHOD", "unsupported or changed evaluation method")
    pool_fields = {"schema_version", "kind", "dataset", "selection", "input_sha256", "input", "provenance"}
    fields(pool, pool_fields | ({"source_ablation"} if "source_ablation" in pool else set()), "pool")
    integer(pool["schema_version"], "schema version", VERSION, VERSION)
    need(pool["kind"] == "bzs-evaluation-pool", "E_EVAL_SCHEMA", "unsupported pool")
    dataset = pool["dataset"]
    fields(dataset, {"queries_sha256", "golden_manifest_sha256", "frozen", "version"}, "dataset identity")
    digest(dataset["queries_sha256"], "queries")
    digest(dataset["golden_manifest_sha256"], "golden manifest", nullable=True)
    need(type(dataset["frozen"]) is bool and (not dataset["frozen"] or dataset["golden_manifest_sha256"] is not None),
         "E_EVAL_IDENTITY", "invalid frozen dataset marker")
    need(dataset["version"] is None or type(dataset["version"]) is int and dataset["version"] == 1,
         "E_EVAL_SCHEMA", "unsupported dataset version")
    selection = pool["selection"]
    need(isinstance(selection, list) and 1 <= len(selection) <= 1000, "E_EVAL_SCHEMA", "invalid query selection")
    seen = set()
    for query in selection:
        fields(query, {"id", "query", "category", "language", "split", "good_for"}, "selected query")
        ident = identifier(query["id"], "query ID")
        need(ident not in seen, "E_EVAL_DUPLICATE", "duplicate selected query")
        seen.add(ident)
        need(query["split"] in SPLITS and not ident.lower().startswith("h") and query["category"] != "holdout",
             "E_EVAL_HOLDOUT", "evaluation package must not expose holdout queries")
        text(query["query"], "query text", 4096)
    # Reuse the golden normalizer for metadata types without loading any new file.
    from .golden import _normalize_queries
    _normalize_queries([{key: val for key, val in q.items() if key != "language" or val is not None}
                        for q in selection])
    snapshot = GoldenSet(selection, dataset["queries_sha256"], dataset["version"], dataset["frozen"],
                         dataset["golden_manifest_sha256"])
    validate_input(pool["input"], snapshot, selection)
    digest(pool["input_sha256"], "candidate input")
    need(isinstance(pool["provenance"], dict), "E_EVAL_SCHEMA", "invalid provenance")
    if "source_ablation" in pool:
        from .evaluation_ablation import validate_manifest
        validate_manifest(pool["source_ablation"], pool)
    bindings = {"package_sha256": manifest_sha, "pool_sha256": pool_sha, "method_sha256": method_sha,
                "corpus_sha256": corpus_digest(pool)}
    return pool, method, manifest, bindings


def corpus_digest(pool: dict) -> str:
    """Ranks may change in a comparison; the judged universe and its evidence may not."""
    return hash_json({"dataset": pool["dataset"], "selection": pool["selection"],
                      "origin": pool["input"]["origin"], "documents": pool["input"]["documents"],
                      "pools": pool["input"]["pools"]})


def golden_projection_identity(pool: dict) -> dict:
    """The expected verification record; this alone is never proof of verification."""
    return {"status": "verified", **pool["dataset"], "selection_sha256": hash_json(pool["selection"]),
            "selected_count": len(pool["selection"])}


def verify_golden_projection(pool: dict, queries_path: str | Path | None) -> dict:
    """Bind packaged query metadata to original bytes, without exporting holdout text.

    Package checksums only prove consistency within an operator-supplied package.
    They cannot certify that its query text/splits came from the original golden
    file. Every potentially passing freeze and run therefore needs that file.
    """
    if queries_path is None:
        return {"status": "not_supplied"}
    selected_splits = list(dict.fromkeys(q["split"] for q in pool["selection"]))
    golden, selected = selected_golden(queries_path, selected_splits)
    expected_dataset = {"queries_sha256": golden.digest, "golden_manifest_sha256": golden.manifest_digest,
                        "frozen": golden.frozen, "version": golden.version}
    need(canonical_json(expected_dataset) == canonical_json(pool["dataset"]),
         "E_EVAL_IDENTITY", "original golden file/manifest differs from the packaged dataset identity")
    projection = [{**q, "language": q.get("language")} for q in selected]
    need(canonical_json(projection) == canonical_json(pool["selection"]),
         "E_EVAL_IDENTITY", "packaged selection differs from complete original non-holdout split projection")
    return golden_projection_identity(pool)


def label_bindings(bindings: dict) -> dict:
    need(isinstance(bindings, dict), "E_EVAL_IDENTITY", "missing label identity bindings")
    for key in ("corpus_sha256", "method_sha256"):
        digest(bindings.get(key), key)
    return {key: bindings[key] for key in ("corpus_sha256", "method_sha256")}


def gate_template(bindings: dict) -> dict:
    return {"schema_version": VERSION, "kind": "bzs-evaluation-gate", "bindings": dict(bindings),
            "environment": {key: None for key in ("id", "hardware", "software_sha256", "command")},
            "review": {key: None for key in ("reviewer_id", "reviewed_at", "evidence_sha256")},
            "thresholds": {key: None for key in THRESHOLDS},
            "non_regression": {"baseline_report_sha256": None, **{key: None for key in DROP_THRESHOLDS}}}


def validate_gate(gate: dict, bindings: dict) -> list[str]:
    fields(gate, {"schema_version", "kind", "bindings", "environment", "review", "thresholds", "non_regression"}, "gate")
    integer(gate["schema_version"], "schema version", VERSION, VERSION)
    need(gate["kind"] == "bzs-evaluation-gate" and gate["bindings"] == bindings,
         "E_EVAL_IDENTITY", "gate does not bind this package")
    missing = []
    for group, keys in (("environment", {"id", "hardware", "software_sha256", "command"}),
                        ("review", {"reviewer_id", "reviewed_at", "evidence_sha256"})):
        values = fields(gate[group], keys, group)
        for key, value in values.items():
            if value is None:
                missing.append(f"missing_{group}_{key}")
            elif key.endswith("sha256"):
                digest(value, group + " " + key)
            else:
                text(value, group + " " + key, 4096)
                if key == "reviewed_at":
                    try:
                        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        need(stamp.utcoffset() is not None, "E_EVAL_SCHEMA", "review timestamp needs a timezone")
                    except ValueError as exc:
                        raise ValueError("E_EVAL_SCHEMA: invalid review timestamp") from exc
    for group, keys in (("thresholds", set(THRESHOLDS)),
                        ("non_regression", {"baseline_report_sha256", *DROP_THRESHOLDS})):
        values = fields(gate[group], keys, group)
        for key, value in values.items():
            if key == "baseline_report_sha256":
                digest(value, "baseline report", nullable=True)
            elif value is None:
                if group == "thresholds" or values["baseline_report_sha256"] is not None:
                    missing.append("missing_threshold_" + key)
            else:
                need(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1,
                     "E_EVAL_SCHEMA", "thresholds must be finite numbers in 0..1 or null")
        if group == "non_regression" and values["baseline_report_sha256"] is None:
            need(all(values[key] is None for key in DROP_THRESHOLDS), "E_EVAL_SCHEMA", "drop thresholds need a baseline")
    return sorted(missing)


def validate_labels(value: dict, pool: dict, bindings: dict) -> dict[tuple[str, str], dict]:
    fields(value, {"schema_version", "kind", "bindings", "judgments"}, "labels")
    integer(value["schema_version"], "schema version", VERSION, VERSION)
    need(value["kind"] == "bzs-relevance-judgments" and value["bindings"] == label_bindings(bindings),
         "E_EVAL_IDENTITY", "labels do not bind this exact package")
    docs = {doc["document_id"]: doc for doc in pool["input"]["documents"]}
    expected = {(q["query_id"], doc) for q in pool["input"]["pools"] for doc in q["document_ids"]}
    rows = value["judgments"]
    need(isinstance(rows, list) and len(rows) <= MAX_PAIRS, "E_EVAL_LIMIT", "invalid judgment collection")
    judgments = {}
    for row in rows:
        fields(row, {"query_id", "document_id", "evidence_sha256", "grade", "origin", "assessor_id", "assessed_at", "note"}, "judgment")
        key = (identifier(row["query_id"], "query ID"), identifier(row["document_id"], "document ID"))
        need(key in expected, "E_EVAL_IDENTITY", "judgment is outside the selected frozen pool")
        need(key not in judgments, "E_EVAL_DUPLICATE", "duplicate/conflicting judgment; adjudicate explicitly")
        need(row["evidence_sha256"] == docs[key[1]]["evidence_sha256"], "E_EVAL_IDENTITY", "judgment evidence changed")
        need(row["origin"] in ("unjudged", "human", "synthetic"), "E_EVAL_SCHEMA", "invalid judgment origin")
        text(row["note"], "judgment note", 4096, empty=True)
        if row["origin"] == "unjudged":
            need(row["grade"] is None and row["assessor_id"] is None and row["assessed_at"] is None,
                 "E_EVAL_SCHEMA", "unjudged records cannot contain a grade or assessor")
        else:
            integer(row["grade"], "relevance grade", 0, 3)
            identifier(row["assessor_id"], "assessor ID")
            try:
                stamp = datetime.fromisoformat(text(row["assessed_at"], "assessment timestamp", 100).replace("Z", "+00:00"))
                need(stamp.utcoffset() is not None, "E_EVAL_SCHEMA", "assessment timestamp needs a timezone")
            except ValueError as exc:
                raise ValueError("E_EVAL_SCHEMA: invalid assessment timestamp") from exc
        judgments[key] = row
    return judgments
