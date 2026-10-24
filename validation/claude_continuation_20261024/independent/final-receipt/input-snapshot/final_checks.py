"""Verify the reviewed documentation delta and final HTTP sanitizer build.

The complete final product suite is p2-9. This stage does not rerun or add
counts to that suite: it checks the subsequent documentation-only change,
planning guards, dependencies, and the server target omitted by asan-core.
"""
from pathlib import Path
import json
import re
import sys

import run_capture as capture

ROOT, OUT = capture.ROOT, capture.OUT
STAGE = "final-verification"
folder = OUT / STAGE
folder.mkdir(exist_ok=True)
helpers = [Path(__file__), OUT / "verify_preserved_scope.py",
           OUT / "verify_http_sanitizer.py", OUT / "select_http_tests.py"]
helper_hashes = {str(p.relative_to(ROOT)): capture.sha(p) for p in helpers}
capture.save(folder / "helper-inputs.json", {"files": helper_hashes})

previous = json.loads((OUT / "p2-9/execution-inputs.json").read_bytes())["files"]
current = capture.inputs()
draft = json.loads((OUT / "author/documentation/handoff-docs-manifest.json").read_bytes())
allowed = {item["path"]: item for item in draft["files"]}
changed = {name for name in set(previous) | set(current) if previous.get(name) != current.get(name)}
assert changed == set(allowed), changed ^ set(allowed)
for name in changed:
    assert previous.get(name) == allowed[name]["before_sha256"], name
    assert current[name] == allowed[name]["after_sha256"], name
capture.save(folder / "documentation-delta.json", {
    "last_full_gate": "p2-9/result.json",
    "last_full_inputs_sha256": capture.sha(OUT / "p2-9/execution-inputs.json"),
    "reviewed_patch_sha256": capture.sha(OUT / "author/documentation/handoff-docs.patch"),
    "only_changed_inputs": {name: allowed[name] for name in sorted(changed)},
    "all_product_test_build_and_recorder_inputs_unchanged": True,
})

python = str(capture.PYTHON)
for name, argv in [
    ("preserved-scope", [python, str(OUT / "verify_preserved_scope.py")]),
    ("planning-contracts", [python, "docs/planning/tools/validate_contracts.py"]),
    ("planning-structure", [python, "docs/planning/tools/plan_v1_2.py", "check"]),
    ("planning-guard", [python, "tools/test_plan_guard.py"]),
    ("python-dependencies", [python, "-m", "pip", "check"]),
]:
    capture.record(STAGE, name, argv)

capture.record(STAGE, "configure-release", ["cmake", "--preset", "release",
    "-DBZS_PYTHON=" + python, "-DBZS_REQUIRE_PYTHON_TESTS=ON"], build=True)
capture.record(STAGE, "build-release-core", ["cmake", "--build", "build-release",
    "--target", "bzs_core", "-j2"], build=True)
capture.record(STAGE, "build-asan-http", ["cmake", "--build", "build-asan",
    "--target", "searchd", "-j2"], build=True)
server = ROOT / "build-asan/searchd"
server_hash = capture.sha(server)
assert server.read_bytes()[:4] == b"\x7fELF" and not server.is_symlink()
server.chmod(server.stat().st_mode | 0o111)
assert capture.sha(server) == server_hash
capture.save(folder / "asan-http-executable.json", {
    "path": str(server.relative_to(ROOT)), "sha256": server_hash,
    "mode_only_preparation": True,
})
capture.record(STAGE, "asan-http", [python, str(OUT / "verify_http_sanitizer.py"),
    "--server", str(server), "--cli", str(ROOT / "build/searchctl"),
    "--test", "HTTPExecution"], asan=True, timeout=180)
http_log = (folder / "asan-http.log").read_text()
http_counts = re.findall(r"^Ran (\d+) tests? in ", http_log, re.MULTILINE)
assert http_counts == ["3"] and "skipped=" not in http_log
http_result = json.loads(http_log[http_log.index('{\n  "harness_sha256"'):])
assert len(http_result["shutdowns"]) == 2
assert all(item["exit_code"] == 0 for item in http_result["shutdowns"])

assert capture.inputs() == current
assert helper_hashes == {str(p.relative_to(ROOT)): capture.sha(p) for p in helpers}
capture.save(folder / "result.json", {
    "kind": "targeted_post_integration_verification",
    "all_commands_passed": True,
    "complete_final_product_gate": "p2-9/result.json",
    "complete_final_product_gate_sha256": capture.sha(OUT / "p2-9/result.json"),
    "execution_inputs_sha256": capture.sha(folder / "execution-inputs.json"),
    "only_change_after_full_product_gate": "eight independently reviewed documentation files",
    "documentation_delta_sha256": capture.sha(folder / "documentation-delta.json"),
    "helper_inputs_sha256": capture.sha(folder / "helper-inputs.json"),
    "source_and_helper_inputs_unchanged": True,
    "extra_http_sanitizer_cases": 3,
    "extra_http_servers_checked": len(http_result["shutdowns"]),
    "extra_http_servers_exit_zero_and_sanitizer_clean": True,
    "release_library_sha256": capture.sha(ROOT / "build-release/libbzs_core.a"),
    "asan_http_server_sha256": server_hash,
    "scope": "Planning/dependency/documentation gates and final HTTP sanitizer lifecycle; not another full suite or product acceptance.",
})
print(json.dumps({"stage": STAGE, "all_commands_passed": True}), flush=True)
