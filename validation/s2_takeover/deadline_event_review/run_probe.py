"""Compile one frozen independent probe against a captured exact library.

The snapshot and executable live outside the repository. Existing evidence is
never overwritten. A baseline run expects the two deadline cases to fail while
all four prior-winner/nondeadline controls pass; the corrected run expects zero
failures. These are recorded separately from the actual process exit status.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = HERE / "store_deadline_probe.cpp"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--expected-failed", type=int, choices=(0, 2), required=True)
    parser.add_argument("--freeze", default="probe-freeze-v1.json")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        parser.error("label must be a safe new evidence name")
    if Path(args.freeze).name != args.freeze:
        parser.error("freeze must name one file in the evidence directory")
    snapshot = args.snapshot.resolve()
    library = snapshot / "libbzs_core.a"
    capture = json.loads((snapshot / "capture.json").read_text())
    inputs = {str(PROBE): digest(PROBE), str(Path(__file__).resolve()): digest(Path(__file__))}
    inputs.update({str(snapshot / rel): digest(snapshot / rel) for rel in capture["copied_inputs"]})
    for rel, expected in capture["copied_inputs"].items():
        if inputs[str(snapshot / rel)] != expected:
            raise RuntimeError(f"captured input changed: {rel}")
    frozen = {"schema_version": 1, "role": "independent_store_deadline_reviewer",
              "probe_sha256": digest(PROBE), "runner_sha256": digest(Path(__file__))}
    freeze_path = HERE / args.freeze
    if freeze_path.exists():
        if json.loads(freeze_path.read_text()) != frozen:
            raise RuntimeError("probe source or runner changed since its named freeze")
    else:
        save(freeze_path, frozen)

    binary = snapshot / (args.label + "-store-probe")
    if binary.exists():
        raise RuntimeError("compiled probe already exists; choose a fresh label")
    command = ["/usr/bin/c++", "-std=c++20", "-O0", "-g", "-Wall", "-Wextra",
               "-Wpedantic", "-Wconversion", "-Wshadow", "-Werror",
               "-I" + str(snapshot / "include"), str(PROBE), str(library),
               "-lsqlite3", "-lxml2", "-ljson-c", "-lcrypto", "-pthread", "-o", str(binary)]
    record = {"schema_version": 1, "role": "independent_store_deadline_reviewer",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "repo_head_at_execution": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "frozen_probe": args.freeze, "freeze_sha256": digest(freeze_path),
              "snapshot_capture": capture, "input_sha256": inputs,
              "compile_argv": command, "cwd": str(ROOT),
              "expected_probe_failed_cases": args.expected_failed,
              "original_e2e_sha256": digest(ROOT / "tests/e2e_session_recovery.py")}
    started = time.monotonic()
    compile_log = HERE / (args.label + "-compile.log")
    with compile_log.open("x", encoding="utf-8") as stream:
        compiled = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    record.update(compile_exit_code=compiled.returncode,
                  compile_seconds=round(time.monotonic() - started, 3),
                  compile_log=compile_log.name, compile_log_sha256=digest(compile_log))
    if compiled.returncode:
        save(HERE / (args.label + "-record.json"), record)
        return compiled.returncode
    before_mode = binary.stat().st_mode
    binary.chmod(before_mode | stat.S_IXUSR)
    record["executable_user_bit_added"] = not bool(before_mode & stat.S_IXUSR)
    record["binary_sha256"] = digest(binary)
    record["probe_argv"] = [str(binary)]
    run_log = HERE / (args.label + "-probe.json")
    started = time.monotonic()
    with run_log.open("x", encoding="utf-8") as stream:
        run = subprocess.run([str(binary)], cwd=snapshot, stdout=stream, stderr=subprocess.STDOUT)
    actual = json.loads(run_log.read_text())
    unchanged = all(digest(Path(path)) == value for path, value in inputs.items())
    expected_names = ({"direct_deadline_terminal_once_at_wall_D_minus_1",
                       "last_source_deadline_preserves_frozen_prefix"}
                      if args.expected_failed else set())
    failed_names = {item["name"] for item in actual["cases"] if not item["passed"]}
    matched = (run.returncode == (1 if args.expected_failed else 0)
               and actual["failed"] == args.expected_failed
               and len(actual["cases"]) == 6 and failed_names == expected_names)
    record.update(probe_exit_code=run.returncode, probe_seconds=round(time.monotonic() - started, 3),
                  probe_log=run_log.name, probe_log_sha256=digest(run_log),
                  passed_cases=actual["passed"], failed_cases=actual["failed"], assertions=actual["assertions"],
                  expected_outcome_matched=matched, input_hashes_unchanged=unchanged,
                  binary_unchanged=digest(binary) == record["binary_sha256"],
                  finished_at_utc=datetime.now(timezone.utc).isoformat())
    save(HERE / (args.label + "-record.json"), record)
    print(json.dumps({key: record[key] for key in ["probe_exit_code", "passed_cases", "failed_cases",
                                                "assertions", "expected_outcome_matched", "input_hashes_unchanged"]}))
    return 0 if matched and unchanged and record["binary_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
