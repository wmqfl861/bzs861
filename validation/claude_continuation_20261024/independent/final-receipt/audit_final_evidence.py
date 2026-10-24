#!/usr/bin/env python3
"""Read-only audit of completed evidence. Writes only the adjacent audit JSON."""
import base64
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path('/workspace/scratch/e2371129386d/bzs861')
B = ROOT / 'validation/claude_continuation_20261024'
OUT = Path(__file__).parent
def sha(data): return hashlib.sha256(data).hexdigest()
def shafile(path): return sha(Path(path).read_bytes())
def read(path): return json.loads(Path(path).read_text())
def git(*args): return subprocess.check_output(['git', *args], cwd=ROOT)

receipt = read(B/'final-report.json')
assert shafile(B/'final-report.json') == '1debe9dfb43c6e79e140e7289ae8f1034b4ee232ad85bc5d580f2d033a0ccb84'
assert shafile(B/'README.md') == '85415103b6cc5a3c1618db78ae869797d36be3beb3b71995b77adfcbd409efd0'
summary = {}

# Both complete archives preserve the previous Debug/ASan evidence byte-for-byte.
P = B/'independent/search-lock-final'
archive = read(P/'archive-manifest-final.json')
prior = read(P/'archive-manifest-debug-asan.json')
for manifest in (archive, prior):
    assert manifest['file_count'] == len(manifest['files'])
    assert sum(x['bytes'] for x in manifest['files']) == manifest['total_bytes']
    for x in manifest['files']:
        data = (P/x['relative_path']).read_bytes()
        assert len(data) == x['bytes'] and sha(data) == x['sha256'], x['relative_path']
        assert data == Path(x['source']).read_bytes(), x['source']
prior_files = {x['relative_path']: x['sha256'] for x in prior['files']}
all_files = {x['relative_path']: x['sha256'] for x in archive['files']}
assert all(all_files.get(k) == v for k,v in prior_files.items())
assert all_files['archive-manifest-debug-asan.json'] == shafile(P/'archive-manifest-debug-asan.json')
assert len(prior_files)+1 == 55 and len(all_files)+1 == 104
summary['final_search_archive'] = {
    'manifest_sha256': shafile(P/'archive-manifest-final.json'),
    'old_files_preserved_including_prior_manifest':55,
    'new_files_including_final_manifest':49,
    'total_files_including_final_manifest':104,
    'all_sources_and_archived_bytes_equal':True,
}

R = P/'final-release'
perf = read(R/'evidence/performance-result.json')
assert shafile(R/'evidence/performance-result.json') == receipt['performance']['sha256']
for key in ('dataset','execution_order','executions_per_variant','observations','limits'):
    assert receipt['performance'][key] == perf[key]
assert perf['executions_per_variant'] == 1 and perf['automatic_retries'] == 0
for name,digest in perf['artifacts'].items(): assert shafile(R/name) == digest, name
copied = read(R/'copy-manifest.json')
old_headers = new_headers = 0
for x in copied['copied_files']:
    assert shafile(x['source']) == x['sha256'] == shafile(x['copy']), x
    path = Path(x['copy'])
    if '/include/bzs/' in x['copy']:
        rel = 'include/bzs/'+path.name
        if '/old/' in x['copy']:
            assert sha(git('show','59081e14fdda765844031e13f874e588334f1a36:'+rel)) == x['sha256']
            old_headers += 1
        else:
            assert shafile(ROOT/rel) == x['sha256']
            new_headers += 1
