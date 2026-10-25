#!/usr/bin/env python3
"""Independent old-library migration, preserved-row and rollback verification."""
from __future__ import annotations
import hashlib,json,os,re,shutil,sqlite3,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit

E=Path(__file__).resolve().parent
BASE=E.parent/'bzs861-baseline'
CAND=E.parent/'bzs861'
RUN=E/'migration-review-v1'
RUN.mkdir(exist_ok=False)

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,value):
    (RUN/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def execute(name,argv,expected=0):
    t=time.monotonic();p=subprocess.run([str(x) for x in argv],cwd=E,capture_output=True)
    (RUN/(name+'.stdout')).write_bytes(p.stdout);(RUN/(name+'.stderr')).write_bytes(p.stderr)
    r={'argv':[str(x) for x in argv],'returncode':p.returncode,'elapsed_seconds':time.monotonic()-t,
       'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(p.stderr).hexdigest(),
       'expected_returncode':expected}
    save(name+'.command.json',r)
    print(name,p.returncode,flush=True)
    if p.returncode!=expected:raise RuntimeError(name+': '+p.stderr.decode(errors='replace')+p.stdout.decode(errors='replace'))
    return p
def dump(db,old_columns=None):
    with sqlite3.connect(db) as conn:
        schema=conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        out={};virtual=[]
        for name,sql in schema:
            if sql and sql.upper().startswith('CREATE VIRTUAL TABLE'):
                virtual.append(name);continue
            columns=[r[1] for r in conn.execute('PRAGMA table_info("'+name.replace('"','""')+'")')]
            if old_columns is not None:columns=old_columns[name]['columns']
            q=lambda s:'"'+s.replace('"','""')+'"'
            records=conn.execute('SELECT '+','.join(map(q,columns))+' FROM '+q(name)).fetchall()
            enc=lambda v:{'bytes_hex':v.hex()} if isinstance(v,bytes) else v
            rendered=sorted(json.dumps([enc(x) for x in row],ensure_ascii=False,separators=(',',':')) for row in records)
            payload=('\n'.join(rendered)+'\n').encode()
            out[name]={'columns':columns,'rows':len(records),'sha256':hashlib.sha256(payload).hexdigest()}
        return {'user_version':conn.execute('PRAGMA user_version').fetchone()[0],'tables':out,
                'virtual_tables_covered_by_shadow_tables':virtual}
def independent_key(url):
    parsed=urlsplit(url)
    before=url.split('#',1)[0]
    if re.search(r'%(?![0-9A-Fa-f]{2})',before):return url
    authority=parsed.hostname.lower()
    if parsed.port and (parsed.scheme,parsed.port) not in [('https',443),('http',80)]:authority+=':'+str(parsed.port)
    unreserved='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
    def fold(m):
        c=chr(int(m[0][1:],16));return c if c in unreserved else m[0].upper()
    path=re.sub(r'%[0-9A-Fa-f]{2}',fold,parsed.path or '/')
    query=re.sub(r'%[0-9A-Fa-f]{2}',fold,parsed.query)
    return parsed.scheme+'://'+authority+path+('?' + query if query else '')

report={'status':'running','baseline_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=BASE,text=True).strip(),
        'network_requests':0,'role':'non-author migration/identity reviewer'}
try:
    inputs={}
    for name,root in [('baseline',BASE),('candidate',CAND)]:
        dst=RUN/name;dst.mkdir();shutil.copytree(root/'include',dst/'include')
        shutil.copy2(root/'build/libbzs_core.a',dst/'libbzs_core.a')
        inputs[name]={'lib_sha256':digest(dst/'libbzs_core.a'),'headers':{str(x.relative_to(dst/'include')):digest(x) for x in sorted((dst/'include').rglob('*')) if x.is_file()},
                     'source_hashes':{str(x.relative_to(root)):digest(x) for x in sorted((root/'src').glob('*.cpp'))}}
    inputs['probe_hashes']={p.name:digest(p) for p in [E/'migration_probe.cpp',E/'original_redirect_probe.cpp',Path(__file__)]}
    save('inputs.json',inputs)
    for name in ['baseline','candidate']:
        d=RUN/name
        for probe,output in [('migration_probe.cpp','migration-probe'),('original_redirect_probe.cpp','original-redirect-probe')]:
            execute('compile-'+name+'-'+output,['c++','-std=c++20','-O0','-g','-I',d/'include',E/probe,d/'libbzs_core.a',
                    '-lsqlite3','-lcrypto','-ljson-c','-lxml2','-pthread','-o',d/output])
    # Real old core creates every business row and the backup quarantine flag.
    baseline_db=RUN/'schema13.sqlite';backup=RUN/'schema13-backup.sqlite'
    p=execute('create-real-schema13',[RUN/'baseline/migration-probe','create',baseline_db,backup])
    (RUN/'fixture-receipt.json').write_bytes(p.stdout)
    before=dump(baseline_db);save('schema13-old-column-snapshot.json',before)
    assert before['user_version']==13
    assert 'deletion_key' not in before['tables']['documents']['columns']
    execute('historical-original-six-cases',[RUN/'baseline/original-redirect-probe'],expected=2)
    candidate_db=RUN/'schema14.sqlite';shutil.copy2(baseline_db,candidate_db)
    execute('upgrade-real-schema13',[RUN/'candidate/migration-probe','open',candidate_db])
    after=dump(candidate_db,before['tables']);save('schema14-old-column-snapshot.json',after)
    changed=[name for name,v in before['tables'].items() if v!=after['tables'][name]]
    assert not changed,changed
    assert after['user_version']==14
    with sqlite3.connect(candidate_db) as conn:
        rows=conn.execute('SELECT owner,url,deletion_key,deleted FROM documents ORDER BY owner,url').fetchall()
        comparisons=[{'owner':o,'url':u,'deleted':bool(d),'actual':k,'expected':independent_key(u),'match':k==independent_key(u)} for o,u,k,d in rows]
        assert all(x['match'] for x in comparisons)
        save('derived-key-comparison.json',comparisons)
        plan=conn.execute('EXPLAIN QUERY PLAN SELECT id,delete_generation,deleted FROM documents WHERE owner=? AND deletion_key IN (?,?) ORDER BY id LIMIT 1001',
                          ('independent-owner','https://independent-chain.example/middle','https://independent-chain.example/final')).fetchall()
        assert any('documents_deletion_key' in row[3] for row in plan),plan
        save('query-plan.json',plan)
    execute('old-core-refuses-schema14',[RUN/'baseline/migration-probe','expect-error',candidate_db,'E_SCHEMA_VERSION'])
    execute('new-core-preserves-backup-quarantine',[RUN/'candidate/migration-probe','expect-error',backup,'E_RESTORE_QUARANTINED'])
    assert dump(backup)['user_version']==13
    execute('verify-migrated-behavior',[RUN/'candidate/migration-probe','verify',candidate_db,RUN/'fixture-receipt.json'])
    execute('verify-second-reopen',[RUN/'candidate/migration-probe','verify',candidate_db,RUN/'fixture-receipt.json'])
    execute('candidate-original-six-cases',[RUN/'candidate/original-redirect-probe'])
    execute('new-writes-cross-handle',[RUN/'candidate/migration-probe','writes',candidate_db])
    # Inject a failing UPDATE into a real old DB copy; migration must not leave
    # the new column, index, user_version or any changed old business rows behind.
    failure=RUN/'rollback-schema13.sqlite';shutil.copy2(baseline_db,failure)
    with sqlite3.connect(failure) as conn:
        conn.execute("CREATE TRIGGER independent_reject_migration BEFORE UPDATE ON documents WHEN OLD.url='https://independent-chain.example/%6Diddle' BEGIN SELECT RAISE(ABORT,'independent migration failure'); END")
    failure_before=dump(failure);save('rollback-before.json',failure_before)
    execute('injected-migration-error',[RUN/'candidate/migration-probe','expect-error',failure,'E_STORAGE'])
    failure_after=dump(failure);save('rollback-after.json',failure_after)
    assert failure_before==failure_after
    with sqlite3.connect(failure) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='documents_deletion_key'").fetchone()[0]==0
        conn.execute('DROP TRIGGER independent_reject_migration')
    execute('retry-after-removing-fixture-fault',[RUN/'candidate/migration-probe','open',failure])
    report.update(status='passed',preserved_tables=len(before['tables']),virtual_tables=before['virtual_tables_covered_by_shadow_tables'],
                  old_business_columns_unchanged=True,documents_checked=len(comparisons),old_core_rejected=True,quarantine_preserved=True,
                  historical_original_expected_failures=2,candidate_original_failures=0,rollback_atomic=True)
except Exception as e:
    report.update(status='failed',error=str(e));raise
finally:
    save('review-result.json',report)
