"""Offline maintenance CLI must not start the service or weaken confirmation."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from bzs import cli,config

@pytest.fixture
def cfg(tmp_path,monkeypatch):
    monkeypatch.setenv("BZS_HOME",str(tmp_path))
    executable=tmp_path/"searchd";executable.touch()
    return config.Config(searchd=str(executable))

def test_offline_storage_never_autostarts(cfg,monkeypatch):
    monkeypatch.setattr(config,"load",lambda:cfg)
    start=Mock();monkeypatch.setattr(cli,"start",start)
    run=Mock(return_value=SimpleNamespace(returncode=7));monkeypatch.setattr(cli.subprocess,"run",run)
    assert cli.main(["storage","inspect","metadata.sqlite"])==7
    assert run.call_args.args[0][-2:]==["storage-inspect","metadata.sqlite"]
    with pytest.raises(SystemExit):cli.main(["storage","convert","metadata.sqlite"])
    assert run.call_count==1
    assert cli.main(["storage","convert","metadata.sqlite","--confirm-offline","--timeout-ms","1234"])==7
    assert run.call_args.args[0][-4:]==["metadata.sqlite","--confirm-offline","--timeout-ms","1234"]
    start.assert_not_called()
