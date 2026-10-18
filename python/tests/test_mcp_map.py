import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import mcp_server


@pytest.fixture
def server(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"job_id": "map_fixture", "state": "queued", "items": []}

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls


async def test_map_adapter_preserves_explicit_zero_limits_and_narrow_scope(server):
    instance, calls = server
    result = await instance.call_tool("map_site", {
        "site_id": "fixture", "seeds": ["https://例子.测试/地图.xml"], "idempotency_key": "map_once",
        "scope_path": "/地图/", "max_urls": 0, "max_depth": 0, "timeout_ms": 1000,
        "per_request_timeout_ms": 1000, "max_requests": 0, "max_bytes": 0})
    assert not result.is_error
    assert calls == [("map.submit", {"site_id": "fixture", "seeds": ["https://xn--fsqu00a.xn--0zwm56d/地图.xml"],
        "idempotency_key": "map_once", "scope_path": "/地图/", "max_urls": 0,
        "max_depth": 0, "timeout_ms": 1000,
        "per_request_timeout_ms": 1000, "max_requests": 0, "max_bytes": 0})]


async def test_map_adapter_leaves_defaults_and_site_authorization_in_core(server):
    instance, calls = server
    await instance.call_tool("list_sites", {})
    await instance.call_tool("map_site", {"site_id": "fixture", "seeds": ["https://example.com/sitemap.xml"],
                                           "idempotency_key": "map_default"})
    assert calls == [("map.sites", {}), ("map.submit", {"site_id": "fixture",
        "seeds": ["https://example.com/sitemap.xml"], "idempotency_key": "map_default"})]
    with pytest.raises(ToolError):
        await instance.call_tool("map_site", {"site_id": "fixture", "seeds": ["https://example.com/"]})
    assert len(calls) == 2


async def test_map_mcp_schema_exposes_no_approval_token_or_clock_override(server):
    instance, _ = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    tool = tools["map_site"]
    assert set(tool.input_schema["required"]) == {"site_id", "seeds", "idempotency_key"}
    assert not {"approved", "admission_token", "now_ms", "site_manifest_sha256"} & set(tool.input_schema["properties"])
    assert tool.annotations.read_only_hint is False and tool.annotations.open_world_hint is True
    assert tool.annotations.idempotent_hint is True
    assert tools["list_sites"].annotations.read_only_hint is True
    assert tools["list_sites"].annotations.open_world_hint is False


async def test_map_core_review_failure_is_not_reported_as_an_empty_success(monkeypatch):
    class Client:
        async def call(self, operation, params):
            raise mcp_server.CoreError("E_SITE_REVIEW", "site review expired", 409)

    monkeypatch.setattr(mcp_server, "_client", lambda: Client())
    with pytest.raises(ToolError, match="E_SITE_REVIEW: site review expired"):
        await mcp_server.build_server().call_tool("map_site", {
            "site_id": "fixture", "seeds": ["https://example.com/sitemap.xml"], "idempotency_key": "expired"})
