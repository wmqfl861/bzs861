#!/usr/bin/env python3
"""Run only after the build owner supplies a completed archive and its SHA256."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import resource
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--library-sha256", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.label)
    assert re.fullmatch(r"[a-f0-9]{64}", args.library_sha256)

    base = Path(__file__).resolve().parent
    harness = base / "json_shared_read_harness.cpp"
    assert sha(harness) == "ea7da2ec466056e8503435f9e189d8ce170e45754371f37c73f7784749f47fb9"
    commit = subprocess.check_output(
        ["git", "rev-parse", args.commit], cwd=args.repo, text=True
    ).strip()
    original_source = subprocess.check_output(
        ["git", "show", f"{commit}:src/json.cpp"], cwd=args.repo
    )
    source_sha = hashlib.sha256(original_source).hexdigest()
    assert source_sha == "dc9cbaa7de880db9142d6392fdf4bea2e724d69797392e7bfe04fd60d8dab5a5"
    assert sha(args.repo / "src/json.cpp") == source_sha

    out = base / args.label
    out.mkdir(exist_ok=False)
    (out / "source-input" / "include" / "bzs").mkdir(parents=True)
    (out / "source-input" / "json.cpp").write_bytes(original_source)
    for name in ("json.hpp", "util.hpp", "error.hpp"):
        contents = subprocess.check_output(
            ["git", "show", f"{commit}:include/bzs/{name}"], cwd=args.repo
        )
        (out / "source-input" / "include" / "bzs" / name).write_bytes(contents)

    # The copied archive is the sole input used for linking. A digest mismatch
    # stops the test, including if the producer unexpectedly rewrote the file.
    library = out / "libbzs_core.a"
    shutil.copy2(args.library, library)
    assert sha(library) == args.library_sha256
    binary = out / "integrated-harness"
    command = [
        "/usr/bin/c++", "-std=c++20", "-O1", "-g", "-pthread",
        "-I", str(out / "source-input" / "include"), str(harness), str(library),
        "-lsqlite3", "-lcrypto", "-lxml2", "-ljson-c", "-o", str(binary),
    ]
    with (out / "compile.log").open("w") as log:
        compiled = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cases = []
    if compiled.returncode == 0:
        for mode, iterations in (
            ("serialize", 400), ("reference_aliases", 2000), ("canonical_reads", 400)
        ):
            log_path = out / f"{mode}.log"
            with log_path.open("w") as log:
                try:
                    run = subprocess.run(
                        [str(binary), mode, str(iterations)],
                        stdout=log, stderr=subprocess.STDOUT, timeout=30,
                    )
                    code = run.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            case = {
                "scenario": mode, "threads": 4, "iterations_per_thread": iterations,
                "exit_code": code, "log": str(log_path),
            }
            if code == 0:
                case["result"] = json.loads(log_path.read_text())
            cases.append(case)
            print(json.dumps(case), flush=True)

    context = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Independent frozen three-case harness linked to the final integrated bzs_core archive.",
        "core_commit": commit,
        "library_origin": str(args.library),
        "library_expected_sha256": args.library_sha256,
        "library_copied_sha256": sha(library),
        "json_cpp_sha256": source_sha,
        "harness_sha256": sha(harness),
        "same_harness_as_baseline_and_candidate": True,
        "compile_command": command,
        "compile_exit_code": compiled.returncode,
        "compiler": subprocess.check_output(["/usr/bin/c++", "--version"], text=True).splitlines()[0],
        "cases": cases,
        "sha256": {
            str(path.relative_to(out)): sha(path)
            for path in sorted(out.rglob("*")) if path.is_file()
        },
    }
    context_path = out / "integrated-context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n")
    passed = (
        compiled.returncode == 0 and len(cases) == 3
        and all(case["exit_code"] == 0 and case["result"]["failures"] == 0
                and case["result"]["completed"] == 4 * case["iterations_per_thread"]
                for case in cases)
    )
    report_path = base / "design-review.json"
    report = json.loads(report_path.read_text())
    report["integrated_artifact_validation"] = {
        "passed": passed, "context": str(context_path), "core_commit": commit,
        "library_sha256": sha(library), "json_cpp_sha256": source_sha,
    }
    report["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": passed, "context": str(context_path)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
