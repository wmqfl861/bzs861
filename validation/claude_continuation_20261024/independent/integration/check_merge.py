import collections
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861')
out = pathlib.Path('/tmp/bzs-continuation-integration-independent')
base = '59081e14fdda765844031e13f874e588334f1a36'
authors = {
    'P2-5': '1c69836f393aee55032beae4bd027f4d286d1e4b',
    'P2-6': 'a9bac84266ee603e27e3957b80a54387e2c44820',
    'P2-7': '23cb1ceffbbc0e65dbcbc83baf44ac4a2159fedc',
    'P2-9': 'a30231a802ab05f0bb3f821c51ef03d156d796d8',
}
sha = lambda data: hashlib.sha256(data).hexdigest()
def git(*args): return subprocess.check_output(['git', *args], cwd=root)
def blob(commit, name): return git('show', commit + ':' + name)
def in_scope(name):
    return name.startswith(('src/', 'include/', 'apps/', 'python/', 'tests/', 'config/')) or name in {
        'CMakeLists.txt', 'CMakePresets.json', 'docs/LOCAL_SEARCH_CONCURRENCY.md',
        'docs/BACKGROUND_DISPATCH.md', 'docs/SOURCE_BACKOFF.md',
    }
def changes(commit): return {name for name in git('diff', '--name-only', base, commit).decode().splitlines() if in_scope(name)}

head_before = git('rev-parse', 'HEAD').decode().strip()
author_changes = {issue: sorted(changes(commit)) for issue, commit in authors.items()}
expected_paths = set().union(*(set(paths) for paths in author_changes.values()))
current_paths = {name for name in git('diff', '--name-only', base).decode().splitlines() if in_scope(name)}
assert current_paths == expected_paths, {'missing': expected_paths - current_paths, 'unexpected': current_paths - expected_paths}
current = {name: (root / name).read_bytes() for name in sorted(current_paths)}
assert all(current[name] == git('show', ':' + name) for name in current)
owners = {name: [issue for issue, paths in author_changes.items() if name in paths] for name in sorted(current)}
records = {}
for name, issues in owners.items():
    records[name] = {'owners': issues, 'working_sha256': sha(current[name]),
                     'index_blob': git('rev-parse', ':' + name).decode().strip(),
                     'bytes': len(current[name]), 'working_equals_index': True,
                     'author_sha256': {issue: sha(blob(authors[issue], name)) for issue in issues}}
    if len(issues) == 1:
        assert current[name] == blob(authors[issues[0]], name), name
        records[name]['relationship'] = 'byte-for-byte equal to sole author commit'

merge_records = {}
for name in ['src/store.cpp', 'tests/unit.cpp']:
    assert owners[name] == ['P2-5', 'P2-9']
    assert blob(head_before, name) == blob(authors['P2-5'], name)
    contents = {'base': blob(base, name), 'search': blob(authors['P2-5'], name),
                'retry': blob(authors['P2-9'], name)}
    with tempfile.TemporaryDirectory(prefix='merge-', dir=out) as temp:
        paths = {key: pathlib.Path(temp) / (key + '.cpp') for key in contents}
        for key, path in paths.items(): path.write_bytes(contents[key])
        argv = ['git', 'merge-file', '-p', '--diff3', str(paths['search']), str(paths['base']), str(paths['retry'])]
        run = subprocess.run(argv, cwd=out, capture_output=True)
        assert run.returncode == 0, (name, run.returncode, run.stderr.decode())
        assert run.stdout == current[name], name
        reverse_argv = ['git', 'merge-file', '-p', '--diff3', str(paths['retry']), str(paths['base']), str(paths['search'])]
        reverse = subprocess.run(reverse_argv, cwd=out, capture_output=True)
        assert reverse.returncode == 0 and reverse.stdout == current[name], name
        merge_records[name] = {'base': base, 'left': authors['P2-5'], 'right': authors['P2-9'],
                               'source_sha256': {key: sha(value) for key, value in contents.items()},
                               'argv': argv, 'exit_code': run.returncode, 'stderr': run.stderr.decode(),
                               'reverse_argv': reverse_argv, 'reverse_exit_code': reverse.returncode,
                               'merged_sha256': sha(run.stdout), 'both_orderings_equal_root_bytes': True}
    records[name]['relationship'] = 'exact conflict-free three-way merge in both orientations'

