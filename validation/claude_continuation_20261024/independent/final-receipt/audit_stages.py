import hashlib
import json
import pathlib
import re
import subprocess
import xml.etree.ElementTree as ET

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861')
batch = root / 'validation/claude_continuation_20261024'
out = pathlib.Path('/tmp/bzs-continuation-receipt-independent')
receipt = json.loads((out / 'input-snapshot/final-report.json').read_text())
mapping = json.loads((out / 'input-snapshot/commit-map.json').read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
read = lambda path: json.loads(path.read_text())
native = ['bzs_tests', 'bzs_batch_tests', 'bzs_map_tests', 'bzs_crawl_tests',
          'bzs_site_publication_tests', 'bzs_contract_tests', 'bzs_retention_tests',
          'bzs_fairness_tests', 'bzs_session_tests', 'bzs_revalidation_tests',
          'bzs_revocation_tests', 'bzs_source_publication_tests', 'bzs_cache_freshness_tests',
          'bzs_search_mode_tests', 'bzs_capacity_cycles_tests']
commits = {entry['issue']: entry['local'] for entry in mapping['commits']}
verified = []
all_test_names = None

for claimed in receipt['local_full_stages']:
    stage = claimed['issue'].lower()
    folder = batch / stage
    result_path = batch / claimed['record']
    result = read(result_path)
    assert sha(result_path) == claimed['sha256']
    manifest = folder / 'execution-inputs.json'
    inputs = read(manifest)['files']
    assert sha(manifest) == claimed['execution_inputs_sha256'] == result['execution_inputs_sha256']
    product = {path: value for path, value in inputs.items() if not path.startswith('validation/')}
    for path, value in product.items():
        blob = subprocess.check_output(['git', 'show', commits[claimed['issue']] + ':' + path], cwd=root)
        assert hashlib.sha256(blob).hexdigest() == value, (stage, path)

    commands = []
    for item in result['commands']:
        record_path = batch / item['record']
        command = read(record_path)
        assert sha(record_path) == item['sha256']
        log_path = record_path.parent / command['log']
        assert sha(log_path) == item['log_sha256'] == command['log_sha256']
        assert command['exit_code'] == 0 and not command['process_watchdog_expired']
        assert command['source_inputs_unchanged']
        assert command['execution_inputs_sha256'] == sha(record_path.parent / command['execution_inputs'])
        if not command['build_preparation']:
            assert command['binaries_before_sha256'] == command['binaries_after_sha256']
        commands.append({'path': item['record'], 'sha256': sha(record_path), 'log_sha256': sha(log_path),
                         'exit_code': 0, 'input_binding_valid': True, 'non_build_binaries_unchanged': not command['build_preparation']})

    ctest_command = read(folder / 'ctest-command.json')
    assert ctest_command['argv'][:3] == ['ctest', '--preset', 'dev']
    assert not set(ctest_command['argv']) & {'-R', '-E', '-L', '-LE', '--tests-regex', '--exclude-regex'}
    xml = ET.parse(folder / 'ctest.xml').getroot()
    counts = {key: int(xml.attrib[key]) for key in ['tests', 'failures', 'disabled', 'skipped']}
    assert counts == claimed['ctest'] == result['ctest'] == {'tests': 27, 'failures': 0, 'disabled': 0, 'skipped': 0}
    cases = xml.findall('testcase')
    assert len(cases) == 27 and all(not any(child.tag in {'failure', 'error', 'skipped'} for child in case) for case in cases)
    names = sorted(case.attrib['name'] for case in cases)
    if all_test_names is None: all_test_names = names
    assert names == all_test_names
    assert re.search(r'100% tests passed, 0 tests failed out of 27', (folder / 'ctest.log').read_text())

    pytest_command = read(folder / 'pytest-command.json')
    assert pytest_command['argv'][1:] == ['-m', 'pytest', 'python/tests', '-q', '-p', 'no:cacheprovider']
    pytest_line = next(line for line in reversed((folder / 'pytest.log').read_text().splitlines()) if re.search(r'\b\d+ passed\b', line))
    assert not re.search(r'\b(failed|skipped|deselected|xfailed|xpassed|error)\b', pytest_line)
    pytest_count = int(re.search(r'(\d+) passed', pytest_line)[1])
    assert pytest_count == claimed['pytest_passed'] == result['pytest_passed']

    asan_folder = batch / result['sanitizer_stage']
    asan_command = read(asan_folder / 'asan-command.json')
    assert asan_command['environment_overrides']['ASAN_OPTIONS'] == 'detect_leaks=1:halt_on_error=1'
    assert asan_command['environment_overrides']['UBSAN_OPTIONS'] == 'halt_on_error=1:print_stacktrace=1'
    assert asan_command['argv'][-2] == 'sanitizers'
    log = (asan_folder / 'asan.log').read_text()
    assert not re.search(r'ERROR: (Address|Leak)Sanitizer|runtime error:|SUMMARY: (Address|Leak|UndefinedBehavior)Sanitizer|AddressSanitizer:DEADLYSIGNAL', log)
    sections = re.split(r'(?m)^RUN_EXEC ', log)
    assert len(sections) == 16 and not sections[0].strip()
    results = []
    for expected_target, section in zip(native, sections[1:]):
        lines = section.strip().splitlines()
        target, summary = lines[0], lines[-1]
        assert target == expected_target
        guards = [json.loads(line.removeprefix('GUARD_EXEC ')) for line in lines if line.startswith('GUARD_EXEC ')]
        assert guards and all(item['target'] == target for item in guards)
        binary_hash = asan_command['binaries_before_sha256']['build-asan/' + target]
        assert all(item['sha256'] == binary_hash for item in guards)
        passed = sum(line.startswith('PASS ') for line in lines)
        if target == 'bzs_tests':
            summary_data = json.loads(summary)
            assert summary_data['tests'] == summary_data['passed'] == passed
            assertions = summary_data['assertions']
        else:
            summary_count = int(re.search(r'\d+', summary)[0])
            assert summary_count == passed
            assertions = int(re.search(r'(\d+) assertions', summary)[1])
            if target == 'bzs_revocation_tests':
                fraction = re.search(r'(\d+)/(\d+) tests', summary)
                assert fraction and fraction[1] == fraction[2]
            else:
                assert re.search(r'\b0 (?:failed|failures)\b', summary), (target, summary)
        results.append({'target': target, 'cases': passed, 'assertions': assertions, 'failures': 0})
    assert results == result['sanitizers']['program_results']
    total_cases = sum(item['cases'] for item in results)
    total_assertions = sum(item['assertions'] for item in results)
    assert total_cases == claimed['sanitizer_cases'] == result['sanitizers']['cases']
    assert total_assertions == claimed['sanitizer_assertions'] == result['sanitizers']['assertions']
    assert len(results) == claimed['sanitizer_programs'] == 15
    verified.append({'stage': stage, 'result_sha256': sha(result_path), 'inputs_sha256': sha(manifest),
                     'product_input_count': len(product), 'all_product_hashes_match_local_commit': commits[claimed['issue']],
                     'ctest': counts, 'pytest_summary': pytest_line, 'sanitizer_cases': total_cases,
                     'sanitizer_assertions': total_assertions, 'programs': results,
                     'accepted_sanitizer_folder': str(asan_folder.relative_to(batch)), 'commands': commands})

old = read(batch / 'p2-7/asan-command.json')
status = read(batch / 'p2-7/validation-status.json')
old_log = (batch / 'p2-7/asan.log').read_text()
assert old['exit_code'] == 0 and sha(batch / 'p2-7/asan.log') == old['log_sha256'] == status['log_sha256']
assert len(re.findall(r'(?m)^RUN_EXEC ', old_log)) == 9
assert status['status'] == 'incomplete_sanitizer_evidence_not_accepted_as_full_pass'
assert read(batch / 'p2-7/result.json')['excluded_incomplete_sanitizer_record'] == 'p2-7/asan-command.json'
repeat = read(batch / 'p2-7-asan-repeat/asan-command.json')
original_build = read(batch / 'p2-7/build-asan-command.json')
for name in [*native, 'libbzs_core.a']:
    key = 'build-asan/' + name
    assert original_build['binaries_after_sha256'][key] == repeat['binaries_before_sha256'][key]
product = lambda path: {key: value for key, value in read(path)['files'].items() if not key.startswith('validation/')}
assert product(batch / 'p2-7/execution-inputs.json') == product(batch / 'p2-7-asan-repeat/execution-inputs.json')

latest = read(batch / 'p2-9/asan-command.json')
current_native = {}
for name in native:
    path = root / 'build-asan' / name
    actual = sha(path)
    assert actual == latest['binaries_after_sha256']['build-asan/' + name]
    current_native[name] = actual

record = {'stages': verified, 'same_27_ctest_names_in_all_stages': all_test_names,
          'p2_7_initial_incomplete_log': {'exit_code': 0, 'run_markers': 9, 'log_sha256': old['log_sha256'],
                                         'explicitly_excluded': True, 'repeat_15_binaries_and_core_library_identical': True,
                                         'repeat_product_inputs_identical': True},
          'final_current_15_native_sanitizer_binaries_match_completed_gate': current_native,
          'new_test_execution_performed_by_reviewer': False}
(out / 'stage-audit.json').write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'stages': [{'stage': item['stage'], 'ctest': item['ctest']['tests'], 'pytest': item['pytest_summary'],
                              'sanitizer_cases': item['sanitizer_cases'], 'sanitizer_assertions': item['sanitizer_assertions']} for item in verified],
                  'original_incomplete_record_retained_and_excluded': True, 'final_native_hashes_match': True}, indent=2))
