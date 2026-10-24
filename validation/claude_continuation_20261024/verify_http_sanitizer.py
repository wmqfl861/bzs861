"""Run formal HTTP cases and also verify sanitizer-clean server shutdowns."""
from pathlib import Path
import hashlib
import json
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import integration

original_stop = integration.RunningServer.stop
shutdowns = []


def checked_stop(server):
    original_stop(server)
    log = (server.root / "server.log").read_text(errors="replace")
    record = {"exit_code": server.process.returncode, "server_log": log,
              "server_log_sha256": hashlib.sha256(log.encode()).hexdigest()}
    shutdowns.append(record)
    assert server.process.returncode == 0, record
    for signal in ("AddressSanitizer", "LeakSanitizer", "UndefinedBehaviorSanitizer", "runtime error:"):
        assert signal not in log, record


integration.RunningServer.stop = checked_stop
try:
    runpy.run_path(str(Path(__file__).with_name("select_http_tests.py")), run_name="__main__")
except SystemExit as result:
    print(json.dumps({"harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "formal_tests_sha256": hashlib.sha256((ROOT / "tests/integration.py").read_bytes()).hexdigest(),
                      "shutdowns": shutdowns}, indent=2))
    if result.code:
        raise
    assert shutdowns, "No server shutdown was verified"
