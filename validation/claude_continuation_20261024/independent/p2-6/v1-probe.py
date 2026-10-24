"""Independent composition: three durable lanes progress while HTTP pool is full."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

OUT = Path(__file__).resolve().parent
AUTHOR = Path('/workspace/scratch/e2371129386d/bzs861-next-dispatcher')
checks = 0


def check(value, message):
    global checks
    checks += 1
    if not value:
        raise AssertionError(message)


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


class RPC:
    def __init__(self):
        self.condition = threading.Condition()
        self.permits = {name: 0 for name in ('read', 'map', 'search', 'sync')}
        self.calls = self.permits.copy()
        self.active = self.permits.copy()
        self.peak = self.permits.copy()
        self.total_peak = 0
        self.finish = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method, params = request['method'], request['params']
                group = {'map.fetch': 'map', 'search.multi': 'search'}.get(method, 'read' if 'attempt_id' in params else 'sync')
                with outer.condition:
                    outer.calls[group] += 1
                    outer.active[group] += 1
                    outer.peak[group] = max(outer.peak[group], outer.active[group])
                    outer.total_peak = max(outer.total_peak, sum(outer.active.values()))
                    outer.condition.notify_all()
                    allowed = outer.condition.wait_for(lambda: outer.finish or outer.permits[group] > 0, timeout=20)
                    if outer.permits[group]:
                        outer.permits[group] -= 1
                try:
                    if not allowed:
                        self.send_error(504)
                        return
                    if group == 'search':
                        result = {'results': {source: {'status': 'empty', 'hits': []} for source in params['sources']}}
                    elif group == 'map':
                        result = {'status': 'error', 'error_code': 'E_FETCH_TIMEOUT', 'url': params['url'],
                                  'final_url': params['url'], 'http_status': None,
                                  'usage': {'external_requests': 1, 'content_bytes': 0}}
                    else:
                        result = {'status': 'timeout', 'error': 'independent released RPC',
                                  'usage': {'external_requests': 1, 'content_bytes': 0}}
                    body = json.dumps({'result': result}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionError, BrokenPipeError):
                    pass
                finally:
                    with outer.condition:
                        outer.active[group] -= 1
                        outer.condition.notify_all()

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()

    def wait(self, group, count):
        with self.condition:
            return self.condition.wait_for(lambda: self.calls[group] >= count, timeout=5)

    def release(self, group):
        with self.condition:
            self.permits[group] += 1
            self.condition.notify_all()

    def release_all(self):
        with self.condition:
            self.finish = True
            self.condition.notify_all()

    def close(self):
        self.release_all()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(5)


def run():
    rpc = RPC()
    process = None
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='bzs-independent-lanes-pool-') as directory:
            root = Path(directory)
            token = secrets.token_hex(32)
            auth = root / 'auth.json'
            auth.write_text(json.dumps({'tokens': [{'sha256': hashlib.sha256(token.encode()).hexdigest(), 'subject': 'reviewer', 'admin': True}]}))
            auth.chmod(0o600)
            worker = root / 'worker.token'
            worker.write_text(secrets.token_hex(32))
            worker.chmod(0o600)
            review = {'status': 'approved', 'payment_scheme': 'no_payment', 'automated_access': 'approved',
                      'checked_at': '1970-01-01T00:00:00Z', 'expires_at': '2999-01-01T00:00:00Z',
                      'terms_url': 'https://review.example/terms', 'cache_policy': 'allowed_with_limits',
                      'redistribution_policy': 'allowed_with_limits', 'approved_by': 'offline-independent-fixture'}
            limits = {'shared_budget_key': 'review-source', 'max_concurrency': 1, 'min_interval_ms': 0,
                      'request_quota': 100, 'quota_window_seconds': 86400}
            source = {'id': 'review-source', 'provider': 'review-source', 'index_family': 'review-source', 'capability': 'web',
                      'kind': 'api', 'enabled': True, 'default': True, 'min_interval_ms': 0, 'daily_cap': 100,
                      'params': {'adapter': 'offline_fixture', 'base_url': 'https://review.example/search'}}
            source['contract'] = {'id': source['id'], 'provider_id': source['provider'], 'index_family': source['index_family'],
                                  'logical_service_id': source['id'], 'capability': 'web', 'endpoint': source['params']['base_url'],
                                  'adapter_id': 'offline_fixture', 'enabled': True, 'review': review, 'limits': limits}
            sources = root / 'sources.json'
            sources.write_text(json.dumps({'version': 1, 'sources': [source]}))
            sites = root / 'sites.json'
            sites.write_text(json.dumps({'version': 1, 'sites': [{'id': 'review-site', 'enabled': True,
                'origin': 'https://review.example', 'path_prefixes': ['/docs/'], 'review': review,
                'limits': {key: value for key, value in limits.items() if key != 'shared_budget_key'}}]}))
            log = (OUT / 'searchd.log').open('w')
            process = subprocess.Popen([str(OUT / 'searchd-combined'), '--auth', str(auth), '--state', str(root / 'state'),
                '--fixtures', str(AUTHOR / 'tests/fixtures'), '--port', '0', '--worker', f'http://127.0.0.1:{rpc.http.server_port}',
                '--worker-token-file', str(worker), '--sources', str(sources), '--sites', str(sites)],
                stdout=subprocess.PIPE, stderr=log, text=True)
            line = queue.Queue()
            threading.Thread(target=lambda: line.put(process.stdout.readline()), daemon=True).start()
            ready = json.loads(line.get(timeout=8))
            check(ready['request_threads'] == ready['threads'] == 8, 'actual default HTTP limits')
            port = ready['port']
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def call(operation='stats', params=None, health=False):
                data = None if health else json.dumps({'operation': operation, 'params': params or {}}).encode()
                req = urllib.request.Request(f'http://127.0.0.1:{port}/' + ('healthz' if health else 'api/v0/call'), data,
                    {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='GET' if health else 'POST')
                try:
                    response = opener.open(req, timeout=15 if operation == 'web.read' else 3)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)

            def ok(operation, params):
                status, body = call(operation, params)
                check(status == 200, f'{operation}: {status} {body}')
                return body['result']

            database = root / 'state/metadata.sqlite'

            def cell(sql, identifier):
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                    try:
                        return db.execute(sql, (identifier,)).fetchone()[0]
                    finally:
                        db.close()

            reads, maps, searches = [], [], []
            for index in range(2):
                reads.append(ok('read_many.submit', {'urls': [f'https://read.review.example/{index}'],
                    'idempotency_key': f'read-{index}', 'per_page_timeout_ms': 15000, 'max_requests': 1})['job_id'])
                maps.append(ok('map.submit', {'site_id': 'review-site', 'seeds': [f'https://review.example/docs/{index}'],
                    'idempotency_key': f'map-{index}', 'per_request_timeout_ms': 15000, 'max_requests': 1})['job_id'])
                searches.append(ok('web.search.start', {'query': f'independent {index}', 'idempotency_key': f'search-{index}',
                    'timeout_ms': 15000, 'deadline_ms': 30000})['session_id'])
            fixture = ok('job.create', {'fixture_id': 'plain', 'idempotency_key': 'independent-fixture'})['job_id']
            cancelled = ok('read_many.submit', {'urls': ['https://read.review.example/cancelled'],
                'idempotency_key': 'cancelled'})['job_id']
            ok('job.control', {'job_id': cancelled, 'action': 'cancel'})
            for group in ('read', 'map', 'search'):
                check(rpc.wait(group, 1), f'{group} background lane enters before release')
            with ThreadPoolExecutor(max_workers=8) as clients:
                pending = [clients.submit(call, 'web.read', {'url': f'https://sync.review.example/{n}', 'timeout_ms': 15000}) for n in range(8)]
                try:
                    check(rpc.wait('sync', 8), 'HTTP pool reaches eight physical calls while background RPCs are held')
                    check(rpc.total_peak == 11, 'bounded composition reaches three background plus eight synchronous RPCs')
                    check(call(health=True)[0] == 200, 'health stays outside both pools')
                    status, response = call('job.get', {'job_id': fixture})
                    check(status == 503 and response['errors'][0]['code'] == 'E_REQUEST_BACKPRESSURE', 'business admission is saturated')
                    check(eventually(lambda: cell('SELECT state FROM jobs WHERE id=?', fixture) == 'succeeded'), 'fixture lane has independent progress')
                    for group in ('search', 'read', 'map'):
                        rpc.release(group)
                        check(rpc.wait(group, 2), f'second durable {group} starts while all HTTP business threads remain held')
                        check(rpc.active['sync'] == 8 and rpc.permits['sync'] == 0, 'HTTP business work has not been released')
                        check(rpc.peak[group] == 1, f'{group} lane never prefetches concurrent leases')
                        rpc.release(group)
                    check(eventually(lambda: all(cell('SELECT state FROM search_streams WHERE id=?', value) == 'completed' for value in searches)), 'both durable searches complete independently')
                    check(eventually(lambda: all(cell('SELECT state FROM jobs WHERE id=?', value) == 'failed' for value in reads + maps)), 'known read/map failures settle independently')
                    for identifier in reads:
                        check(cell('SELECT external_requests FROM read_job_items WHERE job_id=?', identifier) == 1, 'read actual use charged once')
                        check(cell('SELECT unknown_requests FROM read_job_items WHERE job_id=?', identifier) == 0, 'known read outcome is not charged as unknown')
                    for identifier in maps:
                        check(cell('SELECT external_requests FROM map_jobs WHERE job_id=?', identifier) == 1, 'map actual use charged once')
                        check(cell('SELECT unknown_requests FROM map_jobs WHERE job_id=?', identifier) == 0, 'known map outcome is not charged as unknown')
                    check(cell('SELECT state FROM jobs WHERE id=?', cancelled) == 'cancelled', 'queued cancellation remains terminal')
                    check(cell('SELECT attempts FROM jobs WHERE id=?', cancelled) == 0, 'cancelled queued work is never dispatched')
                    check(rpc.calls == {'read': 2, 'map': 2, 'search': 2, 'sync': 8}, 'no cancelled/failed work is replayed')
                finally:
                    rpc.release_all()
                for future in pending:
                    check(future.result(timeout=5)[0] == 504, 'all accepted sync calls settle')
            check(call()[0] == 200, 'HTTP admission recovers')
            process.terminate()
            check(process.wait(8) == 0, 'combined shutdown joins both thread sets')
            process.stdout.close()
            log.close()
    finally:
        rpc.release_all()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        rpc.close()
    result = {'passed': True, 'checks': checks, 'physical_rpcs': rpc.calls, 'peak_by_lane': rpc.peak,
              'peak_total': rpc.total_peak, 'elapsed_seconds': round(time.monotonic() - started, 3),
              'binary_sha256': hashlib.sha256((OUT / 'searchd-combined').read_bytes()).hexdigest(),
              'scope': 'Independently composed P2-7 HTTP executor plus P2-6 dispatcher, Linux Debug; synthetic loopback RPC only.'}
    (OUT / 'execution.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run()
