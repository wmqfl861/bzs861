from pathlib import Path
import hashlib,json,os,subprocess,sys,time
out=Path(__file__).resolve().parent
name,cwd,*command=sys.argv[1:]
root=out.parents[3]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
inputs={str(p.relative_to(root)):sha(p) for folder in ['src','include','python/bzs','python/tests','tests'] for p in sorted((root/folder).rglob('*')) if p.is_file() and not any(x in ('__pycache__','.pytest_cache') for x in p.parts)}
log=out/(name+'.log'); report=out/(name+'.json')
assert not log.exists() and not report.exists(),name
started=time.time()
with log.open('wb') as f:
 result=subprocess.run(command,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,env=os.environ.copy())
record={'name':name,'cwd':cwd,'command':command,'exit_code':result.returncode,'elapsed_seconds':round(time.time()-started,3),'log':log.name,'log_sha256':sha(log),'inputs':inputs}
report.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:v for k,v in record.items() if k!='inputs'}))
print(log.read_text(errors='replace')[-12000:])
sys.exit(result.returncode)
