"""Durable search recovery with real core/worker processes and loopback HTTP.

Transparent proxies stop an RPC before forwarding, after the worker has replied,
or after the core has committed an API response. Crashes use SIGKILL on POSIX.
The fixture only observes SQLite; it never edits leases, events, clocks or state.
Real source HTTP counts distinguish a reserved attempt from an actual request.
This is process/RPC failure coverage, not physical power-loss acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
from urllib.parse import parse_qs, urlsplit

import e2e_web
from e2e_crawl_recovery import Gate, GatedProxy

CONFIG = None
OBSERVATIONS = []


class CountedWeb:
    """Observe real source HTTP and optionally hold one in-flight response."""

    def __init__(self, target_port):
        self.target_port = target_port
        self.requests = Counter()
        self.gates = []
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_request(self):
                self.close_connection = True
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 16384:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                parsed = urlsplit(self.path)
                params = parse_qs(parsed.query)
                if body:
                    params.update(parse_qs(body.decode("utf-8")))
                query = params.get("q", params.get("srsearch", [""]))[0]
                gate = None
                with fixture.lock:
                    fixture.requests[(parsed.path, query)] += 1
                    for candidate in fixture.gates:
                        if not candidate.claimed and candidate.url == parsed.path:
                            candidate.claimed = True
                            gate = candidate
                            break
                if gate and not gate.wait():
                    return
                connection = http.client.HTTPConnection("127.0.0.1", fixture.target_port, timeout=10)
                try:
                    headers = {"Connection": "close"}
                    if "Content-Type" in self.headers:
                        headers["Content-Type"] = self.headers["Content-Type"]
                    connection.request(self.command, self.path, body, headers)
                    response = connection.getresponse()
                    output = response.read(1024 * 1024 + 1)
                    if len(output) > 1024 * 1024:
                        raise RuntimeError("Synthetic source response exceeded fixture bound")
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.getheader("Content-Type", "text/html"))
                    self.send_header("Content-Length", str(len(output)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(output)
                except (BrokenPipeError, ConnectionError, TimeoutError, http.client.HTTPException):
                    return
                finally:
                    connection.close()

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02),
                                       daemon=True)
        self.thread.start()

    def hold(self, path):
        gate = Gate("source-http", "before", path)
        with self.lock:
            self.gates.append(gate)
        return gate

    def counts(self, query):
        with self.lock:
            return {path: count for (path, text), count in self.requests.items() if text == query}

    def close(self):
        with self.lock:
            for gate in self.gates:
                gate.discard()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class RecoveryStack(e2e_web.Stack):
    def spawn(self, name, command):
        command = list(command)
        if name == "fakeweb":
            ready = super().spawn(name, command)
            self.counted_web = CountedWeb(ready["port"])
            return {**ready, "port": self.counted_web.port}
        if name == "worker" and not hasattr(self, "worker_command"):
            self.worker_command = list(command)
        if name == "searchd":
            if not hasattr(self, "core_command"):
                self.core_command = list(command)
                self.rpc_proxy = GatedProxy(self.worker_port)
            command[command.index("--worker") + 1] = f"http://127.0.0.1:{self.rpc_proxy.port}"
        return super().spawn(name, command)

    def start_core(self, no_worker=False):
        command = list(self.core_command)
        if no_worker:
            command.append("--no-worker")
        self.core_port = self.spawn("searchd", command)["port"]

    def start_worker(self):
        command = list(self.worker_command)
        command[command.index("--port") + 1] = str(self.worker_port)
        self.worker_port = self.spawn("worker", command)["port"]
        self.rpc_proxy.target_port = self.worker_port

    def crash(self, name):
        process = self.procs[name]
        if process.poll() is not None:
            raise AssertionError(f"{name} exited before the crash checkpoint")
        process.kill()
        code = process.wait(timeout=5)
        if sys.platform != "win32" and code != -signal.SIGKILL:
            raise AssertionError(f"Expected SIGKILL, got {code}")
        if process.stdout:
            process.stdout.close()
        if not hasattr(self, "crashes"):
            self.crashes = []
        self.crashes.append({"component": name, "exit_code": code})
        return code

    def rows(self, query, values=()):
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
        if hasattr(self, "counted_web"):
            self.counted_web.close()
        super().stop()


class SessionRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bzs-session-crash-")
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

    def parameters(self, key, **options):
        return {"query": key, "idempotency_key": key, "sources": ["ddg", "bing"],
                "timeout_ms": 1000, "deadline_ms": 60000, **options}

    def state(self, session_id):
        return self.ok("session.get", {"session_id": session_id})

    def finished(self, session_id):
        return self.wait_until(
            lambda: (state if (state := self.state(session_id))["state"] in
                     ("completed", "cancelled", "failed") else None),
            "session did not settle within its real lease deadline")

    def page(self, session_id, version, **options):
        return self.ok("search.page", {"session_id": session_id,
                                      "result_set_version": version, **options})

    def assert_receipt(self, session_id, state, used, unknown):
        self.assertEqual(self.stack.rows(
            "SELECT state,used_requests,unknown_requests FROM search_stream_receipts WHERE id=?",
            (session_id,)), [{"state": state, "used_requests": used, "unknown_requests": unknown}])

    def assert_sources(self, session_id, states):
        self.assertEqual(self.stack.rows(
            "SELECT source_id,state FROM search_stream_sources WHERE session_id=? ORDER BY position",
            (session_id,)), [{"source_id": source, "state": state} for source, state in states])

    def assert_events(self, session_id, kinds):
        events = self.ok("session.events", {"session_id": session_id, "limit": 32})
        self.assertEqual([event["kind"] for event in events["events"]], kinds)
        self.assertEqual([event["event_id"] for event in events["events"]], list(range(1, len(kinds) + 1)))
        for event in events["events"]:
            self.assertLessEqual(set(event), {"event_id", "kind", "created_at_ms", "result_set_version"})
        self.assertFalse(events["has_more"])
        return events

    def record(self, session_id, params):
        state = self.state(session_id)
        OBSERVATIONS.append({"test": self.id().rsplit(".", 1)[-1],
                             "crashes": getattr(self.stack, "crashes", []),
                             "state": state["state"],
                             "dispatched_source_attempts": state["dispatched_source_attempts"],
                             "unknown_source_attempts": state["unknown_source_attempts"],
                             "source_http_counts": self.stack.counted_web.counts(params["query"]),
                             "event_count": state["last_event_id"]})

    def core_rpc_crash(self, stage):
        params = self.parameters("core-lost-" + stage)
        gate = self.stack.rpc_proxy.add("search.multi", stage)
        session_id = self.ok("web.search.start", params)["session_id"]
        self.assertTrue(gate.entered.wait(10), "source RPC did not reach crash boundary")
        self.assertEqual(gate.request["params"]["sources"], ["ddg"])
        lease = self.stack.rows("SELECT lease_until FROM search_streams WHERE id=?", (session_id,))[0]
        self.assertGreater(lease["lease_until"], time.time() * 1000)
        self.assertEqual(gate.forwarded, stage == "after")
        self.assert_receipt(session_id, "running", 1, 0)
        self.stack.crash("searchd")
        gate.discard()
        self.stack.start_core()
        # No test clock and no SQLite updates: recovery waits for lease_until.
        final = self.finished(session_id)
        self.assertGreaterEqual(time.time() * 1000, lease["lease_until"])
        self.assertEqual(final["state"], "completed")
        self.assertEqual((final["dispatched_source_attempts"], final["unknown_source_attempts"]), (2, 1))
        page = self.page(session_id, final["result_set_version"])
        self.assertTrue(page["partial"])
        self.assertEqual(len(page["hits"]), 2)
        for hit in page["hits"]:
            self.assertEqual({source["source_id"] for source in hit["sources"]}, {"bing"})
        counts = {"/bing/search": 1}
        if stage == "after":
            counts["/ddg/html/"] = 1
        self.assertEqual(self.stack.counted_web.counts(params["query"]), counts)
        self.assert_sources(session_id, [("ddg", "unknown"), ("bing", "completed")])
        self.assert_receipt(session_id, "completed", 2, 1)
        self.assert_events(session_id, ["queued", "source_unknown", "completed"])
        # A second crash must not reset charged attempts or rerun the lost source.
        self.stack.crash("searchd")
        self.stack.start_core()
        replay = self.ok("web.search.start", params)
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["result_set_version"], final["result_set_version"])
        self.assertEqual(self.page(session_id, final["result_set_version"]), page)
        self.assertEqual(self.stack.counted_web.counts(params["query"]), counts)
        self.assert_receipt(session_id, "completed", 2, 1)
        self.record(session_id, params)

    def test_01_core_crash_before_worker_reserves_unknown_without_sending_source(self):
        self.core_rpc_crash("before")

    def test_02_core_crash_after_worker_reply_never_resends_unknown_source(self):
        self.core_rpc_crash("after")

    def test_03_worker_crash_during_http_then_restart_runs_only_pending_tail(self):
        params = self.parameters("worker-lost-http")
        source_gate = self.stack.counted_web.hold("/ddg/html/")
        session_id = self.ok("web.search.start", params)["session_id"]
        self.assertTrue(source_gate.entered.wait(10), "worker did not issue real source HTTP")
        tail_gate = self.stack.rpc_proxy.add("search.multi", "before")
        self.stack.crash("worker")
        source_gate.discard()
        self.assertTrue(tail_gate.entered.wait(10), "core did not advance beyond failed worker RPC")
        self.assertEqual(tail_gate.request["params"]["sources"], ["bing"])
        self.assert_sources(session_id, [("ddg", "unknown"), ("bing", "running")])
        self.assert_receipt(session_id, "running", 2, 1)
        self.stack.start_worker()
        tail_gate.released.set()
        final = self.finished(session_id)
        self.assertEqual(final["state"], "completed")
        self.assertEqual((final["dispatched_source_attempts"], final["unknown_source_attempts"]), (2, 1))
        self.assertTrue(self.page(session_id, final["result_set_version"])["partial"])
        self.assertEqual(self.stack.counted_web.counts(params["query"]), {"/ddg/html/": 1, "/bing/search": 1})
        self.assert_sources(session_id, [("ddg", "unknown"), ("bing", "completed")])
        self.assert_receipt(session_id, "completed", 2, 1)
        self.assert_events(session_id, ["queued", "source_unknown", "completed"])
        self.record(session_id, params)

    def test_04_lost_start_ack_reuses_committed_session_after_core_crash(self):
        self.stack.crash("searchd")
        self.stack.start_core(no_worker=True)
        proxy = GatedProxy(self.stack.core_port)
        gate = proxy.add("web.search.start", "after")
        actual_port = self.stack.core_port
        params = self.parameters("lost-start-ack")
        try:
            self.stack.core_port = proxy.port
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.stack.call, "web.search.start", params)
                self.assertTrue(gate.entered.wait(10), "accepted start response did not reach ACK gate")
                accepted = gate.response["result"]
                session_id = accepted["session_id"]
                self.assertEqual(accepted["state"], "queued")
                self.assert_receipt(session_id, "queued", 0, 0)
                self.assertEqual(self.stack.counted_web.counts(params["query"]), {})
                self.stack.crash("searchd")
                gate.discard()
                with self.assertRaises((ConnectionError, http.client.HTTPException)):
                    pending.result(timeout=5)
        finally:
            self.stack.core_port = actual_port
            proxy.close()
        self.stack.start_core()
        replay = self.ok("web.search.start", params)
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["session_id"], session_id)
        final = self.finished(session_id)
        self.assertEqual(final["state"], "completed")
        self.assertEqual((final["dispatched_source_attempts"], final["unknown_source_attempts"]), (2, 0))
        self.assert_receipt(session_id, "completed", 2, 0)
        self.assert_events(session_id, ["queued", "result_version", "completed"])
        self.assertEqual(self.stack.counted_web.counts(params["query"]), {"/ddg/html/": 1, "/bing/search": 1})
        self.record(session_id, params)

    def test_05_cancel_fences_late_result_preserves_version_and_event_cursor_after_restart(self):
        params = self.parameters("cancel-late-result", sources=["ddg", "bing", "wiki"])
        first = self.stack.rpc_proxy.add("search.multi", "after")
        second = self.stack.rpc_proxy.add("search.multi", "after")
        session_id = self.ok("web.search.start", params)["session_id"]
        self.assertTrue(first.entered.wait(10))
        first.released.set()
        self.assertTrue(second.entered.wait(10))
        self.assertEqual(second.request["params"]["sources"], ["bing"])
        version = self.state(session_id)["result_set_version"]
        page = self.page(session_id, version, page_size=1)
        self.assertTrue(page["has_more"])
        next_page = self.page(session_id, version, page_size=1, cursor=page["next_cursor"])
        prefix = self.ok("session.events", {"session_id": session_id, "limit": 2})
        self.assertEqual([event["event_id"] for event in prefix["events"]], [1, 2])
        cancelled = self.ok("session.cancel", {"session_id": session_id})
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["result_set_version"], version)
        self.assertEqual((cancelled["dispatched_source_attempts"], cancelled["unknown_source_attempts"]), (2, 1))
        second.released.set()
        # A new completed session proves the worker loop consumed the late RPC.
        probe = self.ok("web.search.start", self.parameters("after-cancel-probe", sources=["wiki"]))
        self.assertEqual(self.finished(probe["session_id"])["state"], "completed")
        self.assertEqual(self.state(session_id)["result_set_version"], version)
        self.assert_sources(session_id, [("ddg", "completed"), ("bing", "unknown"), ("wiki", "cancelled")])
        self.assert_receipt(session_id, "cancelled", 2, 1)
        self.assertEqual(self.stack.counted_web.counts(params["query"]), {"/ddg/html/": 1, "/bing/search": 1})
        self.stack.crash("searchd")
        self.stack.start_core()
        self.assertEqual(self.page(session_id, version, page_size=1), page)
        self.assertEqual(self.page(session_id, version, page_size=1, cursor=page["next_cursor"]), next_page)
        tail_args = {"session_id": session_id, "after_event_id": prefix["next_event_id"]}
        tail = self.ok("session.events", tail_args)
        self.assertEqual([event["kind"] for event in tail["events"]], ["cancelled"])
        self.assertEqual([event["event_id"] for event in tail["events"]], [3])
        self.assertEqual(self.ok("session.events", tail_args)["events"], tail["events"])
        last = tail["next_event_id"]
        for _ in range(3):
            heartbeat = self.ok("session.events", {"session_id": session_id, "after_event_id": last})
            self.assertEqual(heartbeat["events"], [])
            self.assertEqual(heartbeat["last_event_id"], last)
        self.assertEqual(self.ok("session.cancel", {"session_id": session_id})["last_event_id"], last)
        self.assert_events(session_id, ["queued", "result_version", "cancelled"])
        self.record(session_id, params)

    def test_06_real_deadline_rejects_late_response_and_never_dispatches_tail(self):
        params = self.parameters("deadline-late-result", deadline_ms=1000)
        gate = self.stack.rpc_proxy.add("search.multi", "after")
        session_id = self.ok("web.search.start", params)["session_id"]
        self.assertTrue(gate.entered.wait(10))
        final = self.finished(session_id)
        self.assertEqual(final["state"], "failed")
        self.assertEqual(final["error_code"], "E_DEADLINE")
        self.assertEqual(final["result_set_version"], "")
        self.assertEqual((final["dispatched_source_attempts"], final["unknown_source_attempts"]), (1, 1))
        gate.released.set()
        probe = self.ok("web.search.start", self.parameters("after-deadline-probe", sources=["wiki"]))
        self.assertEqual(self.finished(probe["session_id"])["state"], "completed")
        self.assertEqual(self.state(session_id)["result_set_version"], "")
        self.assertEqual(self.stack.counted_web.counts(params["query"]), {"/ddg/html/": 1})
        self.assert_sources(session_id, [("ddg", "unknown"), ("bing", "pending")])
        self.assert_receipt(session_id, "failed", 1, 1)
        self.assert_events(session_id, ["queued", "failed"])
        self.record(session_id, params)


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
    CONFIG = e2e_web.CONFIG = args
    started = time.monotonic()
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False, verbosity=2).result
    print(json.dumps({"session_recovery_e2e_tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "seconds": round(time.monotonic() - started, 3),
                      "database_mutation_by_fixture": False, "clock_injection": False,
                      "real_source_http_counts": True, "physical_power_loss_coverage": False,
                      "observations": OBSERVATIONS}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
