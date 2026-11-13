"""Offline unit tests for the S1 acceptance harness (tools/accept_s1.py).

No network, no service start, no source enabling: every fixture is an
in-memory manifest/report dict. The S0/S1 boundary rules stay intact — these
tests never touch config/sources.json or a real BZS_HOME.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'accept_s1', Path(__file__).resolve().parents[2] / 'tools' / 'accept_s1.py')
accept_s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accept_s1)

from tests import fakeweb  # noqa: E402  (python/tests/fakeweb.py)


def marginalia_spec(*, enabled=True, key_env="BZS_MARGINALIA_KEY", adapter="marginalia_api"):
    """A fully reviewed marginalia spec built from the offline fixture template."""
    source = copy.deepcopy(fakeweb.manifest(8099)["sources"][0])
    source.update(id="marginalia", provider="marginalia", index_family="marginalia",
                  capability="web", enabled=enabled)
    source["params"] = {"adapter": adapter, "base_url": "https://api2.marginalia-search.com/search"}
    if key_env is not None:
        source["params"]["key_env"] = key_env
    source["contract"].update(id="marginalia", provider_id="marginalia", index_family="marginalia",
                              adapter_id=adapter, enabled=enabled, logical_service_id="marginalia",
                              endpoint="https://api2.marginalia-search.com/search")
    source["contract"]["limits"] = dict(source["contract"]["limits"], shared_budget_key="marginalia")
    return source


def probe_report(manifest_sha="a" * 64, fixture=False, ok_sources=("ddg", "bing")):
    rows = [{"item_id": f"p{index:06d}", "source": source, "status": "ok", "hits": 3, "attempt_state": "completed",
             "results": [{"url": "https://example.org/", "title": "t", "snippet": "s", "rank": 1}]}
            for index, source in enumerate(ok_sources, start=1)]
    return {"schema_version": 2, "identity": {"manifest_sha256": manifest_sha},
            "environment": {"test_fixture": fixture}, "rows": rows}


# ---------------------------------------------------------------- criteria

def test_criterion_enumeration_is_bound_to_the_blueprint():
    blueprint = (Path(__file__).resolve().parents[2] / accept_s1.BLUEPRINT_REL).read_text(encoding="utf-8")
    assert [c["id"] for c in accept_s1.CRITERIA] == [f"c{i}" for i in range(1, 10)]
    for criterion in accept_s1.CRITERIA:
        assert 65 <= criterion["blueprint_line"] <= 76, criterion["id"]
        assert criterion["quote"] in blueprint, f"{criterion['id']} quote drifted from the blueprint"
        assert criterion["title"]
    assert "### S1 完成标准" in blueprint


# ------------------------------------------------- D15 public-key detection

def test_enabled_marginalia_without_a_key_is_the_public_fallback():
    reason = accept_s1.marginalia_public_key_fallback(marginalia_spec(), {})
    assert reason is not None, "an enabled marginalia source with no key IS the adapter's public fallback"
    assert "public" in reason and "BZS_MARGINALIA_KEY" in reason


def test_enabled_marginalia_with_the_users_own_key_passes():
    assert accept_s1.marginalia_public_key_fallback(
        marginalia_spec(), {"BZS_MARGINALIA_KEY": "users-own-key-123"}) is None


def test_enabled_marginalia_with_the_literal_public_key_is_rejected():
    reason = accept_s1.marginalia_public_key_fallback(marginalia_spec(), {"BZS_MARGINALIA_KEY": "public"})
    assert reason is not None and "public" in reason


def test_detection_honours_the_configured_key_env_name():
    reason = accept_s1.marginalia_public_key_fallback(
        marginalia_spec(key_env="BZS_ALT_KEY"), {"BZS_MARGINALIA_KEY": "users-own-key-123"})
    assert reason is not None and "BZS_ALT_KEY" in reason


def test_disabled_marginalia_is_out_of_scope_the_public_key_is_s0_only():
    assert accept_s1.marginalia_public_key_fallback(marginalia_spec(enabled=False), {}) is None


def test_non_marginalia_sources_are_ignored():
    other = marginalia_spec(adapter="wikipedia_api")
    other["id"] = other["provider"] = other["index_family"] = "wikipedia_en"
    other["params"] = {"adapter": "wikipedia_api", "base_url": "https://en.wikipedia.org/w/api.php"}
    other["contract"].update(id="wikipedia_en", provider_id="wikipedia_en", index_family="wikipedia_en",
                             adapter_id="wikipedia_api", logical_service_id="wikipedia_en",
                             endpoint="https://en.wikipedia.org/w/api.php")
    assert accept_s1.marginalia_public_key_fallback(other, {}) is None


# ------------------------------------------------------- manifest analysis

def test_manifest_analysis_separates_enabled_approved_and_web():
    sources = fakeweb.manifest(8099)["sources"]
    analysis = accept_s1.manifest_analysis(sources)
    assert analysis["enabled"] == ["ddg", "bing", "blocked", "wiki"]
    assert analysis["approved"] == ["ddg", "bing", "blocked", "wiki"]
    assert "ddg" in analysis["approved_web"] and "wiki" not in analysis["approved_web"]


def test_manifest_analysis_drops_sources_whose_review_is_not_approved():
    sources = copy.deepcopy(fakeweb.manifest(8099)["sources"])
    sources[0]["contract"]["review"]["status"] = "candidate"
    analysis = accept_s1.manifest_analysis(sources)
    assert "ddg" in analysis["enabled"] and "ddg" not in analysis["approved"]


def test_manifest_analysis_ignores_disabled_sources():
    sources = copy.deepcopy(fakeweb.manifest(8099)["sources"])
    for source in sources:
        source["enabled"] = False
    assert accept_s1.manifest_analysis(sources) == {"enabled": [], "approved": [], "approved_web": []}


# -------------------------------------------------------- probe evidence

def test_probe_report_issues_accept_binding_real_evidence():
    assert accept_s1.probe_report_issues(probe_report(), "a" * 64, ["ddg", "bing"]) == []


def test_probe_report_issues_reject_test_fixture_evidence():
    issues = accept_s1.probe_report_issues(probe_report(fixture=True), "a" * 64, ["ddg"])
    assert any("test fixtures" in issue for issue in issues)


def test_probe_report_issues_reject_foreign_manifest_binding():
    issues = accept_s1.probe_report_issues(probe_report(manifest_sha="b" * 64), "a" * 64, ["ddg"])
    assert any("manifest" in issue for issue in issues)


def test_probe_report_issues_require_real_ok_attempts_per_source():
    issues = accept_s1.probe_report_issues(probe_report(ok_sources=("ddg",)), "a" * 64, ["ddg", "bing"])
    assert any("'bing'" in issue for issue in issues)


# ----------------------------------------------- aggregation and exit rule

def test_aggregate_fail_beats_blocked_beats_pass():
    assert accept_s1.aggregate(["PASS", "FAIL"]) == "FAIL"
    assert accept_s1.aggregate(["PASS", "BLOCKED"]) == "BLOCKED"
    assert accept_s1.aggregate(["PASS", "PASS"]) == "PASS"
    assert accept_s1.aggregate([]) == "BLOCKED"


def test_exit_code_is_zero_only_when_all_nine_pass():
    assert accept_s1.exit_code(["PASS"] * 9) == 0
    assert accept_s1.exit_code(["PASS"] * 8 + ["FAIL"]) == 1
    assert accept_s1.exit_code(["PASS"] * 8 + ["BLOCKED"]) == 3
    with pytest.raises(ValueError):
        accept_s1.exit_code(["PASS"] * 8)
    with pytest.raises(ValueError):
        accept_s1.exit_code(["PASS"] * 8 + ["UNKNOWN"])


# ------------------------------------------------------ table and artifact

def _results(statuses):
    assert len(statuses) == 9
    return [{"id": criterion["id"], "status": status, "reason": f"r{criterion['id']}", "evidence": ["e"],
             "config_identity": {"commit": "deadbeef"}} for criterion, status in zip(accept_s1.CRITERIA, statuses)]


def test_render_table_lists_every_criterion_and_status():
    table = accept_s1.render_table(_results(["PASS"] * 8 + ["FAIL"]))
    for criterion in accept_s1.CRITERIA:
        assert criterion["id"] in table
    assert table.count("| PASS |") == 8 and "| FAIL |" in table
    assert "exit 0 only if all 9 PASS" in table


def test_build_artifact_shape_and_summary():
    artifact = accept_s1.build_artifact({"bzs": {"commit": "x"}, "config": {}, "run": {}},
                                        _results(["PASS"] * 7 + ["BLOCKED", "FAIL"]))
    assert artifact["schema_version"] == accept_s1.ARTIFACT_SCHEMA_VERSION
    assert artifact["tool"] == "tools/accept_s1.py"
    assert artifact["blueprint"] == {"path": accept_s1.BLUEPRINT_REL, "section": "### S1 完成标准"}
    assert [c["id"] for c in artifact["criteria"]] == [f"c{i}" for i in range(1, 10)]
    assert artifact["summary"] == {"pass": 7, "fail": 1, "blocked": 1}
    assert artifact["exit_code"] == 1
    assert all(set(c) >= {"id", "status", "reason", "evidence", "config_identity"} for c in artifact["criteria"])


def test_build_artifact_rejects_enumeration_drift_and_bad_status():
    with pytest.raises(ValueError, match="nine criteria"):
        accept_s1.build_artifact({}, _results(["PASS"] * 9)[:8])
    drifted = _results(["PASS"] * 9)
    drifted[3]["id"] = "cX"
    with pytest.raises(ValueError):
        accept_s1.build_artifact({}, drifted)
    bad = _results(["PASS"] * 9)
    bad[2]["status"] = "MAYBE"
    with pytest.raises(ValueError, match="invalid criterion status"):
        accept_s1.build_artifact({}, bad)


def test_grades_results_compose_reason_from_failing_parts():
    grades = accept_s1.Grades()
    grades.add("c1", "PASS", "ok")
    grades.add("c1", "FAIL", "D15 violation: public key in use")
    grades.add("c2", "BLOCKED", "no network")
    results = grades.results({"manifest_sha256": "a" * 64, "commit": "deadbeef"})
    assert [r["status"] for r in results[:2]] == ["FAIL", "BLOCKED"]
    assert results[0]["status"] == "FAIL" and "D15 violation" in results[0]["reason"]
    assert results[1]["status"] == "BLOCKED" and "no network" in results[1]["reason"]
    assert results[2]["status"] == "BLOCKED"  # untouched criteria cannot silently pass
    assert all(r["config_identity"]["manifest_sha256"] == "a" * 64 for r in results)
    assert json.dumps(results)  # serializable artifact payload
