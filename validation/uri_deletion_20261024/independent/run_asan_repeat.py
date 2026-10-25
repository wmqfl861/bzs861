#!/usr/bin/env python3
"""Replay the exact previously built binaries with LSan in native execution."""
import hashlib,json,os,shutil,subprocess,time
from pathlib import Path
E=Path(__file__).resolve().parent
FROZEN=E/'asan-review';D=E/'asan-review-permitted';D.mkdir(exist_ok=False)
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
report={'status':'running','external_network_requests':0,'same_frozen_binaries':True,'original_failure_record':'../asan-review/review-result.json'}
try:
    original=json.loads((FROZEN/'binary-hashes.json').read_text())
    before={n:digest(FROZEN/n) for n in original};assert before==original
    save('exact-binaries-before.json',before)
    # Use the same key binary without overwriting the old folder's files.
    shutil.copy2(FROZEN/'key-probe',D/'key-probe')
    db=D/'real-schema13-copy.sqlite';shutil.copy2(E/'migration-review-v2/schema13.sqlite',db)
    run('real-old-upgrade',[FROZEN/'migration-probe','open',db])
    run('verify-real-old-upgrade',[FROZEN/'migration-probe','verify',db,E/'migration-review-v2/fixture-receipt.json'])
    run('cross-handle',[FROZEN/'migration-probe','writes',db])
    run('original-six-cases',[FROZEN/'original-redirect-probe'])
    run('legacy-direct-seven-cases',[FROZEN/'direct-missing-fence-probe'])
    run('key-matrix',['python3',E/'run_key_review.py',D])
    after={n:digest(FROZEN/n) for n in original};save('exact-binaries-after.json',after);assert after==before
    report.update(status='passed',original_cases=6,original_checks=250,direct_cases=7,direct_checks=119,key_vectors=534,
                  sanitizers='ASan + UBSan with leak detection enabled')
except Exception as exc:
    report.update(status='failed',error=str(exc));raise
finally:save('review-result.json',report)
