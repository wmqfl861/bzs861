"""Capture commands, exact repository inputs, binaries and complete output bytes.

Every record is append-only: choose a new name after a failure or source change.
This runner is validation evidence, not a product test or a source of pass counts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def inputs() -> dict[str, str]:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    names += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    paths = {name.decode() for name in names.split(b"\0")
             if name and not name.startswith(b"validation/")}
    paths.add(str(Path(__file__).resolve().relative_to(ROOT)))
    return {name: digest(ROOT / name) for name in sorted(paths)}


def binaries() -> dict[str, str]:
    paths = set()
    for folder in (ROOT / "build", ROOT / "build-asan"):
        paths.update(path for path in folder.glob("bzs_*") if path.is_file())
        paths.update(folder / name for name in ("searchd", "searchctl", "libbzs_core.a", "CTestTestfile.cmake")
                     if (folder / name).is_file())
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("name")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--asan", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if any(not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value)
           for value in (args.stage, args.name)):
        parser.error("Use simple stage and record names")
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        parser.error("A command is required")
    folder = OUT / args.stage
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / (args.name + "-inputs.json")
    before = inputs()
    save(manifest, {"base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "scope": "All non-validation tracked/untracked repository inputs plus this self-contained recorder",
                    "files": before})
    overrides = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "python")}
    if args.asan:
        overrides.update(ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                         UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    binary_before = binaries()
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    log = folder / (args.name + ".log")
    timed_out = False
    with log.open("xb") as stream:
        try:
            code = subprocess.run(argv, cwd=ROOT, env={**os.environ, **overrides},
                                  stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            timed_out, code = True, 124
            stream.write(b"\nValidation process watchdog expired.\n")
    binary_after = binaries()
    after = inputs()
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    valid = code == 0 and not timed_out and not changed and (args.build or binary_before == binary_after)
    result = {"argv": argv, "cwd": str(ROOT), "started_at_utc": started,
              "duration_seconds": round(time.monotonic() - tick, 3), "exit_code": code,
              "watchdog_expired": timed_out, "environment_overrides": overrides,
              "inputs": manifest.name, "inputs_sha256": digest(manifest),
              "source_inputs_unchanged": not changed, "changed_inputs": changed,
              "build_preparation": args.build, "binaries_before": binary_before,
              "binaries_after": binary_after, "log": log.name, "log_sha256": digest(log),
              "execution_record_valid": valid}
    save(folder / (args.name + "-command.json"), result)
    print(json.dumps({key: result[key] for key in ("argv", "exit_code", "duration_seconds", "changed_inputs", "execution_record_valid")}))
    print("\n".join(log.read_text(errors="replace").splitlines()[-15:]))
    return 0 if valid else code or 125


if __name__ == "__main__":
    sys.exit(main())
