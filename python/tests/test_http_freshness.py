"""Raw cache policy is transported even when no conditional validator exists."""
import pytest

from bzs.worker.fetch import FetchResult
from bzs.worker.freshness import response_freshness
from bzs.worker.server import Worker
from tests.test_http_revalidation import conditional_worker, read_grant


@pytest.mark.parametrize("header,value", [("Cache-Control", "no-store"), ("Cache-Control", "no-cache"),
                                          ("Cache-Control", "max-age=0"), ("Vary", "*"), ("Age", "999")])
async def test_no_validator_still_carries_actual_cache_policy(conditional_worker, header, value):
    worker, server, calls, state = conditional_worker
    state["page"]["headers"] = [(header, value)]
    result = await worker.fetch_extract(read_grant(server))
    assert result["status"] == "ok" and "http_cache" not in result
    assert result["http_freshness"][header.lower().replace("-", "_")] == value
    assert len(calls) == 1


@pytest.mark.parametrize("headers", [{"cache-control": "x" * 4097}, {"cache-control": ""},
                                     {"age": "\n0"}, {"vary": "é"}])
def test_unrepresentable_fields_carry_invalid_marker(headers):
    response = FetchResult("ok", "https://cache.test/", final_url="https://cache.test/", http_status=200,
                           headers=headers, request_profile="a" * 64)
    assert response_freshness(response)["invalid"] is True


async def test_profile_rpc_is_strict_and_never_dispatches(conditional_worker):
    worker, _, calls, _ = conditional_worker
    before = await worker.fetch_profile({})
    assert not calls and len(before["request_profile"]) == 64
    worker.fetcher.accept_language = "fr"
    after = await worker.fetch_profile({})
    assert after["request_profile"] != before["request_profile"] and not calls
    with pytest.raises(ValueError):
        await worker.fetch_profile({"url": "https://not-requested.test/"})
