"""Final independent read-only audit of closed local evidence; executes no tests."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,subprocess
E=Path(__file__).resolve().parent
REPO=E.parent/'bzs861'
BATCH=REPO/'validation/shared_read_late_20261024'
BASE='a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89'
checked={}
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def bind(path):
    path=Path(path);value=digest(path);checked[str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)]=value;return value
def read(path):bind(path);return json.loads(Path(path).read_text())
def git(*args):return subprocess.check_output(['git',*args],cwd=REPO)
def save(name,value):
    with (E/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def parse_counts(text):
    matches=[]
    for line in text.splitlines():
        if 'assertions' not in line:continue
        if line.startswith('{"tests":'):
            value=json.loads(line);assert value['tests']==value['passed'];matches.append((value['tests'],value['assertions'],line))
        elif line.startswith('Contract tests:'):
            match=re.search(r'Contract tests: (\d+) passed, 0 failed;.* (\d+) assertions',line)
            matches.append((int(match[1]),int(match[2]),line))
        else:
            match=re.match(r'^(\d+)(?:/\d+)? (?:.*? )?(?:tests|cases)[,/ ]+(\d+) assertions',line)
            if match:matches.append((int(match[1]),int(match[2]),line))
    assert len(matches)==1,matches
    return matches[0]

report=read(BATCH/'final-report.json');post=read(BATCH/'final/post-run-inputs.json')
docs=read(BATCH/'final/documentation-after-tests.json')
previous=read(E/'integration-ctest-review-v1/ctest-and-auxiliary-review.json')
independent=read(E/'independent-review.json')
assert report['status']=='passed_local_validation' and report['baseline_commit']==BASE
assert git('rev-parse',BASE+'^{tree}').decode().strip()==report['baseline_tree']
assert git('branch','--show-current').decode().strip()==report['working_branch']
assert report['database_schema']['before']==14 and report['database_schema']['after']==15
assert report['package_version']=='0.1.10' and not report['baseline_published']
assert report['summary_script_sha256']==bind(BATCH/'summarize.py')
assert report['input_binding']['post_run_sha256']==bind(BATCH/'final/post-run-inputs.json')

runtime_records=[];common_inputs=None;common_binaries=None
paths=[BATCH/'final'/(name+'-command.json') for name in ('ctest','plan','contracts','plan-guard','pip-check')]
sanitizer_paths=sorted((BATCH/'asan').glob('*-command.json'));assert len(sanitizer_paths)==15
paths+=sanitizer_paths
for path in paths:
    value=read(path);manifest=path.parent/value['inputs'];log=path.parent/value['log']
    assert value['exit_code']==0 and value['execution_record_valid'] and not value['watchdog_expired']
    assert value['source_inputs_unchanged'] and value['changed_inputs']==[] and not value['build_preparation']
    assert value['binaries_before']==value['binaries_after']
    assert bind(manifest)==value['inputs_sha256'] and bind(log)==value['log_sha256'] and log.stat().st_size>0
    inputs=read(manifest);assert inputs['base_commit']==BASE
    if common_inputs is None:common_inputs=inputs['files'];common_binaries=value['binaries_after']
    else:assert inputs['files']==common_inputs and value['binaries_after']==common_binaries
    if path.parent.name=='asan':
        assert value['environment_overrides']['ASAN_OPTIONS']=='detect_leaks=1:halt_on_error=1'
        assert value['environment_overrides']['UBSAN_OPTIONS']=='halt_on_error=1:print_stacktrace=1'
        assert not re.search(r'ERROR: AddressSanitizer|LeakSanitizer: detected|runtime error:',log.read_text())
    runtime_records.append({'path':str(path.relative_to(BATCH)),'command_sha256':bind(path),
      'log_sha256':value['log_sha256'],'inputs_sha256':value['inputs_sha256'],'exit_code':0,'bytes':log.stat().st_size})
assert len(common_inputs)==224 and len(common_binaries)==36
assert post['binaries']==common_binaries
assert all(digest(REPO/path)==value for path,value in post['files'].items())
assert all(digest(REPO/path)==value for path,value in common_binaries.items())
assert set(post['files'])==set(common_inputs)
delta={path for path in common_inputs if common_inputs[path]!=post['files'][path]}
assert delta=={'docs/handoff/NEXT_AGENT.md','docs/implementation_status.json'}
assert {item['path'] for item in docs['changes']}==delta and docs['runtime_inputs_equal']
for item in docs['changes']:
    assert item['tested_sha256']==common_inputs[item['path']]
    assert item['final_sha256']==post['files'][item['path']]
for path,wanted in independent['production'].items():assert post['files'][path]==common_inputs[path]==wanted
assert common_binaries['build/libbzs_core.a']==independent['debug_library_sha256']
assert common_binaries['build-asan/libbzs_core.a']==independent['sanitizer_library_sha256']

detail=BATCH/report['final_ctest']['full_output'];bind(detail)
assert digest(detail)==report['final_ctest']['full_output_sha256']==previous['ctest']['original_full_output_sha256']
assert bind(BATCH/report['final_ctest']['junit'])==report['final_ctest']['junit_sha256']==previous['ctest']['junit_sha256']
assert [(row['cases'],row['assertions'],row['summary']) for row in report['final_ctest']['native_summaries']]==[
    (row['passed'],row['assertions'],row['summary_line']) for row in previous['ctest']['native_details']]
for key,wanted in {'suites':27,'passed':27,'failed':0,'skipped':0,'disabled':0,'native_targets':15,
                   'native_cases':521,'native_assertions':16761,'python_passed':1281}.items():assert report['final_ctest'][key]==wanted
assert report['final_ctest']['duration_seconds']==read(BATCH/'final/ctest-command.json')['duration_seconds']==187.422
target_suites={'bzs_tests':'core','bzs_batch_tests':'batch','bzs_map_tests':'map','bzs_crawl_tests':'crawl',
 'bzs_site_publication_tests':'site_publication','bzs_retention_tests':'retention','bzs_fairness_tests':'fairness',
 'bzs_session_tests':'sessions','bzs_source_publication_tests':'source_publication','bzs_search_mode_tests':'search_modes',
 'bzs_revalidation_tests':'revalidation','bzs_revocation_tests':'revocation','bzs_capacity_cycles_tests':'capacity_cycles',
 'bzs_cache_freshness_tests':'cache_freshness','bzs_contract_tests':'contracts'}
actual_targets={path.name.removesuffix('-command.json') for path in sanitizer_paths}
assert actual_targets==set(target_suites)
assert {row['target'] for row in report['final_sanitizers']['per_target']}==actual_targets
debug_counts={row['suite']:(row['passed'],row['assertions']) for row in previous['ctest']['native_details']}
sanitizer_counts=[]
for row in report['final_sanitizers']['per_target']:
    command=read(BATCH/row['record']);log=(BATCH/row['record']).parent/command['log']
    cases,assertions,summary=parse_counts(log.read_text())
    assert (cases,assertions)==debug_counts[target_suites[row['target']]]
    assert (cases,assertions,summary)==(row['cases'],row['assertions'],row['summary'])
    assert row['log_sha256']==command['log_sha256'] and row['inputs_sha256']==command['inputs_sha256']
    assert Path(command['argv'][0])==REPO/'build-asan'/row['target']
    sanitizer_counts.append({'target':row['target'],'cases':cases,'assertions':assertions,'log_bytes':log.stat().st_size})
assert sum(row['cases'] for row in sanitizer_counts)==521 and sum(row['assertions'] for row in sanitizer_counts)==16761
assert report['final_sanitizers']['passed']==report['final_sanitizers']['targets']==15
assert report['final_sanitizers']['failed']==0 and report['final_sanitizers']['leak_detection_enabled']
assert report['additional_gates']['original_contract_fixtures']['passed']==34
assert report['additional_gates']['planning_read_only']==read(BATCH/'final/plan.log')
assert report['additional_gates']['plan_guard']==read(BATCH/'final/plan-guard.log')

copies=[]
for name in ('independent-copy-manifest.json','independent-ctest-copy-manifest.json','regression-copy-manifest.json','documentation/copy-manifest.json'):
    manifest=read(BATCH/name)
    for item in manifest['files']:
        assert digest(item['source'])==digest(REPO/item['stored'])==item['sha256']
        if 'bytes' in item:assert (REPO/item['stored']).stat().st_size==item['bytes']
    copies.append({'manifest':name,'count':len(manifest['files']),'all_source_and_archived_bytes_equal':True})
assert copies[0]['count']==253 and copies[1]['count']==3
for path in BATCH.rglob('*'):
    if path.is_file():
        first=path.open('rb').read(16)
        assert not first.startswith((b'\x7fELF',b'!<arch>\n',b'SQLite format 3')),str(path)
old=read(BATCH/'old-validation-before.json');assert len(old)==3193
tree={}
for entry in git('ls-tree','-r','-z',BASE,'--','validation').split(b'\0'):
    if not entry:continue
    meta,name=entry.split(b'\t',1);tree[name.decode()]=meta.split()[2].decode()
assert set(tree)==set(old)
for path,wanted in old.items():
    data=(REPO/path).read_bytes();assert hashlib.sha256(data).hexdigest()==wanted
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==tree[path]
preservation=read(BATCH/'final/preservation.json');assert preservation['status']=='passed'
assert preservation['pre_run_manifest_sha256']==bind(BATCH/'old-validation-before.json')
assert not git('diff','--name-only',BASE,'--',*preservation['protected_inputs_unchanged']).strip()
current=(REPO/'docs/implementation_status.json').read_text();prior=git('show',BASE+':docs/implementation_status.json').decode()
feature=lambda value:value.split('  "features": ',1)[1].split('  "current_results": ',1)[0].encode()
assert feature(current)==feature(prior)
assert hashlib.sha256(feature(current)).hexdigest()==preservation['feature_json_sha256']
state=json.loads(current);assert state['status_counts']=={'partial':37,'not_started':46,'accepted':0}
assert len(state['features'])==83 and state['planned_acceptance_items']==161 and not state['planned_acceptance_suite_executed']
assert git('rev-parse','origin/review/claude-2026-10-24').decode().strip()==report['read_only_review_commit']
historical=REPO/report['input_binding']['previous_baseline_library_record']
assert bind(historical)==report['input_binding']['previous_baseline_library_record_sha256']
assert read(historical)['binaries_after']['build/libbzs_core.a']==report['input_binding']['baseline_library_sha256']==independent['baseline_library_sha256']
remote=read(BATCH/report['remote_publication']['observation'])
assert remote['read_only'] and remote['commit']==report['remote_publication']['main'] and remote['tree']==report['remote_publication']['tree']
assert report['remote_publication']['status']=='not_published' and report['remote_publication']['new_remote_ci']=='not_run'

readme=BATCH/'README.md';bind(readme);text=readme.read_text()
assert '完成收据 INSERT' not in text,'README must describe the receipt-state UPDATE accurately'
assert 'JUnit' in text
missing=[]
for link in re.findall(r'\]\(([^)]+)\)',text):
    if '://' in link or link.startswith('#'):continue
    path=(readme.parent/link.split('#')[0]).resolve()
    if not path.exists():missing.append(str(path.relative_to(BATCH)))
assert missing in ([],['independent/final-local-review.json']),missing
assert all(digest(REPO/name if not name.startswith('/') else name)==wanted for name,wanted in checked.items())
result={'status':'passed_final_local_review','reviewed_at_utc':datetime.now(timezone.utc).isoformat(),
 'reviewer':'independent handoff_audit; no production/formal-test/evidence modifications and no tests rerun during final audit',
 'baseline':BASE,'final_report_sha256':checked[str((BATCH/'final-report.json').relative_to(REPO))],
 'final_readme_sha256':checked[str(readme.relative_to(REPO))],
 'ctest':{'passed':27,'total':27,'skipped':0,'disabled':0,'native_targets':15,'native_cases':521,'native_assertions':16761,
   'python_passed':1281,'counts_from_complete_raw_output':True,'junit_used_for_target_status_only':True},
 'sanitizers':{'passed':15,'total':15,'native_cases':521,'native_assertions':16761,'targets':sanitizer_counts,
   'all_original_complete_logs_match':True,'all_exit_zero':True,'leak_detection_enabled':True,
   'same_scenarios_as_debug_not_added_to_unique_case_count':True},
 'runtime_bindings':{'closed_commands':20,'nonvalidation_inputs':224,'binary_and_ctest_configuration_files':36,
   'all_runtime_snapshots_equal':True,'all_execution_period_inputs_and_binaries_stable':True,
   'current_binary_bytes_equal':True,'current_runtime_input_bytes_equal':True,'post_test_documentation_changes':sorted(delta),
   'post_test_documentation_change_record_valid':True,'records':runtime_records},
 'archive_copies':copies,'no_sqlite_static_library_or_elf_archived':True,
 'retention':{'original_validation_files':3193,'baseline_git_blobs_pre_run_and_current_bytes_all_equal':True,
   'original_feature_block_bytes_equal':True,'protected_paths_unchanged':preservation['protected_inputs_unchanged'],
   'status_counts':state['status_counts'],'real_baseline_library_matches_prior_uri_ctest_record':True,
   'old_validation_dependencies_are_separately_bound_not_claimed_inside_runner':True},
 'report_accuracy':{'case_counts_and_assertions_match_raw_logs':True,'known_first_failures_remain_archived':True,
   'overlapping_focus_debug_and_sanitizer_results_not_added':True,'scope_and_known_limits_correctly_stated':True,
   'receipt_failure_is_state_update_documentation_corrected':True,'new_ci_or_remote_publication_not_claimed':True},
 'planned_closure_link':missing,'reviewed_artifact_sha256':checked,
 'verdict':'No remaining blocker found for the completed local bounded late-subscriber increment. This does not approve remote publication or full B-24/product acceptance.'}
save('final-local-review.json',result)
save('final-review-addendum.json',{'files':[
 {'path':'audit_final_local.py','sha256':digest(Path(__file__)),'bytes':Path(__file__).stat().st_size},
 {'path':'final-local-review.json','sha256':digest(E/'final-local-review.json'),'bytes':(E/'final-local-review.json').stat().st_size}],
 'include_this_addendum_itself':True,'scope':'Three additional independent text files after the initial manifest and CTest addendum; archive unchanged, without rewriting prior records.'})
print(json.dumps({'status':result['status'],'ctest':27,'sanitizers':15,'native_cases':521,'native_assertions':16761,
 'python_passed':1281,'runtime_input_files':224,'binary_files':36,'old_validation_files':3193},ensure_ascii=False))
