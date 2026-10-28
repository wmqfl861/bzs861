"""Launcher rollback/health/MCP and offline maintenance adapter contracts."""
import os
import time
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
    monkeypatch.setattr(cli.process_control,"cleanup_child",lambda name,proc,**kw:clean.append((name,proc)))
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
    cleanup.assert_called_once();assert cleanup.call_args.args[:2]==("searchd",created)

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
    cleanup.assert_called_once();assert cleanup.call_args.args[:2]==("worker",created)

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

def test_cli_reports_lifecycle_errors_without_traceback(cfg, monkeypatch, capsys):
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(cli, "ensure_running", Mock(side_effect=RuntimeError("health check failed")))
    assert cli.main(["start"]) == 2
    captured = capsys.readouterr()
    assert "E_CLI: health check failed" in captured.err
    assert "Traceback" not in captured.err


def test_health_failure_still_cleans_remaining_children_when_one_cleanup_fails(cfg,monkeypatch):
    worker=object();searchd=object()
    monkeypatch.setattr(cli,"start",lambda cfg:[("worker",worker),("searchd",searchd)])
    monkeypatch.setattr(cli,"_client",lambda cfg:SimpleNamespace(healthy=lambda:False))
    tick=iter(range(20));monkeypatch.setattr(cli.time,"monotonic",lambda:next(tick));monkeypatch.setattr(cli.time,"sleep",lambda _:None)
    calls=[]
    def cleanup(name,proc,**kw):
        calls.append(name)
        if name=="searchd":raise RuntimeError("stop not confirmed; record retained")
    monkeypatch.setattr(cli.process_control,"cleanup_child",cleanup)
    with pytest.raises(RuntimeError,match="cleanup incomplete"):cli.ensure_running(cfg)
    assert calls==["searchd","worker"]


def test_start_failure_reports_failed_child_cleanup(cfg,monkeypatch):
    worker=object()
    def spawn(name,cmd,**kwargs):
        if name=="worker":return worker
        raise RuntimeError("core failed")
    monkeypatch.setattr(cli,"_spawn",spawn)
    def cleanup(name,proc,**kw):raise RuntimeError("worker stuck")
    monkeypatch.setattr(cli.process_control,"cleanup_child",cleanup)
    with pytest.raises(RuntimeError,match="child cleanup incomplete: worker stuck"):cli.start(cfg)


def test_stop_attempts_both_components_when_first_fails(cfg,monkeypatch):
    stopped=[]
    def stop_owned(name,*,timeout=5.0):
        if name=="searchd":raise RuntimeError("stop not confirmed; record retained")
        stopped.append(name);return True
    monkeypatch.setattr(cli.process_control,"stop_owned",stop_owned)
    with pytest.raises(RuntimeError,match="searchd: stop not confirmed"):cli.stop()
    assert stopped==["worker"]


def test_cleanup_children_cleans_all_children_without_lock(tmp_path,monkeypatch):
    import contextlib
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    from bzs import process_control
    @contextlib.contextmanager
    def busy_lock():
        raise RuntimeError("another launcher operation is running");yield None
    monkeypatch.setattr(process_control,"control_lock",busy_lock)
    cleaned=[]
    monkeypatch.setattr(process_control,"cleanup_child",lambda n,p,**kw:cleaned.append(n))
    process_control.cleanup_children([("worker",object()),("searchd",object())])
    assert cleaned==["searchd","worker"]


def test_cleanup_children_reports_every_failed_child(tmp_path,monkeypatch):
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    from bzs import process_control
    def cleanup(n,p,**kw):raise RuntimeError(f"{n} stuck")
    monkeypatch.setattr(process_control,"cleanup_child",cleanup)
    with pytest.raises(RuntimeError,match="child cleanup incomplete: searchd stuck; worker stuck"):
        process_control.cleanup_children([("worker",object()),("searchd",object())])


@pytest.mark.skipif(os.name!="nt",reason="Windows termination-handle race")
def test_stop_owned_tolerates_fully_reaped_root(tmp_path,monkeypatch):
    # Root exits and is reaped before the stop: the pid resolves to nothing;
    # with no attributable children the record is removed quietly.
    import json,subprocess,sys
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    from bzs import config,process_control
    gone=subprocess.Popen([sys._base_executable,"-c","pass"])
    birth=process_control.birth_identity(gone.pid)
    assert birth is not None
    gone.wait()  # reaped: OpenProcess will report the pid as gone
    record={"version":1,"name":"searchd","pid":gone.pid,"birth":birth,"port":8610,"state":"ready"}
    config._private_write(process_control.record_path("searchd"),json.dumps(record))
    try:
        assert process_control.stop_owned("searchd") is False
    except RuntimeError as exc:
        if "identity changed" not in str(exc):
            raise
        pytest.skip("the reaped pid was reused during the test")
    assert not process_control.record_path("searchd").exists()


@pytest.mark.skipif(os.name!="nt",reason="Windows termination-handle race")
def test_stop_owned_forgets_record_when_target_exited_but_not_reaped(tmp_path,monkeypatch):
    # Exited yet unreaped: the still-open zombie handle proves the recorded
    # birth, so the stop verifies identity and forgets without signalling.
    import json,subprocess,sys
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    from bzs import config,process_control
    zombie=subprocess.Popen([sys._base_executable,"-c","import time;time.sleep(0.3)"])
    birth=process_control.birth_identity(zombie.pid)
    assert birth is not None
    time.sleep(0.8)  # exited, never reaped: OpenProcess succeeds, wait state exited
    record={"version":1,"name":"worker","pid":zombie.pid,"birth":birth,"port":8611,"state":"ready"}
    config._private_write(process_control.record_path("worker"),json.dumps(record))
    try:
        assert process_control.stop_owned("worker") is False
        assert not process_control.record_path("worker").exists()
    finally:
        zombie.wait(timeout=5)
