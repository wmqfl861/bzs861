"""Audit closed parent CTest and auxiliary records without executing tests."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,xml.etree.ElementTree as ET
E=Path(__file__).resolve().parent
REPO=E.parent/'bzs861'
RECORDS=REPO/'validation/shared_read_late_20261024/final'
OUT=E/'integration-ctest-review-v1';OUT.mkdir(exist_ok=False)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (OUT/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
records={};binaries=None;common_inputs=None
for name in ('ctest','plan','contracts','plan-guard','pip-check'):
    path=RECORDS/(name+'-command.json');record=json.loads(path.read_text())
    assert record['exit_code']==0 and record['execution_record_valid'] and not record['watchdog_expired']
    assert record['source_inputs_unchanged'] and record['changed_inputs']==[]
    assert not record['build_preparation'] and record['binaries_before']==record['binaries_after']
    assert digest(RECORDS/record['inputs'])==record['inputs_sha256']
    assert digest(RECORDS/record['log'])==record['log_sha256']
    inputs=json.loads((RECORDS/record['inputs']).read_text())
    assert inputs['base_commit']=='a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89'
    if common_inputs is None:common_inputs=inputs['files'];binaries=record['binaries_after']
    else:assert inputs['files']==common_inputs and record['binaries_after']==binaries
    records[name]={'command_sha256':digest(path),'log_sha256':record['log_sha256'],
      'inputs_sha256':record['inputs_sha256'],'exit_code':0,'elapsed_seconds':record['duration_seconds'],
      'recorded_inputs_stable':True,'recorded_binaries_stable':True}
assert all(digest(REPO/path)==value for path,value in binaries.items())
current_delta=[path for path,value in common_inputs.items() if digest(REPO/path)!=value]
assert all(path.endswith('.md') or path=='docs/implementation_status.json' for path in current_delta),current_delta
detail=RECORDS/'ctest-detail.log';full=detail.read_text()
assert digest(detail)==digest(REPO/'build/Testing/Temporary/LastTest.log')
headers=list(re.finditer(r'(?m)^\d+/27 Testing: ([^\n]+)$',full));assert len(headers)==27
assert full.count('Test Passed.')==27
counts=[]
for i,header in enumerate(headers[:15]):
    block=full[header.end():headers[i+1].start() if i+1<len(headers) else len(full)];matches=[]
    for line in block.splitlines():
        if 'assertions' not in line:continue
        if line.startswith('{"tests":'):
            value=json.loads(line);assert value['tests']==value['passed'];matches.append((value['tests'],value['assertions'],line))
        elif line.startswith('Contract tests:'):
            match=re.search(r'Contract tests: (\d+) passed, 0 failed;.* (\d+) assertions',line)
            matches.append((int(match[1]),int(match[2]),line))
        else:
            match=re.match(r'^(\d+)(?:/\d+)? (?:.*? )?(?:tests|cases)[,/ ]+(\d+) assertions',line)
            if match:matches.append((int(match[1]),int(match[2]),line))
    assert len(matches)==1,(header[1],matches)
    passed,assertions,line=matches[0];counts.append({'suite':header[1],'passed':passed,'assertions':assertions,'summary_line':line})
assert sum(value['passed'] for value in counts)==521
assert sum(value['assertions'] for value in counts)==16761
python=re.findall(r'(?m)^(\d+) passed in ([0-9.]+)s$',full);assert python==[('1281','16.26')]
xml=ET.parse(RECORDS/'ctest.xml').getroot()
assert xml.attrib['tests']=='27' and xml.attrib['failures']=='0' and xml.attrib['skipped']=='0' and xml.attrib['disabled']=='0'
assert [value.attrib['name'] for value in xml.findall('testcase')]==[value[1] for value in headers]
assert all(value.attrib.get('status')=='run' and value.find('failure') is None and value.find('skipped') is None for value in xml.findall('testcase'))
assert '100% tests passed, 0 tests failed out of 27' in (RECORDS/'ctest.log').read_text()
plan=json.loads((RECORDS/'plan.log').read_text());assert plan['packages']==83 and plan['acceptance_mappings']==161 and plan['slices']==10 and not plan['product_acceptance_checked']
contracts=json.loads((RECORDS/'contracts.log').read_text());assert contracts['count']==contracts['passed']==34
guard=json.loads((RECORDS/'plan-guard.log').read_text());assert guard['passed']==13 and guard['failed']==0 and guard['product_acceptance_cases_executed']==0
assert (RECORDS/'pip-check.log').read_text().strip()=='No broken requirements found.'
legacy='validation/s2_takeover/cache_review/probe_http_policy.py'
old_manifest=REPO/'validation/shared_read_late_20261024/old-validation-before.json'
old=json.loads(old_manifest.read_text());assert len(old)==3193
assert legacy in (REPO/'build/CTestTestfile.cmake').read_text() and digest(REPO/legacy)==old[legacy]
assert all(digest(REPO/path)==value for path,value in old.items())
result={'reviewed_at':datetime.now(timezone.utc).isoformat(),'status':'passed','scope':'Read-only review of five closed parent records; no rerun.',
 'records':records,'nonvalidation_input_count':len(common_inputs),'binary_count':len(binaries),
 'recorded_inputs_equal_across_all_five_commands':True,'binaries_match_current':True,
 'current_postrun_documentation_delta':current_delta,'production_tests_and_runtime_inputs_match_current':True,
 'ctest':{'passed':27,'total':27,'skipped':0,'disabled':0,'native_targets':15,'native_groups':521,'native_assertions':16761,
   'native_details':counts,'python_worker_passed':1281,'original_full_output_sha256':digest(detail),
   'archived_detail_matches_original_LastTest_log':True,'junit_sha256':digest(RECORDS/'ctest.xml'),
   'junit_output_is_truncated':'JUnit validates target statuses only; assertion and Python counts use full LastTest.log.'},
 'auxiliary':{'plan_packages':83,'plan_acceptance_mappings':161,'plan_slices':10,'contract_fixtures_passed':34,'plan_guard_passed':13,'pip_check':'no broken requirements'},
 'old_validation_dependency':{'path':legacy,'sha256':old[legacy],'all3193_original_files_match_pre_run_snapshot':True,
   'scope':'Supplemental original-file retention binding, not a claim that run.py included validation inputs in its own manifest.'},
 'limits':'This review does not add overlapping test counts to product acceptance and does not claim the still-separate final sanitizer report or remote CI.'}
save('ctest-and-auxiliary-review.json',result)
save('archive-addendum.json',{'files':[{'path':'audit_closed_ctest.py','sha256':digest(Path(__file__))},
 {'path':'integration-ctest-review-v1/ctest-and-auxiliary-review.json','sha256':digest(OUT/'ctest-and-auxiliary-review.json')}],
 'include_this_addendum_itself':True,'scope':'Additional three text files after the initial independent archive manifest; keep the initial manifest unchanged.'})
print(json.dumps({'status':'passed','ctest':27,'native_groups':521,'native_assertions':16761,'python_worker':1281,
 'input_count':len(common_inputs),'current_documentation_delta':current_delta},ensure_ascii=False))
