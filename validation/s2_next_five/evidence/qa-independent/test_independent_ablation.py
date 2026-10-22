"""Independent synthetic probes for commit 994d744; never real human judgments.

The input builders and arithmetic oracle do not import the author's tests.
All rank/output claims in this file are synthetic declarations for contract tests.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import socket

import pytest

from bzs import evaluation
from bzs.evaluation_ablation import METHOD, contribution_bucket, external_run_digest


def canonical_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def raw_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_run_digest(value, selected, system_id, source_ids):
    matching = {row["query_id"]: row for row in value["runs"] if row["system_id"] == system_id}
    return canonical_digest({"system_id": system_id, "source_ids": source_ids,
                             "query_runs": [{"query_id": qid, "run": matching.get(qid)} for qid in selected]})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("independent QA review must remain offline")
    monkeypatch.setattr(socket, "create_connection", no_network)


@pytest.fixture
def case(tmp_path):
    selected = ["dev-a", "dev-b", "reg-a"]
    queries = write(tmp_path / "queries.synthetic.json", {"queries": [
        {"id": qid, "query": "Independent synthetic " + qid, "category": "code", "language": "en",
         "split": "regression" if qid == "reg-a" else "development"} for qid in selected]})
    ids = [f"doc-{n:02d}" for n in range(12)]
    documents = []
    for ident in ids:
        evidence = {"url": "https://independent.invalid/" + ident, "title": "Synthetic " + ident, "text": "Independent synthetic evidence " + ident}
        documents.append({"document_id": ident, **evidence, "evidence_sha256": canonical_digest(evidence)})
    source_rows = [("a", "provider-a", "service-a", "known-index"),
                   ("a2", "provider-alias", "service-alias", "known-index"),
                   ("b", "provider-b", "service-b", "unknown"),
                   ("b2", "provider-b", "service-b", "unknown")]
    sources = [{"source_id": sid, "system_id": "source-" + sid, "provider": provider,
                "capability": "web", "logical_service_id": service, "index_family": family,
                "configuration_sha256": "4" * 64} for sid, provider, service, family in source_rows]
    rankings = {"source-a": [ids[0], ids[11]], "source-a2": [ids[0]],
                "source-b": ids[1:11], "source-b2": [ids[1]], "base": ids,
                "without-known": ids[1:11], "without-unknown": [ids[0], ids[11]]}
    value = {"schema_version": 1, "kind": "bzs-fixed-candidate-input", "queries_sha256": raw_digest(queries),
             "golden_manifest_sha256": None, "origin": "synthetic",
             "ranking_identity": {"name": "independent-synthetic", "code_sha256": "1" * 64, "config_sha256": "2" * 64},
             "documents": documents, "pools": [{"query_id": qid, "document_ids": ids} for qid in selected],
             "runs": [{"system_id": system, "query_id": qid, "status": "ok", "document_ids": list(rank), "reason": None}
                      for system, rank in rankings.items() for qid in selected]}
    source_ids = [row["source_id"] for row in sources]

    def external(system, removed):
        return {"system_id": system, "source_ids": [sid for sid in source_ids if sid not in removed],
                "implementation": "synthetic_fixture", "code_sha256": "1" * 64,
                "parameters_sha256": "2" * 64, "environment_sha256": "3" * 64, "run_sha256": None}

    manifest = {"schema_version": 1, "kind": "bzs-source-ablation-manifest", "input_sha256": None,
                "method": copy.deepcopy(METHOD), "sources": sources, "base": external("base", []),
                "ablations": [{"ablation_id": system, "removal_kind": "contribution_bucket", "removed_source_ids": removed,
                               "system": external(system, removed)} for system, removed in
                              (("without-known", ["a", "a2"]), ("without-unknown", ["b", "b2"]))]}
    return {"root": tmp_path, "queries": queries, "selected": selected, "ids": ids, "input": value, "manifest": manifest}


def materialize(case, *, refresh_runs=True):
    candidates = write(case["root"] / "candidates.synthetic.json", case["input"])
    case["manifest"]["input_sha256"] = raw_digest(candidates)
    if refresh_runs:
        for system in [case["manifest"]["base"], *(r["system"] for r in case["manifest"]["ablations"])]:
            system["run_sha256"] = independent_run_digest(case["input"], case["selected"], system["system_id"], system["source_ids"])
    manifest = write(case["root"] / "ablation.synthetic.json", case["manifest"])
    return candidates, manifest


def prepare(case, *, refresh_runs=True):
    candidates, manifest = materialize(case, refresh_runs=refresh_runs)
    package = case["root"] / "package"
    evaluation.prepare(str(case["queries"]), str(package), candidates_path=str(candidates), ablation_manifest_path=str(manifest))
    return package


def setup_run(case, *, judged=True, label_mutation=None):
    package = prepare(case)
    labels = read(package / "labels.template.json")
    grades = {case["ids"][0]: 3, case["ids"][1]: 2, case["ids"][2]: 1, case["ids"][11]: 2}
    if judged:
        for row in labels["judgments"]:
            row.update(grade=grades.get(row["document_id"], 0), origin="synthetic", assessor_id="independent-synthetic-oracle",
                       assessed_at="2026-10-22T00:00:00Z", note="Synthetic arithmetic only; never human evidence")
    if label_mutation is not None:
        label_mutation(labels)
    label_path = write(case["root"] / "labels.synthetic.json", labels)
    gate_path = case["root"] / "gate.json"
    write(gate_path, read(package / "gate.template.json"))
    freeze_dir = case["root"] / "freeze"
    evaluation.freeze(str(package), str(gate_path), str(freeze_dir), queries_path=str(case["queries"]))
    return package, gate_path, freeze_dir / "freeze.json", label_path


def evaluate(case, **kwargs):
    package, _, frozen, labels = setup_run(case, **kwargs)
    return evaluation.run(str(package), str(frozen), str(labels), str(case["root"] / "report"), queries_path=str(case["queries"]))


def comparison(report, ident="without-known"):
    return next(row for row in report["source_ablation"]["comparisons"] if row["ablation_id"] == ident)


def dev_group(comp):
    return next(row for row in comp["groups"] if row["dimension"] == "all" and row["split"] == "development")


def test_independent_12_document_idcg_includes_removed_doc_outside_returned_top10(case):
    result = evaluate(case)
    # Ideal includes doc-11 (grade 2), even though base ranks it twelfth and
    # without-known cannot return it. This is not an oracle call into score_ranking.
    idcg = 7 + 3 / math.log2(3) + 3 / math.log2(4) + 1 / math.log2(5)
    base_dcg = 7 + 3 / math.log2(3) + 1 / math.log2(4)
    removed_dcg = 3 + 1 / math.log2(3)
    row = comparison(result)["queries"][0]
    assert row["pool_documents"] == 12
    assert row["metric_deltas"]["ndcg_at_10"] == pytest.approx((base_dcg - removed_dcg) / idcg, abs=1e-14)
    assert row["metric_deltas"]["precision_at_10"] == pytest.approx(0.1)
    assert result["coverage"]["expected_judgments"] == 36
    assert result["source_ablation"]["status"] == result["release_gate"]["status"] == "blocked"
    assert result["source_ablation"]["independent_evidence_count"] is None


def test_removed_unreturned_doc_missing_label_cannot_shrink_pool_or_group(case):
    def omit(labels):
        labels["judgments"] = [r for r in labels["judgments"] if (r["query_id"], r["document_id"]) != ("dev-b", "doc-11")]
    result = evaluate(case, label_mutation=omit)
    group = dev_group(comparison(result))
    assert group["planned_queries"] == 2 and group["compared_queries"] == 1
    assert set(group["metric_deltas"].values()) == {None}
    assert group["diagnostic_compared_subset"]["denominator"] == 1
    assert result["coverage"]["missing_judgments"] == [{"query_id": "dev-b", "document_id": "doc-11"}]
    assert result["source_ablation"]["status"] == "blocked"


@pytest.mark.parametrize("status", ["empty", "failed", "unavailable", "missing"])
def test_whole_source_system_outcome_preserved_even_when_other_adapter_covers_candidates(case, status):
    if status == "missing":
        case["input"]["runs"] = [row for row in case["input"]["runs"] if row["system_id"] != "source-b2"]
    else:
        for row in case["input"]["runs"]:
            if row["system_id"] == "source-b2":
                row.update(status=status, document_ids=[], reason=None if status == "empty" else "independent-synthetic-failure")
    result = evaluate(case)
    for row in result["source_ablation"]["contributions"]:
        source = next(s for s in row["sources"] if s["source_id"] == "b2")
        outcome = next(s for s in row["source_outcomes"] if s["source_id"] == "b2")
        assert outcome["status"] == ("unavailable" if status == "missing" else status)
        assert outcome["reason"] == ("missing_source_query_run" if status == "missing" else None if status == "empty" else "independent-synthetic-failure")
        assert row["attribution_complete"] is (status == "empty")
        if status == "empty":
            assert source["exclusive_documents"] == 0
        else:
            assert all(s["exclusive_documents"] is None for s in row["sources"])
    group = dev_group(comparison(result))
    assert group["compared_queries"] == (2 if status == "empty" else 0)
    frozen = read(case["root"] / "freeze" / "freeze.json")
    assert ("source_ablation_incomplete_source_query_run" in frozen["blocked_reasons"]) is (status != "empty")
    assert ("incomplete_source_query_run" in result["source_ablation"]["blocked_reasons"]) is (status != "empty")


@pytest.mark.parametrize("mutation", ["beyond_top10", "failure_reason", "missing_query"])
def test_external_digest_binds_all_rows_ranks_and_failure_reasons(case, mutation):
    if mutation == "failure_reason":
        row = next(r for r in case["input"]["runs"] if r["system_id"] == "base" and r["query_id"] == "dev-b")
        row.update(status="failed", document_ids=[], reason="initial-failure")
    materialize(case)
    expected = case["manifest"]["base"]["run_sha256"]
    pool = {"input": case["input"], "selection": [{"id": qid} for qid in case["selected"]]}
    assert external_run_digest(pool, "base", ["a", "a2", "b", "b2"]) == expected
    row = next(r for r in case["input"]["runs"] if r["system_id"] == "base" and r["query_id"] == "dev-b")
    if mutation == "beyond_top10":
        row["document_ids"][10], row["document_ids"][11] = row["document_ids"][11], row["document_ids"][10]
    elif mutation == "failure_reason":
        row["reason"] = "different-failure"
    else:
        case["input"]["runs"].remove(row)
    assert external_run_digest(pool, "base", ["a", "a2", "b", "b2"]) != expected
    with pytest.raises(ValueError, match="E_EVAL_IDENTITY: external ranking digest"):
        prepare(case, refresh_runs=False)
    assert not (case["root"] / "package").exists()


@pytest.mark.parametrize("entry", ["freeze", "run"])
@pytest.mark.parametrize("mutation", ["removed_only_candidate", "omitted_source_identity", "incomparable_environment"])
def test_repacked_outer_hashes_do_not_skip_manifest_validation(case, entry, mutation):
    package, gate, frozen, labels = setup_run(case)
    pool = read(package / "pool.json")
    manifest = pool["source_ablation"]["manifest"]
    if mutation == "removed_only_candidate":
        row = next(r for r in pool["input"]["runs"] if r["system_id"] == "without-known" and r["query_id"] == "dev-b")
        row["document_ids"].insert(0, "doc-00")
        system = manifest["ablations"][0]["system"]
        system["run_sha256"] = independent_run_digest(pool["input"], case["selected"], system["system_id"], system["source_ids"])
    elif mutation == "omitted_source_identity":
        manifest["sources"].pop()
    else:
        manifest["ablations"][0]["system"]["environment_sha256"] = "9" * 64
    # Repack consistent outer content identities; structural checks must still run.
    pool["input_sha256"] = canonical_digest(pool["input"])
    manifest["input_sha256"] = pool["input_sha256"]
    pool["source_ablation"]["manifest_sha256"] = canonical_digest(manifest)
    write(package / "pool.json", pool)
    package_value = read(package / "package.json")
    package_value["pool_sha256"] = raw_digest(package / "pool.json")
    write(package / "package.json", package_value)
    output = case["root"] / "repacked-rejected"
    with pytest.raises(ValueError, match="E_EVAL_ABLATION"):
        if entry == "freeze":
            evaluation.freeze(str(package), str(gate), str(output), queries_path=str(case["queries"]))
        else:
            evaluation.run(str(package), str(frozen), str(labels), str(output), queries_path=str(case["queries"]))
    assert not output.exists()


def test_forged_freeze_status_cannot_release_missing_source_or_unjudged_material(case):
    case["input"]["runs"] = [r for r in case["input"]["runs"] if r["system_id"] != "source-b2"]
    package, _, frozen_path, labels = setup_run(case, judged=False)
    frozen = read(frozen_path)
    frozen.update(status="frozen", blocked_reasons=[])
    write(frozen_path, frozen)
    result = evaluation.run(str(package), str(frozen_path), str(labels), str(case["root"] / "unjudged-report"), queries_path=str(case["queries"]))
    assert result["status"] == result["release_gate"]["status"] == "blocked"
    assert result["coverage"]["judged"] == 0
    assert result["coverage"]["expected_judgments"] == 36
    assert all(row["metric_deltas"] is None for row in comparison(result)["queries"])
    reasons = result["quality_gate"]["blocked_reasons"]
    assert "human_judgments_required" in reasons and "missing_judgments" in reasons
    assert "source_ablation_incomplete_source_query_run" in reasons


def test_grouping_is_structured_and_does_not_establish_independence(case):
    a, a2, b, b2 = case["manifest"]["sources"]
    assert contribution_bucket(a) == contribution_bucket(a2)
    assert contribution_bucket(b) == contribution_bucket(b2)
    assert contribution_bucket(b) != contribution_bucket({**b, "provider": "different-provider"})
    assert contribution_bucket(b) != contribution_bucket({**b, "logical_service_id": "different-service"})
    assert contribution_bucket(a) != contribution_bucket({**a, "capability": "news"})
    result = evaluate(case)
    assert result["source_ablation"]["independent_evidence_count"] is None
    assert result["source_ablation"]["production_ranking_replayed"] is False
    assert result["source_ablation"]["source_approval"] is False


@pytest.mark.parametrize("outcome", ["empty", "missing", "nonempty"])
def test_removing_every_source_requires_actual_empty_output_not_reconstruction(case, outcome):
    manifest = case["manifest"]
    for source in manifest["sources"]:
        source["index_family"] = "single-known-index"
    source_ids = [row["source_id"] for row in manifest["sources"]]
    external = {**manifest["ablations"][0]["system"], "system_id": "without-all", "source_ids": []}
    manifest["ablations"] = [{"ablation_id": "without-all", "removal_kind": "contribution_bucket",
                              "removed_source_ids": source_ids, "system": external}]
    case["input"]["runs"] = [r for r in case["input"]["runs"] if not r["system_id"].startswith("without-")]
    if outcome != "missing":
        case["input"]["runs"].extend({"system_id": "without-all", "query_id": qid,
                                      "status": "empty" if outcome == "empty" else "ok",
                                      "document_ids": [] if outcome == "empty" else ["doc-00"], "reason": None}
                                     for qid in case["selected"])
    if outcome == "nonempty":
        with pytest.raises(ValueError, match="E_EVAL_ABLATION: external ranking contains evidence"):
            prepare(case)
    else:
        result = evaluate(case)
        comp = comparison(result, "without-all")
        assert all((row["metric_deltas"] is not None) is (outcome == "empty") for row in comp["queries"])
        assert ("missing_external_query_run" in result["source_ablation"]["blocked_reasons"]) is (outcome == "missing")
        assert result["release_gate"]["status"] == "blocked"

