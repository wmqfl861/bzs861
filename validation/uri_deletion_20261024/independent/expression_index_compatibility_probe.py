#!/usr/bin/env python3
"""Why an unknown-function expression index cannot replace a schema gate."""
import json,sqlite3,tempfile
from pathlib import Path
out=[]
with tempfile.TemporaryDirectory(prefix='bzs-independent-expression-') as temp:
    path=Path(temp)/'db'
    new=sqlite3.connect(path)
    new.create_function('bzs_deletion_key_v1',1,lambda s:s.lower(),deterministic=True)
    new.executescript("CREATE TABLE documents(id TEXT PRIMARY KEY,owner TEXT,url TEXT,deleted INT DEFAULT 0);"
       "INSERT INTO documents VALUES('a','o','%6Diddle',0);"
       "CREATE INDEX documents_deletion_identity_v1 ON documents(owner,bzs_deletion_key_v1(url));"
       "CREATE TABLE jobs(id INT,state TEXT);INSERT INTO jobs VALUES(1,'queued');")
    new.commit();old=sqlite3.connect(path)
    for sql in ["SELECT * FROM documents","UPDATE documents SET deleted=1 WHERE id='a'",
                "UPDATE jobs SET state='succeeded' WHERE id=1","UPDATE documents SET url='/middle' WHERE id='a'",
                "DELETE FROM documents WHERE id='a'","INSERT INTO documents VALUES('b','o','/b',0)"]:
        try:
            rows=old.execute(sql).fetchall();old.commit();out.append({'sql':sql,'succeeds':True,'rows':rows})
        except sqlite3.Error as e:
            old.rollback();out.append({'sql':sql,'succeeds':False,'error':str(e)})
    old.close();new.close()
assert [x['succeeds'] for x in out]==[True,True,True,False,False,False]
print(json.dumps({'sqlite_version':sqlite3.sqlite_version,'rows':out,
 'conclusion':'Missing application function blocks some document writes but permits reads, deletion flags and other-table writes; not a fail-closed compatibility boundary.'},indent=2))
