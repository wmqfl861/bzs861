#!/usr/bin/env python3
"""S1 sign-off harness: evaluate the nine S1 completion criteria on this machine.

The criteria are quoted from docs/planning/development_blueprint_v1.2.md
"### S1 完成标准" (one criterion per line, cited by blueprint_line). Each
criterion is graded PASS / FAIL / BLOCKED with a reason, evidence lines and the
commit/config identity behind it. A JSON artifact (accept_s1.json) is written
to the output directory and a terminal table is printed.

Exit codes: 0 only when all nine criteria PASS; 1 when any FAILs; 3 when none
FAIL but at least one is BLOCKED (missing evidence); 2 for usage/input errors.
A BLOCKED criterion states the exact evidence that would satisfy it.

D15/F3: the shared marginalia `public` key is allowed for S0 probing only. An
enabled marginalia source whose configured key environment variable is unset,
empty or literally `public` FAILs criterion 1: the adapter silently falls back
to the shared public key (python/bzs/worker/sources/apis.py, Marginalia.request,
`... or "public"`), and S1 must run on the user's own key.

The service under test is started by this tool itself (worker + searchd through
the real `bzs start` launcher path) inside an isolated BZS_HOME, so the run
never depends on — or disturbs — an already-running deployment.

Usage: python tools/accept_s1.py [--sources PATH] [--probe-report PATH]
                                 [--ci-run-url URL] [--read-url URL] [--out DIR]
                                 [--skip-live]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_REL = "docs/planning/development_blueprint_v1.2.md"
ARTIFACT_SCHEMA_VERSION = 1
STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED = "PASS", "FAIL", "BLOCKED"
_STATUS_ORDER = {STATUS_FAIL: 2, STATUS_BLOCKED: 1, STATUS_PASS: 0}
CI_STEP_NAME = "Address and undefined-behavior checks"
# Blueprint L71 binds criterion 5's defect-fix-merge question to this handoff
# review (path relative to docs/planning/); the original package's B1–B7 labels
# cannot substitute this round's verification.
CLAUDE_S1_REVIEW_REL = "../handoff/CLAUDE_S1_REVIEW_20261018.md"
CLAUDE_S1_REVIEW_TREE_PATH = "docs/handoff/CLAUDE_S1_REVIEW_20261018.md"

# One entry per blueprint criterion; `quote` must equal the cited blueprint
# line byte-exactly (a full line, not a substring) so the enumeration cannot
# drift from the authored criteria — truncation loses requirements.
CRITERIA: list[dict[str, Any]] = [
    {"id": "c1", "title": "web_search 来源审核/启用/S0 实测与合并隔离", "blueprint_line": 67,
     "quote": "1. `web_search`：至少 2 个经过有效审核、显式启用并经 S0 真实实测可用的来源，含至少 1 个通用网页源；合并去重、每源状态和单源失败隔离通过。假网站和候选 usable 不能代替审核或真实通用网页证据。"},
    {"id": "c2", "title": "read_url 读取/私网阻断/快照/缓存模式", "blueprint_line": 68,
     "quote": "2. `read_url`：读取公开网页，阻断本机/内网/云元数据地址（含经 DNS 与重定向的绕过），提取正文为 Markdown，存快照，支持 `cache_first / refresh / cache_only`。"},
    {"id": "c3", "title": "search_local 本地检索与字节区间", "blueprint_line": 69,
     "quote": "3. `search_local`：检索读过的页面，中文查询走索引，BM25 排序，返回与查询相关的原文片段（字节区间可校验）。"},
    {"id": "c4", "title": "find_in_page 与 read_range 可用", "blueprint_line": 70,
     "quote": "4. `find_in_page`、`read_range` 可用。"},
    {"id": "c5", "title": "缺陷合并修复与 ASan/UBSan 证据绑定", "blueprint_line": 71,
     "quote": "5. 当前主分支与 S1 增量缺陷合并修复，保留原生合同和历史回归；本轮实际自动化与 ASan/UBSan 覆盖有源码/命令/日志绑定。问题对应 `../handoff/CLAUDE_S1_REVIEW_20261018.md`，不能用原包 B1–B7 标签替代本轮核验。"},
    {"id": "c6", "title": "bzs start / stdio MCP 可复现启动与 HTTP 鉴权", "blueprint_line": 72,
     "quote": "6. `bzs start` 与 stdio MCP 配置可复现启动；本机 HTTP 必须鉴权。"},
    {"id": "c7", "title": "C++ 配置合同与双重准入保留", "blueprint_line": 73,
     "quote": "7. 原三类 C++ 配置合同和纯校验 API/CLI 保留，调用方不能改时钟/硬上限；正常来源在核心与 worker 双重准入。"},
    {"id": "c8", "title": "schema 3 历史迁移与旧备份边界", "blueprint_line": 74,
     "quote": "8. schema 3 对两种历史 schema 2 的迁移通过，快照/证据/删除/隔离性质保留；旧备份仍不直接恢复为服务。"},
    {"id": "c9", "title": "代理/解析放宽/无鉴权拒绝与边界回归", "blueprint_line": 75,
     "quote": "9. 生产代理、远程 DNS/CIDR 放宽与无鉴权 HTTP 拒绝；新跨来源凭据、出站与解析边界回归通过。当前 S0/S1 是否完成以实际状态和完整证据判断。"},
]
CRITERIA_IDS = [c["id"] for c in CRITERIA]

DEFAULT_READ_URL = "https://example.com/"
DEFAULT_PROBE_GLOB = "bzs861-s0-probe-*"  # directories produced by docs/S0_COMMAND.md


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Pure analysis helpers (unit-tested offline; no network, no enabling).
# --------------------------------------------------------------------------

def marginalia_public_key_fallback(spec: Mapping[str, Any], env: Mapping[str, str]) -> str | None:
    """D15/F3 detector: an *enabled* marginalia deployment on the shared public key.

    Returns a reason string when the source is enabled and its key environment
    variable is unset, empty or literally `public` (exactly the states where the
    adapter silently substitutes the shared public key), None otherwise.
    Disabled sources are out of scope: probing them with the shared key is
    exactly what S0 allows.
    """
    if not spec.get("enabled"):
        return None
    params = spec.get("params", {})
    if params.get("adapter") != "marginalia_api" and spec.get("id") != "marginalia":
        return None
    key_env = params.get("key_env", "BZS_MARGINALIA_KEY")
    value = (env.get(key_env) or "").strip()
    if not value:
        return (f"source '{spec['id']}' is enabled but {key_env} is unset/empty, so the marginalia adapter "
                "silently falls back to the shared public key (apis.py Marginalia.request `... or \"public\"`); "
                "S1 requires the user's own key (D15)")
    if value == "public":
        return (f"source '{spec['id']}' is enabled but {key_env} is set to the shared public key; "
                "the public key is allowed for S0 probing only, not for S1 (D15)")
    return None


def manifest_analysis(specs: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Split a manifest into enabled / review-approved / approved general-web ids."""
    from bzs.worker.sources.review import SourceReviewError, require_review

    enabled: list[str] = []
    approved: list[str] = []
    approved_web: list[str] = []
    for spec in specs:
        if not spec.get("enabled"):
            continue
        enabled.append(spec["id"])
        try:
            require_review(dict(spec))
        except SourceReviewError:
            continue
        approved.append(spec["id"])
        if spec.get("capability") == "web":
            approved_web.append(spec["id"])
    return {"enabled": enabled, "approved": approved, "approved_web": approved_web}


