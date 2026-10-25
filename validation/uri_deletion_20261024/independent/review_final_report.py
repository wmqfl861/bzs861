"""Independent read-only final report and retained-evidence audit; no tests run."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from review_closed_ctest import ROOT, OUT, EVIDENCE, BASE, current_inputs, git, sha


def main() -> None:
    report_path = EVIDENCE / 'final-report.json'
    report = json.loads(report_path.read_bytes())
    post_path = EVIDENCE / 'final/post-run-inputs.json'
    post = json.loads(post_path.read_bytes())
    ctest_review = json.loads((OUT / 'post-ctest-review.json').read_bytes())
    sanitizer_review = json.loads((OUT / 'post-sanitizer-review.json').read_bytes())
    independent = json.loads((OUT / 'review-result.json').read_bytes())
    assert report['status'] == 'passed_local_validation'
    assert report['repository'] == 'wmqfl861/bzs861' and report['baseline_commit'] == BASE
    assert report['database_schema'] == {'before': 13, 'after': 14, 'stop_all_old_core_processes_before_upgrade': True}
    assert report['package_version'] == '0.1.10'
    assert sha(post_path) == report['input_binding']['post_run_sha256']
    assert post['files'] == current_inputs() and len(post['files']) == 224
    assert len(post['binaries']) == 36
    assert all(sha(ROOT / name) == value for name, value in post['binaries'].items())
    assert all(post['files'][name] == value for name, value in independent['production_hashes'].items())
    assert report['independent_review']['production_sha256'] == independent['production_hashes']

    closed = json.loads((EVIDENCE / 'final/ctest-command.json').read_bytes())
    assert post['binaries'] == closed['binaries_after'] == closed['binaries_before']
    assert sha(EVIDENCE / 'final/ctest-inputs.json') == closed['inputs_sha256']
    assert sha(EVIDENCE / 'final/ctest.log') == closed['log_sha256']
    for name, value in ctest_review['evidence_inputs_sha256'].items():
        assert sha(EVIDENCE / 'final' / name) == value
    ctest = report['final_ctest']
    assert (ctest['suites'], ctest['passed'], ctest['failed'], ctest['skipped'], ctest['disabled']) == (27, 27, 0, 0, 0)
    assert ctest['native_targets'] == 15 and ctest['native_cases'] == 507 and ctest['native_assertions'] == 16106
    assert ctest['python_passed'] == 1281 and ctest['python_seconds'] == 18.82
    assert ctest['cache_cases'] == 63 and ctest['cache_assertions'] == 1323
    assert ctest['duration_seconds'] == closed['duration_seconds']
    assert sha(EVIDENCE / ctest['full_output']) == ctest['full_output_sha256']
    assert sha(EVIDENCE / ctest['junit']) == ctest['junit_sha256']
    detail = (EVIDENCE / ctest['full_output']).read_text()
    assert all(item['summary'] in detail for item in ctest['native_summaries'])
    assert sum(item['cases'] for item in ctest['native_summaries']) == 507
    assert sum(item['assertions'] for item in ctest['native_summaries']) == 16106
    xml = ET.parse(EVIDENCE / ctest['junit']).getroot()
    assert len(xml.findall('testcase')) == 27 and xml.attrib['failures'] == '0' and xml.attrib['skipped'] == '0'

    selected = {item['record']: item for item in report['final_sanitizers']['per_target']}
    reviewed_selected = {'asan/' + item['record']: item for item in sanitizer_review['records']
                         if item['counted_in_complete_suite']}
    assert set(selected) == set(reviewed_selected) and len(selected) == 15
    assert len({item['target'] for item in selected.values()}) == 15
    for record, item in selected.items():
        command_path = EVIDENCE / record
        command = json.loads(command_path.read_bytes())
        reviewed = reviewed_selected[record]
        assert sha(command_path) == reviewed['command_sha256']
        assert sha(command_path.parent / command['inputs']) == item['inputs_sha256'] == reviewed['inputs_sha256']
        assert sha(command_path.parent / command['log']) == item['log_sha256'] == reviewed['actual_log_sha256']
        assert json.loads((command_path.parent / command['inputs']).read_bytes())['files'] == post['files']
        assert command['binaries_before'] == command['binaries_after'] == post['binaries']
        assert item['exit_code'] == command['exit_code'] == 0
        assert item['duration_seconds'] == command['duration_seconds']
        assert item['summary'] == reviewed['summary_lines'][0]
    sanitizers = report['final_sanitizers']
    assert sanitizers['targets'] == sanitizers['passed'] == 15 and sanitizers['failed'] == 0
    assert sum(item['cases'] for item in selected.values()) == sanitizers['native_cases'] == 507
    assert sum(item['assertions'] for item in selected.values()) == sanitizers['native_assertions'] == 16106
    assert sanitizers['excluded_incomplete_log_record'] == 'asan/bzs_contract_tests-command.json'
    assert sanitizers['replacement_record'] == 'asan/bzs_contract_tests-replay-command.json'
    assert sanitizers['excluded_incomplete_log_record'] not in selected
    original = EVIDENCE / 'asan/bzs_contract_tests.log'
    assert original.stat().st_size == 0
    assert sha(EVIDENCE / 'asan/contract-log-integrity-issue.json') == sanitizer_review['log_integrity_issue']['issue_record_sha256']
    assert (EVIDENCE / 'asan/bzs_contract_tests-replay.log').stat().st_size == 2422

    gate_commands = {}
    for name in ('contracts', 'plan', 'plan-guard', 'pip-check'):
        path = EVIDENCE / 'final' / (name + '-command.json')
        command = json.loads(path.read_bytes())
        assert command['execution_record_valid'] and command['exit_code'] == 0
        assert command['source_inputs_unchanged'] and not command['changed_inputs']
        assert command['log_sha256'] == sha(path.parent / command['log'])
        assert command['inputs_sha256'] == sha(path.parent / command['inputs'])
        assert command['binaries_before'] == command['binaries_after'] == post['binaries']
        gate_commands[str(path.relative_to(EVIDENCE))] = sha(path)
    contract = json.loads((EVIDENCE / 'final/contracts.log').read_bytes())
    assert contract['count'] == contract['passed'] == 34 and all(item['passed'] for item in contract['results'])
    assert json.loads((EVIDENCE / 'final/plan.log').read_bytes()) == report['additional_gates']['planning_read_only']
    assert json.loads((EVIDENCE / 'final/plan-guard.log').read_bytes()) == report['additional_gates']['plan_guard']
    assert (EVIDENCE / 'final/pip-check.log').read_text().strip() == 'No broken requirements found.'

    mcp = json.loads((EVIDENCE / 'mcp-description/result.json').read_bytes())
    compare = json.loads((EVIDENCE / 'mcp-description/tool-comparison.json').read_bytes())
    assert all(post['files'][name] == value for name, value in mcp['source_hashes'].items())
    native = mcp['commands'][0]
    assert native['passed'] == 124 and native['exit_code'] == 0 and native['skipped'] == 0
    assert native['sha256'] == sha(EVIDENCE / 'mcp-description' / native['log'])
    assert '124 passed' in (EVIDENCE / 'mcp-description' / native['log']).read_text()
    assert [item['tool_count'] for item in compare['results']] == [22, 23]
    assert all(item['schemas_annotations_and_names_identical'] for item in compare['results'])
    assert compare['description_only_ast'] and compare['source_requests'] == 0 and compare['discovery_core_calls'] == 0

    snapshot = json.loads((EVIDENCE / 'old-validation-before.json').read_bytes())
    assert len(snapshot) == 2793 and all(sha(ROOT / name) == value for name, value in snapshot.items())
    protected = report['preservation']['protected_paths']
    assert git('diff', '--name-only', BASE, '--', *protected) == b''
    assert git('ls-files', '--others', '--exclude-standard', '--', *protected) == b''
    for manifest in ('evidence-copy-manifest.json', 'supplementary-evidence-copy-manifest.json'):
        for item in json.loads((EVIDENCE / manifest).read_bytes())['files']:
            assert sha(ROOT / item['stored']) == item['sha256']
            assert (ROOT / item['stored']).stat().st_size == item['bytes']
    assert report['preservation']['status_counts'] == {'partial': 37, 'not_started': 46, 'accepted': 0}
    assert report['preservation']['registered_sources'] == 17 and report['preservation']['enabled_sources'] == 0 and report['preservation']['sites'] == 0
    assert report['remote_ci']['baseline_only_run'] == 40096268557
    assert set(report['remote_ci']) == {'scope', 'baseline_only_run'}

    files = []
    for path in sorted(EVIDENCE.rglob('*')):
        if not path.is_file():
            continue
        head = path.read_bytes()[:16]
        assert not any(head.startswith(magic) for magic in (b'\x7fELF', b'SQLite format 3\x00', b'!<arch>\n'))
        assert path.suffix not in {'.sqlite', '.db', '.a', '.o', '.so', '.exe', '.dll'}
        files.append({'path': str(path.relative_to(EVIDENCE)), 'sha256': sha(path), 'bytes': path.stat().st_size})
    evidence_snapshot = {'scope': 'All batch evidence present before this final independent review is archived; original empty contracts log is intentionally retained.',
                         'files': files}
    with (OUT / 'final-evidence-snapshot.json').open('x', encoding='utf-8') as stream:
        json.dump(evidence_snapshot, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    result = {
        'reviewer': '/root/handoff_audit',
        'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
        'verdict': 'pass; no blocking discrepancy in final local report, selected evidence, exact inputs, retained history or stated scope',
        'role': 'Reviewer authored independent scratch probes and read-only audits; no production or formal test edits. The contracts evidence replay was performed by the author and audited independently here.',
        'baseline_commit': BASE,
        'review_script_sha256': sha(Path(__file__)),
        'helper_script_sha256': sha(OUT / 'review_closed_ctest.py'),
        'reviewed_final_report_sha256': sha(report_path),
        'reviewed_batch_readme_sha256': sha(EVIDENCE / 'README.md'),
        'post_run_input_snapshot_sha256': sha(post_path),
        'post_ctest_review_sha256': sha(OUT / 'post-ctest-review.json'),
        'post_sanitizer_review_sha256': sha(OUT / 'post-sanitizer-review.json'),
        'batch_evidence_snapshot_sha256': sha(OUT / 'final-evidence-snapshot.json'),
        'batch_evidence_files_at_review': len(files),
        'findings': {
            'ctest': '27/27, zero failed/skipped/disabled; raw full output and XML agree.',
            'native_composition': '15 targets, 507 cases, 16106 assertions; cache is 63 cases/1323 assertions.',
            'python_composition': '1281 passed, within the 27 CTest entries.',
            'sanitizer_selection': 'Exactly 14 intact original records plus one complete same-binary contracts replay; 15 unique targets, no duplicate counting.',
            'contracts_log_integrity': 'Original zero-byte log and contradictory original recorded SHA are preserved, explicitly excluded. Cause remains unestablished. The distinct replay has 2422 bytes and matches SHA 3148f3169a713e7ca028875761ccfcbf987034dba84be44a3a35f578230ad0ff.',
            'inputs': 'All 224 current inputs match post-run and selected sanitizer snapshots; CTest differs only by the declared four Markdown corrections.',
            'binaries': 'All 36 current binary/config outputs match CTest and selected sanitizer pre/post sets. Production hashes remain the independently reviewed v2.',
            'additional_gates': 'Original contract fixtures 34/34, plan structure 83/161/10, plan guard 13/13 and dependency check agree with closed logs.',
            'mcp': '124 focused Python passes and 22/23 unchanged tool contracts are supported by same-source records; no discovery source or Core requests.',
            'history': '2793 old validation files, protected config/plan/golden/workflow/build/lock paths and existing feature state remain unchanged. Prior failures and original manifests preserved.',
            'documentation': 'Current schema14, finite deletion scope, history access boundary, stop-old-Core upgrade rule and source/acceptance limits are consistent.',
            'remote_scope': 'Report declares completed local validation only. No new remote commit/tree or Linux/Windows CI success is asserted.',
            'artifact_scope': 'Batch snapshot contains text/source evidence; no SQLite databases, native executables or static archives found.',
        },
        'counts_are_not_product_acceptances': True,
        'production_tests_reexecuted_by_this_audit': 0,
        'additional_gate_record_hashes': gate_commands,
        'release_boundary': 'This concludes local review at the hashes above. Publication must bind the actual resulting tree and check that exact commit on Linux and Windows separately; no test rerun is requested by this review.',
    }
    with (OUT / 'final-local-review.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('verdict', 'reviewed_final_report_sha256', 'reviewed_batch_readme_sha256', 'batch_evidence_files_at_review')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
