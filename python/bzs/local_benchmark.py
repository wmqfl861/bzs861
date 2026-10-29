"""Reproducible loopback-only benchmark for the native local-search API."""
from __future__ import annotations
import argparse, hashlib, http.client, json, math, os, platform, statistics, time

def benchmark(query, *, port=8610, iterations=20, warmup=3, timeout=10.0, token=None, connection_factory=http.client.HTTPConnection):
    if type(port) is not int or not 1 <= port <= 65535: raise ValueError("port must be in 1..65535")
    if type(iterations) is not int or not 1 <= iterations <= 1000: raise ValueError("iterations must be in 1..1000")
    if type(warmup) is not int or not 0 <= warmup <= 100: raise ValueError("warmup must be in 0..100")
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > 4096: raise ValueError("query must be nonempty and <=4096 UTF-8 bytes")
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 60: raise ValueError("timeout must be in 0.1..60 seconds")
    token = os.environ.get("SEARCH_API_TOKEN", "") if token is None else token
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in token): raise ValueError("SEARCH_API_TOKEN must be 32..512 printable ASCII characters")
    payload = json.dumps({"operation":"search","params":{"query":query}}, ensure_ascii=False, separators=(",",":"), allow_nan=False).encode("utf-8")
    samples, statuses = [], []
    def once():
        conn = connection_factory("127.0.0.1", port, timeout=timeout)
        start = time.perf_counter()
        try:
            conn.request("POST", "/api/v0/call", body=payload, headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Host":"127.0.0.1:"+str(port),"Connection":"close"})
            response = conn.getresponse()
            status = int(response.status)
            raw = response.read(4*1024*1024+1)
            if len(raw) > 4*1024*1024: raise RuntimeError("local API response exceeded 4 MiB")
            if status >= 400: raise RuntimeError("local search API returned HTTP "+str(status))
            try: envelope = json.loads(raw.decode("utf-8","strict"))
            except (UnicodeError, json.JSONDecodeError) as exc: raise RuntimeError("local search API returned invalid JSON") from exc
            if not isinstance(envelope,dict) or envelope.get("status") not in ("ok","partial","empty"): raise RuntimeError("local search API returned a non-success envelope")
            return (time.perf_counter()-start)*1000, status
        finally: conn.close()
    for _ in range(warmup): once()
    total_start = time.monotonic()
    for _ in range(iterations):
        elapsed,status=once(); samples.append(elapsed); statuses.append(status)
    ordered=sorted(samples)
    p95=ordered[max(0,min(len(ordered)-1,math.ceil(0.95*len(ordered))-1))]
    return {"schema_version":1,"operation":"search","network_scope":"loopback_only","query_sha256":hashlib.sha256(query.encode("utf-8")).hexdigest(),"query_bytes":len(query.encode("utf-8")),"iterations":iterations,"warmup":warmup,"statuses":statuses,"latency_ms":{"min":round(min(samples),3),"median":round(statistics.median(samples),3),"p95":round(p95,3),"max":round(max(samples),3),"first_measured":round(samples[0],3),"subsequent_median":round(statistics.median(samples[1:]),3) if len(samples)>1 else None},"elapsed_total_ms":round((time.monotonic()-total_start)*1000,3),"environment":{"python":platform.python_version(),"platform":platform.system()},"response_bodies_recorded":False,"credentials_recorded":False,"cache_hit_claimed":False}

def main(argv=None):
    parser=argparse.ArgumentParser(description="Benchmark local searchd only; does not call external sources.")
    parser.add_argument("--query", required=True); parser.add_argument("--port",type=int,default=8610); parser.add_argument("--iterations",type=int,default=20); parser.add_argument("--warmup",type=int,default=3); parser.add_argument("--timeout",type=float,default=10.0)
    args=parser.parse_args(argv)
    try: result=benchmark(args.query,port=args.port,iterations=args.iterations,warmup=args.warmup,timeout=args.timeout)
    except (OSError,RuntimeError,ValueError) as exc: parser.exit(2,"E_BENCHMARK: "+str(exc)+"\n")
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
