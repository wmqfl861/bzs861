"""Synthetic source attribution and external-run contracts, never human qrels."""
from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys

import pytest

from bzs import evaluation
from bzs.evaluation_ablation import METHOD, contribution_bucket, external_run_digest
from bzs.evaluation_format import file_bytes, load_package, read_json
from bzs.golden import load_golden
from test_evaluation import frozen, inputs, labels, write


def ablation_inputs(tmp_path, *, mutate_input=None, mutate_manifest=None, second_development=False):
    queries, candidates, value = inputs(tmp_path)
    if second_development:
        golden_value, _ = read_json(queries)
        golden_value["queries"].insert(1, {"id": "dev2", "query": "second synthetic case", "category": "code", "language": "en", "split": "development"})
        write(queries, golden_value)
        value["queries_sha256"] = load_golden(queries).digest
        value["pools"].insert(1, {"query_id": "dev2", "document_ids": ["d0", "d1", "d2", "d3"]})
    sources = []
    for sid, provider, service, family in (
        ("a", "provider-a", "service-a", "known-family"),
        ("a2", "provider-alias", "service-alias", "known-family"),
        ("b", "provider-unknown", "service-b", "unknown"),
        ("c", "provider-unknown", "service-c", "unknown"),
        ("c2", "provider-unknown", "service-c", "unknown"),
    ):
        sources.append({"source_id": sid, "system_id": "source-" + sid, "provider": provider, "capability": "web",
                        "logical_service_id": service, "index_family": family, "configuration_sha256": "4" * 64})
    rankings = {"source-a": ["d0", "d3"], "source-a2": ["d0"], "source-b": ["d1", "d3"],
                "source-c": ["d2"], "source-c2": ["d2"], "base": ["d0", "d1", "d2", "d3"],
                "without-a": ["d0", "d1", "d2", "d3"], "without-family": ["d1", "d2", "d3"],
                "without-service-c": ["d0", "d1", "d3"]}
    value["runs"] = [{"system_id": system, "query_id": candidate_pool["query_id"], "status": "ok", "document_ids": list(ids), "reason": None}
                     for system, ids in rankings.items() for candidate_pool in value["pools"]]
    if mutate_input is not None:
        mutate_input(value)
    write(candidates, value)
    selected = [{**q, "language": q.get("language")} for q in load_golden(queries).queries if q["split"] != "holdout"]
    digest_pool = {"input": value, "selection": selected}
    source_ids = [source["source_id"] for source in sources]

    def system(ident, removed):
        active = [sid for sid in source_ids if sid not in removed]
        return {"system_id": ident, "source_ids": active, "implementation": "synthetic_fixture",
                "code_sha256": "1" * 64, "parameters_sha256": "2" * 64, "environment_sha256": "3" * 64,
                "run_sha256": external_run_digest(digest_pool, ident, active)}

    manifest = {"schema_version": 1, "kind": "bzs-source-ablation-manifest",
                "input_sha256": hashlib.sha256(candidates.read_bytes()).hexdigest(), "method": METHOD,
                "sources": sources, "base": system("base", []),
                "ablations": [{"ablation_id": ident, "removal_kind": kind, "removed_source_ids": removed,
                               "system": system(ident, removed)} for ident, kind, removed in (
                    ("without-a", "source", ["a"]),
                    ("without-family", "contribution_bucket", ["a", "a2"]),
                    ("without-service-c", "contribution_bucket", ["c", "c2"]))]}
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    manifest_path = write(tmp_path / "ablation.json", manifest)
    return queries, candidates, manifest_path, value, manifest


def prepare(tmp_path, **kwargs):
    queries, candidates, manifest_path, value, manifest = ablation_inputs(tmp_path, **kwargs)
    out = tmp_path / "package"
    evaluation.prepare(str(queries), str(out), candidates_path=str(candidates), ablation_manifest_path=str(manifest_path))
    return out, queries, candidates, value, manifest


