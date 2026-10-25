"""Independent black-box searchd control-admission probe (loopback only)."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

checks = 0
cases = []


def require(condition, message):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(message)


class Worker:
    def __init__(self):
        self.condition = threading.Condition()
        self.reads = []
        self.cancels = []
        self.read_release = threading.Event()
        self.cancel_release = threading.Event()
        self.cancel_release.set()
        self.profile = hashlib.sha256(b"independent HTTP admission worker").hexdigest()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, params = payload["method"], payload["params"]
                if method == "fetch.profile":
                    result = {"version": "bzs-http-freshness-1", "request_profile": owner.profile}
                elif method == "fetch.cancel_direct":
                    with owner.condition:
                        owner.cancels.append(params["control_id"])
                        owner.condition.notify_all()
                    owner.cancel_release.wait(10)
                    result = {"cancel_requested": True}
                elif method in ("fetch.extract", "fetch.extract_direct"):
                    with owner.condition:
                        owner.reads.append(payload)
                        owner.condition.notify_all()
                    owner.read_release.wait(10)
                    result = owner.response(params["url"])
                else:
                    raise AssertionError(method)
                body = json.dumps({"result": result}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionError, BrokenPipeError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()

    def response(self, url):
        text = "independent admission body"
        return {"status": "ok", "http_status": 200, "html": text, "text": text,
            "title": "Independent admission", "content_type": "text/plain", "extractor": "independent/1",
            "extract_status": "ok", "index_allowed": True, "links": [], "redirects": [], "warnings": [],
            "metadata": {}, "final_url": url,
            "usage": {"external_requests": 1, "content_bytes": len(text)},
            "publication": {"version": "bzs-publication-1", "index_allowed": True, "snippet_allowed": True,
                "follow_allowed": True, "snippet_text": text, "snippet_title": "Independent admission",
                "directives": [], "diagnostics": []},
            "http_freshness": {"version": "bzs-http-freshness-1", "url": url, "request_profile": self.profile,
                "cache_control": "max-age=600", "vary": "", "date": "", "age": "", "expires": "", "pragma": "",
                "received_at_ms": int(time.time() * 1000), "response_delay_ms": 0, "invalid": False}}

    def reached(self, *, reads=None, cancels=None):
        with self.condition:
            return self.condition.wait_for(lambda: (reads is None or len(self.reads) >= reads) and
                                            (cancels is None or len(self.cancels) >= cancels), timeout=5)

    def close(self):
        self.read_release.set()
        self.cancel_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Core:
    def __init__(self, binary, fixtures, root, worker, *, port=0):
        self.binary, self.fixtures, self.root = binary, fixtures, root
        self.tokens = {name: secrets.token_hex(32) for name in ("alice", "bob")}
        auth = root / "auth.json"
        auth.write_text(json.dumps({"tokens": [{"sha256": hashlib.sha256(token.encode()).hexdigest(),
            "subject": name, "admin": name == "alice"} for name, token in self.tokens.items()]}))
        auth.chmod(0o600)
        token = root / "worker.token"
        token.write_text(secrets.token_hex(32))
        token.chmod(0o600)
        sources = root / "sources.json"
        sources.write_text('{"version":1,"sources":[]}')
        self.command = [binary, "--auth", str(auth), "--state", str(root / "state"), "--fixtures", fixtures,
            "--port", str(port), "--threads", "1", "--request-threads", "1", "--no-worker",
            "--worker", f"http://127.0.0.1:{worker.port}", "--worker-token-file", str(token), "--sources", str(sources)]
        self.log = (root / "stderr.log").open("w")
        self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=self.log, text=True)
        ready = queue.Queue()
        self.reader = threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True)
        self.reader.start()
        line = ready.get(timeout=10)
        if not line:
            raise RuntimeError((root / "stderr.log").read_text())
        self.ready = json.loads(line)
        self.port = self.ready["port"]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, operation="web.read.cancel", params=None, *, body=None, owner="alice", headers=None, path="/api/v0/call", method="POST"):
        if body is None:
            body = json.dumps({"operation": operation, "params": params or {}}, separators=(",", ":")).encode()
        merged = {"Content-Type": "application/json", "Authorization": "Bearer " + self.tokens[owner]}
        merged.update(headers or {})
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", body if method == "POST" else None,
                                         merged, method=method)
        try:
            with self.opener.open(request, timeout=7) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def prepare(self):
        status, result = self.call("web.read.prepare")
        require(status == 200, "prepare accepted with free ordinary capacity")
        return result["result"]["read_id"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdout.close()
        self.log.close()
        self.reader.join(timeout=1)


def code(response):
    return response[1].get("errors", [{}])[0].get("code")


def run_case(binary, fixtures, name, function):
    with tempfile.TemporaryDirectory(prefix="bzs-control-admission-independent-") as temporary:
        worker = Worker()
        core = None
        try:
            core = Core(binary, fixtures, Path(temporary), worker)
            require(core.ready["request_threads"] == 1, "ordinary admission capacity preserved")
            require(core.ready["control_threads"] == 1, "one reserved control thread reported")
            require(core.ready["control_body_limit_bytes"] == 4096, "bounded control classifier reported")
            with ThreadPoolExecutor(max_workers=4) as executor:
                try:
                    function(core, worker, executor)
                finally:
                    worker.read_release.set()
                    worker.cancel_release.set()
            cases.append({"name": name, "passed": True})
        finally:
            worker.read_release.set()
            worker.cancel_release.set()
            if core is not None:
                core.close()
            worker.close()


def strict_classifier(core, worker, executor):
    owned = core.prepare()
    held = executor.submit(core.call, "web.read", {"url": "https://admission.example/held", "freshness": "refresh"})
    require(worker.reached(reads=1), "ordinary read physically held")
    unknown = "0" * 48
    valid = json.dumps({"operation": "web.read.cancel", "params": {"read_id": unknown}}, separators=(",", ":")).encode()
    rejected = [
        b'{"operation":"web.read.prepare","params":{}}',
        b'{"operation":"stats","params":{"note":"web.read.cancel"}}',
        b'{"operation":"job.control","params":{"job_id":"x","action":"cancel"}}',
        b'{"operation":"web.read.cancel","params":{"read_id":true}}',
        valid.replace(unknown.encode(), b"A" * 48),
        valid.replace(b'"read_id":', b'"extra":false,"read_id":'),
        valid.replace(b'{"operation":', b'{"extra":null,"operation":'),
        valid.replace(b'"params":', b'"operation":"web.read.cancel","params":'),
        valid.replace(b'"params":', b'"oper\\u0061tion":"web.read.cancel","params":'),
        valid.replace(b'"read_id":', b'"read\\u005fid":"' + unknown.encode() + b'","read_id":'),
        b'{"operation":"web.read.cancel","params":null}',
        b'{"operation":"web.read.cancel"}',
        b'{"nested":' + valid + b'}',
        b'{"operation":"web.read.cancel","params":{"read_id":"' + unknown.encode() + b'"}} trailing',
        valid + b" " * (4097 - len(valid)),
    ]
    for index, payload in enumerate(rejected):
        answer = core.call(body=payload)
        require(answer[0] == 503 and code(answer) == "E_REQUEST_BACKPRESSURE", f"ineligible payload {index} cannot use control slot")
    exact = valid + b" " * (4096 - len(valid))
    answer = core.call(body=exact)
    require(len(exact) == 4096 and answer[0] == 404 and code(answer) == "E_NOT_FOUND", "4096-byte valid control enters Service")
    escaped = valid.replace(b'"operation"', b'"oper\\u0061tion"').replace(b'"params"', b'"par\\u0061ms"').replace(b'"read_id"', b'"read\\u005fid"')
    answer = core.call(body=escaped)
    require(answer[0] == 404 and code(answer) == "E_NOT_FOUND", "JSON-equivalent escaped keys admitted")
    answer = core.call(params={"read_id": owned}, owner="bob")
    require(answer[0] == 404 and code(answer) == "E_NOT_FOUND", "reserved classification preserves owner authorization")
    for headers, expected in [({"Authorization": "Bearer invalid"}, 401), ({"Origin": "https://untrusted.example"}, 403),
                              ({"Host": "untrusted.example"}, 403), ({"Content-Type": "text/plain"}, 415)]:
        require(core.call(body=valid, headers=headers)[0] == expected, "transport/auth checks precede admission")
    require(core.call(path="/healthz", method="GET")[0] == 200, "health IO remains available while ordinary pool full")
    answer = core.call(params={"read_id": owned})
    require(answer[0] == 200 and answer[1]["result"]["cancel_requested"], "prepared cancel accepted while ordinary pool full")
    require(len(worker.reads) == 1 and not worker.cancels, "classification or prepared cancel causes no new Worker execution")
    require(not held.done(), "held ordinary work remains held during classifier matrix")
    worker.read_release.set()
    require(held.result(timeout=7)[0] == 200, "ordinary work survives reserved controls")


def real_last_member_cancel(core, worker, executor):
    read_id = core.prepare()
    pending = executor.submit(core.call, "web.read", {"url": "https://admission.example/controlled", "freshness": "refresh", "read_id": read_id})
    require(worker.reached(reads=1), "controlled ordinary slot is occupied")
    require(core.call("stats")[0] == 503, "normal work cannot borrow control capacity")
    answer = core.call(params={"read_id": read_id})
    require(answer[0] == 200 and answer[1]["result"]["physical_cancel_requested"], "last-member cancel independently admitted")
    require(worker.reached(cancels=1), "physical cooperation sent once")
    worker.read_release.set()
    answer = pending.result(timeout=7)
    require(answer[0] == 409 and code(answer) == "E_CANCELLED", "cancelled read cannot deliver success")
    require(answer[1]["usage"]["external_requests"] == 1, "fixed payer usage retained")
    require(core.call("stats")[0] == 200, "ordinary capacity recovers")


def reserved_slot_is_bounded_and_exclusive(core, worker, executor):
    read_id, later = core.prepare(), core.prepare()
    pending = executor.submit(core.call, "web.read", {"url": "https://admission.example/held-control", "freshness": "refresh", "read_id": read_id})
    require(worker.reached(reads=1), "read registered before cancellation")
    worker.cancel_release.clear()
    first_cancel = executor.submit(core.call, "web.read.cancel", {"read_id": read_id})
    require(worker.reached(cancels=1), "first control occupies reserved executor")
    worker.read_release.set()
    require(code(pending.result(timeout=7)) == "E_CANCELLED", "ordinary read finishes while cancel ACK is held")
    require(core.call("stats")[0] == 200, "ordinary executor is free while control is held")
    second = core.call(params={"read_id": later})
    require(second[0] == 503 and code(second) == "E_REQUEST_BACKPRESSURE", "second eligible control has no ordinary fallback or queue")
    require(core.call(path="/healthz", method="GET")[0] == 200, "health unaffected by held control RPC")
    require(not first_cancel.done(), "first control is demonstrably still held")
    worker.cancel_release.set()
    require(first_cancel.result(timeout=7)[0] == 200, "first control completes after ACK release")
    answer = core.call("web.read", {"url": "https://admission.example/no-snapshot", "freshness": "cache_only", "read_id": later})
    require(code(answer) == "E_NOT_CACHED", "rejected second control did not mutate the prepared member")
    require(len(worker.cancels) == 1, "no deferred or duplicate Worker control")


def shutdown_with_both_lanes_active(core, worker, executor):
    read_id = core.prepare()
    read = executor.submit(core.call, "web.read", {"url": "https://admission.example/stopping", "freshness": "refresh", "read_id": read_id})
    require(worker.reached(reads=1), "shutdown starts with physical read held")
    worker.cancel_release.clear()
    cancel = executor.submit(core.call, "web.read.cancel", {"read_id": read_id})
    require(worker.reached(cancels=1), "shutdown starts with reserved control held")
    core.process.terminate()
    worker.cancel_release.set()
    worker.read_release.set()
    require(core.process.wait(timeout=8) == 0, "shutdown joins active work without abort or hang")
    for future in [read, cancel]:
        try:
            future.result(timeout=7)
        except (ConnectionError, urllib.error.URLError, TimeoutError):
            pass
    require(len(worker.reads) == 1 and len(worker.cancels) == 1, "shutdown creates no replacement work")


def baseline_comparison(binary, fixtures):
    with tempfile.TemporaryDirectory(prefix="bzs-control-admission-baseline-") as temporary:
        worker = Worker()
        core = None
        try:
            core = Core(binary, fixtures, Path(temporary), worker)
            require(core.ready["request_threads"] == 1, "baseline ordinary capacity is one")
            read_id = core.prepare()
            with ThreadPoolExecutor(max_workers=2) as executor:
                try:
                    pending = executor.submit(core.call, "web.read", {"url": "https://admission.example/baseline",
                        "freshness": "refresh", "read_id": read_id})
                    require(worker.reached(reads=1), "baseline ordinary read occupies its only slot")
                    answer = core.call(params={"read_id": read_id})
                    require(answer[0] == 503 and code(answer) == "E_REQUEST_BACKPRESSURE", "baseline cancellation is blocked by ordinary saturation")
                    require(not worker.cancels and not pending.done(), "rejected baseline cancellation has no business effect")
                    worker.read_release.set()
                    require(pending.result(timeout=7)[0] == 200, "baseline read succeeds because cancellation was not admitted")
                    cases.append({"name": "baseline_cancel_is_not_admitted_under_saturation", "observed": True,
                                  "classification": "missing next-increment capability, not a baseline regression"})
                finally:
                    worker.read_release.set()
                    worker.cancel_release.set()
        finally:
            if core is not None:
                core.close()
            worker.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    if args.baseline:
        baseline_comparison(args.server, args.fixtures)
        print(json.dumps({"cases": cases, "checks": checks, "failed": 0}, indent=2), flush=True)
        return
    for name, function in [("strict_classifier_and_transport_precedence", strict_classifier),
                           ("ordinary_saturation_last_member_cancel", real_last_member_cancel),
                           ("bounded_exclusive_control_slot", reserved_slot_is_bounded_and_exclusive),
                           ("shutdown_with_both_lanes_active", shutdown_with_both_lanes_active)]:
        print("RUN " + name, flush=True)
        run_case(args.server, args.fixtures, name, function)
        print("PASS " + name, flush=True)
    print(json.dumps({"cases": cases, "checks": checks, "failed": 0}, indent=2), flush=True)


if __name__ == "__main__":
    main()
