import hashlib
import json
import pathlib

draft = pathlib.Path('/tmp/bzs-continuation-handoff-20261024')
out = pathlib.Path('/tmp/bzs-continuation-docs-independent')
results = {}
for name in ['HANDOFF_START_HERE.md', 'docs/DEVELOPMENT_STATUS.md', 'docs/handoff/NEXT_AGENT.md']:
    before = (draft / 'before' / name).read_bytes()
    after = (draft / 'after' / name).read_bytes()
    body = before.split(b'\n', 1)[1]
    assert body in after, name
    results[name] = {'original_body_after_title_preserved_exactly': True,
                     'body_sha256': hashlib.sha256(body).hexdigest()}

name = 'README.md'
before = (draft / 'before' / name).read_text()
after = (draft / 'after' / name).read_text()
intro = before[before.index('**状态：'):before.index('项目自己')]
paragraphs = intro.rstrip('\n').split('\n\n')
assert len(paragraphs) == 3 and all(paragraph in after for paragraph in paragraphs)
old_test = next(line for line in before.splitlines() if line.startswith('本轮测试分为'))
assert old_test in after
results[name] = {
    'three_original_intro_paragraphs_preserved_exactly': True,
    'old_test_summary_preserved_exactly': True,
    'initial_overstrict_contiguous_block_check': {
        'matched': intro in after,
        'cause': 'The old introductory block ended with three newline bytes; the historical copy uses two before its next explanatory paragraph. All three paragraph bodies and their internal separators are unchanged.',
        'material_history_change': False,
    },
}

name = 'docs/GITHUB_HANDOFF.md'
before = (draft / 'before' / name).read_text()
after = (draft / 'after' / name).read_text()
paragraphs = [paragraph for paragraph in before.split('\n\n') if paragraph.startswith(('本批从已发布 main', '本批准确输入'))]
assert len(paragraphs) == 2 and all(paragraph in after for paragraph in paragraphs)
old_rows = [line for line in before.splitlines() if line.startswith('|')]
assert all(line in after.splitlines() for line in old_rows)
results[name] = {'old_history_table_lines_preserved_exactly': len(old_rows),
                 'old_current_scope_paragraphs_preserved_exactly': 2}
(out / 'prose-history-checks.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(results, indent=2, ensure_ascii=False))
