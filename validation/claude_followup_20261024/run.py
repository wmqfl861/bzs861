"""Record staged audit commands and run the required unchanged full test gates."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv/bin/python"
NATIVE = [
    "bzs_tests", "bzs_batch_tests", "bzs_map_tests", "bzs_crawl_tests",
    "bzs_site_publication_tests", "bzs_contract_tests", "bzs_retention_tests",
    "bzs_fairness_tests", "bzs_session_tests", "bzs_revalidation_tests",
    "bzs_revocation_tests", "bzs_source_publication_tests", "bzs_cache_freshness_tests",
    "bzs_search_mode_tests", "bzs_capacity_cycles_tests",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def inputs() -> dict[str, str]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    extra = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    paths = {name.decode() for name in (tracked + extra).split(b"\0")
             if name and not name.startswith(b"validation/")}
    paths.add(str(Path(__file__).relative_to(ROOT)))
    return {name: sha(ROOT / name) for name in sorted(paths)}


def binaries() -> dict[str, str]:
    paths = []
    for folder in (ROOT / "build", ROOT / "build-asan", ROOT / "build-release"):
        paths.extend(path for path in folder.glob("bzs_*") if path.is_file())
        paths.extend(folder / name for name in ("searchd", "searchctl", "libbzs_core.a", "CTestTestfile.cmake")
                     if (folder / name).is_file())
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)}


def record(stage: str, name: str, argv: list[str], *, asan: bool = False,
           build: bool = False, timeout: int | None = None) -> dict:
    if any(not value or "/" in value or "\\" in value for value in (stage, name)):
        raise ValueError("Stage and command names must be simple labels")
    folder = OUT / stage
    folder.mkdir(exist_ok=True)
    manifest = folder / "execution-inputs.json"
    current = inputs()
    if not manifest.exists():
        save(manifest, {"source_base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "scope": "Actual non-validation tree bytes plus this recorder; dirty implementation/test changes are captured by their hashes.",
            "files": current})
    if json.loads(manifest.read_bytes())["files"] != current:
        raise RuntimeError("Inputs changed within a stage; choose a new stage label after reviewing the change")
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "python")}
    if asan:
        environment.update({"ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
                            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"})
    before = binaries()
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    log_path = folder / (name + ".log")
    timed_out = False
    with log_path.open("x", encoding="utf-8") as log:
        try:
            result = subprocess.run(argv, cwd=ROOT, env={**os.environ, **environment},
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
            log.write("\nAudit command process watchdog expired.\n")
    after = binaries()
    unchanged = inputs() == current
    value = {"argv": argv, "cwd": str(ROOT), "started_at_utc": started,
             "elapsed_seconds": round(time.monotonic() - tick, 3), "exit_code": code,
             "process_watchdog_expired": timed_out, "environment_overrides": environment,
             "execution_inputs": "execution-inputs.json", "execution_inputs_sha256": sha(manifest),
             "source_inputs_unchanged": unchanged, "build_preparation": build,
             "binaries_before_sha256": before, "binaries_after_sha256": after,
             "log": log_path.name, "log_sha256": sha(log_path)}
    save(folder / (name + "-command.json"), value)
    print(json.dumps({key: value[key] for key in (
        "argv", "exit_code", "elapsed_seconds", "source_inputs_unchanged", "log", "log_sha256")}), flush=True)
    print("\n".join(log_path.read_text(errors="replace").splitlines()[-16:]), flush=True)
    if code or not unchanged or (not build and before != after):
        raise SystemExit(code or 125)
    return value


def prepare_executables(folder: str) -> None:
    if folder not in ("build", "build-asan"):
        raise ValueError("Only known local build outputs may be prepared")
    targets = NATIVE + (["searchd", "searchctl"] if folder == "build" else [])
    repairs = []
    for name in targets:
        path = ROOT / folder / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Expected regular built executable: " + str(path))
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & stat.S_IXUSR:
            # Generated executables have occasionally lost only their mode in
            # this workspace. Verify and retain bytes; never repair source files.
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    raise RuntimeError("Expected compiled ELF executable")
            before = sha(path)
            path.chmod(mode | 0o111)
            if sha(path) != before:
                raise RuntimeError("Executable content changed during mode repair")
            repairs.append({"path": str(path.relative_to(ROOT)), "mode_before": oct(mode),
                            "mode_after": oct(stat.S_IMODE(path.stat().st_mode)), "sha256": before})
    print(json.dumps({"built_executables_checked": len(targets), "mode_only_repairs": repairs}), flush=True)


def full_stage(stage: str) -> None:
    record(stage, "configure-debug", ["cmake", "--preset", "dev", "-DBZS_PYTHON=" + str(PYTHON),
                                     "-DBZS_REQUIRE_PYTHON_TESTS=ON"], build=True)
    record(stage, "build-debug", ["cmake", "--build", "--preset", "dev", "-j2"], build=True)
    record(stage, "prepare-debug-executables", [str(PYTHON), str(Path(__file__)), "prepare-executables", "build"], build=True)
    record(stage, "ctest", ["ctest", "--preset", "dev", "--parallel", "2", "--verbose", "--output-junit",
                            str(OUT / stage / "ctest.xml")])
    record(stage, "pytest", [str(PYTHON), "-m", "pytest", "python/tests", "-q", "-p", "no:cacheprovider"])
    record(stage, "configure-asan", ["cmake", "--preset", "asan", "-DBZS_PYTHON=" + str(PYTHON),
                                    "-DBZS_REQUIRE_PYTHON_TESTS=ON"], build=True)
    record(stage, "build-asan", ["cmake", "--build", "--preset", "asan-core", "-j2"], build=True)
    record(stage, "prepare-asan-executables", [str(PYTHON), str(Path(__file__)), "prepare-executables", "build-asan"], build=True)
    record(stage, "asan", [str(PYTHON), str(Path(__file__)), "sanitizers"], asan=True)
    print(json.dumps({"stage": stage, "all_required_gates": "passed"}), flush=True)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "prepare-executables":
        prepare_executables(sys.argv[2])
        return
    if sys.argv[1:] == ["sanitizers"]:
        for target in NATIVE:
            argv = [str(ROOT / "build-asan" / target)]
            if target == "bzs_contract_tests":
                argv.append("docs/planning/validation/contract_cases.json")
            print("RUN_EXEC " + target, flush=True)
            subprocess.run(argv, cwd=ROOT, check=True)
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--asan", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.full:
        full_stage(args.stage)
        return
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not args.name or not argv:
        parser.error("Use STAGE --full, or STAGE NAME [options] -- COMMAND")
    record(args.stage, args.name, argv, asan=args.asan, build=args.build, timeout=args.timeout)


if __name__ == "__main__":
    main()
