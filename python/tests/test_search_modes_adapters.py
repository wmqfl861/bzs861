"""Adapters preserve the core's explicit search grant; no live sources are used."""
import json

import pytest

from bzs import cli, mcp_server
from bzs.core_client import CoreError
from bzs.worker.server import Worker


@pytest.fixture
def mcp(monkeypatch):
    calls = []
    response = {"results": [], "sources": [], "effective_mode": "fast",
                "effective_budget": {"max_sources": 2, "max_reads": 0, "max_model_calls": 0,
                                     "max_browser_calls": 0}, "omitted_sources": ["third"]}

    async def call(operation, params):
        calls.append((operation, params))
        return dict(response)

    monkeypatch.setattr(mcp_server, "_call", call)
    return mcp_server.build_server(), calls, response


async def test_mcp_preserves_explicit_mode_grant_and_core_metadata(mcp):
    instance, calls, response = mcp
    grant = {"query": "中文", "mode": "fast", "max_sources": 1, "limit": 2,
             "per_source": 3, "timeout_ms": 1000, "deadline_ms": 2000, "sources": ["first"]}
    result = await instance.call_tool("web_search", grant)
    assert calls == [("web.search", grant)]
    for key in ("effective_mode", "effective_budget", "omitted_sources"):
        assert result.structured_content[key] == response[key]
    durable = {**grant, "idempotency_key": "mode-fixture"}
    await instance.call_tool("start_web_search", durable)
    assert calls[-1] == ("web.search.start", durable)


async def test_mcp_omits_absent_mode_and_budget_for_core_legacy_defaults(mcp):
    instance, calls, _ = mcp
    await instance.call_tool("web_search", {"query": "legacy"})
    await instance.call_tool("start_web_search", {"query": "legacy", "idempotency_key": "old-key"})
    assert calls == [("web.search", {"query": "legacy", "category": "web"}),
                     ("web.search.start", {"query": "legacy", "idempotency_key": "old-key", "category": "web"})]


@pytest.mark.parametrize("tool", ["web_search", "start_web_search"])
@pytest.mark.parametrize("change", [{"mode": None}, {"mode": True}, {"mode": "null"},
                                     {"max_sources": True}, {"max_sources": "2"}, {"max_sources": 2.0},
                                     {"deadline_ms": None}, {"per_source": False}, {"timeout_ms": "1000"},
                                     {"limit": 1.0}, {"sources": "[]"}, {"sources": [1]}, {"extra": 1}])
async def test_mcp_rejects_raw_coercion_before_core(mcp, tool, change):
    instance, calls, _ = mcp
    request = {"query": "fixture", **change}
    if tool == "start_web_search":
        request["idempotency_key"] = "mode-fixture"
    with pytest.raises(mcp_server.ToolError, match="E_SCHEMA"):
        await instance.call_tool(tool, request)
    assert calls == []


async def test_mcp_never_clamps_explicit_range_or_empty_source_selection(mcp):
    instance, calls, _ = mcp
    await instance.call_tool("web_search", {"query": "fixture", "mode": "fast", "limit": 100, "sources": []})
    assert calls == [("web.search", {"query": "fixture", "mode": "fast", "limit": 100, "sources": []})]


async def test_mode_catalog_is_local_and_schema_excludes_null_and_extra_arguments(mcp):
    instance, calls, _ = mcp
    await instance.call_tool("list_search_modes", {})
    assert calls == [("search.modes", {})]
    tools = {tool.name: tool for tool in await instance.list_tools()}
    assert tools["list_search_modes"].annotations.read_only_hint is True
    assert tools["list_search_modes"].annotations.open_world_hint is False
    for name in ("list_search_modes", "web_search", "start_web_search"):
        assert tools[name].input_schema["additionalProperties"] is False
    for name in ("web_search", "start_web_search"):
        fields = tools[name].input_schema["properties"]
        assert fields["mode"]["enum"] == ["fast", "balanced", "deep"]
        for key in ("mode", "max_sources", "limit", "per_source", "timeout_ms", "deadline_ms"):
            assert all(item.get("type") != "null" for item in fields[key].get("anyOf", []))
    with pytest.raises(mcp_server.ToolError, match="E_SCHEMA"):
        await instance.call_tool("list_search_modes", {"mode": "fast"})
    assert len(calls) == 1


