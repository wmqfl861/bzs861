"""CL-04: strict argument checking is derived from tool signatures and covers every tool.

The SDK pre-parses JSON inside strings (so the literal "null" silently omits an
optional narrowing), coerces booleans to integers and discards unknown fields.
The sweep proves every tool in list_tools() refuses those wire shapes before
anything is forwarded to the core, and that the advertised schemas carry no
null wire type.
"""
import inspect
import json

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


async def test_list_arguments_accept_json_array_strings_and_forward_decoded(server):
    # CL-04r: some desktop clients send JSON arrays as strings and the SDK
    # would decode them. A string decoding to a list of strings is accepted
    # and forwarded as the decoded list; malformed strings stay refused.
    instance, calls = server
    tools = {tool.name: tool for tool in await instance.list_tools()}
    checked = 0
    for name, tool in tools.items():
        schema = tool.input_schema
        for param, field in schema.get("properties", {}).items():
            if _schema_types(field) != ["array"]:
                continue
            assert field["items"]["type"] == "string", (name, param)
            for decoded in (MINIMAL_ARGUMENTS[name].get(param, ["fixture"]), []):
                encoded = json.dumps(decoded)
                before = len(calls)
                result = await instance.call_tool(name, {**MINIMAL_ARGUMENTS[name], param: encoded})
                assert not result.is_error, (name, param)
                assert len(calls) == before + 1, (name, param)
                forwarded = calls[-1][1].get(param)
                assert type(forwarded) is list, (name, param, forwarded)
                assert forwarded == decoded, (name, param, forwarded)
            for malformed in ('["a",1]', '"x"', "null", "{}", "[1]", "[true]",
                              "[null]", '[["a"]]', "true", "5", "["):
                before = len(calls)
                with pytest.raises(ToolError, match="E_SCHEMA"):
                    await instance.call_tool(name, {**MINIMAL_ARGUMENTS[name], param: malformed})
                assert len(calls) == before, (name, param, malformed)
            checked += 1
    assert checked >= 5, "expected urls/seeds/sources across read_urls, map_site, crawl_site, web_search"


async def test_required_type_errors_do_not_suggest_omitting(server):
    # CL-04r: "omit it to use its default" is wrong advice for required
    # parameters such as urls; the hint only applies to defaulted ones.
    instance, calls = server
    with pytest.raises(ToolError, match=r"E_SCHEMA: invalid JSON type for urls$"):
        await instance.call_tool("read_urls", {"urls": 5, "idempotency_key": "no-omit"})
    assert calls == []
    checked = 0
    for tool in await instance.list_tools():
        schema = tool.input_schema
        for param in schema.get("required", []):
            kind, = _schema_types(schema["properties"][param])
            for wrong in WRONG_VALUES[kind]:
                with pytest.raises(ToolError, match="E_SCHEMA") as exc_info:
                    await instance.call_tool(tool.name, {**MINIMAL_ARGUMENTS[tool.name], param: wrong})
                assert calls == [], (tool.name, param, wrong)
                assert "omit it" not in str(exc_info.value), (tool.name, param, wrong)
            checked += 1
    assert checked >= 31, "all registered required parameters must be checked"
    with pytest.raises(ToolError, match="omit it to use its default"):
        await instance.call_tool("web_search", {"query": "alpha", "limit": "5"})
    assert calls == []


@pytest.mark.parametrize("depth", [2000, 10000])
async def test_deep_json_list_strings_raise_schema_error_without_core_calls(server, depth):
    instance, calls = server
    # Python 3.12's C decoder can accept depth 2000; the deeper case also
    # exercises its RecursionError path without changing recursion settings.
    encoded = "[" * depth + '"x"' + "]" * depth
    checked = 0
    for tool in await instance.list_tools():
        for param, field in tool.input_schema.get("properties", {}).items():
            if _schema_types(field) != ["array"]:
                continue
            with pytest.raises(Exception) as exc_info:
                await instance.call_tool(tool.name, {**MINIMAL_ARGUMENTS[tool.name], param: encoded})
            assert calls == [], (tool.name, param)
            assert isinstance(exc_info.value, ToolError), (tool.name, param, type(exc_info.value).__name__)
            assert "E_SCHEMA" in str(exc_info.value), (tool.name, param)
            checked += 1
    assert checked >= 5, "deep JSON must be rejected for every registered list parameter"


@pytest.mark.parametrize("name", ["web_search", "start_web_search"])
async def test_omitted_sources_differ_from_encoded_empty_selection(server, name):
    instance, calls = server
    arguments = dict(MINIMAL_ARGUMENTS[name])
    await instance.call_tool(name, arguments)
    await instance.call_tool(name, {**arguments, "sources": "[]"})
    operation = "web.search" if name == "web_search" else "web.search.start"
    assert calls == [(operation, {**arguments, "category": "web"}),
                     (operation, {**arguments, "sources": []})]


async def test_defaulted_nonnullable_type_error_suggests_omitting(server):
    instance, calls = server
    with pytest.raises(ToolError, match="E_SCHEMA") as exc_info:
        await instance.call_tool("search_session_events", {"session_id": "s", "limit": "20"})
    assert calls == []
    assert "omit it to use its default" in str(exc_info.value)


async def test_every_defaulted_parameter_type_error_suggests_omitting(server):
    instance, calls = server
    checked = 0
    for tool in await instance.list_tools():
        parameters = inspect.signature(instance._registered_tool(tool.name).fn).parameters
        for param, field in tool.input_schema.get("properties", {}).items():
            if parameters[param].default is inspect.Parameter.empty:
                continue
            kind, = _schema_types(field)
            for wrong in WRONG_VALUES[kind]:
                with pytest.raises(ToolError, match="E_SCHEMA") as exc_info:
                    await instance.call_tool(tool.name, {**MINIMAL_ARGUMENTS[tool.name], param: wrong})
                assert calls == [], (tool.name, param, wrong)
                assert "omit it to use its default" in str(exc_info.value), (tool.name, param, wrong)
            checked += 1
    assert checked >= 57, "all registered defaulted parameters must be checked"


async def test_required_nullable_parameter_keeps_null_guard_without_omit_hint(server):
    instance, calls = server

    @instance.tool()
    async def required_nullable(selection: str | None) -> dict:
        return await mcp_server._call("fixture.required_nullable", {"selection": selection})

    for wrong in (1, None, "null"):
        with pytest.raises(ToolError, match="E_SCHEMA") as exc_info:
            await instance.call_tool("required_nullable", {"selection": wrong})
        assert calls == []
        assert "omit it" not in str(exc_info.value)
