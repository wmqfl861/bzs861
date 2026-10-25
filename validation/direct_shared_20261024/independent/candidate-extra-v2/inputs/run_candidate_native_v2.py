#!/usr/bin/env python3
"""Freeze and run reviewer-native probes. Each label is append-only evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
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
REPO = HERE.parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path): digest(path) for path in sorted(set(paths))}


def production_paths() -> list[Path]:
    paths = [REPO / 'CMakeLists.txt']
    for directory, pattern in [('include', '*.hpp'), ('src', '*.cpp'), ('apps', '*.cpp'), ('python/bzs', '*.py')]:
        paths += sorted((REPO / directory).rglob(pattern))
    return paths


def execute(folder: Path, name: str, command: list[str], inputs: list[Path], env: dict[str, str], timeout: int) -> dict:
    before = snapshot(inputs)
    started = datetime.now(timezone.utc).isoformat(); tick = time.monotonic()
    log_path = folder / f'{name}.log'
    with log_path.open('xb') as log:
        try:
            result = subprocess.run(command, cwd=REPO, env={**os.environ, **env},
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            code, expired = result.returncode, False
        except subprocess.TimeoutExpired:
            code, expired = 124, True
    after = snapshot(inputs)
    changed = [path for path in before if before[path] != after[path]]
    record = {'argv': command, 'cwd': str(REPO), 'environment_overrides': env,
              'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': time.monotonic() - tick, 'exit_code': code,
              'watchdog_expired': expired, 'timeout_seconds': timeout,
              'inputs_before': before, 'inputs_after': after, 'changed_inputs': changed,
              'log_path': str(log_path), 'log_sha256': digest(log_path), 'log_bytes': log_path.stat().st_size,
              'execution_record_valid': code == 0 and not expired and not changed}
    save(folder / f'{name}-command.json', record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--expected-library-sha256', required=True)
    parser.add_argument('--source', type=Path, default=HERE / 'candidate_boundary_probe_v2.cpp')
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    if any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for char in args.label):
        parser.error('Use a simple new evidence label')
    library = args.library.resolve()
    if digest(library) != args.expected_library_sha256:
        raise SystemExit('Candidate library differs from the supplied frozen identity')
    folder = HERE / args.label
    runtime = Path('/tmp/bzs861-direct-independent-20261024/handoff-audit') / args.label
    if folder.exists() or runtime.exists():
        raise SystemExit('Refusing to overwrite prior native evidence or runtime')
    folder.mkdir(); runtime.mkdir(parents=True)
    frozen = folder / 'inputs'; frozen.mkdir()
    shutil.copytree(REPO / 'include', frozen / 'include')
    original_source = args.source.resolve()
    source = frozen / original_source.name
    shutil.copy2(original_source, source)
    helper = HERE / 'candidate_boundary_probe_v2.cpp'
    if source.name != helper.name: shutil.copy2(helper, frozen / helper.name)
    shutil.copy2(Path(__file__).resolve(), frozen / 'run_candidate_native_v2.py')
    frozen_lib = runtime / 'libbzs_core.a'; shutil.copy2(library, frozen_lib)
    if digest(frozen_lib) != args.expected_library_sha256 or digest(library) != args.expected_library_sha256:
        raise SystemExit('Library changed during freeze')
    product = production_paths()
    frozen_paths = sorted(path for path in frozen.rglob('*') if path.is_file())
    inputs = product + frozen_paths + [original_source, helper, Path(__file__).resolve(), library, frozen_lib]
    save(folder / 'frozen-inputs.json', {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'baseline_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'library_sha256': digest(frozen_lib), 'library_path': str(frozen_lib),
        'frozen_inputs': snapshot(frozen_paths), 'production_inputs': snapshot(product),
        'scope': 'CMake, all current C++ headers/sources/apps and Python production modules, frozen reviewer source/recorder/headers, original and copied static library. Formal tests/docs are not execution inputs of this focused probe.',
        'platform': platform.platform(), 'python_version': sys.version,
        'compiler_version': subprocess.check_output(['g++', '--version'], text=True),
        'sanitizers': args.sanitizers,
    })
    binary = runtime / 'candidate_boundary_probe'
    command = ['g++', '-std=c++20', '-Wall', '-Wextra', '-Wpedantic', '-pthread']
    if args.sanitizers: command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    command += ['-I' + str(frozen / 'include'), str(source), str(frozen_lib),
                '-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-o', str(binary)]
    env = {'PYTHONDONTWRITEBYTECODE': '1'}
    if args.sanitizers:
        env.update(ASAN_OPTIONS='detect_leaks=1:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    compiled = execute(folder, 'compile', command, inputs, env, 60)
    if not compiled['execution_record_valid']:
        save(folder / 'summary.json', {'status': 'compile_failed_or_inputs_changed', 'compile_exit_code': compiled['exit_code']})
        print(json.dumps({'status': 'compile_failed_or_inputs_changed', 'record': str(folder / 'compile-command.json')}))
        return 1
    ldd = subprocess.check_output(['ldd', str(binary)], text=True)
    linked = []
    for line in ldd.splitlines():
        parts = line.strip().split()
        path = parts[2] if len(parts) >= 3 and parts[1] == '=>' else (parts[0] if parts else '')
        if path.startswith('/') and Path(path).is_file(): linked.append(Path(path).resolve())
    save(folder / 'binary-binding.json', {'path': str(binary), 'sha256': digest(binary), 'bytes': binary.stat().st_size,
                                        'ldd': ldd, 'linked_libraries': snapshot(linked)})
    result = execute(folder, 'run', [str(binary), str(runtime / 'fixtures')], inputs + [binary] + linked, env, 180)
    observation = None
    try: observation = json.loads((folder / 'run.log').read_text())
    except (UnicodeError, json.JSONDecodeError): pass
    passed = result['execution_record_valid'] and observation is not None and observation.get('failures') == 0
    summary = {'status': 'passed_independent_native_boundaries' if passed else 'independent_native_probe_failed',
               'cases': observation.get('cases') if observation else None,
               'checks': observation.get('checks') if observation else None,
               'failures': observation.get('failures') if observation else None,
               'failed_cases': [item['case'] for item in observation['results'] if not item['passed']] if observation else [],
               'library_sha256': digest(frozen_lib), 'binary_sha256': digest(binary),
               'source_sha256': digest(source), 'runner_sha256': digest(Path(__file__).resolve()),
               'compile_command_sha256': digest(folder / 'compile-command.json'),
               'run_command_sha256': digest(folder / 'run-command.json'), 'run_log_sha256': digest(folder / 'run.log'),
               'sanitizers': args.sanitizers,
               'inputs_unchanged': not compiled['changed_inputs'] and not result['changed_inputs'],
               'limits': 'Independent finite offline scenarios. Sanitizer replays do not add distinct scenarios; no full B-24 or product acceptance claim.'}
    save(folder / 'summary.json', summary)
    save(folder / 'artifact-sha256.json', {str(path.relative_to(HERE)): {'sha256': digest(path), 'bytes': path.stat().st_size}
                                         for path in sorted(folder.rglob('*')) if path.is_file()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
