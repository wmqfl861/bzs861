"""Keep an immutable command/input/log record for the independent HTTP review."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs(repo, server):
    files = [*sorted((repo / "src").rglob("*.cpp")), *sorted((repo / "include").rglob("*.hpp")),
             *sorted((repo / "python" / "bzs").rglob("*.py")), repo / "tests" / "e2e_revalidation.py",
             repo / "tests" / "e2e_web.py"]
    return {"repo": str(repo), "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
            "files_sha256": {str(path.relative_to(repo)): digest(path) for path in files},
            "binary_path": str(server), "binary_sha256": digest(server),
            "probe_sha256": digest(HERE / "probe_http_policy.py"), "runner_sha256": digest(__file__)}


parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, default=ROOT)
parser.add_argument("--server", type=Path, required=True)
parser.add_argument("--python", default=sys.executable)
parser.add_argument("--prefix", required=True)
args = parser.parse_args()
repo, server = args.repo.resolve(), args.server.resolve()
if not args.prefix.replace("-", "").replace("_", "").isalnum():
    raise SystemExit("prefix must be a filename component")
log_path, metadata_path = HERE / (args.prefix + ".log"), HERE / (args.prefix + ".json")
if log_path.exists() or metadata_path.exists():
    raise SystemExit("refusing to overwrite validation evidence")
command = [args.python, str(HERE / "probe_http_policy.py"), "--repo", str(repo), "--server", str(server), "--python", args.python]
record = {"command": command, "cwd": str(ROOT), "started_at": datetime.now(timezone.utc).isoformat(),
          "network_scope": "fixture HTTP and Worker/Core RPC on loopback only", "before": inputs(repo, server)}
env = dict(os.environ, PYTHONPATH=str(repo / "python"), PYTHONDONTWRITEBYTECODE="1")
with log_path.open("x") as log:
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
record.update(exit_code=result.returncode, finished_at=datetime.now(timezone.utc).isoformat(), after=inputs(repo, server),
              log_sha256=digest(log_path))
record["inputs_unchanged_during_command"] = record["before"] == record["after"]
metadata_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: record[key] for key in ("exit_code", "inputs_unchanged_during_command", "log_sha256")}))
print("\n".join(log_path.read_text().splitlines()[-5:]))
raise SystemExit(result.returncode)
