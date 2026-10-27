"""Launcher rollback/health/MCP and offline maintenance adapter contracts."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from bzs import cli, config

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    executable=tmp_path/"searchd";executable.touch()
    monkeypatch.setattr(cli,"_read_pid",lambda _name:None)
    return config.Config(searchd=str(executable),sources="sources.json",sites="sites.json")

def test_partial_start_only_cleans_new_worker(cfg, monkeypatch):
    created=object();clean=[]
    def spawn(name,cmd,**kwargs):
        if name=="searchd":raise RuntimeError("core failed")
        return created
    monkeypatch.setattr(cli,"_spawn",spawn)
    monkeypatch.setattr(cli.process_control,"cleanup_child",lambda name,proc:clean.append((name,proc)))
    with pytest.raises(RuntimeError,match="core failed"):cli.start(cfg)
    assert clean==[("worker",created)]

def test_existing_worker_not_cleaned_after_core_failure(cfg,monkeypatch):
    monkeypatch.setattr(cli,"_read_pid",lambda name:123 if name=="worker" else None)
    monkeypatch.setattr(cli.process_control,"read_record",lambda name:{"state":"ready","port":cfg.worker_port})
    monkeypatch.setattr(cli,"_spawn",Mock(side_effect=RuntimeError("core failed")))
    cleanup=Mock();monkeypatch.setattr(cli.process_control,"cleanup_child",cleanup)
    with pytest.raises(RuntimeError,match="core failed"):cli.start(cfg)
    cleanup.assert_not_called()

def test_health_failure_is_not_success_and_cleans_new_children(cfg,monkeypatch):
    created=object();monkeypatch.setattr(cli,"start",lambda cfg:[("searchd",created)])
    monkeypatch.setattr(cli,"_client",lambda cfg:SimpleNamespace(healthy=lambda:False))
    tick=iter(range(20));monkeypatch.setattr(cli.time,"monotonic",lambda:next(tick));monkeypatch.setattr(cli.time,"sleep",lambda _:None)
    cleanup=Mock();monkeypatch.setattr(cli.process_control,"cleanup_child",cleanup)
    with pytest.raises(RuntimeError,match="health check failed"):cli.ensure_running(cfg)
    cleanup.assert_called_once_with("searchd",created)

def test_mcp_does_not_run_after_failed_autostart(cfg,monkeypatch):
    from bzs import mcp_server
    monkeypatch.delenv("BZS_CORE_URL",raising=False)
    monkeypatch.setattr(config,"load",lambda:cfg)
    monkeypatch.setattr(cli,"ensure_running",Mock(side_effect=RuntimeError("failed")))
    invoke=Mock();monkeypatch.setattr(mcp_server,"main",invoke)
    assert cli.cmd_mcp(SimpleNamespace(admin_tools=False,transport="stdio",no_autostart=False))==2
    invoke.assert_not_called()


def test_health_exception_also_cleans_new_children(cfg,monkeypatch):
    created=object();monkeypatch.setattr(cli,"start",lambda cfg:[("worker",created)])
    monkeypatch.setattr(cli,"_client",Mock(side_effect=FileNotFoundError("missing credential file")))
    cleanup=Mock();monkeypatch.setattr(cli.process_control,"cleanup_child",cleanup)
    with pytest.raises(FileNotFoundError):cli.ensure_running(cfg)
    cleanup.assert_called_once_with("worker",created)

@pytest.mark.parametrize("value", [0, -1, 121, True, "30", float("nan"), float("inf")])
def test_invalid_startup_deadline_fails_before_creating_children(cfg,monkeypatch,value):
    cfg.startup_timeout_s=value
    spawn=Mock();monkeypatch.setattr(cli,"_spawn",spawn)
    with pytest.raises(RuntimeError,match="startup_timeout_s"):cli.start(cfg)
    spawn.assert_not_called()

def test_configured_startup_deadline_passed_without_changing_request_timeout(cfg,monkeypatch):
    cfg.startup_timeout_s=17
    spawn=Mock(return_value=None);monkeypatch.setattr(cli,"_spawn",spawn)
    cli.start(cfg)
    assert spawn.call_count==2
    assert all(c.kwargs=={"timeout":17} for c in spawn.call_args_list)