def probe_report_issues(report: Mapping[str, Any], manifest_sha256: str,
                        needed_ids: Iterable[str]) -> list[str]:
    """Validate S0 probe evidence: real (non-fixture), bound to this manifest, with
    successful real attempts for each needed source id. Empty list = evidence OK."""
    issues: list[str] = []
    if not isinstance(report, Mapping):
        return ["probe evidence is not a JSON object"]
    if report.get("environment", {}).get("test_fixture") is True:
        issues.append("probe evidence was produced against test fixtures, not real sources (environment.test_fixture)")
    identity = report.get("identity", {})
    if not isinstance(identity, Mapping) or identity.get("manifest_sha256") != manifest_sha256:
        issues.append("probe evidence is bound to a different sources manifest (identity.manifest_sha256 mismatch)")
    rows = report.get("rows")
    if not isinstance(rows, list):
        issues.append("probe evidence has no attempt rows")
        rows = []
    ok_ids = {row.get("source") for row in rows
              if isinstance(row, Mapping) and row.get("status") == "ok"
              and isinstance(row.get("results"), list) and row.get("results")}
    for source_id in needed_ids:
        if source_id not in ok_ids:
            issues.append(f"no real successful (status=ok, hits>0) probe attempt recorded for '{source_id}'")
    return issues


def aggregate(parts: Iterable[str]) -> str:
    """FAIL beats BLOCKED beats PASS."""
    ranked = sorted((_STATUS_ORDER.get(part, STATUS_BLOCKED) for part in parts), reverse=True)
    if not ranked:
        return STATUS_BLOCKED
    return {2: STATUS_FAIL, 1: STATUS_BLOCKED, 0: STATUS_PASS}[ranked[0]]


def exit_code(statuses: Iterable[str]) -> int:
    statuses = list(statuses)
    if len(statuses) != len(CRITERIA):
        raise ValueError(f"expected exactly {len(CRITERIA)} criterion statuses")
    if STATUS_FAIL in statuses:
        return 1
    if all(status == STATUS_PASS for status in statuses):
        return 0
    if STATUS_BLOCKED in statuses:
        return 3
    raise ValueError(f"unknown criterion status in {statuses!r}")


