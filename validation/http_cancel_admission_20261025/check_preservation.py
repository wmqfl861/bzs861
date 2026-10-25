"""Read-only comparison against the published baseline; writes only this report."""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "bedb45c89d293ab25a8a481df6c8300558225b0c"
OUTPUT = Path(__file__).with_name("preservation.json")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def manifest_digest(records: list[dict]) -> str:
    return digest(json.dumps(records, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))


entries = {}
for row in git("ls-tree", "-r", "-z", BASE).split(b"\0"):
    if row:
        head, name = row.split(b"\t", 1)
        mode, kind, sha = head.decode("ascii").split()
        if kind == "blob":
            entries[name.decode("utf-8")] = (mode, sha)

groups = {
    "historical_validation": sorted(p for p in entries if p.startswith("validation/")),
    "planning": sorted(p for p in entries if p.startswith("docs/planning/")),
    "source_and_site_configuration": ["config/sources.json", "config/sites.json"],
    "other_existing_tests": sorted(p for p in entries if
        (p.startswith("tests/") or p.startswith("python/tests/")) and p != "tests/integration.py"),
    "version_and_schema_sources": ["CMakeLists.txt", "python/pyproject.toml", "src/store.cpp"],
}
extra = ["docs/implementation_status.json", "tests/integration.py"]
requested = sorted({entries[p][1] for p in [*extra, *(p for paths in groups.values() for p in paths)]})
raw = subprocess.check_output(["git", "cat-file", "--batch"], cwd=ROOT,
                              input=("\n".join(requested) + "\n").encode("ascii"))
blobs = {}
position = 0
for expected in requested:
    end = raw.index(b"\n", position)
    sha, kind, size = raw[position:end].decode("ascii").split()
    assert sha == expected and kind == "blob"
    position = end + 1
    content = raw[position:position + int(size)]
    position += int(size)
    assert raw[position:position + 1] == b"\n"
    position += 1
    assert hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest() == sha
    blobs[sha] = content
assert position == len(raw)


def baseline(path: str) -> bytes:
    return blobs[entries[path][1]]


def local(path: str) -> bytes | None:
    file = ROOT / path
    return file.read_bytes() if file.is_file() else None


report_groups = {}
checks = {}
for name, paths in groups.items():
    old_records, new_records, differences = [], [], []
    for path in paths:
        old, new = baseline(path), local(path)
        old_records.append({"path": path, "bytes": len(old), "sha256": digest(old)})
        new_records.append({"path": path, "bytes": None if new is None else len(new),
                            "sha256": None if new is None else digest(new)})
        if old != new:
            differences.append(path)
    checks[name + "_bytes_unchanged"] = not differences
    report_groups[name] = {
        "files": len(paths), "baseline_bytes": sum(row["bytes"] for row in old_records),
        "identical_files": len(paths) - len(differences), "differences": differences,
        "baseline_manifest_sha256": manifest_digest(old_records),
        "working_manifest_sha256": manifest_digest(new_records),
        "comparison": "Each local byte sequence compared directly to the baseline Git blob.",
    }

state_path = "docs/implementation_status.json"
old_state_bytes, new_state_bytes = baseline(state_path), local(state_path)
assert new_state_bytes is not None
old_state, new_state = json.loads(old_state_bytes), json.loads(new_state_bytes)


def original_features_bytes(value: bytes) -> bytes:
    text = value.decode("utf-8")
    match = re.search(r'^  "features":\s*', text, re.MULTILINE)
    assert match is not None
    _, end = json.JSONDecoder().raw_decode(text, match.end())
    return text[match.end():end].encode("utf-8")


old_features, new_features = original_features_bytes(old_state_bytes), original_features_bytes(new_state_bytes)
checks["features_values_unchanged"] = old_state["features"] == new_state["features"]
checks["features_original_text_bytes_unchanged"] = old_features == new_features
features = {
    "path": state_path, "items": len(new_state["features"]),
    "baseline_array_utf8_bytes": len(old_features), "working_array_utf8_bytes": len(new_features),
    "baseline_array_sha256": digest(old_features), "working_array_sha256": digest(new_features),
    "array_value_equal": checks["features_values_unchanged"],
    "original_array_text_bytes_equal": checks["features_original_text_bytes_unchanged"],
    "raw_slice": "UTF-8 bytes of the top-level features array, opening [ through closing ], including internal whitespace.",
    "working_state_file_sha256": digest(new_state_bytes),
}


def test_methods(content: bytes) -> dict[str, str]:
    text = content.decode("utf-8")
    result = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"):
                    result[node.name + "." + method.name] = ast.get_source_segment(text, method)
    return result


