import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import mcp_server


@pytest.fixture
def server(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"job_id": "job_example", "state": "queued", "items": []}

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls


async def test_batch_mcp_forwards_explicit_limits_and_unicode_urls(server):
    instance, calls = server
    result = await instance.call_tool("read_urls", {"urls": ["https://例子.测试/中文", "https://www.example.com/"],
                                                   "idempotency_key": "batch_1", "timeout_ms": 12000,
                                                   "per_page_timeout_ms": 3000, "max_requests": 7,
                                                   "max_bytes": 4096, "max_attempts": 2})
    assert not result.is_error
    assert calls == [("read_many.submit", {"urls": ["https://xn--fsqu00a.xn--0zwm56d/中文", "https://www.example.com/"],
                                            "idempotency_key": "batch_1", "timeout_ms": 12000,
                                            "per_page_timeout_ms": 3000, "max_requests": 7,
                                            "max_bytes": 4096, "max_attempts": 2})]


async def test_batch_mcp_does_not_replace_zero_budget_with_a_larger_default(server):
    instance, calls = server
    await instance.call_tool("read_urls", {"urls": ["https://example.com/"], "idempotency_key": "batch_1",
                                           "max_requests": 0, "max_bytes": 0})
    assert calls[0][1]["max_requests"] == 0 and calls[0][1]["max_bytes"] == 0
    # The core validates these limits. The adapter must not silently widen them.
    await instance.call_tool("read_urls", {"urls": ["https://example.com/"], "idempotency_key": "batch_2"})
    assert "max_requests" not in calls[1][1] and "max_bytes" not in calls[1][1]
    assert calls[1][1]["max_attempts"] == 1


async def test_job_controls_forward_exactly_once_and_results_mark_untrusted_content(server):
    instance, calls = server
    await instance.call_tool("get_job", {"job_id": "job_a"})
    result = await instance.call_tool("job_results", {"job_id": "job_a", "offset": 4, "limit": 2})
    for action in ("pause", "resume", "cancel", "retry_failed"):
        await instance.call_tool("control_job", {"job_id": "job_a", "action": action})
    assert calls == [("job.get", {"job_id": "job_a"}),
                     ("job.results", {"job_id": "job_a", "offset": 4, "limit": 2}),
                     *[("job.control", {"job_id": "job_a", "action": action})
                       for action in ("pause", "resume", "cancel", "retry_failed")]]
    assert result.structured_content["content_trust"] == "untrusted_web_content"


async def test_job_tools_publish_appropriate_side_effect_annotations_and_required_key(server):
    instance, _ = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    submit = tools["read_urls"]
    assert {"urls", "idempotency_key"} <= set(submit.input_schema["required"])
    assert submit.annotations.read_only_hint is False and submit.annotations.open_world_hint is True
    assert submit.annotations.idempotent_hint is True
    for name in ("get_job", "job_results"):
        assert tools[name].annotations.read_only_hint is True and tools[name].annotations.open_world_hint is False
    assert tools["control_job"].annotations.read_only_hint is False
    assert tools["control_job"].annotations.open_world_hint is True
    assert tools["control_job"].annotations.idempotent_hint is False


async def test_invalid_control_or_missing_idempotency_key_does_not_reach_core(server):
    instance, calls = server
    with pytest.raises(ToolError):
        await instance.call_tool("control_job", {"job_id": "job_a", "action": "restart_and_reset_budget"})
    with pytest.raises(ToolError):
        await instance.call_tool("read_urls", {"urls": ["https://example.com/"]})
    assert calls == []


async def test_core_errors_are_visible_to_mcp_clients(monkeypatch):
    class Client:
        async def call(self, operation, params):
            raise mcp_server.CoreError("E_BUDGET", "job budget exhausted", 409)

    monkeypatch.setattr(mcp_server, "_client", lambda: Client())
    with pytest.raises(ToolError, match="E_BUDGET: job budget exhausted"):
        await mcp_server.build_server().call_tool("control_job", {"job_id": "job_a", "action": "retry_failed"})
