import collections
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861')
batch = root / 'validation/claude_continuation_20261024'
out = pathlib.Path('/tmp/bzs-continuation-receipt-independent')
base = '59081e14fdda765844031e13f874e588334f1a36'
sha = lambda data: hashlib.sha256(data).hexdigest()
read = lambda path: json.loads(path.read_text())
def git(*args): return subprocess.check_output(['git', *args], cwd=root)
def tree(rev, *paths):
    result = {}
    for row in git('ls-tree', '-r', '-z', rev, *paths).split(b'\0'):
        if not row: continue
        info, name = row.split(b'\t', 1)
        mode, kind, oid = info.decode().split()
        assert kind == 'blob'
        result[name.decode()] = (mode, oid)
    return result
def blob_id(data): return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

receipt = read(out / 'input-snapshot/final-report.json')
final_manifest_path = batch / 'final-verification/execution-inputs.json'
assert sha(final_manifest_path.read_bytes()) == receipt['final_documented_inputs_sha256']
final_files = read(final_manifest_path)['files']
assert len(final_files) == 226
for name, digest in final_files.items(): assert sha((root / name).read_bytes()) == digest, name
listed = set()
for args in [('ls-files', '-z'), ('ls-files', '--others', '--exclude-standard', '-z')]:
    listed.update(name.decode() for name in git(*args).split(b'\0') if name and not name.startswith(b'validation/'))
assert listed == {name for name in final_files if not name.startswith('validation/')}

old_files = read(batch / 'p2-9/execution-inputs.json')['files']
changed = {name for name in old_files.keys() | final_files.keys() if old_files.get(name) != final_files.get(name)}
draft = read(batch / 'author/documentation/handoff-docs-manifest.json')
allowed = {item['path']: item for item in draft['files']}
assert changed == set(allowed) and len(changed) == 8
assert sha((batch / 'author/documentation/handoff-docs.patch').read_bytes()) == draft['patch']['sha256']
for name in changed:
    assert old_files.get(name) == allowed[name]['before_sha256']
    assert final_files[name] == allowed[name]['after_sha256']
    assert (root / name).read_bytes() == (batch / 'author/documentation/after' / name).read_bytes()

counts = collections.Counter()
old_rows = {}
for name, (mode, oid) in tree(base, 'validation', 'config', 'docs/planning', '.github/workflows').items():
    path = root / name
    assert mode in ['100644', '100755'] and path.is_file() and not path.is_symlink()
    data = path.read_bytes()
    assert blob_id(data) == oid, name
    counts[name.split('/')[0]] += 1
    old_rows[name] = {'git_blob': oid, 'sha256': sha(data)}
assert dict(counts) == receipt['preserved_scope']['unchanged_baseline_files']
assert counts['validation'] == 2051

def raw_value(text, key):
    start = re.search(r'^  "' + re.escape(key) + r'": ', text, re.M).end()
    _, end = json.JSONDecoder().raw_decode(text, start)
    return text[start:end].encode()
before_text = git('show', base + ':docs/implementation_status.json').decode()
after_text = (root / 'docs/implementation_status.json').read_text()
before, after = json.loads(before_text), json.loads(after_text)
assert raw_value(before_text, 'features') == raw_value(after_text, 'features')
assert sha(raw_value(after_text, 'features')) == receipt['preserved_scope']['features_raw_sha256']
histories = [name for name in before if name.startswith('historical_') and isinstance(before[name], dict)] + ['json_concurrency_followup']
assert all(raw_value(before_text, name) == raw_value(after_text, name) for name in histories)
assert after['status_counts'] == {'partial': 37, 'not_started': 46, 'accepted': 0}
assert len(after['features']) == 83 and not any(item.get('full_work_package_accepted') for item in after['features'])
assert all(after['historical_verification_claude_followup_20261024'][name] == value
           for name, value in before.items() if after.get(name) != value)
assert json.loads((root / 'config/sites.json').read_text())['sites'] == []
sources = json.loads((root / 'config/sources.json').read_text())['sources']
assert len(sources) == 17 and not any(item.get('enabled') for item in sources)

mapping = read(out / 'input-snapshot/commit-map.json')
parent = base
remote_records = []
for entry in mapping['commits']:
    path = batch / 'publication-preparation' / (entry['remote'] + '.json')
    remote = read(path)
    assert remote['sha'] == entry['remote']
    assert remote['tree']['sha'] == entry['tree'] == git('rev-parse', entry['local'] + '^{tree}').decode().strip()
    assert [item['sha'] for item in remote['parents']] == [entry['parent']] == [parent]
    remote_records.append({'issue': entry['issue'], 'local': entry['local'], 'remote': entry['remote'],
                           'tree': entry['tree'], 'parent': parent, 'api_record_sha256': sha(path.read_bytes())})
    parent = entry['remote']
