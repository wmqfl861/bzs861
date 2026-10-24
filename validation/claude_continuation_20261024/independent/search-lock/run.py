#!/usr/bin/env python3
"""Independent review command capture for the exact P2-5 source/library."""
from pathlib import Path
import datetime, hashlib, json, subprocess, sys, time
out=Path(__file__).resolve().parent
repo=out.parents[3]
target=Path('/workspace/scratch/e2371129386d/bzs861-next-search-lock')
name=sys.argv[1]
argv=sys.argv[2:]
if argv and argv[0]=='--': argv=argv[1:]
record=out/(name+'.json');log=out/(name+'.log')
assert not record.exists() and not log.exists()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sources():
 files=['src/store.cpp','src/query.cpp','include/bzs/store.hpp','include/bzs/query.hpp','tests/unit.cpp','docs/LOCAL_SEARCH_CONCURRENCY.md']
 return {p:sha(target/p) for p in files}
def artifacts():
 files=[target/'build/libbzs_core.a',target/'build-asan/libbzs_core.a',Path('/tmp/bzs-next-dispatcher-baseline/libbzs_core.a')]
 return {str(p):sha(p) for p in files if p.is_file()}
start=time.monotonic();data={'argv':argv,'utc_started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sources_before':sources(),'artifacts_before':artifacts(),'probe_inputs':{p.name:sha(p) for p in out.glob('*.cpp')},'runner_sha256':sha(Path(__file__).resolve())}
with log.open('w') as stream:process=subprocess.run(argv,cwd=repo,stdout=stream,stderr=subprocess.STDOUT)
data.update(exit_code=process.returncode,seconds=round(time.monotonic()-start,3),sources_after=sources(),artifacts_after=artifacts(),log_sha256=sha(log))
record.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
print(json.dumps({'command':name,'exit_code':process.returncode,'seconds':data['seconds'],'sources_unchanged':data['sources_before']==data['sources_after']}))
print(log.read_text()[-20000:])
sys.exit(process.returncode if data['sources_before']==data['sources_after'] else 90)
