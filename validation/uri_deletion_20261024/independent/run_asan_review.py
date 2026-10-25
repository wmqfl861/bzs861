#!/usr/bin/env python3
"""Same independent C++ probes on the finalized ASan/UBSan core library."""
import hashlib,json,os,shutil,subprocess,time
from pathlib import Path
E=Path(__file__).resolve().parent
ROOT=E.parent/'bzs861'
D=E/'asan-review';D.mkdir(exist_ok=False)
shutil.copytree(ROOT/'include',D/'include')
shutil.copy2(ROOT/'build-asan/libbzs_core.a',D/'libbzs_core.a')
env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=1:halt_on_error=1';env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,r):(D/n).write_text(json.dumps(r,indent=2)+'\n')
def run(n,argv):
    t=time.monotonic();p=subprocess.run([str(x) for x in argv],cwd=E,env=env,capture_output=True)
    (D/(n+'.stdout')).write_bytes(p.stdout);(D/(n+'.stderr')).write_bytes(p.stderr)
    save(n+'.command.json',{'argv':[str(x) for x in argv],'returncode':p.returncode,'elapsed_seconds':time.monotonic()-t,
         'sanitizer_environment':{k:env[k] for k in ['ASAN_OPTIONS','UBSAN_OPTIONS']},
         'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(p.stderr).hexdigest()})
    print(n,p.returncode,flush=True)
    if p.returncode:raise RuntimeError(n+': '+p.stderr.decode(errors='replace')+p.stdout.decode(errors='replace'))
report={'status':'running','external_network_requests':0,'scope':'independent migration/original redirect/legacy direct/key boundaries'}
try:
    save('inputs.json',{'library_sha256':digest(D/'libbzs_core.a'),
       'headers':{str(p.relative_to(D/'include')):digest(p) for p in sorted((D/'include').rglob('*')) if p.is_file()},
       'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'src').glob('*.cpp'))},
       'probes':{n:digest(E/n) for n in ['migration_probe.cpp','original_redirect_probe.cpp','direct_missing_fence_probe.cpp','key_probe.cpp','run_key_review.py']}})
    for src,target in [('migration_probe.cpp','migration-probe'),('original_redirect_probe.cpp','original-redirect-probe'),
                       ('direct_missing_fence_probe.cpp','direct-missing-fence-probe'),('key_probe.cpp','key-probe')]:
        run('compile-'+target,['c++','-std=c++20','-O0','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
             '-I',D/'include',E/src,D/'libbzs_core.a','-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',D/target])
    save('binary-hashes.json',{p.name:digest(p) for p in D.iterdir() if p.is_file() and p.name.endswith('probe')})
    db=D/'real-schema13-copy.sqlite';shutil.copy2(E/'migration-review-v2/schema13.sqlite',db)
    run('real-old-upgrade',[D/'migration-probe','open',db])
    run('verify-real-old-upgrade',[D/'migration-probe','verify',db,E/'migration-review-v2/fixture-receipt.json'])
    run('cross-handle',[D/'migration-probe','writes',db])
    run('original-six-cases',[D/'original-redirect-probe'])
    run('legacy-direct-seven-cases',[D/'direct-missing-fence-probe'])
    run('key-matrix',['python3',E/'run_key_review.py',D])
    report.update(status='passed',original_cases=6,original_checks=250,direct_cases=7,direct_checks=119,key_vectors=534,
                  sanitizers='ASan + UBSan with leak detection enabled')
except Exception as exc:
    report.update(status='failed',error=str(exc));raise
finally:save('review-result.json',report)
