#!/usr/bin/env python3
"""Independent actual searchd -> HTTP Worker -> CoreClient/MCP receipt probe."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request

BODY = 'Independent wire response.'
PROFILE = hashlib.sha256(b'independent-wire-profile').hexdigest()
CHECKS = 0


def check(value, message):
    global CHECKS
    CHECKS += 1
    if not value:
        raise AssertionError(message)


class RPC:
    def __init__(self, token):
        self.token, self.calls, self.lock = token, [], threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path != '/rpc' or self.headers.get('Authorization') != 'Bearer ' + fixture.token:
                    self.send_error(403)
                    return
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method, params = request['method'], request['params']
                with fixture.lock:
                    fixture.calls.append({'method': method, 'params': params})
                if method == 'fetch.profile':
                    result = {'version': 'bzs-http-freshness-1', 'request_profile': PROFILE}
                    payload = {'result': result}
                elif method == 'fetch.extract':
                    target = params['url']
                    if target.endswith('unknown-error'):
                        payload = {'error': {'message': 'independent missing physical receipt'}}
                    else:
                        result = {'status': 'ok', 'http_status': 200, 'html': BODY, 'text': BODY,
                                  'title': 'Independent wire', 'content_type': 'text/plain',
                                  'extractor': 'independent-wire/1', 'extract_status': 'ok',
                                  'index_allowed': True, 'links': [], 'redirects': [], 'warnings': [],
                                  'metadata': {'fixture': 'independent wire'}, 'final_url': target,
                                  'usage': {'external_requests': 1, 'content_bytes': len(BODY.encode())},
                                  'http_freshness': {'version': 'bzs-http-freshness-1', 'url': target,
                                      'request_profile': PROFILE, 'cache_control': 'max-age=60',
                                      'vary': '', 'date': '', 'age': '', 'expires': '', 'pragma': '',
                                      'received_at_ms': int(time.time() * 1000), 'response_delay_ms': 0,
                                      'invalid': False}}
                        if target.endswith('known-error'):
                            result.update(status='network_error', http_status=503, error='independent known response failure')
                        if target.endswith('no-store'):
                            result['http_freshness']['cache_control'] = 'no-store'
                        if target.endswith('legacy'):
                            result.pop('usage')
                        payload = {'result': result}
                else:
                    payload = {'error': {'message': 'unexpected independent method'}}
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02))
        self.thread.start()

    def count(self):
        with self.lock:
            return sum(item['method'] == 'fetch.extract' for item in self.calls)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


def receipt(value, *, known=True, zero=False, shared=True):
    usage = value['usage']
    check(usage['scope'] == 'direct_read', 'usage scope')
    check(usage['known'] is known, 'known status')
    check(usage['model_calls'] == 0, 'no model execution')
    check(usage['external_requests'] == (0 if zero else 1) if known else usage['external_requests'] is None,
          'incremental request count')
    check(usage['content_bytes'] == (0 if zero else len(BODY.encode())) if known else usage['content_bytes'] is None,
          'incremental byte count')
    if shared:
        group = value['shared_read']
        check(group['scope'] == 'process' and group['role'] == 'payer' and group['members'] == 1,
              'single physical attempt role on wire')
        check(len(group['attempt_id']) == 48, 'attempt identifier')
        check(group['physical_usage']['known'] is known, 'physical known status')
        check(group['physical_usage']['external_requests'] == 1 if known else group['physical_usage']['external_requests'] is None,
              'physical request receipt')
    else:
        check('shared_read' not in value, 'cache hit must not invent a physical attempt')


async def scenarios(url, token, rpc):
    from bzs.core_client import CoreClient, CoreError
    from bzs import mcp_server
    from mcp.server.mcpserver.exceptions import ToolError

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    records = []
    failures = 0

    def raw(path, **extra):
        params = {'url': 'https://independent.example/' + path, 'freshness': 'refresh', **extra}
        payload = json.dumps({'operation': 'web.read', 'params': params}).encode()
        request = urllib.request.Request(url + '/api/v0/call', payload,
                                        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        try:
            with opener.open(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    async def record(name, action):
        nonlocal failures
        before, count = CHECKS, rpc.count()
        try:
            observed = await action()
            records.append({'case': name, 'passed': True, 'checks': CHECKS - before,
                            'fetches': rpc.count() - count, 'observation': observed})
        except Exception as error:
            failures += 1
            records.append({'case': name, 'passed': False, 'checks': CHECKS - before,
                            'fetches': rpc.count() - count, 'error': type(error).__name__ + ': ' + str(error)})

    async def http_success():
        status, value = raw('wire-ok')
        check(status == 200, 'successful HTTP status'); receipt(value['result'])
        check(value['usage'] == value['result']['usage'], 'envelope copies Core usage')
        check(value['result']['content']['text'] == BODY, 'returned content')
        check(value['result']['from_cache'] is False, 'network result is not a cache hit')
        return value

    async def http_cache_hit():
        before = rpc.count(); status, value = raw('wire-ok', freshness='cache_only')
        check(status == 200, 'cache-only HTTP status'); receipt(value['result'], zero=True, shared=False)
        check(value['usage'] == value['result']['usage'], 'cached envelope usage')
        check(rpc.count() == before, 'cache-only never origin fetches')
        return value

    async def http_failure(path, code, **extra):
        status, value = raw(path, **extra)
        check(status >= 400 and value['status'] == 'failed', 'HTTP failure preserved')
        check(value['errors'][0]['code'] == code, 'Core error code preserved')
        receipt(value)
        check('result' not in value, 'failure has no successful body')
        return value

    async def http_legacy():
        status, value = raw('wire-legacy')
        check(status == 200, 'legacy success'); receipt(value['result'], known=False)
        check(value['usage'] == value['result']['usage'], 'unknown envelope fields are preserved')
        return value

    client = CoreClient(url, token)

    async def core_client_failure():
        try:
            await client.call('web.read', {'url': 'https://independent.example/core-known-error', 'freshness': 'refresh'})
        except CoreError as error:
            check(error.code == 'E_FETCH_NETWORK', 'CoreClient preserves original error')
            receipt(error.details)
            return {'code': error.code, 'http_status': error.http_status, 'details': error.details}
        raise AssertionError('CoreClient swallowed a failure')

    os.environ['BZS_CORE_URL'] = url
    os.environ['SEARCH_API_TOKEN'] = token
    server = mcp_server.build_server()

    async def mcp_success(path, known=True, freshness='refresh'):
        result = await server.call_tool('read_url', {'url': 'https://independent.example/' + path,
                                                    'freshness': freshness, 'max_bytes': 11})
        check(not result.is_error, 'MCP success')
        value = result.structured_content
        receipt(value, known=known, zero=freshness == 'cache_only', shared=freshness != 'cache_only')
        check(value['text'] == BODY[:11], 'MCP per-caller window')
        check(value['content_trust'] == 'untrusted_web_content', 'MCP content trust retained')
        check('cache' in value, 'MCP retains Core cache explanation')
        return value

    async def mcp_failure(path, code, known):
        try:
            await server.call_tool('read_url', {'url': 'https://independent.example/' + path, 'freshness': 'refresh'})
        except ToolError as error:
            message, encoded = str(error).split('\n', 1)
            check(code in message, 'MCP keeps the Core error code')
            details = json.loads(encoded); receipt(details, known=known)
            check(encoded == json.dumps(details, ensure_ascii=False, separators=(',', ':')), 'MCP serializes intact Core receipt')
            return {'message': message, 'details': details}
        raise AssertionError('MCP swallowed a Core failure')

    await record('http_success_known_receipt', http_success)
    await record('http_cache_only_zero_without_attempt', http_cache_hit)
    await record('http_known_physical_failure_receipt', lambda: http_failure('wire-known-error', 'E_FETCH_NETWORK'))
    await record('http_local_window_failure_keeps_paid_receipt', lambda: http_failure('wire-window', 'E_EVIDENCE_RANGE', start_byte=1000000))
    await record('http_no_store_failure_keeps_paid_receipt', lambda: http_failure('wire-no-store', 'E_CACHE_POLICY'))
    await record('http_legacy_missing_receipt_stays_unknown', http_legacy)
    await record('real_core_client_failure_details', core_client_failure)
    await record('real_mcp_success_core_receipt', lambda: mcp_success('mcp-ok'))
    await record('real_mcp_cache_only_zero_without_attempt', lambda: mcp_success('mcp-ok', freshness='cache_only'))
    await record('real_mcp_legacy_unknown_not_zero', lambda: mcp_success('mcp-legacy', known=False))
    await record('real_mcp_known_failure_details', lambda: mcp_failure('mcp-known-error', 'E_FETCH_NETWORK', True))
    await record('real_mcp_unknown_failure_details', lambda: mcp_failure('mcp-unknown-error', 'E_WORKER', False))
    return {'probe': 'independent-direct-wire-mcp-v1', 'cases': len(records), 'checks': CHECKS,
            'failures': failures, 'results': records, 'fetches_total': rpc.count(),
            'limits': 'Actual native searchd, real loopback HTTP Worker, actual CoreClient and MCP tool dispatch. Worker is offline and synthetic; no origin, shared-concurrency, public cancellation, crash or full acceptance claim.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.runtime.exists(): raise SystemExit('Refusing to reuse existing runtime state')
    args.runtime.mkdir(parents=True)
    token, worker_token = secrets.token_hex(32), secrets.token_hex(32)
    auth = args.runtime / 'auth.json'; auth.write_text(json.dumps({'tokens': [{'sha256': hashlib.sha256(token.encode()).hexdigest(), 'subject': 'alice', 'admin': False}]})); auth.chmod(0o600)
    token_file = args.runtime / 'worker.token'; token_file.write_text(worker_token); token_file.chmod(0o600)
    sources = args.runtime / 'sources.json'; sources.write_text(json.dumps({'version': 1, 'sources': []}))
    fixtures = args.runtime / 'fixtures'; fixtures.mkdir()
    (fixtures / 'manifest.json').write_text(json.dumps({'source_id': 'independent-offline', 'kind': 'fixture', 'fixtures': []}))
    rpc = RPC(worker_token)
    command = [str(args.server.resolve()), '--auth', str(auth), '--state', str(args.runtime / 'state'),
               '--fixtures', str(fixtures), '--port', '0', '--threads', '2', '--request-threads', '4',
               '--worker', 'http://127.0.0.1:' + str(rpc.server.server_address[1]),
               '--worker-token-file', str(token_file), '--sources', str(sources)]
    process = None
    try:
        with (args.evidence / 'searchd-stderr.log').open('x', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8')
            ready = queue.Queue()
            reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()))
            reader.start()
            line = ready.get(timeout=15); reader.join(timeout=1)
            message = json.loads(line)
            with (args.evidence / 'searchd-start.json').open('x', encoding='utf-8') as saved:
                json.dump({'argv': command, 'ready': message}, saved, ensure_ascii=False, indent=2); saved.write('\n')
            report = asyncio.run(scenarios('http://127.0.0.1:' + str(message['port']), token, rpc))
            report['dependencies'] = {name: importlib.metadata.version(name) for name in ['mcp', 'aiohttp']}
            report['worker_calls'] = rpc.calls
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report['failures'] == 0 else 1
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            if process.stdout: process.stdout.close()
        rpc.close()


if __name__ == '__main__':
    raise SystemExit(main())
