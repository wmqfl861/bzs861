"""Bind the narrow Windows watchdog correction to unchanged runtime inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[3]
BASELINE = "fcb58e4679a5bbdff17319b7b4ebff7019733eab"
ALLOWED = {
    "CMakeLists.txt", "tests/crawl.cpp", "README.md", "HANDOFF_START_HERE.md",
    "docs/DEVELOPMENT_STATUS.md", "docs/GITHUB_HANDOFF.md",
    "docs/handoff/NEXT_AGENT.md", "docs/implementation_status.json",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    entries = {}
    for raw in git("ls-tree", "-rz", BASELINE).split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        require(kind == "blob", "Unexpected baseline entry")
        entries[path.decode()] = blob
    preserved, changes = {}, {}
    for path, expected_blob in sorted(entries.items()):
        data = (REPO / path).read_bytes()
        actual_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if path in ALLOWED:
            if actual_blob != expected_blob:
                changes[path] = {"before_blob": expected_blob, "after_blob": actual_blob,
                                 "after_sha256": digest(data)}
        else:
            require(actual_blob == expected_blob, "Unexpected changed or missing file: " + path)
            preserved[path] = digest(data)
    known = set(path.decode() for path in git("ls-files", "-z").split(b"\0") if path)
    known.update(path.decode() for path in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path)
    require(all(path.startswith("validation/s2_takeover/") for path in known - entries.keys()),
            "Unexpected new file outside correction evidence")

    old_test = git("show", BASELINE + ":tests/crawl.cpp")
    new_test = (REPO / "tests/crawl.cpp").read_bytes()
    marker = b"\nint main() {\n"
    require(old_test.count(marker) == new_test.count(marker) == 1, "Ambiguous crawl main boundary")
    old_body = old_test.split(marker)[0]
    new_body = new_test.split(marker)[0]
    require(new_body.count(b"#include <chrono>\n") == 1, "Unexpected chrono include")
    require(new_body.replace(b"#include <chrono>\n", b"", 1) == old_body,
            "Original crawl fixtures, functions, assertions or registration order changed")
    registered = re.findall(rb"^\s*TEST\((\w+)\)", old_body, re.M)
    require(len(registered) == 54, "Unexpected original crawl case count")

    original = json.loads(git("show", BASELINE + ":docs/implementation_status.json"))
    current = json.loads((REPO / "docs/implementation_status.json").read_bytes())
    for key in ("features", "status_counts", "partial_work_packages", "not_started",
                "full_work_packages_accepted", "milestones_accepted", "slices_accepted",
                "planned_acceptance_items", "planned_acceptance_suite_executed"):
        require(original[key] == current[key], "Acceptance or work-package scope changed: " + key)

    before = json.loads((REPO / "validation/s2_takeover/windows_watchdog_followup/linux-properties-before.json").read_bytes())
    discovered = json.loads(subprocess.check_output(
        ["ctest", "--test-dir", "build", "--show-only=json-v1"], cwd=REPO))
    properties = {test["name"]: {prop["name"]: prop["value"] for prop in test.get("properties", [])}
                  for test in discovered["tests"]}
    require(len(properties) == 27 and properties == before["tests"],
            "Generated Linux suite selection or properties changed")
    binary_names = ("build/libbzs_core.a", "build/searchd", "build-asan/libbzs_core.a")
    binaries = {name: digest((REPO / name).read_bytes()) for name in binary_names}
    require(all(value == before["binaries"][name]["sha256"] for name, value in binaries.items()),
            "A production binary changed during this test-only correction")
    for name, expected in {
        "tests/e2e_session_recovery.py": "cbd5a068e50596431658a3107ef7404e6c5b7fc5331ccabef300142c4b9059d5",
        "tests/http_revocation.py": "da597e3cf6a75e882ce882d7224187309a02b07e3de1c35b24a5106678e7fd09",
        "tests/search_modes.cpp": "0f50b7c9ee75d790732f0c1695e695cedfeedf17142d82e41a1fb192bd8edca1",
    }.items():
        require(preserved[name] == expected, "Prior permanent regression changed: " + name)
    historical = {path: value for path, value in preserved.items() if path.startswith("validation/")}
    aggregate = lambda values: digest(json.dumps(values, sort_keys=True, separators=(",", ":")).encode())
    print(json.dumps({
        "schema_version": 1, "status": "passed", "baseline_commit": BASELINE,
        "baseline_tree": git("rev-parse", BASELINE + "^{tree}").decode().strip(),
        "preserved_existing_files": len(preserved),
        "preserved_existing_files_manifest_sha256": aggregate(preserved),
        "preserved_existing_validation_files": len(historical),
        "preserved_existing_validation_manifest_sha256": aggregate(historical),
        "all_prior_validation_bytes_preserved": True,
        "allowed_changed_files": changes,
        "all_production_runtime_source_bytes_unchanged": True,
        "original_crawl_fixtures_functions_assertions_and_registration_order_preserved": True,
        "original_crawl_body_sha256": digest(old_body),
        "original_crawl_cases": len(registered),
        "original_crawl_registered_names_sha256": aggregate([name.decode() for name in registered]),
        "workflow_presets_schema_contract_fixtures_and_dependency_locks_unchanged": True,
        "generated_linux_test_properties_unchanged": True,
        "generated_linux_test_count": len(properties),
        "linux_watchdogs_seconds": {name: properties[name]["TIMEOUT"] for name in ("core", "crawl", "site_publication", "sessions")},
        "windows_configuration_execution": "Not performed locally; review source branch and require actual new Windows CI to show 90/120/120 seconds.",
        "plan_and_acceptance_unchanged": True, "status_counts": current["status_counts"],
        "new_files_limited_to_validation_evidence": True,
        "golden_check": "Raw byte/object hashes only; no query inspection, labeling or tuning.",
        "unchanged_production_binaries_sha256": binaries,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
