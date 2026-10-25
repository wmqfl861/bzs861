"""Separate late-admission transaction fault probe; leaves the six-case probe unchanged."""
from pathlib import Path
import hashlib,json,subprocess,time
E=Path(__file__).resolve().parent
FROZEN=E/'migration-review-v2/candidate'
RUN=E/'admission-rollback-review-v1';RUN.mkdir(exist_ok=False)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (RUN/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def execute(name,argv):
    start=time.monotonic();result=subprocess.run([str(v) for v in argv],cwd=E,capture_output=True)
    (RUN/(name+'.stdout')).write_bytes(result.stdout);(RUN/(name+'.stderr')).write_bytes(result.stderr)
    save(name+'.command.json',{'argv':[str(v) for v in argv],'returncode':result.returncode,
      'elapsed_seconds':round(time.monotonic()-start,3),'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),
      'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()})
    print(name,result.returncode,flush=True);print(result.stdout.decode(errors='replace'),end='',flush=True)
    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace'))
report={'status':'running','scope':'Two branches of late admission rollback, before member INSERT; distinct from completion rollback.'}
try:
    inputs={'sources':{name:digest(E/name) for name in ['probe_support.hpp','late_join_rollback_probe.cpp','run_rollback_review.py']},
      'library_sha256':digest(FROZEN/'libbzs_core.a'),'frozen_migration_inputs_sha256':digest(E/'migration-review-v2/inputs.json')}
    assert inputs['library_sha256']=='35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0';save('inputs.json',inputs)
    binary=RUN/'candidate-probe'
    execute('compile-candidate',['c++','-std=c++20','-O0','-g','-I',FROZEN/'include',E/'late_join_rollback_probe.cpp',FROZEN/'libbzs_core.a',
      '-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',binary])
    execute('run-candidate',[binary,RUN/'candidate-databases'])
    assert all(digest(E/name)==value for name,value in inputs['sources'].items())
    assert digest(FROZEN/'libbzs_core.a')==inputs['library_sha256']
    report.update(status='passed',cases=2,failures=0,sources_unchanged=True,binary_sha256=digest(binary),
      exact_rows_preserved=['jobs','read_job_budgets','read_job_items','read_shared_attempts','read_shared_members','settings'],
      failure='E_STORAGE',same_idempotency_key_retry_succeeds=True,original_request_charged_once=True)
except Exception as error:report.update(status='failed',error=str(error));raise
finally:save('review-result.json',report)
