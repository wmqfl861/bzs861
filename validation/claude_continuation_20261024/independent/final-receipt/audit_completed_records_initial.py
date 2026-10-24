#!/usr/bin/env python3
"""Verify completed record hashes only; does not execute a product command."""
import hashlib,json
from pathlib import Path
ROOT=Path('/workspace/scratch/e2371129386d/bzs861')
B=ROOT/'validation/claude_continuation_20261024'
OUT=Path(__file__).parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
checks=[]
for p in sorted((B/'final-verification').glob('*-command.json')):
 d=read(p)
 assert d['exit_code']==0 and not d['process_watchdog_expired'] and d['source_inputs_unchanged']
 assert d['execution_inputs_sha256']==sha(B/'final-verification/execution-inputs.json')
 assert d['log_sha256']==sha(d['log'])
 if not d['build_preparation']:assert d['binaries_before_sha256']==d['binaries_after_sha256']
 checks.append({'path':str(p.relative_to(B)),'sha256':sha(p),'argv':d['argv'],'log_sha256':d['log_sha256'],'exit_code':0})
assert len(checks)==9
structure=read(B/'final-verification/planning-structure.log')
assert structure['read_only'] and structure['structure_valid']
assert [structure['packages'],structure['acceptance_mappings'],structure['slices']]==[83,161,10]
assert not structure['product_acceptance_checked']
assert (B/'final-verification/python-dependencies.log').read_text().strip()=='No broken requirements found.'

search=[]
for variant,run in [('final-debug','run-probe-debug'),('final-asan','run-probe-asan-native')]:
 folder=B/'independent/search-lock-final'/variant
 report=read(folder/'evidence/review-result.json')
 for name,digest in report['artifacts'].items(): assert sha(folder/name)==digest
 command=read(folder/f'evidence/{run}.json')
 assert command['exit_code']==0 and not command['process_watchdog_expired']
 assert command['inputs_before']==command['inputs_after'] and command['sources_before']==command['sources_after']
 assert command['artifacts_before']==command['artifacts_after']
 assert command['log_sha256']==sha(folder/f'evidence/{run}.log')
 assert (folder/f'evidence/{run}.log').read_text().strip()=='PASS 19 independent scenarios, 191 checks'
 assert report['result']['scenarios']==19 and report['result']['checks']==191 and report['result']['exit_code']==0
 if variant=='final-asan':
  assert command['environment']['ASAN_OPTIONS']=='detect_leaks=1:halt_on_error=1'
  assert command['environment']['UBSAN_OPTIONS']=='halt_on_error=1:print_stacktrace=1'
 search.append({'variant':variant,'report_sha256':sha(folder/'evidence/review-result.json'),'scenarios':19,'checks':191,'log_sha256':command['log_sha256']})

folder=B/'independent/final-http-dispatch'
report=read(folder/'review.json')
for name,digest in report['evidence_sha256'].items(): assert sha(folder/name)==digest
raw={}
for variant,expected in [('http',39),('dispatch',50)]:
 command=read(folder/variant/'command.json')
 result=read(folder/variant/'execution.json')
 assert command['exit_code']==0 and not command['watchdog_expired']
 assert command['log_sha256']==sha(folder/variant/'run.log')
 assert result['checks']==expected
 assert result==report['results']['http' if variant=='http' else 'dispatcher']
 assert command['binary_sha256']==report['binary_sha256']==sha(ROOT/'build/searchd')
 raw[variant]=result
assert report['results']['total_checks']==89
out={'targeted_post_document_commands':checks,'targeted_commands_count':9,'planning_structure':structure,'python_dependencies_clean':True,'independent_final_search':search,'independent_final_http_and_dispatch':raw,'counts_not_added_to_product_acceptance':True,'product_commands_executed_by_reviewer':False,'root_modified':False}
(OUT/'completed-records-audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print('Verified 9 completed targeted commands, two 19-scenario search replays, and final HTTP 39 + dispatcher 50 checks; no product commands executed.')