def render_table(results: list[dict[str, Any]]) -> str:
    lines = ["| # | id | status | criterion | reason |", "|---|---|---|---|---|"]
    for index, (criterion, result) in enumerate(zip(CRITERIA, results), start=1):
        reason = (result.get("reason") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {index} | {criterion['id']} | {result['status']} | {criterion['title']} | {reason[:100]} |")
    counts = Counter(result["status"] for result in results)
    lines.append("")
    lines.append(f"PASS {counts.get(STATUS_PASS, 0)} / FAIL {counts.get(STATUS_FAIL, 0)} / "
                 f"BLOCKED {counts.get(STATUS_BLOCKED, 0)}; exit 0 only if all {len(CRITERIA)} PASS")
    return "\n".join(lines)


def build_artifact(meta: Mapping[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the durable JSON artifact; guard criterion enumeration from drifting."""
    if [r.get("id") for r in results] != CRITERIA_IDS:
        raise ValueError("results must cover exactly the nine criteria in order: " + ", ".join(CRITERIA_IDS))
    for result in results:
        if result["status"] not in (STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED):
            raise ValueError(f"invalid criterion status: {result['status']!r}")
        result.setdefault("reason", None)
        result.setdefault("evidence", [])
        result.setdefault("config_identity", {})
    counts = Counter(result["status"] for result in results)
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "tool": "tools/accept_s1.py",
        "blueprint": {"path": BLUEPRINT_REL, "section": "### S1 完成标准"},
        "generated_at": _utc_now(),
        "bzs": dict(meta.get("bzs", {})),
        "config": dict(meta.get("config", {})),
        "run": dict(meta.get("run", {})),
        "criteria": [dict(result) for result in results],
        "summary": {"pass": counts.get(STATUS_PASS, 0), "fail": counts.get(STATUS_FAIL, 0),
                    "blocked": counts.get(STATUS_BLOCKED, 0)},
    }
    artifact["exit_code"] = exit_code([result["status"] for result in results])
    return artifact


class Grades:
    """Per-criterion check accumulator: (status, note) pairs plus evidence lines."""

    def __init__(self) -> None:
        self.parts: dict[str, list[tuple[str, str]]] = {cid: [] for cid in CRITERIA_IDS}
        self.evidence: dict[str, list[str]] = {cid: [] for cid in CRITERIA_IDS}
        self.extra: dict[str, dict[str, Any]] = {cid: {} for cid in CRITERIA_IDS}

    def add(self, criterion_id: str, status: str, note: str) -> None:
        self.parts[criterion_id].append((status, note))

    def note(self, criterion_id: str, line: str) -> None:
        self.evidence[criterion_id].append(line)

    def block_all(self, criterion_ids: Iterable[str], note: str) -> None:
        for criterion_id in criterion_ids:
            self.add(criterion_id, STATUS_BLOCKED, note)
            self.note(criterion_id, note)

    def results(self, config_identity: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for criterion in CRITERIA:
            cid = criterion["id"]
            parts = self.parts[cid]
            status = aggregate((status for status, _ in parts))
            failing = [note for part_status, note in parts if part_status != STATUS_PASS]
            if status == STATUS_PASS:
                reason = f"{criterion['title']}: all {len(parts)} check(s) passed"
            else:
                reason = "; ".join(dict.fromkeys(failing))[:300] or criterion["title"]
            identity = {"criterion": criterion["title"], "blueprint": f"{BLUEPRINT_REL}#L{criterion['blueprint_line']}",
                        **dict(config_identity), **self.extra[cid]}
            out.append({"id": cid, "title": criterion["title"],
                        "blueprint_ref": f"{BLUEPRINT_REL}#L{criterion['blueprint_line']}",
                        "status": status, "reason": reason, "evidence": self.evidence[cid],
                        "config_identity": identity})
        return out


# --------------------------------------------------------------------------
# Live run: identity, isolated service, criterion execution.
# --------------------------------------------------------------------------

def _import_bzs():
    try:
        import bzs
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT / "python"))
        import bzs
    return bzs


def git_identity(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                                       timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() or None

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None, "note": "git metadata unavailable"}
    return {"commit": commit, "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": git("status", "--porcelain") is not None}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IsolatedService:
    """Worker + searchd started through the real launcher in a throwaway BZS_HOME.

    The sources manifest, searchd binary, sites and fixtures paths come from the
    user's configuration; state, tokens, logs and PID records live only in the
    isolated home, so the user's deployment is never touched.
    """

    def __init__(self, out_dir: Path, sources_path: Path, searchd: str, sites: str, fixtures: str):
        self.home = out_dir / "bzs-home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.searchd = searchd
        self.commands: list[list[str]] = []
        self.started = False
        self.error: str | None = None
        from bzs import cli as bzs_cli
        import bzs.config as config
        self._cli, self._config = bzs_cli, config
        self._previous_home = os.environ.get("BZS_HOME")
        api_token, worker_token = secrets.token_hex(32), secrets.token_hex(32)
        (self.home / "api.token").write_text(api_token + "\n", encoding="utf-8")
        (self.home / "worker.token").write_text(worker_token + "\n", encoding="utf-8")
        (self.home / "auth.json").write_text(json.dumps({"tokens": [
            {"sha256": hashlib.sha256(api_token.encode()).hexdigest(), "subject": "accept-s1", "admin": True}]}),
            encoding="utf-8")
        self.core_port, self.worker_port = _free_port(), _free_port()
        cfg = config.Config(core_port=self.core_port, worker_port=self.worker_port, threads=4, searchd=searchd,
                            sources=str(sources_path), sites=sites, fixtures=fixtures,
                            state_dir=str(self.home / "state"))
        (self.home / "config.json").write_text(json.dumps(cfg.__dict__), encoding="utf-8")

    def __enter__(self) -> "IsolatedService":
        os.environ["BZS_HOME"] = str(self.home)
        try:
            cfg = self._config.load()
            self.commands = [
                [sys.executable, "-m", "bzs.worker.server", "--port", str(cfg.worker_port), "--token-file",
                 str(self.home / "worker.token"), "--sources", cfg.sources],
                [cfg.searchd, "--auth", str(self.home / "auth.json"), "--state", cfg.state_dir, "--fixtures",
                 cfg.fixtures, "--port", str(cfg.core_port), "--threads", str(cfg.threads), "--worker",
                 f"http://127.0.0.1:{cfg.worker_port}", "--worker-token-file", str(self.home / "worker.token"),
                 "--sources", cfg.sources],
            ]
            self._cli.ensure_running(cfg)
            self.started = True
        except BaseException as exc:  # service problems grade criteria, they do not abort the tool
            self.error = f"{type(exc).__name__}: {exc}"[:500]
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._cli.stop()
        except BaseException:
            pass
        if self._previous_home is None:
            os.environ.pop("BZS_HOME", None)
        else:
            os.environ["BZS_HOME"] = self._previous_home

    def client(self):
        from bzs.core_client import CoreClient
        return CoreClient(f"http://127.0.0.1:{self.core_port}",
                          (self.home / "api.token").read_text(encoding="utf-8").strip())


def _http_unauthorized(core_url: str) -> tuple[bool, str]:
    """POST the core API without a token; it must be refused with 401/403."""
    request = urllib.request.Request(
        f"{core_url}/api/v0/call",
        data=json.dumps({"operation": "web.sources", "params": {}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(request, timeout=10)
        return False, "core accepted an unauthenticated API call"
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403), f"unauthenticated core call rejected with HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"could not reach core for the no-auth probe: {exc.reason}"


def _newest_probe_report(user_profile: Path) -> Path | None:
    candidates = sorted(user_profile.glob(f"{DEFAULT_PROBE_GLOB}/probe_report.json"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _phase_static(grades: Grades, specs: list[dict[str, Any]], manifest_sha: str, manifest_path: Path,
                  probe_path: Path | None, env: Mapping[str, str]) -> dict[str, list[str]]:
    """Criterion 1 checks that need no service: review, enablement, D15 key, S0 evidence."""
    analysis = manifest_analysis(specs)
    grades.note("c1", f"manifest {manifest_path} sha256={manifest_sha[:16]}…; enabled={analysis['enabled'] or '[]'}; "
                      f"review-approved={analysis['approved'] or '[]'}; approved general-web={analysis['approved_web'] or '[]'}")
    if len(analysis["approved"]) >= 2:
        grades.add("c1", STATUS_PASS, f"{len(analysis['approved'])} enabled sources carry a currently valid approved review")
    else:
        grades.add("c1", STATUS_FAIL, f"only {len(analysis['approved'])} enabled source(s) carry a currently valid "
                                      "approved review; at least 2 are required (candidate/unverified reviews do not count)")
    if analysis["approved_web"]:
        grades.add("c1", STATUS_PASS, f"approved general-web source(s) enabled: {analysis['approved_web']}")
    else:
        grades.add("c1", STATUS_FAIL, "no review-approved source with capability 'web' is enabled; at least 1 general-web "
                                      "source is required (encyclopedia-only enablement does not satisfy this)")
    key_reasons = [reason for spec in specs if (reason := marginalia_public_key_fallback(spec, env))]
    for reason in key_reasons:
        grades.add("c1", STATUS_FAIL, "D15 violation: " + reason)
        grades.note("c1", "D15 violation: " + reason)
    if not key_reasons:
        grades.add("c1", STATUS_PASS, "no enabled marginalia deployment runs on the shared public key")
    if probe_path is None:
        grades.add("c1", STATUS_BLOCKED, "no S0 probe report found; run the command in docs/S0_COMMAND.md and pass "
                                         "--probe-report (required evidence: a real, non-fixture probe_report.json with "
                                         "ok attempts for the enabled sources, bound to this manifest)")
    else:
        try:
            report = json.loads(probe_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report, error = None, f"{type(exc).__name__}: {exc}"
        if report is None:
            grades.add("c1", STATUS_BLOCKED, f"probe report {probe_path} is unreadable ({error})")
        else:
            issues = probe_report_issues(report, manifest_sha, analysis["approved"])
            grades.note("c1", f"S0 evidence {probe_path}: " + ("every enabled approved source has real ok attempts and "
                                                              "the report is bound to this manifest" if not issues
                                                              else "; ".join(issues)))
            grades.add("c1", STATUS_BLOCKED if issues else STATUS_PASS,
                       "S0 probe evidence incomplete: " + "; ".join(issues) if issues else "S0 probe evidence complete")
    return analysis


def _check_mcp_config(grades: Grades, cli: Any = None) -> None:
    """c6 sub-check: `bzs mcp-config` must emit a stdio MCP client config bound
    to this BZS_HOME. `cli` is injectable so the regression tests can replay a
    stdout buffer captured from the real command."""
    try:
        if cli is None:
            from bzs import cli as bzs_cli
            cli = bzs_cli
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main(["mcp-config"])
        # cmd_mcp_config prints json.dumps(..., indent=2) on stdout (the client
        # guidance goes to stderr), so the WHOLE buffer is one JSON document
        # whose first line is just `{` — parse it whole, never line by line.
        mcp_config = json.loads(buffer.getvalue())
        ok = "bzs861" in mcp_config.get("mcpServers", {})
        grades.add("c6", STATUS_PASS if ok else STATUS_FAIL,
                   "`bzs mcp-config` emits a stdio MCP client config bound to this BZS_HOME"
                   if ok else "`bzs mcp-config` output did not contain the bzs861 server entry")
        grades.note("c6", "mcp-config: " + json.dumps(mcp_config, ensure_ascii=False)[:200])
    except Exception as exc:
        grades.add("c6", STATUS_BLOCKED, f"mcp-config could not be produced: {type(exc).__name__}: {exc}"[:300])


def _phase_service(grades: Grades, service: IsolatedService, args: argparse.Namespace, meta: dict[str, Any]) -> None:
    if not service.started:
        grades.block_all(CRITERIA_IDS[1:], f"service did not start in the isolated home: {service.error}")
        return
    client = service.client()

    # ---- c1 live: merge, per-source status, single-source failure isolation ----
    try:
        live = client.call_sync("web.search", {"query": "open source search engine", "limit": 5})
        statuses = {s["source_id"]: s["status"] for s in live.get("sources", [])}
        errored = sorted(sid for sid, status in statuses.items() if status not in ("ok", "empty"))
        answered = sorted(sid for sid, status in statuses.items() if status in ("ok", "empty"))
        meta.setdefault("live", {})["web_search_sources"] = statuses
        grades.note("c1", f"live web.search per-source statuses {statuses}; isolation "
                          f"{'exercised (errored ' + ','.join(errored) + ' while ' + ','.join(answered) + ' answered)' if errored and answered else 'not exercised in this sample'}; "
                          f"results={len(live.get('results', []))}")
        if statuses or live.get("results"):
            grades.add("c1", STATUS_PASS, "live web.search returned merged results with per-source statuses")
        else:
            grades.add("c1", STATUS_FAIL, "live web.search returned neither per-source statuses nor results")
    except Exception as exc:
        grades.add("c1", STATUS_BLOCKED, f"live web.search could not be verified from this machine: "
                                         f"{type(exc).__name__}: {exc}"[:300])

    # ---- c2 read_url: public read + cache modes + private-address refusals ----
    title = ""
    try:
        first = client.call_sync("web.read", {"url": args.read_url, "max_bytes": 20000, "freshness": "cache_first"})
        snapshot_id = first.get("snapshot_id")
        title = (first.get("title") or "").strip()
        text = (first.get("content", {}) or {}).get("text", "")
        refreshed = client.call_sync("web.read", {"url": args.read_url, "max_bytes": 20000, "freshness": "refresh"})
        cached = client.call_sync("web.read", {"url": args.read_url, "max_bytes": 20000, "freshness": "cache_only"})
        ok = bool(snapshot_id) and bool(text.strip()) and bool(refreshed.get("snapshot_id")) and bool(cached.get("snapshot_id"))
        grades.add("c2", STATUS_PASS if ok else STATUS_FAIL,
                   f"public read of {args.read_url} via cache_first/refresh/cache_only "
                   + ("succeeded with a stored snapshot" if ok else "did not produce text + snapshot in all three modes"))
        grades.note("c2", f"read {args.read_url}: snapshot={snapshot_id}, title={title[:60]!r}, "
                          f"text={len(text)} chars; refresh snapshot={refreshed.get('snapshot_id')}, "
                          f"cache_only snapshot={cached.get('snapshot_id')}")
        grades.extra["c2"]["snapshot_id"] = snapshot_id
        grades.extra["c3"]["snapshot_id"] = snapshot_id
        grades.extra["c4"]["snapshot_id"] = snapshot_id
    except Exception as exc:
        grades.add("c2", STATUS_BLOCKED, f"public read of {args.read_url} failed from this machine: "
                                         f"{type(exc).__name__}: {exc}"[:300])
    for label, url in (("loopback core port", f"http://127.0.0.1:{service.core_port}/"),
                       ("cloud metadata", "http://169.254.169.254/latest/meta-data/")):
        try:
            client.call_sync("web.read", {"url": url, "max_bytes": 100})
            grades.add("c2", STATUS_FAIL, f"web.read accepted a private address ({label}: {url})")
        except Exception:
            grades.add("c2", STATUS_PASS, f"web.read refused the private address ({label}: {url})")
    grades.note("c2", "DNS-rebinding and redirect-chain bypass coverage is asserted by the netguard/e2e regressions "
                      "(python/tests/test_netguard.py, tests/e2e_*.py); bind them to this commit with --ci-run-url")

    # ---- c3/c4 against the snapshot stored by c2 ----
    snapshot_id = grades.extra["c3"].get("snapshot_id")
    query = title or "example domain"
    needle = max(query.split(), key=len) if query.split() else "example"
    if not snapshot_id:
        for cid in ("c3", "c4"):
            grades.add(cid, STATUS_BLOCKED, "no stored snapshot from criterion 2; run with network access so a real "
                                            "page can be read and indexed first")
    else:
        try:  # c4
            found = client.call_sync("find", {"snapshot_id": snapshot_id, "text": needle, "limit": 5})
            ranged = client.call_sync("document.range", {"snapshot_id": snapshot_id, "start_byte": 0, "max_bytes": 2000})
            matches = found.get("matches", [])
            ok = bool(matches) and bool((ranged.get("content", {}) or {}).get("text"))
            grades.add("c4", STATUS_PASS if ok else STATUS_FAIL,
                       f"find_in_page({needle!r}) matched {len(matches)} time(s) and read_range returned a text window"
                       if ok else f"find_in_page/read_range did not both answer (matches={len(matches)})")
            if matches:
                grades.note("c4", f"first match bytes {matches[0]['start_byte']}..{matches[0]['end_byte']}; "
                                  f"document.range returned {len((ranged.get('content', {}) or {}).get('text', ''))} chars from byte 0")
        except Exception as exc:
            grades.add("c4", STATUS_BLOCKED, f"find/document.range failed: {type(exc).__name__}: {exc}"[:300])
        try:  # c3
            local = client.call_sync("search.once", {"query": query, "limit": 5})
            hits = local.get("hits", [])
            grades.add("c3", STATUS_PASS if hits else STATUS_FAIL,
                       f"search_local({query!r}) returned {len(hits)} hit(s) against the stored page"
                       if hits else f"search_local({query!r}) returned no hits for the page just read")
            highlight_hit = next((hit for hit in hits if hit.get("highlights")), None)
            if highlight_hit:
                span = highlight_hit["highlights"][0]
                cited = client.call_sync("evidence.create", {
                    "snapshot_id": highlight_hit.get("snapshot_id") or snapshot_id,
                    "start_byte": span["start_byte"], "end_byte": span["end_byte"]})
                verified = cited.get("location_verified") is True
                grades.add("c3", STATUS_PASS if verified else STATUS_FAIL,
                           f"byte-interval verification via cite_evidence location_verified={cited.get('location_verified')}")
                grades.note("c3", f"highlight bytes {span['start_byte']}..{span['end_byte']} verified against the snapshot")
            grades.note("c3", "BM25 ranking and the Chinese-query path are asserted by the local-search regressions "
                              "(python/tests, tests/e2e_web.py); this live check exercises one real page end-to-end")
        except Exception as exc:
            grades.add("c3", STATUS_BLOCKED, f"search.once/evidence.create failed: {type(exc).__name__}: {exc}"[:300])

    # ---- c6: launcher reproduction (this run), stdio MCP surface, HTTP auth ----
    grades.add("c6", STATUS_PASS, "worker+searchd were started by this run through the real bzs launcher path "
                                  f"(ensure_running) in isolated BZS_HOME {service.home} on ports "
                                  f"{service.worker_port}/{service.core_port}")
    grades.note("c6", "launcher commands: " + " | ".join(" ".join(cmd) for cmd in service.commands)[:400])
    _check_mcp_config(grades)
    try:
        import asyncio
        from bzs.mcp_server import build_server
        server = build_server()
        listed = {tool.name for tool in asyncio.run(server.list_tools())}
        required = {"web_search", "read_url", "search_local", "find_in_page", "read_range"}
        missing = required - listed
        grades.add("c6", STATUS_PASS if not missing else STATUS_FAIL,
                   f"stdio MCP server lists {len(listed)} tools including the five required"
                   if not missing else f"stdio MCP server is missing required tools: {sorted(missing)}")
    except Exception as exc:
        grades.add("c6", STATUS_BLOCKED, f"stdio MCP server could not be built/listed in-process: "
                                         f"{type(exc).__name__}: {exc}"[:300])
    authorized, detail = _http_unauthorized(f"http://127.0.0.1:{service.core_port}")
    grades.add("c6", STATUS_PASS if authorized else STATUS_FAIL, "local HTTP API rejects unauthenticated calls: " + detail)

    # ---- c7 local: pure maintenance CLI + dual admission observation ----
    searchctl = Path(service.searchd).with_name("searchctl.exe" if os.name == "nt" else "searchctl")
    if searchctl.exists():
        probe = subprocess.run([str(searchctl)], capture_output=True, text=True, timeout=20)
        usage = (probe.stdout + probe.stderr)
        ok = "storage-inspect" in usage and "storage-convert" in usage
        grades.add("c7", STATUS_PASS if ok else STATUS_FAIL,
                   "searchctl ships beside searchd and advertises the pure storage-inspect/storage-convert CLI"
                   if ok else "searchctl no longer advertises storage-inspect/storage-convert")
    else:
        grades.add("c7", STATUS_BLOCKED, f"searchctl not found at {searchctl}; the pure validation CLI must ship with "
                                         "the build")
    try:
        listed_sources = client.call_sync("web.sources", {})
        entries = listed_sources.get("sources", [])
        enabled_entries = [entry for entry in entries if entry.get("enabled")]
        grades.add("c7", STATUS_PASS, f"web.sources exposes the admission view: {len(entries)} registered, "
                                      f"{len(enabled_entries)} enabled")
        grades.note("c7", "web.sources: " + ", ".join(f"{e['source_id']}={e.get('verification', '?')}"
                                                      for e in enabled_entries[:8] or entries[:8]))
        grades.note("c7", "core and worker both validate the manifest digest and review state "
                          "(src contract validation + python/bzs/worker/sources/review.py require_review)")
    except Exception as exc:
        grades.add("c7", STATUS_BLOCKED, f"web.sources unavailable: {type(exc).__name__}: {exc}"[:300])

    # ---- c9 local: production relaxations refused ----
    from bzs.config import Config
    base = json.loads((service.home / "config.json").read_text(encoding="utf-8"))
    for field, value in (("proxy", "http://127.0.0.1:9999"), ("allow_cidrs", ["10.0.0.0/8"]),
                         ("proxy_remote_dns", True)):
        try:
            relaxed = Config(**{**base, field: value})
            service._cli.start(relaxed)
            grades.add("c9", STATUS_FAIL, f"bzs start accepted a production-relaxed config ({field}={value!r})")
        except SystemExit as exc:
            grades.add("c9", STATUS_PASS, f"bzs start refused {field}={value!r} ({exc})")
        except (RuntimeError, ValueError, OSError) as exc:
            grades.add("c9", STATUS_PASS if str(exc).startswith("E_CONFIG") else STATUS_FAIL,
                       f"bzs start refused {field}={value!r}: {type(exc).__name__}: {exc}"[:200])
    authorized, detail = _http_unauthorized(f"http://127.0.0.1:{service.core_port}")
    grades.add("c9", STATUS_PASS if authorized else STATUS_FAIL, "no-auth HTTP rejection: " + detail)
    grades.note("c9", "cross-source credential, outbound and resolution boundary regressions "
                      "(test_netguard.py, e2e suites) are CI-verified; bind them to this commit with --ci-run-url")


def _phase_ci(grades: Grades, ci_run_url: str | None, git_meta: Mapping[str, Any]) -> str:
    """Evidence that only exists as CI runs bound to the HEAD commit (c5 always; the
    C++-side parts of c7/c8/c9). Returns the 'what would satisfy it' text used by BLOCKED notes."""
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    sanitizer_declared = False
    if workflow.exists():
        try:
            sanitizer_declared = CI_STEP_NAME in workflow.read_text(encoding="utf-8")
        except OSError:
            sanitizer_declared = False
    needed = ("supply --ci-run-url with a completed CI run of HEAD commit "
              f"{git_meta.get('commit') or '<unknown>'} whose Linux job includes the '{CI_STEP_NAME}' "
              "(ASan+UBSan) step plus the full CTest/pytest matrix")
    # c5: automation + sanitizer coverage bound to this round's code; the
    # blueprint (L71) also binds this criterion's defect-fix-merge question to
    # the CLAUDE_S1_REVIEW handoff, which must surface in the evidence.
    handoff = REPO_ROOT / CLAUDE_S1_REVIEW_TREE_PATH
    handoff_binding = (f"criterion 5 defect-fix-merge is bound to {CLAUDE_S1_REVIEW_REL} "
                       f"({CLAUDE_S1_REVIEW_TREE_PATH}, present in this tree: {handoff.exists()}); the original "
                       "package's B1–B7 labels cannot substitute this round's verification")
    grades.note("c5", f"HEAD {git_meta.get('commit')} dirty={git_meta.get('dirty')}; sanitizer step declared in "
                      ".github/workflows/ci.yml: " + str(sanitizer_declared))
    grades.note("c5", handoff_binding)
    if ci_run_url and sanitizer_declared:
        grades.add("c5", STATUS_PASS, f"supplied CI run attests the {CI_STEP_NAME} step for this commit: {ci_run_url}")
        grades.note("c5", "supplied-run attestation (not verified from this machine): source/command/log binding "
                          "per .github/workflows/ci.yml (asan preset, UBSAN_OPTIONS) and the run logs at " + ci_run_url)
    else:
        grades.add("c5", STATUS_BLOCKED, f"ASan/UBSan evidence for this exact commit not supplied; defect-fix-merge "
                                         f"binding to {CLAUDE_S1_REVIEW_TREE_PATH} applies; {needed}")
    # c7/c8/c9 C++-side evidence
    for cid, suites in (("c7", "bzs_contract_tests over docs/planning/validation/contract_cases.json "
                               "(three C++ config contracts, pure validation API/CLI, clock/hard-cap immutability)"),
                        ("c8", "bzs_storage_maintenance_tests + bzs_incremental_storage_tests (schema-3 migration of "
                               "both historical schema-2 layouts; old backups still refuse to boot as a service)"),
                        ("c9", "boundary regressions: test_netguard.py, tests/e2e_*.py, http_admission.py")):
        if ci_run_url:
            grades.add(cid, STATUS_PASS, f"{suites} are green per the supplied CI run attestation: {ci_run_url}")
        else:
            grades.add(cid, STATUS_BLOCKED, f"{suites} are not bound to this commit locally; {needed}")
        grades.note(cid, suites)
    return needed


def run(argv: list[str] | None = None) -> int:
    bzs = _import_bzs()
    parser = argparse.ArgumentParser(prog="accept_s1", description=__doc__.splitlines()[0])
    parser.add_argument("--sources", help="sources manifest to evaluate and run (default: the sources path of the "
                        "existing BZS_HOME config)")
    parser.add_argument("--out", help="output directory (default: <BZS_HOME>/accept-s1/<timestamp>)")
    parser.add_argument("--probe-report", help="probe_report.json from an S0 run (default: newest "
                        f"{DEFAULT_PROBE_GLOB}/probe_report.json under the user profile)")
    parser.add_argument("--ci-run-url", help="completed CI run URL for the HEAD commit; backs the ASan/UBSan, C++ "
                        "contract, migration and boundary-regression evidence")
    parser.add_argument("--read-url", default=DEFAULT_READ_URL,
                        help=f"public page read by criterion 2 (default: {DEFAULT_READ_URL})")
    parser.add_argument("--skip-live", action="store_true", help="skip network/live checks; live criteria become "
                        "BLOCKED (useful for dry runs, never for sign-off)")
    args = parser.parse_args(argv)

    import bzs.config as config
    try:
        user_cfg = config.load()
    except (FileNotFoundError, ValueError, OSError, KeyError) as exc:
        print(f"E_INPUT: cannot load the existing config ({exc}); run `bzs init` first or pass --sources",
              file=sys.stderr)
        return 2
    sources_path = Path(args.sources or user_cfg.sources)
    if not sources_path.is_absolute():
        sources_path = Path.cwd().joinpath(sources_path).resolve()
    if not sources_path.exists():
        print(f"E_INPUT: sources manifest not found: {sources_path}", file=sys.stderr)
        return 2
    from bzs.worker.sources import load_manifest
    manifest_sha = hashlib.sha256(sources_path.read_bytes()).hexdigest()
    specs = load_manifest(str(sources_path))

    out_dir = Path(args.out) if args.out else config.home() / "accept-s1" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_path = Path(args.probe_report) if args.probe_report else _newest_probe_report(Path.home())

    git_meta = git_identity(REPO_ROOT)
    meta: dict[str, Any] = {
        "bzs": {"version": getattr(bzs, "__version__", None), "file": str(Path(bzs.__file__).resolve()), **git_meta},
        "config": {"sources_manifest": str(sources_path), "manifest_sha256": manifest_sha,
                   "user_bzs_home": str(config.home()), "searchd": user_cfg.searchd,
                   "probe_report": str(probe_path) if probe_path else None, "ci_run_url": args.ci_run_url},
        "run": {"argv": sys.argv[1:], "skip_live": args.skip_live, "read_url": args.read_url, "service": None},
    }
    grades = Grades()
    analysis = _phase_static(grades, specs, manifest_sha, sources_path, probe_path, os.environ)

    if args.skip_live:
        grades.block_all(CRITERIA_IDS[1:], "--skip-live: live verification withheld; run without it on the sign-off "
                                            "machine")
    elif not Path(user_cfg.searchd).exists():
        grades.block_all(CRITERIA_IDS[1:], f"searchd binary not found at {user_cfg.searchd}; build it first "
                                           "(cmake --preset release && cmake --build --preset release)")
    else:
        with IsolatedService(out_dir, sources_path, user_cfg.searchd, user_cfg.sites, user_cfg.fixtures) as service:
            meta["run"]["service"] = {"isolated_home": str(service.home), "core_port": service.core_port,
                                      "worker_port": service.worker_port, "started": service.started,
                                      "error": service.error, "commands": [" ".join(cmd) for cmd in service.commands]}
            _phase_service(grades, service, args, meta)

    _phase_ci(grades, args.ci_run_url, git_meta)

    results = grades.results({"manifest_sha256": manifest_sha, "commit": git_meta.get("commit"),
                              "ci_run_url": args.ci_run_url})
    artifact = build_artifact(meta, results)
    artifact_path = out_dir / "accept_s1.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_table(results))
    print(f"\nS1 acceptance artifact: {artifact_path}")
    for result in results:
        if result["status"] != STATUS_PASS:
            print(f"[{result['id']} {result['status']}] {result['reason']}")
    return artifact["exit_code"]


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