http_path = "tests/integration.py"
old_http, new_http = baseline(http_path), local(http_path)
assert new_http is not None
old_methods, new_methods = test_methods(old_http), test_methods(new_http)
old_name = "DirectReadCancellation.test_cancel_is_subject_to_existing_http_business_backpressure"
new_name = "DirectReadCancellation.test_reserved_cancel_bypasses_full_business_pool_without_lending_its_slot"
unchanged = [name for name, content in old_methods.items() if new_methods.get(name) == content]
unexpected_changes = sorted(name for name in old_methods if name != old_name and name not in unchanged)
added = sorted(set(new_methods) - set(old_methods) - {new_name})
checks["http_original_29_methods_byte_identical"] = len(unchanged) == 29 and not unexpected_changes
checks["http_only_declared_contract_rewrite"] = old_name not in new_methods and new_name in new_methods
checks["http_method_counts"] = len(old_methods) == 30 and len(new_methods) == 34 and len(added) == 4
http = {
    "path": http_path, "baseline_file_sha256": digest(old_http), "working_file_sha256": digest(new_http),
    "baseline_methods": len(old_methods), "working_methods": len(new_methods),
    "unchanged_original_methods": len(unchanged), "unexpected_changes": unexpected_changes,
    "unchanged_method_hashes": [{"method": name, "sha256": digest(old_methods[name].encode("utf-8"))} for name in sorted(unchanged)],
    "declared_rewrite": {"baseline_method": old_name, "working_method": new_name,
        "baseline_method_sha256": digest(old_methods[old_name].encode("utf-8")),
        "working_method_sha256": digest(new_methods[new_name].encode("utf-8")),
        "reason": "Reserved cancellation admission now bypasses a saturated ordinary pool. Ordinary-pool and control-pool saturation remain separately asserted as 503."},
    "added_methods": [{"method": name, "sha256": digest(new_methods[name].encode("utf-8"))} for name in added],
    "comparison": "Exact UTF-8 method source from def through function end, extracted by AST; helper changes and the one declared rewrite are intentional.",
    "execution_claim": "Static preservation inspection only. No test executions were performed by this script.",
}

planning = json.loads(local("docs/planning/requirements_and_backlog.json"))
slices = json.loads(local("docs/planning/slices_v1.2.json"))
sources = json.loads(local("config/sources.json"))
sites = json.loads(local("config/sites.json"))
counts = {
    "planned_packages": len(planning["features"]), "planned_acceptance_items": len(planning["acceptance"]),
    "slices": len(slices["slices"]), "mapped_packages": len(slices["packages"]),
    "mapped_acceptance_items": len(slices["acceptance_mapping"]),
    "status_from_features": {**dict(Counter(item["status"] for item in new_state["features"])), "accepted": 0},
    "status_declared": new_state["status_counts"],
    "registered_sources": len(sources["sources"]),
    "enabled_sources": sum(item.get("enabled") is True for item in sources["sources"]),
    "registered_sites": len(sites["sites"]),
}
# Compute accepted separately rather than inserting an unexamined expected value.
counts["status_from_features"]["accepted"] = sum(item["status"] == "accepted" for item in new_state["features"])
checks["plan_and_status_counts_preserved"] = (
    (counts["planned_packages"], counts["planned_acceptance_items"], counts["slices"]) == (83, 161, 10)
    and counts["mapped_packages"] == 83 and counts["mapped_acceptance_items"] == 161
    and counts["status_from_features"] == counts["status_declared"] == {"partial": 37, "not_started": 46, "accepted": 0}
    and new_state["status_counts"] == old_state["status_counts"])
checks["default_sources_and_sites_preserved"] = (counts["registered_sources"], counts["enabled_sources"], counts["registered_sites"]) == (17, 0, 0)

cmake = local("CMakeLists.txt").decode("utf-8")
package = local("python/pyproject.toml").decode("utf-8")
store = local("src/store.cpp").decode("utf-8")
versions = {
    "cmake_project_version": re.search(r'project\(bzs861 VERSION ([^ ]+)', cmake).group(1),
    "python_package_version": re.search(r'^version = "([^"]+)"', package, re.MULTILINE).group(1),
    "state_version": new_state["version"],
    "state_schema": new_state["integration_inputs"]["schema"],
    "source_schema_upper_guard": int(re.search(r'version>=0&&version<=(\d+)', store).group(1)),
    "source_highest_user_version_assignment": max(int(value) for value in re.findall(r'PRAGMA user_version=(\d+)', store)),
    "scope": "Static version fields plus exact baseline-byte equality of CMakeLists.txt, pyproject.toml and store.cpp; no database was opened or migrated.",
}
checks["version_and_schema_preserved"] = (
    versions["cmake_project_version"] == versions["python_package_version"] == versions["state_version"] == old_state["version"] == "0.1.10"
    and versions["state_schema"] == versions["source_schema_upper_guard"] == versions["source_highest_user_version_assignment"] == 15)

report = {
    "status": "passed" if all(checks.values()) else "failed", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "baseline_commit": BASE, "baseline_tree": git("rev-parse", BASE + "^{tree}").decode().strip(),
    "checks": checks, "groups": report_groups, "features": features, "http_methods": http,
    "counts": counts, "version_and_schema": versions,
    "limitations": ["This is byte/static preservation evidence, not a test run, migration run, source-approval decision or full product acceptance.",
        "Historical validation means every file tracked below validation/ in the named baseline; new evidence files are not compared to themselves.",
        "Parent-edited prose outside docs/planning is outside this preservation comparison."],
    "script_path": str(Path(__file__).relative_to(ROOT)), "script_sha256": digest(Path(__file__).read_bytes()),
}
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": report["status"], "report": str(OUTPUT), "report_sha256": digest(OUTPUT.read_bytes()),
                  "group_counts": {name: row["files"] for name, row in report_groups.items()}, "checks": checks,
                  "counts": counts, "http_baseline_methods": len(old_methods), "http_current_methods": len(new_methods)}, ensure_ascii=False))
raise SystemExit(0 if all(checks.values()) else 1)
