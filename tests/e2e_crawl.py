"""Controlled origins -> real worker/core -> persistent crawl, publication, CLI/MCP.

Every network request targets an explicitly mapped .test fixture or loopback RPC.
Assertions inspect actual HTTP counts, retained results and old search sessions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

import e2e_web

CONFIG = None


class Stack(e2e_web.Stack):
    def __init__(self, root: Path):
        self.root, self.procs, self.logs = root, {}, {}
        self.api_token = secrets.token_hex(32)
        self.other_token = secrets.token_hex(32)
        (root / "auth.json").write_text(json.dumps({"tokens": [
            {"sha256": hashlib.sha256(token.encode()).hexdigest(), "subject": owner, "admin": True}
            for token, owner in ((self.api_token, "owner"), (self.other_token, "other"))]}), encoding="utf-8")
        (root / "auth.json").chmod(0o600)
        (root / "worker.token").write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        (root / "worker.token").chmod(0o600)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.env = dict(os.environ, PYTHONPATH=str(CONFIG.pydir), PYTHONUNBUFFERED="1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            self.env.pop(key, None)
        self.web_port = self.spawn("fakeweb", [CONFIG.python, "-m", "tests.fakecrawl", "0"])["port"]
        sys.path.insert(0, str(CONFIG.pydir))
        from tests import fakecrawl
        (root / "sites.json").write_text(json.dumps(fakecrawl.manifest(self.web_port)), encoding="utf-8")
        (root / "sources.json").write_text('{"version":1,"sources":[]}', encoding="utf-8")
        mappings = [arg for host in fakecrawl.HOSTS for arg in ("--test-host", f"{host}=127.0.0.1")]
        self.worker_command = [CONFIG.python, "-m", "bzs.worker.server", "--port", "0",
            "--token-file", str(root / "worker.token"), "--sources", str(root / "sources.json"),
            "--sites", str(root / "sites.json"), "--test-mode", "--allow-cidr", "127.0.0.1/32", *mappings]
        self.worker_port = self.spawn("worker", self.worker_command)["port"]
        self.start_core()

    def start_core(self):
        old = self.logs.get("searchd")
        if old:
            old.close()
        self.core_port = self.spawn("searchd", [CONFIG.server, "--auth", str(self.root / "auth.json"),
            "--state", str(self.root / "state"), "--fixtures", str(CONFIG.fixtures), "--port", "0", "--threads", "4",
            "--worker", f"http://127.0.0.1:{self.worker_port}", "--worker-token-file", str(self.root / "worker.token"),
            "--sources", str(self.root / "sources.json"), "--sites", str(self.root / "sites.json")])["port"]

    def url(self, path: str, host: str = "bzs-crawl.test") -> str:
        return f"http://{host}:{self.web_port}{path}"

    def stats(self):
        with self.opener.open(f"http://127.0.0.1:{self.web_port}/_test/stats", timeout=5) as response:
            return json.load(response)

    def counts(self):
        return {(row["host"], row["path"]): row["count"] for row in self.stats()["requests"]}

    def control_fixture(self, **changes):
        request = urllib.request.Request(f"http://127.0.0.1:{self.web_port}/_test/control",
            json.dumps(changes).encode(), {"Content-Type": "application/json"}, method="POST")
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)


class CrawlEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bzs-crawl-e2e-")
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

    def ok(self, operation, params=None):
        status, result = self.stack.call(operation, params)
        self.assertEqual(status, 200, result)
        return result["result"]

    def wait_until(self, predicate, description, seconds=20):
        until = time.monotonic() + seconds
        while True:
            result = predicate()
            if result:
                return result
            self.assertLess(time.monotonic(), until, description)
            time.sleep(0.025)

    def completed(self, job_id):
        def terminal():
            job = self.ok("job.get", {"job_id": job_id})
            return job if job["state"] in ("succeeded", "failed", "cancelled", "partial") else None
        return self.wait_until(terminal, f"job {job_id} did not reach a terminal state")

    def submit(self, paths, key, host="bzs-crawl.test", **options):
        return self.ok("crawl.submit", {"site_id": host.split(".")[0],
            "seeds": [self.stack.url(path, host) for path in paths], "idempotency_key": key,
            "max_requests": 40, "max_bytes": 1048576, "max_pages": 20, "max_urls": 40, **options})

    def paged(self, operation, job_id, limit=3):
        items, offset = [], 0
        while True:
            page = self.ok(operation, {"job_id": job_id, "offset": offset, "limit": limit})
            items.extend(page["items"])
            if not page["has_more"] or page.get("next_offset") is None:
                return items
            self.assertGreater(page["next_offset"], offset, page)
            offset = page["next_offset"]

    def results(self, job_id):
        return self.paged("job.results", job_id)

    def crawl_one(self, path, key, **options):
        job = self.submit([path], key, max_depth=0, **options)
        final = self.completed(job["job_id"])
        self.assertEqual(final["state"], "succeeded", final)
        items = self.results(job["job_id"])
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["state"], "succeeded", items)
        return final, items[0]

    def query(self, text):
        return self.ok("search", {"query": text, "limit": 100, "page_size": 100})

    def test_01_recursive_bodies_edges_cycles_and_scope(self):
        before = self.stack.counts()
        job = self.submit(["/crawl/root"], "frontier", max_depth=4)
        final = self.completed(job["job_id"])
        self.assertEqual(final["state"], "succeeded", final)
        pages = self.results(job["job_id"])
        by_url = {row["url"]: row for row in pages}
        paths = ("/crawl/root", "/crawl/a", "/crawl/b", "/crawl/shared", "/crawl/deep/path/leaf")
        self.assertEqual(set(by_url), {self.stack.url(path) for path in paths}, pages)
        for path in paths:
            row = by_url[self.stack.url(path)]
            self.assertTrue(row["body_read"], row)
            self.assertTrue(row["snapshot_id"], row)
            self.assertIn("frontierfixtureunique", self.ok("document.range", {
                "snapshot_id": row["snapshot_id"], "max_bytes": 4000})["text"])
        edges = self.paged("crawl.edges", job["job_id"])
        for parent in ("/crawl/a", "/crawl/b"):
            edge = next((row for row in edges if row["parent"] == self.stack.url(parent) and
                         row["url"] == self.stack.url("/crawl/shared")), None)
            self.assertIsNotNone(edge, edges)
            self.assertTrue(edge["followed"], edge)
            self.assertFalse(edge["reason"], edge)
        self.assertEqual(len(edges), len({(row["parent"], row["url"], row["source"]) for row in edges}))
        counts = self.stack.counts()
        for path in paths:
            self.assertEqual(counts[("bzs-crawl.test", path)] - before.get(("bzs-crawl.test", path), 0), 1)
        for path in ("/outside/ignored", "/crawl/nofollow-target"):
            self.assertEqual(counts.get(("bzs-crawl.test", path), 0), 0)
        self.assertEqual(final["used_pages"], len(paths))
        self.assertEqual(final["usage"]["external_requests"], len(paths) + 1)

    def test_02_page_request_depth_variant_and_byte_budgets(self):
        before = self.stack.counts()
        limited = self.submit(["/crawl/root"], "page-budget", max_pages=2)
        final = self.completed(limited["job_id"])
        self.assertTrue(final["partial"], final)
        self.assertEqual(final["used_pages"], 2)
        self.assertLessEqual(final["budget"]["charged_requests"], 40)
        counts = self.stack.counts()
        body_delta = sum(n - before.get(key, 0) for key, n in counts.items()
                         if key[0] == "bzs-crawl.test" and key[1].startswith("/crawl/"))
        self.assertEqual(body_delta, 2)
        # A fresh origin needs robots first. That metadata request consumes the
        # only request grant and no body URL can be dispatched afterward.
        request = self.submit(["/crawl/root"], "request-budget", "bzs-budget.test", max_requests=1)
        final = self.completed(request["job_id"])
        self.assertTrue(final["partial"], final)
        self.assertEqual(final["budget"]["charged_requests"], 1)
        self.assertEqual(self.stack.counts().get(("bzs-budget.test", "/crawl/root"), 0), 0)
        for path, key, options in (("/crawl/root", "depth-budget", {"max_depth": 0}),
                                  ("/crawl/trap", "query-trap", {}),
                                  ("/crawl/calendar", "calendar-depth", {"max_depth": 2}),
                                  ("/crawl/large", "byte-budget", {"max_bytes": 256})):
            with self.subTest(key=key):
                job = self.submit([path], key, **options)
                final = self.completed(job["job_id"])
                self.assertTrue(final["partial"], final)
                self.assertTrue(final["stop_reason"], final)
                self.assertLessEqual(final["budget"]["charged_bytes"], final["budget"]["max_bytes"])
        queries = {path for host, path in self.stack.counts() if host == "bzs-crawl.test" and path.startswith("/crawl/query?")}
        self.assertLessEqual(len(queries), 4)
        self.assertGreater(len(queries), 0)

    def test_03_noindex_refresh_fences_old_sessions_and_keeps_evidence(self):
        _, original = self.crawl_one("/crawl/policy/index", "index-allowed")
        search = self.query("policyindexunique")
        self.assertEqual(len(search["page"]["hits"]), 1)
        snapshot = original["snapshot_id"]
        evidence = self.ok("evidence.create", {"snapshot_id": snapshot, "start_byte": 0, "end_byte": 5})
        self.stack.control_fixture(policies={"index": "noindex"})
        _, refreshed = self.crawl_one("/crawl/policy/index", "index-refreshed")
        self.assertNotEqual(snapshot, refreshed["snapshot_id"], "header-only policy changes must be persisted")
        self.assertEqual(self.query("policyindexunique")["page"]["hits"], [])
        self.assertEqual(self.ok("search.page", {"session_id": search["session_id"], "page_size": 100})["hits"], [])
        self.assertIn("policyindexunique", self.ok("document.range", {"snapshot_id": snapshot, "max_bytes": 4000})["text"])
        self.assertEqual(self.ok("evidence.read", {"evidence_id": evidence["evidence_id"]})["quote"], evidence["quote"])

    def test_04_snippet_restrictions_apply_to_fresh_and_frozen_results(self):
        _, original = self.crawl_one("/crawl/policy/snippet", "snippet-allowed")
        frozen = self.query("policysnippetunique")
        self.assertTrue(frozen["page"]["hits"][0]["snippet"])
        self.stack.control_fixture(policies={"snippet": "nosnippet"})
        self.crawl_one("/crawl/policy/snippet", "snippet-restricted")
        for page in (self.query("policysnippetunique")["page"],
                     self.ok("search.page", {"session_id": frozen["session_id"], "page_size": 100})):
            self.assertEqual(len(page["hits"]), 1)
            self.assertEqual(page["hits"][0]["snippet"], "")
            self.assertEqual(page["hits"][0].get("highlights", []), [])
        self.assertIn("restrictedexcerptmarker", self.ok("document.range", {
            "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])
        # Removing data-nosnippet text from a separate representation must not
        # relabel its offsets as offsets into the complete canonical snapshot.
        self.crawl_one("/crawl/policy/data", "data-allowed")
        old_data = self.query("policydataunique")
        self.stack.control_fixture(policies={"data": "data"})
        _, restricted = self.crawl_one("/crawl/policy/data", "data-restricted")
        for page in (self.query("policydataunique")["page"],
                     self.ok("search.page", {"session_id": old_data["session_id"], "page_size": 100})):
            self.assertEqual(len(page["hits"]), 1)
            self.assertNotIn("restrictedexcerptmarker", json.dumps(page["hits"]))
            self.assertEqual(page["hits"][0].get("highlights", []), [])
        self.assertIn("restrictedexcerptmarker", self.ok("document.range", {
            "snapshot_id": restricted["snapshot_id"], "max_bytes": 4000})["text"])

    def test_05_empty_restrictive_response_also_withdraws_old_index(self):
        _, original = self.crawl_one("/crawl/policy/empty", "empty-before")
        frozen = self.query("policyemptyunique")
        self.assertEqual(len(frozen["page"]["hits"]), 1)
        self.stack.control_fixture(policies={"empty": "empty-noindex"})
        self.crawl_one("/crawl/policy/empty", "empty-after")
        self.assertEqual(self.query("policyemptyunique")["page"]["hits"], [])
        self.assertEqual(self.ok("search.page", {"session_id": frozen["session_id"]})["hits"], [])
        self.assertIn("policyemptyunique", self.ok("document.range", {
            "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])

    def test_06_agent_scope_nofollow_title_and_links_only_policy(self):
        self.crawl_one("/crawl/policy/other-agent", "other-agent")
        hit = self.query("policyotheragentunique")["page"]["hits"]
        self.assertEqual(len(hit), 1, "Google-specific rules must not prohibit this crawler's indexing")
        self.assertTrue(hit[0]["snippet"])
        self.crawl_one("/crawl/nofollow", "nofollow")
        self.assertEqual(self.stack.counts().get(("bzs-crawl.test", "/crawl/nofollow-target"), 0), 0)
        self.crawl_one("/crawl/nosnippet-title", "restricted-title")
        title_hits = self.query("titlefixtureunique")["page"]["hits"]
        self.assertEqual(len(title_hits), 1)
        self.assertNotIn("restrictedtitlemarker", json.dumps(title_hits))
        _, row = self.crawl_one("/crawl/policy/links", "links-only", host="bzs-links.test")
        links_hits = self.query("policylinksunique")["page"]["hits"]
        self.assertEqual(len(links_hits), 1)
        self.assertEqual(links_hits[0]["snippet"], "")
        self.assertEqual(links_hits[0].get("highlights", []), [])
        self.assertIn("policylinksunique", self.ok("document.range", {
            "snapshot_id": row["snapshot_id"], "max_bytes": 4000})["text"])
        # Explicit refresh is another read entrypoint, not a grant to widen a
        # registered site's links-only redistribution policy.
        self.ok("web.read", {"url": self.stack.url("/crawl/policy/links", "bzs-links.test"), "freshness": "refresh"})
        refreshed_hits = self.query("policylinksunique")["page"]["hits"]
        self.assertEqual(len(refreshed_hits), 1)
        self.assertEqual(refreshed_hits[0]["snippet"], "")
        self.assertEqual(refreshed_hits[0].get("highlights", []), [])

    def test_07_robots_and_redirect_denials_never_dispatch_forbidden_bodies(self):
        for host in ("bzs-deny.test", "bzs-unreachable.test", "bzs-limited.test"):
            with self.subTest(host=host):
                job = self.submit(["/crawl/root"], "robots-" + host, host)
                final = self.completed(job["job_id"])
                self.assertEqual(final["state"], "failed", final)
                self.assertEqual(self.stack.counts().get((host, "/crawl/root"), 0), 0)
                self.assertEqual(self.stack.counts()[(host, "/robots.txt")], 1)
        before = self.stack.counts()
        for kind in ("scope", "private", "robots"):
            with self.subTest(redirect=kind):
                job = self.submit(["/crawl/redirect/" + kind], "redirect-" + kind)
                final = self.completed(job["job_id"])
                self.assertEqual(final["state"], "failed")
                self.assertEqual(final["usage"]["external_requests"], 1, final)
                self.assertEqual(final["budget"]["charged_requests"], 1, final)
                if kind == "private":
                    self.assertIn("E_NETWORK_POLICY", json.dumps(final))
        counts = self.stack.counts()
        for path in ("/outside/redirect-target", "/crawl/private/redirect-target", "/latest/meta-data/"):
            self.assertEqual(counts.get(("bzs-crawl.test", path), 0), before.get(("bzs-crawl.test", path), 0))
        allowed, page = self.crawl_one("/crawl/redirect/allowed", "redirect-allowed")
        self.assertEqual(page["final_url"], self.stack.url("/crawl/redirect-target"))
        self.assertEqual(allowed["used_pages"], 1, "a redirect must not consume a second logical page")
        self.assertEqual(allowed["usage"]["external_requests"], 2)

    def test_08_paused_frontier_survives_restart_without_repeating_committed_pages(self):
        self.stack.control_fixture(close_gates=["recovery"])
        submitted = self.submit(["/crawl/recovery"], "durable-frontier", max_depth=2)
        job_id = submitted["job_id"]
        self.wait_until(lambda: self.stack.counts().get(("bzs-crawl.test", "/crawl/gate/recovery"), 0),
                        "recovery gate was not dispatched")
        rows = self.results(job_id)
        root = next(row for row in rows if row["url"] == self.stack.url("/crawl/recovery"))
        self.assertTrue(root["body_read"], rows)
        self.ok("job.control", {"job_id": job_id, "action": "pause"})
        self.stack.control_fixture(release_gates=["recovery"])
        self.wait_until(lambda: self.ok("job.get", {"job_id": job_id})["state"] == "paused", "job did not pause")
        before = self.stack.counts()
        self.stack.kill("searchd")
        self.stack.start_core()
        self.assertEqual(self.ok("job.get", {"job_id": job_id})["state"], "paused")
        self.assertEqual(self.stack.counts(), before)
        self.ok("job.control", {"job_id": job_id, "action": "resume"})
        final = self.completed(job_id)
        rows = self.results(job_id)
        resumed_root = next(row for row in rows if row["url"] == root["url"])
        self.assertEqual(resumed_root["snapshot_id"], root["snapshot_id"])
        self.assertEqual(self.stack.counts()[("bzs-crawl.test", "/crawl/recovery")], before[("bzs-crawl.test", "/crawl/recovery")])
        self.assertEqual(self.stack.counts()[("bzs-crawl.test", "/crawl/gate/recovery")], before[("bzs-crawl.test", "/crawl/gate/recovery")])
        tail = next(row for row in rows if row["url"] == self.stack.url("/crawl/recovery-tail"))
        self.assertTrue(tail["body_read"], final)
        self.assertLessEqual(final["budget"]["charged_requests"], final["budget"]["max_requests"])
        reused = self.submit(["/crawl/recovery"], "durable-frontier", max_depth=2)
        self.assertEqual(reused["job_id"], job_id)
        self.assertTrue(reused["reused"])

    def test_09_map_and_crawl_jobs_share_fair_origin_dispatch(self):
        host = "bzs-fair.test"
        self.stack.control_fixture(close_gates=["fair"])
        first = self.submit(["/crawl/gate/fair", "/crawl/fair-a", "/crawl/fair-b"], "fair-first", host, max_depth=0)
        self.wait_until(lambda: self.stack.counts().get((host, "/crawl/gate/fair"), 0), "fair first hop not dispatched")
        mapped = self.ok("map.submit", {"site_id": "bzs-fair", "seeds": [self.stack.url("/crawl/maproot", host)],
            "idempotency_key": "fair-map", "max_requests": 5, "max_bytes": 16384})
        second = self.submit(["/crawl/fair-second"], "fair-second", host, max_depth=0)
        self.stack.control_fixture(release_gates=["fair"])
        for job in (first, mapped, second):
            self.assertEqual(self.completed(job["job_id"])["state"], "succeeded")
        order = [row["path"] for row in self.stack.stats()["order"] if row["host"] == host]
        self.assertEqual(order.count("/robots.txt"), 1, order)
        self.assertLess(order.index("/crawl/maproot"), order.index("/crawl/fair-b"), order)
        self.assertLess(order.index("/crawl/fair-second"), order.index("/crawl/fair-b"), order)
        self.assertNotIn("/crawl/map-candidate", order, "map's candidate must remain unread")

    def test_10_late_crawl_response_cannot_revive_deleted_url_alias(self):
        path = "/crawl/gate/deletion"
        self.stack.control_fixture(close_gates=["deletion"])
        alias = self.ok("document.ingest", {"url": self.stack.url(path + "#old", "BZS-CRAWL.test"),
            "content": "deletedaliasfixtureunique", "content_type": "text/plain"})
        submitted = self.submit([path], "deleted-alias", max_depth=0)
        self.wait_until(lambda: self.stack.counts().get(("bzs-crawl.test", path), 0), "deletion gate not dispatched")
        self.ok("document.delete", {"document_id": alias["document_id"]})
        self.stack.control_fixture(release_gates=["deletion"])
        final = self.completed(submitted["job_id"])
        self.assertNotEqual(final["state"], "succeeded", final)
        rows = self.results(submitted["job_id"])
        self.assertTrue(all(not row.get("body_read") and not row.get("snapshot_id") for row in rows), rows)
        self.assertEqual(self.query("deletedaliasfixtureunique")["page"]["hits"], [])
        self.assertEqual(self.paged("crawl.edges", submitted["job_id"]), [])

    def test_11_native_cli_uses_the_same_crawl_operation(self):
        executable = Path(CONFIG.server).with_name("searchctl.exe" if os.name == "nt" else "searchctl")
        path = self.stack.root / "crawl request.json"
        path.write_text(json.dumps({"operation": "crawl.submit", "params": {
            "site_id": "bzs-crawl", "seeds": [self.stack.url("/crawl/cli")], "idempotency_key": "cli-crawl",
            "max_requests": 3, "max_bytes": 8192, "max_pages": 1, "max_depth": 0}}), encoding="utf-8")
        result = subprocess.run([str(executable), "--port", str(self.stack.core_port), "call", str(path)],
            capture_output=True, text=True, encoding="utf-8", env=dict(self.stack.env, SEARCH_API_TOKEN=self.stack.api_token), timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        submitted = json.loads(result.stdout)["result"]
        self.assertEqual(self.completed(submitted["job_id"])["state"], "succeeded")
        self.assertTrue(self.results(submitted["job_id"])[0]["snapshot_id"])

    def test_12_mcp_crawl_to_receipt_edges_and_canonical_read(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def run():
            env = dict(self.stack.env, BZS_CORE_URL=f"http://127.0.0.1:{self.stack.core_port}",
                       SEARCH_API_TOKEN=self.stack.api_token)
            params = StdioServerParameters(command=CONFIG.python, args=["-m", "bzs.mcp_server"], env=env, cwd=str(CONFIG.pydir))
            with open(self.stack.root / "crawl-mcp.log", "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        tools = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertTrue({"crawl_site", "crawl_edges", "get_job", "job_results", "read_range"} <= tools)
                        before = self.stack.counts()
                        for override in ({"max_pages": None}, {"max_pages": True}, {"max_pages": "2"},
                                         {"approved": True}, {"scope_path": "null"}):
                            refused = await session.call_tool("crawl_site", {"site_id": "bzs-crawl",
                                "seeds": [self.stack.url("/crawl/mcp-invalid")],
                                "idempotency_key": "mcp-invalid", **override})
                            self.assertTrue(refused.is_error, refused)
                        self.assertEqual(self.stack.counts(), before)
                        submitted = await session.call_tool("crawl_site", {"site_id": "bzs-crawl",
                            "seeds": [self.stack.url("/crawl/mcp")], "idempotency_key": "mcp-crawl",
                            "max_requests": 3, "max_bytes": 8192, "max_pages": 1, "max_depth": 0})
                        self.assertFalse(submitted.is_error, submitted)
                        job_id = submitted.structured_content["job_id"]
                        until = asyncio.get_running_loop().time() + 15
                        while True:
                            job = await session.call_tool("get_job", {"job_id": job_id})
                            self.assertFalse(job.is_error, job)
                            if job.structured_content["state"] in ("succeeded", "failed", "cancelled", "partial"):
                                break
                            self.assertLess(asyncio.get_running_loop().time(), until)
                            await asyncio.sleep(0.025)
                        self.assertEqual(job.structured_content["state"], "succeeded")
                        pages = await session.call_tool("job_results", {"job_id": job_id})
                        self.assertFalse(pages.is_error, pages)
                        self.assertEqual(pages.structured_content["content_trust"], "untrusted_web_content")
                        snapshot_id = pages.structured_content["items"][0]["snapshot_id"]
                        content = await session.call_tool("read_range", {"snapshot_id": snapshot_id})
                        self.assertFalse(content.is_error, content)
                        self.assertIn("frontierfixtureunique", content.structured_content["text"])
                        edges = await session.call_tool("crawl_edges", {"job_id": job_id})
                        self.assertFalse(edges.is_error, edges)
                        self.assertEqual(edges.structured_content["items"], [])
                        self.assertEqual(edges.structured_content["content_trust"], "untrusted_web_content")

        asyncio.run(asyncio.wait_for(run(), 30))

    def test_13_invalid_budgets_scope_and_caller_approval_are_rejected_before_dispatch(self):
        base = {"site_id": "bzs-crawl", "seeds": [self.stack.url("/crawl/invalid")], "idempotency_key": "invalid-crawl"}
        before = self.stack.counts()
        overrides = [{key: value} for key in ("max_pages", "max_urls", "max_requests", "max_bytes") for value in (0, None, True)]
        overrides += [{key: True} for key in ("approved", "robots", "now_ms", "limits", "max_attempts")]
        overrides += [{"max_pages": 3, "max_urls": 2}, {"scope_path": "/"}, {"scope_path": "/crawl-other/"}]
        overrides += [{"seeds": [self.stack.url(path)]} for path in ("/outside/a", "/crawl/../outside", "/crawl/%2e%2e/outside")]
        overrides += [{"seeds": ["http://169.254.169.254/latest/meta-data/"]}]
        for override in overrides:
            with self.subTest(override=override):
                status, result = self.stack.call("crawl.submit", {**base, **override})
                self.assertGreaterEqual(status, 400, result)
                self.assertEqual(result["status"], "failed", result)
        self.assertEqual(self.stack.counts(), before)

    def test_14_crawl_results_and_edges_remain_owner_isolated(self):
        job, row = self.crawl_one("/crawl/owner", "owner-crawl")
        old_token = self.stack.api_token
        self.stack.api_token = self.stack.other_token
        try:
            for op, params in (("job.get", {"job_id": job["job_id"]}),
                               ("job.results", {"job_id": job["job_id"]}),
                               ("crawl.edges", {"job_id": job["job_id"]}),
                               ("document.range", {"snapshot_id": row["snapshot_id"]})):
                with self.subTest(operation=op):
                    status, result = self.stack.call(op, params)
                    self.assertEqual(status, 404, result)
            self.assertEqual(self.query("frontierfixtureunique")["page"]["hits"], [])
        finally:
            self.stack.api_token = old_token

    def test_15_failed_body_parse_cannot_leave_or_restore_a_forbidden_result(self):
        _, original = self.crawl_one("/crawl/policy/malformed", "malformed-before")
        frozen = self.query("policymalformedunique")
        self.assertEqual(len(frozen["page"]["hits"]), 1)
        self.stack.control_fixture(policies={"malformed": "malformed-noindex"})
        submitted = self.submit(["/crawl/policy/malformed"], "malformed-restricted", max_depth=0)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["state"], "failed", final)
        self.assertTrue(all(not row["body_read"] for row in self.results(submitted["job_id"])))
        self.assertEqual(self.query("policymalformedunique")["page"]["hits"], [])
        self.assertEqual(self.ok("search.page", {"session_id": frozen["session_id"]})["hits"], [])
        self.stack.control_fixture(policies={"malformed": "malformed-allow"})
        submitted = self.submit(["/crawl/policy/malformed"], "malformed-permissive", max_depth=0)
        self.assertEqual(self.completed(submitted["job_id"])["state"], "failed")
        self.assertEqual(self.query("policymalformedunique")["page"]["hits"], [],
                         "a failed parse with permissive headers must not restore the prior result")
        self.assertIn("policymalformedunique", self.ok("document.range", {
            "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])
        # The ordinary read entrypoint also observes a restrictive header even
        # when its new response has no readable body to save.
        original_read = self.ok("web.read", {"url": self.stack.url("/crawl/policy/readempty"), "freshness": "refresh"})
        old_read = self.query("policyreademptyunique")
        self.assertEqual(len(old_read["page"]["hits"]), 1)
        self.stack.control_fixture(policies={"readempty": "empty-noindex"})
        status, result = self.stack.call("web.read", {"url": self.stack.url("/crawl/policy/readempty"), "freshness": "refresh"})
        self.assertGreaterEqual(status, 400, result)
        self.assertEqual(self.query("policyreademptyunique")["page"]["hits"], [])
        self.assertEqual(self.ok("search.page", {"session_id": old_read["session_id"]})["hits"], [])
        self.assertIn("policyreademptyunique", self.ok("document.range", {
            "snapshot_id": original_read["snapshot_id"], "max_bytes": 4000})["text"])

    def test_16_final_url_policy_retracts_previously_read_redirect_representations(self):
        old = self.ok("web.read", {"url": self.stack.url("/crawl/redirect/policy"), "freshness": "refresh"})
        self.assertEqual(old["url"], self.stack.url("/crawl/redirect/policy"))
        self.assertEqual(old["final_url"], self.stack.url("/crawl/policy/redirect"))
        frozen = self.query("policyredirectunique")
        self.assertEqual(len(frozen["page"]["hits"]), 1)
        self.stack.control_fixture(policies={"redirect": "noindex"})
        self.crawl_one("/crawl/policy/redirect", "redirect-policy-restricted")
        self.assertEqual(self.query("policyredirectunique")["page"]["hits"], [])
        self.assertEqual(self.ok("search.page", {"session_id": frozen["session_id"]})["hits"], [])
        self.assertIn("policyredirectunique", self.ok("document.range", {
            "snapshot_id": old["snapshot_id"], "max_bytes": 4000})["text"])

    def test_17_meta_only_empty_refresh_retracts_web_read_and_read_many_results(self):
        for operation, name in (("web.read", "readmeta"), ("read_many.submit", "batchmeta")):
            with self.subTest(operation=operation):
                url = self.stack.url("/crawl/policy/" + name)
                marker = "policy" + name + "unique"
                original = self.ok("web.read", {"url": url, "freshness": "refresh"})
                frozen = self.query(marker)
                self.assertEqual(len(frozen["page"]["hits"]), 1)
                self.stack.control_fixture(policies={name: "empty-meta-noindex"})
                if operation == "web.read":
                    status, result = self.stack.call(operation, {"url": url, "freshness": "refresh"})
                    self.assertGreaterEqual(status, 400, result)
                    self.assertIn("E_PARSE_FAILED", json.dumps(result))
                else:
                    submitted = self.ok(operation, {"urls": [url], "idempotency_key": "batch-meta-refresh",
                                                   "max_requests": 1, "max_bytes": 8192})
                    final = self.completed(submitted["job_id"])
                    self.assertEqual(final["state"], "failed", final)
                    self.assertEqual(final["usage"]["external_requests"], 1)
                    self.assertIn("E_PARSE_FAILED", json.dumps(self.results(submitted["job_id"])))
                self.assertEqual(self.query(marker)["page"]["hits"], [])
                self.assertEqual(self.ok("search.page", {"session_id": frozen["session_id"]})["hits"], [])
                self.assertIn(marker, self.ok("document.range", {
                    "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])

    def test_18_redirect_restriction_is_not_erased_by_allowing_final_body(self):
        logical = self.stack.url("/crawl/redirect/chain-policy")
        target = self.stack.url("/crawl/policy/chain")
        original = self.ok("web.read", {"url": logical, "freshness": "refresh"})
        frozen = self.query("policychainunique")
        self.assertEqual(len(frozen["page"]["hits"]), 1)
        self.stack.control_fixture(policies={"chain-redirect": "noindex"})
        before = self.stack.counts()
        final, row = self.crawl_one("/crawl/redirect/chain-policy", "redirect-chain-ceiling")
        self.assertEqual(row["final_url"], target)
        after = self.stack.counts()
        for path in ("/crawl/redirect/chain-policy", "/crawl/policy/chain"):
            self.assertEqual(after[("bzs-crawl.test", path)] - before.get(("bzs-crawl.test", path), 0), 1)
        self.assertEqual(final["usage"]["external_requests"],
                         sum(value - before.get(key, 0) for key, value in after.items()))
        # The final representation has its own allowing policy; only the
        # logical redirect URL received a restrictive response in this chain.
        hits = self.query("policychainunique")["page"]["hits"]
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["url"], target)
        self.assertEqual(self.ok("search.page", {"session_id": frozen["session_id"]})["hits"], [])
        self.ok("web.read", {"url": logical, "freshness": "refresh"})
        hits = self.query("policychainunique")["page"]["hits"]
        self.assertEqual([hit["url"] for hit in hits], [target],
                         "ordinary read must retain the redirect restriction without restricting independent B")
        submitted = self.ok("read_many.submit", {"urls": [logical], "idempotency_key": "batch-chain-ceiling",
                                               "max_requests": 2, "max_bytes": 8192})
        batch = self.completed(submitted["job_id"])
        self.assertEqual(batch["state"], "succeeded", batch)
        self.assertEqual(batch["usage"]["external_requests"], 2)
        self.assertFalse(self.results(submitted["job_id"])[0]["receipt"]["indexed"])
        hits = self.query("policychainunique")["page"]["hits"]
        self.assertEqual([hit["url"] for hit in hits], [target])
        self.assertIn("policychainunique", self.ok("document.range", {
            "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])
        for name, operation in (("denied-read", "web.read"), ("denied-batch", "read_many.submit")):
            with self.subTest(denied_target=operation):
                entry = self.stack.url("/crawl/redirect/" + name)
                marker = "policy" + name.replace("-", "") + "unique"
                self.ok("web.read", {"url": entry, "freshness": "refresh"})
                saved = self.query(marker)
                self.assertEqual(len(saved["page"]["hits"]), 1)
                self.stack.control_fixture(policies={name: "deny"})
                before = self.stack.counts()
                if operation == "web.read":
                    status, result = self.stack.call(operation, {"url": entry, "freshness": "refresh"})
                    self.assertGreaterEqual(status, 400, result)
                else:
                    submitted = self.ok(operation, {"urls": [entry], "idempotency_key": "denied-target-refresh",
                                                   "max_requests": 2, "max_bytes": 8192})
                    failed = self.completed(submitted["job_id"])
                    self.assertEqual(failed["state"], "failed", failed)
                    self.assertEqual(failed["usage"]["external_requests"], 1)
                self.assertEqual(sum(value - before.get(key, 0) for key, value in self.stack.counts().items()), 1)
                self.assertEqual(self.query(marker)["page"]["hits"], [],
                                 "a refused redirect target must not discard the entry's observed noindex")
                self.assertEqual(self.ok("search.page", {"session_id": saved["session_id"]})["hits"], [])

    def test_19_frozen_results_keep_their_original_representation_policy(self):
        for restriction in ("nosnippet", "noindex"):
            with self.subTest(restriction=restriction):
                kind = "historic-" + restriction
                old_name, new_name = kind + "-old", kind + "-new"
                old_marker = "policy" + old_name.replace("-", "") + "unique"
                new_marker = "policy" + new_name.replace("-", "") + "unique"
                logical = self.stack.url("/crawl/redirect/" + kind)
                original = self.ok("web.read", {"url": logical, "freshness": "refresh"})
                frozen = self.query(old_marker)
                self.assertEqual(len(frozen["page"]["hits"]), 1)
                self.assertTrue(frozen["page"]["hits"][0]["snippet"])
                if restriction == "nosnippet":
                    self.stack.control_fixture(policies={old_name: restriction})
                    self.crawl_one("/crawl/policy/" + old_name, kind + "-restricted")
                    page = self.ok("search.page", {"session_id": frozen["session_id"]})
                    self.assertEqual(page["hits"][0]["snippet"], "")
                self.stack.control_fixture(policies={kind: "new"})
                current = self.ok("web.read", {"url": logical, "freshness": "refresh"})
                self.assertNotEqual(original["snapshot_id"], current["snapshot_id"])
                if restriction == "noindex":
                    # This first restriction arrives only after A has moved
                    # from B to C; the frozen result still represents B.
                    self.stack.control_fixture(policies={old_name: restriction})
                    self.crawl_one("/crawl/policy/" + old_name, kind + "-restricted")
                page = self.ok("search.page", {"session_id": frozen["session_id"]})
                if restriction == "nosnippet":
                    self.assertEqual(len(page["hits"]), 1, page)
                    self.assertEqual(page["hits"][0]["snippet"], "")
                    self.assertEqual(page["hits"][0].get("highlights", []), [])
                else:
                    self.assertEqual(page["hits"], [])
                fresh = self.query(new_marker)["page"]["hits"]
                self.assertEqual(len(fresh), 1, fresh)
                self.assertTrue(fresh[0]["snippet"], "the independent new representation remains allowed")
                self.assertIn(old_marker, self.ok("document.range", {
                    "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])

    def trap_edges(self, job_id, reason):
        edges = [row for row in self.paged("crawl.edges", job_id) if row["reason"] == reason]
        self.assertTrue(edges, reason)
        for row in edges:
            self.assertFalse(row["followed"], row)
            self.assertEqual(row["trap"]["version"], "bzs-crawl-trap-1", row)
            self.assertEqual(row["trap"]["reason"], reason, row)
            self.assertEqual(len(row["trap"]["family_sha256"] or ""), 64, row)
        return edges

    def test_21_date_sequence_and_date_family_stop_before_generic_budgets(self):
        submitted = self.submit(["/crawl/date-sequence/2026/10/01"], "date-sequence", max_depth=8)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["used_pages"], 7, final)
        self.assertEqual(final["stop_reason"], "E_CRAWL_TRAP_DATE_SEQUENCE", final)
        self.assertTrue(final["partial"], final)
        self.assertGreater(final["remaining_pages"], 0)
        self.assertGreater(final["budget"]["remaining_requests"], 0)
        edges = self.trap_edges(submitted["job_id"], "E_CRAWL_TRAP_DATE_SEQUENCE")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["trap"]["observed"], 7)
        self.assertEqual(edges[0]["trap"]["limit"], 6)
        self.assertEqual(self.stack.counts().get(("bzs-crawl.test", "/crawl/date-sequence/2026/10/08"), 0), 0)
        submitted = self.submit(["/crawl/date-family"], "date-family", max_depth=2)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["used_pages"], 17, final)
        self.assertEqual(final["stop_reason"], "E_CRAWL_TRAP_DATE_FAMILY", final)
        self.assertEqual(len(self.trap_edges(submitted["job_id"], "E_CRAWL_TRAP_DATE_FAMILY")), 4)
        for day in range(17, 21):
            self.assertEqual(self.stack.counts().get(("bzs-crawl.test", f"/crawl/date-family/2026/10/{day:02d}"), 0), 0)

    def test_22_filter_combinations_and_query_shape_have_specific_public_reasons(self):
        submitted = self.submit(["/crawl/trap-combinations"], "filter-combinations", max_depth=2)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["used_pages"], 9, final)
        self.assertEqual(final["stop_reason"], "E_CRAWL_TRAP_QUERY_COMBINATIONS", final)
        edges = self.trap_edges(submitted["job_id"], "E_CRAWL_TRAP_QUERY_COMBINATIONS")
        self.assertEqual(len(edges), 2)
        for number in range(9, 11):
            self.assertEqual(self.stack.counts().get(("bzs-crawl.test", f"/crawl/catalog/page/{number}?color={number}&size=large"), 0), 0)
        submitted = self.submit(["/crawl/trap-query-shapes"], "query-shapes", max_depth=2)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["used_pages"], 3, final)
        self.assertEqual(len(self.trap_edges(submitted["job_id"], "E_CRAWL_TRAP_QUERY_PERMUTATIONS")), 1)
        dimensions = [row for row in self.paged("crawl.edges", submitted["job_id"])
                      if row["reason"] == "E_CRAWL_TRAP_QUERY_DIMENSIONS"]
        self.assertEqual({(row["trap"]["observed"], row["trap"]["limit"]) for row in dimensions}, {(9, 8), (17, 16)})
        counts = self.stack.counts()
        for query in ("a=1&b=2&c=3", "b=2&c=3&a=1"):
            self.assertEqual(counts[("bzs-crawl.test", "/crawl/query-shape?" + query)], 1)
        self.assertEqual(counts.get(("bzs-crawl.test", "/crawl/query-shape?c=3&a=1&b=2"), 0), 0)

    def test_23_repeated_pagination_observations_and_reasons_survive_restart(self):
        submitted = self.submit(["/crawl/repeated/page/1"], "repeated-pagination", max_depth=8)
        job_id = submitted["job_id"]
        final = self.completed(job_id)
        self.assertEqual(final["used_pages"], 3, final)
        self.assertEqual(final["stop_reason"], "E_CRAWL_TRAP_DUPLICATE_PAGINATION", final)
        self.assertEqual(final["trap_policy"]["body_observations"], 3, final)
        self.assertEqual(final["trap_policy"]["limited_pagination_families"], 1, final)
        pages = self.results(job_id)
        texts = [self.ok("document.range", {"snapshot_id": row["snapshot_id"], "max_bytes": 8000})["text"] for row in pages]
        self.assertEqual(len(set(texts)), 1, texts)
        self.assertGreaterEqual(len(texts[0].encode()), 256)
        edges = self.trap_edges(job_id, "E_CRAWL_TRAP_DUPLICATE_PAGINATION")
        self.assertEqual(len(edges), 1)
        before = self.stack.counts()
        self.assertEqual(before.get(("bzs-crawl.test", "/crawl/repeated/page/4"), 0), 0)
        self.stack.kill("searchd")
        self.stack.start_core()
        restored = self.ok("job.get", {"job_id": job_id})
        self.assertEqual(restored["trap_policy"], final["trap_policy"])
        self.assertEqual(restored["usage"], final["usage"])
        self.assertEqual(self.results(job_id), pages)
        self.assertEqual(self.trap_edges(job_id, "E_CRAWL_TRAP_DUPLICATE_PAGINATION"), edges)
        reused = self.submit(["/crawl/repeated/page/1"], "repeated-pagination", max_depth=8)
        self.assertEqual(reused["job_id"], job_id)
        self.assertTrue(reused["reused"])
        self.assertEqual(self.stack.counts(), before)

    def test_24_finite_article_slugs_numeric_ids_and_versions_remain_distinct(self):
        submitted = self.submit(["/crawl/finite-documents"], "finite-documents", max_depth=2,
                                max_pages=40, max_urls=60, max_requests=60)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["state"], "succeeded", final)
        self.assertFalse(final["partial"], final)
        self.assertEqual(final["used_pages"], 31, final)
        self.assertEqual(final["trap_policy"]["stops"], [])
        self.assertEqual(len(self.results(submitted["job_id"])), 31)
        self.assertTrue(all(row["body_read"] for row in self.results(submitted["job_id"])))
        edges = self.paged("crawl.edges", submitted["job_id"])
        self.assertEqual(len(edges), 30)
        self.assertTrue(all(row["followed"] and not row["reason"] for row in edges))

    def test_99_manifest_mismatch_retains_refusal_without_body_dispatch(self):
        path = self.stack.root / "sites.json"
        data = json.loads(path.read_text())
        data["sites"][0]["review"]["approved_by"] = "different-synthetic-review"
        path.write_text(json.dumps(data), encoding="utf-8")
        before = self.stack.counts()
        self.stack.kill("worker")
        self.stack.logs["worker"].close()
        command = list(self.stack.worker_command)
        command[command.index("--port") + 1] = str(self.stack.worker_port)
        self.assertEqual(self.stack.spawn("worker", command)["port"], self.stack.worker_port)
        submitted = self.submit(["/crawl/mismatch"], "manifest-mismatch", max_depth=0)
        final = self.completed(submitted["job_id"])
        self.assertEqual(final["state"], "failed", final)
        self.assertIn("E_SITE_MANIFEST_MISMATCH", json.dumps(final))
        self.assertEqual(self.stack.counts(), before)


def main():
    global CONFIG
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--repo", required=True)
    args, rest = parser.parse_known_args()
    repo = Path(args.repo).resolve()
    args.server = str(Path(args.server).resolve())
    args.pydir, args.fixtures = repo / "python", repo / "tests" / "fixtures"
    probe = subprocess.run([args.python, "-c", "import aiohttp, lxml, trafilatura, mcp"], capture_output=True)
    if probe.returncode:
        print("SKIP: worker dependencies missing", file=sys.stderr)
        return 77
    CONFIG = e2e_web.CONFIG = args
    started = time.monotonic()
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False, verbosity=2).result
    print(json.dumps({"crawl_e2e_tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "seconds": round(time.monotonic() - started, 3)}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
