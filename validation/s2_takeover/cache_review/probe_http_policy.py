"""Independent real-HTTP regressions for cache policy paths (review, 2026-10-23)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
import e2e_revalidation as er
import e2e_web


class Origin:
    def __init__(self):
        self.requests = []
        self.control = "max-age=60"
        self.status = 200
        self.etag = False
        self.body = b"Independent review secret body whose storage is forbidden by no-store.\n" * 12
        self.mime = "text/plain"
        self.pragma = []
        self.extra_headers = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                robots = self.path == "/robots.txt"
                conditional = self.headers.get("If-None-Match", "")
                fixture.requests.append({"path": self.path, "etag": conditional})
                status = 200 if robots else 304 if fixture.etag and conditional == '"review"' else fixture.status
                body = b"User-agent: BzsBot\nAllow: /\n" if robots else fixture.body
                self.send_response(status)
                if robots or fixture.control is not None:
                    self.send_header("Cache-Control", "max-age=60" if robots else fixture.control)
                if fixture.etag and not robots:
                    self.send_header("ETag", '"review"')
                for item in fixture.pragma:
                    if not robots:
                        self.send_header("Pragma", item)
                for name, value in fixture.extra_headers:
                    if not robots:
                        self.send_header(name, value)
                if status != 304:
                    self.send_header("Content-Type", ("text/plain" if robots else fixture.mime) + "; charset=utf-8")
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


class ReviewPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bzs-cache-review-")
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

    def ok(self, operation, params=None):
        status, result = self.stack.call(operation, params)
        self.assertEqual(status, 200, result)
        return result["result"]

    def read(self, freshness="refresh"):
        return self.stack.call("web.read", {"url": self.stack.url("/docs/page"), "freshness": freshness})

    def wait(self, job_id):
        until = time.monotonic() + 12
        while time.monotonic() < until:
            job = self.ok("job.get", {"job_id": job_id})
            if job["state"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def crawl(self, key):
        accepted = self.ok("crawl.submit", {"site_id": "conditional-fixture", "seeds": [self.stack.url("/docs/page")], "idempotency_key": key, "max_requests": 3})
        return self.wait(accepted["job_id"])

    def test_203_no_store_direct_cannot_persist(self):
        self.origin.status, self.origin.control = 203, "no-store"
        status, result = self.read()
        count = self.ok("stats")["snapshots"]
        self.assertEqual(count, 0, {"status": status, "snapshots": count, "response": result})

    def test_203_no_store_batch_cannot_persist(self):
        self.origin.status, self.origin.control = 203, "no-store"
        accepted = self.ok("read_many.submit", {"urls": [self.stack.url("/docs/page")], "idempotency_key": "review-203", "max_requests": 1})
        job = self.wait(accepted["job_id"])
        self.assertEqual(job["usage"]["external_requests"], 1)
        self.assertEqual(self.ok("stats")["snapshots"], 0, job)

    def test_203_no_store_crawl_cannot_persist(self):
        self.origin.status, self.origin.control = 203, "no-store"
        job = self.crawl("review-203")
        self.assertEqual(job["usage"]["external_requests"], 2)
        self.assertEqual(self.ok("stats")["snapshots"], 0, job)

    def test_empty_200_no_store_crawl_cannot_persist(self):
        self.origin.control, self.origin.mime = "no-store", "text/html"
        self.origin.body = b"<html><head><title>Private empty page</title></head><body><script>secret=123</script></body></html>"
        job = self.crawl("review-empty")
        self.assertEqual(job["usage"]["external_requests"], 2)
        self.assertEqual(self.ok("stats")["snapshots"], 0, job)

    def test_rejected_304_no_cache_invalidates_previous_freshness(self):
        self.origin.etag = True
        self.assertEqual(self.read()[0], 200)
        self.origin.control = "no-cache"
        status, result = self.read()
        self.assertNotEqual(status, 200, result)  # Current protocol rejects this validation.
        status, result = self.read("cache_only")
        self.assertNotEqual(status, 200, result)

    def test_rejected_304_zero_age_invalidates_previous_freshness(self):
        self.origin.etag = True
        self.assertEqual(self.read()[0], 200)
        self.origin.control = "max-age=0"
        status, result = self.read()
        self.assertNotEqual(status, 200, result)
        status, result = self.read("cache_only")
        self.assertNotEqual(status, 200, result)

    def test_repeated_pragma_keeps_no_cache(self):
        self.origin.control, self.origin.pragma = None, ["no-cache", "extension"]
        self.assertEqual(self.read()[0], 200)
        status, result = self.read("cache_only")
        self.assertNotEqual(status, 200, result)

    def test_truncated_200_keeps_nonpublishing_prefix_but_cannot_be_fresh(self):
        self.origin.body = b"Readable owner evidence.\n" * (8 * 1024 * 1024 // 25 + 100)
        status, result = self.read()
        self.assertEqual(status, 200, result)
        self.assertEqual(self.ok("stats")["snapshots"], 1)
        self.assertFalse(result["result"]["indexed"])
        self.assertIn("download_truncated", result["result"]["fetch"]["warnings"])
        self.assertFalse(result["result"]["cache"]["fresh"])
        self.assertNotEqual(self.read("cache_only")[0], 200)

    def test_repeated_date_never_becomes_fresh(self):
        # send_response emits the first Date. This second value must not replace it.
        self.origin.extra_headers = [("Date", "Tue, 14 Nov 2023 22:13:20 GMT")]
        status, _ = self.read()
        if status == 200:
            self.assertNotEqual(self.read("cache_only")[0], 200)
        self.assertEqual(len(self.origin.requests), 1)

    def test_repeated_age_never_uses_only_last_zero(self):
        self.origin.extra_headers = [("Age", "120"), ("Age", "0")]
        status, _ = self.read()
        if status == 200:
            self.assertNotEqual(self.read("cache_only")[0], 200)
        self.assertEqual(len(self.origin.requests), 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--repo", type=Path, default=ROOT)
    cfg = parser.parse_args()
    cfg.server = str(Path(cfg.server).resolve())
    cfg.pydir, cfg.fixtures = cfg.repo / "python", cfg.repo / "tests" / "fixtures"
    if not e2e_web.importlib.util.find_spec("aiohttp"):
        print("SKIP: worker dependencies missing")
        sys.exit(77)
    er.CONFIG = e2e_web.CONFIG = cfg
    unittest.main(argv=[sys.argv[0]], verbosity=2)
