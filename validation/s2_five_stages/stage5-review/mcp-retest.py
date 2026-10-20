import asyncio
from bzs import mcp_server

async def main():
    calls=[]
    async def call(operation, params):
        calls.append((operation,params)); return {}
    mcp_server._call=call
    server=mcp_server.build_server()
    invalid=[{"sources":"null"},{"sources":"[\"source0\"]"},{"sources":[1]}, {"limit":True},{"deadline_ms":"1000"},{"unknown":1}]
    for changed in invalid:
        calls.clear()
        try:
            await server.call_tool("start_web_search",{"query":"fixture","idempotency_key":"key",**changed})
        except mcp_server.ToolError as exc:
            assert "E_SCHEMA" in str(exc)
        else:
            raise AssertionError("invalid raw arguments reached the core")
        assert not calls
    for query in ('"exact phrase"', "null", "普通查询"):
        calls.clear()
        await server.call_tool("start_web_search",{"query":query,"idempotency_key":"key","sources":["source0"]})
        assert calls[0][1]["query"]==query
        assert calls[0][1]["sources"]==["source0"]
    descriptions={tool.name:tool for tool in await server.list_tools()}
    for name in ("start_web_search","get_search_session","search_session_events","search_session_page","cancel_search_session"):
        assert descriptions[name].input_schema["additionalProperties"] is False
    print("PASS MCP independent checks: 6 invalid wire shapes block before core; 3 exact strings preserved; 5 schemas forbid unknown fields; no network")

asyncio.run(main())
