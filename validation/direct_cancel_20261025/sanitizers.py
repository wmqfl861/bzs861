#!/usr/bin/env python3
"""Run every native target once with its sanitizer build and bounded waits."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[2]
targets = json.loads((root / "CMakePresets.json").read_text())["buildPresets"][2]["targets"]
records = []
for name in targets:
    binary = root / "build-asan" / name
    before = hashlib.sha256(binary.read_bytes()).hexdigest()
    command = [str(binary)]
    if name == "bzs_contract_tests":
        command.append("docs/planning/validation/contract_cases.json")
    started = time.monotonic()
    print("SANITIZER START " + name, flush=True)
    try:
        run = subprocess.run(command, cwd=root, timeout=180, check=False)
        code = run.returncode
    except subprocess.TimeoutExpired:
        code = 124
    records.append({"target": name, "exit_code": code,
                    "seconds": round(time.monotonic() - started, 3),
                    "binary_sha256": before,
                    "binary_unchanged": before == hashlib.sha256(binary.read_bytes()).hexdigest()})
    print("SANITIZER RESULT " + json.dumps(records[-1]), flush=True)
print("SANITIZER SUMMARY " + json.dumps({"targets": len(records),
      "passed": sum(r["exit_code"] == 0 for r in records), "results": records}), flush=True)
raise SystemExit(0 if all(r["exit_code"] == 0 and r["binary_unchanged"] for r in records) else 1)