def evaluate(tmp_path, *, label_mutation=None, **kwargs):
    package, queries, _, _, _ = prepare(tmp_path, **kwargs)
    label_path, label_value = labels(package, tmp_path)
    if label_mutation is not None:
        label_mutation(label_value)
        write(label_path, label_value)
    freeze_path, _ = frozen(package, tmp_path)
    result = evaluation.run(str(package), str(freeze_path), str(label_path), str(tmp_path / "result"), queries_path=str(queries))
    return result


def comparison(report, ident="without-family"):
    return next(row for row in report["source_ablation"]["comparisons"] if row["ablation_id"] == ident)


def group(comparison, split="development"):
    return next(row for row in comparison["groups"] if row["dimension"] == "all" and row["split"] == split)


def test_external_pair_uses_original_idcg_and_preserves_synthetic_blockers(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline ablation must not make network requests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = evaluate(tmp_path)
    ablation = result["source_ablation"]
    expected = evaluation.score_ranking({"d0": 3, "d1": 2, "d2": 1, "d3": 0}, ["d1", "d2", "d3"])
    measured = group(comparison(result))["metric_deltas"]
    assert measured["ndcg_at_10"] == pytest.approx(1 - expected["ndcg"])
    assert measured["precision_at_10"] == pytest.approx(0.1)
    assert measured["mrr_at_10"] == 0
    assert group(comparison(result, "without-a"))["metric_deltas"] == dict.fromkeys(measured, 0)
    assert all(row["pool_documents"] == 4 for row in comparison(result)["queries"])
    assert result["coverage"]["expected_judgments"] == 8
    assert ablation["status"] == result["release_gate"]["status"] == "blocked"
    assert set(ablation["blocked_reasons"]) == {"synthetic_candidate_evidence", "human_judgments_required"}
    assert ablation["production_ranking_replayed"] is False
    assert ablation["independent_evidence_count"] is None
    assert result["network_requests"] == result["model_calls"] == 0
    assert "evaluation_ablation.py" in result["runtime"]["files"]


def test_known_families_and_unknown_services_do_not_create_adapter_votes(tmp_path):
    result = evaluate(tmp_path)
    ablation = result["source_ablation"]
    assert [row["source_ids"] for row in ablation["buckets"]] == [["a", "a2"], ["b"], ["c", "c2"]]
    row = ablation["contributions"][0]
    channels = {source["source_id"]: source for source in row["sources"]}
    assert channels["a"]["exclusive_documents"] == 0
    assert channels["a2"]["exclusive_documents"] == 0
    assert channels["c"]["exclusive_documents"] == channels["c2"]["exclusive_documents"] == 0
    assert channels["b"]["exclusive_relevant_documents"] == 1
    buckets = {bucket["bucket_id"]: bucket for bucket in row["buckets"]}
    assert buckets[channels["a"]["contribution_bucket"]]["exclusive_relevant_documents"] == 1
    assert buckets[channels["c"]["contribution_bucket"]]["exclusive_relevant_documents"] == 1
    assert channels["c"]["contribution_bucket"] != channels["b"]["contribution_bucket"]
    assert row["attribution_complete"] is True


def test_delta_keeps_negative_direction_without_inventing_a_quality_threshold(tmp_path):
    def mutate(value):
        for row in value["runs"]:
            if row["system_id"] == "base":
                row["document_ids"].reverse()
    result = evaluate(tmp_path, mutate_input=mutate)
    delta = group(comparison(result, "without-a"))["metric_deltas"]
    assert delta["ndcg_at_10"] < 0
    assert delta["mrr_at_10"] == -0.5
    assert result["source_ablation"]["threshold_assessment"] == "no_implicit_ablation_improvement_gate"


def test_removed_unreturned_unjudged_candidate_still_blocks_whole_group(tmp_path):
    def remove_judgment(value):
        value["judgments"] = [row for row in value["judgments"] if (row["query_id"], row["document_id"]) != ("dev2", "d0")]
    result = evaluate(tmp_path, second_development=True, label_mutation=remove_judgment)
    metrics = group(comparison(result))
    assert metrics["planned_queries"] == 2
    assert metrics["compared_queries"] == 1
    assert all(value is None for value in metrics["metric_deltas"].values())
    assert metrics["diagnostic_compared_subset"]["denominator"] == 1
    assert result["coverage"]["expected_judgments"] == 12
    assert result["coverage"]["missing_judgments"] == [{"query_id": "dev2", "document_id": "d0"}]
    assert "missing_judgments" in result["source_ablation"]["blocked_reasons"]
    assert "missing_judgments" in comparison(result)["queries"][1]["blocked_reasons"]


@pytest.mark.parametrize("status", ["empty", "failed", "unavailable", "missing"])
def test_external_empty_failed_and_missing_remain_distinct(tmp_path, status):
    def mutate(value):
        if status == "missing":
            value["runs"] = [row for row in value["runs"] if not (row["system_id"] == "without-family" and row["query_id"] == "dev1")]
        else:
            row = next(row for row in value["runs"] if row["system_id"] == "without-family" and row["query_id"] == "dev1")
            row.update(status=status, document_ids=[], reason=None if status == "empty" else "synthetic-outcome")
    result = evaluate(tmp_path, mutate_input=mutate)
    selected = group(comparison(result))
    reported = "unavailable" if status == "missing" else status
    assert selected["ablation_status_counts"] == {reported: 1}
    assert selected["compared_queries"] == (1 if status == "empty" else 0)
    if status == "empty":
        assert selected["metric_deltas"]["ndcg_at_10"] == 1
    else:
        assert all(value is None for value in selected["metric_deltas"].values())
        assert "source_ablation_" + ("missing_external_query_run" if status == "missing" else "incomplete_external_query_run") in result["quality_gate"]["blocked_reasons"]
        frozen_value, _ = read_json(tmp_path / "frozen" / "freeze.json")
        assert any("external_query_run" in reason for reason in frozen_value["blocked_reasons"])


def test_completely_absent_external_system_cannot_be_reconstructed_by_filtering(tmp_path):
    def mutate(value):
        value["runs"] = [row for row in value["runs"] if row["system_id"] != "without-family"]
    result = evaluate(tmp_path, mutate_input=mutate)
    assert all(row["metric_deltas"] is None for row in comparison(result)["queries"])
    assert "missing_external_query_run" in result["source_ablation"]["blocked_reasons"]
    assert all(row["ablation_reason"] == "missing_external_query_run" for row in comparison(result)["queries"])


def test_failed_source_does_not_inflate_exclusive_contribution(tmp_path):
    def mutate(value):
        for row in value["runs"]:
            if row["system_id"] == "source-a2":
                row.update(status="failed", document_ids=[], reason="synthetic-timeout")
    result = evaluate(tmp_path, mutate_input=mutate)
    for row in result["source_ablation"]["contributions"]:
        assert row["attribution_complete"] is False
        assert all(source["exclusive_documents"] is None for source in row["sources"])
    assert "incomplete_source_query_run" in result["source_ablation"]["blocked_reasons"]
    assert group(comparison(result))["compared_queries"] == 0


def test_unattributed_pool_candidate_is_retained_and_blocks(tmp_path):
    def mutate(value):
        for row in value["runs"]:
            row["document_ids"] = [doc for doc in row["document_ids"] if doc != "d2"]
            if not row["document_ids"]:
                row.update(status="empty")
    result = evaluate(tmp_path, mutate_input=mutate)
    assert result["coverage"]["expected_judgments"] == 8
    assert all(row["unattributed_document_ids"] == ["d2"] for row in result["source_ablation"]["contributions"])
    assert "unattributed_pool_candidates" in result["source_ablation"]["blocked_reasons"]
    assert group(comparison(result))["compared_queries"] == 0


@pytest.mark.parametrize("field", ["code_sha256", "parameters_sha256", "environment_sha256", "run_sha256"])
def test_missing_external_identity_is_frozen_as_blocked(tmp_path, field):
    result = evaluate(tmp_path, mutate_manifest=lambda value: value["ablations"][1]["system"].update({field: None}))
    assert "missing_external_" + field in result["source_ablation"]["blocked_reasons"]
    assert group(comparison(result))["compared_queries"] == 0
    frozen_value, _ = read_json(tmp_path / "frozen" / "freeze.json")
    assert "source_ablation_missing_external_" + field in frozen_value["blocked_reasons"]


def test_missing_source_configuration_cannot_claim_a_comparable_pair(tmp_path):
    result = evaluate(tmp_path, mutate_manifest=lambda value: value["sources"][0].update(configuration_sha256=None))
    assert "missing_source_configuration_identity" in result["source_ablation"]["blocked_reasons"]
    assert group(comparison(result))["compared_queries"] == 0


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema_version=True),
    lambda m: m.update(schema_version=2),
    lambda m: m.update(unknown=True),
    lambda m: m.update(input_sha256="f" * 64),
    lambda m: m.update(method={**METHOD, "idcg_scope": "surviving_candidates"}),
    lambda m: m["sources"][1].update(source_id="a"),
    lambda m: m["sources"][1].update(system_id="source-a"),
    lambda m: m["sources"][4].update(index_family="known-family"),
    lambda m: m["ablations"][1].update(removed_source_ids=["a"]),
    lambda m: m["ablations"][2].update(removed_source_ids=["b", "c", "c2"]),
    lambda m: m["ablations"][1].update(removed_source_ids=["a2", "a"]),
    lambda m: m["ablations"][1].update(removed_source_ids=["a", "a"]),
    lambda m: m["ablations"][0].update(removal_kind="after_observing_results"),
    lambda m: m["ablations"][0].update(ablation_id="without-family"),
    lambda m: m["ablations"][1]["system"].update(source_ids=["c", "c2", "b"]),
    lambda m: m["ablations"][1]["system"].update(environment_sha256="f" * 64),
    lambda m: m["ablations"][1]["system"].update(parameters_sha256="f" * 64),
    lambda m: m["ablations"][1]["system"].update(code_sha256="f" * 64),
    lambda m: m["ablations"][1]["system"].update(run_sha256="f" * 64),
    lambda m: m["base"].update(implementation="python_rrf"),
    lambda m: m["base"].update(code_sha256="f" * 64),
])
def test_invalid_or_noncomparable_manifests_fail_before_output(tmp_path, mutate):
    with pytest.raises(ValueError, match="E_EVAL_"):
        prepare(tmp_path, mutate_manifest=mutate)
    assert not (tmp_path / "package").exists()


