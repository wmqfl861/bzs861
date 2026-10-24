"""Bind final root inputs and run unchanged independent probes on copied binaries."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent
binding = json.loads((OUT / 'binding-before.json').read_bytes())
ROOT = Path(binding['root'])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


commands = []
for name, script in (('http', 'review.py'), ('dispatch', 'probe.py')):
    folder = OUT / name
    log = folder / 'run.log'
    record = folder / 'command.json'
    if log.exists() or record.exists():
        raise FileExistsError('Each final probe needs a fresh evidence directory')
    if sha(ROOT / 'build/searchd') != binding['binary_sha256']:
        raise RuntimeError('Root binary differs from the frozen final input')
    argv = [sys.executable, str(folder / script)]
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    expired = False
    try:
        completed = subprocess.run(argv, cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
        code, output = completed.returncode, completed.stdout
    except subprocess.TimeoutExpired as error:
        code, expired = 124, True
        output = (error.stdout or b'') + b'\nIndependent probe watchdog expired.\n'
    with log.open('xb') as stream:
        stream.write(output)
    value = {'name': name, 'argv': argv, 'cwd': str(ROOT), 'started_at_utc': started,
             'elapsed_seconds': round(time.monotonic() - tick, 3), 'exit_code': code,
             'watchdog_expired': expired, 'log_sha256': sha(log),
             'script_sha256': sha(folder / script), 'binary_sha256': binding['binary_sha256']}
    with record.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    commands.append(value)
    print(json.dumps(value), flush=True)
    print(output.decode(errors='replace'), flush=True)

after = {'product_changed': [name for name, digest in binding['product_files'].items()
                             if sha(ROOT / name) != digest],
         'root_binary_sha256': sha(ROOT / 'build/searchd'),
         'http_copy_sha256': sha(OUT / 'http/searchd-reviewed'),
         'dispatch_copy_sha256': sha(OUT / 'dispatch/searchd-combined'),
         'stage_manifest_sha256': sha(ROOT / binding['stage_manifest_path']),
         'fixtures': {str(p.relative_to(ROOT / 'tests/fixtures')): sha(p)
                      for p in sorted((ROOT / 'tests/fixtures').rglob('*')) if p.is_file()},
         'dispatch_fixtures': {str(p.relative_to(Path(binding['dispatch_fixture_path']))): sha(p)
                               for p in sorted(Path(binding['dispatch_fixture_path']).rglob('*')) if p.is_file()},
         'probe_sha256': {name: sha(OUT / name) for name in binding['probe_sha256']}}
after['all_bound_inputs_unchanged'] = (not after['product_changed']
    and after['root_binary_sha256'] == after['http_copy_sha256'] == after['dispatch_copy_sha256'] == binding['binary_sha256']
    and after['stage_manifest_sha256'] == binding['stage_manifest_sha256']
    and after['fixtures'] == after['dispatch_fixtures'] == binding['fixtures']
    and after['probe_sha256'] == binding['probe_sha256'])
with (OUT / 'binding-after.json').open('x') as stream:
    json.dump(after, stream, indent=2)
    stream.write('\n')
print(json.dumps({'all_bound_inputs_unchanged': after['all_bound_inputs_unchanged']}), flush=True)
raise SystemExit(0 if after['all_bound_inputs_unchanged'] and all(item['exit_code'] == 0 for item in commands) else 1)
