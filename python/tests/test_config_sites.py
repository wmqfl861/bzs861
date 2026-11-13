"""Deployment policy wiring: neither process silently widens an old installation."""
import json

import pytest

from bzs import cli, config


def test_init_creates_empty_site_registry_without_replacing_existing_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    cfg = config.load()
    assert cfg.sites == str(tmp_path / "sites.json")
    assert json.loads((tmp_path / "sites.json").read_text()) == {"version": 1, "sites": []}
    original = {name: (tmp_path / name).read_bytes() for name in ("api.token", "worker.token", "auth.json")}
    # A routine repeat init must preserve both operator policy and credentials.
    (tmp_path / "sites.json").write_text('{"version":1,"sites":[],"operator_note":"retained"}')
    config.init()
    assert json.loads((tmp_path / "sites.json").read_text())["operator_note"] == "retained"
    assert all((tmp_path / name).read_bytes() == value for name, value in original.items())


def test_legacy_config_does_not_acquire_a_discovery_policy_implicitly(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"sources":"old-sources.json","core_port":8610}')
    assert config.load().sites == ""


def test_launcher_passes_exact_same_review_file_to_worker_and_core(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    executable = tmp_path / "searchd"
    executable.touch()
    policy = tmp_path / "reviewed sites.json"
    cfg = config.Config(searchd=str(executable), sources="sources.json", sites=str(policy),
                        worker_args=["--test-host", "fixture.test=127.0.0.1"])
    calls = []
    monkeypatch.setattr(cli, "_read_pid", lambda name: None)
    monkeypatch.setattr(cli, "_spawn", lambda name, command, **kwargs: calls.append((name, command)))
    cli.start(cfg)
    assert [name for name, _ in calls] == ["worker", "searchd"]
    for _, command in calls:
        assert command.count("--sites") == 1
        assert command[command.index("--sites") + 1] == str(policy)
    assert calls[0][1][-2:] == cfg.worker_args
    calls.clear()
    cfg.sites = ""
    cli.start(cfg)
    assert all("--sites" not in command for _, command in calls)


@pytest.mark.parametrize("arguments", [
    ["--sites", "another.json"], ["--sites=another.json"],
    ["--site", "another.json"], ["--site=another.json"],
    ["--sit", "another.json"], ["--sit=another.json"],
    ["--si", "another.json"], ["--si=another.json"],
])
def test_launcher_rejects_worker_policy_overrides_before_starting_either_process(tmp_path, monkeypatch, arguments):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    executable = tmp_path / "searchd"
    executable.touch()
    cfg = config.Config(searchd=str(executable), sites=str(tmp_path / "approved.json"), worker_args=arguments)
    calls = []
    monkeypatch.setattr(cli, "_read_pid", lambda name: None)
    monkeypatch.setattr(cli, "_spawn", lambda name, command, **kwargs: calls.append((name, command)))
    with pytest.raises(SystemExit, match="E_CONFIG.*shared site policy"):
        cli.start(cfg)
    assert calls == []