def test_external_ranking_cannot_return_only_removed_source_evidence(tmp_path):
    def mutate(value):
        for row in value["runs"]:
            if row["system_id"] == "without-family":
                row["document_ids"].insert(0, "d0")
    with pytest.raises(ValueError, match="surviving sources"):
        prepare(tmp_path, mutate_input=mutate)
    assert not (tmp_path / "package").exists()


def test_recorded_candidates_cannot_declare_the_synthetic_ranking_implementation(tmp_path):
    with pytest.raises(ValueError, match="C\\+\\+ implementation"):
        prepare(tmp_path, mutate_input=lambda value: value.update(origin="recorded"))
    assert not (tmp_path / "package").exists()


def test_unlisted_candidate_system_is_not_silently_ignored(tmp_path):
    def mutate(value):
        value["runs"].append({"system_id": "unlisted-source", "query_id": "dev1", "status": "empty", "document_ids": [], "reason": None})
    with pytest.raises(ValueError, match="every supplied candidate system"):
        prepare(tmp_path, mutate_input=mutate)


def test_ablation_bounds_include_planned_but_missing_query_runs(tmp_path, monkeypatch):
    from bzs import evaluation_ablation
    monkeypatch.setattr(evaluation_ablation, "MAX_PAIRS", 10)
    with pytest.raises(ValueError, match="planned source/comparison query pairs"):
        prepare(tmp_path)


