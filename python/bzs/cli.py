"""bzs: launcher and helper CLI (cross-platform; process management lives here, not in C++)."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, config, process_control
from .core_client import CoreClient, CoreError
from .urls import to_uri


def _pid_file(name: str) -> Path:
    return process_control.record_path(name)


def _alive(pid: int) -> bool:
    return process_control.valid_pid(pid) and process_control.birth_identity(pid) is not None


def _is_ours(name: str, pid: int) -> bool:
    return process_control.valid_pid(pid) and process_control.owned_pid(name) == pid


def _read_pid(name: str) -> int | None:
    return process_control.owned_pid(name)


def _spawn(name: str, cmd: list[str], *, timeout: float = 30.0) -> subprocess.Popen:
    proc = process_control.spawn(name, cmd, timeout=timeout)
    ready = process_control.read_record(name)
    print(f"{name} ready on 127.0.0.1:{ready['port']} (pid {proc.pid})")
    return proc


def _client(cfg: config.Config) -> CoreClient:
    return CoreClient(cfg.core_url, config.api_token())


def cmd_init(args) -> int:
    path = config.init(force=args.force)
    cfg = config.load()
    print(f"config: {path}")
    print(f"searchd binary: {cfg.searchd} ({'found' if Path(cfg.searchd).exists() else 'NOT FOUND - build it first'})")
    print(f"sources manifest: {cfg.sources} (edit to enable/disable sources)")
    print(f"site discovery policy: {cfg.sites} (empty by default; requires a reviewed site)")
    return 0


def start(cfg: config.Config) -> list[tuple[str, subprocess.Popen]]:
    h = config.home()
    if (type(cfg.startup_timeout_s) not in (int, float) or not math.isfinite(cfg.startup_timeout_s)
            or not 1 <= cfg.startup_timeout_s <= 120):
        raise RuntimeError("startup_timeout_s must be a finite number in 1..120")
    if cfg.proxy or cfg.proxy_remote_dns or cfg.allow_cidrs:
        raise SystemExit("E_CONFIG: production proxies, proxy_remote_dns and allow_cidrs are disabled")
    for argument in cfg.worker_args:
        option = argument.split("=", 1)[0]
        if option.startswith("--") and len(option) > 2 and "--sites".startswith(option):
            # argparse accepts unambiguous long-option prefixes as well as '='.
            # The launcher must select this policy once for both processes.
            raise SystemExit("E_CONFIG: set sites in config.json; worker_args cannot override the shared site policy")
    if not Path(cfg.searchd).exists():
        raise SystemExit(f"searchd not found at {cfg.searchd}; build with `cmake --preset release && cmake --build --preset release`")
    site_args = ["--sites", cfg.sites] if cfg.sites else []
    commands = [
        ("worker", cfg.worker_port, [sys.executable, "-m", "bzs.worker.server", "--port", str(cfg.worker_port),
         "--token-file", str(h / "worker.token"), "--sources", cfg.sources, *site_args, *cfg.worker_args]),
        ("searchd", cfg.core_port, [cfg.searchd, "--auth", str(h / "auth.json"), "--state", cfg.state_dir,
         "--fixtures", cfg.fixtures, "--port", str(cfg.core_port), "--threads", str(cfg.threads),
         "--worker", f"http://127.0.0.1:{cfg.worker_port}", "--worker-token-file", str(h / "worker.token"),
         "--sources", cfg.sources, *site_args]),
    ]
    created = []
    with process_control.control_lock():
        try:
            for name, port, command in commands:
                if type(port) is not int or not 1 <= port <= 65535:
                    raise RuntimeError("configured ports must be integers in 1..65535")
                if _read_pid(name) is None:
                    proc = _spawn(name, command, timeout=cfg.startup_timeout_s)
                    if proc is not None:
                        created.append((name, proc))
                else:
                    record = process_control.read_record(name)
                    if record["state"] != "ready" or record["port"] != port:
                        raise RuntimeError(f"{name}: existing process is not ready on the configured port")
                    print(f"{name} already running")
        except BaseException:
            for name, proc in reversed(created):
                process_control.cleanup_child(name, proc)
            raise
    return created


def stop() -> None:
    with process_control.control_lock():
        for name in ("searchd", "worker"):
            if process_control.stop_owned(name):
                print(f"stopped {name}")


def cmd_start(args) -> int:
    ensure_running(config.load())
    return 0


def cmd_stop(args) -> int:
    stop()
    return 0


def cmd_status(args) -> int:
    cfg = config.load()
    client = _client(cfg)
    print(f"searchd: {'up' if client.healthy() else 'down'} ({cfg.core_url}, pid {_read_pid('searchd')})")
    print(f"worker:  pid {_read_pid('worker')}")
    if client.healthy():
        try:
            for s in client.call_sync("web.sources")["sources"]:
                if s["enabled"]:
                    st = s.get("state") or {}
                    cool = st.get("cooldown_remaining_ms", 0) // 1000
                    print(f"  {s['source_id']:14s} {s['capability']:12s} {'default ' if s['default'] else '        '}"
                          f"{s['verification']:10s} last={st.get('last_status', '-'):12s} cooldown={cool}s today={st.get('requests_today', 0)}")
        except CoreError as exc:
            print(f"  sources unavailable: {exc}")
    return 0


def ensure_running(cfg: config.Config) -> None:
    if _read_pid("searchd") is not None and _read_pid("worker") is not None:
        core_record = process_control.read_record("searchd")
        worker_record = process_control.read_record("worker")
        if (core_record["state"] == worker_record["state"] == "ready"
                and core_record["port"] == cfg.core_port and worker_record["port"] == cfg.worker_port
                and _client(cfg).healthy()):
            return
    created = start(cfg)
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _client(cfg).healthy():
                return
            time.sleep(0.1)
        raise RuntimeError("searchd health check failed")
    except BaseException:
        with process_control.control_lock():
            for name, proc in reversed(created):
                process_control.cleanup_child(name, proc)
        raise


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main
    if args.admin_tools and args.transport != "stdio":
        return mcp_main(["--transport", args.transport, "--admin-tools"])
    if not args.no_autostart and not os.environ.get("BZS_CORE_URL"):
        try:
            # stdout belongs to the MCP protocol: route startup messages to stderr.
            saved, sys.stdout = sys.stdout, sys.stderr
            try:
                ensure_running(config.load())
            finally:
                sys.stdout = saved
        except (OSError, ValueError, RuntimeError, SystemExit) as exc:
            print(f"bzs: autostart failed: {exc}", file=sys.stderr)
            return 2
    extra = ["--insecure-no-auth"] if args.insecure_no_auth else []
    if args.admin_tools:
        extra.append("--admin-tools")
    return mcp_main(["--transport", args.transport, "--port", str(args.port), *extra])


def cmd_mcp_config(args) -> int:
    exe = Path(sys.executable)
    snippet = {"mcpServers": {"bzs861": {"command": str(exe), "args": ["-m", "bzs.cli", "mcp"],
                                         "env": {"BZS_HOME": str(config.home())}}}}
    print(json.dumps(snippet, indent=2))
    env = f"--env BZS_HOME={config.home()} " if os.environ.get("BZS_HOME") else ""
    print("\nClaude Code (stdio, all projects):\n  claude mcp add --scope user " + env +
          f"--transport stdio bzs861 -- {exe} -m bzs.cli mcp", file=sys.stderr)
    print("Claude Code (HTTP, after `bzs mcp-token add NAME` and `bzs mcp --transport streamable-http`):\n"
          "  claude mcp add --scope user --transport http bzs861 http://127.0.0.1:8612/mcp "
          "--header \"Authorization: Bearer <token>\"", file=sys.stderr)
    return 0


def cmd_mcp_token(args) -> int:
    from .mcp_auth import add_client, clients_path, load_clients, revoke_client
    if args.action == "add":
        try:
            token = add_client(args.name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(token)
        print(f"\nStored only its SHA-256 in {clients_path()}. This token is shown once; give it to the client as\n"
              f"  Authorization: Bearer <token>\nfor `bzs mcp --transport streamable-http` (http://127.0.0.1:8612/mcp).",
              file=sys.stderr)
        return 0
    if args.action == "revoke":
        ok = revoke_client(args.name)
        print("revoked" if ok else "no such client")
        return 0 if ok else 1
    for client in load_clients():
        print(f"{client['name']:24s} created {client.get('created_at', '?')}  scopes={','.join(client.get('scopes', []))}")
    return 0


def cmd_call(args) -> int:
    cfg = config.load()
    params = json.loads(args.params) if args.params else {}
    try:
        print(json.dumps(_client(cfg).call_sync(args.operation, params), ensure_ascii=False, indent=2))
        return 0
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_storage(args) -> int:
    cfg = config.load()
    native = Path(cfg.searchd).with_name("searchctl.exe" if os.name == "nt" else "searchctl")
    command = [str(native), "storage-" + args.storage_action, args.database]
    if args.storage_action == "convert":
        if not args.confirm_offline:
            raise SystemExit("E_ARGUMENT: stop searchd and supply --confirm-offline")
        command += ["--confirm-offline", "--timeout-ms", str(args.timeout_ms)]
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"E_MAINTENANCE: cannot start native helper: {exc}", file=sys.stderr)
        return 2


def cmd_jobs_compact(args) -> int:
    cfg = config.load()
    try:
        result = _client(cfg).call_sync("jobs.compact", {"limit": args.limit})
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke_principal(args) -> int:
    cfg = config.load()
    try:
        result = _client(cfg).call_sync("principal.revoke", {"subject": args.subject})
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_search_modes(args) -> int:
    cfg = config.load()
    try:
        result = _client(cfg).call_sync("search.modes", {})
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args) -> int:
    cfg = config.load()
    try:
        params = {"query": " ".join(args.query), "category": args.category}
        for key in ("limit", "mode", "max_sources", "per_source", "timeout_ms", "deadline_ms", "sources"):
            value = getattr(args, key, None)
            if value is not None:
                params[key] = value
        if args.refresh:
            params["refresh"] = True
        r = _client(cfg).call_sync("web.search", params)
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for x in r["results"]:
        found = ", ".join(f"{s['source_id']}#{s['rank']}" for s in x["sources"])
        print(f"{x['rank']:>2}. {x['title']}\n    {x['url']}\n    {x['snippet'][:160]}\n    [{found}]")
    print("sources: " + "; ".join(f"{s['source_id']}={s['status']}" for s in r["sources"]))
    if "effective_mode" in r:
        print("mode: " + r["effective_mode"] + "; budget: " + json.dumps(r.get("effective_budget", {}), ensure_ascii=False, sort_keys=True))
        if r.get("omitted_sources"):
            print("omitted by source budget: " + ", ".join(r["omitted_sources"]))
    if not r["results"]:
        failed = [s for s in r["sources"] if s["status"] not in ("ok", "empty")]
        if failed and len(failed) == len(r["sources"]):
            errors = {s.get("error", s.get("reason", "")) for s in failed}
            print("\nNo source answered. Check this machine's direct network access and source approvals, then "
                  "run `bzs probe`. Errors: " + "; ".join(sorted(e for e in errors if e))[:400], file=sys.stderr)
            return 2
    return 0


def cmd_read(args) -> int:
    cfg = config.load()
    try:
        r = _client(cfg).call_sync("web.read", {"url": to_uri(args.url), "max_bytes": args.max_bytes,
                                                "freshness": args.freshness})
    except CoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    c = r["content"]
    print(f"# {r['title']}\n{r['final_url']}  (snapshot {r['snapshot_id']}, {c['total_bytes']} bytes, cache={r['from_cache']})\n")
    print(c["text"])
    if c.get("next_start_byte"):
        print(f"\n… more from byte {c['next_start_byte']}")
    return 0


def cmd_probe(args) -> int:
    from .probe import probe
    cfg = config.load() if not args.sources else None
    sources = args.sources or cfg.sources
    queries = args.queries or str(config.REPO_ROOT / "tests" / "golden" / "queries.json")
    proxy = args.proxy if args.proxy is not None else (cfg.proxy if cfg else "")
    try:
        report = asyncio.run(probe(sources, args.out, queries, only=args.only or None, per_source=args.per_source,
                                   delay_s=args.delay, timeout_s=args.timeout, proxy=proxy or None,
                                   proxy_remote_dns=bool(cfg and cfg.proxy_remote_dns),
                                   allow_private=list(cfg.allow_cidrs) if cfg else None,
                                   include_candidates=args.include_candidates, resume=args.resume))
    except (ValueError, OSError) as exc:
        print(f"E_PROBE: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Probe interrupted. Its checkpoint keeps reserved attempts charged; use --resume with the same inputs.",
              file=sys.stderr)
        return 130
    print((Path(args.out) / "probe_report.md").read_text(encoding="utf-8"))
    return 0 if any(row["status"] in ("ok", "empty") for row in report["rows"]) else 1


def cmd_replay(args) -> int:
    from .replay import replay
    try:
        report = replay(args.report, args.out, allow_parser_change=args.allow_parser_change)
    except (ValueError, OSError) as exc:
        print(f"E_REPLAY: {exc}", file=sys.stderr)
        return 2
    print((Path(args.out) / "replay_report.md").read_text(encoding="utf-8"))
    return 0 if report["status"] == "passed" else 1


def cmd_evaluation(args) -> int:
    from . import evaluation
    try:
        if args.evaluation_action == "prepare":
            result = evaluation.prepare(args.queries or str(config.REPO_ROOT / "tests" / "golden" / "queries.json"),
                                        args.out, candidates_path=args.candidates, probe_path=args.probe_report,
                                        splits=args.splits, ablation_manifest_path=args.ablation_manifest)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0  # A prepared, unjudged template is not a passed quality gate.
        if args.evaluation_action == "freeze":
            result = evaluation.freeze(args.package, args.gate, args.out, queries_path=args.queries)
            print(json.dumps({"status": result["status"], "blocked_reasons": result["blocked_reasons"],
                              "artifact": str(Path(args.out) / "freeze.json")}, ensure_ascii=False, sort_keys=True))
            return 0 if result["status"] == "frozen" else 3
        result = evaluation.run(args.package, args.freeze, args.labels, args.out, baseline_path=args.baseline,
                                queries_path=args.queries)
        print(json.dumps({"status": result["status"], "scope": result["scope"],
                          "blocked_reasons": result["quality_gate"]["blocked_reasons"],
                          "release_gate": result["release_gate"],
                          "artifact": str(Path(args.out) / "evaluation_report.json")}, ensure_ascii=False, sort_keys=True))
        return {"passed": 0, "failed": 1, "blocked": 3}[result["status"]]
    except (ValueError, OSError) as exc:
        print(f"E_EVALUATION: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bzs", description=f"bzs861 {__version__} launcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create ~/.bzs861 with tokens and config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    sub.add_parser("start", help="start worker and searchd").set_defaults(fn=cmd_start)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("mcp", help="run the MCP server (stdio by default)")
    p.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    p.add_argument("--port", type=int, default=8612)
    p.add_argument("--no-autostart", action="store_true")
    p.add_argument("--admin-tools", action="store_true", help="explicit principal management tools, stdio only")
    p.add_argument("--insecure-no-auth", action="store_true", help="removed: HTTP transport always requires client tokens")
    p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("mcp-token", help="manage per-client tokens for the MCP HTTP transport")
    p.add_argument("action", choices=("add", "list", "revoke"))
    p.add_argument("name", nargs="?", default="")
    p.set_defaults(fn=cmd_mcp_token)
    sub.add_parser("mcp-config", help="print MCP client configuration").set_defaults(fn=cmd_mcp_config)
    p = sub.add_parser("call", help="raw core operation")
    p.add_argument("operation")
    p.add_argument("params", nargs="?")
    p.set_defaults(fn=cmd_call)
    p = sub.add_parser("revoke-principal", help="permanently revoke another core principal (administrator only)")
    p.add_argument("subject")
    p.set_defaults(fn=cmd_revoke_principal)
    p = sub.add_parser("jobs", help="manage owned job history")
    jobs = p.add_subparsers(dest="jobs_action", required=True)
    compact = jobs.add_parser("compact", help="delete eligible settled terminal details after the core's minimum 7-day retention",
                             description="Delete eligible batch-read, map and crawl detail history for the authenticated owner. "
                             "The core enforces a minimum 7-day retention and keeps job identities, summaries and usage counters. "
                             "Active and paused jobs remain. Repeating this command may compact the next eligible batch.")
    compact.add_argument("--limit", type=int, default=16, help="maximum jobs in this batch, 1..64 (default: 16; validated by the core)")
    compact.set_defaults(fn=cmd_jobs_compact)
    p = sub.add_parser("storage", help="explicit offline native storage maintenance; never autostarts")
    actions = p.add_subparsers(dest="storage_action", required=True)
    for action in ("inspect", "convert"):
        sp = actions.add_parser(action)
        sp.add_argument("database", help="existing metadata.sqlite file")
        if action == "convert":
            sp.add_argument("--confirm-offline", action="store_true")
            sp.add_argument("--timeout-ms", type=int, default=120000)
        sp.set_defaults(fn=cmd_storage)
    sub.add_parser("search-modes", help="inspect source-only fast/balanced/deep search budgets").set_defaults(fn=cmd_search_modes)
    p = sub.add_parser("search", help="web search from the terminal")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int)
    p.add_argument("--category", default="web")
    p.add_argument("--mode", choices=("fast", "balanced", "deep"))
    p.add_argument("--max-sources", type=int)
    p.add_argument("--per-source", type=int)
    p.add_argument("--timeout-ms", type=int)
    p.add_argument("--deadline-ms", type=int)
    p.add_argument("--source", action="append", dest="sources", help="explicit source ID; repeat to select several")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("read", help="read a URL from the terminal")
    p.add_argument("url")
    p.add_argument("--max-bytes", type=int, default=20000)
    p.add_argument("--freshness", default="cache_first")
    p.set_defaults(fn=cmd_read)
    p = sub.add_parser("probe", help="S0: measure each source from this machine")
    p.add_argument("--out", default="probe-out")
    p.add_argument("--resume", action="store_true",
                   help="resume this exact run; completed and uncertain attempts remain charged and are not resent")
    p.add_argument("--sources")
    p.add_argument("--queries")
    p.add_argument("--only", nargs="*")
    p.add_argument("--include-candidates", action="store_true",
                   help="explicitly probe named candidates selected by --only; does not approve or enable normal searches")
    p.add_argument("--per-source", type=int, default=8)
    p.add_argument("--delay", type=float, default=4.0)
    p.add_argument("--timeout", type=float, default=12.0)
    p.add_argument("--proxy", default=None, help="reserved; outbound proxies are currently rejected")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("replay", help="reparse recorded probe responses offline and compare complete hits")
    p.add_argument("--report", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--allow-parser-change", action="store_true",
                   help="explicitly compare using a different parser identity")
    p.set_defaults(fn=cmd_replay)
    p = sub.add_parser("evaluation", help="offline fixed-candidate judgments, freezing and relevance metrics")
    ep = p.add_subparsers(dest="evaluation_action", required=True)
    e = ep.add_parser("prepare", help="validate fixed evidence and create unjudged templates; no network")
    source = e.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidates", help="versioned fixed-candidate JSON input")
    source.add_argument("--probe-report", help="existing schema-2 probe_report.json with complete saved responses")
    e.add_argument("--ablation-manifest", help="optional versioned source identities and predeclared external ablation comparisons")
    e.add_argument("--queries", help="frozen golden input or explicitly unfrozen synthetic fixture")
    e.add_argument("--splits", nargs="+", choices=("development", "regression"),
                   help="defaults to both non-holdout splits; holdout is not supported")
    e.add_argument("--out", required=True, help="new output directory; never overwrite previous evidence")
    e.set_defaults(fn=cmd_evaluation)
    e = ep.add_parser("freeze", help="freeze exact inputs/method/gate; missing criteria remain blocked")
    e.add_argument("--package", required=True)
    e.add_argument("--gate", required=True)
    e.add_argument("--queries", help="original query file with sibling manifest; omitted verification keeps the gate blocked")
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_evaluation)
    e = ep.add_parser("run", help="score frozen candidates; incomplete judgments cannot pass")
    e.add_argument("--package", required=True)
    e.add_argument("--queries", help="reverify the original query bytes and selected split projection; required to pass")
    e.add_argument("--freeze", required=True)
    e.add_argument("--labels", required=True)
    e.add_argument("--baseline", help="report whose exact SHA-256 was frozen for non-regression")
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_evaluation)
    args = ap.parse_args(argv)
    if args.cmd == "mcp" and args.insecure_no_auth:
        ap.error("--insecure-no-auth is unavailable; HTTP MCP requires a client token")
    if args.cmd == "probe" and args.include_candidates and not args.only:
        ap.error("--include-candidates requires an explicit nonempty --only list")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
