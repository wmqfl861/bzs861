"""Actual fixture HTTP -> Worker -> Core conditional refresh, without SQLite writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import e2e_web

CONFIG = None
HOST = "bzs-revalidation.test"


class Origin:
    def __init__(self):
        self.requests = []
        self.redirect = False
        self.bad_robots = False
        self.bad_page = False
        self.restrict_304 = ""
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                conditional = self.headers.get("If-None-Match", "")
                fixture.requests.append({"path": self.path, "etag": conditional,
                    "last_modified": self.headers.get("If-Modified-Since", ""),
                    "user_agent": self.headers.get("User-Agent", "")})
                robots = self.path == "/robots.txt"
                if fixture.redirect and self.path == "/docs/plain":
                    self.send_response(302)
                    self.send_header("Location", "/docs/redirect-target")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                tag = '"fixture-1"'
                status = 304 if conditional == tag else 200
                if status == 304 and (fixture.bad_robots if robots else fixture.bad_page):
                    tag = '"wrong-body"'
                if robots:
                    body, mime = b"User-agent: BzsBot\nAllow: /\n", "text/plain"
                elif self.path == "/docs/root":
                    body = ("<html><title>Conditional fixture</title><main><p>"
                            + "Revalidation fixture text remains byte stable and readable. " * 20
                            + '</p><a href="/docs/child">child document</a></main></html>').encode()
                    mime = "text/html"
                else:
                    body = ("conditionalneedle " + "The retained page remains available with exact body evidence. " * 20).encode()
                    mime = "text/plain"
                self.send_response(status)
                self.send_header("ETag", tag)
                self.send_header("Last-Modified", "Tue, 14 Nov 2023 22:13:20 GMT")
                self.send_header("Cache-Control", "max-age=2" if robots else "max-age=3600")
                if status == 304 and not robots and fixture.restrict_304:
                    self.send_header("X-Robots-Tag", fixture.restrict_304)
                if status != 304:
                    self.send_header("Content-Type", mime + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if status != 304:
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class Stack(e2e_web.Stack):
    def __init__(self, root: Path, fixture: Origin):
        self.root, self.procs, self.logs = root, {}, {}
        self.fixture, self.web_port = fixture, fixture.port
        self.api_token = secrets.token_hex(32)
        (root / "auth.json").write_text(json.dumps({"tokens": [{
            "sha256": hashlib.sha256(self.api_token.encode()).hexdigest(), "subject": "owner", "admin": True}]}))
        (root / "auth.json").chmod(0o600)
        (root / "worker.token").write_text(secrets.token_hex(32) + "\n")
        (root / "worker.token").chmod(0o600)
        (root / "sources.json").write_text('{"version":1,"sources":[]}')
        origin = self.url("")
        manifest = {"version": 1, "sites": [{"id": "conditional-fixture", "enabled": True,
            "origin": origin, "path_prefixes": ["/docs/"], "review": {
                "status": "approved", "payment_scheme": "no_payment", "automated_access": "approved",
                "checked_at": "2020-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                "terms_url": "https://example.org/fixture-terms", "cache_policy": "allowed_with_limits",
                "redistribution_policy": "allowed_with_limits", "approved_by": "offline-test-fixture"},
            "limits": {"max_concurrency": 1, "min_interval_ms": 0, "request_quota": 1000, "quota_window_seconds": 86400}}]}
        (root / "sites.json").write_text(json.dumps(manifest))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.env = dict(os.environ, PYTHONPATH=str(CONFIG.pydir), PYTHONUNBUFFERED="1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            self.env.pop(key, None)
        self.worker_port = self.spawn("worker", [CONFIG.python, "-m", "bzs.worker.server", "--port", "0",
            "--token-file", str(root / "worker.token"), "--sources", str(root / "sources.json"),
            "--sites", str(root / "sites.json"), "--test-mode", "--allow-cidr", "127.0.0.1/32",
            "--test-host", HOST + "=127.0.0.1"])["port"]
        self.start_core()

    def url(self, path):
        return f"http://{HOST}:{self.web_port}{path}"

    def start_core(self):
        self.core_port = self.spawn("searchd", [CONFIG.server, "--auth", str(self.root / "auth.json"),
            "--state", str(self.root / "state"), "--fixtures", str(CONFIG.fixtures), "--port", "0", "--threads", "4",
            "--worker", f"http://127.0.0.1:{self.worker_port}", "--worker-token-file", str(self.root / "worker.token"),
            "--sources", str(self.root / "sources.json"), "--sites", str(self.root / "sites.json")])["port"]


class Revalidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bzs-conditional-e2e-")
        self.fixture = Origin()
        self.stack = Stack.__new__(Stack)
        self.stack.procs, self.stack.logs = {}, {}
        try:
            self.stack.__init__(Path(self.tmp.name), self.fixture)
        except Exception:
            self.tearDown()
            raise

    def tearDown(self):
        self.stack.stop()
        self.fixture.stop()
        self.tmp.cleanup()

    def ok(self, operation, params=None):
        status, body = self.stack.call(operation, params)
        self.assertEqual(status, 200, body)
        return body["result"]

    def read(self, path="/docs/plain", freshness="refresh"):
        return self.ok("web.read", {"url": self.stack.url(path), "freshness": freshness})

    def wait_job(self, job_id):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            job = self.ok("job.get", {"job_id": job_id})
            if job["state"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def crawl(self, key, path="/docs/root"):
        accepted = self.ok("crawl.submit", {"site_id": "conditional-fixture", "seeds": [self.stack.url(path)],
            "idempotency_key": key, "max_requests": 10, "max_bytes": 1000000, "max_depth": 3})
        return self.wait_job(accepted["job_id"])

    def test_direct_304_preserves_snapshot_across_core_restart(self):
        first = self.read()
        self.stack.kill("searchd")
        self.stack.start_core()
        second = self.read()
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertEqual(second["content"]["text"], first["content"]["text"])
        self.assertEqual(second["fetch"]["http_status"], 304)
        self.assertTrue(second["fetch"]["revalidated"])
        self.assertEqual(len(self.fixture.requests), 2)
        self.assertEqual(self.fixture.requests[-1]["etag"], '"fixture-1"')
        self.assertEqual(self.fixture.requests[-1]["last_modified"], "Tue, 14 Nov 2023 22:13:20 GMT")
        self.read(freshness="cache_only")
        self.assertEqual(len(self.fixture.requests), 2)

    def test_batch_304_refunds_only_unused_body_grant(self):
        first = self.read()
        accepted = self.ok("read_many.submit", {"urls": [self.stack.url("/docs/plain")],
            "idempotency_key": "conditional-batch", "max_requests": 1, "max_bytes": 100000})
        job = self.wait_job(accepted["job_id"])
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["usage"]["external_requests"], 1)
        self.assertEqual(job["usage"]["content_bytes"], 0)
        self.assertEqual(job["budget"]["charged_bytes"], 0)
        results = self.ok("job.results", {"job_id": accepted["job_id"]})
        self.assertEqual(results["items"][0]["receipt"]["snapshot_id"], first["snapshot_id"])
        self.assertEqual(len(self.fixture.requests), 2)

    def test_crawl_revalidates_robots_and_complete_discovery(self):
        first = self.crawl("full")
        self.assertEqual(first["state"], "succeeded", first)
        self.assertEqual(len(self.fixture.requests), 3)
        time.sleep(2.1)  # Real HTTP freshness expires; no clock injection or DB changes.
        second = self.crawl("conditional")
        self.assertEqual(second["state"], "succeeded", second)
        self.assertEqual(second["usage"]["external_requests"], 3)
        self.assertEqual(second["usage"]["content_bytes"], 0)
        tail = self.fixture.requests[3:]
        self.assertEqual([row["path"] for row in tail], ["/robots.txt", "/docs/root", "/docs/child"])
        self.assertTrue(all(row["etag"] == '"fixture-1"' for row in tail))
        self.assertTrue(all(row["user_agent"].startswith("BzsBot/") for row in tail))

    def test_invalid_robots_304_never_dispatches_content(self):
        self.assertEqual(self.crawl("full")["state"], "succeeded")
        time.sleep(2.1)
        self.fixture.bad_robots = True
        failed = self.crawl("invalid-validator")
        self.assertEqual(failed["state"], "failed", failed)
        self.assertEqual([r["path"] for r in self.fixture.requests[3:]], ["/robots.txt"])
        self.assertEqual(failed["usage"]["external_requests"], 1)
        self.assertEqual(failed["usage"]["content_bytes"], 0)

    def test_same_origin_redirect_removes_the_old_resource_validator(self):
        self.read()
        self.fixture.redirect = True
        refreshed = self.read()
        self.assertEqual(refreshed["fetch"]["http_status"], 200)
        self.assertEqual([(r["path"], r["etag"]) for r in self.fixture.requests[1:]],
                         [("/docs/plain", '"fixture-1"'), ("/docs/redirect-target", "")])

    def test_rejected_304_still_applies_new_noindex_observation(self):
        first = self.read()
        self.fixture.bad_page = True
        self.fixture.restrict_304 = "noindex, nosnippet"
        status, _ = self.stack.call("web.read", {"url": self.stack.url("/docs/plain"), "freshness": "refresh"})
        self.assertNotEqual(status, 200)
        cached = self.read(freshness="cache_only")
        self.assertEqual(cached["snapshot_id"], first["snapshot_id"])
        results = self.ok("search", {"query": "conditionalneedle"})
        self.assertEqual(results["frozen_count"], 0, results)
        self.assertEqual(len(self.fixture.requests), 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    CONFIG = parser.parse_args()
    CONFIG.pydir = CONFIG.repo / "python"
    CONFIG.fixtures = CONFIG.repo / "tests" / "fixtures"
    if not e2e_web.importlib.util.find_spec("aiohttp"):
        print("SKIP: worker dependencies missing")
        sys.exit(77)
    e2e_web.CONFIG = CONFIG
    unittest.main(argv=[sys.argv[0]], verbosity=2)
