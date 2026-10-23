"""Actual fixture HTTP, Worker, Core and administrative revocation races.

The fixture publishes its observed request count before releasing a blocked
response. This proves revocation occurs after dispatch, without timing sleeps.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import e2e_crawl
import e2e_web


class Stack(e2e_crawl.Stack):
    def call_as(self, operation, params=None, *, other=False):
        body = json.dumps({"operation": operation, "params": params or {}}).encode()
        token = self.other_token if other else self.api_token
        request = urllib.request.Request(f"http://127.0.0.1:{self.core_port}/api/v0/call", body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + token},
                                         method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def rows(self, query, values=()):
        path = self.root / "state" / "metadata.sqlite"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, values)]


class RevocationEndToEnd(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bzs-revocation-e2e-")
        self.stack = Stack.__new__(Stack)
        self.stack.procs, self.stack.logs = {}, {}
        try:
            self.stack.__init__(Path(self.temp.name))
        except Exception:
            self.stack.stop()
            self.temp.cleanup()
            raise

    def tearDown(self):
        self.stack.stop()
        self.temp.cleanup()

    def ok(self, operation, params=None, **options):
        code, response = self.stack.call_as(operation, params, **options)
        self.assertEqual(code, 200, response)
        return response["result"]

    def until(self, predicate, description):
        deadline = time.monotonic() + 20
        while True:
            result = predicate()
            if result:
                return result
            self.assertLess(time.monotonic(), deadline, description)
            time.sleep(0.02)

    def wait_for_request(self, path):
        self.until(lambda: self.stack.counts().get(("bzs-crawl.test", path)) == 1,
                   "fixture did not observe the request")

    def revoke(self):
        result = self.ok("principal.revoke", {"subject": "owner"}, other=True)
        self.assertTrue(result["revoked"])
        self.assertEqual(result["auth_epoch"], 1)
        return result

    def denied(self, operation, params=None):
        code, response = self.stack.call_as(operation, params)
        self.assertEqual(code, 403, response)
        self.assertEqual(response["errors"][0]["code"], "E_PRINCIPAL_REVOKED")
        self.assertNotIn("result", response)

    def wait_cancelled(self, job):
        self.until(lambda: self.stack.rows("SELECT state FROM jobs WHERE id=?", (job,)) == [{"state": "cancelled"}],
                   "revoked in-flight job did not settle")

    def assert_no_body_and_restart(self, receipt):
        self.assertEqual(self.stack.rows("SELECT COUNT(*) AS count FROM snapshots"), [{"count": 0}])
        counts = self.stack.counts()
        self.stack.kill("searchd")
        self.stack.start_core()
        self.denied("stats")
        again = self.ok("principal.revoke", {"subject": "owner"}, other=True)
        self.assertTrue(again["already_revoked"])
        self.assertEqual(again["revocation_id"], receipt["revocation_id"])
        self.assertEqual(again["auth_epoch"], receipt["auth_epoch"])
        self.assertEqual(self.stack.counts(), counts)
        self.assertEqual(self.ok("stats", other=True)["documents"], 0)

    def test_batch_revocation_after_dispatch_preserves_known_charge_without_publication(self):
        name = "revoked-batch"
        path = "/crawl/gate/" + name
        self.stack.control_fixture(close_gates=[name])
        job = self.ok("read_many.submit", {"urls": [self.stack.url(path), self.stack.url("/crawl/unread-tail")],
            "idempotency_key": "revoke-batch", "max_requests": 4, "max_bytes": 32768,
            "per_page_timeout_ms": 15000})["job_id"]
        self.wait_for_request(path)
        receipt = self.revoke()
        self.denied("job.results", {"job_id": job})
        self.stack.control_fixture(release_gates=[name])
        self.wait_cancelled(job)
        self.assertEqual(self.stack.rows("SELECT charged_requests FROM read_job_budgets WHERE job_id=?", (job,)),
                         [{"charged_requests": 1}])
        self.assertEqual(self.stack.rows("SELECT SUM(external_requests) AS requests,SUM(unknown_requests) AS unknown FROM read_job_items WHERE job_id=?", (job,)),
                         [{"requests": 1, "unknown": 0}])
        self.assertEqual(self.stack.counts().get(("bzs-crawl.test", "/crawl/unread-tail"), 0), 0)
        self.assert_no_body_and_restart(receipt)

    def test_crawl_revocation_after_page_dispatch_stops_frontier_and_late_snapshot(self):
        name = "revoked-crawl"
        path = "/crawl/gate/" + name
        self.stack.control_fixture(close_gates=[name])
        job = self.ok("crawl.submit", {"site_id": "bzs-crawl", "seeds": [self.stack.url(path), self.stack.url("/crawl/unread-tail")],
            "idempotency_key": "revoke-crawl", "max_requests": 8, "max_bytes": 32768,
            "max_pages": 2, "max_depth": 0, "per_request_timeout_ms": 15000})["job_id"]
        self.wait_for_request(path)
        receipt = self.revoke()
        self.stack.control_fixture(release_gates=[name])
        self.wait_cancelled(job)
        self.assertEqual(self.stack.rows("SELECT charged_requests,external_requests,unknown_requests FROM map_jobs WHERE job_id=?", (job,)),
                         [{"charged_requests": 2, "external_requests": 2, "unknown_requests": 0}])
        self.assertEqual(self.stack.counts().get(("bzs-crawl.test", "/crawl/unread-tail"), 0), 0)
        self.denied("crawl.edges", {"job_id": job})
        self.assert_no_body_and_restart(receipt)

    def test_direct_read_late_response_is_denied_after_real_worker_fetch(self):
        name = "revoked-direct"
        path = "/crawl/gate/" + name
        self.stack.control_fixture(close_gates=[name])
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.stack.call_as, "web.read", {"url": self.stack.url(path), "freshness": "refresh"})
            self.wait_for_request(path)
            receipt = self.revoke()
            self.stack.control_fixture(release_gates=[name])
            code, response = pending.result(timeout=20)
        self.assertEqual(code, 403, response)
        self.assertEqual(response["errors"][0]["code"], "E_PRINCIPAL_REVOKED")
        self.assertNotIn("result", response)
        self.assert_no_body_and_restart(receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--repo", required=True)
    args, rest = parser.parse_known_args()
    for dependency in ("aiohttp", "mcp", "trafilatura", "lxml"):
        if importlib.util.find_spec(dependency) is None:
            print("SKIP: worker dependencies missing")
            raise SystemExit(77)
    args.pydir = Path(args.repo) / "python"
    args.fixtures = Path(args.repo) / "tests" / "fixtures"
    e2e_crawl.CONFIG = e2e_web.CONFIG = args
    unittest.main(argv=[__file__, *rest], verbosity=2)
