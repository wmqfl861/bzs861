"""Read-only audit of closed sanitizer records and the distinct contracts replay."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from review_closed_ctest import ROOT, OUT, EVIDENCE, current_inputs, sha


def main() -> None:
    folder = EVIDENCE / 'asan'
    originals = {path.name: json.loads(path.read_bytes()) for path in folder.glob('*-command.json')
                 if not path.name.endswith('-replay-command.json')}
    assert len(originals) == 15
    replay_name = 'bzs_contract_tests-replay-command.json'
    replay = json.loads((folder / replay_name).read_bytes())
    original_name = 'bzs_contract_tests-command.json'
    original = originals[original_name]
    original_log = folder / original['log']
    assert original_log.stat().st_size == 0
    assert original['log_sha256'] == '3148f3169a713e7ca028875761ccfcbf987034dba84be44a3a35f578230ad0ff'
    assert sha(original_log) != original['log_sha256']
    issue = json.loads((folder / 'contract-log-integrity-issue.json').read_bytes())
    assert issue['actual_log_bytes'] == 0 and issue['actual_log_sha256'] == sha(original_log)
    assert issue['recorded_log_sha256'] == original['log_sha256']
    assert issue['cause'] == 'not established'
    assert replay['argv'] == original['argv']
    assert replay['environment_overrides'] == original['environment_overrides']
    assert replay['binaries_before'] == replay['binaries_after'] == original['binaries_after']

    ctest = json.loads((EVIDENCE / 'final/ctest-command.json').read_bytes())
    current = current_inputs()
    expected_env = {'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1',
                    'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
    valid_records = {name: record for name, record in originals.items() if name != original_name}
    valid_records[replay_name] = replay
    all_records = {**originals, replay_name: replay}
    records = []
    for name, record in sorted(all_records.items()):
        path = folder / name
        inputs_path = folder / record['inputs']
        inputs = json.loads(inputs_path.read_bytes())
        log_path = folder / record['log']
        assert record['exit_code'] == 0 and record['execution_record_valid']
        assert not record['watchdog_expired']
        assert record['source_inputs_unchanged'] and record['changed_inputs'] == []
        assert record['inputs_sha256'] == sha(inputs_path)
        assert inputs['files'] == current
        assert record['binaries_before'] == record['binaries_after'] == ctest['binaries_after']
        assert all(sha(ROOT / file) == value for file, value in record['binaries_after'].items())
        assert all(record['environment_overrides'][key] == value for key, value in expected_env.items())
        match = sha(log_path) == record['log_sha256']
        assert match == (name != original_name)
        summaries = []
        if match:
            log = log_path.read_text()
            summaries = [line for line in log.splitlines()
                         if 'assertions' in line and re.search(r'(?:tests|cases|"tests")', line)]
            assert len(summaries) == 1
            assert not re.search(r'(?:ERROR: (?:AddressSanitizer|LeakSanitizer)|runtime error:|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer))', log)
        records.append({'record': name, 'command_sha256': sha(path),
                        'inputs_sha256': sha(inputs_path), 'input_files': len(inputs['files']),
                        'log': record['log'], 'log_bytes': log_path.stat().st_size,
                        'recorded_log_sha256': record['log_sha256'], 'actual_log_sha256': sha(log_path),
                        'log_hash_matches': match, 'counted_in_complete_suite': match,
                        'exit_code': record['exit_code'], 'summary_lines': summaries})

    assert len(valid_records) == 15
    assert len({tuple(record['argv']) for record in valid_records.values()}) == 15
    assert all(json.loads((folder / record['inputs']).read_bytes())['files'] ==
               json.loads((folder / original['inputs']).read_bytes())['files'] for record in valid_records.values())
    expected = {
        'bzs_batch_tests-command.json': (30, 801),
        'bzs_cache_freshness_tests-command.json': (63, 1323),
        'bzs_capacity_cycles_tests-command.json': (4, 4800),
        'bzs_contract_tests-replay-command.json': (55, 269),
        'bzs_crawl_tests-command.json': (54, 2798),
        'bzs_fairness_tests-command.json': (18, 339),
        'bzs_map_tests-command.json': (24, 462),
        'bzs_retention_tests-command.json': (19, 480),
        'bzs_revalidation_tests-command.json': (24, 372),
        'bzs_revocation_tests-command.json': (12, 146),
        'bzs_search_mode_tests-command.json': (22, 382),
        'bzs_session_tests-command.json': (23, 298),
        'bzs_site_publication_tests-command.json': (32, 1203),
        'bzs_source_publication_tests-command.json': (20, 180),
        'bzs_tests-command.json': (107, 2253),
    }
    assert set(expected) == set(valid_records)
    for entry in records:
        if entry['counted_in_complete_suite']:
            count, assertions = expected[entry['record']]
            line = entry['summary_lines'][0]
            assert re.search(r'(?<!\d)' + str(count) + r'(?!\d)', line)
            assert re.search(r'(?<!\d)' + str(assertions) + r'(?!\d)', line)

    snapshot = json.loads((EVIDENCE / 'old-validation-before.json').read_bytes())
    assert len(snapshot) == 2793
    assert all(sha(ROOT / name) == value for name, value in snapshot.items())
    result = {
        'reviewer': '/root/handoff_audit',
        'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
        'verdict': 'pass for complete 15-target sanitizer evidence using the distinct contracts replay; original incomplete contracts record excluded',
        'scope': 'Read-only audit; reviewer did not rerun tests, edit product files or modify prior evidence.',
        'review_script_sha256': sha(Path(__file__)),
        'helper_script_sha256': sha(OUT / 'review_closed_ctest.py'),
        'complete_sanitizer_targets': 15,
        'native_test_cases': sum(item[0] for item in expected.values()),
        'native_assertions': sum(item[1] for item in expected.values()),
        'environment_overrides': expected_env,
        'all_input_manifests_equal_current_224_inputs': True,
        'all_binary_sets_equal_closed_ctest_and_current': True,
        'unique_executed_targets_in_complete_set': 15,
        'records': records,
        'log_integrity_issue': {
            'original_command': original_name,
            'original_reported_exit_code': original['exit_code'],
            'original_recorded_log_sha256': original['log_sha256'],
            'original_current_log_sha256': sha(original_log),
            'original_current_log_bytes': 0,
            'original_is_excluded_from_complete_evidence': True,
            'issue_record_sha256': sha(folder / 'contract-log-integrity-issue.json'),
            'cause': 'not established',
            'replacement_record': replay_name,
            'same_binary_fixture_source_inputs_and_options': True,
            'replacement_complete_log_verified': True,
            'original_record_and_empty_log_retained_unchanged_by_reviewer': True,
        },
        'preservation': {'old_validation_files': 2793, 'post_sanitizer_hashes_equal_pre_run_snapshot': True,
                         'baseline_blob_equality_already_proven_in': 'post-ctest-review.json'},
        'counting_limit': 'The original contracts invocation is excluded and its replay supplies the one contracts target. These 15 targets overlap Debug and independent scenarios and are not additional blueprint acceptances.',
        'pending_scope': 'Final consolidated report and exact published commit/tree/CI still require separate review.',
    }
    with (OUT / 'post-sanitizer-review.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('verdict', 'complete_sanitizer_targets', 'native_test_cases', 'native_assertions')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