def test_old_method_identity_cannot_be_rehashed_into_new_ablation_support(tmp_path):
    package, _, _, _, _ = prepare(tmp_path)
    method, _ = read_json(package / "method.json")
    method.update(version="bzs-relevance-1", source_ablation="not_implemented")
    method_raw = file_bytes(method)
    (package / "method.json").write_bytes(method_raw)
    manifest, _ = read_json(package / "package.json")
    manifest["method_sha256"] = hashlib.sha256(method_raw).hexdigest()
    write(package / "package.json", manifest)
    with pytest.raises(ValueError, match="E_EVAL_METHOD"):
        load_package(package)


def test_external_output_change_cannot_reuse_the_old_run_digest(tmp_path):
    queries, candidates, manifest_path, value, manifest = ablation_inputs(tmp_path)
    row = next(row for row in value["runs"] if row["system_id"] == "without-family")
    row["document_ids"].reverse()
    write(candidates, value)
    manifest["input_sha256"] = hashlib.sha256(candidates.read_bytes()).hexdigest()
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match="complete selected-query runs"):
        evaluation.prepare(str(queries), str(tmp_path / "package"), candidates_path=str(candidates), ablation_manifest_path=str(manifest_path))


def test_ablation_plan_is_bound_by_freeze_while_labels_retain_original_corpus(tmp_path):
    package, queries, _, _, _ = prepare(tmp_path)
    label_path, _ = labels(package, tmp_path)
    freeze_path, _ = frozen(package, tmp_path)
    pool, _, manifest, bindings = load_package(package)
    pool["source_ablation"]["manifest"]["ablations"][0]["ablation_id"] = "renamed-after-freeze"
    pool_raw = file_bytes(pool)
    (package / "pool.json").write_bytes(pool_raw)
    manifest["pool_sha256"] = hashlib.sha256(pool_raw).hexdigest()
    write(package / "package.json", manifest)
    _, _, _, changed = load_package(package)
    assert changed["corpus_sha256"] == bindings["corpus_sha256"]
    assert changed["package_sha256"] != bindings["package_sha256"]
    with pytest.raises(ValueError, match="freeze does not bind"):
        evaluation.run(str(package), str(freeze_path), str(label_path), str(tmp_path / "reused"), queries_path=str(queries))