assert old_headers == new_headers == 16
driver = R/'scan_blocking_negative.cpp'
assert driver.read_bytes() == git('show','59081e14fdda765844031e13f874e588334f1a36:validation/claude_followup_20261024/p2-5/benchmark-inputs/scan_blocking_negative.cpp')
assert shafile(driver) == perf['driver_source_sha256']
commands = {}
for kind in ('compile','run'):
    for variant in ('old','new'):
        meta = read(R/f'evidence/{kind}-performance-{variant}.json')
        commands[(kind,variant)] = meta
        assert meta['exit_code'] == 0 and not meta['process_watchdog_expired']
        assert meta['sources_before'] == meta['sources_after'] and meta['sources_unchanged']
        assert meta['inputs_before'] == meta['inputs_after'] and meta['inputs_unchanged']
        assert len(meta['sources_before']) == 43
        for path,digest in meta['sources_after'].items(): assert shafile(path) == digest
        for path,digest in meta['inputs_after'].items(): assert shafile(path) == digest
        for path,digest in meta['artifacts_after'].items(): assert shafile(path) == digest
        assert meta['log_sha256'] == shafile(R/f'evidence/{kind}-performance-{variant}.log')
        assert meta['runner_sha256'] == shafile(P/'capture.py')
        if kind == 'run':
            assert meta['artifacts_before'] == meta['artifacts_after']
            assert meta['argv'][1:] == ['2000','204800']
            expected = perf['runs'][variant]
            assert meta['utc_started'] == expected['utc_started']
            assert meta['elapsed_seconds'] == expected['elapsed_seconds_including_data_construction']
            assert next(iter(meta['artifacts_after'].values())) == expected['driver_binary_sha256']
            assert shafile(Path(meta['argv'][0]).parent/'libbzs_core.a') == expected['library_sha256']
            lines = (R/f'evidence/run-performance-{variant}.log').read_text().splitlines()
            assert len(lines) == 3 and lines[0] == '2000 docs x 200 KiB'
            for line, observation in zip(lines[1:], perf['observations']):
                query, search, wait, retrieval = re.fullmatch(r'query (.*?)\s+search=(\d+) ms, worst concurrent stats\(\)=(\d+) ms, retrieval=(\S+)',line).groups()
                assert query == observation['query']
                assert int(search) == observation[f'{variant}_search_ms']
                assert int(wait) == observation[f'{variant}_worst_concurrent_stats_ms']
                assert retrieval == observation['retrieval']
normalize = lambda argv: [s.replace('/old/','/variant/').replace('/new/','/variant/') for s in argv]
assert normalize(commands[('compile','old')]['argv']) == normalize(commands[('compile','new')]['argv'])
old_start = datetime.datetime.fromisoformat(commands[('run','old')]['utc_started'])
new_start = datetime.datetime.fromisoformat(commands[('run','new')]['utc_started'])
gap = (new_start-old_start).total_seconds()-commands[('run','old')]['elapsed_seconds']
assert 0 <= gap < 1
assert shafile(ROOT/'build-release/libbzs_core.a') == perf['runs']['new']['library_sha256']
summary['performance'] = {
    'report_sha256':shafile(R/'evidence/performance-result.json'),
    'old_and_new_header_counts':[old_headers,new_headers],
    'original_driver_equal_to_published_590':True,
    'normalized_compile_argv_equal':True,
    'raw_log_observations_equal_to_report_and_receipt':True,
    'actual_source_library_and_binary_hashes_match':True,
    'gap_between_old_completion_and_new_start_seconds':round(gap,6),
    'observations':perf['observations'],
    'descriptive_only_no_product_test_rerun':True,
}

# Exactly decode byte-preserving archives instead of assuming UTF-8 logs.
encoded = []
for name in ('recorder','recorder-dependency'):
    folder = B/'independent'/name
    manifest = read(folder/'copy-manifest.json')
    report = read(folder/'review.json')
    original_map = {}
    for row in manifest['files']:
        stored = (folder/row['stored']).read_bytes()
        assert sha(stored) == row['stored_sha256']
        original = base64.b64decode(stored) if 'base64' in row['encoding'] else stored
        assert sha(original) == row['original_sha256']
        original_path = Path(manifest['source'])/row['original']
        assert original_path.read_bytes() == original
        original_map[row['original']] = row['original_sha256']
        if 'base64' in row['encoding']:
            encoded.append({'archive':name,'stored':row['stored'],'original_sha256':sha(original),'bytes':len(original)})
    assert all(original_map.get(path)==digest for path,digest in report['evidence_sha256'].items())
    summary[name] = {'archived_files':len(manifest['files']),'all_original_bytes_preserved':True,'verdict':report['verdict']}
assert len(encoded)==2
summary['base64_encoded_logs'] = encoded

# The delegated reader was not an HTTP author; unchanged HTTP excerpt bridges
# the earlier whole-receipt SHA to the final performance-augmented receipt.
H = B/'independent/final-http-sanitizer'
hreport = read(H/'review.json')
assert shafile(H/'review.json') == 'bb17725ac2b21f754cecbf95940d208af4aca818cb6afdb5509cb54b513916f4'
hmanifest = read(H/'evidence-manifest.json')
assert shafile(H/'evidence-manifest.json') == hreport['evidence']['manifest_sha256']
for row in hmanifest['files']:
    data = (H/row['snapshot']).read_bytes()
    assert sha(data)==row['sha256'] and len(data)==row['bytes']
    assert data == Path(row['source']).read_bytes()
