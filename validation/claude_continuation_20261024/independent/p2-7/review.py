"""Independent P2-7 probes: actual default saturation, disconnect and shutdown."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path('/workspace/scratch/e2371129386d/bzs861')
OUT = Path(__file__).resolve().parent
BINARY = OUT / 'searchd-reviewed'
CHECKS = 0
RESULTS = []


def check(condition, message):
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture:
    def __init__(self):
        self.release = threading.Event()
        self.changed = threading.Condition()
        self.started = self.active = self.peak = 0
        outer = self

        class RPC(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if request.get('method') != 'fetch.extract':
                    self.send_error(400)
                    return
                with outer.changed:
                    outer.started += 1
                    outer.active += 1
                    outer.peak = max(outer.peak, outer.active)
                    outer.changed.notify_all()
                try:
                    if not outer.release.wait(20):
                        self.send_error(504)
                        return
                    body = b'{"result":{"status":"timeout","error":"independent released RPC"}}'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionError):
                    pass
                finally:
                    with outer.changed:
                        outer.active -= 1
                        outer.changed.notify_all()

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), RPC)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()

    def wait(self, count):
        with self.changed:
            return self.changed.wait_for(lambda: self.started >= count, timeout=5)

    def close(self):
        self.release.set()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(5)


class Server:
    def __init__(self, path, fixture, name):
        token = secrets.token_hex(32)
        self.token = token
        auth = path / 'auth.json'
        auth.write_text(json.dumps({'tokens': [{'sha256': hashlib.sha256(token.encode()).hexdigest(),
                                               'subject': 'reviewer', 'admin': True}]}))
        auth.chmod(0o600)
        worker = path / 'worker.token'
        worker.write_text(secrets.token_hex(32))
        worker.chmod(0o600)
        sources = path / 'sources.json'
        sources.write_text('{"version":1,"sources":[]}')
        self.log = (OUT / (name + '-searchd.log')).open('w')
        self.process = subprocess.Popen([str(BINARY), '--auth', str(auth), '--state', str(path / 'state'),
                                         '--fixtures', str(ROOT / 'tests/fixtures'), '--port', '0', '--no-worker',
                                         '--worker', f'http://127.0.0.1:{fixture.http.server_port}',
                                         '--worker-token-file', str(worker), '--sources', str(sources)],
                                        stdout=subprocess.PIPE, stderr=self.log, text=True)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True).start()
        self.ready = json.loads(ready.get(timeout=8))
        self.port = self.ready['port']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, operation='stats', params=None, *, token=None, headers=None, health=False):
        payload = None if health else json.dumps({'operation': operation, 'params': params or {}}).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/' + ('healthz' if health else 'api/v0/call'), payload,
                                         {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or self.token),
                                          **(headers or {})}, method='GET' if health else 'POST')
        try:
            response = self.opener.open(request, timeout=3 if health or operation != 'web.read' else 15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def raw_read(self, suffix):
        payload = json.dumps({'operation': 'web.read', 'params': {'url': 'https://independent.example/' + suffix,
                                                               'timeout_ms': 15000}}).encode()
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=3)
        sock.sendall((f'POST /api/v0/call HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                      f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                      f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n').encode() + payload)
        return sock

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.process.stdout.close()
        self.log.close()


def saturation_and_disconnect():
    fixture = Fixture()
    server = None
    raw = None
    try:
        with tempfile.TemporaryDirectory(prefix='bzs-independent-default-http-') as tmp:
            server = Server(Path(tmp), fixture, 'default-saturation')
            check(server.ready['threads'] == 8 and server.ready['request_threads'] == 8, 'actual defaults must be 8 IO / 8 business')
            with ThreadPoolExecutor(max_workers=7) as clients:
                futures = [clients.submit(server.call, 'web.read', {'url': f'https://independent.example/{n}', 'timeout_ms': 15000})
                           for n in range(7)]
                try:
                    raw = server.raw_read('disconnected')
                    check(fixture.wait(8), 'all eight physical RPCs must enter before release')
                    check(fixture.active == fixture.peak == 8, 'default business pool actually saturated')
                    raw.close()
                    raw = None
                    check(server.call(health=True)[0] == 200, 'health must finish while every business RPC is still held')
                    check(not fixture.release.is_set(), 'health must not wait for business completion')
                    for n in range(12):
                        status, response = server.call('document.ingest', {'url': f'https://independent.example/rejected-{n}',
                                                                        'content': 'must never commit', 'content_type': 'text/plain'})
                        check(status == 503 and response['errors'][0]['code'] == 'E_REQUEST_BACKPRESSURE', 'reject excess admission without queuing')
                    check(server.call(token='invalid-token')[0] == 401, 'auth validation stays live under saturation')
                    check(server.call(headers={'Content-Type': 'text/plain'})[0] == 415, 'content type rejection stays live under saturation')
                    check(server.call(headers={'Origin': 'https://untrusted.example'})[0] == 403, 'origin rejection stays live under saturation')
                    check(fixture.started == 8 and fixture.active == 8, 'disconnect does not free an executing RPC slot or start rejected work')
                finally:
                    fixture.release.set()
                for future in futures:
                    check(future.result(timeout=5)[0] == 504, 'each accepted call completes exactly once')
                status, response = server.call()
                check(status == 200 and response['result']['documents'] == 0, 'all slots recover; rejected writes leave zero documents')
                check(fixture.started == 8, 'no replay of a disconnected or rejected request')
            server.stop()
            check(server.process.returncode == 0, 'normal shutdown exits successfully')
    finally:
        if raw is not None:
            raw.close()
        fixture.release.set()
        if server is not None:
            server.stop()
        fixture.close()
    RESULTS.append({'case': 'default_8_saturation_disconnect_recovery', 'passed': True, 'physical_rpcs': fixture.started, 'peak': fixture.peak})


def shutdown_during_dispatch():
    fixture = Fixture()
    server = None
    sockets = []
    try:
        with tempfile.TemporaryDirectory(prefix='bzs-independent-shutdown-http-') as tmp:
            server = Server(Path(tmp), fixture, 'shutdown')
            sockets = [server.raw_read(f'shutdown-{n}') for n in range(8)]
            check(fixture.wait(8), 'all RPCs must run before shutdown')
            server.process.send_signal(signal.SIGTERM)
            stopped_io = False
            for _ in range(5):
                try:
                    with server.opener.open(f'http://127.0.0.1:{server.port}/healthz', timeout=.25) as response:
                        response.read()
                except OSError:
                    stopped_io = True
                    break
            check(stopped_io, 'IO must stop accepting health work after the signal')
            check(server.process.poll() is None and not fixture.release.is_set(), 'process must join held business work before destruction')
            # Completion order is event controlled: releasing after signal must
            # safely join business workers before IO and Service are destroyed.
            fixture.release.set()
            check(server.process.wait(8) == 0, 'shutdown safely joins in-flight work')
            check(fixture.started == 8, 'shutdown never retries in-flight work')
            for sock in sockets:
                sock.close()
            sockets.clear()
            server.stop()
    finally:
        fixture.release.set()
        for sock in sockets:
            sock.close()
        if server is not None:
            server.stop()
        fixture.close()
    RESULTS.append({'case': 'shutdown_with_8_live_dispatches', 'passed': True, 'physical_rpcs': fixture.started, 'peak': fixture.peak})


if __name__ == '__main__':
    inputs = {str(path.relative_to(ROOT)): digest(path) for path in [ROOT / 'apps/searchd/main.cpp', ROOT / 'tests/integration.py']}
    executable_sha256 = digest(ROOT / 'build/searchd')
    shutil.copyfile(ROOT / 'build/searchd', BINARY)
    BINARY.chmod(0o755)
    check(digest(BINARY) == executable_sha256, 'independent executable copy is byte-identical to frozen root Debug build')
    started = time.monotonic()
    saturation_and_disconnect()
    shutdown_during_dispatch()
    check(inputs == {name: digest(ROOT / name) for name in inputs}, 'reviewed HTTP sources stayed unchanged')
    check(digest(BINARY) == executable_sha256, 'executed binary stayed unchanged')
    result = {'cases': RESULTS, 'checks': CHECKS, 'inputs': inputs, 'elapsed_seconds': round(time.monotonic() - started, 3),
              'executable_sha256': executable_sha256, 'scope': 'Linux independent real loopback; copy of root Debug server only; no external endpoints and no ASan claim'}
    (OUT / 'execution.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