def test_capability_and_structured_service_identity_avoid_ambiguous_buckets():
    source = {"index_family": "unknown", "provider": "a:b", "capability": "web", "logical_service_id": "c"}
    different = {**source, "provider": "a", "logical_service_id": "b:c"}
    assert contribution_bucket(source) != contribution_bucket(different)
    assert contribution_bucket(source) != contribution_bucket({**source, "capability": "news"})
    known = {**source, "index_family": "family-a"}
    assert contribution_bucket(known) == contribution_bucket({**known, "provider": "other", "logical_service_id": "other"})
    assert contribution_bucket(known) != contribution_bucket({**known, "capability": "news"})


def test_manifest_cli_prepares_only_unjudged_nonholdout_artifacts(tmp_path):
    queries, candidates, manifest_path, _, _ = ablation_inputs(tmp_path)
    out = tmp_path / "package"
    process = subprocess.run([sys.executable, "-m", "bzs.evaluation", "prepare", "--queries", str(queries),
                              "--candidates", str(candidates), "--ablation-manifest", str(manifest_path), "--out", str(out)],
                             text=True, capture_output=True, check=False, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["quality_gate"] == "blocked"
    judgments, _ = read_json(out / "labels.template.json")
    assert all(row["grade"] is None and row["origin"] == "unjudged" for row in judgments["judgments"])
    assert all(b"SYNTHETIC_SEALED_CANARY" not in path.read_bytes() for path in out.iterdir())
    pool, _, _, _ = load_package(out)
    assert pool["source_ablation"]["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
