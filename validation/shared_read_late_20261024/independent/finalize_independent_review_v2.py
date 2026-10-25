"""Read-only source/evidence checks and archive inventory for the independent B-24 review."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,subprocess
E=Path(__file__).resolve().parent
REPO=E.parent/'bzs861'
BASELINE='a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89'
PRODUCTION={'include/bzs/store.hpp':'ecc93f4edb412c48f04686126fcdabfbc4b4b17752a56c0bddc72f589bd03772',
 'src/store.cpp':'2c85dc7708183b950331866dbd40e11f9de70ac34799ae65d32fbec0fbec3b89',
 'src/batch.cpp':'ab8e4fd0b9b16645dc37bd7df319177cab81d026be7bcca104b25d0bbdcd9fd1'}
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    if (E/name).exists():
        assert json.loads((E/name).read_text())==value,name;return
    with (E/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def git(*args):return subprocess.check_output(['git',*args],cwd=REPO)
def blob(path):return git('show',BASELINE+':'+path).decode()
def base_tree(prefix):
    values={}
    for record in git('ls-tree','-r','-z',BASELINE,'--',prefix).split(b'\0'):
        if not record:continue
        header,path=record.split(b'\t',1);mode,kind,oid=header.decode().split();assert kind=='blob'
        values[path.decode()]=oid
    return values
def verify_base_file(path,oid):
    data=(REPO/path).read_bytes();actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    assert actual==oid,path
    return hashlib.sha256(data).hexdigest()

assert git('rev-parse','HEAD').decode().strip()==BASELINE
assert set(git('diff','--name-only','--','src','include').decode().splitlines())==set(PRODUCTION)
assert all(digest(REPO/path)==value for path,value in PRODUCTION.items())
save('reviewed-production-delta.json',{'baseline':BASELINE,'candidate_sources':PRODUCTION,
 'patch':git('diff','--','include/bzs/store.hpp','src/store.cpp','src/batch.cpp').decode(),
 'public_api_and_worker_protocol_changed':False,
 'settlement_publication_retention_functions_changed':False,
 'review_conclusion':'No blocking defect found in the bounded late-admission implementation. Independent fault, migration and cancellation probes passed.'})

tests={}
paths=git('diff','--name-only','--','tests').decode().splitlines()
for path in paths:
    old=blob(path);new=(REPO/path).read_text();expected=old
    if path=='tests/unit.cpp':
        expected=expected.replace('"PRAGMA user_version=15;"','"PRAGMA user_version=16;"')
        expected=expected.replace('"PRAGMA user_version")==15','"PRAGMA user_version")==16')
    expected=expected.replace('"PRAGMA user_version")==14','"PRAGMA user_version")==15')
    removed_addition=''
    if path=='tests/batch.cpp':
        begin=new.index('  void check_late_shared_receipt(')
        end=new.index('  TEST(schema_three_additive_migration_',begin)
        removed_addition=new[begin:end];stripped=new[:begin]+new[end:]
        assert stripped==expected,path
        assert len(re.findall(r'\bTEST\(',removed_addition))==14
    else:assert new==expected,path
    tests[path]={'baseline_sha256':hashlib.sha256(old.encode()).hexdigest(),'candidate_sha256':digest(REPO/path),
      'original_content_preserved_except_schema_assertions':True,
      'added_case_groups':len(re.findall(r'\bTEST\(',removed_addition)),
      'baseline_case_groups':len(re.findall(r'^\s+TEST\(',old,re.MULTILINE))}
assert tests['tests/batch.cpp']['baseline_case_groups']==30
assert len(paths)==8

old_manifest=REPO/'validation/shared_read_late_20261024/old-validation-before.json'
old_files=json.loads(old_manifest.read_text());tree=base_tree('validation')
assert set(tree)==set(old_files) and len(tree)==3193
for path,oid in tree.items():assert verify_base_file(path,oid)==old_files[path],path
historical_command=REPO/'validation/uri_deletion_20261024/final/ctest-command.json'
historical_library=json.loads(historical_command.read_text())['binaries_after']['build/libbzs_core.a']
assert historical_library=='30ea0955517d8a2a585f2b01c4ba42fd1530ca4a9431284f74fa04caf2d80338'
assert digest(E/'migration-review-v2/baseline/libbzs_core.a')==historical_library
plans=base_tree('docs/planning')
for path,oid in plans.items():verify_base_file(path,oid)
configuration={}
for path in ('config/sources.json','config/sites.json'):
    verify_base_file(path,base_tree(path)[path]);configuration[path]=digest(REPO/path)
sources=json.loads((REPO/'config/sources.json').read_text())['sources']
sites=json.loads((REPO/'config/sites.json').read_text())['sites']
assert len(sources)==17 and all(source.get('enabled') is False for source in sources) and len(sites)==0
status=json.loads((REPO/'docs/implementation_status.json').read_text())
assert status['status_counts']=={'partial':37,'not_started':46,'accepted':0}
assert len(status['features'])==83 and status['planned_acceptance_items']==161
assert not status['planned_acceptance_suite_executed']
save('source-and-history-preservation.json',{
 'reviewed_at':datetime.now(timezone.utc).isoformat(),'baseline':BASELINE,'production':PRODUCTION,
 'formal_tests':tests,'old_validation_files':3193,'old_validation_matches_baseline_blobs_and_before_snapshot':True,
 'old_validation_snapshot_sha256':digest(old_manifest),'planning_files_unchanged':len(plans),
 'real_baseline_library_matches_historical_uri_ctest_binary':True,
 'historical_ctest_command_sha256':digest(historical_command),'historical_library_sha256':historical_library,
 'configuration_sha256':configuration,'sources':17,'enabled_sources':0,'sites':0,
 'work_packages':83,'planned_acceptance_items':161,'status_counts':status['status_counts'],
 'documentation_sha256':{path:digest(REPO/path) for path in ['HANDOFF_START_HERE.md','README.md','docs/API.md',
   'docs/DEVELOPMENT_STATUS.md','docs/GITHUB_HANDOFF.md','docs/SHARED_READS.md','docs/URI_DELETION_IDENTITY.md',
   'docs/handoff/NEXT_AGENT.md','docs/implementation_status.json']},
 'documentation_scope':'Current implementation contract reviewed before final integration report; final result counts remain subject to the parent integration audit.'})

command_checks=[]
for path in sorted(E.rglob('*.command.json')):
    value=json.loads(path.read_text());prefix=path.name.removesuffix('.command.json')
    for stream in ('stdout','stderr'):
        actual=path.with_name(prefix+'.'+stream);assert digest(actual)==value[stream+'_sha256'],str(actual)
    if 'inputs_stable' in value:assert value['inputs_stable']
    command_checks.append({'path':str(path.relative_to(E)),'returncode':value['returncode'],'logs_match':True})
source_checks=[]
for name in ('migration-review-v1','migration-review-v2','behavior-review-v1','behavior-review-v2','admission-rollback-review-v1'):
    value=json.loads((E/name/'inputs.json').read_text());sources=value.get('probe_sources',value.get('sources',{}))
    for source,wanted in sources.items():assert digest(E/source)==wanted,source
    source_checks.append({'run':name,'sources_unchanged':True})
sanitizer_before=json.loads((E/'sanitizer-review-v1/run-inputs-before.json').read_text())
sanitizer_after=json.loads((E/'sanitizer-review-v1/run-inputs-after.json').read_text())
assert sanitizer_before==sanitizer_after
for label,wanted in sanitizer_after.items():
    if label.startswith('frozen/'):actual=E/'sanitizer-review-v1'/label
    elif label=='runner':actual=E/'run_sanitizer_review.py'
    elif label.startswith('original-probe/'):actual=E/label.removeprefix('original-probe/')
    elif label.startswith('production/'):actual=REPO/label.removeprefix('production/')
    else:raise AssertionError(label)
    assert digest(actual)==wanted,label
save('evidence-integrity-review.json',{'command_records':command_checks,'all_command_log_digests_match':True,
 'original_probe_and_runner_sources':source_checks,'sanitizer_frozen_inputs_match_before_after_and_current':True,
 'current_production_matches_reviewed_candidate':True})

summary={
 'reviewed_at':datetime.now(timezone.utc).isoformat(),'status':'passed_in_independent_scope',
 'baseline':BASELINE,'baseline_library_sha256':'30ea0955517d8a2a585f2b01c4ba42fd1530ca4a9431284f74fa04caf2d80338',
 'debug_library_sha256':'35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0',
 'sanitizer_library_sha256':'68b726db8e5e75e1a534a577c56f6b8a9565b88fcad02a5667ea58c1dd9db774',
 'production':PRODUCTION,'production_or_formal_tests_modified_by_reviewer':False,
 'real_old_library_migration':{'record':'migration-review-v2/review-result.json','schema_before':14,'schema_after':15,
   'old_tables_preserved':47,'old_virtual_interfaces_covered_by_shadow_data':['content_fts','substring_fts'],
   'legacy_inflight_defaults_closed':True,'old_single_shared_cancelled_payer_settlement_preserved':True,
   'new_attempt_opens':True,'old_binary_rejected':True,'quarantined_backup_rejected':True,'migration_fault_rolls_back':True},
 'behavior':{'record':'behavior-review-v2/review-result.json','groups':6,'incompatibility_variants_in_one_group':8,
   'real_baseline_checks':131,'real_baseline_expected_failures':4,'candidate_checks':163,'candidate_failures':0},
 'admission_transaction_fault':{'record':'admission-rollback-review-v1/review-result.json','branches':2,
   'checks':151,'failures':0,'scope':'Singleton promotion and existing-group extension, six-table exact rollback, same-key retry and one original payer charge.'},
 'sanitizer':{'record':'sanitizer-review-v1/review-result.json','status':'passed','replay_of_same_probes':True,
   'adds_unique_cases':False,'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'},
 'preserved_first_failures':[
   {'record':'finalize-review-v1-failure.json','classification':'review summary script counting error',
    'description':'The first inventory script counted the TEST macro definition as a test. The v2 script counts actual TEST invocations. This did not change any source, formal test or executed probe result.'},
   {'record':'migration-review-v1/review-result.json','classification':'diagnostic connection limitation',
    'description':'External Python SQLite integrity_check returned SQL logic error on both untouched schema14 and upgraded15. Core-registered connections pass. Original error and separate observation retained; not a demonstrated production regression.'},
   {'record':'behavior-review-v1/review-result.json','classification':'independent publication fixture error',
    'description':'The first publication fixture omitted the required version and did not advance publication_sequence. Its candidate result was one failure. v2 uses a valid policy and verifies sequence advancement before submission; production unchanged.'}],
 'source_test_history_review':'source-and-history-preservation.json','evidence_digest_review':'evidence-integrity-review.json',
 'limits':[
   'Only durable read_many late admission; direct web.read/map/crawl/search coordination remains outside this increment.',
   'A durable unexpired lease is not proof of process liveness. Lost execution keeps original unknown settlement; no automatic resend.',
   'Closure is monotonic for admission only and does not change existing completion/accounting under clock rollback.',
   'At most32 matching returned candidates are evaluated; SQL may scan additional same-URI open rows. Membership is32 cumulative.',
   'Global publication_sequence equality is intentionally conservative.',
   'Stop old Core before schema15 migration. Startup version rejection does not make already-open mixed binaries safe.',
   'Synthetic local probes do not establish real-network behavior, arbitrary power-loss safety, multi-node operation or full B-24 acceptance.'],
 'integration_suite_status':'Parent full CTest and fifteen sanitizer-target review pending; not claimed by this independent report.',
 'publication_status':'Local only. No remote writes, new CI or release claimed.'}
save('independent-review.json',summary)

allowed={'.cpp','.hpp','.py','.json','.md','.log','.stdout','.stderr'}
included=[];excluded=[]
for path in sorted(E.rglob('*')):
    if not path.is_file():continue
    relative=str(path.relative_to(E))
    if path.suffix in allowed:
        included.append({'path':relative,'bytes':path.stat().st_size,'sha256':digest(path)})
    else:excluded.append({'path':relative,'bytes':path.stat().st_size})
save('archive-manifest.json',{'generated_at':datetime.now(timezone.utc).isoformat(),
 'scope':'Archive all included files plus this manifest under the new validation directory; do not change prior evidence.',
 'included_count':len(included),'included_files':included,'excluded_count':len(excluded),'excluded_files':excluded,
 'exclusions':'Compiled binaries, static libraries and synthetic SQLite databases stay in scratch. Their probes, frozen headers, raw logs, content digests, command and input bindings are archived.',
 'manifest_self_hash':'Intentionally excluded to avoid a self-referential digest; parent copy manifest should bind this file.'})
print(json.dumps({'independent_status':summary['status'],'old_validation_files':3193,'command_records':len(command_checks),
 'archivable_files_plus_manifest':len(included)+1,'excluded_binary_and_database_files':len(excluded)},ensure_ascii=False))
