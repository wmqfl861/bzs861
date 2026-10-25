"""Real loopback HTTP/CLI tests. Never contacts an external search/model endpoint."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

CONFIG = None

class RunningServer:
    def __init__(self, root: Path, no_worker: bool = False, *, extra_args=()):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.alice = secrets.token_hex(32)
        self.bob = secrets.token_hex(32)
        self.auth = root / 'auth.json'
        self.auth.write_text(json.dumps({'tokens': [
            {'sha256': hashlib.sha256(self.alice.encode()).hexdigest(), 'subject': 'alice', 'admin': True},
            {'sha256': hashlib.sha256(self.bob.encode()).hexdigest(), 'subject': 'bob', 'admin': False},
        ]}), encoding='utf-8')
        self.auth.chmod(0o600)
        self.no_worker = no_worker
        self.extra_args = list(extra_args)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.process = None
        self.start()

    def start(self):
        self.log = open(self.root / 'server.log', 'a', encoding='utf-8')
        cmd = [CONFIG.server, '--auth', str(self.auth), '--state', str(self.root / 'state'),
               '--fixtures', CONFIG.fixtures, '--port', '0']
        if self.no_worker:
            cmd.append('--no-worker')
        cmd.extend(self.extra_args)
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self.log, text=True, encoding='utf-8')
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True).start()
        try:
            line = ready.get(timeout=15)
            if not line:
                raise RuntimeError('Server exited before readiness: ' + (self.root / 'server.log').read_text())
            message = json.loads(line)
            self.port = message['port']
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.process.stdout:
                self.process.stdout.close()
        self.log.close()

    def call(self, op, params=None, *, token=None, raw=None, extra_headers=None, timeout=15):
        body = raw if raw is not None else json.dumps({'operation': op, 'params': params or {}}, ensure_ascii=False).encode()
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (self.alice if token is None else token)}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/v0/call', body, headers, method='POST')
        try:
            with self.opener.open(req, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def cli(self, *args, token=None):
        env = dict(os.environ, SEARCH_API_TOKEN=token or self.alice)
        return subprocess.run([CONFIG.cli, '--port', str(self.port), *args],
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=20)

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='bzs-http-')
        cls.server = RunningServer(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.tmp.cleanup()

    def ok(self, op, params=None, **kw):
        code, body = self.server.call(op, params, **kw)
        self.assertEqual(code, 200, body)
        self.assertIn(body['status'], ('success', 'partial'))
        self.assertEqual(body['usage'], {'external_requests': 0, 'model_calls': 0})
        return body['result']

    def ingest(self, content, suffix=None):
        return self.ok('document.ingest', {'url': 'https://integration.example/' + (suffix or secrets.token_hex(8)),
                    'content': content, 'content_type': 'text/plain', 'title': 'Integration fixture'})

    def test_01_health_cli(self):
        result = self.server.cli('health')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['result']['ready'])

    def test_02_auth_rejected(self):
        status, body = self.server.call('stats', token='invalid')
        self.assertEqual(status, 401)
        self.assertEqual(body['errors'][0]['code'], 'E_AUTH')

    def test_03_explicit_capabilities(self):
        caps = self.ok('capabilities')
        self.assertFalse(caps['production_ready'])
        self.assertEqual(caps['live_web_sources'], 0)
        self.assertNotIn('web.search', caps['operations'])
        status, body = self.server.call('web.search', {'query': 'hello'})
        self.assertEqual(status, 422)
        self.assertEqual(body['errors'][0]['code'], 'E_CAPABILITY_UNAVAILABLE')

    def test_04_strict_input(self):
        for raw in (b'{"operation":"stats","operation":"backup.create"}',
                    b'{"operation":"stats","params":{"x":NaN}}',
                    b'{"operation":"stats"} trailing'):
            status, _ = self.server.call('', raw=raw)
            self.assertEqual(status, 400)
        status, body = self.server.call('stats', {'owner': 'bob'})
        self.assertEqual(status, 400)
        self.assertEqual(body['errors'][0]['code'], 'E_SCHEMA')

    def test_05_origin_and_host(self):
        status, _ = self.server.call('stats', extra_headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(status, 403)
        status, _ = self.server.call('stats', extra_headers={'Host': 'untrusted.example'})
        self.assertEqual(status, 403)

    def test_06_real_chinese_roundtrip(self):
        doc = self.ingest('中文搜索😀 C++ std::expected')
        self.assertEqual(self.ok('document.read', {'snapshot_id': doc['snapshot_id']})['canonical_text'], '中文搜索😀 C++ std::expected')
        found = self.ok('find', {'snapshot_id': doc['snapshot_id'], 'text': '搜索😀'})['matches'][0]
        encoded = doc['canonical_text'].encode()
        self.assertEqual(encoded[found['start_byte']:found['end_byte']].decode(), found['quote'])
        ev = self.ok('evidence.create', {'snapshot_id': doc['snapshot_id'], 'start_byte': found['start_byte'], 'end_byte': found['end_byte']})
        self.assertEqual(self.ok('evidence.read', {'evidence_id': ev['evidence_id']})['quote'], '搜索😀')

    def test_07_owner_isolation(self):
        doc = self.ingest('OwnerIsolationCanary')
        status, _ = self.server.call('document.read', {'snapshot_id': doc['snapshot_id']}, token=self.server.bob)
        self.assertEqual(status, 404)
        self.assertEqual(self.ok('search', {'query': 'OwnerIsolationCanary'}, token=self.server.bob)['frozen_count'], 0)

    def test_08_update_and_delete(self):
        doc = self.ingest('BeforeUpdateCanary', 'update')
        new = self.ingest('AfterUpdateCanary', 'update')
        self.assertNotEqual(doc['snapshot_id'], new['snapshot_id'])
        self.assertEqual(self.ok('document.read', {'snapshot_id': doc['snapshot_id']})['canonical_text'], 'BeforeUpdateCanary')
        self.assertEqual(self.ok('search', {'query': 'BeforeUpdateCanary'})['frozen_count'], 0)
        session = self.ok('search', {'query': 'AfterUpdateCanary'})
        self.ok('document.delete', {'document_id': new['document_id']})
        # Per-document tombstone: the old session stays valid but cannot return deleted content.
        page = self.ok('search.page', {'session_id': session['session_id']})
        self.assertEqual(page['hits'], [])
        status, _ = self.server.call('document.read', {'snapshot_id': new['snapshot_id']})
        self.assertEqual(status, 404)

    def test_09_fixture_search_and_ingest_cli(self):
        result = self.server.cli('fixture-search', 'C++')
        self.assertEqual(result.returncode, 0, result.stderr)
        hits = json.loads(result.stdout)['result']['hits']
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]['body_read'])
        result = self.server.cli('fixture-ingest', hits[0]['fixture_id'])
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)['result']
        self.assertIn('std::expected<int, Error>', doc['canonical_text'])
        self.assertNotIn('evil_script_content', doc['canonical_text'])

    def test_10_job_execution_and_idempotency(self):
        params = {'fixture_id': 'plain', 'idempotency_key': 'integration-job'}
        job = self.ok('job.create', params)
        same = self.ok('job.create', params)
        self.assertEqual(job['job_id'], same['job_id'])
        native = self.server.cli('get-job', job['job_id'])
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(json.loads(native.stdout)['result']['job_id'], job['job_id'])
        until = time.monotonic() + 8
        while time.monotonic() < until:
            state = self.ok('job.get', {'job_id': job['job_id']})
            if state['state'] in ('succeeded', 'failed'):
                break
            time.sleep(0.04)
        self.assertEqual(state['state'], 'succeeded', state)
        self.assertEqual(state['used_pages'], 1)
        self.assertNotIn('canonical_text', state['result'])
        status, _ = self.server.call('job.create', dict(params, fixture_id='developer'))
        self.assertEqual(status, 409)

    def test_11_pagination_freeze(self):
        for i in range(3):
            self.ingest('PaginationCanary ' + str(i))
        result = self.ok('search', {'query': 'PaginationCanary', 'page_size': 1})
        ids = [result['page']['hits'][0]['document_id']]
        cursor = result['page']['next_cursor']
        self.ingest('PaginationCanary late')
        while cursor:
            page = self.ok('search.page', {'session_id': result['session_id'], 'cursor': cursor, 'page_size': 1})
            ids.extend(hit['document_id'] for hit in page['hits'])
            cursor = page['next_cursor']
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    def test_12_oversized_content(self):
        status, body = self.server.call('document.ingest', {'url': 'https://integration.example/large',
            'content_type': 'text/plain', 'content': 'x' * (1024 * 1024 + 1)})
        self.assertEqual(status, 400)
        self.assertEqual(body['errors'][0]['code'], 'E_SCHEMA')

    def test_13_slow_header_deadline(self):
        started = time.monotonic()
        with socket.create_connection(('127.0.0.1', self.server.port), timeout=3) as sock:
            sock.settimeout(8)
            sock.sendall(f'GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\n'.encode())
            try:
                received = sock.recv(1)
            except ConnectionResetError:
                # Windows can report the server's deadline close as TCP reset.
                received = b''
            self.assertEqual(received, b'')
            self.assertGreaterEqual(time.monotonic() - started, 4)

    def test_14_backup_admin_and_quarantine(self):
        status, _ = self.server.call('backup.create', token=self.server.bob)
        self.assertEqual(status, 403)
        result = self.ok('backup.create')
        self.assertEqual(result['restore_state'], 'quarantined')
        self.assertFalse(result['includes_credentials'])
        self.assertTrue(result['contains_private_content'])

    def test_15_cli_ingest_and_query(self):
        path = Path(self.tmp.name) / 'local.txt'
        path.write_text('CliImportCanary 中文资料', encoding='utf-8')
        result = self.server.cli('ingest', str(path), 'https://integration.example/cli')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.server.cli('search', 'CliImportCanary')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['frozen_count'], 1)

    def test_16_restart_persistence_and_controls(self):
        with tempfile.TemporaryDirectory(prefix='bzs-restart-') as tmp:
            server = RunningServer(Path(tmp), no_worker=True)
            try:
                status, body = server.call('fixture.ingest', {'fixture_id': 'plain'})
                self.assertEqual(status, 200)
                snapshot = body['result']['snapshot_id']
                status, body = server.call('job.create', {'fixture_id': 'developer', 'idempotency_key': 'control'})
                job = body['result']['job_id']
                status, body = server.call('job.control', {'job_id': job, 'action': 'pause'})
                self.assertEqual(body['result']['state'], 'paused')
                server.stop()
                server.start()
                status, body = server.call('document.read', {'snapshot_id': snapshot})
                self.assertEqual(status, 200)
                self.assertIn('SQLite', body['result']['canonical_text'])
                status, body = server.call('job.get', {'job_id': job})
                self.assertEqual(body['result']['state'], 'paused')
                status, body = server.call('job.control', {'job_id': job, 'action': 'cancel'})
                self.assertEqual(body['result']['state'], 'cancelled')
            finally:
                server.stop()

    def test_17_invalid_search_options_preserve_session_quota(self):
        with tempfile.TemporaryDirectory(prefix='bzs-search-validation-') as tmp:
            server = RunningServer(Path(tmp), no_worker=True)
            try:
                status, body = server.call('fixture.ingest', {'fixture_id': 'plain'})
                self.assertEqual(status, 200, body)
                invalid_sizes = (0, 101, -1, '1', None, True, 1.5, {}, [])
                # A rejected request must not consume any of the 64 session slots.
                for i in range(64):
                    size = invalid_sizes[i % len(invalid_sizes)]
                    with self.subTest(page_size=size, attempt=i):
                        status, body = server.call('search', {'query': 'SQLite', 'page_size': size})
                        self.assertEqual(status, 400, body)
                        self.assertEqual(body['errors'][0]['code'], 'E_SCHEMA')
                status, body = server.call('search', {'query': 'SQLite', 'page_size': 1})
                self.assertEqual(status, 200, body)
                self.assertEqual(body['result']['frozen_count'], 1)
                self.assertEqual(len(body['result']['page']['hits']), 1)
            finally:
                server.stop()

    def no_model_contract(self):
        return {'policy': {'allow_reference_backends': False,
                           'external_nonmodel_services': 'reviewed_free_only',
                           'allow_provider_hosted_search': False,
                           'allow_unconfigured_fallback': False},
                'default_text_profile': None, 'profiles': {}, 'roles': {}}

    def test_18_contract_validation_and_trusted_parameters(self):
        before = self.ok('stats')
        config = self.no_model_contract()
        params = {'kind': 'model-config', 'config': config}
        result = self.ok('contract.validate', params)
        self.assertEqual(result['kind'], 'model-config')
        self.assertTrue(result['validated'])
        self.assertFalse(result['applied'])
        effective = result['effective_config']
        self.assertIsNone(effective['default_text_profile'])
        self.assertEqual(effective['profiles'], {})
        self.assertEqual(effective['text_fallback_profiles'], [])
        self.assertEqual(effective['effective_roles'], dict.fromkeys(
            ('planner', 'extractor', 'verifier', 'writer', 'embedder', 'reranker', 'vision', 'asr')))
        for extra in ({'now_ms': 0}, {'validation_now_ms': 0}, {'limits': {'model_concurrency': 100000}}):
            with self.subTest(extra=extra):
                status, body = self.server.call('contract.validate', dict(params, **extra))
                self.assertEqual(status, 400, body)
                self.assertEqual(body['errors'][0]['code'], 'E_SCHEMA')
        self.assertEqual(self.ok('stats'), before)

    def test_19_validation_does_not_enable_execution(self):
        before_stats = self.ok('stats')
        before_caps = self.ok('capabilities')
        self.assertIn('contract.validate', before_caps['operations'])
        model = self.no_model_contract()
        model['default_text_profile'] = 'test-model'
        model['profiles']['test-model'] = {
            'kind': 'generation', 'protocol_adapter': 'openai_chat',
            'base_url': 'https://model-provider.invalid/v1', 'model': 'test-model',
            'credential_ref': 'env:BZS_CONTRACT_TEST_UNUSED_KEY', 'capabilities': ['text']}
        model_result = self.ok('contract.validate', {'kind': 'model-config', 'config': model})
        self.assertTrue(model_result['validated'])
        self.assertFalse(model_result['applied'])
        self.assertEqual(model_result['effective_config']['effective_roles']['planner']['profile'], 'test-model')
        current = time.time()
        source = {'id': 'validated-source', 'provider_id': 'test-provider', 'index_family': 'test-index',
                  'capability': 'web', 'endpoint': 'https://source.invalid/search', 'adapter_id': 'test-adapter',
                  'review': {'status': 'approved', 'payment_scheme': 'no_payment', 'automated_access': 'approved',
                             'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(current - 86400)),
                             'expires_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(current + 86400)),
                             'terms_url': 'https://source.invalid/terms', 'approved_by': 'test-fixture'},
                  'limits': {'max_concurrency': 1, 'min_interval_ms': 1000, 'shared_budget_key': 'test-fixture'},
                  'enabled': True, 'logical_service_id': 'validated-source-service'}
        source_result = self.ok('contract.validate', {'kind': 'source-manifest', 'config': source})
        self.assertTrue(source_result['validated'])
        self.assertFalse(source_result['applied'])
        self.assertTrue(source_result['effective_config']['enabled'])
        source['review'].update(checked_at='2010-01-01T00:00:00Z', expires_at='2011-01-01T00:00:00Z')
        status, body = self.server.call('contract.validate', {'kind': 'source-manifest', 'config': source})
        self.assertEqual(status, 400, body)
        self.assertEqual(body['errors'][0]['code'], 'E_CONTRACT')
        search = {'query': 'contract validation', 'scope': {'corpus': 'external'},
                  'mode': 'fast', 'constraint_mode': 'strict', 'network_mode': 'normal',
                  'budget': {'max_source_requests': 1, 'max_reads': 0, 'max_model_calls': 0, 'timeout_ms': 1000}}
        search_result = self.ok('contract.validate', {'kind': 'search-request', 'config': search})
        self.assertTrue(search_result['validated'])
        self.assertFalse(search_result['applied'])
        self.assertEqual(search_result['effective_config']['network_mode'], 'normal')
        after_caps = self.ok('capabilities')
        self.assertEqual(after_caps, before_caps)
        self.assertEqual(after_caps['external_network'], 'disabled')
        self.assertEqual(after_caps['live_web_sources'], 0)
        self.assertIn('models', after_caps['not_implemented'])
        for operation in ('web.search', 'model.call'):
            status, body = self.server.call(operation, {})
            self.assertEqual(status, 422, body)
            self.assertEqual(body['errors'][0]['code'], 'E_CAPABILITY_UNAVAILABLE')
        self.assertEqual(self.ok('stats'), before_stats)

    def test_20_cli_contract_validation_and_file_limit(self):
        before = self.ok('stats')
        config = self.no_model_contract()
        expected = self.ok('contract.validate', {'kind': 'model-config', 'config': config})
        path = Path(self.tmp.name) / 'model-contract.json'
        encoded = json.dumps(config).encode('utf-8')
        path.write_bytes(encoded + b' ' * (65536 - len(encoded)))
        result = self.server.cli('validate', 'model-config', str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], expected)
        self.assertEqual(json.loads(result.stdout)['usage'], {'external_requests': 0, 'model_calls': 0})
        denied = self.server.cli('validate', 'model-config', str(path), token='invalid-contract-validation-token' * 2)
        self.assertEqual(denied.returncode, 1, denied.stderr)
        self.assertEqual(json.loads(denied.stdout)['errors'][0]['code'], 'E_AUTH')
        path.write_bytes(path.read_bytes() + b' ')
        oversized = self.server.cli('validate', 'model-config', str(path))
        self.assertEqual(oversized.returncode, 2, oversized.stderr)
        self.assertIn('E_CONTENT_TOO_LARGE', oversized.stderr)
        self.assertEqual(self.ok('stats'), before)


class HeldWorker:
    """A real loopback RPC endpoint held until the test explicitly releases it."""

    def __init__(self, response=None):
        self.release = threading.Event()
        self.condition = threading.Condition()
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.direct_cancellations = []
        self.request_records = []
        self.response = response
        self.profile = hashlib.sha256(b'http-direct-read-receipt-fixture').hexdigest()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, result):
                body = json.dumps({'result': result}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    self.send_error(413)
                    return
                request = json.loads(self.rfile.read(size))
                if self.path != '/rpc':
                    self.send_error(400)
                    return
                if request['method'] == 'fetch.profile':
                    self.reply({'version': 'bzs-http-freshness-1', 'request_profile': fixture.profile})
                    return
                if request['method'] == 'fetch.cancel_direct':
                    with fixture.condition:
                        fixture.direct_cancellations.append(request['params']['control_id'])
                        fixture.condition.notify_all()
                    self.reply({'cancel_requested': True})
                    return
                if request['method'] not in ('fetch.extract', 'fetch.extract_direct'):
                    self.send_error(400)
                    return
                with fixture.condition:
                    fixture.calls += 1
                    fixture.request_records.append(request)
                    fixture.active += 1
                    fixture.peak = max(fixture.peak, fixture.active)
                    fixture.condition.notify_all()
                try:
                    if not fixture.release.wait(15):
                        self.send_error(504)
                        return
                    result = (fixture.response(request['params'], fixture.profile) if fixture.response else
                              {'status': 'timeout', 'error': 'released test RPC'})
                    self.reply(result)
                except (BrokenPipeError, ConnectionError):
                    pass
                finally:
                    with fixture.condition:
                        fixture.active -= 1
                        fixture.condition.notify_all()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    def wait_calls(self, count):
        with self.condition:
            return self.condition.wait_for(lambda: self.calls >= count, timeout=5)

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class HTTPExecution(unittest.TestCase):
    def start_held(self, *, request_threads=None):
        temp = tempfile.TemporaryDirectory(prefix='bzs-http-executor-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        worker = HeldWorker()
        self.addCleanup(worker.close)
        token = root / 'worker.token'
        token.write_text(secrets.token_hex(32), encoding='utf-8')
        token.chmod(0o600)
        sources = root / 'sources.json'
        sources.write_text(json.dumps({'version': 1, 'sources': []}), encoding='utf-8')
        args = ['--threads', '1', '--worker', f'http://127.0.0.1:{worker.port}',
                '--worker-token-file', str(token), '--sources', str(sources)]
        if request_threads is not None:
            args.extend(['--request-threads', str(request_threads)])
        server = RunningServer(root, no_worker=True, extra_args=args)
        # Release RPCs before joining the core, including assertion failures.
        self.addCleanup(server.stop)
        self.addCleanup(worker.release.set)
        return server, worker

    def health_while_held(self, server, worker):
        self.assertFalse(worker.release.is_set())
        with server.opener.open(f'http://127.0.0.1:{server.port}/healthz', timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.load(response)['result']['ready'])
        self.assertFalse(worker.release.is_set(), 'health must finish before any business RPC is released')

    def test_saturated_requests_leave_health_and_transport_checks_live(self):
        server, worker = self.start_held()
        with ThreadPoolExecutor(max_workers=1) as clients:
            pending = clients.submit(server.call, 'web.read', {'url': 'https://executor.example/held', 'timeout_ms': 10000})
            try:
                self.assertTrue(worker.wait_calls(1), 'business request must reach the held RPC')
                self.health_while_held(server, worker)
                status, body = server.call('document.ingest', {'url': 'https://executor.example/not-admitted',
                                          'content': 'must not be committed', 'content_type': 'text/plain'}, timeout=3)
                self.assertEqual(status, 503, body)
                self.assertEqual(body['errors'][0]['code'], 'E_REQUEST_BACKPRESSURE')
                status, body = server.call('stats', token='invalid', timeout=3)
                self.assertEqual(status, 401, body)
                self.assertEqual(body['errors'][0]['code'], 'E_AUTH')
                status, body = server.call('stats', extra_headers={'Origin': 'https://untrusted.example'}, timeout=3)
                self.assertEqual(status, 403, body)
                self.assertEqual(body['errors'][0]['code'], 'E_ORIGIN')
                self.assertEqual(worker.calls, 1, 'rejected work must not be dispatched or retried')
            finally:
                worker.release.set()
            status, body = pending.result(timeout=5)
            self.assertEqual(status, 504, body)
            self.assertEqual(body['errors'][0]['code'], 'E_FETCH_TIMEOUT')
        status, body = server.call('stats')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['result']['documents'], 0, '503 must have no business side effects')

    def test_explicit_business_limit_runs_concurrently_on_one_io_thread(self):
        server, worker = self.start_held(request_threads=2)
        with ThreadPoolExecutor(max_workers=2) as clients:
            pending = [clients.submit(server.call, 'web.read', {'url': f'https://executor.example/{i}', 'timeout_ms': 10000})
                       for i in range(2)]
            try:
                self.assertTrue(worker.wait_calls(2), 'both RPCs must start before either is released')
                self.health_while_held(server, worker)
                status, body = server.call('web.read', {'url': 'https://executor.example/excess'}, timeout=3)
                self.assertEqual(status, 503, body)
                self.assertEqual(body['errors'][0]['code'], 'E_REQUEST_BACKPRESSURE')
                self.assertEqual(worker.calls, 2)
                self.assertEqual(worker.peak, 2)
            finally:
                worker.release.set()
            for result in pending:
                self.assertEqual(result.result(timeout=5)[0], 504)
        self.assertEqual(server.call('stats')[0], 200, 'completed requests must return their admission slots')

    def test_request_thread_limit_is_validated_before_listening(self):
        for count in ('0', '33', '-1', 'two', '2x'):
            with self.subTest(count=count):
                result = subprocess.run([CONFIG.server, '--request-threads', count], capture_output=True,
                                        text=True, encoding='utf-8', timeout=5)
                self.assertEqual(result.returncode, 2)
                self.assertIn('E_ARGUMENT: Invalid request thread count', result.stderr)
                self.assertEqual(result.stdout, '')


class DirectReadReceipts(unittest.TestCase):
    """Real HTTP serialization of Core-owned direct-read execution receipts."""

    body_text = 'HTTP receipt 中文 body'
    failed_body = 'partial 上游 bytes'

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bzs-http-direct-receipts-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.worker = HeldWorker(response=self.worker_response)
        self.addCleanup(self.worker.close)
        token = root / 'worker.token'
        token.write_text(secrets.token_hex(32), encoding='utf-8')
        token.chmod(0o600)
        sources = root / 'sources.json'
        sources.write_text(json.dumps({'version': 1, 'sources': []}), encoding='utf-8')
        self.server = RunningServer(root, no_worker=True, extra_args=[
            '--threads', '1', '--request-threads', '1',
            '--worker', f'http://127.0.0.1:{self.worker.port}',
            '--worker-token-file', str(token), '--sources', str(sources)])
        self.addCleanup(self.server.stop)
        self.addCleanup(self.worker.release.set)
        # Admission concurrency is covered by the native handshake tests.
        # These sequential requests exercise actual RPC/HTTP envelopes only.
        self.worker.release.set()

    def worker_response(self, params, profile):
        url = params['url']
        if url.endswith('/known-failure'):
            return {'status': 'network_error', 'error': 'known physical failure',
                    'usage': {'external_requests': 1, 'content_bytes': len(self.failed_body.encode('utf-8'))}}
        if url.endswith('/unknown-failure'):
            return {'status': 'network_error', 'error': 'physical usage was not reported'}
        return {
            'status': 'ok', 'http_status': 200, 'final_url': url,
            'html': self.body_text, 'text': self.body_text, 'title': 'HTTP receipt fixture',
            'content_type': 'text/plain', 'extractor': 'http-receipt-fixture/1',
            'extract_status': 'ok', 'index_allowed': True, 'links': [], 'redirects': [],
            'warnings': [], 'metadata': {},
            'usage': {'external_requests': 1, 'content_bytes': len(self.body_text.encode('utf-8'))},
            'http_freshness': {
                'version': 'bzs-http-freshness-1', 'url': url, 'request_profile': profile,
                'cache_control': 'max-age=3600', 'vary': '', 'date': '', 'age': '',
                'expires': '', 'pragma': '', 'received_at_ms': time.time_ns() // 1_000_000,
                'response_delay_ms': 0, 'invalid': False}}

    @staticmethod
    def expected_usage(requests, content_bytes, known=True):
        return {'scope': 'direct_read', 'external_requests': requests,
                'content_bytes': content_bytes, 'model_calls': 0, 'known': known}

    def assert_payer_receipt(self, record, requests, content_bytes, known=True):
        self.assertEqual(record['usage'], self.expected_usage(requests, content_bytes, known))
        self.assertIs(record['usage']['known'], known)
        receipt = record['shared_read']
        self.assertRegex(receipt['attempt_id'], r'\A[0-9a-f]{48}\Z')
        self.assertEqual(receipt['role'], 'payer')
        self.assertEqual(receipt['scope'], 'process')
        self.assertEqual(receipt['members'], 1)
        self.assertEqual(receipt['physical_usage'], {
            'external_requests': requests, 'content_bytes': content_bytes, 'known': known})
        self.assertIs(receipt['physical_usage']['known'], known)

    def test_success_and_cache_hit_preserve_direct_usage_and_receipt(self):
        url = 'https://receipt.example/success'
        status, envelope = self.server.call('web.read', {'url': url, 'freshness': 'refresh'})
        self.assertEqual(status, 200, envelope)
        self.assertEqual(envelope['status'], 'success')
        self.assertEqual(envelope['errors'], [])
        result = envelope['result']
        self.assertFalse(result['from_cache'])
        self.assertEqual(result['content']['text'], self.body_text)
        self.assert_payer_receipt(result, 1, len(self.body_text.encode('utf-8')))
        self.assertEqual(envelope['usage'], result['usage'], 'HTTP must preserve Core accounting')
        self.assertEqual(self.worker.calls, 1)

        status, cached = self.server.call('web.read', {'url': url, 'freshness': 'cache_only'})
        self.assertEqual(status, 200, cached)
        self.assertEqual(cached['status'], 'success')
        self.assertEqual(cached['errors'], [])
        self.assertTrue(cached['result']['from_cache'])
        self.assertEqual(cached['result']['snapshot_id'], result['snapshot_id'])
        self.assertEqual(cached['result']['content']['text'], self.body_text)
        self.assertEqual(cached['usage'], self.expected_usage(0, 0))
        self.assertEqual(cached['result']['usage'], cached['usage'])
        self.assertNotIn('shared_read', cached['result'])
        self.assertNotIn('shared_read', cached)
        self.assertEqual(self.worker.calls, 1, 'cache_only must not fetch the page again')

    def test_window_error_remains_a_failure_with_its_paid_execution_receipt(self):
        url = 'https://receipt.example/bad-window'
        status, envelope = self.server.call('web.read', {
            'url': url, 'freshness': 'refresh', 'start_byte': 10000})
        self.assertEqual(status, 400, envelope)
        self.assertEqual(envelope['status'], 'failed')
        self.assertEqual(len(envelope['errors']), 1)
        self.assertEqual(envelope['errors'][0]['code'], 'E_EVIDENCE_RANGE')
        self.assertNotIn('result', envelope)
        self.assert_payer_receipt(envelope, 1, len(self.body_text.encode('utf-8')))
        self.assertEqual(self.worker.calls, 1, 'a failed output window must not retry the fetch')

        status, cached = self.server.call('web.read', {'url': url, 'freshness': 'cache_only'})
        self.assertEqual(status, 200, cached)
        self.assertEqual(cached['result']['content']['text'], self.body_text)
        self.assertEqual(cached['usage'], self.expected_usage(0, 0))
        self.assertNotIn('shared_read', cached['result'])
        self.assertEqual(self.worker.calls, 1)

    def test_worker_failures_preserve_known_and_unknown_physical_usage(self):
        attempts = set()
        for number, known in enumerate((True, False), start=1):
            with self.subTest(known=known):
                path = 'known-failure' if known else 'unknown-failure'
                status, envelope = self.server.call('web.read', {
                    'url': 'https://receipt.example/' + path, 'freshness': 'refresh'})
                self.assertEqual(status, 502, envelope)
                self.assertEqual(envelope['status'], 'failed')
                self.assertEqual(len(envelope['errors']), 1)
                self.assertEqual(envelope['errors'][0]['code'], 'E_FETCH_NETWORK')
                self.assertNotIn('result', envelope)
                self.assert_payer_receipt(envelope, 1 if known else None,
                                          len(self.failed_body.encode('utf-8')) if known else None, known)
                attempts.add(envelope['shared_read']['attempt_id'])
                self.assertEqual(self.worker.calls, number, 'failure cannot cause an implicit second fetch')
        self.assertEqual(len(attempts), 2)
        status, stats = self.server.call('stats')
        self.assertEqual(status, 200, stats)
        self.assertEqual(stats['result']['snapshots'], 0)


class DirectReadCancellation(unittest.TestCase):
    """A separate HTTP call controls a held synchronous ordinary read."""

    body_text = DirectReadReceipts.body_text
    failed_body = DirectReadReceipts.failed_body
    worker_response = DirectReadReceipts.worker_response
    expected_usage = staticmethod(DirectReadReceipts.expected_usage)
    assert_payer_receipt = DirectReadReceipts.assert_payer_receipt

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bzs-http-direct-cancel-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.worker = HeldWorker(response=self.worker_response)
        self.addCleanup(self.worker.close)
        token = root / 'worker.token'
        token.write_text(secrets.token_hex(32), encoding='utf-8')
        token.chmod(0o600)
        sources = root / 'sources.json'
        sources.write_text(json.dumps({'version': 1, 'sources': []}), encoding='utf-8')
        self.server = RunningServer(root, no_worker=True, extra_args=[
            '--threads', '1', '--request-threads', '3',
            '--worker', f'http://127.0.0.1:{self.worker.port}',
            '--worker-token-file', str(token), '--sources', str(sources)])
        self.addCleanup(self.server.stop)
        self.addCleanup(self.worker.release.set)

    def prepare(self):
        status, body = self.server.call('web.read.prepare')
        self.assertEqual(status, 200, body)
        prepared = body['result']
        self.assertEqual(prepared['state'], 'prepared')
        self.assertEqual(prepared['scope'], 'process')
        self.assertEqual(prepared['expires_in_ms'], 120000)
        self.assertRegex(prepared['read_id'], r'\A[0-9a-f]{48}\Z')
        return prepared['read_id']

    def cancel(self, read_id, *, physical, accepted=True):
        status, body = self.server.call('web.read.cancel', {'read_id': read_id})
        self.assertEqual(status, 200, body)
        response = body['result']
        self.assertEqual(response['read_id'], read_id)
        self.assertEqual(response['state'], 'cancelled' if accepted else 'completed')
        self.assertIs(response['cancel_requested'], accepted)
        self.assertIs(response['physical_cancel_requested'], physical)
        return response

    def test_prepare_cancel_owner_isolation_and_one_time_claim_survive_http(self):
        status, caps = self.server.call('capabilities')
        self.assertEqual(status, 200, caps)
        self.assertIn('web.read.prepare', caps['result']['operations'])
        self.assertIn('web.read.cancel', caps['result']['operations'])
        read_id = self.prepare()
        for operation, params in (
            ('web.read.cancel', {'read_id': read_id}),
            ('web.read', {'url': 'https://cancel.example/prepared', 'read_id': read_id}),
        ):
            with self.subTest(operation=operation):
                status, denied = self.server.call(operation, params, token=self.server.bob)
                self.assertEqual(status, 404, denied)
                self.assertEqual(denied['errors'][0]['code'], 'E_NOT_FOUND')
        self.cancel(read_id, physical=False)
        self.cancel(read_id, physical=False)
        params = {'url': 'https://cancel.example/prepared', 'freshness': 'refresh', 'read_id': read_id}
        status, cancelled = self.server.call('web.read', params)
        self.assertEqual(status, 409, cancelled)
        self.assertEqual(cancelled['errors'][0]['code'], 'E_CANCELLED')
        self.assertNotIn('result', cancelled)
        self.assertNotIn('shared_read', cancelled)
        self.assertEqual(cancelled['usage'], self.expected_usage(0, 0))
        status, replay = self.server.call('web.read', params)
        self.assertEqual(status, 409, replay)
        self.assertEqual(replay['errors'][0]['code'], 'E_READ_STATE')
        self.assertEqual(self.worker.calls, 0)
        self.assertEqual(self.worker.direct_cancellations, [])

    def test_live_cancel_keeps_original_paid_receipt_and_uses_direct_control_rpc(self):
        read_id = self.prepare()
        with ThreadPoolExecutor(max_workers=1) as clients:
            pending = clients.submit(self.server.call, 'web.read', {
                'url': 'https://cancel.example/held', 'freshness': 'refresh',
                'timeout_ms': 10000, 'read_id': read_id})
            try:
                self.assertTrue(self.worker.wait_calls(1))
                status, denied = self.server.call('web.read.cancel', {'read_id': read_id}, token=self.server.bob)
                self.assertEqual(status, 404, denied)
                self.assertEqual(self.worker.direct_cancellations, [])
                self.cancel(read_id, physical=True)
                self.cancel(read_id, physical=False)
                self.assertFalse(pending.done(), 'payer still owns the held physical execution')
                self.assertEqual(len(self.worker.direct_cancellations), 1)
                request = self.worker.request_records[0]
                self.assertEqual(request['method'], 'fetch.extract_direct')
                control_id = request['params']['control_id']
                self.assertRegex(control_id, r'\A[0-9a-f]{48}\Z')
                self.assertEqual(self.worker.direct_cancellations, [control_id])
                for key in ('attempt_id', 'max_requests', 'max_content_bytes'):
                    self.assertNotIn(key, request['params'])
            finally:
                self.worker.release.set()
            status, cancelled = pending.result(timeout=5)
        self.assertEqual(status, 409, cancelled)
        self.assertEqual(cancelled['errors'][0]['code'], 'E_CANCELLED')
        self.assertNotIn('result', cancelled)
        self.assert_payer_receipt(cancelled, 1, len(self.body_text.encode('utf-8')))
        self.assertEqual(cancelled['shared_read']['attempt_id'], control_id)
        self.assertEqual(self.worker.calls, 1)
        self.cancel(read_id, physical=False)

    def test_completed_read_handle_cannot_cancel_or_replay_a_later_attempt(self):
        self.worker.release.set()
        first = self.prepare()
        params = {'url': 'https://cancel.example/completed', 'freshness': 'refresh', 'read_id': first}
        status, completed = self.server.call('web.read', params)
        self.assertEqual(status, 200, completed)
        self.assert_payer_receipt(completed['result'], 1, len(self.body_text.encode('utf-8')))
        self.cancel(first, physical=False, accepted=False)
        status, replay = self.server.call('web.read', params)
        self.assertEqual(status, 409, replay)
        self.assertEqual(replay['errors'][0]['code'], 'E_READ_STATE')
        second = self.prepare()
        self.assertNotEqual(first, second)
        params['read_id'] = second
        status, next_result = self.server.call('web.read', params)
        self.assertEqual(status, 200, next_result)
        self.assertNotEqual(completed['result']['shared_read']['attempt_id'],
                            next_result['result']['shared_read']['attempt_id'])
        self.cancel(first, physical=False, accepted=False)
        self.assertEqual(self.worker.calls, 2)
        self.assertEqual(self.worker.direct_cancellations, [])

    def test_cancel_is_subject_to_existing_http_business_backpressure(self):
        self.server.stop()
        index = self.server.extra_args.index('--request-threads')
        self.server.extra_args[index + 1] = '1'
        self.server.start()
        read_id = self.prepare()
        with ThreadPoolExecutor(max_workers=1) as clients:
            pending = clients.submit(self.server.call, 'web.read', {
                'url': 'https://cancel.example/saturated', 'freshness': 'refresh',
                'timeout_ms': 10000, 'read_id': read_id})
            try:
                self.assertTrue(self.worker.wait_calls(1))
                status, denied = self.server.call('web.read.cancel', {'read_id': read_id}, timeout=3)
                self.assertEqual(status, 503, denied)
                self.assertEqual(denied['errors'][0]['code'], 'E_REQUEST_BACKPRESSURE')
                self.assertEqual(self.worker.direct_cancellations, [])
                with self.server.opener.open(f'http://127.0.0.1:{self.server.port}/healthz', timeout=3) as response:
                    self.assertEqual(response.status, 200)
            finally:
                self.worker.release.set()
            status, completed = pending.result(timeout=5)
        self.assertEqual(status, 200, completed)
        self.assertEqual(completed['result']['content']['text'], self.body_text)
        self.cancel(read_id, physical=False, accepted=False)
        self.assertEqual(self.worker.calls, 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', required=True)
    parser.add_argument('--cli', required=True)
    parser.add_argument('--fixtures', required=True)
    CONFIG = parser.parse_args()
    unittest.main(argv=['integration'], verbosity=2)
