#!/usr/bin/env python3
"""Record SESSION author's commands, output, exit status and input hashes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("--cwd", required=True)
parser.add_argument("--hash", action="append", default=[])
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ["--"] else args.command
root = Path(__file__).resolve().parent
def digest(path):
    item = Path(path)
    return hashlib.sha256(item.read_bytes()).hexdigest() if item.is_file() else None
metadata = {"role": "author_self_test", "argv": command, "cwd": args.cwd,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "input_sha256": {path: digest(path) for path in args.hash}}
metadata["source_head"] = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=args.cwd, text=True).strip()
metadata["tracked_patch_sha256"] = hashlib.sha256(subprocess.check_output(
    ["git", "diff", "HEAD", "--"], cwd=args.cwd)).hexdigest()
started = time.monotonic()
with (root / (args.name + ".log")).open("w", encoding="utf-8") as log:
    completed = subprocess.run(command, cwd=args.cwd, stdout=log, stderr=subprocess.STDOUT)
metadata.update(exit_code=completed.returncode, seconds=round(time.monotonic()-started, 3),
                finished_utc=datetime.now(timezone.utc).isoformat(),
                log_sha256=digest(root / (args.name + ".log")))
(root / (args.name + ".json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n")
print(json.dumps(metadata, ensure_ascii=False))
print((root / (args.name + ".log")).read_text(errors="replace")[-16000:])
sys.exit(completed.returncode)
