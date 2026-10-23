"""Independent, portable regression for the exact-ref HTTP SQLite rows helper.

Retain real connections so garbage collection cannot hide a missing close.
The desired contract is unchanged between the old and fixed ref: rows() must
close its connection on both successful return and a query exception.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()

    def git(*parts: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(repo), *parts])

    commit = git("rev-parse", "--verify", args.ref + "^{commit}").decode().strip()
    paths = ("tests/integration.py", "tests/http_revocation.py")
    sources = {path: git("show", commit + ":" + path) for path in paths}
    integration = ModuleType("integration")
    integration.__file__ = str(repo / paths[0])
    exec(compile(sources[paths[0]], commit + ":" + paths[0], "exec"), integration.__dict__)
    fixture = ModuleType("reviewed_http_revocation")
    fixture.__file__ = str(repo / paths[1])
    with patch.dict(sys.modules, {"integration": integration}):
        exec(compile(sources[paths[1]], commit + ":" + paths[1], "exec"), fixture.__dict__)

    connect = sqlite3.connect
    results = []
    with tempfile.TemporaryDirectory(prefix="bzs-independent-rows-lifecycle-") as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        seed = connect(root / "state" / "metadata.sqlite")
        try:
            seed.execute("CREATE TABLE present_probe_table(value INTEGER)")
        finally:
            seed.close()
        for name, query, expected_error in (
            ("successful_query_closes_connection", "SELECT 1", None),
            ("query_exception_closes_connection", "SELECT * FROM absent_probe_table", sqlite3.OperationalError),
        ):
            retained = []
            modes = []

            def tracked_connect(database, *parts, **options):
                connection = connect(database, *parts, **options)
                retained.append(connection)
                modes.append(str(database).endswith("?mode=ro") and options.get("uri") is True)
                return connection

            returned = None
            error = None
            with patch.object(sqlite3, "connect", tracked_connect):
                try:
                    returned = fixture.RevocationHTTP.rows(SimpleNamespace(root=root), query)
                except Exception as caught:
                    error = caught
            behavior = (
                error is None and returned == [(1,)] if expected_error is None
                else type(error) is expected_error
            )
            closed = []
            # Always release the diagnostic's handles before temporary-directory
            # cleanup, so old-code failure remains observable on Windows too.
            try:
                for connection in retained:
                    try:
                        connection.execute("SELECT 2").fetchall()
                    except sqlite3.ProgrammingError as caught:
                        closed.append("closed" in str(caught).lower())
                    else:
                        closed.append(False)
            finally:
                for connection in retained:
                    connection.close()
            results.append({
                "case": name,
                "query_behavior_preserved": behavior,
                "observed_exception": type(error).__name__ if error is not None else None,
                "connections_retained": len(retained),
                "readonly_uri_preserved": len(modes) == 1 and all(modes),
                "closed_before_diagnostic_cleanup": closed,
                "passed": behavior and len(retained) == 1 and all(modes) and all(closed),
            })

    failures = sum(not case["passed"] for case in results)
    report = {
        "schema": "bzs-independent-http-rows-lifecycle-1",
        "reviewer": "/root/source_policy",
        "method": "retain real sqlite3.Connection references; require explicit close after normal return and SQL error",
        "source_commit": commit,
        "source_tree": git("rev-parse", commit + "^{tree}").decode().strip(),
        "source_files": {path: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for path, data in sources.items()},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "windows_ci_execution": False,
        "starts_server": False,
        "repository_files_modified": False,
        "cases": results,
        "passed": len(results) - failures,
        "failed": failures,
        "expected_exit_code": int(failures != 0),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(failures != 0)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
