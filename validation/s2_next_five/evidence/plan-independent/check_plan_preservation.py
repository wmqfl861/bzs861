from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path('/workspace/scratch/80f41eb198a3/bzs861')
OUT = Path(__file__).resolve().parent
BASE = '2c536e9834c4897b0c70e3bd6651b765cc67f45e'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def baseline(path):
    return git('show', f'{BASE}:{path}')


def tracked_under(prefix):
    return git('ls-tree', '-r', '--name-only', BASE, prefix).decode().splitlines()


def hash_group(paths):
    result = {}
    for path in paths:
        old = digest(baseline(path))
        new = digest((ROOT / path).read_bytes()) if (ROOT / path).is_file() else None
        result[path] = {'baseline_sha256': old, 'current_sha256': new, 'unchanged': old == new}
    return result


def read_json_pair(path):
    return json.loads(baseline(path)), json.loads((ROOT / path).read_bytes())


def changes(before, after, path=''):
    if type(before) is not type(after):
        return [{'path': path, 'before': before, 'after': after}]
    if isinstance(before, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                result.append({'path': f'{path}/{key}', 'before': before.get(key), 'after': after.get(key), 'presence_changed': True})
            else:
                result.extend(changes(before[key], after[key], f'{path}/{key}'))
        return result
    if isinstance(before, list):
        if len(before) != len(after):
            return [{'path': path, 'before': before, 'after': after}]
        return [item for i, (x, y) in enumerate(zip(before, after)) for item in changes(x, y, f'{path}/{i}')]
    return [] if before == after else [{'path': path, 'before': before, 'after': after}]


backlog_before, backlog = read_json_pair('docs/planning/requirements_and_backlog.json')
slices_before, slices = read_json_pair('docs/planning/slices_v1.2.json')
status_before, status = read_json_pair('docs/implementation_status.json')
slice_changes = changes(slices_before, slices)


def allowed_slice_change(change):
    path, before, after = change['path'], change['before'], change['after']
    if path.startswith('/acceptance_mapping/') and path.endswith('/execution_status'):
        return before == 'not_signed_off_for_0.1.8' and after == 'not_signed_off_for_0.1.9'
    return (path, before, after) in [
        ('/review_revision', '2026-10-20', '2026-10-21'),
        ('/implementation_stage', '0.1.8_s2_five_stages_integrated', '0.1.9_s2_next_five_increments'),
        ('/scope_policy/current_report', 'validation/s2_five_stages/final-report.json', 'validation/s2_next_five/final-report.json'),
    ]


acceptance_before = [{k: v for k, v in row.items() if k != 'execution_status'} for row in slices_before['acceptance_mapping']]
acceptance_after = [{k: v for k, v in row.items() if k != 'execution_status'} for row in slices['acceptance_mapping']]
stable_feature_keys = ['feature_id', 'task_id', 'name', 'slice', 'v11_phase', 'status', 'full_work_package_accepted', 'received_candidate_status', 'baseline_main_status', 'retained_main_slice', 'baseline_test_ids', 'mandatory_deferred_semantics']
feature_before = [{k: v for k, v in row.items() if k in stable_feature_keys} for row in status_before['features']]
feature_after = [{k: v for k, v in row.items() if k in stable_feature_keys} for row in status['features']]
feature_ids = [row['feature_id'] for row in status['features']]
mandatory_before = [{k: v for k, v in row.items() if k in ['feature_id', 'mandatory_deferred_semantics']} for row in status_before['features']]
mandatory_after = [{k: v for k, v in row.items() if k in ['feature_id', 'mandatory_deferred_semantics']} for row in status['features']]
historical_paths = tracked_under('validation/s2_five_stages')
golden_paths = tracked_under('tests/golden')
historical_current = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'validation/s2_five_stages').rglob('*') if p.is_file())
golden_current = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'tests/golden').rglob('*') if p.is_file())
planning_paths = tracked_under('docs/planning')
immutable_planning_paths = [p for p in planning_paths if p not in ['docs/planning/README.md', 'docs/planning/development_blueprint_v1.2.md', 'docs/planning/slices_v1.2.json']]
groups = {
    'historical_s2_five_stages': hash_group(historical_paths),
    # Golden bytes are only hashed. They are never decoded, parsed or exported.
    'golden_hash_only': hash_group(golden_paths),
    'unchanged_planning_sources': hash_group(immutable_planning_paths),
    'default_configs': hash_group(['config/sources.json', 'config/sites.json']),
}
sources = json.loads((ROOT / 'config/sources.json').read_bytes())['sources']
sites = json.loads((ROOT / 'config/sites.json').read_bytes())['sites']
source_counts = {'registered': len(sources), 'enabled': sum(row.get('enabled') is True for row in sources), 'all_explicitly_disabled': all(row.get('enabled') is False for row in sources)}

