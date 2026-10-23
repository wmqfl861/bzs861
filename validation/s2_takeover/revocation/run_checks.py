"""Repeat focused checks with immutable per-run logs, source and binary hashes."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--tests", nargs="+", choices=("native", "http", "e2e", "mcp"), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    out = Path(__file__).resolve().parent
    commands = {
        "native": [str(args.build / "bzs_revocation_tests")],
        "http": [args.python, "tests/http_revocation.py", "--server", str(args.build / "searchd"),
                 "--cli", str(args.build / "searchctl"), "--fixtures", str(root / "tests/fixtures")],
        "e2e": [args.python, "tests/e2e_revocation.py", "--server", str(args.build / "searchd"),
                "--python", args.python, "--repo", str(root)],
        "mcp": [args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "python/tests/test_principal_revocation_adapters.py"],
    }
    source_files = [p for folder in ("src", "include", "apps", "tests", "python")
                    for p in (root / folder).rglob("*") if p.is_file() and p.suffix in (".cpp", ".hpp", ".py", ".toml")]
    source_files += [root / "CMakeLists.txt", root / "CMakePresets.json"]
    before = {str(p.relative_to(root)): digest(p) for p in sorted(source_files)}
    manifest_path = out / (args.prefix + "-inputs.json")
    with manifest_path.open("x") as stream:
        json.dump(before, stream, indent=2)
        stream.write("\n")
    failures = 0
    for label in args.tests:
        log = out / f"{args.prefix}-{label}.log"
        metadata = out / f"{args.prefix}-{label}.json"
        if log.exists() or metadata.exists():
            raise ValueError("Refusing to overwrite prior validation evidence")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(root / "python"))
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tick = time.monotonic()
        binaries = {str(p): digest(p) for p in [args.build / "searchd", args.build / "searchctl",
                                                args.build / "bzs_revocation_tests"] if p.is_file()}
        with log.open("x") as stream:
            result = subprocess.run(commands[label], stdout=stream, stderr=subprocess.STDOUT, cwd=root, env=env,
                                    timeout=180, check=False)
        unchanged = before == {str(p.relative_to(root)): digest(p) for p in sorted(source_files)}
        record = {"argv": commands[label], "cwd": str(root), "started_at_utc": started,
                  "elapsed_seconds": round(time.monotonic() - tick, 3), "exit_code": result.returncode,
                  "log": log.name, "log_sha256": digest(log), "inputs": manifest_path.name,
                  "inputs_sha256": digest(manifest_path), "source_unchanged_during_command": unchanged,
                  "binaries_sha256": binaries}
        metadata.write_text(json.dumps(record, indent=2) + "\n")
        print(label, result.returncode, flush=True)
        print(log.read_text()[-9000:], flush=True)
        failures += result.returncode != 0 or not unchanged
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
