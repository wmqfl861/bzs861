#!/usr/bin/env python3
"""Audit a closed CTest run and its frozen inputs without executing tests."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
REPO = HERE.parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures: list[dict] = []
    def check(condition: bool, name: str, detail: object = None) -> None:
        if not condition: failures.append({'check': name, 'detail': detail})
    command_path = OUT / 'final/ctest-v2-command.json'
    command = json.loads(command_path.read_text())
    manifest_path = command_path.parent / command['inputs']
    manifest = json.loads(manifest_path.read_text())
    wrapper_path = command_path.parent / command['log']
    detail_path = OUT / 'final/ctest-v2-detail.log'
    junit_path = OUT / 'final/ctest-v2-junit.xml'
    wrapper, detail = wrapper_path.read_text(), detail_path.read_text()
    check(command['exit_code'] == 0 and command['execution_record_valid'] and not command['watchdog_expired'], 'successful_closed_command')
    check(command['source_inputs_unchanged'] and command['changed_inputs'] == [], 'recorded_inputs_stable')
    check(sha(wrapper_path) == command['log_sha256'], 'original_complete_wrapper_log_hash')
    check(sha(manifest_path) == command['inputs_sha256'], 'input_manifest_hash')
    check(command['binaries_before'] == command['binaries_after'], 'recorded_binaries_stable')

    input_mismatches = [name for name, digest in manifest['files'].items() if not (REPO / name).is_file() or sha(REPO / name) != digest]
    check(not input_mismatches, 'all_current_227_input_bytes_match', input_mismatches)
    check(len(manifest['files']) == 227, 'manifest_count_227')
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=REPO)
    current_names = {name.decode() for name in names.split(b'\0') if name and not name.startswith(b'validation/')}
    current_names.add('validation/direct_shared_20261024/run.py')
    check(current_names == set(manifest['files']), 'manifest_covers_current_nonvalidation_inputs_and_recorder', sorted(current_names.symmetric_difference(manifest['files'])))
    binary_mismatches = [name for name, digest in command['binaries_after'].items() if not (REPO / name).is_file() or sha(REPO / name) != digest]
    check(not binary_mismatches, 'all_current_binary_bytes_match', binary_mismatches)

    xml = ET.parse(junit_path).getroot()
    xml_cases = xml.findall('testcase')
    check(int(xml.attrib['tests']) == len(xml_cases) == 28, 'junit_28_suites')
    check(all(int(xml.attrib[key]) == 0 for key in ('failures', 'disabled', 'skipped')), 'junit_zero_failures_disabled_skipped')
    check(all(case.attrib.get('status') == 'run' and not any(case.find(tag) is not None for tag in ('skipped','failure','error')) for case in xml_cases), 'all_junit_cases_run_successfully')
    xml_by_name = {case.attrib['name']: case for case in xml_cases}
    starts = list(re.finditer(r'^(\d+)/28 Testing: ([^\n]+)\n', detail, re.MULTILINE))
    check(len(starts) == 28, 'full_lasttest_28_sections')
    blocks: list[dict] = []
    native: list[dict] = []
    unittests: list[dict] = []
    python_count = None
    for index, match in enumerate(starts):
        number, name = int(match[1]), match[2]
        block = detail[match.start():starts[index+1].start() if index+1 < len(starts) else len(detail)]
        found = re.search(r'Output:\n-+\n(.*?)\n<end of output>', block, re.DOTALL)
        check(found is not None, 'complete_test_stdout', name)
        output = found[1] if found else ''
        check('Test Passed.' in block and 'Test Failed.' not in block, 'lasttest_case_passed', name)
        check(name in xml_by_name and (xml_by_name[name].findtext('system-out') or '').rstrip('\n') == output.rstrip('\n'), 'junit_stdout_matches_lasttest', name)
        verbose_lines = [value[1] for value in re.finditer(r'^' + str(number) + r': ?(.*)$', wrapper, re.MULTILINE)]
        stdout_lines = output.splitlines()
        contiguous = any(verbose_lines[start:start+len(stdout_lines)] == stdout_lines for start in range(max(0, len(verbose_lines)-len(stdout_lines)+1)))
        check(contiguous, 'hash_bound_wrapper_contains_exact_stdout', name)
        suspicious = [line for line in output.splitlines() if re.search(r'\b[1-9]\d*\s+skipped\b|\bOK\s*\(skipped=[1-9]|^\s*(?:SKIP|SKIPPED)\b|\.\.\.\s+skipped\b|\bNot Run\b|\bDISABLED\b', line)]
        check(not suspicious, 'no_nested_test_skips', {'suite': name, 'lines': suspicious})
        blocks.append({'number': number, 'name': name, 'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
                       'output_lines': len(stdout_lines), 'wrapper_output_matches': contiguous,
                       'command': re.search(r'^Command: (.*)$', block, re.MULTILINE)[1]})
        if number <= 16:
            counts = None
            for line in output.splitlines():
                if line.startswith('{"tests":'):
                    value = json.loads(line); check(value['tests'] == value['passed'], 'core_all_declared_cases_passed')
                    counts = (value['tests'], value['assertions'], line)
                else:
                    patterns = [r'^(\d+) (?:[a-z ]+ )?tests(?:,| /) (\d+) assertions(?:,| /) 0 failures(?:, .*|$)',
                                r'^(\d+) cases, (\d+) assertions, 0 failed$',
                                r'^(\d+)/(\d+) tests, (\d+) assertions$',
                                r'^Contract tests: (\d+) passed, 0 failed; .*?, (\d+) assertions$']
                    for p in patterns:
                        value = re.match(p, line)
                        if value:
                            if '/' in p and p.startswith(r'^(\d+)/(\d+)'):
                                check(value[1] == value[2], 'native_fraction_all_passed', name)
                                counts = (int(value[1]), int(value[3]), line)
                            else: counts = (int(value[1]), int(value[2]), line)
                            break
            check(counts is not None, 'native_summary_present', name)
            if counts: native.append({'suite': name, 'cases': counts[0], 'assertions': counts[1], 'summary': counts[2]})
        elif name == 'python_worker':
            value = re.search(r'^(\d+) passed in ([0-9.]+)s$', output, re.MULTILINE)
            check(value is not None and int(value[1]) == 1294, 'pytest_1294_passed')
            if value: python_count = {'passed': int(value[1]), 'seconds': float(value[2]), 'skipped': 0}
        else:
            value = re.search(r'^Ran (\d+) tests in ([0-9.]+)s$', output, re.MULTILINE)
            check(value is not None and re.search(r'^OK$', output, re.MULTILINE) is not None, 'nested_unittest_success_summary', name)
            if value: unittests.append({'suite': name, 'cases': int(value[1]), 'seconds': float(value[2]), 'skipped': 0})
    check([item['number'] for item in blocks] == list(range(1,29)), 'suite_numbers_cover_1_to_28')
    check({item['name'] for item in blocks} == set(xml_by_name), 'junit_lasttest_case_sets_match')
    check((len(native),sum(item['cases'] for item in native),sum(item['assertions'] for item in native)) == (16,544,20397), 'native_counts_16_544_20397')
    check('100% tests passed, 0 tests failed out of 28' in wrapper, 'ctest_wrapper_final_success')
    check(len(unittests) == 11 and sum(item['cases'] for item in unittests) == 123, 'unittest_components_11_123')

    # The one executed historical validation script is outside run.py's input
    # set. Bind it and all preserved old evidence to the separate pre-work
    # snapshot plus the published Git tree, without calling this a run.py input.
    old_path = OUT / 'old-validation-before.json'
    old = json.loads(old_path.read_text())
    if 'files' in old: old = old['files']
    entries = subprocess.check_output(['git','ls-tree','-r','-z',manifest['base_commit'],'--','validation'], cwd=REPO)
    old_git = {}
    for entry in entries.split(b'\0'):
        if not entry: continue
        metadata, name = entry.split(b'\t',1)
        mode, kind, identity = metadata.decode().split()
        check(kind == 'blob', 'old_validation_git_entry_blob', name.decode())
        old_git[name.decode()] = identity
    check(set(old_git) == set(old) and len(old) == 3590, 'old_validation_pre_snapshot_matches_baseline_file_set')
    old_mismatches = []
    for name, digest in old.items():
        path = REPO / name
        if not path.is_file(): old_mismatches.append(name); continue
        data = path.read_bytes()
        git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if hashlib.sha256(data).hexdigest() != digest or git_hash != old_git.get(name): old_mismatches.append(name)
    check(not old_mismatches, 'old_validation_current_bytes_equal_pre_snapshot_and_git_blobs', old_mismatches)
    historical_script = 'validation/s2_takeover/cache_review/probe_http_policy.py'
    check(historical_script not in manifest['files'] and historical_script in old, 'historical_test_supplemental_binding_explicit')

    independent_manifest = HERE / 'archive-manifest.json'
    independent = json.loads(independent_manifest.read_text())
    archive_changed = [name for name, value in independent['files'].items() if sha(HERE/name) != value['sha256'] or (HERE/name).stat().st_size != value['bytes']]
    check(not archive_changed, 'prior_independent_archive_488_bytes_unchanged', archive_changed)
    lasttest = REPO / 'build/Testing/Temporary/LastTest.log'
    check(lasttest.exists() and sha(lasttest) == sha(detail_path), 'copied_lasttest_equals_closed_build_log')
    record = {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'passed_closed_ctest_independent_review' if not failures else 'closed_ctest_review_failed',
        'audit_kind': 'Read-only audit of existing closed evidence; no product test execution.',
        'reviewer_script_sha256': sha(Path(__file__).resolve()),
        'command_record': {'path': '../final/ctest-v2-command.json', 'sha256': sha(command_path), 'exit_code': command['exit_code'], 'duration_seconds': command['duration_seconds']},
        'artifacts': {str(path.relative_to(OUT)): {'sha256': sha(path), 'bytes': path.stat().st_size} for path in (wrapper_path,detail_path,junit_path,manifest_path)},
        'ctest': {'suites': 28, 'passed': 28, 'failed': 0, 'disabled': 0, 'skipped': 0, 'nested_skips': 0,
                  'native_targets': len(native), 'native_cases': sum(x['cases'] for x in native), 'native_assertions': sum(x['assertions'] for x in native),
                  'native_summaries': native, 'python_worker': python_count, 'unittest_components': unittests, 'suite_bindings': blocks,
                  'time_note': '207.694 s is recorder wall duration; CTest reports 206.75 s. JUnit/LastTest local PDT timestamp corresponds to the recorder UTC start. Counts are suite components, not additional CTest suites.'},
        'input_binding': {'manifest_count': len(manifest['files']), 'manifest_file_set_complete': current_names == set(manifest['files']),
                          'all_current_inputs_match': not input_mismatches, 'recorded_before_after_source_stable': command['source_inputs_unchanged'],
                          'binary_count': len(command['binaries_after']), 'all_current_binaries_match': not binary_mismatches,
                          'recorded_before_after_binaries_equal': command['binaries_before'] == command['binaries_after'],
                          'verified_input_files': manifest['files'], 'verified_binaries': command['binaries_after']},
        'supplemental_historical_binding': {'pre_snapshot': '../old-validation-before.json', 'pre_snapshot_sha256': sha(old_path),
                                          'count': len(old), 'baseline_commit': manifest['base_commit'],
                                          'all_current_pre_snapshot_and_git_blob_bytes_equal': not old_mismatches,
                                          'executed_script': historical_script, 'script_sha256': old[historical_script],
                                          'limits': 'This original validation dependency is covered by a separate pre-work freeze and post-run Git/current-byte comparison, not by run.py\'s 227-file runtime manifest.'},
        'preserved_independent_archive': {'manifest_sha256': sha(independent_manifest), 'files': len(independent['files']), 'all_bytes_unchanged': not archive_changed},
        'failed_checks': failures,
        'limits': ['Local closed CTest only; root sanitizer-v3 records and final aggregate report are reviewed separately when closed.', 'No remote CI, publication or complete blueprint acceptance claim.'],
    }
    target = HERE / 'closed-ctest-review.json'
    with target.open('x', encoding='utf-8') as stream: json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n')
    files = [Path(__file__).resolve(), target]
    with (HERE/'closed-ctest-archive.json').open('x', encoding='utf-8') as stream:
        json.dump({'files':{str(path.relative_to(HERE)):{'sha256':sha(path),'bytes':path.stat().st_size} for path in files},'scope':'Append-only CTest review supplement; original 488-file archive remains unchanged.'},stream,indent=2);stream.write('\n')
    print(json.dumps({'status':record['status'],'native': [len(native),sum(x['cases'] for x in native),sum(x['assertions'] for x in native)],'python':python_count,'unittest_cases':sum(x['cases'] for x in unittests),'inputs':len(manifest['files']),'binaries':len(command['binaries_after']),'failed_checks':failures},ensure_ascii=False,indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
