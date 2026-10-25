"""Real schema14 API fixture, independent schema15 migration/legacy settlement review."""
from __future__ import annotations
import hashlib,json,shutil,sqlite3,subprocess,time
from pathlib import Path

E=Path(__file__).resolve().parent
BASE=E.parent/'bzs861-b24-baseline'
CAND=E.parent/'bzs861'
RUN=E/'migration-review-v2'
RUN.mkdir(exist_ok=False)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    with (RUN/name).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def execute(name,argv,expected=0):
    started=time.monotonic();result=subprocess.run([str(v) for v in argv],cwd=E,capture_output=True)
    (RUN/(name+'.stdout')).write_bytes(result.stdout);(RUN/(name+'.stderr')).write_bytes(result.stderr)
    save(name+'.command.json',{'argv':[str(v) for v in argv],'returncode':result.returncode,'expected_returncode':expected,
      'elapsed_seconds':round(time.monotonic()-started,3),'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),
      'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()})
    print(name,result.returncode,flush=True)
    if result.returncode!=expected:raise RuntimeError(name+': '+result.stderr.decode(errors='replace')+result.stdout.decode(errors='replace'))
    return result
def dump(db,columns=None):
    connection=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    try:
        tables={};virtual=[]
        for name,sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
            if sql and sql.upper().startswith('CREATE VIRTUAL TABLE'):virtual.append(name);continue
            quote=lambda value:'"'+value.replace('"','""')+'"'
            fields=columns[name]['columns'] if columns is not None else [row[1] for row in connection.execute('PRAGMA table_info('+quote(name)+')')]
            rows=connection.execute('SELECT '+','.join(quote(v) for v in fields)+' FROM '+quote(name)).fetchall()
            enc=lambda value:{'bytes_hex':value.hex()} if isinstance(value,bytes) else value
            rendered=sorted(json.dumps([enc(v) for v in row],ensure_ascii=False,separators=(',',':')) for row in rows)
            tables[name]={'columns':fields,'rows':len(rows),'sha256':hashlib.sha256(('\n'.join(rendered)+'\n').encode()).hexdigest()}
        return {'version':connection.execute('PRAGMA user_version').fetchone()[0],'tables':tables,'virtual_tables_covered_by_shadow_data':virtual}
    finally:connection.close()

report={'status':'running','baseline_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=BASE,text=True).strip(),
        'scope':'Independent real old API fixture; no production/formal-test edits or external requests.'}
try:
    inputs={}
    libraries={'baseline':E.parent/'b24-baseline-evidence/libbzs_core.a','candidate':CAND/'build/libbzs_core.a'}
    expected={'baseline':'30ea0955517d8a2a585f2b01c4ba42fd1530ca4a9431284f74fa04caf2d80338',
              'candidate':'35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0'}
    for name,root in [('baseline',BASE),('candidate',CAND)]:
        destination=RUN/name;destination.mkdir();shutil.copytree(root/'include',destination/'include')
        shutil.copy2(libraries[name],destination/'libbzs_core.a');assert digest(destination/'libbzs_core.a')==expected[name]
        inputs[name]={'library_sha256':expected[name],
          'headers':{str(p.relative_to(destination/'include')):digest(p) for p in sorted((destination/'include').rglob('*')) if p.is_file()},
          'production_sources':{str(p.relative_to(root)):digest(p) for p in sorted((root/'src').glob('*.cpp'))}}
    inputs['probe_sources']={name:digest(E/name) for name in ['probe_support.hpp','migration_probe.cpp','migration_probe_v2.cpp','run_migration_review_v2.py']}
    save('inputs.json',inputs)
    for name in ('baseline','candidate'):
        folder=RUN/name
        execute('compile-'+name,['c++','-std=c++20','-O0','-g','-I',folder/'include',E/'migration_probe_v2.cpp',folder/'libbzs_core.a',
                                '-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',folder/'migration-probe'])
    old=RUN/'real-schema14.sqlite';backup=RUN/'real-schema14-backup.sqlite'
    created=execute('create-real-schema14',[RUN/'baseline/migration-probe','create',old,backup])
    (RUN/'fixture-receipt.json').write_bytes(created.stdout)
    before=dump(old);save('schema14-old-columns.json',before);assert before['version']==14
    assert 'dispatch_timeout_ms' not in before['tables']['read_job_items']['columns']
    candidate=RUN/'upgraded-schema15.sqlite';shutil.copy2(old,candidate)
    execute('open-new-core',[RUN/'candidate/migration-probe','open',candidate])
    after=dump(candidate,before['tables']);save('schema15-old-columns.json',after)
    assert after['version']==15 and after['tables']==before['tables']
    execute('verify-legacy-before-settlement',[RUN/'candidate/migration-probe','verify',candidate,RUN/'fixture-receipt.json'])
    execute('verify-second-open',[RUN/'candidate/migration-probe','verify',candidate,RUN/'fixture-receipt.json'])
    assert dump(candidate,before['tables'])['tables']==before['tables']
    execute('old-core-refuses15',[RUN/'baseline/migration-probe','expect-error',candidate,'E_SCHEMA_VERSION'])
    execute('backup-still-quarantined',[RUN/'candidate/migration-probe','expect-error',backup,'E_RESTORE_QUARANTINED'])
    assert dump(backup)['version']==14
    execute('baseline-core-integrity',[RUN/'baseline/migration-probe','integrity',old])
    execute('candidate-core-integrity',[RUN/'candidate/migration-probe','integrity',candidate])
    with sqlite3.connect(candidate) as conn:
        defaults=conn.execute('SELECT state,dispatch_timeout_ms,sharing_closed,count(*) FROM read_job_items GROUP BY 1,2,3 ORDER BY 1').fetchall()
        plan=conn.execute("EXPLAIN QUERY PLAN SELECT job_id,position FROM read_job_items INDEXED BY read_shared_open_uri WHERE url=? AND state='running' AND sharing_closed=0 AND request_grant>0 ORDER BY job_id,position LIMIT 32",('https://independent-shared.example/single',)).fetchall()
        assert all(timeout==0 and closed==1 for _,timeout,closed,_ in defaults)
        assert any('read_shared_open_uri' in row[3] for row in plan)
        assert not any('TEMP B-TREE' in row[3] for row in plan)
        save('legacy-defaults-and-query-plan.json',{'legacy_rows':defaults,'query_plan':plan,'integrity_check':'ok through registered Core connection',
          'bound_note':'At most32 matching returned candidates; residual owner/permission filters may inspect more same-URI open rows.'})
    execute('legacy-completes-new-attempt-opens',[RUN/'candidate/migration-probe','settle',candidate,RUN/'fixture-receipt.json'])
    # Fault only a copy of the real baseline fixture, after its API-created state.
    rollback=RUN/'rollback-schema14.sqlite';shutil.copy2(old,rollback)
    with sqlite3.connect(rollback) as conn:
        conn.execute("CREATE TRIGGER independent_migration_abort BEFORE UPDATE ON read_job_items BEGIN SELECT RAISE(ABORT,'independent migration fault'); END")
    failure_before=dump(rollback);save('rollback-before.json',failure_before)
    execute('injected-migration-error',[RUN/'candidate/migration-probe','expect-error',rollback,'E_STORAGE'])
    failure_after=dump(rollback);save('rollback-after.json',failure_after);assert failure_before==failure_after
    with sqlite3.connect(rollback) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='read_shared_open_uri'").fetchone()[0]==0
        conn.execute('DROP TRIGGER independent_migration_abort')
    execute('retry-after-fixture-fault-removal',[RUN/'candidate/migration-probe','open',rollback])
    assert all(digest(RUN/name/'libbzs_core.a')==value for name,value in expected.items())
    report.update(status='passed',schema_before=14,schema_after=15,old_tables_preserved=len(before['tables']),
      virtual_tables=before['virtual_tables_covered_by_shadow_data'],old_row_contents_preserved=True,
      legacy_defaults_closed=True,legacy_single_and_shared_and_cancelled_payer_settle=True,new_attempt_open_and_late_join=True,
      old_binary_rejected=True,backup_quarantine_preserved=True,migration_rollback_atomic=True,
      probe_sources_unchanged=all(digest(E/name)==value for name,value in inputs['probe_sources'].items()))
except Exception as error:
    report.update(status='failed',error=str(error));raise
finally:save('review-result.json',report)
