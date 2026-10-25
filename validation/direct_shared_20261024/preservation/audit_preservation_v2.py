#!/usr/bin/env python3
"""Read-only source/evidence preservation audit; writes a NEW JSON record only.

This script never runs product tests, imports project modules, or contacts Git
remotes. The --output file and its .sha256 sidecar must not already exist.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


BASELINE = "346048b4ae329e750d15f1e35952aab4513e3912"
BASELINE_TREE = "458a6f321e94defd74616ba6ebbf2983252cb495"
PREFIX = "validation/direct_shared_20261024/"
LEGACY_PROFILE = b'if(method=="fetch.profile")throw Error("E_WORKER","Legacy fixture has no profile proof",502);'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class GitBlobs:
    """Hash Git object bytes, with one cat-file process and deduplication."""
    def __init__(self, repo):
        self.process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.hashes = {}

    def digest(self, oid):
        if oid in self.hashes:
            return self.hashes[oid]
        self.process.stdin.write(oid.encode() + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise RuntimeError("Unexpected Git blob header: " + repr(header))
        remaining = int(header[2])
        digest = hashlib.sha256()
        while remaining:
            chunk = self.process.stdout.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise RuntimeError("Truncated Git blob")
            digest.update(chunk)
            remaining -= len(chunk)
        if self.process.stdout.read(1) != b"\n":
            raise RuntimeError("Invalid Git batch delimiter")
        self.hashes[oid] = digest.hexdigest()
        return self.hashes[oid]

    def close(self):
        self.process.stdin.close()
        self.process.stdout.close()
        if self.process.wait() != 0:
            raise RuntimeError("Git cat-file failed")


def disk_hash(path):
    if path.is_symlink():
        return sha(str(path.readlink()).encode())
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raw_member(data, requested):
    """Return exact UTF-8 value bytes of a top-level JSON member."""
    text = data.decode("utf-8")
    decoder = json.JSONDecoder()
    position = text.index("{") + 1
    matches = []
    while True:
        while text[position].isspace():
            position += 1
        if text[position] == "}":
            break
        key, position = decoder.raw_decode(text, position)
        while text[position].isspace():
            position += 1
        if text[position] != ":":
            raise ValueError("Missing JSON colon")
        position += 1
        while text[position].isspace():
            position += 1
        start = position
        _, position = decoder.raw_decode(text, position)
        if key == requested:
            matches.append(text[start:position].encode("utf-8"))
        while text[position].isspace():
            position += 1
        if text[position] == "}":
            break
        if text[position] != ",":
            raise ValueError("Missing JSON comma")
        position += 1
    if len(matches) != 1:
        raise ValueError("Expected exactly one top-level member " + requested)
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    sidecar = Path(str(output) + ".sha256")
    if output.exists() or sidecar.exists():
        parser.error("Refusing to overwrite existing audit evidence; choose a new output name")
    if not output.parent.is_dir():
        parser.error("Output parent directory must already exist")

    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=repo)

    def base(path):
        return git("show", BASELINE + ":" + path)

    def current(path):
        return (repo / path).read_bytes()

    checks = []

    def check(name, passed, detail=None):
        item = {"name": name, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        checks.append(item)

    def reverse(path, transformations):
        original = current(path)
        restored = original
        steps = []
        for old, replacement, count in transformations:
            occurrences = restored.count(old)
            steps.append({"removed_or_replaced_utf8": old.decode(), "occurrences": occurrences,
                          "expected_occurrences": count})
            if occurrences == count:
                restored = restored.replace(old, replacement)
        passed = restored == base(path) and all(s["occurrences"] == s["expected_occurrences"] for s in steps)
        check("strict_compatibility_reversal:" + path, passed)
        return {"path": path, "baseline_sha256": sha(base(path)), "current_sha256": sha(original),
                "restored_sha256": sha(restored), "exact_baseline_after_reversal": passed, "steps": steps}

    tree = git("rev-parse", BASELINE + "^{tree}").decode().strip()
    check("pinned_baseline_tree", tree == BASELINE_TREE, tree)
    inventory = {}
    for entry in git("ls-tree", "-r", "-z", "--full-tree", BASELINE).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if kind != "blob":
            raise RuntimeError("Non-blob baseline path: " + raw_path.decode())
        inventory[raw_path.decode()] = {"mode": mode, "oid": oid}
    before_path = PREFIX + "old-validation-before.json"
    before_bytes = current(before_path)
    before = json.loads(before_bytes)
    old_paths = sorted(p for p in inventory if p.startswith("validation/"))
    check("old_validation_before_inventory", set(before) == set(old_paths), {
        "before_count": len(before), "baseline_count": len(old_paths),
        "missing_from_before": sorted(set(old_paths) - set(before)),
        "not_in_baseline": sorted(set(before) - set(old_paths))})
    check("old_validation_count_3590", len(before) == len(old_paths) == 3590)
    baseline_record = json.loads(current(PREFIX + "baseline.json"))
    check("baseline_record_identity", baseline_record["baseline_commit"] == BASELINE and
          baseline_record["baseline_tree"] == tree and baseline_record["old_validation_files"] == 3590)

    blobs = GitBlobs(repo)
    rows = {}
    try:
        for path, entry in sorted(inventory.items()):
            rows[path] = {"baseline_git_blob": entry["oid"], "baseline_sha256": blobs.digest(entry["oid"]),
                          "current_sha256": disk_hash(repo / path)}
    finally:
        blobs.close()
    old_rows = {}
    for path in old_paths:
        old_rows[path] = {"before_sha256": before.get(path), **rows[path]}
    old_mismatches = [p for p, row in old_rows.items()
                      if not row["before_sha256"] == row["baseline_sha256"] == row["current_sha256"]]
    check("old_validation_three_way_sha256", not old_mismatches, old_mismatches)

    legacy_paths = {"tests/unit.cpp": (6, 1, b'm'), "tests/revalidation.cpp": (None, 1, b'method'),
                    "tests/revocation.cpp": (4, 1, b'method'), "tests/site_publication.cpp": (4, 3, b'method'),
                    "tests/crawl.cpp": (None, 1, b'method'), "tests/retention.cpp": (4, 1, b'method')}
    allowed_modified = set(legacy_paths) | {
        ".github/workflows/ci.yml", "CMakeLists.txt", "CMakePresets.json", "HANDOFF_START_HERE.md", "README.md",
        "apps/searchd/main.cpp", "docs/API.md", "docs/DEVELOPMENT_STATUS.md", "docs/GITHUB_HANDOFF.md",
        "docs/SHARED_READS.md", "docs/handoff/NEXT_AGENT.md", "docs/implementation_status.json",
        "include/bzs/error.hpp", "include/bzs/web.hpp", "python/bzs/core_client.py", "python/bzs/mcp_server.py",
        "src/store.cpp", "src/web.cpp", "tests/cache_freshness.cpp", "tests/integration.py"}
    modified = sorted(p for p, row in rows.items() if row["baseline_sha256"] != row["current_sha256"])
    check("only_authorized_baseline_files_changed", not (set(modified) - allowed_modified),
          sorted(set(modified) - allowed_modified))
    untracked = set(git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
    tracked = set(git("ls-files", "-z").decode().split("\0")) - {""}
    added = (untracked | tracked) - set(inventory)
    allowed_new = {"docs/DIRECT_SHARED_READS.md", "tests/direct_shared.cpp", "python/tests/test_mcp_read.py"}
    unexpected_new = sorted(p for p in added if p not in allowed_new and not p.startswith(PREFIX))
    check("only_authorized_new_source_or_evidence_paths", not unexpected_new, unexpected_new)

    categories = {}
    prefixes = {"planning": "docs/planning/", "configuration": "config/", "golden": "tests/golden/",
                "fixtures": "tests/fixtures/"}
    for name, prefix in prefixes.items():
        selected = sorted(p for p in inventory if p.startswith(prefix))
        actual = sorted(str(p.relative_to(repo)) for p in (repo / prefix).rglob("*") if p.is_file())
        bad = [p for p in selected if rows[p]["baseline_sha256"] != rows[p]["current_sha256"]]
        passed = not bad and selected == actual
        categories[name] = {"files": selected, "count": len(selected), "byte_identical": passed,
                            "changed": bad, "inventory_added": sorted(set(actual) - set(selected)),
                            "inventory_missing": sorted(set(selected) - set(actual))}
        check("immutable_category:" + name, passed)
    locks = sorted(p for p in inventory if not p.startswith("validation/") and
                   ("lock" in Path(p).name.lower() or p in {
                       "python/pyproject.toml", "python/bzs/__init__.py", "tools/requirements-test.txt"}))
    check("dependency_locks_and_python_version_files", all(rows[p]["baseline_sha256"] == rows[p]["current_sha256"] for p in locks), locks)

    status_path = "docs/implementation_status.json"
    old_status = json.loads(base(status_path))
    status = json.loads(current(status_path))
    old_features, features = raw_member(base(status_path), "features"), raw_member(current(status_path), "features")
    check("features_original_array_bytes", old_features == features,
          {"baseline_sha256": sha(old_features), "current_sha256": sha(features), "bytes": len(features),
           "members": len(status["features"]), "comparison": "exact original JSON value bytes, including internal whitespace"})
    metrics = {k: status.get(k) for k in (
        "version", "status_counts", "partial_work_packages", "not_started", "full_work_packages_accepted",
        "milestones_accepted", "slices_accepted", "planned_acceptance_items", "planned_acceptance_suite_executed",
        "planned_work_packages", "s0_live_sources", "s1_acceptance", "s2_acceptance", "full_product_independent_review",
        "source_availability", "site_discovery_availability", "site_crawl_availability")}
    check("original_progress_and_availability_metrics", all(value == old_status.get(key) for key, value in metrics.items()), metrics)
    check("feature_status_totals", Counter(f["status"] for f in status["features"]) == Counter({"partial": 37, "not_started": 46}) and
          not any(f["full_work_package_accepted"] for f in status["features"]))
    backlog = json.loads(current("docs/planning/requirements_and_backlog.json"))
    slices = json.loads(current("docs/planning/slices_v1.2.json"))
    check("original_planning_83_161_10", len(backlog["features"]) == len(slices["packages"]) == 83 and
          len(backlog["acceptance"]) == len(slices["acceptance_mapping"]) == 161 and len(slices["slices"]) == 10)
    sources = json.loads(current("config/sources.json"))["sources"]
    sites = json.loads(current("config/sites.json"))["sites"]
    source_metrics = {"registered": len(sources), "enabled": sum(s.get("enabled") is True for s in sources),
                      "contract_enabled": sum(s.get("contract", {}).get("enabled") is True for s in sources),
                      "approved": sum(s.get("contract", {}).get("review", {}).get("status") == "approved" for s in sources),
                      "registered_sites": len(sites)}
    check("sources_17_disabled_unapproved_sites_0", source_metrics == {
        "registered": 17, "enabled": 0, "contract_enabled": 0, "approved": 0, "registered_sites": 0}, source_metrics)

    migration_start = b"  void Store::migrate() {"
    migration_end = b"  std::int64_t Store::epoch_locked("
    migrations = []
    for data in (base("src/store.cpp"), current("src/store.cpp")):
        start = data.index(migration_start)
        migrations.append(data[start:data.index(migration_end, start)])
    check("schema_migration_original_bytes", migrations[0] == migrations[1], {
        "baseline_sha256": sha(migrations[0]), "current_sha256": sha(migrations[1]), "bytes": len(migrations[1])})
    schema_markers = [re.findall(rb"PRAGMA user_version(?:=[0-9]+)?|version<=\d+", m) for m in migrations]
    check("schema_15_unchanged", schema_markers[0] == schema_markers[1] and b"version<=15" in schema_markers[1] and
          schema_markers[1][-1] == b"PRAGMA user_version=15" and status["integration_inputs"]["schema"] == 15,
          [x.decode() for x in schema_markers[1]])
    versions = {
        "cmake": re.search(rb"project\(bzs861 VERSION ([^ ]+)", current("CMakeLists.txt")).group(1).decode(),
        "python_project": re.search(rb'^version = "([^"]+)"', current("python/pyproject.toml"), re.M).group(1).decode(),
        "python_module": re.search(rb'__version__ = "([^"]+)"', current("python/bzs/__init__.py")).group(1).decode()}
    check("all_product_versions_0_1_10", set(versions.values()) == {"0.1.10"}, versions)
    tokenizer = "include/bzs/tokenizer.hpp"
    check("index_format_original_bytes", rows[tokenizer]["baseline_sha256"] == rows[tokenizer]["current_sha256"] and
          b'bzs-ascii-cjk/2+codepoint-grams/1' in current(tokenizer) and
          status["integration_inputs"]["index_format"] == "bzs-ascii-cjk/2+codepoint-grams/1")

    cmake_addition = b'''  add_executable(bzs_direct_shared_tests tests/direct_shared.cpp)
  target_link_libraries(bzs_direct_shared_tests PRIVATE bzs_core Threads::Threads)
  if(MSVC)
    target_compile_options(bzs_direct_shared_tests PRIVATE /W4 /utf-8)
  else()
    target_compile_options(bzs_direct_shared_tests PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  endif()
  add_test(NAME direct_shared COMMAND bzs_direct_shared_tests)
  set_tests_properties(direct_shared PROPERTIES TIMEOUT 90)
'''
    gates = [reverse("CMakeLists.txt", [(cmake_addition, b"", 1)]),
             reverse("CMakePresets.json", [(b'        "bzs_direct_shared_tests",\n', b"", 1)]),
             reverse(".github/workflows/ci.yml", [(b'          ./build-asan/bzs_direct_shared_tests\n', b"", 1),
                                                   (b'revalidation|direct_shared|revocation', b'revalidation|revocation', 1)])]
    old_targets = re.findall(rb"add_test\(NAME\s+(\w+)", base("CMakeLists.txt"))
    targets = re.findall(rb"add_test\(NAME\s+(\w+)", current("CMakeLists.txt"))
    check("existing_ctest_order_and_names_retained", [t for t in targets if t != b"direct_shared"] == old_targets and
          targets.count(b"direct_shared") == 1, {"baseline": [t.decode() for t in old_targets], "current": [t.decode() for t in targets]})

    compatibility = []
    for path, (indent, count, variable) in legacy_paths.items():
        branch = LEGACY_PROFILE.replace(b'method==', variable + b'==')
        added_branch = branch if indent is None else b" " * indent + branch + b"\n"
        compatibility.append(reverse(path, [(added_branch, b"", count)]))
    old_check = b'  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2&&w.profiles==0);\n'
    new_check = b'  const auto profiles_before=w.profiles;\n  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2&&w.profiles==profiles_before);\n'
    compatibility.append(reverse("tests/cache_freshness.cpp", [(new_check, old_check, 1)]))
    assertion_exception = {"path": "tests/cache_freshness.cpp", "test": "old_worker_metadata_requires_refresh_and_no_cache_only_network",
                           "old": "w.requests==2&&w.profiles==0", "new": "w.requests==2&&w.profiles==profiles_before",
                           "reason": "Earlier successful misses now request a local profile. The exact capture immediately before cache_only preserves zero additional profile calls for that operation, E_CACHE_STALE, and exactly two physical requests.",
                           "all_original_assertion_bytes_identical": False,
                           "operation_scoped_no_network_assertion_preserved": True}
    integration_path = "tests/integration.py"
    old_http, new_http = base(integration_path).decode(), current(integration_path).decode()
    old_nodes = {n.name: n for n in ast.parse(old_http).body if isinstance(n, ast.ClassDef)}
    new_nodes = {n.name: n for n in ast.parse(new_http).body if isinstance(n, ast.ClassDef)}
    original_classes = {name: ast.get_source_segment(old_http, node) == ast.get_source_segment(new_http, new_nodes[name])
                        for name, node in old_nodes.items() if name != "HeldWorker"}
    check("http_original_classes_and_assertions_exact", all(original_classes.values()), original_classes)
    added_class = ast.get_source_segment(new_http, new_nodes["DirectReadReceipts"]).encode()
    reply = b'''            def reply(self, result):
                body = json.dumps({'result': result}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)

'''
    old_response = b'''                    body = json.dumps({'result': {'status': 'timeout', 'error': 'released test RPC'}}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(body)
'''
    compatibility.append(reverse(integration_path, [
        (b"\n\n" + added_class + b"\n\n", b"", 1),
        (b"    def __init__(self, response=None):\n", b"    def __init__(self):\n", 1),
        (b"        self.response = response\n        self.profile = hashlib.sha256(b'http-direct-read-receipt-fixture').hexdigest()\n", b"", 1),
        (reply, b"", 1),
        (b"                if self.path != '/rpc':\n                    self.send_error(400)\n                    return\n                if request['method'] == 'fetch.profile':\n                    self.reply({'version': 'bzs-http-freshness-1', 'request_profile': fixture.profile})\n                    return\n                if request['method'] != 'fetch.extract':\n",
         b"                if self.path != '/rpc' or request['method'] != 'fetch.extract':\n", 1),
        (b"                    result = (fixture.response(request['params'], fixture.profile) if fixture.response else\n                              {'status': 'timeout', 'error': 'released test RPC'})\n                    self.reply(result)\n", old_response, 1)]))
    old_test_paths = [p for p in inventory if p.startswith(("tests/", "python/tests/")) and p.endswith((".cpp", ".py"))]
    compatibility_paths = {item["path"] for item in compatibility}
    untouched_test_paths = [p for p in old_test_paths if p not in compatibility_paths]
    check("all_other_original_test_source_bytes", all(rows[p]["baseline_sha256"] == rows[p]["current_sha256"] for p in untouched_test_paths), untouched_test_paths)
    native_names = {}
    for path in old_test_paths:
        if path.endswith(".cpp"):
            old_names = re.findall(rb"^\s*TEST\(([^)]+)\)", base(path), re.M)
            names = re.findall(rb"^\s*TEST\(([^)]+)\)", current(path), re.M)
            native_names[path] = {"old_count": len(old_names), "current_count": len(names), "same_order_and_names": old_names == names}
    check("all_original_native_test_names_preserved", all(v["same_order_and_names"] for v in native_names.values()), native_names)
    formal = current("tests/direct_shared.cpp")
    v3 = current(PREFIX + "regression-author/direct-shared-v3-input.cpp")
    v4 = current(PREFIX + "regression-author/direct-shared-v4-input.cpp")
    v5 = current(PREFIX + "regression-author/direct-shared-v5-input.cpp")
    fragment = current(PREFIX + "regression-author/direct-shared-v5-added-group.cpp.fragment")
    removed_new_oracle = b'    CHECK(frozen.at("representation_publication").at("snippet_text").str()==large_body);\n'
    check("direct_shared_first_22_groups_preserved", formal == v5 and formal.count(fragment) == 1 and formal.replace(fragment, b"", 1) == v3 and
          v4.count(removed_new_oracle) == 1 and v4.replace(removed_new_oracle, b"", 1) == v5,
          {"formal_sha256": sha(formal), "v3_sha256": sha(v3), "v4_sha256": sha(v4), "v5_sha256": sha(v5),
           "new_test_oracle_correction": "Only the newly added representation_publication.snippet_text assertion was removed; direct representations may legally store an empty separate policy.",
           "declared_groups": len(re.findall(rb"^TEST\(", formal, re.M))})

    # Bind every changed source/document and the new formal tests to this run.
    bound_paths = sorted(set(modified) | allowed_new | {before_path, PREFIX + "baseline.json"})
    bindings = {p: disk_hash(repo / p) for p in bound_paths}
    check("inputs_stable_during_audit", all(bindings[p] == rows[p]["current_sha256"] for p in modified),
          {"comparison": "hash at initial inventory versus final binding", "paths": modified})
    failures = [item["name"] for item in checks if not item["passed"]]
    report = {"kind": "static_preservation_audit", "audit_revision": 2,
              "changes_from_v1": ["Explicitly include the final README.md handoff update among allowed documentation changes.",
                                  "Bind final v5 test input and verify its one-assertion new-test oracle correction against preserved v4 and original v3."],
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "invocation": [sys.executable, *sys.argv],
              "repository": str(repo), "baseline_commit": BASELINE, "baseline_tree": tree,
              "observed_head": git("rev-parse", "HEAD").decode().strip(),
              "script_sha256": disk_hash(Path(__file__).resolve()), "before_inventory_sha256": sha(before_bytes),
              "runtime_product_tests_executed": 0, "counts_are_static_source_or_file_counts_only": True,
              "passed": not failures, "failed_checks": failures, "checks": checks,
              "old_validation_three_way_hashes": old_rows, "protected_categories": categories,
              "non_validation_baseline_file_hashes": {p: row for p, row in rows.items() if not p.startswith("validation/")},
              "build_gate_reversals": gates, "legacy_test_compatibility_reversals": compatibility,
              "unsupported_profile_fixture_branches": sum(value[1] for value in legacy_paths.values()),
              "explicit_assertion_compatibility_exception": assertion_exception,
              "final_input_sha256": bindings,
              "limits": ["Static preservation is not functional, concurrency, sanitizer, HTTP, or acceptance validation.",
                         "No remote branch state is queried or changed; this audit does not prove remote immutability.",
                         "The features JSON array and original metrics are frozen; explanatory fields outside that array may change."]}
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    with output.open("xb") as destination:
        destination.write(encoded)
    with sidecar.open("x", encoding="utf-8") as destination:
        destination.write(sha(encoded) + "  " + output.name + "\n")
    print(json.dumps({"record": str(output), "sha256": sha(encoded), "passed": not failures,
                      "failed_checks": failures, "old_validation_files": len(old_rows),
                      "features_sha256": sha(features), "static_checks": len(checks),
                      "runtime_product_tests_executed": 0}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
