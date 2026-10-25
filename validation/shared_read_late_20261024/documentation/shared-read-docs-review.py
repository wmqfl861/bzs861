from pathlib import Path
import hashlib
import json
import re
import subprocess

root = Path('/workspace/scratch/133513441190/bzs861')
baseline = 'a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89'
docs = ['docs/SHARED_READS.md', 'README.md', 'docs/API.md', 'docs/DEVELOPMENT_STATUS.md', 'HANDOFF_START_HERE.md', 'docs/GITHUB_HANDOFF.md', 'docs/handoff/NEXT_AGENT.md']
tests = ['tests/unit.cpp', 'tests/crawl.cpp', 'tests/map.cpp', 'tests/retention.cpp', 'tests/revalidation.cpp', 'tests/revocation.cpp', 'tests/site_publication.cpp']
def previous(name):
    return subprocess.check_output(['git', 'show', baseline + ':' + name], cwd=root).decode()
def digest(data):
    return hashlib.sha256(data).hexdigest()

report = {'baseline_commit': baseline, 'scope': 'Documentation and exact schema assertion review only; no runtime test or CI result asserted', 'documents': [], 'relative_file_links_checked': 0, 'missing_relative_file_links': [], 'history_preservation': {}, 'schema_assertion_changes': []}
for name in docs:
    data = (root / name).read_bytes()
    report['documents'].append({'path': name, 'sha256': digest(data)})
    for target in re.findall(r'\]\(([^\s)]+)(?:\s+[^)]*)?\)', data.decode()):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('#'):
            continue
        target = target.split('#', 1)[0]
        report['relative_file_links_checked'] += 1
        if not ((root / name).parent / target).exists():
            report['missing_relative_file_links'].append({'document': name, 'target': target})
assert not report['missing_relative_file_links'], report['missing_relative_file_links']

for name in ['HANDOFF_START_HERE.md', 'docs/handoff/NEXT_AGENT.md']:
    original_body = previous(name).split('\n\n', 1)[1]
    assert (root / name).read_text().endswith(original_body), name
    report['history_preservation'][name] = {'old_body_preserved': True, 'old_body_sha256': digest(original_body.encode())}
name = 'docs/DEVELOPMENT_STATUS.md'
original_body = previous(name).split('\n\n', 2)[2]
assert (root / name).read_text().endswith(original_body), name
report['history_preservation'][name] = {'old_body_preserved': True, 'old_body_sha256': digest(original_body.encode())}
name = 'docs/GITHUB_HANDOFF.md'
original_body = previous(name).split('## 当前接手（基线 ef45490）\n\n', 1)[1]
assert (root / name).read_text().endswith(original_body), name
report['history_preservation'][name] = {'old_body_preserved': True, 'old_body_sha256': digest(original_body.encode())}
name = 'README.md'
def historical_block(data):
    return data.split('## 历史交接与验证说明\n\n', 1)[1].split('\n## 已有数据与后续范围', 1)[0]
old_history = historical_block(previous(name))
assert historical_block((root / name).read_text()) == old_history
report['history_preservation'][name] = {'old_history_section_preserved': True, 'old_history_sha256': digest(old_history.encode())}

for name in tests:
    old = previous(name)
    current_changes = old.count('"PRAGMA user_version")==14')
    expected = old.replace('"PRAGMA user_version")==14', '"PRAGMA user_version")==15')
    future_changes = 0
    if name == 'tests/unit.cpp':
        replacements = [
            ('database_exec(future, "PRAGMA user_version=15;");', 'database_exec(future, "PRAGMA user_version=16;");'),
            ('CHECK(database_scalar(future, "PRAGMA user_version")==15);', 'CHECK(database_scalar(future, "PRAGMA user_version")==16);')
        ]
        for old_value, new_value in replacements:
            assert expected.count(old_value) == 1, old_value
            expected = expected.replace(old_value, new_value)
            future_changes += 1
    assert (root / name).read_text() == expected, name
    report['schema_assertion_changes'].append({'path': name, 'current_14_to_15': current_changes, 'future_15_to_16': future_changes, 'no_other_changes': True})
assert sum(item['current_14_to_15'] for item in report['schema_assertion_changes']) == 13
assert sum(item['future_15_to_16'] for item in report['schema_assertion_changes']) == 2

check = subprocess.run(['git', 'diff', '--check', '--', *docs, *tests], cwd=root, text=True, capture_output=True)
report['git_diff_check'] = {'exit_code': check.returncode, 'stdout': check.stdout, 'stderr': check.stderr}
assert check.returncode == 0
report['result'] = 'passed'
output = Path('/workspace/scratch/133513441190/shared-read-docs-review.json')
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'result': report['result'], 'documents': len(docs), 'relative_file_links_checked': report['relative_file_links_checked'], 'historical_bodies_preserved': len(report['history_preservation']), 'schema_test_files': len(tests), 'numeric_schema_changes_only': 15, 'report': str(output)}, ensure_ascii=False))
