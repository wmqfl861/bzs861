import hashlib
import json
import pathlib
import posixpath
import re
import subprocess
from urllib.parse import unquote, urlsplit

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861')
draft = pathlib.Path('/tmp/bzs-continuation-handoff-20261024')
out = pathlib.Path('/tmp/bzs-continuation-docs-independent')
sha = lambda data: hashlib.sha256(data).hexdigest()
manifest = json.loads((draft / 'handoff-docs-manifest.json').read_text())
planned = 'validation/claude_continuation_20261024/README.md'
pending_contracts = {
    'docs/LOCAL_SEARCH_CONCURRENCY.md': '1c69836f393aee55032beae4bd027f4d286d1e4b',
    'docs/BACKGROUND_DISPATCH.md': 'a9bac84266ee603e27e3957b80a54387e2c44820',
    'docs/SOURCE_BACKOFF.md': 'a30231a802ab05f0bb3f821c51ef03d156d796d8',
}
links = []
for item in manifest['files']:
    if not item['path'].endswith('.md'): continue
    name = item['path']
    text = (draft / 'after' / name).read_text()
    for match in re.finditer(r'\[[^\]]+\]\(([^)\s]+)(?:\s+"[^"]*")?\)', text):
        target = match.group(1)
        url = urlsplit(target)
        if url.scheme or url.netloc: continue
        path = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path))) if url.path else name
        assert not path.startswith('../'), (name, target)
        record = {'document': name, 'line': text[:match.start()].count('\n') + 1,
                  'target': target, 'repository_path': path}
        if (out / 'replay' / path).exists(): record['resolved_from'] = 'independently_replayed_document'
        elif path in pending_contracts:
            content = subprocess.check_output(['git', 'show', pending_contracts[path] + ':' + path], cwd=root)
            record.update(resolved_from='fixed_product_commit', commit=pending_contracts[path], sha256=sha(content))
        elif (root / path).exists(): record['resolved_from'] = 'existing_root_file_or_directory'
        elif path == planned: record['resolved_from'] = 'declared_pending_integration_report'
        else: record['resolved_from'] = 'unexpected_missing'
        links.append(record)
assert not any(row['resolved_from'] == 'unexpected_missing' for row in links)

ci_dir = root / 'validation/claude_continuation_20261024/baseline-ci'
run_bytes, jobs_bytes = (ci_dir / 'run.json').read_bytes(), (ci_dir / 'jobs.json').read_bytes()
run, jobs = json.loads(run_bytes), json.loads(jobs_bytes)
assert run['id'] == 40067206826
assert run['head_sha'] == '59081e14fdda765844031e13f874e588334f1a36'
assert run['head_commit']['tree_id'] == '5d555fabc43180aa905e0581a301a3bafbeca1d5'
assert run['event'] == 'push' and run['status'] == 'completed' and run['conclusion'] == 'success'
assert len(jobs['jobs']) == 2 and {job['name'] for job in jobs['jobs']} == {'windows', 'linux'}
assert all(job['run_id'] == run['id'] and job['status'] == 'completed' and job['conclusion'] == 'success' for job in jobs['jobs'])
plan = json.loads((root / 'docs/planning/slices_v1.2.json').read_text())
assert len(plan['packages']) == 83 and len(plan['acceptance_mapping']) == 161 and len(plan['slices']) == 10

bindings = {}
paths = [
    root / 'apps/searchd/main.cpp', root / 'tests/integration.py',
    root.parent / 'bzs861-next-dispatcher/src/service.cpp',
    root.parent / 'bzs861-next-dispatcher/include/bzs/service.hpp',
    root.parent / 'bzs861-next-dispatcher/docs/BACKGROUND_DISPATCH.md',
    root.parent / 'bzs861-next-search-lock/src/store.cpp',
    root.parent / 'bzs861-next-search-lock/src/query.cpp',
    root.parent / 'bzs861-next-search-lock/docs/LOCAL_SEARCH_CONCURRENCY.md',
    root.parent / 'bzs861-next-retry/src/store.cpp',
    root.parent / 'bzs861-next-retry/src/web.cpp',
    root.parent / 'bzs861-next-retry/docs/SOURCE_BACKOFF.md',
    pathlib.Path('/tmp/bzs-next-http-independent/review.json'),
    pathlib.Path('/tmp/bzs-next-http-independent/execution.json'),
    root.parent / 'bzs861-next-search-lock/validation/claude_continuation_20261024/independent/retry-after/review-result.json',
    root.parent / 'bzs861-next-dispatcher/validation/claude_continuation_20261024/independent/search-lock/review-result.json',
]
for path in paths:
    bindings[str(path)] = {'sha256': sha(path.read_bytes()), 'bytes': path.stat().st_size}

result = {
    'scope': 'Independent document-link and historical-evidence check; not a fresh remote CI query or production execution',
    'local_markdown_link_count': len(links), 'unexpected_missing': [],
    'pending_integration_entries': sorted({row['repository_path'] for row in links if row['resolved_from'] == 'declared_pending_integration_report'}),
    'links': links,
    'historical_ci': {'run': run['id'], 'commit': run['head_sha'], 'tree': run['head_commit']['tree_id'],
                      'conclusion': 'success', 'jobs': jobs['jobs'],
                      'run_artifact': str(ci_dir / 'run.json'), 'run_sha256': sha(run_bytes),
                      'jobs_artifact': str(ci_dir / 'jobs.json'), 'jobs_sha256': sha(jobs_bytes),
                      'applies_to': 'historical baseline only'},
    'plan': {'packages': 83, 'acceptance_mapping': 161, 'slices': 10, 'product_acceptance_claimed': False},
    'reviewed_implementation_and_evidence_bindings': bindings,
}
(out / 'links-and-evidence.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'local_markdown_links': len(links), 'unexpected_missing': [],
                  'pending_integration_entries': result['pending_integration_entries'],
                  'baseline_ci_matches': True, 'plan': result['plan']}, indent=2, ensure_ascii=False))
