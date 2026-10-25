#!/usr/bin/env python3
"""Independently close the aggregate local review from already-finished records."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
REPO = HERE.parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def native_summary(text: str) -> tuple[int, int, str] | None:
    values = []
    for line in text.splitlines():
        if line.startswith('{"tests":'):
            item = json.loads(line)
            if item['tests'] == item['passed']: values.append((item['tests'], item['assertions'], line))
            continue
        value = re.match(r'^(\d+)/(\d+) tests, (\d+) assertions$', line)
        if value:
            if value[1] == value[2]: values.append((int(value[1]), int(value[3]), line))
            continue
        for pattern in (
            r'^(\d+) (?:[a-z ]+ )?tests(?:,| /) (\d+) assertions(?:,| /) 0 failures(?:, .*|$)',
            r'^(\d+) cases, (\d+) assertions, 0 failed$',
            r'^Contract tests: (\d+) passed, 0 failed; .*?, (\d+) assertions$',
        ):
            value = re.match(pattern, line)
            if value:
                values.append((int(value[1]), int(value[2]), line)); break
    return values[0] if len(values) == 1 else None


def main() -> int:
    issues: list[dict] = []
    def check(condition: bool, label: str, detail: object = None) -> None:
        if not condition: issues.append({'check': label, 'detail': detail})
    report_path, readme_path = OUT/'final-report.json', OUT/'README.md'
    report, post = load(report_path), load(OUT/'final/post-run-inputs.json')
    files, binaries = post['files'], post['binaries']
    readme = readme_path.read_text()
    observed_refs = {}
    def references(item: object) -> None:
        if isinstance(item, dict):
            if isinstance(item.get('path'),str) and isinstance(item.get('sha256'),str):
                path = OUT/item['path']
                check(path.is_file() and sha(path)==item['sha256'], 'report_reference_hash', item['path'])
                observed_refs[item['path']] = item['sha256']
            for value in item.values(): references(value)
        elif isinstance(item,list):
            for value in item: references(value)
    references(report)
    check(report['status']=='passed_local_validation','aggregate_report_status')
    check(len(files)==227 and len(binaries)==38,'final_input_and_binary_counts')
    changed_files=[name for name,value in files.items() if not (REPO/name).is_file() or sha(REPO/name)!=value]
    changed_binaries=[name for name,value in binaries.items() if not (REPO/name).is_file() or sha(REPO/name)!=value]
    check(not changed_files,'current_227_inputs_equal_post_run',changed_files)
    check(not changed_binaries,'current_38_binary_bytes_equal_post_run',changed_binaries)
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=REPO)
    current_names={name.decode() for name in names.split(b'\0') if name and not name.startswith(b'validation/')}
    current_names.add('validation/direct_shared_20261024/run.py')
    check(current_names==set(files),'current_input_file_set_complete',sorted(current_names.symmetric_difference(files)))

    closed_commands=[]
    def command_record(relative: str, build: bool=False) -> dict:
        path=OUT/relative; value=load(path)
        check(value['exit_code']==0 and value['execution_record_valid'] and not value['watchdog_expired'],'selected_command_success',relative)
        check(value['source_inputs_unchanged'] and not value['changed_inputs'],'selected_command_sources_stable',relative)
        check(value['build_preparation']==build,'selected_command_kind',relative)
        inp=path.parent/value['inputs']; log=path.parent/value['log']
        check(sha(inp)==value['inputs_sha256'] and load(inp)['files']==files,'selected_manifest_hash_and_227_files',relative)
        check(log.is_file() and sha(log)==value['log_sha256'],'selected_complete_log_hash',relative)
        if not build: check(value['binaries_before']==value['binaries_after']==binaries,'selected_command_binary_binding',relative)
        closed_commands.append({'record':relative,'sha256':sha(path),'log_sha256':sha(log),'log_bytes':log.stat().st_size,'inputs_sha256':sha(inp)})
        return value

    ctest=command_record('final/ctest-v2-command.json')
    ctest_review=load(HERE/'closed-ctest-review-v2.json')
    check(ctest_review['status']=='passed_closed_ctest_independent_review' and not ctest_review['failed_checks'],'prior_full_ctest_audit_passed')
    check(ctest_review['input_binding']['verified_input_files']==files and ctest_review['input_binding']['verified_binaries']==binaries,'ctest_review_exact_current_binding')
    for name,value in ctest_review['artifacts'].items(): check(sha(OUT/name)==value['sha256'],'ctest_original_review_artifact_unchanged',name)
    detail_path=OUT/report['final_ctest']['full_output']; detail=detail_path.read_text()
    check(sha(detail_path)==report['final_ctest']['full_output_sha256'],'report_complete_ctest_log_hash')
    check(sha(OUT/report['final_ctest']['junit'])==report['final_ctest']['junit_sha256'],'report_junit_hash')
    for key in ('suites','passed','failed','disabled','skipped','nested_skips','native_targets','native_cases','native_assertions'):
        check(report['final_ctest'][key]==ctest_review['ctest'][key],'report_ctest_count_'+key)
    check(report['final_ctest']['python_passed']==ctest_review['ctest']['python_worker']['passed']==1294,'pytest_count_matches')
    actual_native={item['suite']:(item['cases'],item['assertions']) for item in ctest_review['ctest']['native_summaries']}
    debug_by_target={}
    for item in ctest_review['ctest']['suite_bindings']:
        if item['number']<=16:
            executable=Path(re.findall(r'"([^\"]+)"',item['command'])[0]).name
            debug_by_target[executable]=actual_native[item['name']]

    debug_build=command_record(report['builds']['debug']['path'],True)
    asan_build=command_record(report['builds']['sanitizers']['path'],True)
    for name,value in binaries.items():
        source=asan_build if name.startswith('build-asan/') else debug_build
        check(source['binaries_after'].get(name)==value,'binary_matches_final_successful_build',name)

    closure=load(OUT/'final/sanitizer-closure.json')
    selected=report['final_sanitizers']['per_target']
    check(selected==closure['targets'],'report_and_closure_selected_targets_identical')
    presets=load(REPO/'CMakePresets.json')
    configured=set(next(item for item in presets['buildPresets'] if item['name']=='asan-core')['targets'])
    selected_names=[item['target'] for item in selected]
    check(len(selected_names)==len(set(selected_names))==16 and set(selected_names)==configured==set(debug_by_target),'all_16_native_targets_once')
    v4=[item for item in selected if '-v4-command.json' in item['record']]
    v5=[item for item in selected if '-v5-command.json' in item['record']]
    repair_targets={'bzs_revalidation_tests','bzs_cache_freshness_tests'}
    check(len(v4)==14 and len(v5)==2 and {x['target'] for x in v5}==repair_targets,'explicit_14_v4_plus_2_v5_selection')
    sanitizer_rows=[]
    options={'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
    for item in selected:
        command=command_record(item['record'])
        path=OUT/item['record']; log=path.parent/command['log']; text=log.read_text()
        check(all(command['environment_overrides'].get(k)==v for k,v in options.items()),'sanitizer_options_not_weakened',item['target'])
        check(Path(command['argv'][0]).name==item['target'],'sanitizer_executed_named_target',item['target'])
        counts=native_summary(text)
        check(counts is not None and counts[:2]==(item['cases'],item['assertions'])==debug_by_target[item['target']],'sanitizer_actual_log_counts_match_debug',item['target'])
        check(log.stat().st_size>0,'selected_sanitizer_log_nonempty',item['target'])
        check(not re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|runtime error:|LeakSanitizer has encountered a fatal error|AddressSanitizer:DEADLYSIGNAL',text),'selected_sanitizer_has_no_diagnostic',item['target'])
        check(command['log_sha256']==item['log_sha256'] and command['inputs_sha256']==item['inputs_sha256'],'selected_report_log_and_input_hashes',item['target'])
        sanitizer_rows.append({'target':item['target'],'record':item['record'],'record_sha256':sha(path),'cases':item['cases'],'assertions':item['assertions'],'log_sha256':sha(log),'log_bytes':log.stat().st_size})
    check(sum(x['cases'] for x in selected)==544 and sum(x['assertions'] for x in selected)==20397,'sanitizer_totals_same_544_20397')

    driver_path=OUT/'asan/execution-v4.json'; driver=load(driver_path)
    check(driver['all_passed'] is False and closure['initial_driver_all_passed'] is False,'original_v4_driver_failure_preserved')
    check(sha(OUT/'run_sanitizers.py')==driver['driver_sha256'],'original_driver_source_hash')
    driver_failures={x['target'] for x in driver['targets'] if x['recorder_exit_code']!=0}
    check(driver_failures==repair_targets and len(driver['targets'])==16,'original_v4_failures_exactly_two_launchers')
    repair_rows=[]
    for ref in closure['binary_permissions_repaired_without_byte_changes']:
        p=OUT/ref['path']; value=load(p)
        check(sha(p)==ref['sha256'],'permission_summary_hash',ref['path'])
        artifact=REPO/value['artifact']
        check(value['child_started'] is False and value['per_target_command_record_absent_due_to_launcher_exception'],'launch_record_does_not_claim_child_exit',ref['path'])
        check(value['observed_mode_before']=='0o644' and value['mode_after']=='0o755' and value['bytes_unchanged'],'permission_summary_scope',ref['path'])
        check(stat.S_IMODE(artifact.stat().st_mode)==0o755,'current_execute_permission_restored',value['artifact'])
        check(sha(artifact)==value['sha256']==asan_build['binaries_after'][value['artifact']]==binaries[value['artifact']],'permission_repair_identical_build_bytes',value['artifact'])
        failed_log=OUT/'asan'/value['failed_attempt_log']; failed_inputs=OUT/'asan'/value['failed_attempt_inputs']
        check(failed_log.exists() and failed_log.stat().st_size==0,'original_unstarted_log_remains_empty',value['artifact'])
        check(load(failed_inputs)['files']==files,'unstarted_attempt_source_binding',value['artifact'])
        absent=failed_log.with_name(failed_log.name.removesuffix('.log')+'-command.json')
        check(not absent.exists(),'no_fabricated_original_child_command',value['artifact'])
        repair_rows.append({'summary':ref['path'],'summary_sha256':sha(p),'artifact':value['artifact'],'current_mode':oct(stat.S_IMODE(artifact.stat().st_mode)),'sha256':sha(artifact),'original_empty_log_sha256':sha(failed_log),'historical_error_scope':'Reported launcher PermissionError; original traceback was not fully captured. Its cause is not independently reconstructed from an empty log. This original attempt is excluded; v5 actual execution is complete.'})

    restricted_path=OUT/'asan/restricted-environment-analysis-v3.json'; restricted=load(restricted_path)
    check(len(restricted['closed_fatal_environment_records'])==8 and len(restricted['incomplete_on_driver_termination'])==1,'restricted_failed_and_incomplete_attempts_preserved')
    restricted_rows=[]
    for item in restricted['closed_fatal_environment_records']:
        path=OUT/'asan'/item['record']; value=load(path); log=path.parent/value['log']; text=log.read_text()
        check(value['exit_code']!=0 and not value['execution_record_valid'],'restricted_attempt_not_passing',item['record'])
        check(sha(log)==item['log_sha256']==value['log_sha256'],'restricted_original_log_hash',item['record'])
        check('LeakSanitizer has encountered a fatal error' in text,'restricted_lsan_fatal_present',item['record'])
        check(all(value['environment_overrides'].get(k)==v for k,v in options.items()),'restricted_options_also_kept_enabled',item['record'])
        check(value['binaries_before']==value['binaries_after']==binaries,'restricted_same_binary_bytes',item['record'])
        restricted_rows.append({'record':item['record'],'exit_code':value['exit_code'],'log_sha256':sha(log),'explicit_proc_inspection_error':"Can't open /proc/" in text})
    for item in restricted['incomplete_on_driver_termination']:
        log=OUT/'asan'/item['path']
        check(log.stat().st_size==item['bytes'] and sha(log)==item['sha256'],'incomplete_original_log_preserved',item['path'])
        check(not log.with_name(log.name.removesuffix('.log')+'-command.json').exists(),'incomplete_not_fabricated_as_closed',item['path'])

    for name in ('contracts','plan','plan-guard','pip-check'): command_record('final/'+name+'-v2-command.json')
    contract=load(OUT/'final/contracts-v2.log'); guard=load(OUT/'final/plan-guard-v2.log'); plan=load(OUT/'final/plan-v2.log')
    check(contract['count']==contract['passed']==34 and all(x['passed'] for x in contract['results']),'34_original_contracts')
    check(guard['passed']==13 and guard['failed']==0 and not guard['product_acceptance_cases_executed'],'13_planning_guard_not_acceptance')
    check((plan['packages'],plan['acceptance_mappings'],plan['slices'])==(83,161,10) and not plan['product_acceptance_checked'],'unchanged_plan_counts_no_acceptance_claim')
    check((OUT/'final/pip-check-v2.log').read_text().strip()=='No broken requirements found.','dependency_gate_output')

    own=load(HERE/'independent-review.json')
    check(own['status']=='passed_final_v3_independent_focused_review','focused_independent_final_verdict')
    check(own['final_debug_library_sha256']==binaries['build/libbzs_core.a'] and own['final_sanitizer_library_sha256']==binaries['build-asan/libbzs_core.a'] and own['final_searchd_sha256']==binaries['build/searchd'],'independent_exact_final_builds')
    for name,value in own['production_inputs'].items(): check(files[str(Path(name).relative_to(REPO))]==value,'independent_final_source_input',name)
    for name, value in own['selected_records'].items():
        p=HERE/value['path']; check(sha(p)==value['summary_sha256'],'independent_selected_summary_unchanged',name)
        command=load(p.parent/'run-command.json'); check(sha(Path(command['log_path']))==command['log_sha256'],'independent_selected_log_unchanged',name)
    for manifest_name in ('archive-manifest.json','closed-ctest-archive.json','closed-ctest-archive-v2.json'):
        value=load(HERE/manifest_name)
        for name,record in value['files'].items(): check(sha(HERE/name)==record['sha256'] and (HERE/name).stat().st_size==record['bytes'],'prior_independent_archive_bytes_unchanged',name)

    old=load(OUT/'old-validation-before.json')
    if 'files' in old: old=old['files']
    check(len(old)==3590 and all(sha(REPO/name)==value for name,value in old.items()),'old_3590_validation_bytes_still_equal_freeze')
    check(ctest_review['supplemental_historical_binding']['all_current_pre_snapshot_and_git_blob_bytes_equal'],'prior_old_git_blob_binding_retained')
    preservation=load(OUT/report['preservation']['path'])
    check(preservation['passed'] and not preservation['failed_checks'] and len(preservation['checks'])==38,'38_static_preservation_checks')
    for name,value in preservation['final_input_sha256'].items(): check(sha(REPO/name)==value,'preservation_audit_current_input',name)
    original=subprocess.check_output(['git','show',report['baseline_commit']+':docs/implementation_status.json'],cwd=REPO).decode()
    current=(REPO/'docs/implementation_status.json').read_text()
    def feature_bytes(text: str) -> bytes:
        match=re.search(r'"features"\s*:\s*',text); start=match.end(); _,end=json.JSONDecoder().raw_decode(text,start)
        return text[start:end].encode()
    feature=feature_bytes(current)
    check(feature==feature_bytes(original) and len(feature)==122468 and hashlib.sha256(feature).hexdigest()=='f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d','original_feature_bytes_independently_equal')
    config=load(REPO/'config/sources.json'); sites=load(REPO/'config/sites.json')
    check(len(config['sources'])==17 and not any(x.get('enabled',False) for x in config['sources']) and len(sites['sites'])==0,'sources_17_disabled_sites_zero')
    for folder in ('docs/planning','config'):
        data=subprocess.check_output(['git','ls-tree','-r','-z',report['baseline_commit'],'--',folder],cwd=REPO)
        for entry in data.split(b'\0'):
            if not entry: continue
            metadata,name=entry.split(b'\t',1); source=(REPO/name.decode()).read_bytes()
            identity=hashlib.sha1(b'blob '+str(len(source)).encode()+b'\0'+source).hexdigest()
            check(identity==metadata.decode().split()[2],'planning_and_config_git_bytes_unchanged',name.decode())

    docs=load(OUT/report['documentation_audit']['path'])
    check(docs['all_bound_input_hashes_match'] and docs['bound_file_count']==227,'document_final_audit_binding')
    for name,value in docs['document_hashes'].items(): check(sha(REPO/name)==value,'current_document_hash',name)
    local_links=[]
    for target in re.findall(r'\]\(([^)]+)\)',readme):
        if '://' in target or target.startswith('#'): continue
        target=target.split('#',1)[0]
        if target:
            p=OUT/target; check(p.exists(),'validation_readme_local_link',target); local_links.append(target)
    for item in docs['link_check']['parent_report_placeholders']:
        p=(REPO/item['path']).parent/item['target']; check(p.exists(),'prior_parent_report_link_now_exists',str(p))
    for phrase in ('14 个 v4 成功执行 + 2 个 v5 补跑执行','all_passed=false','原始 traceback 未单独完整捕获','1,294','20,397','18 场景 / 679','12 场景 / 137','完整 CACHE-01 / B-24 未签收'):
        check(phrase in readme,'readme_explicit_scope_and_nonadditive_counting',phrase)
    check(report['remote_publication']['new_remote_ci']=='not_yet_run_at_report_generation','no_future_ci_claim')
    check(report['database_schema']=={'before':15,'after':15,'migration_added':False} and report['package_version']=='0.1.10','schema_and_version_unchanged')
    check(report['summary_script_sha256']==sha(OUT/'summarize.py'),'aggregate_summary_script_hash')

    result={
        'recorded_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'passed_final_local_aggregate_review' if not issues else 'blocked_final_local_aggregate_review',
        'audit_kind':'Read-only independent audit of completed records. No product tests, mutations to existing evidence, source changes or remote writes.',
        'reviewer_script_sha256':sha(Path(__file__).resolve()),
        'root_report':{'path':'../final-report.json','sha256':sha(report_path)},
        'root_readme':{'path':'../README.md','sha256':sha(readme_path)},
        'post_run_inputs':{'path':'../final/post-run-inputs.json','sha256':sha(OUT/'final/post-run-inputs.json'),'files':227,'binaries':38},
        'final_ctest':{'review':'closed-ctest-review-v2.json','review_sha256':sha(HERE/'closed-ctest-review-v2.json'),'suites':28,'passed':28,'skipped':0,'nested_skips':0,'native_targets':16,'native_cases':544,'native_assertions':20397,'python_pytest_passed':1294,'unittest_component_cases':123,'junit_note':'24 JUnit stdout fields explicitly truncate at 1024 bytes. Full counts use complete LastTest, already verified byte-for-byte against all 28 stdout sections of the hash-bound verbose log.'},
        'final_sanitizers':{'selection':'14 actual successful v4 executions plus revalidation and cache_freshness v5 actual executions','targets':sanitizer_rows,'cases':544,'assertions':20397,'same_cases_as_debug':True,'options':options,'initial_v4_driver_all_passed':driver['all_passed'],'initial_driver_sha256':sha(driver_path),'repairs':repair_rows,'restricted_v3_failures':restricted_rows,'restricted_v3_incomplete_logs':restricted['incomplete_on_driver_termination'],'historical_diagnostic_scope':'The eight failed logs directly show LSan fatal process-inspection failures. The two launcher causes are summarized contemporaneous observations; original full tracebacks are not available and are not treated as full execution evidence. Only the 16 complete selected actual-success records support the final sanitizer result.'},
        'closed_successful_records_verified':closed_commands,
        'report_references_verified':observed_refs,
        'preservation':{'old_validation_files':3590,'old_bytes_still_equal_pre_snapshot':True,'baseline_git_blob_comparison_carried_from_unchanged_closed_ctest_audit':True,'original_features_bytes':len(feature),'original_features_sha256':hashlib.sha256(feature).hexdigest(),'planning_and_config_bytes_equal_baseline':True,'registered_sources':17,'enabled_sources':0,'sites':0,'static_preservation_checks':38,'existing_independent_archives_unchanged':True},
        'documentation':{'current_document_hashes_match':not any(x['check']=='current_document_hash' for x in issues),'readme_links_checked':len(local_links),'all_prior_parent_report_placeholders_now_exist':True,'counting':'CTest components, formal additions, independent cases, sanitizer replays and original failures remain distinct. No duplicated acceptance count; no full B-24/CACHE-01 or future CI claim.','outer_service_receipt_boundary_retained':True},
        'failed_checks':issues,
        'limits':['Local validation closure only. Commit/publish and the new remote commit/tree/push CI are subsequent parent actions, not established here.','Historical incomplete/failed traces remain incomplete/failed; final success is supported by separately bound actual executions.','No additional test or source/evidence modifications were made by this audit. Existing reports remain unchanged.'],
        'repository_writes_complete_after_this_review':True,
    }
    target=HERE/('final-local-review.json' if not issues else 'final-local-review-blocked.json')
    with target.open('x',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    if not issues:
        entries={}
        for path in sorted(HERE.rglob('*')):
            if not path.is_file(): continue
            relative=path.relative_to(HERE)
            if 'runtime' in relative.parts or '__pycache__' in relative.parts or path.suffix in ('.pyc','.a','.sqlite','.sqlite-wal','.sqlite-shm'): continue
            path.read_text(encoding='utf-8')
            entries[str(relative)]={'sha256':sha(path),'bytes':path.stat().st_size}
        with (HERE/'final-local-archive.json').open('x',encoding='utf-8') as stream:
            json.dump({'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'All independent text/source/header/log evidence after final local aggregate audit. Excludes runtime state and this manifest itself. Earlier archives are preserved.','file_count':len(entries),'files':entries},stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'status':result['status'],'ctest':'28/28','sanitizers':'14 v4 + 2 v5','native':[544,20397],'inputs':len(files),'binaries':len(binaries),'failed_checks':issues,'report':str(target)},ensure_ascii=False,indent=2))
    return 0 if not issues else 1


if __name__=='__main__':
    raise SystemExit(main())
