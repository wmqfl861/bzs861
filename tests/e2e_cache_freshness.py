"""Real HTTP -> Worker -> Core cache directives without origin approval claims."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import e2e_revalidation as er
import e2e_web


class Origin:
    def __init__(self):
        self.requests = []
        self.control = "max-age=60"
        self.age = ""
        self.tag = False
        self.error = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get("If-None-Match", "")))
                robots = self.path == "/robots.txt"
                status = 200 if robots else (503 if fixture.error else
                    304 if fixture.tag and self.headers.get("If-None-Match") == '"fresh"' else 200)
                body = b"User-agent: BzsBot\nAllow: /\n" if robots else b"freshnessneedle owner-scoped evidence body\n" * 10
                self.send_response(status)
                self.send_header("Cache-Control", "max-age=60" if robots else fixture.control)
                if fixture.age and not robots:
                    self.send_header("Age", fixture.age)
                if fixture.tag and not robots:
                    self.send_header("ETag", '"fresh"')
                if status != 304:
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if status != 304:
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class CacheFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bzs-cache-e2e-")
        self.origin = Origin()
        self.stack = er.Stack.__new__(er.Stack)
        self.stack.procs, self.stack.logs = {}, {}
        try:
            self.stack.__init__(Path(self.tmp.name), self.origin)
        except Exception:
            self.tearDown()
            raise

    def tearDown(self):
        self.stack.stop()
        self.origin.stop()
        self.tmp.cleanup()

    def ok(self, op, params=None):
        status, result = self.stack.call(op, params)
        self.assertEqual(status, 200, result)
        return result["result"]

    def read(self, freshness="cache_first"):
        return self.ok("web.read", {"url": self.stack.url("/docs/page"), "freshness": freshness})

    def error(self, code, freshness="cache_first"):
        status, result = self.stack.call("web.read", {"url": self.stack.url("/docs/page"), "freshness": freshness})
        self.assertNotEqual(status, 200, result)
        self.assertEqual(result["errors"][0]["code"], code, result)

    def wait(self, job_id):
        until = time.monotonic() + 12
        while time.monotonic() < until:
            job = self.ok("job.get", {"job_id": job_id})
            if job["state"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        self.fail("bounded job did not finish")

    def test_no_validator_fresh_cache_survives_core_restart(self):
        first = self.read()
        self.assertTrue(self.read()["from_cache"])
        self.stack.kill("searchd")
        self.stack.start_core()
        self.assertEqual(self.read("cache_only")["snapshot_id"], first["snapshot_id"])
        self.assertEqual(len(self.origin.requests), 1)

    def test_no_cache_and_upstream_age_do_not_become_default_seven_days(self):
        for control, age in (("no-cache", ""), ("max-age=0", ""), ("max-age=60", "120")):
            self.origin.control, self.origin.age = control, age
            before = len(self.origin.requests)
            first = self.read("refresh")
            self.assertFalse(first["cache"]["fresh"])
            self.assertFalse(self.read()["from_cache"])
            self.error("E_CACHE_STALE", "cache_only")
            self.assertEqual(len(self.origin.requests), before + 2)

    def test_no_store_denies_new_snapshot_and_invalidates_old_reuse(self):
        self.origin.control = "no-store"
        self.error("E_CACHE_POLICY")
        self.assertEqual(self.ok("stats")["snapshots"], 0)
        self.origin.control = "max-age=60"
        first = self.read()
        self.origin.control = "no-store"
        self.error("E_CACHE_POLICY", "refresh")
        self.error("E_CACHE_STALE", "cache_only")
        self.assertEqual(self.ok("stats")["snapshots"], 1)
        self.assertIn("freshnessneedle", self.ok("document.read", {"snapshot_id": first["snapshot_id"]})["canonical_text"])

    def test_304_no_store_cannot_leave_old_fresh_cache_usable(self):
        self.origin.tag = True
        self.read()
        self.origin.control = "no-store"
        self.error("E_FETCH_HTTP", "refresh")
        self.assertEqual(self.origin.requests[-1][1], '"fresh"')
        self.error("E_CACHE_STALE", "cache_only")
        self.assertEqual(len(self.origin.requests), 2)

    def test_failed_refresh_never_silently_serves_expired_body(self):
        self.origin.control = "max-age=0"
        first = self.read()
        self.origin.error = True
        self.error("E_FETCH_HTTP")
        self.error("E_CACHE_STALE", "cache_only")
        self.assertEqual(len(self.origin.requests), 2)
        self.assertEqual(self.ok("document.read", {"snapshot_id": first["snapshot_id"]})["snapshot_id"], first["snapshot_id"])

    def test_no_store_batch_and_crawl_charge_requests_without_receipts(self):
        self.origin.control = "no-store"
        read = self.ok("read_many.submit", {"urls": [self.stack.url("/docs/page")], "idempotency_key": "cache-batch", "max_requests": 1})
        read_job = self.wait(read["job_id"])
        self.assertEqual(read_job["usage"]["external_requests"], 1)
        self.assertEqual(self.ok("stats")["snapshots"], 0)
        crawl = self.ok("crawl.submit", {"site_id": "conditional-fixture", "seeds": [self.stack.url("/docs/page")], "idempotency_key": "cache-crawl", "max_requests": 3})
        crawl_job = self.wait(crawl["job_id"])
        self.assertEqual(crawl_job["usage"]["external_requests"], 2)
        self.assertEqual(self.ok("stats")["snapshots"], 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--repo", required=True)
    cfg = parser.parse_args()
    cfg.server = str(Path(cfg.server).resolve())
    cfg.pydir = Path(cfg.repo).resolve() / "python"
    cfg.fixtures = Path(cfg.repo).resolve() / "tests" / "fixtures"
    er.CONFIG = e2e_web.CONFIG = cfg
    unittest.main(argv=[sys.argv[0]], verbosity=2)
