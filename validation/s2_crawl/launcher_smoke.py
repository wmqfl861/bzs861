"""Reproduce this increment's isolated Linux launcher lifecycle, without web requests.

Run with the installed worker Python, from the repository root:
  python validation/s2_crawl/launcher_smoke.py --server build-s1-review/searchd
The report contains no generated credentials, request headers or private state.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--output", default="validation/s2_crawl/launcher_smoke.json")
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("This recorded lifecycle harness requires Linux child-subreaper support.")
    repo = Path(__file__).resolve().parents[2]
    server = Path(args.server).resolve()
    output = Path(args.output).resolve()
    started = time.monotonic()
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "commands": [], "assertions": [],
              "scope": "Real isolated launcher lifecycle; loopback control/health only; no source, site or model requests.",
              "python_version": sys.version.split()[0], "searchd_binary": str(server.relative_to(repo))}
    owned = {}
    reaped = {}

    def check(name, condition, observed=None):
        item = {"name": name, "passed": bool(condition)}
        if observed is not None:
            item["observed"] = observed
        report["assertions"].append(item)
        if not condition:
            raise AssertionError(name)

    def free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    with tempfile.TemporaryDirectory(prefix="bzs-crawl-launcher-") as temporary:
        home = Path(temporary)
        env = dict(os.environ, BZS_HOME=str(home), PYTHONPATH=str(repo / "python"), PYTHONDONTWRITEBYTECODE="1")
        for name in ("SEARCH_API_TOKEN", "BZS_CORE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)

        def command(stage, *arguments):
            argv = [sys.executable, "-B", "-m", "bzs.cli", *arguments]
            before = time.monotonic()
            process = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True,
                                     encoding="utf-8", timeout=25)
            report["commands"].append({"stage": stage, "argv": argv, "exit_code": process.returncode,
                                       "elapsed_ms": round((time.monotonic() - before) * 1000)})
            if process.returncode:
                raise AssertionError(f"launcher command failed: {stage}, exit {process.returncode}")
            return process.stdout

        try:
            check("harness_adopts_only_its_orphaned_launcher_children", ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0)
            check("selected_binary_exists", server.is_file())
            command("init", "init")
            cfg_path = home / "config.json"
            cfg = json.loads(cfg_path.read_text())
            check("new_config_binds_its_own_empty_site_file", cfg["sites"] == str(home / "sites.json"))
            check("empty_site_manifest_bytes_preserved", (home / "sites.json").read_bytes() == (repo / "config/sites.json").read_bytes())
            sources = json.loads((home / "sources.json").read_text())["sources"]
            check("all_default_search_sources_remain_disabled", len(sources) == 17 and not any(s["enabled"] for s in sources))
            core_port, worker_port = free_port(), free_port()
            while worker_port == core_port:
                worker_port = free_port()
            cfg.update(searchd=str(server), core_port=core_port, worker_port=worker_port, threads=2)
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            command("start", "start")
            for name in ("searchd", "worker"):
                pid = int((home / "run" / f"{name}.pid").read_text())
                argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                check(f"{name}_uses_exact_site_file", b"--sites" in argv and str(home / "sites.json").encode() in argv)
                check(f"{name}_belongs_to_temporary_installation", str(home / "worker.token").encode() in argv)
                owned[name] = pid
            status = command("status", "status")
            check("status_reports_running_core", "searchd: up" in status)
            sites = json.loads(command("map_sites", "call", "map.sites"))
            digest = hashlib.sha256((home / "sites.json").read_bytes()).hexdigest()
            check("core_loaded_empty_exact_site_manifest", sites["sites"] == [] and sites["manifest_sha256"] == digest)
            caps = json.loads(command("capabilities", "call", "capabilities"))
            check("core_reports_version_and_crawl_api", caps["version"] == "0.1.4" and
                  {"map.sites", "map.submit", "crawl.submit", "crawl.edges"} <= set(caps["operations"]))
            actual_sources = json.loads(command("sources", "call", "web.sources"))["sources"]
            check("no_source_enabled_or_dispatched", len(actual_sources) == 17 and
                  not any(s["enabled"] or s.get("state") for s in actual_sources))
            request = urllib.request.Request(f"http://127.0.0.1:{worker_port}/rpc",
                data=b'{"method":"health","params":{}}', headers={"Content-Type": "application/json",
                "Authorization": "Bearer " + (home / "worker.token").read_text().strip()})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                health = json.load(response)["result"]
            check("worker_reports_same_site_manifest_and_version", health["sites_manifest_sha256"] == digest and health["version"] == "0.1.4")
            check("worker_reports_current_publication_policy", health["publication_policy"] == "bzs-publication-1")
            check("worker_has_no_adapters_or_proxy", health["adapters"] == [] and health["proxy"] is False)
            report["manifest_sha256"] = digest
            command("stop", "stop")
            for name, pid in owned.items():
                waited, status = os.waitpid(pid, 0)
                reaped[name] = {"pid": waited, "exit_code": os.waitstatus_to_exitcode(status)}
            check("both_owned_processes_exited_and_reaped", len(reaped) == 2)
            closed = []
            for port in (core_port, worker_port):
                with socket.socket() as sock:
                    sock.settimeout(1)
                    closed.append(sock.connect_ex(("127.0.0.1", port)) != 0)
            check("both_temporary_ports_are_closed", all(closed))
            report["result"] = "passed"
        except Exception as error:
            report["result"] = "failed"
            report["failure"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            # Target only children whose command line proves this temporary
            # installation identity, including a partially completed start.
            for name in ("searchd", "worker"):
                if name in reaped:
                    continue
                try:
                    pid = owned.get(name) or int((home / "run" / f"{name}.pid").read_text())
                    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
                    if str(home / "worker.token").encode() in raw:
                        os.kill(pid, signal.SIGTERM)
                        os.waitpid(pid, 0)
                except (FileNotFoundError, ProcessLookupError, ChildProcessError):
                    pass
            report["cleanup"] = {"only_owned_processes_targeted": True, "reaped": reaped}
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "commands": len(report["commands"]),
                      "assertions": len(report["assertions"]), "report": str(output.relative_to(repo))}))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
