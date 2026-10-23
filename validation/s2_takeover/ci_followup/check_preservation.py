"""Check the test-only follow-up against the exact first 0.1.10 publication."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[3]
BASELINE = "a06e7e91f6a115b345ff1072095b80e15d193ad4"
ALLOWED = {
    "tests/http_revocation.py",
    "README.md",
    "HANDOFF_START_HERE.md",
    "docs/DEVELOPMENT_STATUS.md",
    "docs/GITHUB_HANDOFF.md",
    "docs/handoff/NEXT_AGENT.md",
    "docs/implementation_status.json",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def methods(source: str) -> dict[str, str]:
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.ClassDef) and node.name == "RevocationHTTP")
    return {method.name: ast.get_source_segment(source, method)
            for method in node.body if isinstance(method, ast.FunctionDef)}


def main() -> None:
    entries = {}
    for entry in git("ls-tree", "-rz", BASELINE).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, digest = metadata.decode().split()
        require(kind == "blob", "Unexpected baseline tree entry")
        entries[path.decode()] = digest
    preserved = {}
    changes = {}
    for path, expected_blob in sorted(entries.items()):
        data = (REPO / path).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if path in ALLOWED:
            if blob != expected_blob:
                changes[path] = {"before_blob": expected_blob, "after_blob": blob,
                                 "after_sha256": sha256(data)}
        else:
            require(blob == expected_blob, "Unexpected modification: " + path)
            preserved[path] = sha256(data)

    files = set(path.decode() for path in git("ls-files", "-z").split(b"\0") if path)
    files.update(path.decode() for path in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path)
    new_files = sorted(files - entries.keys())
    require(all(path.startswith("validation/s2_takeover/") for path in new_files),
            "Unexpected new file outside follow-up validation evidence")

    old_test = git("show", BASELINE + ":tests/http_revocation.py").decode()
    new_test = (REPO / "tests/http_revocation.py").read_text()
    before_methods, after_methods = methods(old_test), methods(new_test)
    require(before_methods.keys() == after_methods.keys(), "Original HTTP test methods changed")
    unchanged_methods = sorted(name for name in before_methods if name != "rows")
    require(all(before_methods[name] == after_methods[name] for name in unchanged_methods),
            "Original setup, strict cleanup or HTTP assertions changed")

    old_status = json.loads(git("show", BASELINE + ":docs/implementation_status.json"))
    new_status = json.loads((REPO / "docs/implementation_status.json").read_bytes())
    for key in ("features", "status_counts", "partial_work_packages", "not_started",
                "full_work_packages_accepted", "milestones_accepted", "slices_accepted",
                "planned_acceptance_items", "planned_acceptance_suite_executed"):
        require(old_status[key] == new_status[key], "Product scope or acceptance changed: " + key)
    original = json.loads((REPO / "validation/s2_takeover/final-report.json").read_bytes())
    binaries = {
        "build/libbzs_core.a": original["runtime_library_sha256"],
        "build/searchd": original["searchd_sha256"],
    }
    for path, expected in binaries.items():
        require(sha256((REPO / path).read_bytes()) == expected, "Production binary changed: " + path)

    original_validation = {path: value for path, value in preserved.items() if path.startswith("validation/")}
    aggregate = lambda mapping: sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode())
    print(json.dumps({
        "schema_version": 1,
        "status": "passed",
        "baseline_commit": BASELINE,
        "baseline_tree": git("rev-parse", BASELINE + "^{tree}").decode().strip(),
        "preserved_existing_files": len(preserved),
        "preserved_existing_files_manifest_sha256": aggregate(preserved),
        "preserved_existing_validation_files": len(original_validation),
        "preserved_existing_validation_manifest_sha256": aggregate(original_validation),
        "all_existing_validation_bytes_preserved": True,
        "production_sources_build_configuration_workflow_unchanged": True,
        "allowed_changed_files": changes,
        "original_http_methods_byte_identical": unchanged_methods,
        "production_binaries_sha256": binaries,
        "production_binaries_identical_to_final_v2": True,
        "new_files_limited_to_followup_evidence": True,
        "plan_and_acceptance_unchanged": True,
        "status_counts": new_status["status_counts"],
        "golden_check": "Only raw-byte object hashes compared; no query inspection or tuning.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