blueprint_before = baseline('docs/planning/development_blueprint_v1.2.md').decode()
blueprint_after = (ROOT / 'docs/planning/development_blueprint_v1.2.md').read_text()
baseline_body = blueprint_before.split('## 1. 采用的排序与本轮边界', 1)[1]
current_body = blueprint_after.split('## 1. 采用的排序与本轮边界', 1)[1].split('\n## 2026-10-21 五个接续增量', 1)[0].rstrip() + '\n'
checks = {
    'backlog_complete_object_equal': backlog_before == backlog,
    '83_packages': len(backlog['features']) == len(slices['packages']) == len(status['features']) == 83,
    'unique_feature_ids': len(set(feature_ids)) == 83,
    '10_slices_exactly_preserved': len(slices['slices']) == 10 and slices_before['slices'] == slices['slices'],
    'packages_full_objects_exactly_preserved': slices_before['packages'] == slices['packages'],
    'total_effort_exactly_preserved': slices_before['total_effort'] == slices['total_effort'],
    '161_acceptance_objects_preserved_except_not_signed_off_version': len(backlog['acceptance']) == len(slices['acceptance_mapping']) == 161 and acceptance_before == acceptance_after,
    'only_allowed_slice_metadata_changes': all(allowed_slice_change(change) for change in slice_changes),
    'stable_feature_fields_exactly_preserved': feature_before == feature_after,
    'mandatory_deferred_semantics_exactly_preserved': mandatory_before == mandatory_after,
    'status_36_47_0_preserved': status['status_counts'] == status_before['status_counts'] == {'partial': 36, 'not_started': 47, 'accepted': 0} and Counter(row['status'] for row in status['features']) == {'partial': 36, 'not_started': 47},
    'no_package_or_slice_signed_off': not any(row['full_work_package_accepted'] for row in status['features']) and status['full_work_packages_accepted'] == 0 and status['slices_accepted'] == [] and status['milestones_accepted'] == [] and status['planned_acceptance_suite_executed'] is False,
    'historical_file_set_preserved': sorted(historical_paths) == historical_current,
    'golden_exact_two_file_set_preserved': sorted(golden_paths) == golden_current == ['tests/golden/manifest.json', 'tests/golden/queries.json'],
    'all_protected_bytes_unchanged': all(row['unchanged'] for group in groups.values() for row in group.values()),
    '17_disabled_sources_and_empty_sites': source_counts == {'registered': 17, 'enabled': 0, 'all_explicitly_disabled': True} and sites == [],
    'original_blueprint_body_preserved_before_new_progress_appendix': baseline_body == current_body,
}
observed_docs = ['docs/planning/development_blueprint_v1.2.md', 'docs/planning/slices_v1.2.json', 'docs/implementation_status.json', 'docs/HTTP_REVALIDATION.md', 'docs/handoff/S2_NEXT_FIVE_20261021.md', 'docs/DEVELOPMENT_STATUS.md', 'docs/handoff/NEXT_AGENT.md']
record = {
    'kind': 'independent-plan-preservation-readonly-audit',
    'created_at': datetime.now(timezone.utc).isoformat(),
    'baseline': BASE,
    'observed_head': git('rev-parse', 'HEAD').decode().strip(),
    'reviewer': 'qa_ablation_agent',
    'scope': 'plan preservation, protected-byte hashes, default configurations and scoped document consistency; no product tests',
    'golden_handling': 'SHA-256 only; neither golden file decoded/parsed/exported; holdout text not inspected',
    'checks': checks,
    'all_structural_checks_passed': all(checks.values()),
    'slice_leaf_changes': slice_changes,
    'implementation_status_changed_path_groups': dict(Counter('/'.join('[]' if part.isdigit() else part for part in change['path'].split('/')) for change in changes(status_before, status))),
    'mandatory_semantics_present': sum('mandatory_deferred_semantics' in row for row in status['features']),
    'protected_file_counts': {name: len(group) for name, group in groups.items()},
    'source_counts': source_counts,
    'site_count': len(sites),
    'protected_hashes': groups,
    'document_hashes_at_audit': {p: digest((ROOT / p).read_bytes()) for p in observed_docs},
    'limits': 'Status/progress and final-result metadata are still being updated by the parent. This snapshot does not certify a future commit, CI result, product acceptance, source approval or real relevance score.',
}
target = OUT / 'plan-preservation.json'
target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'record': str(target), 'sha256': digest(target.read_bytes()), 'checks': checks, 'protected_file_counts': record['protected_file_counts'], 'source_counts': source_counts, 'site_count': len(sites)}, ensure_ascii=False, indent=2))
