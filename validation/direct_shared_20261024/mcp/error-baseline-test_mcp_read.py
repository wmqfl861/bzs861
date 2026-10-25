from copy import deepcopy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import core_client, mcp_server


@pytest.mark.parametrize(("freshness", "from_cache", "optional"), [
    pytest.param("refresh", False, {
        "usage": {"scope": "direct_read", "external_requests": 2, "content_bytes": 8192,
                  "model_calls": 0, "known": True},
        "shared_read": {"attempt_id": "a" * 48, "role": "payer", "members": 3, "scope": "process",
                        "physical_usage": {"external_requests": 2, "content_bytes": 8192, "known": True}},
        "cache": {"fresh": True, "age_ms": 150, "reason": "explicit_max_age"},
    }, id="shared-payer-known"),
    pytest.param("refresh", False, {
        "usage": {"scope": "direct_read", "external_requests": 0, "content_bytes": 0,
                  "model_calls": 0, "known": True},
        "shared_read": {"attempt_id": "a" * 48, "role": "subscriber", "members": 3, "scope": "process",
                        "physical_usage": {"external_requests": 2, "content_bytes": 8192, "known": True}},
        "cache": {"fresh": False, "age_ms": 0, "reason": "no_cache"},
    }, id="subscriber-keeps-zero-increment-and-physical-association"),
    pytest.param("refresh", False, {
        "usage": {"scope": "direct_read", "external_requests": None, "content_bytes": None,
                  "model_calls": 0, "known": False},
        "shared_read": {"attempt_id": "b" * 48, "role": "payer", "members": 2, "scope": "process",
                        "physical_usage": {"external_requests": None, "content_bytes": None, "known": False}},
    }, id="payer-unknown-is-not-zero"),
    pytest.param("refresh", False, {
        "usage": {"scope": "direct_read", "external_requests": 0, "content_bytes": 0,
                  "model_calls": 0, "known": True},
        "shared_read": {"attempt_id": "b" * 48, "role": "subscriber", "members": 2, "scope": "process",
                        "physical_usage": {"external_requests": None, "content_bytes": None, "known": False}},
    }, id="subscriber-unknown-association-is-not-recomputed"),
    pytest.param("cache_only", True, {
        "usage": {"scope": "direct_read", "external_requests": 0, "content_bytes": 0,
                  "model_calls": 0, "known": True},
        "cache": {"fresh": True, "age_ms": 1234, "reason": "explicit_max_age"},
    }, id="cache-hit-does-not-create-shared-attempt"),
    pytest.param("refresh", False, {}, id="legacy-response-does-not-invent-optional-fields"),
])
async def test_read_url_forwards_core_usage_sharing_and_cache_without_recalculation(
        monkeypatch, freshness, from_cache, optional):
    calls = []
    core_result = {
        "snapshot_id": "snapshot_fixture", "title": "Fixture", "url": "https://example.com/page",
        "final_url": "https://example.com/page", "fetched_at_ms": 1700000000000,
        "from_cache": from_cache,
        "content": {"text": "fixture", "start_byte": 0, "end_byte": 7,
                    "total_bytes": 16, "next_start_byte": 7},
        "metadata": {"fixture": True}, "fetch": {"http_status": 200},
        **deepcopy(optional),
    }
    original = deepcopy(core_result)

    async def call(operation, params):
        calls.append((operation, params))
        return core_result

    monkeypatch.setattr(mcp_server, "_call", call)
    response = await mcp_server.build_server().call_tool("read_url", {
        "url": "https://example.com/page", "max_bytes": 7, "freshness": freshness,
    })

    assert not response.is_error
    assert calls == [("web.read", {"url": "https://example.com/page", "max_bytes": 7,
                                   "start_byte": 0, "freshness": freshness, "max_age_s": 604800})]
    result = response.structured_content
    assert {key: result[key] for key in ("usage", "shared_read", "cache") if key in result} == optional
    assert result["from_cache"] is from_cache
    assert result["snapshot_id"] == "snapshot_fixture" and result["text"] == "fixture"
    assert result["next_start_byte"] == 7
    assert result["content_trust"] == "untrusted_web_content"
    assert core_result == original


def client_with_failure_body(monkeypatch, body):
    response = Mock(status=502)
    response.json = AsyncMock(return_value=body)
    request_context = AsyncMock()
    request_context.__aenter__.return_value = response
    session = Mock()
    session.post.return_value = request_context
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(core_client.aiohttp, "ClientSession", Mock(return_value=session_context))
    client = core_client.CoreClient("http://127.0.0.1:8610", "fixture-token")
    monkeypatch.setattr(mcp_server, "_client", lambda: client)
    return client, session.post


@pytest.mark.parametrize("role", ["payer", "subscriber"])
@pytest.mark.parametrize("known", [True, False])
async def test_core_read_failure_preserves_usage_and_sharing_through_mcp(monkeypatch, role, known):
    physical = {"external_requests": 2 if known else None,
                "content_bytes": 8192 if known else None, "known": known}
    details = {
        "usage": {"scope": "direct_read",
                  "external_requests": 0 if role == "subscriber" else physical["external_requests"],
                  "content_bytes": 0 if role == "subscriber" else physical["content_bytes"],
                  "model_calls": 0, "known": True if role == "subscriber" else known},
        "shared_read": {"attempt_id": "c" * 48, "role": role, "members": 3, "scope": "process",
                        "physical_usage": physical},
    }
    body = {"status": "failed", "request_id": "fixture-request", "api_version": "0.0.1",
            "errors": [{"code": "E_FETCH_NETWORK", "message": "来源请求失败"}],
            "cache": {"must_not_be_error_details": True}, **deepcopy(details)}
    original = deepcopy(body)
    client, post = client_with_failure_body(monkeypatch, body)

    with pytest.raises(core_client.CoreError) as core_failure:
        await client.call("web.read", {"url": "https://example.com/page"})
    assert core_failure.value.http_status == 502
    assert str(core_failure.value) == "E_FETCH_NETWORK: 来源请求失败"
    assert core_failure.value.details == details

    with pytest.raises(ToolError) as tool_failure:
        await mcp_server.build_server().call_tool("read_url", {"url": "https://example.com/page"})
    message, encoded = str(tool_failure.value).split("\n", 1)
    assert message == "E_FETCH_NETWORK: 来源请求失败"
    assert json.loads(encoded) == details
    assert encoded == json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    assert post.call_count == 2  # One Core call and one MCP call; neither retries the failed request.
    assert body == original


@pytest.mark.parametrize("extra", [{}, {"usage": None, "shared_read": []},
                                   {"usage": 0, "shared_read": False}])
async def test_core_read_failure_without_object_details_keeps_original_mcp_error(monkeypatch, extra):
    body = {"status": "failed", "errors": [{"code": "E_FETCH_NETWORK", "message": "origin unavailable"}],
            **extra}
    client, post = client_with_failure_body(monkeypatch, body)
    with pytest.raises(core_client.CoreError) as core_failure:
        await client.call("web.read", {"url": "https://example.com/page"})
    assert core_failure.value.details == {}
    with pytest.raises(ToolError) as tool_failure:
        await mcp_server.build_server().call_tool("read_url", {"url": "https://example.com/page"})
    assert str(tool_failure.value) == "E_FETCH_NETWORK: origin unavailable"
    assert post.call_count == 2
