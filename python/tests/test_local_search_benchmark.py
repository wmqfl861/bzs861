import json, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bzs import local_benchmark as bench

class Response:
    status=200
    def read(self, limit=-1): return json.dumps({"status":"ok","result":{"secret_body":"not recorded"}}).encode()[:limit]
class Connection:
    calls=[]
    def __init__(self,host,port,timeout): assert host=="127.0.0.1"; self.closed=False; Connection.calls.append(self)
    def request(self,method,path,body,headers):
        assert method=="POST" and path=="/api/v0/call"; assert headers["Authorization"]=="Bearer "+"t"*40
        payload=json.loads(body); assert payload["operation"]=="search" and "query" in payload["params"]
    def getresponse(self): return Response()
    def close(self): self.closed=True

def test_benchmark_is_loopback_only_and_never_records_credentials_or_body():
    Connection.calls.clear()
    result=bench.benchmark("中文 query",port=8610,iterations=5,warmup=2,token="t"*40,connection_factory=Connection)
    assert len(Connection.calls)==7 and all(x.closed for x in Connection.calls)
    assert result["network_scope"]=="loopback_only" and result["operation"]=="search"
    assert result["latency_ms"]["p95"]>=result["latency_ms"]["min"]
    assert result["response_bodies_recorded"] is False and result["credentials_recorded"] is False and result["cache_hit_claimed"] is False
    assert "secret_body" not in json.dumps(result) and "tttttt" not in json.dumps(result)

@pytest.mark.parametrize("kwargs",[{"port":0},{"port":65536},{"iterations":0},{"iterations":1001},{"warmup":101},{"timeout":0.01},{"timeout":float("nan")}])
def test_benchmark_rejects_invalid_or_unbounded_arguments(kwargs):
    with pytest.raises(ValueError): bench.benchmark("local query",token="t"*40,**kwargs)

def test_benchmark_rejects_invalid_token():
    with pytest.raises(ValueError,match="SEARCH_API_TOKEN"): bench.benchmark("local query",token="short")
