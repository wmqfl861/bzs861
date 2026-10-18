"""Synthetic origins -> real worker -> persistent core -> HTTP/CLI/MCP discovery.

No public network requests. Only explicitly mapped .test hosts use the loopback
test exception; the production resolver and reviewed-site checks stay enabled.
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
        worker_token = secrets.token_hex(32)
        (root / "auth.json").write_text(json.dumps({"tokens": [
            {"sha256": hashlib.sha256(self.api_token.encode()).hexdigest(), "subject": "owner", "admin": True}]}))
        (root / "auth.json").chmod(0o600)
        (root / "worker.token").write_text(worker_token + "\n")
        (root / "worker.token").chmod(0o600)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.env = dict(os.environ, PYTHONPATH=str(CONFIG.pydir), PYTHONUNBUFFERED="1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            self.env.pop(key, None)
        self.web_port = self.spawn("fakeweb", [CONFIG.python, "-m", "tests.fakemap", "0"])["port"]
        sys.path.insert(0, str(CONFIG.pydir))
        from tests import fakemap
        (root / "sites.json").write_text(json.dumps(fakemap.manifest(self.web_port)), encoding="utf-8")
        (root / "sources.json").write_text('{"version":1,"sources":[]}', encoding="utf-8")
        mappings = [arg for host in fakemap.HOSTS for arg in ("--test-host", f"{host}=127.0.0.1")]
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

    def url(self, path: str, host: str = "bzs-map.test") -> str:
        return f"http://{host}:{self.web_port}{path}"

    def counts(self) -> dict[tuple[str, str], int]:
        with self.opener.open(f"http://127.0.0.1:{self.web_port}/_test/stats", timeout=5) as response:
            return {(row["host"], row["path"]): row["count"] for row in json.load(response)["requests"]}


class DiscoveryEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bzs-map-e2e-")
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
        status, body = self.stack.call(operation, params)
        self.assertEqual(status, 200, body)
        return body["result"]

    def completed(self, job_id):
        until = time.monotonic() + 20
        while True:
            job = self.ok("job.get", {"job_id": job_id})
            if job["state"] in ("succeeded", "failed", "cancelled", "partial"):
                return job
            self.assertLess(time.monotonic(), until, job)
            time.sleep(0.025)

    def submit(self, paths, key, host="bzs-map.test", **options):
        params = {"site_id": host.split(".")[0], "seeds": [self.stack.url(path, host) for path in paths],
                  "idempotency_key": key, "max_requests": 30, "max_bytes": 1048576, **options}
        return self.ok("map.submit", params)

    def results(self, job_id):
        items, offset = [], 0
        while True:
            page = self.ok("job.results", {"job_id": job_id, "offset": offset, "limit": 3})
            items.extend(page["items"])
            next_offset = page.get("next_offset")
            if next_offset is None:
                return items
            self.assertGreater(next_offset, offset, page)
            offset = next_offset

    def test_01_reviewed_registry_and_all_discovery_formats_do_not_read_candidates(self):
        registry = self.ok("map.sites")
        self.assertEqual(len(registry["sites"]), 4)
        self.assertTrue(all(site["review_valid"] for site in registry["sites"]))
        self.assertEqual(len(registry["manifest_sha256"]), 64)
        before = self.ok("stats")["documents"]
        job = self.submit(["/map/index.xml", "/map/feed.xml", "/map/feed.atom", "/map/index.html"], "formats")
        final = self.completed(job["job_id"])
        self.assertEqual(final["state"], "succeeded", final)
        items = self.results(job["job_id"])
        by_url = {item["url"]: item for item in items}
        expected = {"/map/candidates/sitemap-a": "sitemap", "/map/candidates/rss": "rss",
                    "/map/candidates/atom": "atom", "/map/candidates/html": "html"}
        for path, source in expected.items():
            item = by_url[self.stack.url(path)]
            self.assertEqual(item["source"], source, item)
            self.assertFalse(item["body_read"])
            self.assertNotIn("snapshot_id", item)
            self.assertTrue(item["parent"].startswith(self.stack.url("/map/")))
        deep = by_url[self.stack.url("/map/deep/path/candidate-b")]
        self.assertGreater(deep["path_depth"], deep["depth"])
        self.assertTrue(all(item["url"].startswith(self.stack.url("/map/")) for item in items))
        self.assertEqual(len(items), len(by_url), "fragments or Sitemap cycles duplicated candidates")
        self.assertEqual(self.ok("stats")["documents"], before)
        counts = self.stack.counts()
        self.assertEqual(counts[("bzs-map.test", "/robots.txt")], 1)
        for path in ("/map/index.xml", "/map/nested.xml", "/map/child.xml"):
            self.assertEqual(counts[("bzs-map.test", path)], 1, counts)
        for path in (*expected, "/outside/sitemap.xml", "/outside/not-in-scope"):
            self.assertEqual(counts.get(("bzs-map.test", path), 0), 0)

    def test_02_robots_denial_unreachable_and_rate_limit_never_dispatch_content(self):
        for host in ("bzs-deny.test", "bzs-unreachable.test", "bzs-limited.test"):
            with self.subTest(host=host):
                job = self.submit(["/map/index.html"], "denied-" + host, host)
                final = self.completed(job["job_id"])
                self.assertEqual(final["state"], "failed", final)
                self.assertEqual(self.results(job["job_id"]), [])
                counts = self.stack.counts()
                self.assertEqual(counts.get((host, "/map/index.html"), 0), 0)
                self.assertEqual(counts[(host, "/robots.txt")], 1)

    def test_03_redirects_recheck_scope_and_robots(self):
        before = self.stack.counts()
        for kind in ("scope", "private", "robots"):
            with self.subTest(kind=kind):
                job = self.submit(["/map/redirect/" + kind], "redirect-" + kind)
                self.assertEqual(self.completed(job["job_id"])["state"], "failed")
        counts = self.stack.counts()
        for path in ("/outside/redirect-target", "/map/private/redirect-target", "/latest/meta-data/"):
            self.assertEqual(counts.get(("bzs-map.test", path), 0), before.get(("bzs-map.test", path), 0))

    def test_04_restart_reuses_persistent_rules_and_idempotent_results(self):
        params = {"site_id": "bzs-map", "seeds": [self.stack.url("/map/feed.xml")],
                  "idempotency_key": "persisted-map", "max_requests": 5, "max_bytes": 8192}
        submitted = self.ok("map.submit", params)
        self.assertEqual(self.completed(submitted["job_id"])["state"], "succeeded")
        items = self.results(submitted["job_id"])
        counts = self.stack.counts()
        self.stack.kill("searchd")
        self.stack.start_core()
        reused = self.ok("map.submit", params)
        self.assertEqual(reused["job_id"], submitted["job_id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(self.results(reused["job_id"]), items)
        self.assertEqual(self.stack.counts(), counts)
        after = self.submit(["/map/feed.atom"], "after-restart")
        self.assertEqual(self.completed(after["job_id"])["state"], "succeeded")
        self.assertEqual(self.stack.counts()[("bzs-map.test", "/robots.txt")], counts[("bzs-map.test", "/robots.txt")])

    def test_05_history_is_visible_without_new_body_publication_and_xml_entities_are_blocked(self):
        historical = self.ok("web.read", {"url": self.stack.url("/map/candidates/history"), "freshness": "refresh"})
        alias_url = self.stack.url("/map/candidates/history-alias#old", "BZS-MAP.test")
        canonical_alias = self.stack.url("/map/candidates/history-alias")
        alias = self.ok("document.ingest", {"url": alias_url, "content": "Imported history alias fixture.",
                                             "content_type": "text/plain"})
        before = self.ok("stats")["documents"]
        job = self.submit(["/map/feed.xml"], "history-map")
        self.assertEqual(self.completed(job["job_id"])["state"], "succeeded")
        history = [item for item in self.results(job["job_id"])
                   if item["url"] == self.stack.url("/map/candidates/history")]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "history")
        self.assertFalse(history[0]["body_read"])
        aliases = [item for item in self.results(job["job_id"]) if item["url"] == canonical_alias]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["source"], "history")
        self.assertFalse(aliases[0]["body_read"])
        self.assertEqual(self.ok("stats")["documents"], before)
        self.ok("document.delete", {"document_id": historical["document_id"]})
        self.ok("document.delete", {"document_id": alias["document_id"]})
        # Deletion must hide already published map candidates even when their
        # normalized URL differs from the original document's stored identity.
        remaining = [item["url"] for item in self.results(job["job_id"])]
        self.assertNotIn(self.stack.url("/map/candidates/history"), remaining)
        self.assertNotIn(canonical_alias, remaining)
        later = self.submit(["/map/feed.xml"], "deleted-history-map")
        self.completed(later["job_id"])
        self.assertNotIn(self.stack.url("/map/candidates/history"), [x["url"] for x in self.results(later["job_id"])])
        self.assertNotIn(canonical_alias, [x["url"] for x in self.results(later["job_id"])])
        self.assertEqual(self.stack.counts().get(("bzs-map.test", "/map/candidates/history-alias"), 0), 0)
        entity = self.submit(["/map/entity.xml"], "entity-map")
        self.assertEqual(self.completed(entity["job_id"])["state"], "failed")
        self.assertEqual(sum(n for (_, path), n in self.stack.counts().items() if path == "/_test/entity-target"), 0)

    def test_06_mcp_discovery_to_persistent_candidates(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def run():
            env = dict(self.stack.env, BZS_CORE_URL=f"http://127.0.0.1:{self.stack.core_port}",
                       SEARCH_API_TOKEN=self.stack.api_token)
            params = StdioServerParameters(command=CONFIG.python, args=["-m", "bzs.mcp_server"],
                                          env=env, cwd=str(CONFIG.pydir))
            with open(self.stack.root / "map-mcp.log", "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        tools = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertTrue({"list_sites", "map_site", "get_job", "job_results"} <= tools)
                        registry = await session.call_tool("list_sites", {})
                        self.assertFalse(registry.is_error, registry)
                        submitted = await session.call_tool("map_site", {"site_id": "bzs-map",
                            "seeds": [self.stack.url("/map/feed.atom")], "idempotency_key": "mcp-map",
                            "max_requests": 3, "max_bytes": 8192, "scope_path": "/map/"})
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
                        result = await session.call_tool("job_results", {"job_id": job_id})
                        self.assertFalse(result.is_error, result)
                        return job.structured_content, result.structured_content

        job, results = asyncio.run(asyncio.wait_for(run(), 30))
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(results["content_trust"], "untrusted_web_content")
        self.assertIn(self.stack.url("/map/candidates/atom"), [item["url"] for item in results["items"]])
        self.assertTrue(all(item["body_read"] is False for item in results["items"]))

    def test_07_native_cli_uses_the_same_map_operation(self):
        executable = Path(CONFIG.server).with_name("searchctl.exe" if os.name == "nt" else "searchctl")
        request_path = self.stack.root / "map request.json"
        request_path.write_text(json.dumps({"operation": "map.submit", "params": {
            "site_id": "bzs-map", "seeds": [self.stack.url("/map/feed.xml")],
            "idempotency_key": "native-cli-map", "max_requests": 3, "max_bytes": 8192}}), encoding="utf-8")
        env = dict(self.stack.env, SEARCH_API_TOKEN=self.stack.api_token)
        result = subprocess.run([str(executable), "--port", str(self.stack.core_port), "call", str(request_path)],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        submitted = json.loads(result.stdout)["result"]
        self.assertEqual(self.completed(submitted["job_id"])["state"], "succeeded")
        candidates = self.results(submitted["job_id"])
        self.assertIn(self.stack.url("/map/candidates/rss"), [item["url"] for item in candidates])

    def test_08_scope_and_privileged_overrides_are_rejected_before_any_dispatch(self):
        before = self.stack.counts()
        base = {"site_id": "bzs-map", "seeds": [self.stack.url("/map/feed.xml")], "idempotency_key": "invalid-map"}
        overrides = [{key: 0} for key in ("max_requests", "max_bytes", "max_urls")]
        overrides += [{key: True} for key in ("approved", "admission_token", "now_ms", "limits")]
        overrides += [{"scope_path": "/"}, {"scope_path": "/map2/"}]
        overrides += [{"seeds": [self.stack.url(path)]} for path in (
            "/outside/a", "/map/../outside", "/map/%2e%2e/outside", "/map%2f../outside",
            "/map/%252e%252e/outside", "/map/\\outside")]
        overrides += [{"seeds": ["http://169.254.169.254/latest/meta-data/"]}]
        for override in overrides:
            with self.subTest(override=override):
                status, result = self.stack.call("map.submit", {**base, **override})
                self.assertGreaterEqual(status, 400, result)
                self.assertEqual(result["status"], "failed")
        self.assertEqual(self.stack.counts(), before)

    def test_09_worker_manifest_mismatch_cannot_reuse_core_robots_admission(self):
        # The two deployments deliberately have different policy identities.
        # Keep the worker's endpoint fixed so a connection error cannot satisfy
        # the assertion intended for an actual manifest rejection.
        path = self.stack.root / "sites.json"
        data = json.loads(path.read_text())
        data["sites"][0]["review"]["approved_by"] = "different-fixture-review"
        path.write_text(json.dumps(data), encoding="utf-8")
        before = self.stack.counts()
        self.stack.kill("worker")
        self.stack.logs["worker"].close()
        command = list(self.stack.worker_command)
        command[command.index("--port") + 1] = str(self.stack.worker_port)
        ready = self.stack.spawn("worker", command)
        self.assertEqual(ready["port"], self.stack.worker_port)
        submitted = self.submit(["/map/feed.xml"], "manifest-mismatch")
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
    print(json.dumps({"map_e2e_tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "seconds": round(time.monotonic() - started, 3)}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
