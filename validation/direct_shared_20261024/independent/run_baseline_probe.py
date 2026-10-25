#!/usr/bin/env python3
"""Build/run the independent baseline once; retain all first-run evidence."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
BASE = Path('/workspace/scratch/133513441190/direct-shared-baseline-20261024')
EXPECTED_LIB = '35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0'
SOURCE = HERE / 'baseline_direct_probe.cpp'
RUN = HERE / 'baseline-v1'
RUNTIME = HERE / 'runtime' / 'baseline-v1'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path): sha(path) for path in sorted(set(paths))}


def execute(name: str, command: list[str], inputs: list[Path], timeout: int) -> dict:
    before = snapshot(inputs)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    start = time.monotonic()
    log_path = RUN / f'{name}.log'
    with log_path.open('xb') as log:
        try:
            process = subprocess.run(command, cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=timeout, check=False)
            exit_code, timed_out = process.returncode, False
        except subprocess.TimeoutExpired:
            exit_code, timed_out = None, True
    after = snapshot(inputs)
    record = {
        'argv': command, 'cwd': str(HERE), 'started_at': started,
        'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'elapsed_seconds': time.monotonic() - start, 'exit_code': exit_code,
        'timed_out': timed_out, 'timeout_seconds': timeout,
        'inputs_before': before, 'inputs_after': after,
        'changed_inputs': [key for key in before if before[key] != after[key]],
        'log_path': str(log_path), 'log_sha256': sha(log_path),
        'log_bytes': log_path.stat().st_size,
    }
    save(RUN / f'{name}-command.json', record)
    return record


def main() -> int:
    if RUN.exists() or RUNTIME.exists():
        raise SystemExit('Refusing to overwrite prior baseline evidence/runtime')
    RUN.mkdir()
    RUNTIME.mkdir(parents=True)
    manifest = json.loads((BASE / 'manifest.json').read_text())
    failures = {name: {'expected': digest, 'actual': sha(BASE / name)}
                for name, digest in manifest['files'].items() if sha(BASE / name) != digest}
    save(RUN / 'baseline-binding.json', {
        'manifest': manifest, 'manifest_sha256': sha(BASE / 'manifest.json'),
        'copied_artifact_mismatches': failures,
        'expected_library_sha256': EXPECTED_LIB,
        'library_matches': sha(BASE / 'libbzs_core.a') == EXPECTED_LIB,
        'platform': platform.platform(), 'python': sys.version,
        'compiler': shutil.which('g++'),
        'compiler_version': subprocess.check_output(['g++', '--version'], text=True),
        'scope': 'Frozen published baseline; missing capability is an observation, not a regression.'
    })
    if failures or sha(BASE / 'libbzs_core.a') != EXPECTED_LIB:
        raise SystemExit('Frozen baseline binding mismatch; no compile/run performed')
    includes = sorted((BASE / 'include').rglob('*.hpp'))
    inputs = [Path(__file__).resolve(), SOURCE, BASE / 'manifest.json', BASE / 'libbzs_core.a', *includes]
    binary = RUNTIME / 'baseline_direct_probe'
    command = ['g++', '-std=c++20', '-Wall', '-Wextra', '-Wpedantic', '-pthread',
               '-I' + str(BASE / 'include'), str(SOURCE), str(BASE / 'libbzs_core.a'),
               '-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-o', str(binary)]
    compile_record = execute('compile', command, inputs, 60)
    if compile_record['exit_code'] != 0 or compile_record['changed_inputs']:
        save(RUN / 'summary.json', {'status': 'compile_failed_or_input_changed',
                                  'compile': compile_record['exit_code']})
        return 1
    linked = subprocess.check_output(['ldd', str(binary)], text=True)
    linked_paths = []
    for line in linked.splitlines():
        parts = line.strip().split()
        candidate = parts[2] if len(parts) >= 3 and parts[1] == '=>' else (parts[0] if parts else '')
        if candidate.startswith('/') and Path(candidate).is_file():
            linked_paths.append(Path(candidate).resolve())
    save(RUN / 'binary-binding.json', {'binary': str(binary), 'sha256': sha(binary),
                                     'bytes': binary.stat().st_size, 'ldd': linked,
                                     'linked_libraries': snapshot(linked_paths)})
    run_record = execute('run', [str(binary), str(RUNTIME / 'database')],
                         inputs + [binary] + linked_paths, 30)
    observation = None
    try:
        observation = json.loads((RUN / 'run.log').read_text())
    except (UnicodeError, json.JSONDecodeError):
        pass
    passed = (run_record['exit_code'] == 0 and not run_record['changed_inputs'] and
              observation is not None and observation.get('observation_matches_expected_baseline') is True)
    save(RUN / 'summary.json', {
        'status': 'baseline_observation_confirmed' if passed else 'baseline_probe_did_not_match',
        'classification': 'missing_capability_not_product_regression',
        'source_sha256': sha(SOURCE), 'runner_sha256': sha(Path(__file__).resolve()),
        'library_sha256': EXPECTED_LIB, 'binary_sha256': sha(binary),
        'compile_record_sha256': sha(RUN / 'compile-command.json'),
        'run_record_sha256': sha(RUN / 'run-command.json'),
        'run_log_sha256': sha(RUN / 'run.log'),
        'physical_fetches': observation.get('physical_fetches') if observation else None,
        'both_fetches_entered_before_release': observation.get('both_fetches_entered_before_release') if observation else None,
        'direct_usage_missing_in_both_results': all(item['usage_absent_despite_worker_receipt'] for item in observation['callers']) if observation else None,
        'unchanged_inputs': not compile_record['changed_inputs'] and not run_record['changed_inputs'],
        'scope': 'Two simultaneous same-owner refresh calls via frozen public C++ API and an offline synchronized Worker; no HTTP/MCP or acceptance-suite claim.'
    })
    save(RUN / 'artifact-sha256.json', {str(path.relative_to(HERE)): {'sha256': sha(path), 'bytes': path.stat().st_size}
                                     for path in sorted(RUN.iterdir()) if path.is_file()})
    print(json.dumps(json.loads((RUN / 'summary.json').read_text()), ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
