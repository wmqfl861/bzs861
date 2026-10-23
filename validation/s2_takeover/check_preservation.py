"""Verify baseline plan, historical evidence, golden bytes and disabled defaults."""
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
BASELINE = "3ef86c653a2e2085848d5cdb151d66392c6af49a"


def old_bytes(path):
    return subprocess.check_output(["git", "show", BASELINE + ":" + path], cwd=REPO)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASELINE], cwd=REPO, text=True
    ).splitlines()
    frozen = [path for path in files if path.startswith("validation/")]
    frozen += [
        "docs/planning/development_blueprint_v1.1.md",
        "docs/planning/functional_spec_v0.3.md",
        "docs/planning/development_plan_v1.1.xlsx",
        "docs/planning/requirements_and_backlog.json",
        "tests/golden/queries.json", "tests/golden/manifest.json",
        "config/sources.json", "config/sites.json",
        "tools/requirements-s1-lock.txt",
    ]
    frozen += [path for path in files if path.startswith((
        "docs/planning/schemas/", "docs/planning/validation/", "docs/planning/review/"
    ))]
    kept = {}
    for path in sorted(set(frozen)):
        before, after = old_bytes(path), (REPO / path).read_bytes()
        require(before == after, "Historical input changed: " + path)
        kept[path] = digest(after)

    plan_path = "docs/planning/slices_v1.2.json"
    before, after = json.loads(old_bytes(plan_path)), json.loads((REPO / plan_path).read_bytes())
    allowed = {"review_revision", "implementation_stage", "scope_policy", "acceptance_mapping"}
    require({k: v for k, v in before.items() if k not in allowed} ==
            {k: v for k, v in after.items() if k not in allowed}, "Plan structure changed")
    require(len(after["packages"]) == 83 and len(after["slices"]) == 10 and
            len(after["acceptance_mapping"]) == 161, "Plan counts changed")
    old_scope, new_scope = dict(before["scope_policy"]), dict(after["scope_policy"])
    old_scope.pop("current_report"); new_scope.pop("current_report")
    require(old_scope == new_scope, "Plan scope or acceptance gate changed")
    for old, new in zip(before["acceptance_mapping"], after["acceptance_mapping"]):
        require(old["execution_status"] == "not_signed_off_for_0.1.9" and
                new["execution_status"] == "not_signed_off_for_0.1.10", "Acceptance signoff changed")
        require({k: v for k, v in old.items() if k != "execution_status"} ==
                {k: v for k, v in new.items() if k != "execution_status"},
                "Acceptance meaning or evidence changed: " + old["id"])

    status_path = "docs/implementation_status.json"
    old_status = json.loads(old_bytes(status_path))
    new_status = json.loads((REPO / status_path).read_bytes())
    require(old_status["status_counts"] == {"partial": 36, "not_started": 47, "accepted": 0}, "Baseline package states changed")
    require(new_status["status_counts"] == {"partial": 37, "not_started": 46, "accepted": 0}, "Current package states changed")
    for old, new in zip(old_status["features"], new_status["features"]):
        for key in ("feature_id", "status", "full_work_package_accepted", "baseline_test_ids",
                    "mandatory_deferred_semantics"):
            if key == "status" and old["feature_id"] == "MODE-01":
                require(old[key] == "not_started" and new[key] == "partial", "MODE-01 must remain partial")
            else:
                require(old.get(key) == new.get(key), "Package requirement changed: " + old["feature_id"])
    sources = json.loads((REPO / "config/sources.json").read_bytes())["sources"]
    require(len(sources) == 17 and all(item["enabled"] is False for item in sources), "Source approval changed")
    require(json.loads((REPO / "config/sites.json").read_bytes())["sites"] == [], "Site approval changed")
    result = {
        "status": "passed", "baseline": BASELINE,
        "packages": 83, "slices": 10, "acceptance_ids": 161,
        "status_counts": new_status["status_counts"],
        "allowed_plan_changes": "Plan execution date/version/report pointer only; all acceptance IDs remain unsigned. MODE-01 changes from not_started to partial in implementation status only.",
        "preserved_file_count": len(kept), "files_sha256": kept,
        "golden_content_inspected_for_tuning": False,
        "golden_check": "Raw byte comparison and SHA-256 only; original 20/15/5 partition manifest unchanged.",
        "enabled_sources": 0, "registered_sites": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