@pytest.fixture
def terminal(monkeypatch):
    calls = []

    class Client:
        def call_sync(self, operation, params):
            calls.append((operation, params))
            return {"results": [], "sources": [], "effective_mode": "deep",
                    "effective_budget": {"max_reads": 0}, "omitted_sources": ["third"]}

    monkeypatch.setattr(cli.config, "load", lambda: object())
    monkeypatch.setattr(cli, "_client", lambda cfg: Client())
    return calls


def test_cli_maps_mode_flags_exactly_and_exposes_effective_budget(terminal, capsys):
    assert cli.main(["search", "中文", "fixture", "--mode", "deep", "--max-sources", "1", "--limit", "2",
                     "--per-source", "3", "--timeout-ms", "1000", "--deadline-ms", "2000",
                     "--source", "first", "--refresh"]) == 0
    assert terminal == [("web.search", {"query": "中文 fixture", "category": "web", "mode": "deep",
                                       "max_sources": 1, "limit": 2, "per_source": 3, "timeout_ms": 1000,
                                       "deadline_ms": 2000, "sources": ["first"], "refresh": True})]
    output = capsys.readouterr().out
    assert "mode: deep" in output and '"max_reads": 0' in output and "third" in output


def test_cli_legacy_defaults_remain_omitted_and_catalog_is_unmodified(terminal, capsys):
    assert cli.main(["search", "legacy"]) == 0
    assert terminal == [("web.search", {"query": "legacy", "category": "web"})]
    capsys.readouterr()
    assert cli.main(["search-modes"]) == 0
    assert terminal[-1] == ("search.modes", {})
    assert json.loads(capsys.readouterr().out)["effective_budget"] == {"max_reads": 0}


def test_cli_forwards_over_budget_value_for_core_rejection(monkeypatch, terminal, capsys):
    calls = []

    class Denied:
        def call_sync(self, operation, params):
            calls.append((operation, params))
            raise CoreError("E_SCHEMA", "preset limit exceeded", 400)

    monkeypatch.setattr(cli, "_client", lambda cfg: Denied())
    assert cli.main(["search", "fixture", "--mode", "fast", "--limit", "11"]) == 1
    assert calls[0][1]["limit"] == 11
    assert "E_SCHEMA" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [["--mode", "FAST"], ["--max-sources", "true"], ["--deadline-ms", "1.5"]])
def test_cli_invalid_syntax_never_reaches_core(terminal, arguments):
    with pytest.raises(SystemExit):
        cli.main(["search", "fixture", *arguments])
    assert terminal == []


@pytest.fixture
def worker():
    calls = []

    class Sources:
        manifest_digest = "a" * 64

        async def run_many(self, *args):
            calls.append(args)
            return {}

    instance = Worker.__new__(Worker)
    instance.sources = Sources()
    return instance, calls


async def test_worker_preserves_subsecond_remaining_deadline_without_rounding(worker):
    instance, calls = worker
    await instance.search_multi({"query": "fixture", "sources": ["first"], "timeout_ms": 37,
                                 "manifest_sha256": instance.sources.manifest_digest})
    assert calls == [(["first"], "fixture", 10, None, 0.037)]


@pytest.mark.parametrize("timeout", [0, -1, 30001, True, "37", 37.0, None])
async def test_worker_rejects_invalid_timeout_without_dispatch(worker, timeout):
    instance, calls = worker
    with pytest.raises(ValueError, match="timeout_ms"):
        await instance.search_multi({"query": "fixture", "sources": ["first"], "timeout_ms": timeout,
                                     "manifest_sha256": instance.sources.manifest_digest})
    assert calls == []
