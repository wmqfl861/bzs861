"""Real loopback RPC tests: rejected input must never dispatch business work."""
import json
import pytest
import pytest_asyncio
from aiohttp import ClientSession, web
from bzs.worker.server import make_app

TOKEN = "r" * 64

class WorkerStub:
    def __init__(self):
        self.calls = []
        self.cancelled = set()
    async def start(self): pass
    async def close(self): pass
    async def health(self, params):
        self.calls.append(("health", params))
        if params.get("bad_result") == "nan":
            return {"value": float("nan")}
        if params.get("bad_result") == "object":
            return {"value": object()}
        if params.get("bad_result") == "surrogate":
            return {"value": "\ud800"}
        return {"ok": True}
    async def fetch_extract(self, params):
        self.calls.append(("fetch", params))
        return {"status": "ok"}
    async def fetch_cancel(self, params):
        self.calls.append(("cancel", params))
        self.cancelled.add(params.get("attempt_id"))
        return {"cancelled": True}
    async def search_multi(self, params):
        raise KeyError("private-worker-token")
    map_fetch = fetch_extract
    fetch_profile = health
    fetch_extract_direct = fetch_extract
    fetch_cancel_direct = fetch_cancel

@pytest_asyncio.fixture
async def rpc():
    worker = WorkerStub()
    runner = web.AppRunner(make_app(worker, TOKEN), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/rpc"
    async with ClientSession(trust_env=False) as client:
        async def post(body, **headers):
            h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
            h.update(headers)
            async with client.post(url, data=body, headers=h) as response:
                return response.status, await response.json()
        yield worker, post
    await runner.cleanup()

INVALID = [
    b'{"method":"health","method":"fetch.extract"}',
    b'{"method":"health","m\\u0065thod":"fetch.extract"}',
    b'{"method":"fetch.cancel","params":{"attempt_id":"one","attempt_id":"two"}}',
    b'{"method":"fetch.extract","params":{"nested":{"x":1,"x":2}}}',
    b'{"method":"fetch.extract","params":null}',
    b'{"method":"fetch.extract","params":false}',
    b'{"method":"fetch.extract","params":0}',
    b'{"method":"fetch.extract","params":[]}',
    b'{"method":"fetch.extract","params":""}',
    b'{"method":false}', b'{"method":[]}', b'{}', b'[]', b'null', b'"health"', b'1',
    b'{"method":"health","unexpected":1}',
    b'{"method":"fetch.extract","params":{"x":NaN}}',
    b'{"method":"fetch.extract","params":{"x":Infinity}}',
    b'{"method":"fetch.extract","params":{"x":1e9999}}',
    b'{"method":"fetch.extract","params":{"x":9223372036854775808}}',
    b'{"method":"fetch.extract","params":{"x":-9223372036854775809}}',
    b'{"method":"fetch.extract","params":{"x":"\\ud800"}}',
    b'{"method":"fetch.extract","params":{"\\udfff":0}}',
    b'{"method":"fetch.extract","params":{"x":"\xff"}}',
    b'{"method":"fetch.extract","params":{"x":' + b'['*65 + b'0' + b']'*65 + b'}}',
    b'{"method":"fetch.extract","params":{"x":[' + b'0,'*20000 + b'0]}}',
]

# Short ids keep PYTEST_CURRENT_TEST within the 32767-character Windows
# environment-variable cap; the bodies themselves stay byte-exact.
INVALID_IDS = [
    "dup-method", "dup-method-escaped", "dup-cancel-id", "dup-param-key",
    "params-null", "params-false", "params-zero", "params-array", "params-string",
    "method-false", "method-array", "empty-object", "top-array", "top-null",
    "top-string", "top-number", "unexpected-field", "nan", "infinity",
    "float-overflow", "int-overflow", "int-underflow", "lone-surrogate",
    "surrogate-key", "invalid-utf8", "depth-65", "nodes-20001",
]

@pytest.mark.parametrize("body", INVALID, ids=INVALID_IDS)
async def test_rejected_envelope_has_no_dispatch_or_cancel_effects(rpc, body):
    worker, post = rpc
    status, reply = await post(body)
    assert status == 400
    assert reply["error"]["code"] == "E_PARAMS"
    assert worker.calls == []
    assert worker.cancelled == set()

@pytest.mark.parametrize("params", [{}, {"x":False}, {"x":0}, {"x":None},
    {"lo":-(2**63), "hi":2**63-1, "float":0.5}, {"text":"中文 café 😀 { [ \\\""}])
async def test_valid_params_and_missing_params_preserved(rpc, params):
    worker, post = rpc
    status, reply = await post(json.dumps({"method":"fetch.extract", "params":params}).encode())
    assert status == 200 and reply["result"]["status"] == "ok"
    assert worker.calls == [("fetch", params)]
    status, _ = await post(b'{"method":"health"}')
    assert status == 200 and worker.calls[-1] == ("health", {})

async def test_internal_keyerror_not_unknown_method_or_secret_leak(rpc):
    worker, post = rpc
    status, reply = await post(b'{"method":"search.multi"}')
    assert reply["error"]["code"] == "E_INTERNAL"
    assert "private-worker-token" not in json.dumps(reply)
    status, reply = await post(b'{"method":"not.a.method"}')
    assert reply["error"]["code"] == "E_METHOD"
    assert not worker.calls

@pytest.mark.parametrize("headers,expected", [({"Authorization":"Bearer wrong"},401), ({"Origin":"https://browser.invalid"},403)])
async def test_authentication_and_origin_precede_json_decoding(rpc, headers, expected):
    worker, post = rpc
    status, _ = await post(b'{bad json', **headers)
    assert status == expected and not worker.calls

async def test_request_limit_and_recovery(rpc):
    worker, post = rpc
    status, reply = await post(b' '*(1024*1024+1))
    assert status == 413 and reply["error"]["code"] == "E_REQUEST_TOO_LARGE"
    assert not worker.calls
    status, reply = await post(b'{"method":"health"}')
    assert status == 200 and reply["result"]["ok"]

async def test_valid_cancel_dispatches_once(rpc):
    worker, post = rpc
    status, reply = await post(b'{"method":"fetch.cancel","params":{"attempt_id":"one"}}')
    assert status == 200 and reply["result"]["cancelled"]
    assert worker.calls == [("cancel", {"attempt_id":"one"})]
    assert worker.cancelled == {"one"}

@pytest.mark.parametrize("kind", ["nan", "object", "surrogate"])
async def test_invalid_worker_results_are_internal_errors_not_bad_client_params(rpc, kind):
    worker, post = rpc
    status, reply = await post(json.dumps({"method":"health", "params":{"bad_result":kind}}).encode())
    assert status == 200 and reply["error"]["code"] == "E_INTERNAL"
    assert worker.calls == [("health", {"bad_result":kind})]

async def test_exact_existing_transport_limit_is_not_reduced(rpc):
    worker, post = rpc
    payload = b'{"method":"health"}'
    status, reply = await post(payload + b' ' * (1024 * 1024 - len(payload)))
    assert status == 200 and reply["result"]["ok"]
    assert worker.calls == [("health", {})]


async def test_internal_failure_is_diagnosed_on_stderr_not_in_response(rpc, caplog):
    import logging
    worker, post = rpc
    with caplog.at_level(logging.ERROR, logger="bzs.worker"):
        status, reply = await post(json.dumps({"method": "search.multi", "params": {}}).encode())
    assert status == 200 and reply["error"]["code"] == "E_INTERNAL"
    assert "private-worker-token" not in json.dumps(reply)
    records = [r for r in caplog.records if r.name == "bzs.worker"]
    assert records and "rpc internal failure" in records[0].getMessage()
    assert "search.multi" in records[0].getMessage()
    assert records[0].exc_info and "private-worker-token" in "".join(
        logging.Formatter().formatException(records[0].exc_info))


def test_worker_logging_goes_to_stderr_only(capsys):
    from bzs.worker import server
    server.configure_stderr_logging()
    try:
        try:
            raise RuntimeError("diagnostic boom")
        except RuntimeError:
            server._log.exception("simulated failure (method=%r)", "fetch.extract")
    finally:
        server._log.handlers.clear()  # keep the module logger unconfigured
    captured = capsys.readouterr()
    assert "simulated failure" in captured.err and "fetch.extract" in captured.err
    assert "Traceback" in captured.err and "diagnostic boom" in captured.err
    assert captured.out == ""  # stdout stays reserved for the protocol
