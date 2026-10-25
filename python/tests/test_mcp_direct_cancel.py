"""The explicit read handle remains owned and interpreted by the core."""
from copy import deepcopy
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bzs import mcp_server
from tests.test_mcp_read import client_with_failure_body


@pytest.mark.parametrize(("name", "arguments", "operation", "core_args", "result"), [
    ("prepare_read_url", {}, "web.read.prepare", {},
     {"read_id": "a" * 48, "scope": "process", "expires_in_ms": 120000}),
    ("cancel_read_url", {"read_id": "a" * 48}, "web.read.cancel", {"read_id": "a" * 48},
     {"cancel_requested": True, "state": "cancelled", "physical_cancel_requested": False}),
])
async def test_prepare_and_cancel_forward_exact_core_objects(monkeypatch, name, arguments, operation, core_args, result):
    calls = []
    original = deepcopy(result)

    async def call(op, args):
        calls.append((op, args))
        return result

    monkeypatch.setattr(mcp_server, "_call", call)
    response = await mcp_server.build_server().call_tool(name, arguments)
    assert not response.is_error and response.structured_content == original
    assert calls == [(operation, core_args)] and result == original


@pytest.mark.parametrize("read_id", [None, "", "a" * 48])
async def test_read_url_forwards_supplied_handle_without_silent_downgrade(monkeypatch, read_id):
    calls = []

    async def call(op, args):
        calls.append((op, args))
        return {"content": {"text": "done"}}

    monkeypatch.setattr(mcp_server, "_call", call)
    response = await mcp_server.build_server().call_tool("read_url", {"url": "https://example.com/page", "read_id": read_id})
    expected = {"url": "https://example.com/page", "max_bytes": 20000,
                "start_byte": 0, "freshness": "cache_first", "max_age_s": 604800}
    if read_id is not None:
        expected["read_id"] = read_id
    assert calls == [("web.read", expected)]
    assert not response.is_error and response.structured_content["text"] == "done"


async def test_invalid_explicit_handle_keeps_core_error_and_does_not_retry_legacy(monkeypatch):
    body = {"status": "failed", "errors": [{"code": "E_SCHEMA", "message": "invalid read_id"}]}
    _, post = client_with_failure_body(monkeypatch, body)
    with pytest.raises(ToolError, match="E_SCHEMA"):
        await mcp_server.build_server().call_tool("read_url", {"url": "https://example.com/page", "read_id": ""})
    assert post.call_count == 1
    assert json.loads(post.call_args.kwargs["data"])["params"]["read_id"] == ""


async def test_control_tools_are_local_and_prepare_is_not_idempotent():
    tools = {tool.name: tool for tool in await mcp_server.build_server().list_tools()}
    prepare, cancel = tools["prepare_read_url"], tools["cancel_read_url"]
    assert prepare.annotations.open_world_hint is False and cancel.annotations.open_world_hint is False
    assert prepare.annotations.read_only_hint is False and cancel.annotations.read_only_hint is False
    assert prepare.annotations.idempotent_hint is False and cancel.annotations.idempotent_hint is True
    assert "read_id" in tools["read_url"].input_schema["properties"]
    assert "read_id" not in tools["read_url"].input_schema.get("required", [])


async def test_cancelled_read_error_keeps_unknown_physical_usage_and_no_retry(monkeypatch):
    details = {
        "usage": {"scope": "direct_read", "external_requests": 0, "content_bytes": 0,
                  "model_calls": 0, "known": True},
        "shared_read": {"attempt_id": "a" * 48, "role": "subscriber", "members": 2, "scope": "process",
                        "physical_usage": {"external_requests": None, "content_bytes": None, "known": False}},
    }
    body = {"status": "failed", "errors": [{"code": "E_CANCELLED", "message": "read member cancelled"}], **details}
    _, post = client_with_failure_body(monkeypatch, body)
    with pytest.raises(ToolError, match="E_CANCELLED") as failed:
        await mcp_server.build_server().call_tool("read_url", {"url": "https://example.com/page", "read_id": "a" * 48})
    assert '"external_requests":null' in str(failed.value)
    assert post.call_count == 1
