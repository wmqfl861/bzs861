#!/usr/bin/env python3
"""Run 13 planning-tool regressions, independently of product acceptance.

Usage (paths are resolved from this script's repository, not the caller's cwd):
    python3 tools/test_plan_guard.py
    python3 tools/test_plan_guard.py --output validation/s1_review/plan_guard.json

Every subprocess runs a copied checker and copied inputs in its own disposable
directory. Even a regression that ignores --plan and writes its default path can
only change that case's temporary copy. No external dependencies are required.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CHECKER = "docs/planning/tools/plan_v1_2.py"
PLAN = "docs/planning/slices_v1.2.json"
BACKLOG = "docs/planning/requirements_and_backlog.json"
BLUEPRINT = "docs/planning/development_blueprint_v1.2.md"
INPUTS = (CHECKER, PLAN, BACKLOG, BLUEPRINT)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run_checks() -> dict[str, Any]:
    started = monotonic()
    inputs = {rel: (REPO / rel).read_bytes() for rel in INPUTS}
    plan = json.loads(inputs[PLAN])
    backlog = json.loads(inputs[BACKLOG])
    expected_counts = (len(backlog["features"]), len(backlog["acceptance"]), len(plan["slices"]))
    results: list[dict[str, Any]] = []

    with TemporaryDirectory(prefix="bzs861-plan-guard-") as temporary:
        temp_root = Path(temporary)

        def run(name: str, command: list[str], expected: int, *,
                candidate: dict[str, Any] | None = None, raw: str | None = None,
                missing: bool = False, diagnostic: str | None = None,
                optimized: bool = False) -> None:
            sandbox = temp_root / name
            watched: dict[Path, bytes | None] = {}
            for rel, data in inputs.items():
                target = sandbox / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                watched[target] = data
            args = list(command)
            if candidate is not None or raw is not None or missing:
                fixture = sandbox / "fixture.json"
                if candidate is not None:
                    fixture.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
                elif raw is not None:
                    fixture.write_text(raw, encoding="utf-8")
                watched[fixture] = fixture.read_bytes() if fixture.exists() else None
                args += ["--plan", str(fixture), "--backlog", str(sandbox / BACKLOG)]
            invocation = sandbox / "different-working-directory"
            invocation.mkdir()
            executable = [sys.executable] + (["-O"] if optimized else []) + [str(sandbox / CHECKER)]
            result: dict[str, Any] = {"case": name, "expected_exit": expected, "exit": None}
            errors: list[str] = []
            try:
                process = subprocess.run(executable + args, cwd=invocation, capture_output=True,
                                         text=True, timeout=10)
                result["exit"] = process.returncode
                require(process.returncode == expected,
                        f"expected exit {expected}, got {process.returncode}: {process.stderr[:400]}")
                if diagnostic:
                    require(diagnostic in process.stdout + process.stderr,
                            f"missing diagnostic: {diagnostic}")
                    result["diagnostic_checked"] = diagnostic
                if expected == 0:
                    summary = json.loads(process.stdout)
                    require(summary.get("read_only") is True and
                            summary.get("product_acceptance_checked") is False,
                            "checker must report read-only structure validation, not product acceptance")
                    require((summary.get("packages"), summary.get("acceptance_mappings"),
                             summary.get("slices")) == expected_counts, "reported plan counts changed")
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                errors.append(str(exc).replace(str(temp_root), "<temporary>")[:800])
            unchanged = all((path.read_bytes() if path.exists() else None) == data
                            for path, data in watched.items())
            result["all_case_inputs_unchanged"] = unchanged
            if not unchanged:
                errors.append("copied checker, authoritative input or fixture was changed/created")
            result["passed"] = not errors
            if errors:
                result["errors"] = errors
            results.append(result)

        run("default_plan_copy_check", [], 0)
        run("explicit_plan_copy_check", ["check"], 0)

        enriched = copy.deepcopy(plan)
        enriched["extra_review_fixture"] = {"unknown_field": ["preserve", None, 7], "state": "fixture_only"}
        enriched["packages"][0]["extra_review_fixture"] = {"approved_by": "fixture_only"}
        enriched["packages"][0]["full_work_package_accepted"] = True
        enriched["scope_policy"]["full_work_packages_accepted"] = sum(
            package["full_work_package_accepted"] is True for package in enriched["packages"])
        enriched["acceptance_mapping"][0]["execution_status"] = "fixture_only_signed_off"
        enriched["acceptance_mapping"][0]["evidence"] = [{"fixture_only": True, "artifact": "do-not-rewrite"}]
        run("preserve_extra_metadata_and_review_states", ["check"], 0, candidate=enriched)
        run("reject_legacy_write_existing_copy", ["--write"], 2, candidate=enriched,
            diagnostic="--write is disabled")
        run("reject_legacy_write_missing_path", ["--write"], 2, missing=True,
            diagnostic="--write is disabled")

        def reject(name: str, mutate: Any, diagnostic: str, *, optimized: bool = False) -> None:
            candidate = copy.deepcopy(plan)
            mutate(candidate)
            run(name, ["check"], 1, candidate=candidate, diagnostic=diagnostic, optimized=optimized)

        reject("missing_mapping_array", lambda p: p.pop("acceptance_mapping"), "plan.acceptance_mapping")
        reject("missing_acceptance_id", lambda p: p["acceptance_mapping"].pop(), "acceptance IDs: missing=")
        reject("duplicate_acceptance_id",
               lambda p: p["acceptance_mapping"].append(p["acceptance_mapping"][0]), "duplicate id")
        reject("erased_original_acceptance",
               lambda p: p["acceptance_mapping"][0].update(baseline_acceptance=""),
               "baseline acceptance text differs")
        reject("self_dependency_cycle",
               lambda p: p["packages"][0]["depends"].append(p["packages"][0]["id"]),
               "package dependencies contain a cycle")
        reject("unknown_dependency", lambda p: p["packages"][0]["depends"].append("UNKNOWN-FIXTURE"),
               "unknown dependency")
        reject("missing_review_state_with_optimization",
               lambda p: p["packages"][0].pop("full_work_package_accepted"),
               "explicit full_work_package_accepted state required", optimized=True)
        run("duplicate_json_key", ["check"], 1, raw='{"packages":[],"packages":[]}',
            diagnostic="duplicate JSON key: packages")

    require(len(results) == 13, "planning regression case count changed")
    source_unchanged = all((REPO / rel).read_bytes() == data for rel, data in inputs.items())
    passed = sum(result["passed"] for result in results)
    return {
        "schema_version": 1,
        "suite": "plan_guard",
        "scope": "planning_tool_regression_only",
        "product_acceptance_cases_executed": 0,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": sys.version.split()[0],
        "elapsed_seconds": round(monotonic() - started, 3),
        "passed": passed,
        "failed": len(results) - passed,
        "source_inputs_unchanged": source_unchanged,
        "temporary_case_directories_removed": not temp_root.exists(),
        "plan_counts": dict(zip(("packages", "acceptance_mappings", "slices"), expected_counts)),
        "input_sha256": {rel: sha256(data) for rel, data in inputs.items()},
        "runner_sha256": sha256(Path(__file__).read_bytes()),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="save JSON report; relative paths are repository-relative")
    args = parser.parse_args()
    output = args.output
    if output is not None:
        output = output.resolve() if output.is_absolute() else (REPO / output).resolve()
        protected = {(REPO / rel).resolve() for rel in INPUTS} | {Path(__file__).resolve()}
        if output in protected:
            parser.error("--output must not overwrite a checker, runner or authoritative planning input")
    try:
        report = run_checks()
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = {key: report[key] for key in ("suite", "passed", "failed", "source_inputs_unchanged",
                                               "product_acceptance_cases_executed")}
        if output is not None:
            summary["report"] = output.relative_to(REPO).as_posix() if output.is_relative_to(REPO) else str(output)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if (report["failed"] == 0 and report["source_inputs_unchanged"] and
                     report["temporary_case_directories_removed"]) else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"plan guard failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
