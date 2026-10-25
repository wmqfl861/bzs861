#!/usr/bin/env python3
"""Recheck the original frozen candidate with the corrected current-index oracle."""
from pathlib import Path
import json
import shutil
import subprocess
from run_candidate_native import HERE, digest, execute, save


def main() -> int:
    prior = HERE / 'candidate-native-v1'
    frozen = prior / 'inputs'
    library = Path('/tmp/bzs861-direct-independent-20261024/handoff-audit/candidate-native-v1/libbzs_core.a')
    expected = 'ea64be39e821893b3da32e37e1180ab151e971f8ddbfd72e188d9d5a3c94c0d8'
    assert digest(library) == expected
    folder = HERE / 'candidate-native-v1-oracle-corrected'
    runtime = Path('/tmp/bzs861-direct-independent-20261024/handoff-audit/candidate-native-v1-oracle-corrected')
    if folder.exists() or runtime.exists(): raise SystemExit('Refusing to overwrite prior evidence')
    folder.mkdir(); runtime.mkdir(parents=True)
    source = folder / 'candidate_boundary_probe_v2.cpp'
    shutil.copy2(HERE / 'candidate_boundary_probe_v2.cpp', source)
    binary = runtime / 'candidate_boundary_probe'
    dependencies = [Path(path) for path in json.loads((prior / 'binary-binding.json').read_text())['linked_libraries']]
    inputs = [source, HERE / 'candidate_boundary_probe_v2.cpp', Path(__file__).resolve(),
              HERE / 'run_candidate_native.py', HERE / 'v1-failure-classification.json', library,
              prior / 'frozen-inputs.json', prior / 'run-command.json', prior / 'run.log',
              *sorted((frozen / 'include').rglob('*.hpp')), *dependencies]
    save(folder / 'binding.json', {
        'library_sha256': expected, 'headers': str(frozen / 'include'),
        'original_candidate_manifest_sha256': digest(prior / 'frozen-inputs.json'),
        'original_probe_source_sha256': digest(frozen / 'candidate_boundary_probe.cpp'),
        'corrected_probe_source_sha256': digest(source),
        'scope': 'Original candidate v1 static library plus its original frozen headers. The current working tree is not a claimed binary input. Only the reviewer index-visibility oracle changed; correct no-store/noindex expectations remain mandatory.',
        'expected_remaining_failure': 'malformed_usage_does_not_discard_valid_no_store_and_noindex',
    })
    command = ['g++', '-std=c++20', '-Wall', '-Wextra', '-Wpedantic', '-pthread',
               '-I' + str(frozen / 'include'), str(source), str(library),
               '-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-o', str(binary)]
    compiled = execute(folder, 'compile', command, inputs, {'PYTHONDONTWRITEBYTECODE': '1'}, 60)
    if not compiled['execution_record_valid']: return 2
    result = execute(folder, 'run', [str(binary), str(runtime / 'fixtures')], inputs + [binary],
                     {'PYTHONDONTWRITEBYTECODE': '1'}, 180)
    observation = json.loads((folder / 'run.log').read_text())
    failed = [item['case'] for item in observation['results'] if not item['passed']]
    confirmed = (result['exit_code'] == 1 and not result['changed_inputs'] and
                 failed == ['malformed_usage_does_not_discard_valid_no_store_and_noindex'])
    summary = {'status': 'original_candidate_defect_confirmed_with_corrected_oracle' if confirmed else 'unexpected_oracle_replay_result',
               'cases': observation['cases'], 'checks': observation['checks'], 'failures': observation['failures'],
               'failed_cases': failed, 'candidate_library_sha256': expected,
               'probe_binary_sha256': digest(binary), 'source_sha256': digest(source),
               'compile_record_sha256': digest(folder / 'compile-command.json'),
               'run_record_sha256': digest(folder / 'run-command.json'),
               'run_log_sha256': digest(folder / 'run.log'),
               'production_result': 'failed; this record is a defect reproduction, not a passing product gate',
               'inputs_unchanged': not compiled['changed_inputs'] and not result['changed_inputs']}
    save(folder / 'summary.json', summary)
    save(folder / 'artifact-sha256.json', {str(path.relative_to(HERE)): {'sha256': digest(path), 'bytes': path.stat().st_size}
                                         for path in sorted(folder.rglob('*')) if path.is_file()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if confirmed else 1


if __name__ == '__main__':
    raise SystemExit(main())
