"""Real HTTP -> Worker -> Core cache directives without origin approval claims."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
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
        self.redirects = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get("If-None-Match", "")))
                if self.path in fixture.redirects:
                    status, target, body, control = fixture.redirects[self.path]
                    self.send_response(status)
                    self.send_header("Location", target)
                    self.send_header("Cache-Control", control)
                    self.send_header("ETag", '"redirect-only"')
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
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

    def http_chain(self):
        self.origin.tag = True
        self.origin.redirects = {
            "/docs/start": (301, "/docs/middle", b"first jump", "max-age=60"),
            "/docs/middle": (307, "/docs/page", b"second jump", "max-age=60"),
        }
        return self.stack.url("/docs/start")

    def test_http_chain_original_cache_survives_restart_and_refreshes_only_final_validator(self):
        original = self.http_chain()
        first = self.ok("web.read", {"url": original, "freshness": "cache_first"})
        self.assertEqual(self.origin.requests, [("/docs/start", ""), ("/docs/middle", ""), ("/docs/page", "")])
        self.assertEqual(first["cache"]["redirect_hops"], 2)
        for freshness in ("cache_only", "cache_first"):
            hit = self.ok("web.read", {"url": original, "freshness": freshness})
            self.assertTrue(hit["from_cache"])
            self.assertEqual(hit["snapshot_id"], first["snapshot_id"])
        self.assertEqual(len(self.origin.requests), 3)
        self.stack.kill("searchd")
        self.stack.start_core()
        hit = self.ok("web.read", {"url": original, "freshness": "cache_only"})
        self.assertEqual(hit["snapshot_id"], first["snapshot_id"])
        self.assertEqual(len(self.origin.requests), 3)
        refreshed = self.ok("web.read", {"url": original, "freshness": "refresh"})
        self.assertEqual(refreshed["fetch"]["http_status"], 304)
        self.assertTrue(refreshed["fetch"]["revalidated"])
        self.assertEqual(refreshed["snapshot_id"], first["snapshot_id"])
        self.assertEqual(refreshed["content"]["text"], first["content"]["text"])
        self.assertEqual(self.origin.requests[3:], [("/docs/start", ""), ("/docs/middle", ""), ("/docs/page", '"fresh"')])

    def test_http_chain_batch_304_accounts_for_each_request_and_redirect_body(self):
        original = self.http_chain()
        first = self.ok("web.read", {"url": original})
        accepted = self.ok("read_many.submit", {"urls": [original], "idempotency_key": "chain-304",
            "max_requests": 3, "max_bytes": 100000})
        job = self.wait(accepted["job_id"])
        self.assertEqual(job["state"], "succeeded", job)
        redirect_bytes = len(b"first jump") + len(b"second jump")
        self.assertEqual(job["usage"]["external_requests"], 3)
        self.assertEqual(job["usage"]["content_bytes"], redirect_bytes)
        self.assertEqual(job["usage"]["unknown_requests_charged"], 0)
        self.assertEqual(job["usage"]["unknown_bytes_charged"], 0)
        self.assertEqual(job["usage"]["reserved_requests"], 0)
        self.assertEqual(job["usage"]["reserved_bytes"], 0)
        self.assertEqual(job["budget"]["charged_requests"], 3)
        self.assertEqual(job["budget"]["charged_bytes"], redirect_bytes)
        results = self.ok("job.results", {"job_id": accepted["job_id"]})
        self.assertEqual(results["items"][0]["receipt"]["snapshot_id"], first["snapshot_id"])
        retained = self.ok("document.read", {"snapshot_id": first["snapshot_id"]})
        self.assertEqual(retained["canonical_text"], first["content"]["text"])
        self.assertEqual(self.origin.requests[3:], [("/docs/start", ""), ("/docs/middle", ""), ("/docs/page", '"fresh"')])

    def test_http_chain_cache_is_owner_scoped_without_a_global_final_alias(self):
        original = self.http_chain()
        first = self.ok("web.read", {"url": original})
        status, result = self.stack.call("web.read", {"url": self.stack.url("/docs/page"), "freshness": "cache_only"})
        self.assertNotEqual(status, 200, result)
        self.assertEqual(result["errors"][0]["code"], "E_NOT_CACHED", result)
        other_token = secrets.token_hex(32)
        auth_path = self.stack.root / "auth.json"
        auth = json.loads(auth_path.read_text())
        auth["tokens"].append({"sha256": hashlib.sha256(other_token.encode()).hexdigest(), "subject": "other-owner", "admin": False})
        auth_path.write_text(json.dumps(auth))
        self.stack.kill("searchd")
        self.stack.start_core()
        owner_token = self.stack.api_token
        try:
            self.stack.api_token = other_token
            status, result = self.stack.call("web.read", {"url": original, "freshness": "cache_only"})
            self.assertNotEqual(status, 200, result)
            self.assertEqual(result["errors"][0]["code"], "E_NOT_CACHED", result)
            self.assertEqual(len(self.origin.requests), 3)
            other = self.ok("web.read", {"url": original, "freshness": "cache_first"})
            self.assertFalse(other["from_cache"])
            self.assertNotEqual(other["snapshot_id"], first["snapshot_id"])
            self.assertEqual(self.origin.requests[3:], [("/docs/start", ""), ("/docs/middle", ""), ("/docs/page", "")])
        finally:
            self.stack.api_token = owner_token
        self.assertEqual(self.ok("web.read", {"url": original, "freshness": "cache_only"})["snapshot_id"], first["snapshot_id"])
        self.assertEqual(len(self.origin.requests), 6)

    def test_failed_new_http_route_invalidates_the_old_direct_cache(self):
        original = self.stack.url("/docs/start")
        first = self.ok("web.read", {"url": original})
        self.origin.redirects["/docs/start"] = (301, "/docs/page", b"new route", "max-age=60")
        self.origin.error = True
        status, result = self.stack.call("web.read", {"url": original, "freshness": "refresh"})
        self.assertNotEqual(status, 200, result)
        self.assertEqual(result["errors"][0]["code"], "E_FETCH_HTTP", result)
        status, result = self.stack.call("web.read", {"url": original, "freshness": "cache_only"})
        self.assertNotEqual(status, 200, result)
        self.assertEqual(result["errors"][0]["code"], "E_CACHE_STALE", result)
        self.assertEqual([request[0] for request in self.origin.requests], ["/docs/start", "/docs/start", "/docs/page"])
        self.assertEqual(self.ok("stats")["snapshots"], 1)
        self.assertIn("freshnessneedle", self.ok("document.read", {"snapshot_id": first["snapshot_id"]})["canonical_text"])


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
