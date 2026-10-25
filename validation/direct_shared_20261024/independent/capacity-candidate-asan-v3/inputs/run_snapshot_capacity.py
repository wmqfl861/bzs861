#!/usr/bin/env python3
"""Record append-only capacity comparisons using explicitly frozen input libraries."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from run_candidate_native_v2 import HERE, REPO, digest, execute, save, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--mode', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--headers', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--expected-library-sha256', required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    if any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for char in args.label): parser.error('Simple new label required')
    library, headers = args.library.resolve(), args.headers.resolve()
    if digest(library) != args.expected_library_sha256: raise SystemExit('Frozen library mismatch')
    folder = HERE / args.label
    runtime = Path('/tmp/bzs861-direct-independent-20261024/handoff-audit') / args.label
    if folder.exists() or runtime.exists(): raise SystemExit('Refusing to overwrite evidence')
    folder.mkdir(); runtime.mkdir(parents=True)
    frozen = folder / 'inputs'; frozen.mkdir()
    shutil.copytree(headers, frozen / 'include')
    source = frozen / 'snapshot_capacity_probe.cpp'; shutil.copy2(HERE / source.name, source)
    shutil.copy2(Path(__file__).resolve(), frozen / 'run_snapshot_capacity.py')
    shutil.copy2(HERE / 'run_candidate_native_v2.py', frozen / 'run_candidate_native_v2.py')
    provenance = args.provenance.resolve()
    copied_lib = runtime / 'libbzs_core.a'; shutil.copy2(library, copied_lib)
    frozen_paths = sorted(path for path in frozen.rglob('*') if path.is_file())
    inputs = [*frozen_paths, library, copied_lib, provenance, HERE / source.name,
              Path(__file__).resolve(), HERE / 'run_candidate_native_v2.py', *sorted(headers.rglob('*.hpp'))]
    save(folder / 'frozen-inputs.json', {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'mode': args.mode, 'library_sha256': digest(copied_lib), 'header_source': str(headers),
        'provenance_path': str(provenance), 'provenance_sha256': digest(provenance),
        'frozen_inputs': snapshot(frozen_paths),
        'scope': 'Explicit frozen library and its matching frozen headers. Current repository sources are not claimed inputs; prior closed manifest supplies their provenance.',
        'sanitizers': args.sanitizers,
    })
    binary = runtime / 'snapshot_capacity_probe'
    command = ['g++', '-std=c++20', '-Wall', '-Wextra', '-Wpedantic', '-pthread']
    env = {'PYTHONDONTWRITEBYTECODE': '1'}
    if args.sanitizers:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        env.update(ASAN_OPTIONS='detect_leaks=1:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command += ['-I' + str(frozen / 'include'), str(source), str(copied_lib), '-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-o', str(binary)]
    compiled = execute(folder, 'compile', command, inputs, env, 60)
    if not compiled['execution_record_valid']: return 2
    ldd = subprocess.check_output(['ldd', str(binary)], text=True)
    dependencies = []
    for line in ldd.splitlines():
        parts = line.strip().split()
        name = parts[2] if len(parts) >= 3 and parts[1] == '=>' else (parts[0] if parts else '')
        if name.startswith('/') and Path(name).is_file(): dependencies.append(Path(name).resolve())
    save(folder / 'binary-binding.json', {'path': str(binary), 'sha256': digest(binary), 'bytes': binary.stat().st_size,
                                        'ldd': ldd, 'linked_libraries': snapshot(dependencies)})
    result = execute(folder, 'run', [str(binary), str(runtime / 'fixtures'), args.mode], inputs + [binary] + dependencies, env, 180)
    observation = None
    try: observation = json.loads((folder / 'run.log').read_text())
    except (UnicodeError, json.JSONDecodeError): pass
    passed = result['execution_record_valid'] and observation is not None and observation.get('failures') == 0
    summary = {'status': 'passed_capacity_delivery' if passed else 'capacity_delivery_failed',
               'mode': args.mode, 'cases': observation.get('cases') if observation else None,
               'checks': observation.get('checks') if observation else None,
               'failures': observation.get('failures') if observation else None,
               'failed_cases': [item['case'] for item in observation['results'] if not item['passed']] if observation else [],
               'exit_code': result['exit_code'], 'library_sha256': digest(copied_lib), 'source_sha256': digest(source),
               'binary_sha256': digest(binary), 'run_log_sha256': digest(folder / 'run.log'),
               'run_command_sha256': digest(folder / 'run-command.json'),
               'inputs_unchanged': not compiled['changed_inputs'] and not result['changed_inputs'],
               'sanitizers': args.sanitizers,
               'counting': 'Two capacity scenarios; repetitions across versions or sanitizer builds are not additional scenarios.'}
    save(folder / 'summary.json', summary)
    save(folder / 'artifact-sha256.json', {str(path.relative_to(HERE)): {'sha256': digest(path), 'bytes': path.stat().st_size}
                                         for path in sorted(folder.rglob('*')) if path.is_file()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
