import hashlib
import json
import os
import pathlib
import subprocess
import time

out = pathlib.Path('/tmp/bzs-independent-next-retry')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
library = out / 'libbzs_core-correction-v1-asan.a'
expected = '9ddd7d58d165eb55490ca877c5a01c0102ce314e96168ddcc99c29e878ab2396'
assert sha(library) == expected
env_values = {'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1',
              'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
failed = False
for name in ('matrix', 'protocol', 'proof-legacy'):
    binary = out / (name + '-correction-v1-asan')
    compiled = json.loads((out / (name + '-correction-v1-asan-compile.json')).read_text())
    assert compiled['compile_exit'] == 0
    assert sha(binary) == compiled['binary_sha256']
    log = out / (name + '-correction-v1-asan.log')
    assert not log.exists()
    started = time.monotonic()
    with log.open('w') as handle:
        result = subprocess.run([str(binary)], cwd=out, env=os.environ | env_values,
                                stdout=handle, stderr=subprocess.STDOUT, timeout=55)
    record = {'argv': [str(binary)], 'cwd': str(out), 'env': env_values,
              'exit_code': result.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3),
              'binary_sha256': sha(binary), 'library_sha256': sha(library), 'log_sha256': sha(log),
              'compiled_binary_unchanged': sha(binary) == compiled['binary_sha256']}
    (out / (name + '-correction-v1-asan.json')).write_text(json.dumps(record, indent=2) + '\n')
    print(name, result.returncode, log.read_text(), flush=True)
    failed |= result.returncode != 0
raise SystemExit(int(failed))
