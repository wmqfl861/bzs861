#!/usr/bin/env python3
"""Capture one author command; retain failures, bind source and artifact bytes."""
from pathlib import Path
import datetime, hashlib, json, os, subprocess, sys, time
root=Path(__file__).resolve().parents[4]
output=Path(__file__).resolve().parent
name=sys.argv[1]
argv=sys.argv[2:]
if argv and argv[0]=="--": argv=argv[1:]
record=output/(name+".json")
log=output/(name+".log")
assert not record.exists() and not log.exists(), "run name already used"
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def inputs():
 paths=subprocess.check_output(["git","ls-files","--cached","--others","--exclude-standard"],cwd=root,text=True).splitlines()
 return {p:sha(root/p) for p in paths if not p.startswith("validation/") and (root/p).is_file()}
def artifacts():
 paths=["build/libbzs_core.a","build/bzs_fairness_tests","build/searchd","build/searchctl","build-asan/libbzs_core.a","build-asan/bzs_fairness_tests"]
 return {p:sha(root/p) for p in paths if (root/p).is_file()}
start=time.monotonic(); data={"argv":argv,"cwd":str(root),"utc_started":datetime.datetime.now(datetime.timezone.utc).isoformat(),"inputs":inputs(),"artifacts_before":artifacts(),"runner_sha256":sha(Path(__file__).resolve())}
with log.open("w") as stream:
 proc=subprocess.run(argv,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
data.update(exit_code=proc.returncode,seconds=round(time.monotonic()-start,3),log_sha256=sha(log),artifacts_after=artifacts())
data["inputs_unchanged"]=data["inputs"]==inputs()
record.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
print(json.dumps({"command":name,"exit_code":proc.returncode,"seconds":data["seconds"],"inputs_unchanged":data["inputs_unchanged"]}))
print(log.read_text()[-14000:])
sys.exit(proc.returncode if data["inputs_unchanged"] else 90)