original_dir = Path('/tmp/bzs-continuation-receipt-http-independent')
http_files = [p for p in original_dir.rglob('*') if p.is_file()]
assert len(http_files)==21
for path in http_files: assert path.read_bytes()==(H/path.relative_to(original_dir)).read_bytes()
excerpt = read(H/'final-report-http-excerpt.json')
for key,value in excerpt.items():
    if key=='preserved_failure_statements':
        assert all(receipt['evidence_integrity'][k]==v for k,v in value.items())
    else: assert receipt[key]==value
assert shafile(ROOT/'build-asan/searchd')==hreport['binary_sha256']
summary['delegated_http_receipt'] = {
    'review_sha256':shafile(H/'review.json'),
    'manifest_sha256':shafile(H/'evidence-manifest.json'),
    'all_21_archived_files_equal_original':True,
    'unchanged_http_excerpt_matches_final_receipt':True,
    'cross_checks':25,'formal_cases':3,'listening_servers_clean_exit_zero':2,
    'invalid_argument_startup_subprocesses_expected_exit_2':5,
    'scope_note':'Two running servers, not only two total searchd subprocesses; no product tests rerun by receipt reviewer.',
}

# All existing author/reviewer evidence blobs (including failed runs) remain
# exactly those committed by their authors; newly added reconstruction patches
# and final reports do not rewrite them.
evidence_commits = [
    ('1c69836f393aee55032beae4bd027f4d286d1e4b','author/search_lock'),
    ('a30231a802ab05f0bb3f821c51ef03d156d796d8','author/retry_after'),
    ('23a8a646a9ddc45af37b05e00d0ec45be5b96427','independent/retry-after'),
    ('612b254a7e6d7882c1f3d83167dc30e893080e92','independent/search-lock'),
]
preserved = []
for commit,subdir in evidence_commits:
    prefix = 'validation/claude_continuation_20261024/'+subdir
    entries = git('ls-tree','-r','-z',commit,'--',prefix).split(b'\0')
    count = 0
    for row in entries:
        if not row: continue
        meta,path = row.split(b'\t',1)
        name = path.decode()
        blob = meta.split()[2].decode()
        data = (ROOT/name).read_bytes()
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==blob,name
        count += 1
    assert count
    preserved.append({'source_commit':commit,'prefix':prefix,'original_files_unchanged':count})
summary['committed_author_and_independent_evidence_preserved'] = preserved

# Current receipt + eight independently approved handoff files have no missing
# repository-relative Markdown destinations. Network URLs are merely reported.
scope = read(OUT/'scope-and-mapping-audit.json')
docpaths = [B/'README.md']+[ROOT/p for p in scope['exact_document_delta']]
links=[]
for path in docpaths:
    for raw in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text()):
        target = raw.strip().strip('<>')
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',target):
            links.append({'source':str(path.relative_to(ROOT)),'target':target,'kind':'external_not_fetched'});continue
        destination = unquote(target.split('#',1)[0])
        if destination:
            assert (path.parent/destination).exists(),(path,target)
        links.append({'source':str(path.relative_to(ROOT)),'target':target,'kind':'local_destination_exists'})
summary['markdown_links']={'files':len(docpaths),'total':len(links),'local':sum(x['kind']=='local_destination_exists' for x in links),'links':links,'anchors_checked':False,'external_network_requests':False}
summary['review_roles']={
    'P2-5':{'author':'search_lock','independent_code_and_final_runtime':'dispatcher'},
    'P2-6':{'author':'dispatcher','independent_code_and_final_runtime':'retry_after'},
    'P2-7':{'author':'root','independent_code_and_final_runtime':'retry_after'},
    'P2-9':{'author':'retry_after','independent_code_and_clock_correction':'search_lock'},
    'documentation':{'author':'dispatcher','independent':'search_lock'},
    'recorder':{'author':'root','independent':'retry_after'},
    'final_receipt':{'author':'root','reviewer':'search_lock','HTTP_helper_review':'retry_after','note':'Receipt checks evidence and preservation. It does not substitute self-review for the dispatcher P2-5 review.'},
}
summary['root_modification_or_product_execution']=False
(OUT/'final-evidence-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ('markdown_links','review_roles')},ensure_ascii=False,indent=2))
print('Markdown links:',summary['markdown_links']['total'],'local:',summary['markdown_links']['local'])
