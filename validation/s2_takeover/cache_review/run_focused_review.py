"""Immutable final native/profile and Python/response-clock review evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import subprocess

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True, type=Path)
parser.add_argument("--build", required=True, type=Path)
parser.add_argument("--python", required=True)
parser.add_argument("--prefix", required=True)
args = parser.parse_args()
repo, build = args.repo.resolve(), args.build.resolve()
if not args.prefix.replace("-", "").replace("_", "").isalnum():
    raise SystemExit("prefix must be a filename component")
files = [*sorted((repo / "src").rglob("*.cpp")), *sorted((repo / "include").rglob("*.hpp")),
         *sorted((repo / "python" / "bzs").rglob("*.py")), repo / "tests" / "cache_freshness.cpp",
         repo / "python" / "tests" / "test_http_freshness_review.py",
         repo / "python" / "tests" / "test_http_revalidation.py", repo / "python" / "tests" / "test_map_worker.py"]


def inputs():
    return {"repo": str(repo), "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
            "files_sha256": {str(path.relative_to(repo)): digest(path) for path in files},
            "binary_sha256": digest(build / "bzs_cache_freshness_tests"), "core_library_sha256": digest(build / "libbzs_core.a"),
            "runner_sha256": digest(__file__)}


env = dict(os.environ, PYTHONPATH=str(repo / "python"), PYTHONDONTWRITEBYTECODE="1")
cases = [("native", [str(build / "bzs_cache_freshness_tests")], repo),
         ("clock", [args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_http_freshness_review.py"], repo / "python")]
failed = False
for name, command, cwd in cases:
    log_path = HERE / (args.prefix + "-" + name + ".log")
    metadata_path = log_path.with_suffix(".json")
    if log_path.exists() or metadata_path.exists():
        raise SystemExit("refusing to overwrite validation evidence")
    record = {"command": command, "cwd": str(cwd), "started_at": datetime.now(timezone.utc).isoformat(), "before": inputs()}
    with log_path.open("x") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    record.update(exit_code=result.returncode, finished_at=datetime.now(timezone.utc).isoformat(), after=inputs(), log_sha256=digest(log_path))
    record["inputs_unchanged_during_command"] = record["before"] == record["after"]
    metadata_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"group": name, "exit_code": result.returncode, "inputs_unchanged_during_command": record["inputs_unchanged_during_command"]}))
    print("\n".join(log_path.read_text().splitlines()[-3:]))
    failed = failed or result.returncode != 0 or not record["inputs_unchanged_during_command"]
raise SystemExit(1 if failed else 0)
