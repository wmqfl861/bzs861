"""Real core-process crashes at observable RPC boundaries.

The proxies are transparent loopback test fixtures. They can discard a request
before forwarding, or discard a complete response before the caller receives it.
The test sends SIGKILL to the core on POSIX, never pauses a job, never edits the
database, and waits for the actual persisted lease deadline after restarting.
This covers process/RPC failures, not physical power loss or every SQLite write.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

import e2e_crawl
import e2e_web

CONFIG = None


@dataclass
class Gate:
    operation: str
    stage: str
    url: str | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    released: threading.Event = field(default_factory=threading.Event)
    claimed: bool = False
    discarded: bool = False
    request: dict | None = None
    response: dict | None = None
    forwarded: bool = False

    def discard(self):
        self.discarded = True
        self.released.set()

    def wait(self):
        self.entered.set()
        # A failed test must not leave a fixture thread holding an RPC forever.
        return self.released.wait(20) and not self.discarded


class GatedProxy:
    """Forward ordinary HTTP bytes; gate a single matching request or response."""

    def __init__(self, target_port: int):
        self.target_port = target_port
        self.gates: list[Gate] = []
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.close_connection = True
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                request = json.loads(body)
                operation = request.get("operation", request.get("method"))
                gate = None
                with proxy.lock:
                    for candidate in proxy.gates:
                        if (not candidate.claimed and candidate.operation == operation and
                                (candidate.url is None or
                                 candidate.url == request.get("params", {}).get("url"))):
                            candidate.claimed = True
                            candidate.request = request
                            gate = candidate
                            break
                if gate and gate.stage == "before" and not gate.wait():
                    # In particular, cleanup must never forward a discarded
                    # pre-worker request after the core has died.
                    return
                connection = http.client.HTTPConnection("127.0.0.1", proxy.target_port, timeout=15)
                try:
                    headers = {"Content-Type": "application/json", "Connection": "close"}
                    if "Authorization" in self.headers:
                        headers["Authorization"] = self.headers["Authorization"]
                    if gate:
                        gate.forwarded = True
                    connection.request("POST", self.path, body, headers)
                    response = connection.getresponse()
                    output = response.read(24 * 1024 * 1024 + 1)
                    if len(output) > 24 * 1024 * 1024:
                        raise RuntimeError("Fixture proxy response exceeded the worker RPC bound")
                    if gate and gate.stage == "after":
                        gate.response = json.loads(output)
                        if not gate.wait():
                            return
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(output)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(output)
                except (BrokenPipeError, ConnectionError, TimeoutError, http.client.HTTPException):
                    # Expected if the core was killed while this RPC was held.
                    return
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02),
                                       daemon=True)
        self.thread.start()

    def add(self, operation: str, stage: str, url: str | None = None) -> Gate:
        gate = Gate(operation, stage, url)
        with self.lock:
            self.gates.append(gate)
        return gate

    def close(self):
        with self.lock:
            for gate in self.gates:
                gate.discard()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class RecoveryStack(e2e_crawl.Stack):
    def start_core(self, no_worker=False, offline=False):
        if not hasattr(self, "rpc_proxy"):
            self.rpc_proxy = GatedProxy(self.worker_port)
        old = self.logs.get("searchd")
        if old:
            old.close()
        command = [CONFIG.server, "--auth", str(self.root / "auth.json"),
                   "--state", str(self.root / "state"), "--fixtures", str(CONFIG.fixtures),
                   "--port", "0", "--threads", "4", "--sites", str(self.root / "sites.json")]
        if not offline:
            command.extend(["--worker", f"http://127.0.0.1:{self.rpc_proxy.port}",
                            "--worker-token-file", str(self.root / "worker.token"),
                            "--sources", str(self.root / "sources.json")])
        if no_worker:
            command.append("--no-worker")
        self.core_port = self.spawn("searchd", command)["port"]

    def crash_core(self):
        process = self.procs["searchd"]
        if process.poll() is not None:
            raise AssertionError("Core exited before the crash checkpoint")
        process.kill()
        code = process.wait(timeout=5)
        if sys.platform != "win32" and code != -signal.SIGKILL:
            raise AssertionError(f"Expected SIGKILL, got {code}")
        if process.stdout:
            process.stdout.close()
        return code

    def rows(self, query: str, values=()):
        # Observer connection only: no lease edits, clock injection or repairs.
        path = self.root / "state" / "metadata.sqlite"
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, values).fetchall()]
        finally:
            connection.close()

    def stop(self):
        if hasattr(self, "rpc_proxy"):
            self.rpc_proxy.close()
        super().stop()


class CrawlRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bzs-crawl-crash-")
        self.stack = RecoveryStack.__new__(RecoveryStack)
        self.stack.procs, self.stack.logs = {}, {}
        try:
            self.stack.__init__(Path(self.tmp.name))
        except Exception:
            self.stack.stop()
            self.tmp.cleanup()
            raise

    def tearDown(self):
        self.stack.stop()
        self.tmp.cleanup()

    def ok(self, operation, params=None):
        status, response = self.stack.call(operation, params)
        self.assertEqual(status, 200, response)
        return response["result"]

    def wait_until(self, predicate, description, seconds=20):
        deadline = time.monotonic() + seconds
        while True:
            result = predicate()
            if result:
                return result
            self.assertLess(time.monotonic(), deadline, description)
            time.sleep(0.025)

    def completed(self, job_id):
        return self.wait_until(
            lambda: (job if (job := self.ok("job.get", {"job_id": job_id}))["state"] in
                     ("succeeded", "partial", "failed", "cancelled") else None),
            "crashed job did not settle after its real lease expired")

    def parameters(self, paths, key, **options):
        return {"site_id": "bzs-crawl", "seeds": [self.stack.url(path) for path in paths],
                "idempotency_key": key, "max_requests": 40, "max_bytes": 32 * 1024 * 1024,
                "max_pages": 20, "max_urls": 40, "max_depth": 0,
                "per_request_timeout_ms": 1000, **options}

    def results(self, job_id):
        return self.ok("job.results", {"job_id": job_id, "limit": 100})["items"]

    def query(self, text="frontierfixtureunique"):
        return self.ok("search", {"query": text, "limit": 100, "page_size": 100})

    def assert_accounting(self, job, grant):
        usage, budget = job["usage"], job["budget"]
        self.assertEqual(usage["unknown_requests_charged"], grant["request_grant"], job)
        self.assertEqual(usage["unknown_bytes_charged"], grant["byte_grant"], job)
        self.assertEqual(usage["reserved_requests"], 0, job)
        self.assertEqual(usage["reserved_bytes"], 0, job)
        self.assertEqual(budget["charged_requests"],
                         usage["external_requests"] + usage["unknown_requests_charged"], job)
        self.assertEqual(budget["charged_bytes"],
                         usage["content_bytes"] + usage["unknown_bytes_charged"], job)
        hop = self.stack.rows("SELECT * FROM map_hops WHERE attempt_id=?", (grant["attempt_id"],))
        self.assertEqual(len(hop), 1)
        self.assertEqual(hop[0]["state"], "failed")
        self.assertEqual(hop[0]["error_code"], "E_EXECUTION_UNKNOWN")
        self.assertEqual(hop[0]["usage_unknown"], 1)
        self.assertIsNone(hop[0]["response_json"])
        site = self.stack.rows("SELECT * FROM site_request_state WHERE origin=?", (job["origin"],))[0]
        self.assertEqual(site["active_attempt"], "")
        self.assertEqual(site["active_until"], 0)
        charged = self.stack.rows("SELECT SUM(charged_requests) AS n FROM map_jobs WHERE origin=?",
                                  (job["origin"],))[0]["n"]
        self.assertEqual(site["requests"], charged)

    def lost_hop(self, stage, delete_existing=False):
        prefix = f"/crawl/crash-{stage}" + ("-delete" if delete_existing else "")
        root, lost, tail = (prefix + suffix for suffix in ("-root", "-lost", "-tail"))
        lost_url = self.stack.url(lost)
        existing = None
        if delete_existing:
            warm = self.ok("crawl.submit", self.parameters([tail], "warm-target"))
            self.assertEqual(self.completed(warm["job_id"])["state"], "succeeded")
            existing = self.results(warm["job_id"])[0]
        gate = self.stack.rpc_proxy.add("map.fetch", stage, lost_url)
        params = self.parameters([root, lost, tail], "lost-hop-" + stage)
        manifest = (self.stack.root / "sites.json").read_bytes()
        submitted = self.ok("crawl.submit", params)
        job_id = submitted["job_id"]
        self.assertTrue(gate.entered.wait(10), "RPC did not reach the requested fault boundary")
        current = self.ok("job.get", {"job_id": job_id})
        self.assertEqual(current["state"], "running", current)
        grant = self.stack.rows(
            "SELECT h.*,j.lease_until FROM map_hops h JOIN jobs j ON j.id=h.job_id "
            "WHERE h.job_id=? AND h.state='running'", (job_id,))[0]
        self.assertEqual(grant["url"], lost_url)
        self.assertEqual(grant["attempt_id"], gate.request["params"]["attempt_id"])
        self.assertGreater(grant["lease_until"], int(time.time() * 1000))
        self.assertGreater(grant["byte_grant"], 0)
        self.assertGreater(current["budget"]["remaining_bytes"], grant["byte_grant"])
        before = self.stack.counts()
        actual_lost = 1 if stage == "after" else 0
        self.assertEqual(before.get(("bzs-crawl.test", lost), 0), actual_lost)
        self.assertEqual(gate.forwarded, stage == "after")
        if stage == "after":
            # The complete artifact exists in the worker's response, but has
            # not crossed the core RPC boundary and is not core evidence.
            self.assertEqual(gate.response["result"]["status"], "ok")
            self.assertEqual(gate.response["result"]["page"]["final_url"], lost_url)
            self.assertEqual(gate.response["result"]["usage"]["external_requests"], 1)
        rows = {row["url"]: row for row in self.results(job_id)}
        committed = rows[self.stack.url(root)]
        self.assertTrue(committed["body_read"])
        self.assertFalse(rows[lost_url]["body_read"])
        self.assertFalse(rows[self.stack.url(tail)]["body_read"])
        frozen = self.query()
        evidence = self.ok("evidence.create", {"snapshot_id": committed["snapshot_id"],
                                               "start_byte": 0, "end_byte": 5})
        # Confirm the lease is still live after the observer API calls.
        self.assertEqual(self.ok("job.get", {"job_id": job_id})["state"], "running")
        self.assertGreater(grant["lease_until"], int(time.time() * 1000))
        exit_code = self.stack.crash_core()
        gate.discard()
        self.assertEqual(self.stack.counts(), before)
        self.stack.start_core(no_worker=delete_existing)
        self.assertEqual((self.stack.root / "sites.json").read_bytes(), manifest)
        if existing:
            self.assertTrue(self.ok("document.delete", {"document_id": existing["document_id"]})["deleted"])
            self.stack.crash_core()
            self.stack.start_core()
        final = self.completed(job_id)
        self.assertGreaterEqual(int(time.time() * 1000), grant["lease_until"])
        # Job terminal state and partial coverage are separate API fields.
        # A failed physical hop makes the terminal job failed while the
        # successfully committed pages remain available.
        self.assertEqual(final["state"], "failed", final)
        self.assertTrue(final["partial"], final)
        self.assertEqual(final["stop_reason"], "E_EXECUTION_UNKNOWN", final)
        self.assert_accounting(final, grant)
        self.assertEqual(final["used_pages"], 2 if existing else 3, final)
        self.assertEqual(final["succeeded_pages"], 1 if existing else 2, final)
        self.assertEqual(final["failed_pages"], 2 if existing else 1, final)
        after_rows = {row["url"]: row for row in self.results(job_id)}
        self.assertEqual(after_rows[self.stack.url(root)]["snapshot_id"], committed["snapshot_id"])
        if existing:
            deleted_tail = after_rows[self.stack.url(tail)]
            self.assertFalse(deleted_tail["body_read"])
            self.assertEqual(deleted_tail["error_code"], "E_TARGET_DELETED")
            self.assertEqual(self.stack.rows("SELECT COUNT(*) AS n FROM map_hops WHERE job_id=? AND url=?",
                                             (job_id, self.stack.url(tail)))[0]["n"], 0)
            self.assertEqual(self.stack.rows("SELECT COUNT(*) AS n FROM snapshots WHERE document_id=?",
                                             (existing["document_id"],))[0]["n"], 0)
        else:
            self.assertTrue(after_rows[self.stack.url(tail)]["body_read"])
        self.assertEqual(after_rows[lost_url]["state"], "failed")
        self.assertEqual(after_rows[lost_url]["error_code"], "E_EXECUTION_UNKNOWN")
        self.assertFalse(after_rows[lost_url]["body_read"])
        self.assertFalse(after_rows[lost_url]["available"])
        self.assertIsNone(after_rows[lost_url]["snapshot_id"])
        count = self.stack.rows(
            "SELECT COUNT(*) AS n FROM snapshots s JOIN documents d ON d.id=s.document_id "
            "WHERE d.owner='owner' AND d.url=?", (lost_url,))[0]["n"]
        self.assertEqual(count, 0)
        self.assertNotIn(lost_url, [hit["url"] for hit in self.query()["page"]["hits"]])
        # Root evidence and the unrelated frozen result survive this crash.
        self.assertEqual(self.ok("evidence.read", {"evidence_id": evidence["evidence_id"]})["quote"],
                         evidence["quote"])
        old_hits = self.ok("search.page", {"session_id": frozen["session_id"], "page_size": 100})["hits"]
        self.assertIn(committed["snapshot_id"], [row["snapshot_id"] for row in old_hits])
        counts = self.stack.counts()
        self.assertEqual(counts[("bzs-crawl.test", root)], 1)
        self.assertEqual(counts.get(("bzs-crawl.test", lost), 0), actual_lost)
        self.assertEqual(counts[("bzs-crawl.test", tail)], 1)
        reused = self.ok("crawl.submit", params)
        self.assertEqual(reused["job_id"], job_id)
        self.assertTrue(reused["reused"])
        self.assertEqual(self.stack.counts(), counts)
        print(json.dumps({"checkpoint": stage + "_worker_rpc", "deleted_pending_tail": bool(existing),
                          "core_exit_code": exit_code, "attempt_id": grant["attempt_id"],
                          "request_grant": grant["request_grant"], "byte_grant": grant["byte_grant"],
                          "fixture_lost_url_requests": actual_lost, "state": final["state"],
                          "unknown_requests_charged": final["usage"]["unknown_requests_charged"],
                          "unknown_bytes_charged": final["usage"]["unknown_bytes_charged"],
                          "used_pages": final["used_pages"], "reused_job_id": reused["job_id"]}))

    def test_01_crash_before_worker_dispatch_preserves_unknown_grant_without_sending_late_request(self):
        self.lost_hop("before")

    def test_02_crash_after_worker_response_never_publishes_or_repeats_the_lost_artifact(self):
        self.lost_hop("after")

    def test_03_deleting_the_pending_tail_during_recovery_blocks_its_first_dispatch(self):
        self.lost_hop("after", delete_existing=True)

    def test_04_lost_submit_ack_after_commit_reuses_job_snapshots_edges_and_usage(self):
        proxy = GatedProxy(self.stack.core_port)
        gate = proxy.add("crawl.submit", "after")
        pool = ThreadPoolExecutor(max_workers=1)
        params = self.parameters(["/crawl/root"], "lost-accepted-ack", max_depth=4)

        def submit_without_ack():
            body = json.dumps({"operation": "crawl.submit", "params": params}).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{proxy.port}/api/v0/call", body,
                {"Content-Type": "application/json", "Authorization": "Bearer " + self.stack.api_token},
                method="POST")
            with self.stack.opener.open(request, timeout=20) as response:
                return json.load(response)

        try:
            caller = pool.submit(submit_without_ack)
            self.assertTrue(gate.entered.wait(10), "Submit response was not captured")
            self.assertFalse(caller.done(), "Caller already received the accepted ACK")
            job_id = gate.response["result"]["job_id"]
            final = self.completed(job_id)
            self.assertEqual(final["state"], "succeeded", final)
            rows = self.results(job_id)
            self.assertEqual(len(rows), 5, rows)
            self.assertTrue(all(row["body_read"] and row["receipt"] for row in rows))
            edges = self.ok("crawl.edges", {"job_id": job_id, "limit": 100})["items"]
            self.assertGreater(len(edges), 0)
            current = self.query()
            self.assertEqual(len(current["page"]["hits"]), 5)
            evidence = self.ok("evidence.create", {"snapshot_id": rows[0]["snapshot_id"],
                                                   "start_byte": 0, "end_byte": 5})
            counts = self.stack.counts()
            self.assertFalse(caller.done(), "Caller received the ACK before the crash")
            exit_code = self.stack.crash_core()
            gate.discard()
            # The actual waiting HTTP caller receives a transport failure,
            # rather than the accepted response retained only by the fixture.
            with self.assertRaises((OSError, http.client.HTTPException)):
                caller.result(timeout=5)
            self.stack.start_core()
            reused = self.ok("crawl.submit", params)
            self.assertEqual(reused["job_id"], job_id)
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["state"], "succeeded")
            recovered = self.results(job_id)
            self.assertEqual({row["url"]: row["snapshot_id"] for row in recovered},
                             {row["url"]: row["snapshot_id"] for row in rows})
            self.assertEqual(self.ok("crawl.edges", {"job_id": job_id, "limit": 100})["items"], edges)
            self.assertEqual(reused["usage"], final["usage"])
            self.assertEqual(reused["budget"], final["budget"])
            self.assertEqual(self.stack.counts(), counts)
            self.assertEqual(self.ok("evidence.read", {"evidence_id": evidence["evidence_id"]})["quote"],
                             evidence["quote"])
            self.assertEqual(len(self.ok("search.page", {"session_id": current["session_id"],
                                                         "page_size": 100})["hits"]), 5)
            print(json.dumps({"checkpoint": "committed_job_before_submit_ack", "core_exit_code": exit_code,
                              "reused_job_id": job_id, "snapshots": len(rows), "edges": len(edges),
                              "extra_http_requests_after_retry": 0, "state": reused["state"]}))
        finally:
            gate.discard()
            proxy.close()
            pool.shutdown(wait=True)

    def policy_on_offline_restart(self, mode):
        submitted = self.ok("crawl.submit", self.parameters(["/crawl/root"], "policy-startup"))
        self.assertEqual(self.completed(submitted["job_id"])["state"], "succeeded")
        original = self.results(submitted["job_id"])[0]
        self.assertTrue(original["body_read"])
        frozen = self.query()
        self.assertEqual(len(frozen["page"]["hits"]), 1)
        self.assertTrue(frozen["page"]["hits"][0]["snippet"])
        evidence = self.ok("evidence.create", {"snapshot_id": original["snapshot_id"],
                                               "start_byte": 0, "end_byte": 5})
        path = self.stack.root / "sites.json"
        config = json.loads(path.read_text())
        if mode == "empty":
            config["sites"] = []
        else:
            site = next(row for row in config["sites"] if row["id"] == "bzs-crawl")
            if mode == "links_only":
                site["review"]["redistribution_policy"] = "links_only"
            elif mode == "expired":
                site["review"]["expires_at"] = "2020-01-01T00:00:00Z"
            else:
                site["review"]["status"] = "rejected"
        counts = self.stack.counts()
        path.write_text(json.dumps(config), encoding="utf-8")
        self.stack.crash_core()
        # Deliberately omit --worker: retained publication is still governed
        # by the explicitly supplied --sites configuration.
        self.stack.start_core(offline=True)
        self.assertEqual(self.ok("capabilities")["external_network"], "disabled")
        for page in (self.query()["page"],
                     self.ok("search.page", {"session_id": frozen["session_id"], "page_size": 100})):
            if mode == "links_only":
                self.assertEqual(len(page["hits"]), 1, page)
                self.assertEqual(page["hits"][0]["snippet"], "")
                self.assertEqual(page["hits"][0]["title"], original["url"])
                self.assertEqual(page["hits"][0]["highlights"], [])
            else:
                self.assertEqual(page["hits"], [])
        self.assertEqual(self.stack.counts(), counts)
        self.assertIn("frontierfixtureunique", self.ok("document.range", {
            "snapshot_id": original["snapshot_id"], "max_bytes": 4000})["text"])
        self.assertEqual(self.ok("evidence.read", {"evidence_id": evidence["evidence_id"]})["quote"],
                         evidence["quote"])

    def test_05_links_only_applies_to_stored_results_after_explicit_offline_restart(self):
        self.policy_on_offline_restart("links_only")

    def test_06_rejected_review_applies_to_stored_results_after_explicit_offline_restart(self):
        self.policy_on_offline_restart("rejected")

    def test_07_expired_review_applies_to_stored_results_after_explicit_offline_restart(self):
        self.policy_on_offline_restart("expired")

    def test_08_explicit_empty_registry_withdraws_previous_scopes_on_offline_restart(self):
        self.policy_on_offline_restart("empty")


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
    probe = subprocess.run([args.python, "-c", "import aiohttp,lxml,trafilatura,mcp"], capture_output=True)
    if probe.returncode:
        print("SKIP: worker dependencies missing", file=sys.stderr)
        return 77
    CONFIG = e2e_crawl.CONFIG = e2e_web.CONFIG = args
    started = time.monotonic()
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False, verbosity=2).result
    print(json.dumps({"crawl_recovery_e2e_tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "seconds": round(time.monotonic() - started, 3),
                      "database_mutation_by_fixture": False, "pause_or_resume_calls": 0}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
