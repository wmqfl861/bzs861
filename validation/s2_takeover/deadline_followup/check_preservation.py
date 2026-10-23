"""Verify the deadline correction preserves all prior evidence and other inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[3]
BASELINE = "199eb635ca90c657dff673fbf88bed44e7c4b40b"
ALLOWED = {
    "src/web.cpp", "src/sessions.cpp", "tests/search_modes.cpp", "docs/SEARCH_MODES.md",
    "README.md", "HANDOFF_START_HERE.md", "docs/DEVELOPMENT_STATUS.md",
    "docs/GITHUB_HANDOFF.md", "docs/handoff/NEXT_AGENT.md", "docs/implementation_status.json",
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
    numstat = git("diff", "--numstat", BASELINE, "--", "tests/search_modes.cpp").decode().strip().split("\t")
    require(len(numstat) == 3 and int(numstat[0]) > 0 and numstat[1] == "0",
            "Original search-mode tests were removed or changed")
    original = json.loads(git("show", BASELINE + ":docs/implementation_status.json"))
    current = json.loads((REPO / "docs/implementation_status.json").read_bytes())
    for key in ("features", "status_counts", "partial_work_packages", "not_started",
                "full_work_packages_accepted", "milestones_accepted", "slices_accepted",
                "planned_acceptance_items", "planned_acceptance_suite_executed"):
        require(original[key] == current[key], "Acceptance or work-package scope changed: " + key)
    recovered = preserved["tests/e2e_session_recovery.py"]
    require(recovered == "cbd5a068e50596431658a3107ef7404e6c5b7fc5331ccabef300142c4b9059d5",
            "Original six real recovery tests changed")
    require(preserved["tests/http_revocation.py"] == "da597e3cf6a75e882ce882d7224187309a02b07e3de1c35b24a5106678e7fd09",
            "Reviewed Windows fixture correction changed")
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
        "production_code_changes_limited_to": ["src/sessions.cpp", "src/web.cpp"],
        "original_search_mode_test_lines_removed": 0,
        "original_recovery_e2e_sha256": recovered,
        "reviewed_http_revocation_fixture_sha256": preserved["tests/http_revocation.py"],
        "workflow_build_settings_schema_contract_fixtures_and_dependency_locks_unchanged": True,
        "plan_and_acceptance_unchanged": True, "status_counts": current["status_counts"],
        "new_files_limited_to_validation_evidence": True,
        "golden_check": "Raw byte/object hashes only; no query inspection, labeling or tuning.",
        "current_production_binaries_sha256": {
            name: digest((REPO / name).read_bytes())
            for name in ("build/libbzs_core.a", "build/searchd")
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
