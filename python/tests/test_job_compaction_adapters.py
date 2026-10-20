"""Compaction adapters preserve the native owner, retention and limit boundary."""
import json

import pytest

from bzs import cli, mcp_server


@pytest.fixture
def cli_core(monkeypatch):
    calls = []
    response = {"version": "bzs-job-retention-1", "minimum_retention_ms": 604800000,
                "compacted_jobs": 2, "deleted_detail_rows": 12, "deleted_robots_records": 0,
                "has_more_jobs": True, "retained_job_keys": 2, "external_requests": 0, "model_calls": 0}

    class Client:
        def call_sync(self, operation, params):
            calls.append((operation, params))
            return response

    monkeypatch.setattr(cli.config, "load", lambda: object())
    monkeypatch.setattr(cli, "_client", lambda cfg: Client())
    return calls, response


@pytest.mark.parametrize("arguments,limit", [([], 16), (["--limit", "1"], 1), (["--limit", "64"], 64),
                                           (["--limit", "0"], 0), (["--limit", "65"], 65)])
def test_cli_compaction_forwards_one_exact_limit_and_complete_receipt(cli_core, capsys, arguments, limit):
    calls, response = cli_core
    assert cli.main(["jobs", "compact", *arguments]) == 0
    assert calls == [("jobs.compact", {"limit": limit})]
    assert json.loads(capsys.readouterr().out) == response
    # Native range errors are never silently clamped or replaced by a default.


@pytest.mark.parametrize("arguments", [["--days", "0"], ["--scope", "all"], ["--owner", "other"],
                                       ["--limit", "true"], ["--limit", "null"], ["--limit", "1.0"]])
def test_cli_cannot_select_another_owner_or_change_retention(cli_core, arguments):
    calls, _ = cli_core
    with pytest.raises(SystemExit) as exc:
        cli.main(["jobs", "compact", *arguments])
    assert exc.value.code == 2 and calls == []


def test_cli_core_failure_does_not_print_a_successful_receipt(monkeypatch, capsys):
    calls = []

    class Client:
        def call_sync(self, operation, params):
            calls.append((operation, params))
            raise cli.CoreError("E_SCHEMA", "Limit out of range", 400)

    monkeypatch.setattr(cli.config, "load", lambda: object())
    monkeypatch.setattr(cli, "_client", lambda cfg: Client())
    assert cli.main(["jobs", "compact", "--limit", "0"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "E_SCHEMA" in output.err
    assert calls == [("jobs.compact", {"limit": 0})]


@pytest.fixture
def mcp_core(monkeypatch):
    calls = []
    response = {"version": "bzs-job-retention-1", "compacted_jobs": 0, "deleted_detail_rows": 0,
                "deleted_robots_records": 0, "has_more_jobs": False, "external_requests": 0, "model_calls": 0}

    async def call(operation, params):
        calls.append((operation, params))
        return response

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls, response


@pytest.mark.parametrize("arguments,limit", [({}, 16), ({"limit": 1}, 1), ({"limit": 64}, 64),
                                           ({"limit": 0}, 0), ({"limit": 65}, 65)])
async def test_mcp_forwards_exact_limit_without_widening(mcp_core, arguments, limit):
    server, calls, response = mcp_core
    result = await server.call_tool("compact_job_history", arguments)
    assert calls == [("jobs.compact", {"limit": limit})]
    assert result.structured_content == response


@pytest.mark.parametrize("arguments", [{"limit": True}, {"limit": False}, {"limit": "16"}, {"limit": "null"},
                                       {"limit": None}, {"limit": 16.0}, {"limit": []}, {"limit": {}},
                                       {"days": 0}, {"scope": "all"}, {"owner": "other"}, {"extra": 1}, None, []])
async def test_mcp_rejects_invalid_raw_types_or_scope_knobs_before_sdk(mcp_core, arguments):
    server, calls, _ = mcp_core
    with pytest.raises(mcp_server.ToolError, match="E_SCHEMA"):
        await server.call_tool("compact_job_history", arguments)
    assert calls == []


async def test_mcp_compaction_schema_and_annotations_describe_real_deletion(mcp_core):
    server, _, _ = mcp_core
    tool = next(tool for tool in await server.list_tools() if tool.name == "compact_job_history")
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["properties"]) == {"limit"}
    assert tool.input_schema["properties"]["limit"]["type"] == "integer"
    assert tool.input_schema["properties"]["limit"]["default"] == 16
    assert tool.annotations.read_only_hint is False and tool.annotations.open_world_hint is False
    assert tool.annotations.idempotent_hint is False and tool.annotations.destructive_hint is True
    assert "Delete eligible" in tool.description and "7-day" in tool.description


async def test_mcp_keeps_the_native_error_visible(monkeypatch):
    calls = []

    class Client:
        async def call(self, operation, params):
            calls.append((operation, params))
            raise mcp_server.CoreError("E_SCHEMA", "Limit out of range", 400)

    monkeypatch.setattr(mcp_server, "_client", lambda: Client())
    with pytest.raises(mcp_server.ToolError, match="E_SCHEMA: Limit out of range"):
        await mcp_server.build_server().call_tool("compact_job_history", {"limit": 0})
    assert calls == [("jobs.compact", {"limit": 0})]
