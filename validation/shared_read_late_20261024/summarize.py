"""Summarize closed local execution records; do not run tests or alter old evidence."""
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


def record(name: str, expected_binaries: dict | None = None) -> dict:
    path = OUT / name
    item = json.loads(path.read_bytes())
    assert item["execution_record_valid"] and item["exit_code"] == 0, name
    assert item["source_inputs_unchanged"] and not item["changed_inputs"], name
    assert not item["watchdog_expired"], name
    assert sha(path.parent / item["log"]) == item["log_sha256"], name
    assert sha(path.parent / item["inputs"]) == item["inputs_sha256"], name
    assert item["binaries_before"] == item["binaries_after"], name
    if expected_binaries is not None:
        assert item["binaries_after"] == expected_binaries, name
    return item


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


def main() -> None:
    baseline = read("baseline.json")
    ctest = record("final/ctest-command.json")
    binaries = ctest["binaries_after"]
    ctest_files = read("final/ctest-inputs.json")["files"]
    xml = ET.parse(OUT / "final/ctest.xml").getroot()
    assert int(xml.attrib["tests"]) == len(xml.findall("testcase")) == 27
    assert all(int(xml.attrib[key]) == 0 for key in ("failures", "disabled", "skipped"))
    detail = (OUT / "final/ctest-detail.log").read_text()
    summaries = native_summaries(detail)
    assert len(summaries) == 15
    native_cases = sum(item["cases"] for item in summaries)
    native_assertions = sum(item["assertions"] for item in summaries)
    assert (native_cases, native_assertions) == (521, 16761)
    python = re.findall(r"^(\d+) passed in ([\d.]+)s$", detail, re.MULTILINE)
    assert len(python) == 1 and int(python[0][0]) == 1281

    targets = []
    for path in sorted((OUT / "asan").glob("*-command.json")):
        relative = str(path.relative_to(OUT))
        command = record(relative, binaries)
        assert json.loads((path.parent / command["inputs"]).read_bytes())["files"] == ctest_files
        assert command["environment_overrides"]["ASAN_OPTIONS"] == "detect_leaks=1:halt_on_error=1"
        assert command["environment_overrides"]["UBSAN_OPTIONS"] == "halt_on_error=1:print_stacktrace=1"
        count = native_summaries((path.parent / command["log"]).read_text())
        assert len(count) == 1, relative
        targets.append({"target": path.name.removesuffix("-command.json"), **count[0],
                        "record": relative, "exit_code": command["exit_code"],
                        "duration_seconds": command["duration_seconds"],
                        "log_sha256": command["log_sha256"], "inputs_sha256": command["inputs_sha256"]})
    assert len(targets) == 15 and len({item["target"] for item in targets}) == 15
    assert sum(item["cases"] for item in targets) == native_cases
    assert sum(item["assertions"] for item in targets) == native_assertions
    for name in ("contracts", "plan", "plan-guard", "pip-check"):
        command = record("final/" + name + "-command.json", binaries)
        assert read("final/" + command["inputs"])["files"] == ctest_files
    contracts = read("final/contracts.log")
    assert contracts["count"] == contracts["passed"] == 34
    assert all(item["passed"] for item in contracts["results"])
    assert (OUT / "final/pip-check.log").read_text().strip() == "No broken requirements found."

    recorder = runpy.run_path(str(OUT / "run.py"))
    post_files = recorder["inputs"]()
    assert recorder["binaries"]() == binaries
    changed = sorted(name for name in set(post_files) | set(ctest_files) if post_files.get(name) != ctest_files.get(name))
    assert changed == ["docs/handoff/NEXT_AGENT.md", "docs/implementation_status.json"], changed
    save("final/documentation-after-tests.json", {
        "scope": "Two status/handoff documents updated only after all runtime gates closed; no production, test, build or configuration change.",
        "changes": [{"path": name, "tested_sha256": ctest_files[name], "final_sha256": post_files[name]} for name in changed],
        "reason": "Clarify that historical remote CI covers neither unpublished schema14 nor schema15; replace pending result pointers with completed local validation and its exact final report.",
        "runtime_inputs_equal": True,
    })
    save("final/post-run-inputs.json", {"files": post_files, "binaries": binaries,
                                        "scope": "All current non-validation tracked/untracked inputs plus run.py; final documentation delta recorded separately."})
    independent = read("independent/independent-review.json")
    assert independent["status"] == "passed_in_independent_scope"
    assert all(post_files[name] == value for name, value in independent["production"].items())
    previous = ROOT / "validation/uri_deletion_20261024/final/ctest-command.json"
    previous_library = json.loads(previous.read_bytes())["binaries_after"]["build/libbzs_core.a"]
    assert previous_library == baseline["baseline_library_sha256"] == independent["baseline_library_sha256"]
    assert sha(Path(baseline["baseline_library"])) == previous_library
    preservation = read("final/preservation.json")
    assert preservation["status"] == "passed" and preservation["old_validation_files"] == 3193
    remote = read("final/remote-main.json")
    assert remote["commit"] == baseline["remote_main"]
    baseline_regression = read("regression/baseline-v2-result.json")
    candidate_regression = read("regression/candidate-v1-result.json")
    assert baseline_regression["run_exit_code"] == 1 and len(baseline_regression["failures"]) == 11
    assert candidate_regression["run_exit_code"] == 0 and not candidate_regression["failures"]

    report = {
        "status": "passed_local_validation", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": "wmqfl861/bzs861", "baseline_commit": baseline["base_commit"], "baseline_tree": baseline["base_tree"],
        "baseline_published": False, "working_branch": "work/b24-late-subscribers-20261024",
        "read_only_review_commit": "30f058557e3f7ebb873e9780759323b49cfe9636", "package_version": "0.1.10",
        "database_schema": {"before": 14, "after": 15, "stop_all_old_core_processes_before_upgrade": True},
        "implementation_contract": "../../docs/SHARED_READS.md",
        "regression": {
            "retained_groups": 30, "added_groups": 14, "schema_only_adjustment_record": "author/current-schema-test-adjustment.json",
            "real_baseline": {"record": "regression/baseline-v2-result.json", "cases": 44, "assertions": 1150, "expected_new_capability_failures": 11, "all_original_groups_passed": True},
            "first_candidate": {"record": "regression/candidate-v1-result.json", "cases": 44, "assertions": 1456, "failures": 0},
            "counting_rule": "Focused candidate batch is included in CTest and replayed by sanitizers; do not add these executions together.",
        },
        "final_ctest": {
            "suites": 27, "passed": 27, "failed": 0, "skipped": 0, "disabled": 0,
            "native_targets": 15, "native_cases": native_cases, "native_assertions": native_assertions,
            "python_passed": int(python[0][0]), "python_seconds": float(python[0][1]),
            "duration_seconds": ctest["duration_seconds"], "command_record": "final/ctest-command.json",
            "full_output": "final/ctest-detail.log", "full_output_sha256": sha(OUT / "final/ctest-detail.log"),
            "junit": "final/ctest.xml", "junit_sha256": sha(OUT / "final/ctest.xml"),
            "native_summaries": summaries,
            "counting_rule": "Native and Python are components of the 27 CTest suites. Native counts use the complete LastTest.log copy because JUnit output is truncated.",
        },
        "final_sanitizers": {"targets": 15, "passed": 15, "failed": 0, "native_cases": native_cases,
                             "native_assertions": native_assertions, "same_cases_as_debug": True,
                             "leak_detection_enabled": True, "per_target": targets},
        "additional_gates": {
            "original_contract_fixtures": {"passed": 34, "failed": 0, "record": "final/contracts-command.json"},
            "planning_read_only": read("final/plan.log"), "plan_guard": read("final/plan-guard.log"),
            "dependency_check": {"status": "passed", "record": "final/pip-check-command.json"},
        },
        "independent_review": {
            "record": "independent/independent-review.json", "verdict": independent["status"],
            "production_sha256": independent["production"], "real_old_database_tables_preserved": 47,
            "virtual_tables_covered_by_shadow_data": 2, "behavior_groups": 6, "behavior_checks": 163,
            "baseline_behavior_checks": 131, "baseline_expected_failures": 4,
            "admission_rollback_branches": 2, "admission_rollback_checks": 151,
            "sanitizer_replays_same_scenarios": True, "final_evidence_audit": "independent/final-local-review.json",
        },
        "input_binding": {
            "post_run": "final/post-run-inputs.json", "post_run_sha256": sha(OUT / "final/post-run-inputs.json"),
            "tested_input_files": len(ctest_files), "binaries": len(binaries),
            "all_runtime_gate_snapshots_equal": True, "production_test_build_inputs_equal_to_post_run": True,
            "documentation_after_tests": "final/documentation-after-tests.json",
            "supplementary_historical_probe": preservation["supplementary_dependency_binding"],
            "baseline_library_sha256": previous_library, "previous_baseline_library_record": str(previous.relative_to(ROOT)),
            "previous_baseline_library_record_sha256": sha(previous),
        },
        "preservation": {
            "record": "final/preservation.json", "old_validation_files": 3193,
            "all_old_validation_baseline_pre_and_post_run_bytes_equal": True,
            "original_feature_json_bytes_equal": True, "protected_paths": preservation["protected_inputs_unchanged"],
            "status_counts": preservation["status_counts"], "planned_packages": 83, "planned_acceptance_items": 161,
            "slices": 10, "registered_sources": 17, "enabled_sources": 0, "sites": 0,
            "six_prior_decisions_unchanged": True, "product_acceptance_checked": False,
        },
        "failure_history": "README.md#原失败与修正记录",
        "remote_publication": {
            "status": "not_published", "main": remote["commit"], "tree": remote["tree"], "observation": "final/remote-main.json",
            "new_remote_ci": "not_run", "historical_baseline_only_run": 40096268557,
            "block": "Automatic approval rejected pushing HEAD to main because continuing development did not explicitly authorize updating the default branch. No retry or indirect bypass this increment.",
        },
        "limits": independent["limits"] + [
            "No real external source/site requests, paid/model calls, source activation, new tag or package bump.",
            "Formal, focused and independent sanitizer replays overlap and do not establish full B-24 or product acceptance.",
        ],
        "summary_script_sha256": sha(Path(__file__)),
    }
    save("final-report.json", report)
    print(json.dumps({"status": report["status"], "ctest": 27, "native_groups": native_cases,
                      "native_assertions": native_assertions, "python": int(python[0][0]),
                      "sanitizer_targets": len(targets), "post_test_documentation_changes": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
