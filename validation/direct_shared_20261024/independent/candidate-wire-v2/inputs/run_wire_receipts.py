#!/usr/bin/env python3
"""Freeze native server/Python adapter inputs and record real wire/MCP checks."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from run_candidate_native import HERE, REPO, digest, execute, production_paths, save, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--expected-server-sha256', required=True)
    args = parser.parse_args()
    if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.label): parser.error('Invalid evidence label')
    original = args.server.resolve()
    if digest(original) != args.expected_server_sha256: raise SystemExit('Server identity mismatch')
    folder = HERE / args.label
    runtime = Path('/tmp/bzs861-direct-independent-20261024/handoff-audit') / args.label
    if folder.exists() or runtime.exists(): raise SystemExit('Refusing to overwrite wire evidence')
    folder.mkdir(); runtime.mkdir(parents=True)
    frozen = folder / 'inputs'; frozen.mkdir()
    shutil.copytree(REPO / 'python/bzs', frozen / 'python/bzs', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    source = frozen / 'wire_receipt_probe.py'; shutil.copy2(HERE / source.name, source)
    shutil.copy2(Path(__file__).resolve(), frozen / 'run_wire_receipts.py')
    server = runtime / 'searchd'; shutil.copy2(original, server)
    if digest(server) != args.expected_server_sha256 or digest(original) != args.expected_server_sha256:
        raise SystemExit('Server changed during freeze')
    frozen_inputs = sorted(path for path in frozen.rglob('*') if path.is_file())
    product = production_paths()
    ldd = subprocess.check_output(['ldd', str(server)], text=True)
    linked = []
    for line in ldd.splitlines():
        parts = line.split()
        path = parts[2] if len(parts) >= 3 and parts[1] == '=>' else (parts[0] if parts else '')
        if path.startswith('/') and Path(path).is_file(): linked.append(Path(path).resolve())
    inputs = product + frozen_inputs + linked + [original, server, Path(__file__).resolve(), HERE / 'run_candidate_native.py', HERE / 'wire_receipt_probe.py']
    save(folder / 'frozen-inputs.json', {'server_sha256': digest(server), 'server': str(server),
                                      'production_inputs': snapshot(product), 'frozen_inputs': snapshot(frozen_inputs),
                                      'ldd': ldd, 'linked_libraries': snapshot(linked), 'python_executable': sys.executable,
                                      'scope': 'Actual native server binary plus frozen actual Python CoreClient/MCP modules and reviewer wire probe. Worker is an isolated loopback test double; no production configuration or source approval is changed.'})
    command = [sys.executable, str(source), '--server', str(server), '--runtime', str(runtime / 'fixtures'), '--evidence', str(folder)]
    result = execute(folder, 'run', command, inputs,
                     {'PYTHONPATH': str(frozen / 'python'), 'PYTHONDONTWRITEBYTECODE': '1'}, 120)
    observation = None
    try: observation = json.loads((folder / 'run.log').read_text())
    except (UnicodeError, json.JSONDecodeError): pass
    passed = result['execution_record_valid'] and observation is not None and observation.get('failures') == 0
    summary = {'status': 'passed_independent_wire_and_mcp' if passed else 'independent_wire_probe_failed',
               'cases': observation.get('cases') if observation else None,
               'checks': observation.get('checks') if observation else None,
               'failures': observation.get('failures') if observation else None,
               'failed_cases': [r['case'] for r in observation['results'] if not r['passed']] if observation else [],
               'server_sha256': digest(server), 'source_sha256': digest(source),
               'run_record_sha256': digest(folder / 'run-command.json'), 'run_log_sha256': digest(folder / 'run.log'),
               'inputs_unchanged': not result['changed_inputs'],
               'limits': 'Wire and thin-adapter evidence; does not add unique native sharing scenarios or full product acceptance.'}
    save(folder / 'summary.json', summary)
    save(folder / 'artifact-sha256.json', {str(path.relative_to(HERE)): {'sha256': digest(path), 'bytes': path.stat().st_size}
                                         for path in sorted(folder.rglob('*')) if path.is_file()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
