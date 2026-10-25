"""Run new independent counterexamples on frozen real old and candidate libraries."""
from pathlib import Path
import hashlib,json,subprocess,time
E=Path(__file__).resolve().parent
FROZEN=E/'migration-review-v2'
RUN=E/'behavior-review-v2';RUN.mkdir(exist_ok=False)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (RUN/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def execute(name,argv,expected):
    start=time.monotonic();result=subprocess.run([str(v) for v in argv],cwd=E,capture_output=True)
    (RUN/(name+'.stdout')).write_bytes(result.stdout);(RUN/(name+'.stderr')).write_bytes(result.stderr)
    save(name+'.command.json',{'argv':[str(v) for v in argv],'returncode':result.returncode,'expected':expected,
      'elapsed_seconds':round(time.monotonic()-start,3),'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),
      'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()})
    print(name,result.returncode,flush=True);print(result.stdout.decode(errors='replace'),end='',flush=True)
    if result.returncode!=expected:raise RuntimeError(result.stderr.decode(errors='replace'))
    return result
report={'status':'running','scope':'Six independent case groups, one with eight incompatibility controls; same sources, real frozen old/candidate libraries.'}
try:
    inputs={'sources':{name:digest(E/name) for name in ['probe_support.hpp','late_join_probe_v2.cpp','run_behavior_review_v2.py']},
      'libraries':{name:digest(FROZEN/name/'libbzs_core.a') for name in ('baseline','candidate')},
      'frozen_migration_inputs_sha256':digest(FROZEN/'inputs.json')};save('inputs.json',inputs)
    for name in ('baseline','candidate'):
        folder=FROZEN/name;binary=RUN/(name+'-probe')
        execute('compile-'+name,['c++','-std=c++20','-O0','-g','-I',folder/'include',E/'late_join_probe_v2.cpp',folder/'libbzs_core.a',
          '-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',binary],0)
        execute('run-'+name,[binary,RUN/(name+'-databases')],4 if name=='baseline' else 0)
    assert all(digest(E/name)==value for name,value in inputs['sources'].items())
    report.update(status='passed',baseline_expected_failures=4,candidate_failures=0,case_groups=6,extra_incompatibility_variants=8,
                  sources_unchanged=True,binary_sha256={name:digest(RUN/(name+'-probe')) for name in ('baseline','candidate')})
except Exception as error:report.update(status='failed',error=str(error));raise
finally:save('review-result.json',report)
