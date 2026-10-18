"""Real loopback HTTP/CLI tests. Never contacts an external search/model endpoint."""
from __future__ import annotations
import argparse
import hashlib
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
    def __init__(self, root: Path, no_worker: bool = False):
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
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.process = None
        self.start()

    def start(self):
        self.log = open(self.root / 'server.log', 'a', encoding='utf-8')
        cmd = [CONFIG.server, '--auth', str(self.auth), '--state', str(self.root / 'state'),
               '--fixtures', CONFIG.fixtures, '--port', '0']
        if self.no_worker:
            cmd.append('--no-worker')
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

    def call(self, op, params=None, *, token=None, raw=None, extra_headers=None):
        body = raw if raw is not None else json.dumps({'operation': op, 'params': params or {}}, ensure_ascii=False).encode()
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (self.alice if token is None else token)}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/v0/call', body, headers, method='POST')
        try:
            with self.opener.open(req, timeout=15) as response:
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

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', required=True)
    parser.add_argument('--cli', required=True)
    parser.add_argument('--fixtures', required=True)
    CONFIG = parser.parse_args()
    unittest.main(argv=['integration'], verbosity=2)
