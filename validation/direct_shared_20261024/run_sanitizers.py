"""Run every configured native sanitizer target with append-only evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("revision")
    args = parser.parse_args()
    if not args.revision or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.revision):
        parser.error("Use a simple revision name")
    presets = json.loads((ROOT / "CMakePresets.json").read_bytes())
    targets = next(item["targets"] for item in presets["buildPresets"] if item["name"] == "asan-core")
    assert len(targets) == len(set(targets)) == 16
    destination = OUT / "asan" / ("execution-" + args.revision + ".json")
    assert not destination.exists(), "Evidence is append-only"
    started = datetime.now(timezone.utc).isoformat()
    results = []
    for target in targets:
        argv = [sys.executable, str(OUT / "run.py"), "--asan", "--timeout", "180", "asan",
                target + "-" + args.revision, "--", "./build-asan/" + target]
        if target == "bzs_contract_tests":
            argv.append("docs/planning/validation/contract_cases.json")
        code = subprocess.run(argv, cwd=ROOT).returncode
        results.append({"target": target, "argv": argv, "recorder_exit_code": code})
    record = {"started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "targets": results, "all_passed": all(item["recorder_exit_code"] == 0 for item in results),
              "counting_rule": "These are sanitizer replays of the same native groups, not additional acceptance cases."}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return 0 if record["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
