"""Temporary harness checks; never writes the project or runs product tests."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
PROJECT = Path("/workspace/scratch/e2371129386d/bzs861")
ROOT = BASE / "b"
SCRIPTS = ROOT / "validation/claude_followup_20261024"
sys.path.insert(0, str(SCRIPTS))
import run


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return {"argv": argv, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main():
    record = {"scope": "Temporary copied CTest grammar and small system-ELF fixtures only; no product tests or repository mutation", "observations": []}
    target_dir = ROOT / "build"
    target_dir.mkdir()
    (ROOT / "python").mkdir()
    (ROOT / ".venv/bin").mkdir(parents=True)
    (ROOT / ".venv/bin/python").symlink_to(sys.executable)
    for target in run.NATIVE:
        shutil.copyfile("/usr/bin/printf" if target == "bzs_tests" else "/usr/bin/false", target_dir / target)
        (target_dir / target).chmod(0o755)
    original_path = PROJECT / "build/CTestTestfile.cmake"
    original_project = original_path.read_bytes()
    original = original_project.decode().replace(str(PROJECT), str(ROOT)).encode()
    generated = target_dir / "CTestTestfile.cmake"
    generated.write_bytes(original)
    before = command(["ctest", "--test-dir", str(target_dir), "--show-only=json-v1"])
    assert before["exit_code"] == 0, before
    run.guard_ctest("fixture-check", "build")
    after = command(["ctest", "--test-dir", str(target_dir), "--show-only=json-v1"])
    assert after["exit_code"] == 0, after
    old, new = json.loads(before["stdout"])["tests"], json.loads(after["stdout"])["tests"]
    assert len(old) == len(new) == 27
    observed_native = []
    for left, right in zip(old, new):
        assert left["name"] == right["name"]
        assert left.get("properties") == right.get("properties")
        old_command, new_command = left.get("command"), right.get("command")
        if old_command and Path(old_command[0]).name in run.NATIVE:
            assert new_command[3:] == old_command
            assert new_command[:2] == [str(run.PYTHON), str(run.GUARD)]
            assert new_command[2] == sha(Path(old_command[0]))
            observed_native.append(left["name"])
        else:
            assert new_command == old_command
    assert len(observed_native) == 15
    record["ctest_parser_equivalence"] = {"test_count": 27, "guarded_native_count": 15,
        "all_original_argv_after_stripping_prefix": True, "all_properties_equal": True,
        "test_order_and_names_equal": True, "native_tests": observed_native}
    record["source_ctest_sha256"] = hashlib.sha256(original_project).hexdigest()
    mapping_path = SCRIPTS / "fixture-check/build-native-launch-map.json"
    mapping = json.loads(mapping_path.read_text())
    restored = generated.read_text()
    for row in mapping["commands"]:
        restored = restored.replace(row["guarded_line"], row["original_line"], 1)
    assert restored.encode() == original
    record["byte_inverse_exact"] = True
    print_target = target_dir / "bzs_tests"
    print_target.chmod(0o644)
    printed = command([sys.executable, str(run.GUARD), sha(print_target), str(print_target), "%s|%s", "first arg", "second"])
    assert printed["exit_code"] == 0 and printed["stdout"] == "first arg|second", printed
    assert '"mode_before": "0o644"' in printed["stderr"]
    assert printed["stderr"].count("GUARD_EXEC ") == 1
    record["observations"].append(printed)
    failed_target = target_dir / "bzs_batch_tests"
    failed_target.chmod(0o644)
    failed = command([sys.executable, str(run.GUARD), sha(failed_target), str(failed_target)])
    assert failed["exit_code"] == 1 and failed["stderr"].count("GUARD_EXEC ") == 1, failed
    record["observations"].append(failed)
    failed_target.chmod(0o644)
    mismatch = command([sys.executable, str(run.GUARD), "0" * 64, str(failed_target)])
    assert mismatch["exit_code"] == 125 and not failed_target.stat().st_mode & 0o111, mismatch
    assert "GUARD_EXEC " not in mismatch["stderr"]
    record["observations"].append(mismatch)
    text_target = target_dir / "bzs_map_tests"
    text_target.write_text("not an ELF\n")
    denied_text = command([sys.executable, str(run.GUARD), sha(text_target), str(text_target)])
    assert denied_text["exit_code"] == 125 and "GUARD_EXEC " not in denied_text["stderr"]
    record["observations"].append(denied_text)
    text_target.unlink()
    text_target.symlink_to(print_target)
    denied_link = command([sys.executable, str(run.GUARD), sha(print_target), str(text_target)])
    assert denied_link["exit_code"] == 125 and "GUARD_EXEC " not in denied_link["stderr"]
    record["observations"].append(denied_link)
    assert original_path.read_bytes() == original_project
    assert (PROJECT / "validation/claude_followup_20261024/run.py").read_bytes() == (BASE / "a/validation/claude_followup_20261024/run.py").read_bytes()
    record["main_config_and_runner_unchanged"] = True
    record["status"] = "passed"
    (BASE / "draft-verification.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "passed", "ctest_tests": 27, "guarded_native": 15,
        "original_argv_properties_order_preserved": True, "mode_only_repair": True,
        "original_nonzero_exit_preserved": True, "bad_hash_non_ELF_symlink_refused": True,
        "project_tests_run": 0}))


if __name__ == "__main__":
    main()
