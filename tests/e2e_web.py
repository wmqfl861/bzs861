"""S1 end-to-end test with real processes: fake web <- bzs-worker <- searchd <- HTTP API / MCP stdio.

Never touches the real internet. Every page lives on a local fake web server; the worker maps
the test host name to 127.0.0.1 and exempts only 127.0.0.1/32 from the public-address rule
(both are TEST-ONLY switches). Exit code 77 (CTest "skipped") when the Python worker
dependencies are not installed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = None
HOST = "bzs-e2e.test"


def ready_line(proc: subprocess.Popen, log: Path, timeout: float = 20.0) -> dict:
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: q.put(proc.stdout.readline()), daemon=True).start()
    try:
        line = q.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError(f"no readiness line; log:\n{log.read_text(errors='replace')}")
    if not line:
        raise RuntimeError(f"process exited during startup; log:\n{log.read_text(errors='replace')}")
    return json.loads(line)


class Stack:
    """fakeweb + worker + searchd in a temporary home."""

    def __init__(self, root: Path):
        self.root = root
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, object] = {}
        self.api_token = secrets.token_hex(32)
        worker_token = secrets.token_hex(32)
        (root / "auth.json").write_text(json.dumps({"tokens": [
            {"sha256": hashlib.sha256(self.api_token.encode()).hexdigest(), "subject": "owner", "admin": True}]}),
            encoding="utf-8")
        (root / "auth.json").chmod(0o600)
        (root / "worker.token").write_text(worker_token + "\n", encoding="utf-8")
        (root / "worker.token").chmod(0o600)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        env = dict(os.environ, PYTHONPATH=str(CONFIG.pydir), PYTHONUNBUFFERED="1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        self.env = env
        self.web_port = self.spawn("fakeweb", [CONFIG.python, "-m", "tests.fakeweb", "0"])["port"]
        sys.path.insert(0, str(CONFIG.pydir))
        from tests import fakeweb  # noqa: E402  (python/tests/fakeweb.py)
        self.fakeweb = fakeweb
        (root / "sources.json").write_text(json.dumps(fakeweb.manifest(self.web_port)), encoding="utf-8")
        self.worker_port = self.spawn("worker", [
            CONFIG.python, "-m", "bzs.worker.server", "--port", "0", "--token-file", str(root / "worker.token"),
            "--sources", str(root / "sources.json"), "--test-mode", "--allow-cidr", "127.0.0.1/32",
            "--test-host", f"{HOST}=127.0.0.1"])["port"]
        self.core_port = self.spawn("searchd", [
            CONFIG.server, "--auth", str(root / "auth.json"), "--state", str(root / "state"),
            "--fixtures", str(CONFIG.fixtures), "--port", "0", "--threads", "4",
            "--worker", f"http://127.0.0.1:{self.worker_port}", "--worker-token-file", str(root / "worker.token"),
            "--sources", str(root / "sources.json")])["port"]

    def spawn(self, name: str, cmd: list[str]) -> dict:
        log_path = self.root / f"{name}.log"
        log = open(log_path, "a", encoding="utf-8")
        self.logs[name] = log
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
                                cwd=str(CONFIG.pydir), env=self.env)
        self.procs[name] = proc
        return ready_line(proc, log_path)

    def kill(self, name: str) -> None:
        proc = self.procs.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc and proc.stdout:
            proc.stdout.close()

    def stop(self) -> None:
        for name in ("searchd", "worker", "fakeweb"):
            self.kill(name)
        for log in self.logs.values():
            log.close()

    def url(self, path: str) -> str:
        return f"http://{HOST}:{self.web_port}{path}"

    def call(self, op: str, params: dict | None = None) -> tuple[int, dict]:
        body = json.dumps({"operation": op, "params": params or {}}, ensure_ascii=False).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.core_port}/api/v0/call", body,
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_token},
                                     method="POST")
        try:
            with self.opener.open(req, timeout=60) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as resp:
            return resp.code, json.load(resp)


class EndToEnd(unittest.TestCase):
    stack: Stack

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bzs-e2e-")
        cls.stack = Stack.__new__(Stack)
        cls.stack.procs, cls.stack.logs = {}, {}
        try:
            cls.stack.__init__(Path(cls.tmp.name))
        except Exception:
            cls.stack.stop()
            cls.tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.stack.stop()
        cls.tmp.cleanup()

    def ok(self, op: str, params: dict | None = None) -> dict:
        status, body = self.stack.call(op, params)
        self.assertEqual(status, 200, body)
        return body["result"]

    def err(self, op: str, params: dict, code: str, http: int) -> dict:
        status, body = self.stack.call(op, params)
        self.assertEqual((status, body["errors"][0]["code"]), (http, code), body)
        return body

    def test_01_capabilities(self):
        caps = self.ok("capabilities")
        self.assertEqual(caps["external_network"], "worker_mediated")
        self.assertIn("web.search", caps["operations"])
        self.assertEqual(caps["live_web_sources"], 4)

    def test_02_web_search_merges_sources(self):
        r = self.ok("web.search", {"query": "resumable crawling"})
        statuses = {s["source_id"]: s["status"] for s in r["sources"]}
        self.assertEqual(statuses, {"ddg": "ok", "bing": "ok", "wiki": "ok"})
        self.assertFalse(r["partial"])
        top = r["results"][0]
        # Same page from two engines (one wraps it in a redirect, one adds a trailing slash) is one result.
        self.assertTrue(top["url"].startswith(self.stack.url("/pages/article.html")))
        self.assertEqual({s["source_id"] for s in top["sources"]}, {"ddg", "bing"})
        self.assertEqual(top["index_families"], 2)
        self.assertEqual(top["snippet_kind"], "search_engine_snippet")
        urls = [x["url"] for x in r["results"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(any(u.endswith("/pages/zh.html") for u in urls))

    def test_03_blocked_source_cools_down_and_others_still_answer(self):
        r = self.ok("web.search", {"query": "resumable", "sources": ["blocked", "ddg"]})
        statuses = {s["source_id"]: s["status"] for s in r["sources"]}
        self.assertEqual(statuses["blocked"], "blocked")
        self.assertTrue(r["partial"])
        self.assertTrue(r["results"])
        cached = self.ok("web.search", {"query": "resumable", "sources": ["blocked", "ddg"]})
        self.assertTrue(cached["from_cache"])  # identical query within minutes: no upstream request
        self.assertEqual(cached["usage"]["external_requests"], 0)
        again = self.ok("web.search", {"query": "resumable", "sources": ["blocked", "ddg"], "refresh": True})
        skipped = [s for s in again["sources"] if s["source_id"] == "blocked"][0]
        self.assertEqual((skipped["status"], skipped["reason"]), ("skipped", "cooldown"))
        states = {s["source_id"]: s["state"] for s in self.ok("web.sources")["sources"]}
        self.assertGreater(states["blocked"]["cooldown_remaining_ms"], 0)

    def test_04_read_url_extracts_and_caches(self):
        url = self.stack.url("/pages/article.html")
        first = self.ok("web.read", {"url": url})
        text = first["content"]["text"]
        self.assertFalse(first["from_cache"])
        self.assertEqual(first["content_trust"], "untrusted_web_content")
        self.assertIn("resumable crawling", text)
        self.assertIn("std::expected", text)
        self.assertNotIn("IGNORE PREVIOUS INSTRUCTIONS", text)  # hidden text is dropped
        self.assertNotIn("Copyright footer", text)
        again = self.ok("web.read", {"url": url})
        self.assertTrue(again["from_cache"])
        self.assertEqual(again["snapshot_id"], first["snapshot_id"])
        fresh = self.ok("web.read", {"url": url, "freshness": "refresh"})
        self.assertFalse(fresh["from_cache"])
        self.assertEqual(fresh["snapshot_id"], first["snapshot_id"])  # unchanged content -> same snapshot
        cached = self.ok("web.read", {"url": url, "freshness": "cache_only"})
        self.assertTrue(cached["from_cache"])
        self.err("web.read", {"url": self.stack.url("/pages/never-read"), "freshness": "cache_only"}, "E_NOT_CACHED", 404)

    def test_04b_robots_header_change_replaces_index_projection(self):
        url = self.stack.url("/pages/robots/toggle")
        first = self.ok("web.read", {"url": url, "freshness": "refresh"})
        self.assertTrue(first["indexed"])
        second = self.ok("web.read", {"url": url, "freshness": "refresh"})
        self.assertEqual(first["content"]["text"], second["content"]["text"])
        self.assertFalse(second["indexed"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        hits = self.ok("search", {"query": "resumable"})["page"]["hits"]
        self.assertNotIn(first["document_id"], [hit["document_id"] for hit in hits])
        for kind in ("plain", "markdown", "json"):
            page = self.ok("web.read", {"url": self.stack.url("/pages/robots/" + kind)})
            self.assertFalse(page["indexed"], kind)
            self.assertIn("textrobotsneedle", page["content"]["text"])
        self.assertEqual(self.ok("search", {"query": "textrobotsneedle"})["frozen_count"], 0)

    def test_05_ssrf_is_refused(self):
        for url in (f"http://127.0.0.1:{self.stack.web_port}/pages/article.html", "http://169.254.169.254/latest/meta-data/",
                    f"http://localhost:{self.stack.web_port}/", "http://10.0.0.1/", "http://2130706433/"):
            self.err("web.read", {"url": url}, "E_URL_BLOCKED", 403)
        # A public-looking page that redirects to the metadata service is stopped by the worker.
        self.err("web.read", {"url": self.stack.url("/pages/redirect-private")}, "E_URL_BLOCKED", 403)
        self.err("web.read", {"url": self.stack.url("/pages/redirect-loopback")}, "E_URL_BLOCKED", 403)

    def test_06_gbk_page_and_chinese_local_search(self):
        page = self.ok("web.read", {"url": self.stack.url("/pages/zh.html")})
        self.assertIn("断点续爬", page["content"]["text"])
        self.assertEqual(page["fetch"]["charset"], "gb18030")
        for query in ("断点续爬", "搜索引擎", "检查点"):
            r = self.ok("search", {"query": query})
            self.assertEqual(r["retrieval"], "fts5_bm25_verified", query)
            self.assertGreaterEqual(r["frozen_count"], 1, query)
            hit = r["page"]["hits"][0]
            self.assertEqual(hit["snippet_kind"], "query_highlights")
            self.assertIn(query, hit["snippet"])
        self.assertEqual(self.ok("search", {"query": "断点续传"})["frozen_count"], 0)
        # Agents pass IRIs: non-ASCII paths are percent-encoded before fetching and storing.
        iri = self.ok("web.read", {"url": self.stack.url("/pages/中文路径")})
        self.assertTrue(iri["url"].endswith("/pages/%E4%B8%AD%E6%96%87%E8%B7%AF%E5%BE%84"))
        self.assertIn("断点续爬", iri["content"]["text"])

    def test_07_find_range_and_evidence(self):
        snap = self.ok("web.read", {"url": self.stack.url("/pages/article.html")})["snapshot_id"]
        found = self.ok("find", {"snapshot_id": snap, "text": "checkpoints"})
        self.assertTrue(found["matches"])
        m = found["matches"][0]
        ev = self.ok("evidence.create", {"snapshot_id": snap, "start_byte": m["start_byte"], "end_byte": m["end_byte"]})
        self.assertEqual(ev["quote"], "checkpoints")
        self.assertTrue(ev["location_verified"])
        window = self.ok("document.range", {"snapshot_id": snap, "start_byte": 0, "max_bytes": 40})
        self.assertTrue(window["truncated"])
        self.assertEqual(window["next_start_byte"], window["end_byte"])
        rest = self.ok("document.range", {"snapshot_id": snap, "start_byte": window["next_start_byte"], "max_bytes": 262144})
        self.assertFalse(rest["truncated"])
        local = self.ok("search", {"query": "std::expected"})
        self.assertGreaterEqual(local["frozen_count"], 1)

    def test_08_unreadable_pages_report_why(self):
        self.err("web.read", {"url": self.stack.url("/pages/challenge")}, "E_ACCESS_RESTRICTED", 403)
        self.err("web.read", {"url": self.stack.url("/pages/paper.pdf")}, "E_UNSUPPORTED_CONTENT", 415)
        noindex = self.ok("web.read", {"url": self.stack.url("/pages/other.html")})
        self.assertFalse(noindex["indexed"])
        self.assertEqual(self.ok("search", {"query": "unrelated matters"})["frozen_count"], 0)

    def test_09_mcp_stdio(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def run() -> dict:
            env = dict(self.stack.env, BZS_CORE_URL=f"http://127.0.0.1:{self.stack.core_port}",
                       SEARCH_API_TOKEN=self.stack.api_token)
            params = StdioServerParameters(command=CONFIG.python, args=["-m", "bzs.mcp_server"], env=env,
                                           cwd=str(CONFIG.pydir))
            with open(self.stack.root / "mcp.log", "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = {t.name for t in (await session.list_tools()).tools}
                        search = await session.call_tool("web_search", {"query": "resumable crawling", "limit": 5})
                        read = await session.call_tool("read_url", {"url": self.stack.url("/pages/article.html"), "max_bytes": 300})
                        iri = await session.call_tool("read_url", {"url": self.stack.url("/pages/中文路径")})
                        local = await session.call_tool("search_local", {"query": "断点续爬"})
                        bad = await session.call_tool("read_url", {"url": "http://169.254.169.254/"})
                        return {"tools": tools, "search": search, "read": read, "local": local, "bad": bad, "iri": iri}

        out = asyncio.run(asyncio.wait_for(run(), 60))
        self.assertTrue({"web_search", "read_url", "read_range", "find_in_page", "search_local", "cite_evidence",
                         "list_sources"} <= out["tools"])
        search = out["search"].structured_content
        self.assertFalse(out["search"].is_error)
        self.assertTrue(search["results"][0]["url"].startswith(self.stack.url("/pages/article.html")))
        read = out["read"].structured_content
        self.assertEqual(read["content_trust"], "untrusted_web_content")
        self.assertLessEqual(len(read["text"].encode()), 300)
        self.assertIsNotNone(read["next_start_byte"])
        self.assertTrue(out["local"].structured_content["hits"])
        self.assertTrue(out["bad"].is_error)
        self.assertFalse(out["iri"].is_error)
        self.assertIn("断点续爬", out["iri"].structured_content["text"])
        self.assertIn("E_URL_BLOCKED", out["bad"].content[0].text)

    def test_09b_mcp_http_requires_a_client_token(self):
        import socket

        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        home = self.stack.root / "mcp-home"
        env = dict(self.stack.env, BZS_HOME=str(home), BZS_CORE_URL=f"http://127.0.0.1:{self.stack.core_port}",
                   SEARCH_API_TOKEN=self.stack.api_token)
        refused = subprocess.run([CONFIG.python, "-m", "bzs.mcp_server", "--transport", "streamable-http", "--port", "1"],
                                 cwd=str(CONFIG.pydir), env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 2)  # no client tokens yet -> refuses to serve HTTP
        made = subprocess.run([CONFIG.python, "-m", "bzs.cli", "mcp-token", "add", "laptop"], cwd=str(CONFIG.pydir),
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(made.returncode, 0, made.stderr)
        token = made.stdout.strip()
        stored = (home / "mcp_clients.json").read_text(encoding="utf-8")
        self.assertNotIn(token, stored)  # only the digest is kept
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        log = open(self.stack.root / "mcp-http.log", "w", encoding="utf-8")
        proc = subprocess.Popen([CONFIG.python, "-m", "bzs.mcp_server", "--transport", "streamable-http", "--port",
                                 str(port)], cwd=str(CONFIG.pydir), env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with socket.socket() as s:
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.1)
            url = f"http://127.0.0.1:{port}/mcp"
            probe = urllib.request.Request(url, b"{}", {"Content-Type": "application/json",
                                                        "Accept": "application/json, text/event-stream"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as no_token:
                self.stack.opener.open(probe, timeout=10)
            self.assertEqual(no_token.exception.code, 401)
            probe.add_header("Authorization", "Bearer bzsmcp_wrong")
            with self.assertRaises(urllib.error.HTTPError) as wrong:
                self.stack.opener.open(probe, timeout=10)
            self.assertEqual(wrong.exception.code, 401)

            async def run() -> dict:
                client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
                async with client:
                    async with streamable_http_client(url, http_client=client) as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            await session.initialize()
                            result = await session.call_tool("web_search", {"query": "resumable crawling", "limit": 3})
                            return result.structured_content

            out = asyncio.run(asyncio.wait_for(run(), 60))
            self.assertTrue(out["results"])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()

    def test_10_worker_down_is_explicit_and_local_features_survive(self):
        self.stack.kill("worker")
        self.err("web.search", {"query": "resumable"}, "E_WORKER_UNAVAILABLE", 503)
        self.err("web.read", {"url": self.stack.url("/pages/new.html")}, "E_WORKER_UNAVAILABLE", 503)
        self.assertGreaterEqual(self.ok("search", {"query": "resumable"})["frozen_count"], 1)
        cached = self.ok("web.read", {"url": self.stack.url("/pages/article.html")})
        self.assertTrue(cached["from_cache"])


def main() -> int:
    global CONFIG
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--repo", required=True)
    args, rest = ap.parse_known_args()
    repo = Path(args.repo).resolve()
    args.server = str(Path(args.server).resolve())
    args.pydir = repo / "python"
    args.fixtures = repo / "tests" / "fixtures"
    probe = subprocess.run([args.python, "-c", "import aiohttp, lxml, trafilatura, mcp"], capture_output=True)
    if probe.returncode != 0:
        print("SKIP: worker dependencies missing; run `python3 -m pip install -e python[test]`", file=sys.stderr)
        return 77
    CONFIG = args
    started = time.monotonic()
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False, verbosity=2).result
    print(json.dumps({"e2e_tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                      "seconds": round(time.monotonic() - started, 1)}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
