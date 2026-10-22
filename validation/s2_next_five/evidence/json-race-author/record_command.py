"""Capture scoped author commands without accepting skipped or failed runs."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--label', required=True)
p.add_argument('--cwd', required=True)
p.add_argument('--timeout', type=float, default=180)
p.add_argument('--input', action='append', default=[])
p.add_argument('command', nargs=argparse.REMAINDER)
a = p.parse_args()
command = a.command[1:] if a.command[:1] == ['--'] else a.command
root = Path(__file__).resolve().parent
hashes = {str(Path(path).resolve()): hashlib.sha256(Path(path).read_bytes()).hexdigest()
          for path in a.input}
started = time.monotonic()
try:
    result = subprocess.run(command, cwd=a.cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=a.timeout)
    output, code, timed_out = result.stdout, result.returncode, False
except subprocess.TimeoutExpired as error:
    output, code, timed_out = error.stdout or b'', None, True
elapsed = time.monotonic() - started
log = root / (a.label + '.log')
log.write_bytes(output)
record = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'argv': command, 'cwd': a.cwd, 'return_code': code,
          'timed_out': timed_out, 'elapsed_seconds': elapsed,
          'inputs_sha256': hashes, 'log': str(log),
          'log_sha256': hashlib.sha256(output).hexdigest()}
(root / (a.label + '.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'record': str(root / (a.label + '.json')), 'return_code': code,
                  'elapsed_seconds': elapsed, 'timed_out': timed_out}))
print(output.decode(errors='replace')[-7000:])
raise SystemExit(124 if timed_out else code if code is not None and code >= 0 else 128 - code)
