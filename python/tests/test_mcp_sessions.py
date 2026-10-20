import pytest

from bzs import mcp_server

ToolError = mcp_server.ToolError


@pytest.fixture
def server(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"session_id": "session_fixture", "events": [], "hits": []}

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls


async def test_durable_search_forwards_budget_and_identity_without_widening(server):
    instance, calls = server
    await instance.call_tool("start_web_search", {"query": "中文", "idempotency_key": "query_1", "sources": [],
                                                 "limit": 0, "per_source": 3, "timeout_ms": 1000, "deadline_ms": 3000})
    assert calls == [("web.search.start", {"query": "中文", "idempotency_key": "query_1", "sources": [],
                                         "limit": 0, "per_source": 3, "timeout_ms": 1000, "deadline_ms": 3000})]


async def test_reconnect_cancel_and_version_bound_paging_map_once(server):
    instance, calls = server
    await instance.call_tool("get_search_session", {"session_id": "s1"})
    await instance.call_tool("search_session_events", {"session_id": "s1", "after_event_id": 14, "limit": 7})
    result = await instance.call_tool("search_session_page", {"session_id": "s1", "result_set_version": "v1",
                                                            "cursor": "cursor1", "page_size": 2})
    await instance.call_tool("cancel_search_session", {"session_id": "s1"})
    assert calls == [("session.get", {"session_id": "s1"}),
                     ("session.events", {"session_id": "s1", "after_event_id": 14, "limit": 7}),
                     ("search.page", {"session_id": "s1", "result_set_version": "v1", "cursor": "cursor1", "page_size": 2}),
                     ("session.cancel", {"session_id": "s1"})]
    assert result.structured_content["content_trust"] == "untrusted_search_content"


async def test_search_session_annotations_and_required_identities(server):
    instance, _ = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    assert {"query", "idempotency_key"} <= set(tools["start_web_search"].input_schema["required"])
    assert tools["start_web_search"].annotations.idempotent_hint is True
    assert tools["start_web_search"].annotations.open_world_hint is True
    assert {"session_id", "result_set_version"} <= set(tools["search_session_page"].input_schema["required"])
    for name in ("get_search_session", "search_session_events", "search_session_page"):
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.open_world_hint is False
    assert tools["cancel_search_session"].annotations.read_only_hint is False
    assert tools["cancel_search_session"].annotations.idempotent_hint is True
    for name in ("start_web_search", "get_search_session", "search_session_events", "search_session_page", "cancel_search_session"):
        assert tools[name].input_schema["additionalProperties"] is False
        for field in tools[name].input_schema["properties"].values():
            assert all(item.get("type") != "null" for item in field.get("anyOf", []))


@pytest.mark.parametrize("changed", [
    {"sources": value} for value in (None, "null", '["fixture"]', "[]", True, [1], [None])
] + [{key: value} for key in ("limit", "per_source", "timeout_ms", "deadline_ms")
     for value in (True, "1000", 1.0, None)] + [{"extra": 1}, {"language": "null"}, {"language": None}])
async def test_raw_search_shape_never_widens_source_selection_or_budget(server, changed):
    instance, calls = server
    with pytest.raises(ToolError, match="E_SCHEMA"):
        await instance.call_tool("start_web_search", {"query": "fixture", "idempotency_key": "fixture", **changed})
    assert calls == []


@pytest.mark.parametrize("name,args", [
    ("get_search_session", {"session_id": "s", "extra": 1}),
    ("cancel_search_session", {"session_id": 1}),
    ("search_session_events", {"session_id": "s", "after_event_id": True}),
    ("search_session_events", {"session_id": "s", "limit": "32"}),
    ("search_session_page", {"session_id": "s", "result_set_version": "v", "cursor": "null"}),
    ("search_session_page", {"session_id": "s", "result_set_version": "v", "cursor": None}),
    ("search_session_page", {"session_id": "s", "result_set_version": "v", "page_size": False}),
])
async def test_raw_reconnect_controls_reject_coercion_before_core(server, name, args):
    instance, calls = server
    with pytest.raises(ToolError, match="E_SCHEMA"):
        await instance.call_tool(name, args)
    assert calls == []
