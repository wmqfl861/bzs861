"""Run the unchanged HTTP probe and retain each searchd shutdown observation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    namespace = runpy.run_path(str(Path(__file__).with_name("http_probe.py")), run_name="independent_http_probe")
    core = namespace["Core"]
    original_close = core.close
    shutdowns = []

    def observed_close(instance):
        original_close(instance)
        log = output / f"asan-server-{len(shutdowns) + 1}.log"
        log.write_bytes((instance.root / "stderr.log").read_bytes())
        shutdowns.append({"pid": instance.process.pid, "exit_code": instance.process.returncode,
                          "stderr": log.name, "stderr_sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
        if instance.process.returncode != 0:
            raise AssertionError(f"searchd shutdown returned {instance.process.returncode}; see {log}")

    core.close = observed_close
    sys.argv = [str(Path(__file__).with_name("http_probe.py")), "--server", args.server, "--fixtures", args.fixtures]
    try:
        namespace["main"]()
    finally:
        record = {"note": "Additional observation around Core.close; original probe cases and assertions are unchanged.",
                  "shutdowns": shutdowns, "all_exit_zero": len(shutdowns) == 4 and all(s["exit_code"] == 0 for s in shutdowns)}
        (output / "asan-shutdowns.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
