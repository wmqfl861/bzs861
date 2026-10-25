"""Independent read-only review of closed CTest and its supporting inputs.

Creates one new review JSON in the reviewer's scratch evidence directory.
Does not run product tests or modify repository files or previous evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import difflib
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('/workspace/scratch/133513441190/bzs861')
OUT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'validation/uri_deletion_20261024'
FINAL = EVIDENCE / 'final'
BASE = 'ef45490c6a0c384d8ad89452e2ba7e4df2cf2340'


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=ROOT)


def current_inputs() -> dict[str, str]:
    names = git('ls-files', '-z') + git('ls-files', '--others', '--exclude-standard', '-z')
    paths = {name.decode() for name in names.split(b'\0')
             if name and not name.startswith(b'validation/')}
    paths.add('validation/uri_deletion_20261024/run.py')
    return {name: sha(ROOT / name) for name in sorted(paths)}


def changed_material(patch: str) -> list[str]:
    return [line for line in patch.splitlines()
            if line.startswith('diff --git')
            or (line.startswith('+') and not line.startswith('+++'))
            or (line.startswith('-') and not line.startswith('---'))]


def main() -> None:
    command = json.loads((FINAL / 'ctest-command.json').read_bytes())
    inputs = json.loads((FINAL / command['inputs']).read_bytes())
    changes = json.loads((FINAL / 'documentation-after-ctest.json').read_bytes())
    assert command['exit_code'] == 0 and command['execution_record_valid']
    assert command['source_inputs_unchanged'] and command['changed_inputs'] == []
    assert not command['watchdog_expired']
    assert command['inputs_sha256'] == sha(FINAL / command['inputs'])
    assert command['log_sha256'] == sha(FINAL / command['log'])
    assert inputs['base_commit'] == BASE
    assert git('rev-parse', 'HEAD').decode().strip() == BASE

    current = current_inputs()
    assert set(inputs['files']) == set(current)
    actual_changes = {name: {'before_sha256': inputs['files'][name], 'after_sha256': current[name]}
                      for name in current if current[name] != inputs['files'][name]}
    assert actual_changes == changes['changed_files']
    assert set(actual_changes) == {'README.md', 'docs/API.md', 'docs/SOURCE_BACKOFF.md', 'docs/GITHUB_HANDOFF.md'}
    assert command['binaries_before'] == command['binaries_after']
    assert all(sha(ROOT / name) == value for name, value in command['binaries_after'].items())
    reviewed = json.loads((OUT / 'review-result.json').read_bytes())
    assert all(current[name] == value == inputs['files'][name]
               for name, value in reviewed['production_hashes'].items())
    assert sha(ROOT / 'build/libbzs_core.a') == reviewed['actual_final_debug_library_sha256']
    assert sha(ROOT / 'build-asan/libbzs_core.a') == reviewed['actual_final_sanitizer_library_sha256']

    detail = (FINAL / 'ctest-detail.log').read_text()
    assert sha(FINAL / 'ctest-detail.log') == sha(ROOT / 'build/Testing/Temporary/LastTest.log')
    xml = ET.parse(FINAL / 'ctest.xml').getroot()
    cases = xml.findall('testcase')
    assert xml.attrib['tests'] == '27' and len(cases) == 27
    assert all(xml.attrib.get(key, '0') == '0' for key in ('failures', 'errors', 'disabled', 'skipped'))
    assert all(case.attrib['status'] == 'run' and not any(case.find(tag) is not None
               for tag in ('failure', 'error', 'skipped')) for case in cases)
    detail_names = re.findall(r'^\d+/27 Test: (.+)$', detail, re.M)
    assert detail_names == [case.attrib['name'] for case in cases]
    assert len(re.findall(r'^Test Passed\.$', detail, re.M)) == 27
    assert detail.count('<end of output>') == 27
    assert 'End testing:' in detail
    assert '100% tests passed, 0 tests failed out of 27' in (FINAL / 'ctest.log').read_text()
    assert re.findall(r'^(\d+) passed in [\d.]+s$', detail, re.M) == ['1281']
    assert re.findall(r'^(\d+) cases, (\d+) assertions, (\d+) failed$', detail, re.M) == [('63', '1323', '0')]
    native_summaries = [line for line in detail.splitlines()
                        if ('assertions' in line and re.search(r'(?:tests|cases|"tests")', line))]
    assert len(native_summaries) == 15
    native_counts = [107, 30, 24, 54, 32, 19, 18, 23, 20, 22, 24, 12, 4, 63, 55]
    native_assertions = [2253, 801, 462, 2798, 1203, 480, 339, 298, 180, 382, 372, 146, 4800, 1323, 269]
    for summary, count, assertions in zip(native_summaries, native_counts, native_assertions):
        assert re.search(r'(?<!\d)' + str(count) + r'(?!\d)', summary)
        assert re.search(r'(?<!\d)' + str(assertions) + r'(?!\d)', summary)

    old_patch = (OUT / 'test-delta-reviewed.patch').read_text()
    new_patch = git('diff', '--', 'tests').decode()
    old_material, new_material = changed_material(old_patch), changed_material(new_patch)
    material_changes = [line for line in difflib.unified_diff(old_material, new_material, n=0)
                        if (line.startswith('+') and not line.startswith('+++'))
                        or (line.startswith('-') and not line.startswith('---'))]
    expected_fixture_delta = '++  meta.set("site_publication_generation",store.site_publication_generation());'
    assert material_changes == [expected_fixture_delta]
    old_tests = git('show', BASE + ':tests/cache_freshness.cpp').decode()
    new_tests = (ROOT / 'tests/cache_freshness.cpp').read_text()
    original_name = 'direct_cache_guard_does_not_require_a_redirect_publication_fence'
    def extract_test(text: str) -> str:
        marker = 'TEST(' + original_name + ')'
        return marker + text.split(marker, 1)[1].split('\nTEST(', 1)[0]
    assert extract_test(old_tests) == extract_test(new_tests)

    snapshot_path = EVIDENCE / 'old-validation-before.json'
    snapshot = json.loads(snapshot_path.read_bytes())
    assert sha(snapshot_path) == sha(ROOT.parent / 'bzs-old-validation-sha256.json')
    entries = git('ls-tree', '-rz', '--full-tree', BASE)
    blobs = {}
    for entry in entries.split(b'\0'):
        if entry:
            meta, name = entry.split(b'\t', 1)
            _, kind, oid = meta.split()
            if kind == b'blob':
                blobs[name.decode()] = oid.decode()
    old_validation = {name: oid for name, oid in blobs.items() if name.startswith('validation/')}
    assert set(old_validation) == set(snapshot) and len(snapshot) == 2793
    payload = ('\n'.join(old_validation.values()) + '\n').encode()
    batch = io.BytesIO(subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT, input=payload))
    for name, oid in old_validation.items():
        header = batch.readline().decode().split()
        assert header[:2] == [oid, 'blob']
        data = batch.read(int(header[2]))
        assert batch.read(1) == b'\n'
        assert hashlib.sha256(data).hexdigest() == snapshot[name] == sha(ROOT / name)
    assert not batch.read()

    protected = ['config', 'tests/golden', 'docs/planning', '.github/workflows',
                 'CMakeLists.txt', 'CMakePresets.json', 'tools/requirements-s1-lock.txt',
                 'tools/requirements-test.txt', 'python/pyproject.toml']
    assert git('diff', '--name-only', BASE, '--', *protected) == b''
    assert git('ls-files', '--others', '--exclude-standard', '--', *protected) == b''
    prior_state_text = git('show', BASE + ':docs/implementation_status.json').decode()
    state_text = (ROOT / 'docs/implementation_status.json').read_text()
    prior_state, state = json.loads(prior_state_text), json.loads(state_text)
    def feature_bytes(text: str) -> bytes:
        return text.split('  "features": ', 1)[1].split('  "current_results": ', 1)[0].encode()
    assert feature_bytes(prior_state_text) == feature_bytes(state_text)
    assert len(state['features']) == 83 and state['planned_acceptance_items'] == 161
    assert state['status_counts'] == {'partial': 37, 'not_started': 46, 'accepted': 0}
    assert state['historical_reports'][:len(prior_state['historical_reports'])] == prior_state['historical_reports']
    sources = json.loads((ROOT / 'config/sources.json').read_bytes())['sources']
    sites = json.loads((ROOT / 'config/sites.json').read_bytes())['sites']
    assert len(sources) == 17 and not any(source.get('enabled') for source in sources) and sites == []
    review_ref = git('rev-parse', 'origin/review/claude-2026-10-24').decode().strip()
    assert review_ref == '30f058557e3f7ebb873e9780759323b49cfe9636'

    archive = json.loads((EVIDENCE / 'independent/archive-manifest.json').read_bytes())
    for item in archive['files']:
        assert sha(EVIDENCE / 'independent' / item['path']) == item['sha256']
        assert sha(OUT / item['path']) == item['sha256']

    evidence_inputs = ['ctest-command.json', 'ctest-inputs.json', 'ctest.log', 'ctest-detail.log',
                       'ctest.xml', 'documentation-after-ctest.json', 'preservation.json']
    result = {
        'reviewer': '/root/handoff_audit',
        'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
        'verdict': 'pass for closed CTest, four documentation corrections, exact source/binary inputs and retained history',
        'scope': 'Read-only independent evidence audit; no product tests rerun. Full sanitizer suite and final consolidated report were not part of this check.',
        'baseline_commit': BASE,
        'review_script_sha256': sha(Path(__file__)),
        'evidence_inputs_sha256': {name: sha(FINAL / name) for name in evidence_inputs},
        'ctest': {
            'tests': 27, 'passed': 27, 'failed': 0, 'skipped': 0, 'disabled': 0,
            'test_names': detail_names, 'full_detail_matches_original_LastTest_log': True,
            'all_27_sections_have_complete_output_and_pass_markers': True,
            'native_suites': 15, 'native_test_cases': sum(native_counts),
            'native_assertions': sum(native_assertions), 'native_summary_lines': native_summaries,
            'python_worker_passed': 1281, 'cache_cases': 63, 'cache_assertions': 1323,
            'counts_note': 'Native and Python counts are components of the 27 CTest entries; independent and sanitizer replays overlap these scenarios, and none is a count of blueprint acceptance items.',
            'timezone_note': 'CTest raw detail/XML displays CST (UTC+08:00); runner timestamp is UTC. Both identify the same 2026-10-24 17:08 UTC execution.',
        },
        'input_binding': {
            'input_files': len(current), 'input_path_sets_equal': True,
            'changes_since_ctest_exactly_documented_four_markdown_files': actual_changes,
            'all_other_inputs_unchanged': True, 'recorded_binary_files': len(command['binaries_after']),
            'binaries_before_after_current_equal': True,
            'production_equal_independent_v2': reviewed['production_hashes'],
            'debug_library_sha256': sha(ROOT / 'build/libbzs_core.a'),
            'asan_library_sha256': sha(ROOT / 'build-asan/libbzs_core.a'),
        },
        'documentation_review': {
            'previous_five_stale_current_schema_or_batch_references_resolved': True,
            'source_backoff_schema13_scoped_as_history': True,
            'current_schema14_and_historical_auxiliary_migration_distinguished': True,
            'test_delta_since_previous_static_review': material_changes,
            'current_test_patch_sha256': hashlib.sha256(new_patch.encode()).hexdigest(),
            'original_direct_no_fence_test_byte_preserved': True,
            'no_existing_test_assertion_weakened': True,
        },
        'preservation': {
            'old_validation_count': len(snapshot), 'all_baseline_blobs_snapshot_and_current_bytes_equal': True,
            'snapshot_sha256': sha(snapshot_path), 'protected_paths_unchanged': protected,
            'no_new_untracked_files_in_protected_paths': True,
            'feature_json_bytes_unchanged': True, 'feature_json_sha256': hashlib.sha256(feature_bytes(state_text)).hexdigest(),
            'feature_count': 83, 'acceptance_items': 161, 'status_counts': state['status_counts'],
            'historical_reports_prefix_preserved': True, 'registered_sources': 17, 'enabled_sources': 0, 'sites': 0,
            'local_read_only_audit_ref': review_ref, 'original_independent_archive_files_intact': len(archive['files']),
            'supplementary_dependency_binding': 'Separately frozen before execution and checked again after CTest against actual baseline Git blobs; not part of run.py direct snapshot.',
        },
        'pending_scope': ['Closed full sanitizer command records and final consolidated report require a separate review.',
                          'Remote published commit/tree and exact-commit Linux/Windows results are not asserted here.'],
    }
    with (OUT / 'post-ctest-review.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('verdict', 'reviewed_at_utc')}, ensure_ascii=False))
    print(json.dumps({'ctest': 27, 'native_cases': sum(native_counts), 'native_assertions': sum(native_assertions),
                      'python_passed': 1281, 'input_files': len(current), 'binary_files': len(command['binaries_after']),
                      'preserved_old_validation': len(snapshot), 'preserved_independent_archive': len(archive['files'])}))


if __name__ == '__main__':
    main()
