import pytest

from mcp.server.mcpserver.exceptions import ToolError

from bzs import cli, mcp_server


async def test_revocation_is_absent_from_default_mcp_and_explicit_on_admin_stdio(monkeypatch):
    calls = []

    async def call(operation, params):
        calls.append((operation, params))
        return {"subject": params["subject"], "revoked": True}

    monkeypatch.setattr(mcp_server, "_call", call)
    assert "revoke_principal" not in {tool.name for tool in await mcp_server.build_server().list_tools()}
    server = mcp_server.build_server(admin_tools=True)
    tools = {tool.name: tool for tool in await server.list_tools()}
    tool = tools["revoke_principal"]
    assert tool.input_schema["additionalProperties"] is False
    assert tool.input_schema["required"] == ["subject"]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
    result = await server.call_tool("revoke_principal", {"subject": "alice"})
    assert not result.is_error
    assert calls == [("principal.revoke", {"subject": "alice"})]


@pytest.mark.parametrize("arguments", [{"subject": "alice", "admin": True}, {"subject": ["alice"]},
                                      {"subject": None}, {"subject": 1}, {"subject": True},
                                      {"subject": "alice", "token": "secret"}])
async def test_admin_tool_keeps_raw_subject_type_and_rejects_untrusted_fields(monkeypatch, arguments):
    async def never(*args):
        pytest.fail("Invalid management input reached the core")

    monkeypatch.setattr(mcp_server, "_call", never)
    with pytest.raises(ToolError):
        await mcp_server.build_server(admin_tools=True).call_tool("revoke_principal", arguments)


def test_http_admin_tools_are_refused_before_credentials_or_startup(monkeypatch):
    monkeypatch.setattr(cli.config, "load", lambda: pytest.fail("Should refuse before loading credentials"))
    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: pytest.fail("Must not start MCP"))
    with pytest.raises(SystemExit) as result:
        mcp_server.main(["--transport", "streamable-http", "--admin-tools"])
    assert result.value.code == 2
    with pytest.raises(SystemExit) as result:
        cli.main(["mcp", "--transport", "streamable-http", "--admin-tools"])
    assert result.value.code == 2


def test_programmatic_http_admin_tools_are_refused():
    with pytest.raises(ValueError, match="stdio"):
        mcp_server.build_server(token_verifier=object(), admin_tools=True)
    with pytest.raises(ValueError, match="stdio"):
        mcp_server.build_server(auth=object(), admin_tools=True)


def test_cli_passes_only_subject_to_core(monkeypatch, capsys):
    calls = []

    class Client:
        def call_sync(self, operation, params):
            calls.append((operation, params))
            return {"subject": params["subject"], "revoked": True}

    monkeypatch.setattr(cli.config, "load", lambda: object())
    monkeypatch.setattr(cli, "_client", lambda _: Client())
    assert cli.main(["revoke-principal", "alice"]) == 0
    assert calls == [("principal.revoke", {"subject": "alice"})]
    assert '"revoked": true' in capsys.readouterr().out
