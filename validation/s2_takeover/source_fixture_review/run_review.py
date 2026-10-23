"""Independent positive/negative source-publication HTTP fixture verification."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import subprocess

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True, type=Path)
parser.add_argument("--server", required=True, type=Path)
parser.add_argument("--python", required=True)
parser.add_argument("--prefix", required=True)
args = parser.parse_args()
repo, server = args.repo.resolve(), args.server.resolve()
if not args.prefix.replace("-", "").replace("_", "").isalnum():
    raise SystemExit("prefix must be a filename component")
log_path, record_path = HERE / (args.prefix + ".log"), HERE / (args.prefix + ".json")
if log_path.exists() or record_path.exists():
    raise SystemExit("refusing to overwrite validation evidence")
files = [*sorted((repo / "src").rglob("*.cpp")), *sorted((repo / "include").rglob("*.hpp")),
         *sorted((repo / "python" / "bzs").rglob("*.py")), repo / "tests" / "e2e_web.py",
         repo / "python" / "tests" / "fakeweb.py", repo / "docs" / "API.md", repo / "docs" / "SOURCE_PUBLICATION.md"]
digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs():
    return {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
            "files_sha256": {str(path.relative_to(repo)): digest(path) for path in files},
            "binary_sha256": digest(server), "runner_sha256": digest(__file__)}


command = [args.python, str(repo / "tests" / "e2e_web.py"), "--repo", str(repo), "--server", str(server),
           "--python", args.python, "EndToEnd.test_02_web_search_merges_sources",
           "EndToEnd.test_02a_explicit_snippet_permission_preserves_search_engine_text"]
record = {"command": command, "cwd": str(repo), "started_at": datetime.now(timezone.utc).isoformat(),
          "network_scope": "fixture HTTP and Worker/Core RPC on loopback only", "before": inputs()}
env = dict(os.environ, PYTHONPATH=str(repo / "python"), PYTHONDONTWRITEBYTECODE="1")
with log_path.open("x") as log:
    result = subprocess.run(command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
record.update(exit_code=result.returncode, finished_at=datetime.now(timezone.utc).isoformat(), after=inputs(), log_sha256=digest(log_path))
record["inputs_unchanged_during_command"] = record["before"] == record["after"]
record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: record[key] for key in ("exit_code", "inputs_unchanged_during_command", "log_sha256")}))
print(log_path.read_text())
raise SystemExit(result.returncode or (0 if record["inputs_unchanged_during_command"] else 1))
