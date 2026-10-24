import collections
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861')
draft = pathlib.Path('/tmp/bzs-continuation-handoff-20261024')
out = pathlib.Path('/tmp/bzs-continuation-docs-independent')
base = '59081e14fdda765844031e13f874e588334f1a36'
sha = lambda value: hashlib.sha256(value).hexdigest()
manifest = json.loads((draft / 'handoff-docs-manifest.json').read_text())
expected = {item['path']: item for item in manifest['files']}
patch = (draft / 'handoff-docs.patch').read_bytes()
assert sha(patch) == manifest['patch']['sha256']
actual_paths = re.findall(r'^diff --git a/(\S+) b/\1$', patch.decode(), re.M)
assert set(actual_paths) == set(expected) and len(actual_paths) == 8
replay = out / 'replay'
replay.mkdir(exist_ok=False)
files = {}
root_before = {}
for name, item in expected.items():
    if item['action'] == 'updated':
        baseline = subprocess.check_output(['git', 'show', base + ':' + name], cwd=root)
        original = (draft / 'before' / name).read_bytes()
        assert baseline == original
        assert sha(original) == item['before_sha256']
        target = replay / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(baseline)
        root_before[name] = sha((root / name).read_bytes())
    else:
        root_before[name] = sha((root / name).read_bytes()) if (root / name).exists() else None
    proposed = (draft / 'after' / name).read_bytes()
    assert sha(proposed) == item['after_sha256']
    files[name] = {'before_matches_git': item['action'] == 'updated',
                   'action': item['action'], 'after_sha256': sha(proposed)}

commands = []
for args in (['git', 'apply', '--check', str(draft / 'handoff-docs.patch')],
             ['git', 'apply', str(draft / 'handoff-docs.patch')]):
    result = subprocess.run(args, cwd=replay, capture_output=True, text=True)
    commands.append({'argv': args, 'cwd': str(replay), 'exit_code': result.returncode,
                     'stdout': result.stdout, 'stderr': result.stderr})
    assert result.returncode == 0
for name in expected:
    assert (replay / name).read_bytes() == (draft / 'after' / name).read_bytes()
    files[name]['replayed_bytes_equal_after'] = True

def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, key
        result[key] = value
    return result

def top_values(text):
    decoder = json.JSONDecoder(object_pairs_hook=no_duplicates)
    i = 1
    spans = {}
    while True:
        while text[i].isspace(): i += 1
        if text[i] == '}': break
        key, i = decoder.raw_decode(text, i)
        while text[i].isspace(): i += 1
        assert text[i] == ':'
        i += 1
        while text[i].isspace(): i += 1
        begin = i
        _, i = decoder.raw_decode(text, i)
        assert key not in spans
        spans[key] = text[begin:i].encode()
        while text[i].isspace(): i += 1
        if text[i] == '}': break
        assert text[i] == ','
        i += 1
    return spans

name = 'docs/implementation_status.json'
before_text = (draft / 'before' / name).read_text()
after_text = (draft / 'after' / name).read_text()
before = json.loads(before_text, object_pairs_hook=no_duplicates)
after = json.loads(after_text, object_pairs_hook=no_duplicates)
old_raw, new_raw = top_values(before_text), top_values(after_text)
assert before.keys() <= after.keys()
assert before['features'] == after['features'] and old_raw['features'] == new_raw['features']
assert len(after['features']) == 83
counts = collections.Counter(row['status'] for row in after['features'])
assert counts == {'partial': 37, 'not_started': 46}
assert after['status_counts'] == {'partial': 37, 'not_started': 46, 'accepted': 0}
assert not any(row.get('full_work_package_accepted') for row in after['features'])
changed = [key for key in before if before[key] != after[key]]
backup = after['historical_verification_claude_followup_20261024']
assert all(backup[key] == before[key] for key in changed)
old_history = [key for key in before if key.startswith('historical_') and isinstance(before[key], dict)] + ['json_concurrency_followup']
assert all(old_raw[key] == new_raw[key] for key in old_history)
assert all(after[key][:len(before[key])] == before[key] for key in ('historical_reports', 'historical_review_records'))
unchanged_raw = [key for key in before if key not in changed]
assert all(old_raw[key] == new_raw[key] for key in unchanged_raw)

configs = {}
for name in ('config/sources.json', 'config/sites.json', 'docs/planning/slices_v1.2.json'):
    data = (root / name).read_bytes()
    assert data == subprocess.check_output(['git', 'show', base + ':' + name], cwd=root)
    configs[name] = {'sha256': sha(data), 'matches_baseline': True}
sources = json.loads((root / 'config/sources.json').read_text())['sources']
sites = json.loads((root / 'config/sites.json').read_text())['sites']
assert len(sources) == 17 and not any(row['enabled'] for row in sources) and len(sites) == 0

source_bindings = []
for binding in manifest['source_bindings']:
    path = root.parent / binding['worktree'] / binding['path']
    actual = sha(path.read_bytes())
    assert actual == binding['sha256'], binding
    source_bindings.append({'path': str(path), 'sha256': actual, 'matches_author_bound_input': True})
root_after = {name: sha((root / name).read_bytes()) if (root / name).exists() else None for name in expected}
assert root_before == root_after
result = {
    'patch_sha256': sha(patch), 'author_patch_unchanged': sha((draft / 'handoff-docs.patch').read_bytes()) == sha(patch),
    'files': files, 'patch_replay': commands, 'no_duplicate_json_keys': True,
    'features': {'count': 83, 'status_counts': dict(counts), 'raw_value_bytes': len(old_raw['features']),
                 'raw_value_sha256': sha(old_raw['features']), 'raw_before_after_equal': True,
                 'all_full_work_package_accepted_false': True},
    'changed_original_top_level_keys': changed,
    'all_changed_values_preserved_in_new_history': True,
    'old_history_object_raw_bytes_unchanged': old_history,
    'other_unchanged_top_level_raw_values': unchanged_raw,
    'old_history_lists_only_append': {key: after[key][len(before[key]):] for key in ('historical_reports', 'historical_review_records')},
    'configs_and_plan': configs, 'sources': {'registered': 17, 'enabled': 0}, 'sites': {'registered': 0},
    'source_bindings': source_bindings, 'root_document_hashes_before': root_before,
    'root_document_hashes_after': root_after, 'root_documents_unchanged': True,
    'no_production_tests_reexecuted': True,
}
(out / 'artifact-checks.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'patch_replayed_exactly': True, 'features': result['features'],
                  'old_history_raw_fields': len(old_history), 'changed_keys': changed,
                  'root_documents_unchanged': True}, indent=2, ensure_ascii=False))
