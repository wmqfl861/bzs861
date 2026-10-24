"""Verify recorded full-stage evidence and write a compact, immutable index."""
from pathlib import Path
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
stage = OUT / sys.argv[1]
assert stage.parent == OUT and stage.is_dir()
asan_stage = OUT / sys.argv[2] if len(sys.argv) == 3 else stage
assert asan_stage.parent == OUT and asan_stage.is_dir()
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = stage / "execution-inputs.json"
commands = []
paths = sorted(stage.glob("*-command.json"))
if asan_stage != stage:
    paths = [path for path in paths if path.name != "asan-command.json"]
    paths += sorted(asan_stage.glob("*-command.json"))
    product = lambda folder: {key: value for key, value in json.loads((folder / "execution-inputs.json").read_bytes())["files"].items()
                              if not key.startswith("validation/")}
    assert product(stage) == product(asan_stage), "Repeated sanitizer run must use identical product inputs"
    original = json.loads((stage / "build-asan-command.json").read_bytes())["binaries_after_sha256"]
    repeated = json.loads((asan_stage / "asan-command.json").read_bytes())["binaries_before_sha256"]
    for key, value in original.items():
        if key.startswith("build-asan/bzs_") or key == "build-asan/libbzs_core.a":
            assert repeated[key] == value, key
for path in paths:
    value = json.loads(path.read_bytes())
    assert value["exit_code"] == 0 and value["source_inputs_unchanged"], path
    assert value["execution_inputs_sha256"] == digest(path.parent / "execution-inputs.json"), path
    assert value["log_sha256"] == digest(path.parent / value["log"]), path
    if not value["build_preparation"]:
        assert value["binaries_before_sha256"] == value["binaries_after_sha256"], path
    commands.append({"record": str(path.relative_to(OUT)), "sha256": digest(path), "log": str((path.parent / value["log"]).relative_to(OUT)),
                     "log_sha256": value["log_sha256"], "elapsed_seconds": value["elapsed_seconds"]})
names = {Path(item["record"]).name for item in commands}
assert {"ctest-command.json", "pytest-command.json", "asan-command.json"} <= names
xml = ET.parse(stage / "ctest.xml").getroot()
ctest = {key: int(xml.attrib[key]) for key in ("tests", "failures", "disabled", "skipped")}
assert ctest == {"tests": 27, "failures": 0, "disabled": 0, "skipped": 0}
assert len(xml.findall("testcase")) == 27
pytest_log = (stage / "pytest.log").read_text()
pytest_count = int(re.findall(r"(\d+) passed", pytest_log)[-1])
asan_log = (asan_stage / "asan.log").read_text()
targets = re.findall(r"^RUN_EXEC (bzs_\w+)$", asan_log, flags=re.MULTILINE)
assert len(targets) == len(set(targets)) == 15
summaries = []
for section in asan_log.split("RUN_EXEC ")[1:]:
    lines = section.strip().splitlines()
    name, summary = lines[0], lines[-1]
    if name == "bzs_tests":
        data = json.loads(summary)
        assert data["passed"] == data["tests"]
        cases, checks = data["tests"], data["assertions"]
    elif name == "bzs_contract_tests":
        match = re.fullmatch(r"Contract tests: (\d+) passed, 0 failed; .* (\d+) assertions", summary)
        assert match, summary
        cases, checks = map(int, match.groups())
    elif name == "bzs_revocation_tests":
        match = re.fullmatch(r"(\d+)/(\d+) tests, (\d+) assertions", summary)
        assert match and match[1] == match[2], summary
        cases, checks = int(match[1]), int(match[3])
    elif name == "bzs_cache_freshness_tests":
        match = re.fullmatch(r"(\d+) cases, (\d+) assertions, 0 failed", summary)
        assert match, summary
        cases, checks = map(int, match.groups())
    else:
        match = re.fullmatch(r"(\d+) (?:[a-z ]+ )?tests(?:,| /) (\d+) assertions(?:,| /) 0 failures(?:, \d+ wall ms)?", summary)
        assert match, summary
        cases, checks = map(int, match.groups())
    assert len(re.findall(r"^PASS ", section, flags=re.MULTILINE)) == cases, name
    summaries.append({"target": name, "cases": cases, "assertions": checks, "failures": 0})
assert len(summaries) == 15
result = {"stage": stage.name, "execution_inputs_sha256": digest(manifest), "commands": commands,
          "sanitizer_stage": asan_stage.name, "sanitizer_inputs_sha256": digest(asan_stage / "execution-inputs.json"),
          "ctest": ctest, "pytest_passed": pytest_count, "sanitizers": {"native_programs": targets,
          "cases": sum(item["cases"] for item in summaries), "program_results": summaries,
          "assertions": sum(item["assertions"] for item in summaries), "failures": 0},
          "all_required_gates": "passed", "collector_sha256": digest(Path(__file__))}
if asan_stage != stage:
    result["excluded_incomplete_sanitizer_record"] = str((stage / "asan-command.json").relative_to(OUT))
    result["repeated_sanitizer_binding"] = "All product inputs and all 15 native binaries plus their core library match the original build; only the output recorder and an additional ASan searchd build differ."
with (stage / "result.json").open("x") as stream:
    json.dump(result, stream, indent=2)
    stream.write("\n")
print(json.dumps(result, indent=2))