name = 'include/bzs/web.hpp'
original = blob(base, name)
concurrency = blob(authors['P2-6'], name)
retry = blob(authors['P2-9'], name)
anchor = b'namespace bzs {\n'
old_comment = b'  // Worker RPC port. searchd implements it over loopback HTTP; tests use a fake.\n'
new_comment = (b'  // Worker RPC port. Calls from HTTP business threads and independent background\n'
               b'  // lanes may overlap; implementations must support concurrent calls. searchd\n'
               b'  // gives each RPC its own loopback HTTP connection; tests use a synchronized fake.\n')
constant = (b'  // Search-source backoff is bounded independently of automatic-site/robots policy.\n'
            b'  inline constexpr std::int64_t source_retry_after_limit_ms=24LL*60*60*1000;\n')
assert original.count(anchor) == original.count(old_comment) == 1
assert concurrency == original.replace(old_comment, new_comment)
assert retry == original.replace(anchor, anchor + constant)
expected = original.replace(old_comment, new_comment).replace(anchor, anchor + constant)
assert expected == current[name]
records[name]['relationship'] = 'exact union: P2-6 concurrent WorkerPort contract comment plus P2-9 limit comment/constant; no other change'
records[name]['base_sha256'] = sha(original)
records[name]['expected_union_sha256'] = sha(expected)

name = 'apps/searchd/main.cpp'
original = blob(base, name)
http = blob(authors['P2-7'], name)
dispatch = blob(authors['P2-6'], name)
old_handle = b'    std::jthread worker_thread;\n'
new_handle = b'    BackgroundWorker worker_thread;\n'
assert original.count(old_handle) == http.count(old_handle) == 1
assert dispatch == original.replace(old_handle, new_handle)
expected = http.replace(old_handle, new_handle)
assert expected == current[name]
assert current[name] == blob('8f753c24e55779bfca840a51037f7b77068f0daa', name)
records[name]['relationship'] = 'exact reviewed P2-7 main.cpp plus the one P2-6 BackgroundWorker handle replacement'
records[name]['expected_union_sha256'] = sha(expected)
records[name]['matches_already_integrated_p2_6_commit'] = True

name = 'tests/unit.cpp'
test_names = lambda data: re.findall(rb'^\s+TEST\((\w+)\)', data, re.M)
name_sets = {issue: test_names(blob(commit, name)) for issue, commit in authors.items() if issue in ['P2-5', 'P2-9']}
base_names, current_names = test_names(blob(base, name)), test_names(current[name])
assert all(len(names) == len(set(names)) for names in [base_names, current_names, *name_sets.values()])
assert set(current_names) == set().union(*(set(names) for names in name_sets.values()))
tests = {'baseline_count': len(base_names), 'author_counts': {issue: len(names) for issue, names in name_sets.items()},
         'integrated_count': len(current_names), 'all_author_test_bodies_preserved_by_full_file_merge': True,
         'new_test_names': {issue: [name.decode() for name in names if name not in base_names] for issue, names in name_sets.items()},
         'no_duplicate_names': True}

after = {name: (root / name).read_bytes() for name in current}
assert current == after
assert all(after[name] == git('show', ':' + name) for name in after)
baseline_guard = {}
for name in ['config/sources.json', 'config/sites.json', 'CMakeLists.txt', 'CMakePresets.json']:
    data = (root / name).read_bytes()
    assert data == blob(base, name)
    baseline_guard[name] = {'sha256': sha(data), 'equals_baseline': True}

result = {
    'scope': 'Read-only integration equality/merge verification; no re-review of P2-5 implementation or production tests',
    'base': base, 'authors': authors, 'root_head_before': head_before,
    'root_head_after': git('rev-parse', 'HEAD').decode().strip(),
    'git_version': git('--version').decode().strip(),
    'expected_changed_files': len(expected_paths), 'author_changed_files': author_changes,
    'current_scope_equals_author_union': True, 'files': records, 'three_way_merges': merge_records,
    'formal_unit_registration_union': tests, 'unchanged_baseline_guards': baseline_guard,
    'all_checked_working_files_equal_index': True, 'source_bytes_unchanged_during_check': True,
    'root_or_running_validation_logs_modified': False, 'production_tests_executed': False,
}
(out / 'merge-checks.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'changed_files': len(records), 'single_author_exact_files': sum(len(value['owners']) == 1 for value in records.values()),
                  'store_merge': merge_records['src/store.cpp']['merged_sha256'],
                  'unit_merge': merge_records['tests/unit.cpp']['merged_sha256'],
                  'web_header': records['include/bzs/web.hpp']['working_sha256'],
                  'main': records['apps/searchd/main.cpp']['working_sha256'],
                  'unit_test_counts': {key: value for key, value in tests.items() if key.endswith('count') or key == 'author_counts'},
                  'passed': True}, indent=2))
