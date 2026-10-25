"""Verify retained history against the real Git baseline and pre-run SHA256 set."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "ef45490c6a0c384d8ad89452e2ba7e4df2cf2340"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    entries = subprocess.check_output(["git", "ls-tree", "-rz", "--full-tree", BASE], cwd=ROOT)
    blobs = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.split()
        if kind == b"blob":
            blobs[name.decode()] = oid.decode()
    before_path = OUT / "old-validation-before.json"
    before = json.loads(before_path.read_bytes())
    original = {name: oid for name, oid in blobs.items() if name.startswith("validation/")}
    assert set(original) == set(before), "Pre-run history set differs from the baseline"
    differences = []
    for name, oid in original.items():
        data = (ROOT / name).read_bytes()
        git_hash = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if git_hash != oid or sha(data) != before[name]:
            differences.append(name)
    assert not differences, differences
    protected = ["config", "tests/golden", "docs/planning", ".github/workflows",
                 "CMakeLists.txt", "CMakePresets.json", "tools/requirements-s1-lock.txt",
                 "tools/requirements-test.txt", "python/pyproject.toml"]
    changed = subprocess.check_output(["git", "diff", "--name-only", BASE, "--", *protected], cwd=ROOT, text=True).splitlines()
    assert not changed, changed
    current_text = (ROOT / "docs/implementation_status.json").read_text()
    prior_text = subprocess.check_output(["git", "show", BASE + ":docs/implementation_status.json"], cwd=ROOT, text=True)
    def feature_bytes(text: str) -> bytes:
        return text.split('  "features": ', 1)[1].split('  "current_results": ', 1)[0].encode()
    assert feature_bytes(current_text) == feature_bytes(prior_text)
    state = json.loads(current_text)
    assert len(state["features"]) == 83
    assert state["status_counts"] == {"partial": 37, "not_started": 46, "accepted": 0}
    assert state["planned_acceptance_items"] == 161
    sources = json.loads((ROOT / "config/sources.json").read_bytes())["sources"]
    sites = json.loads((ROOT / "config/sites.json").read_bytes())["sites"]
    assert len(sources) == 17 and not any(source.get("enabled") for source in sources) and sites == []
    review = subprocess.check_output(["git", "rev-parse", "origin/review/claude-2026-10-24"], cwd=ROOT, text=True).strip()
    assert review == "30f058557e3f7ebb873e9780759323b49cfe9636"
    probe = "validation/s2_takeover/cache_review/probe_http_policy.py"
    result = {"status": "passed", "baseline": BASE, "old_validation_files": len(original),
              "old_validation_differences": differences, "pre_run_manifest": str(before_path.relative_to(ROOT)),
              "pre_run_manifest_sha256": sha(before_path.read_bytes()),
              "supplementary_dependency_binding": {
                  "scope": "Collected separately before the final command, verified against baseline blobs and current post-run bytes; not claimed as part of run.py's direct snapshot",
                  "ctest_historical_probe": probe, "sha256": before[probe]},
              "protected_inputs_unchanged": protected, "feature_json_bytes_unchanged": True,
              "feature_json_sha256": sha(feature_bytes(current_text)), "status_counts": state["status_counts"],
              "sources": {"registered": 17, "enabled": 0, "sites": 0},
              "local_read_only_review_ref": review, "script_sha256": sha(Path(__file__).read_bytes())}
    target = OUT / "final/preservation.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
