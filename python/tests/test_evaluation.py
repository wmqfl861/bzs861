"""Synthetic arithmetic and evidence contracts; no real relevance judgments."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from bzs import cli, evaluation
from bzs.evaluation_format import METHOD, file_bytes, golden_projection_identity, hash_json, load_package, read_json
from bzs.golden import load_golden


def write(path, value):
    path.write_bytes(file_bytes(value))
    return path


def inputs(tmp_path):
    queries = write(tmp_path / "queries.json", {"queries": [
        {"id": "dev1", "query": "synthetic development", "category": "code", "language": "en", "split": "development"},
        {"id": "reg1", "query": "synthetic regression", "category": "zh_tech", "language": "zh", "split": "regression"},
        {"id": "h_fixture", "query": "SYNTHETIC_SEALED_CANARY", "category": "holdout", "language": "en", "split": "holdout"},
    ]})
    golden = load_golden(queries)
    docs = []
    for i in range(4):
        evidence = {"url": f"https://fixture.invalid/doc/{i}", "title": f"Synthetic document {i}", "text": f"Fixture evidence {i}"}
        docs.append({"document_id": f"d{i}", **evidence, "evidence_sha256": hash_json(evidence)})
    value = {"schema_version": 1, "kind": "bzs-fixed-candidate-input", "queries_sha256": golden.digest,
             "golden_manifest_sha256": None, "origin": "synthetic",
             "ranking_identity": {"name": "synthetic-fixture", "code_sha256": "1" * 64, "config_sha256": "2" * 64},
             "documents": docs,
             "pools": [{"query_id": qid, "document_ids": [f"d{i}" for i in range(4)]} for qid in ("dev1", "reg1")],
             "runs": [{"system_id": "fixture", "query_id": qid, "status": "ok", "document_ids": ["d0", "d1", "d3"], "reason": None}
                      for qid in ("dev1", "reg1")]}
    candidates = write(tmp_path / "candidates.json", value)
    return queries, candidates, value


def package(tmp_path):
    queries, candidates, value = inputs(tmp_path)
    out = tmp_path / "package"
    evaluation.prepare(str(queries), str(out), candidates_path=str(candidates))
    return out, queries, candidates, value


def labels(package_dir, tmp_path, *, judged=True):
    value, _ = read_json(package_dir / "labels.template.json")
    if judged:
        for row in value["judgments"]:
            row.update(origin="synthetic", grade={"d0": 3, "d1": 2, "d2": 1, "d3": 0}[row["document_id"]],
                       assessor_id="synthetic-fixture", assessed_at="2026-10-20T00:00:00Z", note="Synthetic unit-test oracle only")
    return write(tmp_path / "labels.json", value), value


def frozen(package_dir, tmp_path, *, thresholds=False, baseline=None):
    gate, _ = read_json(package_dir / "gate.template.json")
    if thresholds:
        for key in gate["thresholds"]:
            gate["thresholds"][key] = 1 if key.endswith("_max") else 0
    if baseline is not None:
        gate["non_regression"] = {"baseline_report_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
                                  "ndcg_at_10_max_drop": 0, "mrr_at_10_max_drop": 0, "precision_at_10_max_drop": 0}
    gate_path = write(tmp_path / "gate.json", gate)
    evaluation.freeze(str(package_dir), str(gate_path), str(tmp_path / "frozen"))
    return tmp_path / "frozen" / "freeze.json", gate_path


def group(report, split="development"):
    return next(row for row in report["systems"][0]["groups"] if row["dimension"] == "all" and row["split"] == split)


def test_metric_oracle_uses_entire_pool_fixed_precision_denominator_and_cutoff():
    grades = {"a": 3, "b": 2, "c": 1, "d": 0}
    score = evaluation.score_ranking(grades, ["a", "b", "d"], cutoff=3)
    assert score["ndcg"] == pytest.approx(0.9467676761267002, abs=1e-14)
    assert score["precision"] == pytest.approx(2 / 3)
    assert score["reciprocal_rank"] == 1
    assert evaluation.score_ranking(grades, ["a", "b", "c", "d"], cutoff=3)["ndcg"] == 1
    assert evaluation.score_ranking(grades, ["a"], cutoff=10)["precision"] == 0.1
    assert evaluation.score_ranking(grades, ["d", "c"], cutoff=1)["reciprocal_rank"] == 0
    rr = evaluation.score_ranking({"a": 0, "b": 0, "c": 2}, ["a", "b", "c"], cutoff=3)
    assert rr["reciprocal_rank"] == pytest.approx(1 / 3)


def test_metric_empty_and_all_zero_pools_never_receive_perfect_scores():
    for grades in ({}, {"a": 0}):
        score = evaluation.score_ranking(grades, [])
        assert score == {"ndcg": 0, "precision": 0, "reciprocal_rank": 0, "no_relevant": True}


@pytest.mark.parametrize("grade", [True, -1, 4, 1.0, None, "2", float("nan"), float("inf")])
def test_metric_invalid_grades_are_not_coerced(grade):
    with pytest.raises(ValueError, match="E_EVAL_SCHEMA"):
        evaluation.score_ranking({"a": grade}, ["a"])


def test_metric_missing_or_duplicate_results_are_errors():
    with pytest.raises(ValueError, match="MISSING_JUDGMENT"):
        evaluation.score_ranking({"a": 1}, ["b"])
    with pytest.raises(ValueError, match="DUPLICATE"):
        evaluation.score_ranking({"a": 1}, ["a", "a"])


@pytest.mark.parametrize("same_evidence", [False, True])
def test_relabeling_a_duplicate_document_does_not_gain_another_rank(tmp_path, same_evidence):
    queries, candidates, value = inputs(tmp_path)
    duplicate = {**value["documents"][0], "document_id": "duplicate"}
    if not same_evidence:
        duplicate["title"] += " another result representation"
        duplicate["evidence_sha256"] = hash_json({key: duplicate[key] for key in ("url", "title", "text")})
    value["documents"].append(duplicate)
    value["pools"][0]["document_ids"].append("duplicate")
    value["runs"][0]["document_ids"].append("duplicate")
    write(candidates, value)
    with pytest.raises(ValueError, match="E_EVAL_DUPLICATE"):
        evaluation.prepare(str(queries), str(tmp_path / "denied"), candidates_path=str(candidates))


def test_prepare_is_unjudged_and_never_exports_holdout_text(tmp_path):
    out, queries, candidates, _ = package(tmp_path)
    original = (queries.read_bytes(), candidates.read_bytes())
    pool, method, _, _ = load_package(out)
    assert [q["id"] for q in pool["selection"]] == ["dev1", "reg1"]
    assert method == METHOD
    for path in out.iterdir():
        assert b"SYNTHETIC_SEALED_CANARY" not in path.read_bytes()
        assert b"h_fixture" not in path.read_bytes()
    template, _ = read_json(out / "labels.template.json")
    assert len(template["judgments"]) == 8
    assert all(row["grade"] is None and row["origin"] == "unjudged" and row["assessor_id"] is None for row in template["judgments"])
    assert (queries.read_bytes(), candidates.read_bytes()) == original


def test_unjudged_templates_freeze_and_run_as_blocked_without_overwriting(tmp_path):
    out, _, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path, judged=False)
    freeze_path, gate_path = frozen(out, tmp_path)
    before = {path: path.read_bytes() for path in (label_path, gate_path, freeze_path)}
    result = evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "report"))
    assert result["status"] == "blocked" and result["release_gate"]["status"] == "blocked"
    reasons = result["quality_gate"]["blocked_reasons"]
    assert "human_judgments_required" in reasons and "missing_judgments" in reasons
    assert "missing_threshold_ndcg_at_10_min" in reasons and "missing_environment_hardware" in reasons
    assert result["coverage"]["judged"] == 0 and len(result["coverage"]["missing_judgments"]) == 8
    assert group(result)["metrics"] == {key: None for key in evaluation.METRICS}
    assert all(path.read_bytes() == raw for path, raw in before.items())
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "report"))


def test_synthetic_scoring_reports_each_split_and_never_claims_human_or_release_pass(tmp_path):
    out, _, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path)
    freeze_path, _ = frozen(out, tmp_path, thresholds=True)
    result = evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "report"))
    assert result["status"] == "blocked" and result["release_gate"]["status"] == "blocked"
    assert result["coverage"]["human"] == 0 and result["coverage"]["synthetic"] == 8
    assert group(result)["metrics"]["ndcg_at_10"] == pytest.approx(0.9467676761267002)
    assert group(result)["metrics"]["precision_at_10"] == 0.2
    assert group(result)["metrics"]["mrr_at_10"] == 1
    assert group(result, "regression")["planned_queries"] == 1
    assert any(row["dimension"] == "language" and row["value"] == "zh" for row in result["systems"][0]["groups"])
    assert result["constraint_failures"] is None and result["citation_failures"] is None
    assert result["source_ablation"]["status"] == "blocked"
    assert result["source_ablation"]["blocked_reasons"] == ["missing_ablation_manifest"]


def test_missing_pool_label_blocks_idcg_even_when_missing_document_was_not_returned(tmp_path):
    out, _, _, _ = package(tmp_path)
    label_path, value = labels(out, tmp_path)
    value["judgments"] = [row for row in value["judgments"] if not (row["query_id"] == "dev1" and row["document_id"] == "d2")]
    write(label_path, value)
    freeze_path, _ = frozen(out, tmp_path, thresholds=True)
    result = evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "report"))
    assert result["coverage"]["absent"] == 1
    assert result["coverage"]["judgment_coverage"] == 7 / 8
    assert group(result)["metrics"]["ndcg_at_10"] is None
    assert group(result, "regression")["metrics"]["ndcg_at_10"] is not None
    assert result["status"] == "blocked"


@pytest.mark.parametrize("mode", ["failed", "empty", "omitted"])
def test_failed_empty_and_missing_queries_keep_distinct_denominators(tmp_path, mode):
    queries, candidates, value = inputs(tmp_path)
    row = value["runs"][0]
    if mode == "omitted":
        value["runs"].pop(0)
    else:
        row.update(status=mode, document_ids=[], reason="network_error" if mode == "failed" else None)
    write(candidates, value)
    out = tmp_path / "package"
    evaluation.prepare(str(queries), str(out), candidates_path=str(candidates))
    label_path, _ = labels(out, tmp_path)
    freeze_path, _ = frozen(out, tmp_path, thresholds=True)
    result = evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "report"))
    result_group = group(result)
    assert result_group["planned_queries"] == 1 and result_group["answer_coverage"] == 0
    if mode == "empty":
        assert result_group["query_coverage"] == 1 and result_group["abstention_rate"] == 1
        assert result_group["metrics"] == {key: 0 for key in evaluation.METRICS}
    else:
        assert result_group["query_coverage"] == 0 and result_group["abstention_rate"] == 0
        assert result_group["metrics"] == {key: None for key in evaluation.METRICS}
    assert result["status"] != "passed"


@pytest.mark.parametrize("change", ["query", "split", "evidence", "duplicate_document", "unknown_result", "duplicate_result", "holdout_pool"])
def test_input_identity_and_holdout_fail_before_writing_package(tmp_path, change):
    queries, candidates, value = inputs(tmp_path)
    if change in ("query", "split"):
        source, _ = read_json(queries)
        source["queries"][0][change] = "changed" if change == "query" else "regression"
        write(queries, source)
    elif change == "evidence":
        value["documents"][0]["text"] += " changed"
    elif change == "duplicate_document":
        value["documents"].append(value["documents"][0])
    elif change == "unknown_result":
        value["runs"][0]["document_ids"].append("unknown")
    elif change == "duplicate_result":
        value["runs"][0]["document_ids"].append("d0")
    else:
        value["pools"].append({"query_id": "h_fixture", "document_ids": []})
    write(candidates, value)
    out = tmp_path / "denied"
    with pytest.raises(ValueError):
        evaluation.prepare(str(queries), str(out), candidates_path=str(candidates))
    assert not out.exists()


def test_explicit_holdout_is_not_an_available_mode(tmp_path):
    queries, candidates, _ = inputs(tmp_path)
    with pytest.raises(ValueError, match="E_EVAL_HOLDOUT"):
        evaluation.prepare(str(queries), str(tmp_path / "no"), candidates_path=str(candidates), splits=["holdout"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["evaluation", "prepare", "--candidates", str(candidates), "--splits", "holdout", "--out", str(tmp_path / "no")])
    assert exc.value.code == 2 and not (tmp_path / "no").exists()


@pytest.mark.parametrize("target", ["pool.json", "method.json", "package.json"])
def test_package_byte_drift_cannot_reuse_a_freeze(tmp_path, target):
    out, _, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path)
    freeze_path, _ = frozen(out, tmp_path)
    (out / target).write_bytes((out / target).read_bytes() + b" ")
    with pytest.raises(ValueError, match="E_EVAL_IDENTITY"):
        evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "denied"))
    assert not (tmp_path / "denied").exists()


@pytest.mark.parametrize("change", ["float_cutoff", "bool_grade"])
def test_method_rehash_cannot_hide_a_numeric_type_change(tmp_path, change):
    out, _, _, _ = package(tmp_path)
    method, _ = read_json(out / "method.json")
    if change == "float_cutoff":
        method["cutoff"] = 10.0
    else:
        method["grades"][1] = True
    write(out / "method.json", method)
    manifest, _ = read_json(out / "package.json")
    manifest["method_sha256"] = hashlib.sha256((out / "method.json").read_bytes()).hexdigest()
    write(out / "package.json", manifest)
    with pytest.raises(ValueError, match="E_EVAL_METHOD"):
        load_package(out)


@pytest.mark.parametrize("stage", ["freeze", "run"])
@pytest.mark.parametrize("damage", ["query", "split", "metadata", "capability", "order", "omit_query", "frozen_claim", "version_claim"])
def test_rehashed_package_cannot_claim_an_original_golden_projection(tmp_path, stage, damage):
    queries, candidates, value = inputs(tmp_path)
    original, _ = read_json(queries)
    original["queries"].insert(1, {"id": "dev2", "query": "another synthetic development query",
                                   "category": "general_en", "language": "en", "split": "development"})
    write(queries, original)
    value["queries_sha256"] = load_golden(queries).digest
    value["pools"].append({"query_id": "dev2", "document_ids": ["d0", "d1", "d2", "d3"]})
    value["runs"].append({"system_id": "fixture", "query_id": "dev2", "status": "ok",
                           "document_ids": ["d0"], "reason": None})
    write(candidates, value)
    out = tmp_path / "package"
    evaluation.prepare(str(queries), str(out), candidates_path=str(candidates))
    pool, _ = read_json(out / "pool.json")
    if damage == "query":
        pool["selection"][0]["query"] = "different synthetic query"
    elif damage == "split":
        pool["selection"][0]["split"] = "regression"
    elif damage == "metadata":
        pool["selection"][0]["language"] = "zh"
    elif damage == "capability":
        pool["selection"][0]["good_for"] = ["news"]
    elif damage == "order":
        pool["selection"].reverse()
    elif damage == "omit_query":
        pool["selection"] = [q for q in pool["selection"] if q["id"] != "dev1"]
        for key in ("pools", "runs"):
            pool["input"][key] = [q for q in pool["input"][key] if q["query_id"] != "dev1"]
    elif damage == "frozen_claim":
        pool["dataset"].update(frozen=True, golden_manifest_sha256="a" * 64)
        pool["input"]["golden_manifest_sha256"] = "a" * 64
    else:
        pool["dataset"]["version"] = 1
    write(out / "pool.json", pool)
    manifest, _ = read_json(out / "package.json")
    manifest["pool_sha256"] = hashlib.sha256((out / "pool.json").read_bytes()).hexdigest()
    write(out / "package.json", manifest)
    # The package is internally consistent even though its original-file claim is false.
    _, _, _, bindings = load_package(out)
    gate, _ = read_json(out / "gate.template.json")
    gate["bindings"] = bindings
    gate_path = write(tmp_path / "gate.json", gate)
    denied = tmp_path / "denied"
    if stage == "freeze":
        with pytest.raises(ValueError, match="E_EVAL_IDENTITY"):
            evaluation.freeze(str(out), str(gate_path), str(denied), queries_path=str(queries))
    else:
        unverified = tmp_path / "unverified"
        evaluation.freeze(str(out), str(gate_path), str(unverified))
        frozen_value, _ = read_json(unverified / "freeze.json")
        frozen_value.update(status="frozen", blocked_reasons=[], golden_verification=golden_projection_identity(pool))
        write(unverified / "freeze.json", frozen_value)
        with pytest.raises(ValueError, match="E_EVAL_IDENTITY"):
            evaluation.run(str(out), str(unverified / "freeze.json"), str(out / "labels.template.json"),
                           str(denied), queries_path=str(queries))
    assert not denied.exists()


def test_original_query_verification_is_required_at_freeze_and_each_run(tmp_path):
    out, queries, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path)
    gate, _ = read_json(out / "gate.template.json")
    for key in gate["thresholds"]:
        gate["thresholds"][key] = 1 if key.endswith("_max") else 0
    gate["environment"].update(id="synthetic-fixture", hardware="synthetic-test-machine", command="synthetic-test-only")
    gate["review"].update(reviewer_id="synthetic-fixture", reviewed_at="2026-10-20T00:00:00Z", evidence_sha256="f" * 64)
    gate_path = write(tmp_path / "gate.json", gate)
    missing = evaluation.freeze(str(out), str(gate_path), str(tmp_path / "unverified"))
    assert missing["status"] == "blocked" and missing["blocked_reasons"] == ["original_golden_not_verified"]
    verified = evaluation.freeze(str(out), str(gate_path), str(tmp_path / "verified"), queries_path=str(queries))
    assert verified["status"] == "frozen" and verified["golden_verification"]["status"] == "verified"
    assert verified["golden_verification"]["selected_count"] == 2
    assert verified["golden_verification"]["frozen"] is False  # The synthetic fixture is still not the golden dataset.
    report = evaluation.run(str(out), str(tmp_path / "verified" / "freeze.json"), str(label_path), str(tmp_path / "missing-run"))
    assert "original_golden_not_verified" in report["quality_gate"]["blocked_reasons"]
    verified_run = evaluation.run(str(out), str(tmp_path / "verified" / "freeze.json"), str(label_path),
                                 str(tmp_path / "verified-run"), queries_path=str(queries))
    assert verified_run["status"] == "blocked" and verified_run["golden_verification"] == verified["golden_verification"]
    assert not any("golden_not_verified" in reason for reason in verified_run["quality_gate"]["blocked_reasons"])
    late_verification = evaluation.run(str(out), str(tmp_path / "unverified" / "freeze.json"), str(label_path),
                                      str(tmp_path / "late-verification"), queries_path=str(queries))
    assert "original_golden_not_verified_at_freeze" in late_verification["quality_gate"]["blocked_reasons"]
    for directory in (tmp_path / "verified", tmp_path / "verified-run"):
        for path in directory.iterdir():
            assert b"SYNTHETIC_SEALED_CANARY" not in path.read_bytes()


def test_original_query_byte_drift_after_verified_freeze_is_rejected(tmp_path):
    out, queries, _, _ = package(tmp_path)
    evaluation.freeze(str(out), str(out / "gate.template.json"), str(tmp_path / "frozen"), queries_path=str(queries))
    queries.write_bytes(queries.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="E_EVAL_IDENTITY"):
        evaluation.run(str(out), str(tmp_path / "frozen" / "freeze.json"), str(out / "labels.template.json"),
                       str(tmp_path / "denied"), queries_path=str(queries))
    assert not (tmp_path / "denied").exists()


def test_runtime_drift_and_changed_frozen_gate_are_rejected(tmp_path, monkeypatch):
    out, _, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path)
    freeze_path, _ = frozen(out, tmp_path)
    runtime = evaluation.runtime_identity()
    runtime["python"] += "-changed"
    monkeypatch.setattr(evaluation, "runtime_identity", lambda: runtime)
    with pytest.raises(ValueError, match="runtime changed"):
        evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "denied"))
    monkeypatch.undo()
    value, _ = read_json(freeze_path)
    value["gate"]["thresholds"]["ndcg_at_10_min"] = 0
    write(freeze_path, value)
    with pytest.raises(ValueError, match="gate content changed"):
        evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "denied"))


@pytest.mark.parametrize("damage", ["duplicate", "bool_grade", "different_evidence", "wrong_corpus", "human_without_assessor"])
def test_label_conflicts_and_false_completeness_are_rejected(tmp_path, damage):
    out, _, _, _ = package(tmp_path)
    label_path, value = labels(out, tmp_path)
    if damage == "duplicate":
        value["judgments"].append(value["judgments"][0])
    elif damage == "bool_grade":
        value["judgments"][0]["grade"] = True
    elif damage == "different_evidence":
        value["judgments"][0]["evidence_sha256"] = "0" * 64
    elif damage == "wrong_corpus":
        value["bindings"]["corpus_sha256"] = "0" * 64
    else:
        value["judgments"][0].update(origin="human", assessor_id=None)
    write(label_path, value)
    freeze_path, _ = frozen(out, tmp_path)
    with pytest.raises(ValueError):
        evaluation.run(str(out), str(freeze_path), str(label_path), str(tmp_path / "denied"))
    assert not (tmp_path / "denied").exists()


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'[]', b'\xff'])
def test_strict_json_rejects_duplicate_nonfinite_and_nonobject_data(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read_json(path)


def test_nested_oversized_and_symlink_inputs_are_rejected(tmp_path):
    path = write(tmp_path / "input.json", {"x": "a" * 100})
    with pytest.raises(ValueError, match="LIMIT"):
        read_json(path, maximum=16)
    nested = {}
    for _ in range(34):
        nested = {"x": nested}
    write(path, nested)
    with pytest.raises(ValueError, match="nesting"):
        read_json(path)
    link = tmp_path / "link.json"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("host does not permit unprivileged symlinks")
    with pytest.raises(ValueError, match="symbolic"):
        read_json(link)


def test_known_threshold_failure_is_failed_even_when_other_requirements_are_blocked(tmp_path):
    out, _, _, _ = package(tmp_path)
    label_path, _ = labels(out, tmp_path)
    gate, _ = read_json(out / "gate.template.json")
    gate["thresholds"]["ndcg_at_10_min"] = 0.99
    gate_path = write(tmp_path / "gate.json", gate)
    evaluation.freeze(str(out), str(gate_path), str(tmp_path / "frozen"))
    result = evaluation.run(str(out), str(tmp_path / "frozen" / "freeze.json"), str(label_path), str(tmp_path / "report"))
    assert result["status"] == "failed"
    assert any(check["check"] == "ndcg_at_10_min" and check["status"] == "failed" for check in result["quality_gate"]["checks"])
    assert result["release_gate"]["status"] == "blocked"


def test_nonregression_reuses_labels_and_idcg_only_when_the_frozen_corpus_is_unchanged(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    out, queries, _, value = package(first)
    label_path, _ = labels(out, first)
    freeze_path, _ = frozen(out, first, thresholds=True)
    baseline = evaluation.run(str(out), str(freeze_path), str(label_path), str(first / "report"))
    second = tmp_path / "second"
    second.mkdir()
    changed = copy.deepcopy(value)
    changed["ranking_identity"]["code_sha256"] = "3" * 64
    for row in changed["runs"]:
        row["document_ids"] = ["d3", "d2", "d1"]
    candidates = write(second / "candidates.json", changed)
    new_package = second / "package"
    evaluation.prepare(str(queries), str(new_package), candidates_path=str(candidates))
    freeze_path, _ = frozen(new_package, second, thresholds=True, baseline=first / "report" / "evaluation_report.json")
    report = evaluation.run(str(new_package), str(freeze_path), str(label_path), str(second / "report"),
                            baseline_path=str(first / "report" / "evaluation_report.json"))
    assert baseline["bindings"]["corpus_sha256"] == report["bindings"]["corpus_sha256"]
    assert baseline["bindings"]["pool_sha256"] != report["bindings"]["pool_sha256"]
    assert report["status"] == "failed"
    assert any(check["check"] == "ndcg_at_10_max_drop" and check["observed"] > 0 for check in report["quality_gate"]["checks"])
    assert "baseline_quality_gate_not_passed" in report["quality_gate"]["blocked_reasons"]


def test_cli_exit_statuses_do_not_confuse_prepared_templates_with_passed_quality(tmp_path, monkeypatch, capsys):
    queries, candidates, _ = inputs(tmp_path)
    out = tmp_path / "package"
    def forbidden(*args, **kwargs):
        pytest.fail("offline evaluation touched runtime configuration or network")
    monkeypatch.setattr(cli.config, "load", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    assert cli.main(["evaluation", "prepare", "--queries", str(queries), "--candidates", str(candidates), "--out", str(out)]) == 0
    assert cli.main(["evaluation", "freeze", "--package", str(out), "--gate", str(out / "gate.template.json"), "--out", str(tmp_path / "frozen")]) == 3
    assert cli.main(["evaluation", "run", "--package", str(out), "--freeze", str(tmp_path / "frozen" / "freeze.json"),
                     "--labels", str(out / "labels.template.json"), "--out", str(tmp_path / "report")]) == 3
    stdout = capsys.readouterr().out
    assert "SYNTHETIC_SEALED_CANARY" not in stdout and '"release_gate"' in stdout


def test_standalone_module_is_executable(tmp_path):
    process = subprocess.run([sys.executable, "-m", "bzs.evaluation", "--help"], capture_output=True, text=True, timeout=10)
    assert process.returncode == 0
    assert "prepare" in process.stdout and "freeze" in process.stdout and "run" in process.stdout


def probe_recording(tmp_path):
    from bzs.probe_format import parser_identity, safe_response_metadata
    from bzs.probe_state import record_body
    from bzs.worker.fetch import FetchResult

    queries, _, _ = inputs(tmp_path)
    golden = load_golden(queries)
    selected = [q for q in golden.queries if q["split"] != "holdout"]
    out = tmp_path / "probe"
    out.mkdir()
    options = {"source_ids": ["wiki"]}
    plan = [{"item_id": f"p{i + 1:06d}", "source": "wiki", "query_id": query["id"], "query": query["query"],
             "language": query["language"], "category": query["category"], "split": query["split"],
             "state": "completed" if i == 0 else "uncertain", "reason": None if i == 0 else "interrupted"}
            for i, query in enumerate(selected)]
    entries = [{"title": f"Synthetic Page {i}", "snippet": f"Synthetic description {i}"} for i in range(1, 11)]
    body = json.dumps({"query": {"search": entries}}).encode("utf-8")
    response = FetchResult(status="ok", url="https://source.invalid/", final_url="https://source.invalid/",
                           http_status=200, headers={"content-type": "application/json"}, body=body)
    results = [{"url": f"https://en.wikipedia.org/wiki/Synthetic_Page_{i}", "title": entry["title"],
                "snippet": entry["snippet"], "rank": i} for i, entry in enumerate(entries, 1)]
    metadata = {**safe_response_metadata(response), **record_body(out, "a000001", body)}
    rows = [{"item_id": plan[0]["item_id"], "source": "wiki", "query_id": "dev1", "query": selected[0]["query"],
             "attempt_state": "completed", "status": "ok", "hits": 10, "results": results,
             "response": metadata, "raw": metadata["raw_path"]},
            {"item_id": plan[1]["item_id"], "source": "wiki", "query_id": "reg1", "query": selected[1]["query"],
             "attempt_state": "uncertain", "status": "uncertain", "hits": 0, "results": []}]
    report = {"schema_version": 2, "run_id": "synthetic-evaluation-recording", "identity": {
        "queries_sha256": golden.digest, "golden_manifest_sha256": golden.manifest_digest, "manifest_sha256": "1" * 64,
        "options_sha256": hash_json(options), "parser": parser_identity()}, "options": options,
        "sources": {"wiki": {"id": "wiki", "adapter_id": "wikipedia_api", "params": {"lang": "en"}}},
        "plan": plan, "rows": rows, "environment": {"test_fixture": True}}
    return queries, write(out / "probe_report.json", report), report


def test_probe_import_replays_all_ten_results_without_a_fetcher_and_retains_uncertain(tmp_path, monkeypatch):
    queries, report_path, _ = probe_recording(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("probe import attempted network access")
    monkeypatch.setattr("bzs.worker.fetch.Fetcher.__init__", forbidden)
    # urllib3's first import probes local IPv6 socket support. Block actual
    # connections and DNS, while allowing that non-outbound capability check.
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.socket.connect_ex", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    summary = evaluation.prepare(str(queries), str(tmp_path / "package"), probe_path=str(report_path))
    pool, _, _, _ = load_package(tmp_path / "package")
    assert summary["judgment_count"] == 10 and len(pool["input"]["documents"]) == 10
    assert pool["provenance"]["kind"] == "probe_source_top10" and not pool["provenance"]["source_fusion"]
    assert pool["input"]["origin"] == "synthetic"
    assert pool["input"]["runs"][0]["status"] == "ok" and len(pool["input"]["runs"][0]["document_ids"]) == 10
    assert pool["input"]["runs"][1]["status"] == "unavailable"
    assert pool["input"]["runs"][1]["reason"] == "probe_uncertain"


@pytest.mark.parametrize("damage", ["fourth_result", "missing_response", "body_hash", "parser_identity", "options_identity", "plan_identity", "holdout"])
def test_probe_import_refuses_changed_or_incomplete_evidence(tmp_path, damage):
    queries, report_path, report = probe_recording(tmp_path)
    if damage == "fourth_result":
        report["rows"][0]["results"][3]["title"] = "changed"
    elif damage == "missing_response":
        report["rows"][0].pop("response")
    elif damage == "body_hash":
        report["rows"][0]["response"]["body_sha256"] = "0" * 64
    elif damage == "parser_identity":
        report["identity"]["parser"]["code_sha256"] = "0" * 64
    elif damage == "options_identity":
        report["options"]["source_ids"].append("other")
    elif damage == "plan_identity":
        report["rows"][0]["query"] = "different query"
    else:
        report["plan"][0]["query_id"] = "h_fixture"
    write(report_path, report)
    before = report_path.read_bytes()
    with pytest.raises(ValueError):
        evaluation.prepare(str(queries), str(tmp_path / "denied"), probe_path=str(report_path))
    assert not (tmp_path / "denied").exists() and report_path.read_bytes() == before


def test_complete_probe_network_failure_is_not_an_empty_response(tmp_path):
    queries, report_path, report = probe_recording(tmp_path)
    row = report["rows"][0]
    row.update(status="network_error", hits=0, results=[])
    row.pop("response")
    row.pop("raw")
    write(report_path, report)
    evaluation.prepare(str(queries), str(tmp_path / "package"), probe_path=str(report_path))
    pool, _, _, _ = load_package(tmp_path / "package")
    assert pool["input"]["runs"][0]["status"] == "failed"
    assert pool["input"]["runs"][0]["reason"] == "network_error"
    assert not pool["input"]["documents"]


@pytest.mark.parametrize("value", [True, "0.5", -0.01, 1.01])
def test_invalid_thresholds_are_rejected_not_coerced(tmp_path, value):
    out, _, _, _ = package(tmp_path)
    gate, _ = read_json(out / "gate.template.json")
    gate["thresholds"]["ndcg_at_10_min"] = value
    write(tmp_path / "gate.json", gate)
    with pytest.raises(ValueError, match="thresholds"):
        evaluation.freeze(str(out), str(tmp_path / "gate.json"), str(tmp_path / "denied"))
    assert not (tmp_path / "denied").exists()


def test_forged_freeze_success_marker_does_not_override_actual_missing_requirements(tmp_path):
    out, _, _, _ = package(tmp_path)
    freeze_path, _ = frozen(out, tmp_path)
    value, _ = read_json(freeze_path)
    value.update(status="frozen", blocked_reasons=[])
    write(freeze_path, value)
    report = evaluation.run(str(out), str(freeze_path), str(out / "labels.template.json"), str(tmp_path / "report"))
    assert report["status"] == "blocked" and "human_judgments_required" in report["quality_gate"]["blocked_reasons"]


def test_cli_real_process_prepare_freeze_and_unjudged_run(tmp_path):
    queries, candidates, _ = inputs(tmp_path)
    out = tmp_path / "package"
    commands = [(["prepare", "--queries", str(queries), "--candidates", str(candidates), "--out", str(out)], 0),
                (["freeze", "--queries", str(queries), "--package", str(out), "--gate", str(out / "gate.template.json"), "--out", str(tmp_path / "frozen")], 3),
                (["run", "--queries", str(queries), "--package", str(out), "--freeze", str(tmp_path / "frozen" / "freeze.json"),
                  "--labels", str(out / "labels.template.json"), "--out", str(tmp_path / "report")], 3)]
    for arguments, expected in commands:
        process = subprocess.run([sys.executable, "-m", "bzs.evaluation", *arguments], capture_output=True, text=True, timeout=15)
        assert process.returncode == expected, process.stderr
        assert "SYNTHETIC_SEALED_CANARY" not in process.stdout + process.stderr
    report, _ = read_json(tmp_path / "report" / "evaluation_report.json")
    assert report["status"] == "blocked" and report["network_requests"] == 0 and report["model_calls"] == 0
    assert report["golden_verification"]["status"] == "verified"
