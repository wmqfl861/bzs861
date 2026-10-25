"""Replay the unchanged independent migration, six-case, and admission rollback probes with sanitizers."""
from __future__ import annotations
from pathlib import Path
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time
E=Path(__file__).resolve().parent
CAND=E.parent/'bzs861'
RUN=E/'sanitizer-review-v1'
SOURCE_NAMES=['probe_support.hpp','migration_probe.cpp','migration_probe_v2.cpp','late_join_probe_v2.cpp','late_join_rollback_probe.cpp']
PROBES={'migration':'migration_probe_v2.cpp','behavior':'late_join_probe_v2.cpp','admission-rollback':'late_join_rollback_probe.cpp'}
ENV={'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
EXPECTED_LIB='68b726db8e5e75e1a534a577c56f6b8a9565b88fcad02a5667ea58c1dd9db774'
EXPECTED_PRODUCTION={'include/bzs/store.hpp':'ecc93f4edb412c48f04686126fcdabfbc4b4b17752a56c0bddc72f589bd03772',
 'src/store.cpp':'2c85dc7708183b950331866dbd40e11f9de70ac34799ae65d32fbec0fbec3b89',
 'src/batch.cpp':'ab8e4fd0b9b16645dc37bd7df319177cab81d026be7bcca104b25d0bbdcd9fd1'}
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (RUN/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def bindings():
    values={'frozen/'+str(p.relative_to(RUN/'frozen')):digest(p) for p in sorted((RUN/'frozen').rglob('*')) if p.is_file()}
    values['runner']=digest(Path(__file__))
    values.update({'original-probe/'+name:digest(E/name) for name in SOURCE_NAMES})
    values.update({'production/'+name:digest(CAND/name) for name in EXPECTED_PRODUCTION})
    return values
def execute(name,argv,sanitized=False):
    before=bindings();start=time.monotonic();env=os.environ.copy()
    if sanitized:env.update(ENV)
    result=subprocess.run([str(v) for v in argv],cwd=E,capture_output=True,env=env)
    (RUN/(name+'.stdout')).write_bytes(result.stdout);(RUN/(name+'.stderr')).write_bytes(result.stderr)
    after=bindings();stable=before==after if sanitized else all(after.get(name)==value for name,value in before.items())
    save(name+'.command.json',{'argv':[str(v) for v in argv],'returncode':result.returncode,
      'elapsed_seconds':round(time.monotonic()-start,3),'sanitizer_environment':ENV if sanitized else {},
      'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(result.stderr).hexdigest(),
      'inputs_before':before,'inputs_after':after,'inputs_stable':stable,
      'compile_output_additions_allowed':not sanitized})
    print(name,result.returncode,flush=True);print(result.stdout.decode(errors='replace'),end='',flush=True)
    if result.returncode or not stable:raise RuntimeError(name+': '+result.stderr.decode(errors='replace'))
    return result
def dump(db,columns):
    connection=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    try:
        tables={}
        for name,table in columns.items():
            quote=lambda value:'"'+value.replace('"','""')+'"'
            fields=table['columns'];rows=connection.execute('SELECT '+','.join(quote(v) for v in fields)+' FROM '+quote(name)).fetchall()
            enc=lambda value:{'bytes_hex':value.hex()} if isinstance(value,bytes) else value
            rendered=sorted(json.dumps([enc(v) for v in row],ensure_ascii=False,separators=(',',':')) for row in rows)
            tables[name]={'columns':fields,'rows':len(rows),'sha256':hashlib.sha256(('\n'.join(rendered)+'\n').encode()).hexdigest()}
        return {'version':connection.execute('PRAGMA user_version').fetchone()[0],'tables':tables}
    finally:connection.close()
def prepare():
    RUN.mkdir(exist_ok=False);frozen=RUN/'frozen';frozen.mkdir();(frozen/'sources').mkdir();(frozen/'bin').mkdir()
    shutil.copytree(E/'migration-review-v2/candidate/include',frozen/'include')
    shutil.copy2(CAND/'build-asan/libbzs_core.a',frozen/'libbzs_core.a');assert digest(frozen/'libbzs_core.a')==EXPECTED_LIB
    for name in SOURCE_NAMES:shutil.copy2(E/name,frozen/'sources'/name)
    assert all(digest(CAND/name)==value for name,value in EXPECTED_PRODUCTION.items())
    for name,source in PROBES.items():
        execute('compile-'+name,['c++','-std=c++20','-O0','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
          '-I',frozen/'include',frozen/'sources'/source,frozen/'libbzs_core.a',
          '-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',frozen/'bin'/name])
    # Compilers add their distinct output binaries; freeze the complete runnable input set afterward.
    save('frozen-inputs.json',bindings())
def run():
    report={'status':'running','scope':'Same probes as Debug; sanitizer execution is replay, not additional unique coverage.',
      'sanitizer_environment':ENV,'library_sha256':EXPECTED_LIB}
    before=bindings();save('run-inputs-before.json',before)
    try:
        assert before==json.loads((RUN/'frozen-inputs.json').read_text())
        source_db=E/'migration-review-v2/real-schema14.sqlite';db=RUN/'real-schema14-to15.sqlite'
        old=json.loads((E/'migration-review-v2/schema14-old-columns.json').read_text())
        assert dump(source_db,old['tables'])=={'version':14,'tables':old['tables']}
        shutil.copy2(source_db,db);shutil.copy2(E/'migration-review-v2/fixture-receipt.json',RUN/'fixture-receipt.json')
        save('migration-fixture-inputs.json',{'real_schema14_sha256':digest(source_db),'copied_database_sha256':digest(db),
          'old_table_snapshot_sha256':digest(E/'migration-review-v2/schema14-old-columns.json'),
          'receipt_sha256':digest(RUN/'fixture-receipt.json'),'baseline_library_sha256':'30ea0955517d8a2a585f2b01c4ba42fd1530ca4a9431284f74fa04caf2d80338'})
        binary=RUN/'frozen/bin/migration'
        execute('migrate-real-schema14',[binary,'open',db],True)
        after=dump(db,old['tables']);save('schema15-old-column-digests.json',after)
        assert after=={'version':15,'tables':old['tables']}
        execute('registered-core-integrity',[binary,'integrity',db],True)
        execute('legacy-closed-and-preserved',[binary,'verify',db,RUN/'fixture-receipt.json'],True)
        execute('legacy-completion-and-new-open-attempt',[binary,'settle',db,RUN/'fixture-receipt.json'],True)
        execute('six-behavior-groups',[RUN/'frozen/bin/behavior',RUN/'behavior-databases'],True)
        execute('two-admission-rollback-branches',[RUN/'frozen/bin/admission-rollback',RUN/'admission-rollback-databases'],True)
        report.update(status='passed',old_tables_preserved=47,behavior_groups=6,admission_rollback_branches=2,
          sanitizers_clean=True,leak_detection_enabled=True,production_unchanged=True)
    except Exception as error:report.update(status='failed',error=str(error));raise
    finally:
        after=bindings();save('run-inputs-after.json',after);report['frozen_inputs_stable']=before==after;save('review-result.json',report)
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('prepare','run'):raise SystemExit('usage: run_sanitizer_review.py prepare|run')
    prepare() if sys.argv[1]=='prepare' else run()
