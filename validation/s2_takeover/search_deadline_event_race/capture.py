"""Capture one focused command and its exact deadline-race source inputs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--label", required=True)
parser.add_argument("--timeout", type=int, default=240)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ["--"] else args.command
if not command:
    parser.error("a command is required after --")
directory = Path(__file__).resolve().parent
repo = directory.parents[2]
log_path, report_path = directory / (args.label + ".log"), directory / (args.label + ".json")
if log_path.exists() or report_path.exists():
    raise SystemExit("Refusing to overwrite existing evidence")
paths = ("src/web.cpp", "src/sessions.cpp", "tests/search_modes.cpp", "tests/e2e_session_recovery.py")
sources = {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths}
base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
matches = {path: hashlib.sha256(subprocess.check_output(["git", "show", base + ":" + path], cwd=repo)).hexdigest() == sources[path] for path in paths}
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(repo / "python"))
started = datetime.now(timezone.utc).isoformat()
tick = time.monotonic()
result = subprocess.run(command, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=args.timeout)
log_path.write_text(result.stdout, encoding="utf-8")
report = {"command": command, "cwd": str(repo), "started_at": started,
          "finished_at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.monotonic() - tick, 3),
          "exit_code": result.returncode, "git_base": base, "source_sha256": sources, "source_matches_base": matches,
          "environment": {key: env[key] for key in ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "ASAN_OPTIONS", "UBSAN_OPTIONS") if key in env},
          "log": log_path.name, "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest()}
report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"label": args.label, "exit_code": result.returncode, "seconds": report["seconds"], "log": str(log_path)}))
raise SystemExit(result.returncode)
