"""Real loopback endpoints: no token forwarding or mutating request replay."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from bzs.core_client import CoreClient, CoreError


@contextmanager
def redirect_pair(status):
    seen = {"origin": [], "destination": []}
    class Destination(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.reply()
        def do_POST(self):
            self.reply()
        def reply(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen["destination"].append((self.command, self.headers.get("Authorization"), body))
            raw = b'{"status":"ok","result":{"accepted":true}}'
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    target = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    class Origin(Destination):
        def reply(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen["origin"].append(self.command)
            self.send_response(status)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/other")
            raw = b'{"status":"ok","result":{}}'
            self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threads = [Thread(target=s.serve_forever,kwargs={"poll_interval":0.01},daemon=True) for s in (target,origin)]
    for thread in threads: thread.start()
    try: yield f"http://127.0.0.1:{origin.server_port}", seen
    finally:
        for server in (origin,target): server.shutdown();server.server_close()
        for thread in threads: thread.join(timeout=2)


@pytest.mark.parametrize("status", [301,302,303,307,308])
@pytest.mark.parametrize("asynchronous", [False,True],ids=["sync","async"])
def test_core_call_refuses_redirect_without_forwarding_credentials_or_body(status, asynchronous):
    with redirect_pair(status) as (url, seen):
        client=CoreClient(url,"synthetic-review-token",timeout_s=2)
        try:
            if asynchronous: asyncio.run(client.call("document.ingest",{"content":"synthetic-body"}))
            else: client.call_sync("document.ingest",{"content":"synthetic-body"})
        except CoreError as error:
            assert error.code=="E_CORE_REDIRECT"
        else:
            pytest.fail(f"redirect was accepted; destination requests={len(seen['destination'])}")
        assert seen["origin"]==["POST"]
        assert seen["destination"]==[]


def test_core_health_does_not_follow_redirects():
    with redirect_pair(302) as (url,seen):
        assert not CoreClient(url,"synthetic-review-token").healthy()
        assert seen["origin"]==["GET"]
        assert seen["destination"]==[]
