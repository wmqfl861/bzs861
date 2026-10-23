#!/usr/bin/env python3
"""Recreate genuine schema12 input, then upgrade a copy with a frozen schema13 library.

Build both libraries first. This script snapshots their bytes and headers, records
the source inputs, compiles the same probe twice, and preserves all command logs.
The original database is never passed to the new library. No network is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

BASELINE = "3ef86c653a2e2085848d5cdb151d66392c6af49a"
HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def source_input(root, library):
    files = [root / "CMakeLists.txt"]
    for directory in ("src", "include"):
        files.extend(p for p in (root / directory).rglob("*") if p.is_file())
    return {
        "root": str(root),
        "head": git(root, "rev-parse", "HEAD"),
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "tracked_status": git(root, "status", "--porcelain", "--untracked-files=no"),
        "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(
            ["git", "diff", "HEAD", "--"], cwd=root)).hexdigest(),
        "source_sha256": {str(p.relative_to(root)): digest(p) for p in sorted(files)},
        "library": str(library),
        "library_sha256": digest(library),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--old-library", type=Path)
    parser.add_argument("--new-library", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path, required=True)
    args = parser.parse_args()
    old_root, new_root = args.old_root.resolve(), args.new_root.resolve()
    old_lib = (args.old_library or old_root / "build-migration-old/libbzs_core.a").resolve()
    new_lib = (args.new_library or new_root / "build/libbzs_core.a").resolve()
    work, records = args.work_dir.resolve(), args.records_dir.resolve()
    if work.exists() or records.exists():
        raise RuntimeError("Use new work and records directories; previous evidence is never overwritten")
    old_before, new_before = source_input(old_root, old_lib), source_input(new_root, new_lib)
    if old_before["head"] != BASELINE or old_before["tracked_status"]:
        raise RuntimeError("Old input must be the clean, exact schema12 baseline commit")
    work.mkdir(parents=True)
    records.mkdir(parents=True)
    report = {"role": "integration_migration_verifier_shared_component_author",
              "independent_review_scope": ["source publication / principal revocation interaction"],
              "authored_component_not_independently_reviewed": "shared read_many",
              "old_source": old_before, "new_source": new_before,
              "driver_sha256": digest(HERE / "migration_driver.cpp"),
              "external_network_requests": 0}

    def save(name, value):
        (records / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    save("input-provenance.json", report)

    def run(name, cwd, command, inputs):
        recorder = [sys.executable, str(HERE / "record_run.py"), "--cwd", str(cwd),
                    "--records-dir", str(records)]
        for item in inputs:
            recorder.extend(("--hash", str(item)))
        subprocess.run([*recorder, name, "--", *map(str, command)], check=True)

    executables = {}
    for label, root, library, expected, macro in (
        ("old", old_root, old_lib, old_before, "0"),
        ("new", new_root, new_lib, new_before, "1"),
    ):
        frozen = work / label
        frozen.mkdir()
        shutil.copytree(root / "include", frozen / "include")
        shutil.copyfile(library, frozen / "libbzs_core.a")
        if digest(frozen / "libbzs_core.a") != expected["library_sha256"]:
            raise RuntimeError(f"{label} library changed while being captured")
        driver = HERE / "migration_driver.cpp"
        executable = frozen / "migration-driver"
        run("compile-" + label, root,
            ["/usr/bin/c++", "-std=c++20", "-O0", "-g", "-Wall", "-Wextra",
             "-Wpedantic", "-Wconversion", "-Wshadow", "-Werror",
             "-DBZS_MIGRATION_NEW=" + macro, "-I" + str(frozen / "include"),
             driver, frozen / "libbzs_core.a", "-lsqlite3", "-lxml2", "-ljson-c",
             "-lcrypto", "-pthread", "-o", executable],
            [driver, frozen / "libbzs_core.a"])
        executable.chmod(executable.stat().st_mode | 0o100)
        executables[label] = executable
        report[label + "_executable_sha256"] = digest(executable)
        report[label + "_frozen_headers_sha256"] = {
            str(p.relative_to(frozen)): digest(p)
            for p in sorted((frozen / "include").rglob("*")) if p.is_file()}

    old_input, upgrade = work / "schema12-input", work / "schema13-upgrade"
    run("create-old", old_root, [executables["old"], old_input], [executables["old"]])
    report["old_database_sha256"] = digest(old_input / "store.db")
    report["old_backup_sha256"] = digest(old_input / "quarantined-old-backup.db")
    shutil.copytree(old_input, upgrade)
    run("upgrade-verify", new_root, [executables["new"], upgrade],
        [executables["new"], upgrade / "store.db", upgrade / "old_creation.json"])
    if digest(old_input / "store.db") != report["old_database_sha256"]:
        raise RuntimeError("The frozen original schema12 database changed")
    old_after, new_after = source_input(old_root, old_lib), source_input(new_root, new_lib)
    if old_after != old_before or new_after != new_before:
        raise RuntimeError("Source or library inputs changed during verification; replay after a code freeze")
    report["old_input_unchanged"] = True
    report["source_and_library_inputs_unchanged"] = True
    report["upgraded_database_sha256"] = digest(upgrade / "store.db")
    report["new_backup_sha256"] = digest(upgrade / "quarantined-new-backup.db")
    for source in (old_input / "old_creation.json", upgrade / "new_verification.json",
                   upgrade / "after_migration_legacy_footprints.json"):
        shutil.copyfile(source, records / source.name)
    report["status"] = "passed"
    save("report.json", report)
    print("Migration verification passed; text evidence: " + str(records))


if __name__ == "__main__":
    main()