refs = {}
for file, expected_ref, expected_commit in [
    ('main-before-publication.json', 'refs/heads/main', base),
    ('review-before-publication.json', 'refs/heads/review/claude-2026-10-24', receipt['read_only_review']),
]:
    path = batch / 'publication-preparation' / file
    ref = read(path)
    assert ref['ref'] == expected_ref and ref['object']['sha'] == expected_commit
    refs[file] = {'ref': expected_ref, 'sha': expected_commit, 'api_record_sha256': sha(path.read_bytes())}

reconstruction = read(batch / 'author/retry_after/product-input-reconstruction.json')
base_tree = tree(base)
virtual = {name: value for name, value in base_tree.items() if not name.startswith('validation/')}
patch_names = set()
for item in reconstruction['patches']:
    data = (batch / 'author/retry_after' / item['patch']).read_bytes()
    assert sha(data) == item['sha256']
    patch_names.update(re.findall(r'^diff --git a/(\S+) b/\1$', data.decode(), re.M))
assert not any(name.startswith(('validation/', '/', '../')) for name in patch_names)
replayed = []
with tempfile.TemporaryDirectory(prefix='rejected-input-replay-', dir=out) as temp:
    temp = pathlib.Path(temp)
    for name in patch_names & virtual.keys():
        path = temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(git('show', base + ':' + name))
        path.chmod(0o755 if virtual[name][0] == '100755' else 0o644)
    for item in reconstruction['patches']:
        patch = batch / 'author/retry_after' / item['patch']
        commands = []
        for extra in [['--check'], []]:
            argv = ['git', 'apply', *extra, str(patch)]
            run = subprocess.run(argv, cwd=temp, capture_output=True, text=True)
            assert run.returncode == 0, (item['patch'], run.stderr)
            commands.append({'argv': argv, 'exit_code': run.returncode, 'stderr': run.stderr})
        for name in patch_names:
            path = temp / name
            if path.exists():
                mode = '100755' if path.stat().st_mode & 0o111 else '100644'
                virtual[name] = (mode, blob_id(path.read_bytes()))
            else: virtual.pop(name, None)
        actual = {name: value for name, value in tree(item['to_author_commit']).items() if not name.startswith('validation/')}
        assert virtual == actual
        reconstructed_tree = tree(item['reconstructed_tree_with_baseline_validation'])
        assert reconstructed_tree == (virtual | {name: value for name, value in base_tree.items() if name.startswith('validation/')})
        replayed.append({'patch': item['patch'], 'sha256': item['sha256'], 'commands': commands,
                         'all_non_validation_blobs_equal_author': item['to_author_commit'],
                         'non_validation_count': len(actual), 'reported_reconstructed_tree_matches': True})

for item in receipt['independent_records']:
    assert sha((batch / item['path']).read_bytes()) == item['sha256'], item['path']
assert sha((batch / 'final-report.json').read_bytes()) == sha((out / 'input-snapshot/final-report.json').read_bytes())

result = {'final_manifest_sha256': sha(final_manifest_path.read_bytes()), 'all_226_current_inputs_match': True,
          'non_validation_input_count': len(listed), 'exact_document_delta': sorted(changed),
          'all_other_product_test_build_recorder_inputs_identical_to_final_full_gate': True,
          'old_baseline_files': dict(counts), 'baseline_blob_scan': old_rows,
          'feature_raw_sha256': sha(raw_value(after_text, 'features')), 'all_83_feature_bytes_preserved': True,
          'old_raw_history_objects_preserved': histories, 'reserved_source_state': {'registered': 17, 'enabled': 0, 'sites': 0},
          'remote_product_commit_api_bindings': remote_records, 'remote_refs_at_prepublication_read': refs,
          'rejected_and_corrected_product_inputs_independently_replayed': replayed,
          'all_independent_record_hashes_match_final_report': True, 'root_edits_or_product_test_execution': False}
(out / 'scope-and-mapping-audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'inputs': 226, 'old_files': dict(counts), 'features': 83, 'history_objects': len(histories),
                  'doc_delta': len(changed), 'remote_commits': len(remote_records), 'product_reconstructions': len(replayed),
                  'all_passed': True}, indent=2))
