"""Offline unit tests for the S1 acceptance harness (tools/accept_s1.py).

No network, no service start, no source enabling: every fixture is an
in-memory manifest/report dict. The S0/S1 boundary rules stay intact — these
tests never touch config/sources.json or a real BZS_HOME.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
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
    lines = blueprint.splitlines()
    assert [c["id"] for c in accept_s1.CRITERIA] == [f"c{i}" for i in range(1, 10)]
    for criterion in accept_s1.CRITERIA:
        assert 65 <= criterion["blueprint_line"] <= 76, criterion["id"]
        # The quote must EQUAL the cited blueprint line, not merely be contained
        # in the file: a truncated quote (or a prefix of the real line) would
        # also satisfy a substring check and let the enumeration drift.
        assert criterion["quote"] == lines[criterion["blueprint_line"] - 1], \
            f"{criterion['id']} quote is not byte-equal to blueprint line {criterion['blueprint_line']}"
        assert lines.count(criterion["quote"]) == 1, \
            f"{criterion['id']} quote must match exactly one full blueprint line"
        assert criterion["title"]
    assert "### S1 完成标准" in blueprint


# --------------------------------------------- c5 handoff review binding

def test_c5_quote_carries_the_claude_s1_review_binding():
    # Blueprint L71: the defect-fix-merge question corresponds to
    # ../handoff/CLAUDE_S1_REVIEW_20261018.md and the original package's B1–B7
    # labels cannot substitute this round's verification. A truncated quote
    # loses that binding (review finding 1).
    c5 = next(c for c in accept_s1.CRITERIA if c["id"] == "c5")
    assert "../handoff/CLAUDE_S1_REVIEW_20261018.md" in c5["quote"]
    assert "B1–B7" in c5["quote"] and "不能用原包 B1–B7 标签替代本轮核验" in c5["quote"]


def test_c5_blocked_evidence_surfaces_the_claude_s1_review_binding():
    grades = accept_s1.Grades()
    accept_s1._phase_ci(grades, None, {"commit": "deadbeef", "dirty": False})
    c5 = next(r for r in grades.results({}) if r["id"] == "c5")
    assert c5["status"] == "BLOCKED"  # no --ci-run-url: no false PASS
    surfaced = [c5["reason"]] + c5["evidence"]
    assert any("CLAUDE_S1_REVIEW_20261018.md" in line and "B1–B7" in line for line in surfaced), surfaced


def test_c5_pass_on_a_supplied_run_is_an_attestation_and_keeps_the_binding():
    grades = accept_s1.Grades()
    accept_s1._phase_ci(grades, "https://ci.example/runs/1", {"commit": "deadbeef", "dirty": False})
    c5 = next(r for r in grades.results({}) if r["id"] == "c5")
    assert c5["status"] == "PASS"
    pass_notes = [note for status, note in grades.parts["c5"] if status == accept_s1.STATUS_PASS]
    assert any("supplied" in note for note in pass_notes), pass_notes  # attestation wording
    assert any("supplied" in line and "ci.example" in line for line in c5["evidence"]), c5["evidence"]
    assert any("CLAUDE_S1_REVIEW_20261018.md" in line for line in c5["evidence"]), c5["evidence"]


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


# --------------------------------------------- c6 mcp-config sub-check

class _ReplayMcpConfigCli:
    """Replays a stdout buffer captured from the real `bzs mcp-config`."""

    def __init__(self, stdout: str):
        self._stdout = stdout
        self.argv = None

    def main(self, argv):
        self.argv = list(argv)
        sys.stdout.write(self._stdout)
        return 0


def _capture_real_mcp_config_stdout(home: Path) -> str:
    """Run the REAL `bzs mcp-config` in an isolated BZS_HOME and capture stdout.

    Offline and side-effect free: the command only reads BZS_HOME and
    sys.executable (no config, no service, no network). Also pins the test run
    to this worktree's bzs package, per the shared isolation rules.
    """
    import bzs
    assert Path(bzs.__file__).resolve().is_relative_to(accept_s1.REPO_ROOT), \
        f"bzs must be imported from this worktree, got {bzs.__file__}"
    import bzs.cli as bzs_cli
    os.environ["BZS_HOME"] = str(home)
    try:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assert bzs_cli.main(["mcp-config"]) == 0
    finally:
        os.environ.pop("BZS_HOME", None)
    return captured.getvalue()


def test_c6_mcp_config_subcheck_parses_the_real_multiline_output(tmp_path):
    real_stdout = _capture_real_mcp_config_stdout(tmp_path / "capture-home")
    # The real command prints json.dumps(..., indent=2): multi-line, first line `{`.
    assert real_stdout.splitlines()[0].strip() == "{"
    assert len(real_stdout.splitlines()) > 3
    assert json.loads(real_stdout)["mcpServers"]["bzs861"]

    replay = _ReplayMcpConfigCli(real_stdout)
    grades = accept_s1.Grades()
    accept_s1._check_mcp_config(grades, cli=replay)
    assert replay.argv == ["mcp-config"]
    statuses = [status for status, _ in grades.parts["c6"]]
    assert statuses == ["PASS"], grades.parts["c6"]  # graded on content, not blocked on a parse error
    assert any("mcp-config" in line for line in grades.evidence["c6"])


def test_c6_mcp_config_subcheck_fails_on_content_not_on_parse(tmp_path):
    real_stdout = _capture_real_mcp_config_stdout(tmp_path / "capture-home")
    assert "bzs861" in real_stdout  # sanity: the real payload does carry the entry
    missing_entry = json.dumps({"mcpServers": {"other-server": {}}}, indent=2) + "\n"
    grades = accept_s1.Grades()
    accept_s1._check_mcp_config(grades, cli=_ReplayMcpConfigCli(missing_entry))
    statuses = [status for status, _ in grades.parts["c6"]]
    assert statuses == ["FAIL"], grades.parts["c6"]  # content verdict, no BLOCKED-on-parse
    assert "bzs861 server entry" in grades.parts["c6"][0][1]
