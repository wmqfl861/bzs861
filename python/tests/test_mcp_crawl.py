"""The MCP crawler exposes the native operation without widening its grants."""
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import mcp_server


@pytest.fixture
def server(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"job_id": "crawl_fixture", "state": "queued", "items": []}

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls


async def test_crawl_adapter_preserves_explicit_limits_for_core_validation(server):
    instance, calls = server
    result = await instance.call_tool("crawl_site", {
        "site_id": "fixture", "seeds": ["https://例子.测试/正文"], "idempotency_key": "crawl_once",
        "scope_path": "/正文/", "max_pages": 0, "max_urls": 0, "max_depth": 0,
        "timeout_ms": 0, "per_request_timeout_ms": 0, "max_requests": 0, "max_bytes": 0})
    assert not result.is_error
    assert calls == [("crawl.submit", {
        "site_id": "fixture", "seeds": ["https://xn--fsqu00a.xn--0zwm56d/正文"],
        "idempotency_key": "crawl_once", "scope_path": "/正文/", "max_pages": 0, "max_urls": 0,
        "max_depth": 0, "timeout_ms": 0, "per_request_timeout_ms": 0, "max_requests": 0, "max_bytes": 0})]


async def test_crawl_adapter_omits_defaults_and_cannot_inject_site_approval(server):
    instance, calls = server
    parameters = {"site_id": "fixture", "seeds": ["https://example.com/docs/"],
                  "idempotency_key": "crawl_default"}
    await instance.call_tool("crawl_site", parameters)
    assert calls == [("crawl.submit", parameters)]
    tools = {tool.name: tool for tool in await instance.list_tools()}
    assert not {"approved", "robots_record", "sites_manifest_sha256", "max_attempts", "now_ms"} & set(
        tools["crawl_site"].input_schema["properties"])
    assert tools["crawl_site"].input_schema["additionalProperties"] is False
    assert tools["crawl_site"].input_schema["properties"]["max_pages"]["type"] == "integer"


@pytest.mark.parametrize("override", [
    {"max_pages": value} for value in (None, True, False, 2.0, "2", "null", "true")
] + [{"seeds": value} for value in ('["https://example.com/"]', [1], [None])]
  + [{"scope_path": value} for value in ("null", "true", '"/docs/"', "docs/", "")]
  + [{"approved": True}, {"robots": {}}, {"sites_manifest_sha256": "anything"}, {"max_attempts": 2}])
async def test_crawl_rejects_raw_types_and_unknown_fields_before_sdk_coercion(server, override):
    instance, calls = server
    with pytest.raises(ToolError, match="E_SCHEMA"):
        await instance.call_tool("crawl_site", {"site_id": "fixture", "seeds": ["https://example.com/"],
            "idempotency_key": "strict-crawl", **override})
    assert calls == []


@pytest.mark.parametrize("override", [{"offset": None}, {"offset": True}, {"limit": "20"}, {"extra": 1}])
async def test_crawl_edge_paging_rejects_unexpected_raw_arguments(server, override):
    instance, calls = server
    with pytest.raises(ToolError, match="E_SCHEMA"):
        await instance.call_tool("crawl_edges", {"job_id": "fixture", **override})
    assert calls == []


async def test_crawl_edges_keeps_paging_and_untrusted_annotation(server):
    instance, calls = server
    result = await instance.call_tool("crawl_edges", {"job_id": "crawl_fixture", "offset": 7, "limit": 2})
    assert not result.is_error
    assert calls == [("crawl.edges", {"job_id": "crawl_fixture", "offset": 7, "limit": 2})]
    assert result.structured_content["content_trust"] == "untrusted_web_content"
    assert "untrusted" in result.structured_content["notice"]


async def test_crawl_tools_are_described_as_persistent_network_work(server):
    instance, _ = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    assert not tools["crawl_site"].annotations.read_only_hint
    assert tools["crawl_site"].annotations.open_world_hint
    assert tools["crawl_site"].annotations.idempotent_hint
    assert tools["crawl_edges"].annotations.read_only_hint
    assert not tools["crawl_edges"].annotations.open_world_hint
    assert "max_pages" in tools["crawl_site"].input_schema["properties"]


async def test_core_crawl_refusal_remains_a_tool_error(monkeypatch):
    class RefusingCore:
        async def call(self, operation, params):
            assert operation == "crawl.submit"
            raise mcp_server.CoreError("E_SITE_REVIEW", "Site is not approved", 403)

    monkeypatch.setattr(mcp_server, "_client", lambda: RefusingCore())
    instance = mcp_server.build_server()
    with pytest.raises(ToolError, match="E_SITE_REVIEW"):
        await instance.call_tool("crawl_site", {"site_id": "unapproved",
            "seeds": ["https://example.com/"], "idempotency_key": "blocked"})
