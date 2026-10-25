#!/usr/bin/env python3
"""Record one local validation without replacing earlier results."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent / "local"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    result = {}
    for folder in ("src", "include", "apps", "tests", "python/bzs", "python/tests", "config", "tools"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc",):
                result[path.relative_to(ROOT).as_posix()] = digest(path)
    for name in ("CMakeLists.txt", "CMakePresets.json", "python/pyproject.toml", ".github/workflows/ci.yml"):
        result[name] = digest(ROOT / name)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    target = EVIDENCE / (args.label + ".json")
    output = EVIDENCE / (args.label + ".log")
    if target.exists() or output.exists():
        raise SystemExit("Evidence label already exists; choose another label")
    before = inputs()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tick = time.monotonic()
    with output.open("wb") as stream:
        try:
            run = subprocess.run(command, cwd=ROOT / args.cwd, stdout=stream,
                                 stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
            code = run.returncode
        except subprocess.TimeoutExpired:
            stream.write(b"\nVALIDATION PROCESS TIMEOUT\n")
            code = 124
        except OSError as error:
            stream.write((f"PROCESS DID NOT START: {type(error).__name__}: {error}\n").encode())
            code = 125
    after = inputs()
    record = {"command": command, "cwd": str(ROOT / args.cwd), "started_utc": started,
              "seconds": round(time.monotonic() - tick, 3), "exit_code": code,
              "output": output.name, "output_sha256": digest(output),
              "inputs_unchanged": before == after, "inputs_before": before,
              "changed_inputs": sorted(key for key in before.keys() | after.keys()
                                       if before.get(key) != after.get(key))}
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: record[key] for key in ("exit_code", "seconds", "inputs_unchanged", "changed_inputs", "output")}), flush=True)
    if code:
        print(output.read_text(errors="replace")[-10000:], flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
