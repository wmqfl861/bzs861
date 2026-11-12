"""CL-04: strict argument checking is derived from tool signatures and covers every tool.

The SDK pre-parses JSON inside strings (so the literal "null" silently omits an
optional narrowing), coerces booleans to integers and discards unknown fields.
The sweep proves every tool in list_tools() refuses those wire shapes before
anything is forwarded to the core, and that the advertised schemas carry no
null wire type.
"""
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import mcp_server

# Minimal well-formed arguments per tool (admin tool included); a new tool must
# be added here, which is exactly what keeps the sweep complete.
MINIMAL_ARGUMENTS = {
    "list_search_modes": {},
    "web_search": {"query": "alpha"},
    "start_web_search": {"query": "alpha", "idempotency_key": "sweep-start"},
    "get_search_session": {"session_id": "s"},
    "search_session_events": {"session_id": "s"},
    "search_session_page": {"session_id": "s", "result_set_version": "v1"},
    "cancel_search_session": {"session_id": "s"},
    "prepare_read_url": {},
    "cancel_read_url": {"read_id": "r"},
    "read_url": {"url": "https://example.com/"},
    "read_urls": {"urls": ["https://example.com/"], "idempotency_key": "sweep-read"},
    "list_sites": {},
    "map_site": {"site_id": "docs", "seeds": ["https://example.com/docs/"], "idempotency_key": "sweep-map"},
    "crawl_site": {"site_id": "docs", "seeds": ["https://example.com/docs/"], "idempotency_key": "sweep-crawl"},
    "crawl_edges": {"job_id": "j"},
    "get_job": {"job_id": "j"},
    "job_results": {"job_id": "j"},
    "compact_job_history": {},
    "control_job": {"job_id": "j", "action": "pause"},
    "read_range": {"snapshot_id": "snap"},
    "find_in_page": {"snapshot_id": "snap", "text": "alpha"},
    "search_local": {"query": "alpha"},
    "cite_evidence": {"snapshot_id": "snap", "start_byte": 0, "end_byte": 4},
    "list_sources": {},
    "revoke_principal": {"subject": "bob"},
}

# Wire values that must never satisfy each advertised schema type.
WRONG_VALUES = {
    "integer": ["5", True, 2.5, None],
    "boolean": ["true", 1, None],
    "string": [5, True, ["x"], None],
    "array": ["x", 5, [1], [None], None],
}


@pytest.fixture
def server(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"job_id": "fixture", "state": "queued", "items": []}

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(admin_tools=True), calls


def _schema_types(field):
    if "type" in field:
        return [field["type"]]
    return [item.get("type") for item in field.get("anyOf", [])]


async def test_listed_schemas_advertise_no_null_and_no_extra_fields(server):
    instance, _ = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    assert set(tools) == set(MINIMAL_ARGUMENTS), "a tool was added without minimal sweep arguments"
    for tool in tools.values():
        schema = tool.input_schema
        assert schema.get("additionalProperties") is False, tool.name
        for param, field in schema.get("properties", {}).items():
            assert "null" not in _schema_types(field), (tool.name, param)
        if "scope_path" in schema.get("properties", {}):
            assert schema["properties"]["scope_path"].get("pattern") == "^/", tool.name


async def test_well_formed_arguments_still_reach_the_core(server):
    instance, calls = server
    for name, arguments in MINIMAL_ARGUMENTS.items():
        result = await instance.call_tool(name, dict(arguments))
        assert not result.is_error, name
    assert len(calls) == len(MINIMAL_ARGUMENTS)


async def test_every_optional_argument_refuses_null_string_wrong_types_and_explicit_null(server):
    instance, calls = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    checked = 0
    for name, tool in tools.items():
        schema = tool.input_schema
        required = set(schema.get("required", []))
        for param, field in schema.get("properties", {}).items():
            if param in required:
                continue
            kinds = _schema_types(field)
            assert len(kinds) == 1 and kinds[0] in WRONG_VALUES, (name, param, kinds)
            # The literal "null" string is only a coercion hazard where omitting
            # is possible without a declared default (str | None and friends);
            # a str parameter with a real default legitimately accepts the
            # string itself and the core validates the value.
            attempts = []
            if kinds[0] != "string" or "default" not in field:
                attempts.append(("null-string", param, "null"))
            attempts += [("wrong-type", param, wrong) for wrong in WRONG_VALUES[kinds[0]]]
            for label, param_name, value in attempts:
                with pytest.raises(ToolError, match="E_SCHEMA") as exc_info:
                    await instance.call_tool(name, {**MINIMAL_ARGUMENTS[name], param_name: value})
                assert calls == [], (name, param, label, exc_info.value)
                checked += 1
    assert checked > 100, "the sweep must actually cover the tool surface"


async def test_unknown_fields_are_refused_for_every_tool(server):
    instance, calls = server
    tools = {tool.name for tool in await instance.list_tools()}
    for name in tools:
        with pytest.raises(ToolError, match="E_SCHEMA"):
            await instance.call_tool(name, {**MINIMAL_ARGUMENTS[name], "unexpected_field": 1})
        assert calls == [], name
