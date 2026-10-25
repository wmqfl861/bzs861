"""Bind final local results to exact current sources, binaries and retained evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import runpy
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name: str) -> dict:
    return json.loads((OUT / name).read_bytes())


def save(name: str, value: object) -> None:
    with (OUT / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def native_summaries(text: str) -> list[dict]:
    results = []
    patterns = (
        r"^(\d+) (?:[a-z ]+ )?tests(?:,| /) (\d+) assertions(?:,| /) 0 failures(?:, .*|$)",
        r"^(\d+) cases, (\d+) assertions, 0 failed$",
        r"^(\d+)/\d+ tests, (\d+) assertions$",
        r"^Contract tests: (\d+) passed, 0 failed; .*?, (\d+) assertions$",
    )
    for line in text.splitlines():
        if line.startswith('{"tests":'):
            value = json.loads(line)
            assert value["tests"] == value["passed"]
            results.append({"cases": value["tests"], "assertions": value["assertions"], "summary": line})
            continue
        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                results.append({"cases": int(match[1]), "assertions": int(match[2]), "summary": line})
                break
    return results


def record(name: str, files: dict, binaries: dict, build: bool = False) -> dict:
    path = OUT / name
    item = json.loads(path.read_bytes())
    assert item["execution_record_valid"] and item["exit_code"] == 0, name
    assert item["source_inputs_unchanged"] and not item["changed_inputs"], name
    assert not item["watchdog_expired"] and item["build_preparation"] == build, name
    assert sha(path.parent / item["log"]) == item["log_sha256"], name
    assert sha(path.parent / item["inputs"]) == item["inputs_sha256"], name
    assert json.loads((path.parent / item["inputs"]).read_bytes())["files"] == files, name
    if not build:
        assert item["binaries_before"] == item["binaries_after"] == binaries, name
    return item


def reference(name: str) -> dict:
    return {"path": name, "sha256": sha(OUT / name)}


def main() -> None:
    recorder = runpy.run_path(str(OUT / "run.py"))
    files, binaries = recorder["inputs"](), recorder["binaries"]()
    baseline = read("baseline.json")
    ctest = record("final/ctest-v2-command.json", files, binaries)
    xml_path = OUT / "final/ctest-v2-junit.xml"
    xml = ET.parse(xml_path).getroot()
    assert int(xml.attrib["tests"]) == len(xml.findall("testcase")) == 28
    assert all(int(xml.attrib[key]) == 0 for key in ("failures", "disabled", "skipped"))
    detail_path = OUT / "final/ctest-v2-detail.log"
    detail = detail_path.read_text()
    assert not re.search(r"\b[1-9]\d*\s+skipped\b|\bOK\s*\(skipped=[1-9]|\b(?:SKIP|SKIPPED):", detail)
    summaries = native_summaries(detail)
    assert len(summaries) == 16
    native_cases = sum(item["cases"] for item in summaries)
    native_assertions = sum(item["assertions"] for item in summaries)
    assert (native_cases, native_assertions) == (544, 20397)
    direct = [item for item in summaries if "direct shared tests" in item["summary"]]
    assert len(direct) == 1 and (direct[0]["cases"], direct[0]["assertions"]) == (23, 3636)
    python = re.findall(r"^(\d+) passed in ([\d.]+)s$", detail, re.MULTILINE)
    assert len(python) == 1 and int(python[0][0]) == 1294

    debug_build = record("build/candidate-v5-command.json", files, binaries, build=True)
    asan_build = record("build/asan-v2-command.json", files, binaries, build=True)
    for name, value in binaries.items():
        source = debug_build if name.startswith("build/") else asan_build
        assert source["binaries_after"][name] == value, name
    targets = []
    replacements = {"bzs_revalidation_tests", "bzs_cache_freshness_tests"}
    presets = json.loads((ROOT / "CMakePresets.json").read_bytes())
    configured_targets = next(item["targets"] for item in presets["buildPresets"] if item["name"] == "asan-core")
    for target in sorted(configured_targets):
        revision = "v5" if target in replacements else "v4"
        path = OUT / "asan" / (target + "-" + revision + "-command.json")
        relative = str(path.relative_to(OUT))
        command = record(relative, files, binaries)
        assert command["environment_overrides"]["ASAN_OPTIONS"] == "detect_leaks=1:halt_on_error=1"
        assert command["environment_overrides"]["UBSAN_OPTIONS"] == "halt_on_error=1:print_stacktrace=1"
        count = native_summaries((path.parent / command["log"]).read_text())
        assert len(count) == 1, relative
        targets.append({"target": target, **count[0],
                        "record": relative, "exit_code": 0,
                        "duration_seconds": command["duration_seconds"],
                        "log_sha256": command["log_sha256"], "inputs_sha256": command["inputs_sha256"]})
    assert len(targets) == 16 and len({item["target"] for item in targets}) == 16
    assert sum(item["cases"] for item in targets) == native_cases
    assert sum(item["assertions"] for item in targets) == native_assertions
    initial_sanitizers = read("asan/execution-v4.json")
    assert not initial_sanitizers["all_passed"]
    assert {item["target"] for item in initial_sanitizers["targets"] if item["recorder_exit_code"]} == replacements
    for name in ("revalidation", "cache-freshness"):
        repair = read("asan/" + name + "-launcher-permission-v4.json")
        assert not repair["child_started"] and repair["bytes_unchanged"]
        assert repair["sha256"] == binaries[repair["artifact"]]
    for name in ("contracts", "plan", "plan-guard", "pip-check"):
        record("final/" + name + "-v2-command.json", files, binaries)
    contracts = read("final/contracts-v2.log")
    assert contracts["count"] == contracts["passed"] == 34
    assert all(item["passed"] for item in contracts["results"])
    assert (OUT / "final/pip-check-v2.log").read_text().strip() == "No broken requirements found."
    plan, guard = read("final/plan-v2.log"), read("final/plan-guard-v2.log")
    assert (plan["packages"], plan["acceptance_mappings"], plan["slices"]) == (83, 161, 10)
    assert guard["passed"] == 13 and guard["failed"] == 0

    independent = read("independent/independent-review.json")
    assert independent["status"] == "passed_final_v3_independent_focused_review"
    assert independent["final_debug_library_sha256"] == binaries["build/libbzs_core.a"]
    assert independent["final_sanitizer_library_sha256"] == binaries["build-asan/libbzs_core.a"]
    assert independent["final_searchd_sha256"] == binaries["build/searchd"]
    for name, value in independent["production_inputs"].items():
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        assert files[str(path.relative_to(ROOT))] == value, name
    independent_groups = {}
    for name, cases, checks in (
        ("candidate-native-v3", 13, 551), ("candidate-extra-v3", 3, 99),
        ("capacity-candidate-v3", 2, 29), ("candidate-wire-v3", 12, 137),
    ):
        item = read("independent/" + name + "/summary.json")
        assert (item["cases"], item["checks"], item["failures"]) == (cases, checks, 0)
        assert item["inputs_unchanged"]
        independent_groups[name] = {**reference("independent/" + name + "/summary.json"),
                                   "cases": cases, "checks": checks, "failures": 0}
    preservation = read("preservation/audit-v3.json")
    assert preservation["passed"] and not preservation["failed_checks"]
    old_files = read("old-validation-before.json")
    if "files" in old_files:
        old_files = old_files["files"]
    assert len(old_files) == 3590
    assert all(sha(ROOT / name) == value for name, value in old_files.items())
    docs = read("docs-audit/audit-final-v5.json")
    assert docs["all_bound_input_hashes_match"] and docs["bound_file_count"] == len(files) == 227
    for name, value in docs["document_hashes"].items():
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        assert sha(path) == value, name
    remote = read("final/remote-before-publication.json")
    assert remote["main"]["sha"] == baseline["baseline_commit"]
    assert remote["main"]["tree"] == baseline["baseline_tree"]
    assert remote["read_only_review"]["sha"] == "30f058557e3f7ebb873e9780759323b49cfe9636"
    source = read("independent/baseline-source-provenance.json")
    assert baseline["files"]["libbzs_core.a"] == "35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0"
    save("final/sanitizer-closure.json", {
        "status": "passed_all_16_selected_native_targets", "targets": targets,
        "selection": "Fourteen successful v4 executions plus the two v5 executions whose v4 launchers failed before starting a child.",
        "initial_driver": reference("asan/execution-v4.json"),
        "initial_driver_all_passed": False,
        "binary_permissions_repaired_without_byte_changes": [
            reference("asan/revalidation-launcher-permission-v4.json"),
            reference("asan/cache-freshness-launcher-permission-v4.json"),
        ],
        "restricted_environment_attempt": reference("asan/restricted-environment-stop-v3.json"),
        "leak_detection_kept_enabled": True, "all_selected_input_and_binary_hashes_equal": True,
    })
    save("final/post-run-inputs.json", {
        "scope": "All final non-validation repository inputs plus the recorder; identical across all final runtime gates.",
        "files": files, "binaries": binaries,
    })
    report = {
        "status": "passed_local_validation", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": "wmqfl861/bzs861", "baseline_commit": baseline["baseline_commit"],
        "baseline_tree": baseline["baseline_tree"], "baseline_published": True,
        "working_branch": "work/direct-shared-20261024", "package_version": "0.1.10",
        "database_schema": {"before": 15, "after": 15, "migration_added": False},
        "scope": "CACHE-01 / FT-032 / B-24: same-WebService direct read sharing, one commit, independent delivery fences and truthful incremental/associated usage.",
        "implementation_contract": "../../docs/DIRECT_SHARED_READS.md",
        "regression": {
            "new_native_groups": 23, "native_assertions": 3636,
            "new_python_mcp_tests": 13, "new_http_tests": 3,
            "formal_source_sha256": files["tests/direct_shared.cpp"],
            "retained_legacy_tests": "Eight unsupported profile branches in six old fixture files; one cache_only counter assertion made relative to prior network calls. Strict inverse-byte checks in preservation.",
            "author_evidence": ["regression-author/README.md", "mcp/manifest.json", "fake-profile-followup/"],
            "counting_rule": "These tests are already included in CTest. Focused, independent and sanitizer executions are not additive product acceptance counts.",
        },
        "builds": {
            "debug": reference("build/candidate-v5-command.json"),
            "sanitizers": reference("build/asan-v2-command.json"),
            "all_final_sources_unchanged_during_builds": True,
        },
        "final_ctest": {
            "suites": 28, "passed": 28, "failed": 0, "skipped": 0, "disabled": 0, "nested_skips": 0,
            "native_targets": 16, "native_cases": native_cases, "native_assertions": native_assertions,
            "python_passed": int(python[0][0]), "python_seconds": float(python[0][1]),
            "duration_seconds": ctest["duration_seconds"], "command_record": "final/ctest-v2-command.json",
            "full_output": "final/ctest-v2-detail.log", "full_output_sha256": sha(detail_path),
            "junit": "final/ctest-v2-junit.xml", "junit_sha256": sha(xml_path),
            "native_summaries": summaries,
            "counting_rule": "Native and Python counts are components of the 28 suites, not additional suites. Complete LastTest.log provides native summaries.",
        },
        "final_sanitizers": {
            "targets": 16, "passed": 16, "failed": 0, "native_cases": native_cases,
            "native_assertions": native_assertions, "same_cases_as_debug": True,
            "leak_detection_enabled": True, "per_target": targets,
            "closure": reference("final/sanitizer-closure.json"),
        },
        "additional_gates": {
            "original_contract_fixtures": {"passed": 34, "failed": 0, "record": "final/contracts-v2-command.json"},
            "planning_read_only": plan, "plan_guard": guard,
            "dependency_check": {"status": "passed", "record": "final/pip-check-v2-command.json"},
        },
        "independent_review": {
            **reference("independent/independent-review.json"), "verdict": independent["status"],
            "groups": independent_groups, "native_distinct_scenarios": 18, "native_checks": 679,
            "wire_adapter_scenarios": 12, "wire_adapter_checks": 137,
            "same_native_scenarios_replayed_with_sanitizers": True,
            "baseline_provenance": reference("independent/baseline-source-provenance.json"),
            "baseline_missing_capability": reference("independent/baseline-v1/summary.json"),
            "real_capacity_before_after": [
                reference("independent/capacity-baseline-v1/summary.json"),
                reference("independent/capacity-candidate-v2/summary.json"),
                reference("independent/capacity-candidate-v3/summary.json"),
            ],
        },
        "input_binding": {
            "post_run": reference("final/post-run-inputs.json"), "tested_input_files": len(files),
            "binaries": len(binaries), "all_final_runtime_gate_input_and_binary_snapshots_equal": True,
            "all_final_inputs_equal_to_post_run": True, "final_post_test_source_changes": [],
            "baseline_library_sha256": baseline["files"]["libbzs_core.a"],
        },
        "preservation": {
            **reference("preservation/audit-v3.json"), "old_validation_files": 3590,
            "all_old_validation_baseline_pre_and_post_run_bytes_equal": True,
            "original_feature_json_bytes_equal": True,
            "status_counts": {"partial": 37, "not_started": 46, "accepted": 0},
            "planned_packages": 83, "planned_acceptance_items": 161, "slices": 10,
            "registered_sources": 17, "enabled_sources": 0, "sites": 0,
            "read_only_review_commit": remote["read_only_review"]["sha"],
            "six_prior_decisions_unchanged": True, "product_acceptance_checked": False,
        },
        "documentation_audit": reference("docs-audit/audit-final-v5.json"),
        "failure_history": "README.md#原失败与修正记录",
        "remote_publication": {
            "phase": "local_validation_before_commit_and_publication",
            "main_push_explicitly_authorized_by_user": True,
            "observed_parent": reference("final/remote-before-publication.json"),
            "new_remote_ci": "not_yet_run_at_report_generation",
            "baseline_ci_run": 40157161849,
            "note": "This immutable report precedes publication. After publishing, verify the actual main commit tree and its own push Checks; baseline CI is not evidence for this increment.",
        },
        "limits": independent["limits"] + [
            "Only one WebService instance; no cross-instance/process or direct/durable/map/crawl/search coalescing.",
            "No public direct cancellation ID or durable execution/billing/recovery ledger.",
            "An outer Service post-return principal check can still throw without execution details; no universal error receipt guarantee.",
            "No real external source/site requests, paid/model calls, source activation, migration, new tag or package bump.",
            "Full CACHE-01/B-24 and all product packages remain unsigned; repeated test executions are not new acceptance cases.",
        ],
        "summary_script_sha256": sha(Path(__file__)),
    }
    save("final-report.json", report)
    print(json.dumps({"status": report["status"], "ctest": 28, "native_groups": native_cases,
                      "native_assertions": native_assertions, "python": 1294, "sanitizer_targets": 16,
                      "old_validation_files": 3590, "unchanged_runtime_inputs": len(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
