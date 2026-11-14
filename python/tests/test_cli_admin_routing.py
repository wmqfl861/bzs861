"""CL-28: `bzs call` must pick the administrator token for admin-only operations.

After CL-08 split tokens, a fresh home's api.token is the non-admin owner
token, so `bzs call backup.create` was refused (E_FORBIDDEN). cmd_call now
routes admin-only operations through config.admin_token(); everything else
keeps the owner token. Environment overrides keep winning for both.
"""
from unittest.mock import Mock

import pytest

from bzs import cli, config


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    monkeypatch.delenv("SEARCH_API_TOKEN", raising=False)
    (tmp_path / "api.token").write_text("owner-token\n", encoding="utf-8")
    (tmp_path / "owner.token").write_text("owner-token\n", encoding="utf-8")
    (tmp_path / "admin.token").write_text("admin-token\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text(
        '{"entries":[{"name":"owner","sha256":"o"},{"name":"admin","sha256":"a","admin":true}]}',
        encoding="utf-8")
    return tmp_path


def _capture_client(monkeypatch, tokens):
    calls = []

    class FakeClient:
        def __init__(self, url, token):
            calls.append(token)
            self.tokens = calls

        def call_sync(self, operation, params):
            return {"ok": True, "operation": operation}

    monkeypatch.setattr(cli, "CoreClient", FakeClient)
    monkeypatch.setattr(cli.config, "load", lambda: Mock(core_url="http://127.0.0.1:1", searchd="x"))
    return calls


def test_admin_only_operations_use_the_admin_token(home, monkeypatch):
    calls = _capture_client(monkeypatch, None)
    assert cli.main(["call", "backup.create", '{"destination":"backup.sqlite"}']) == 0
    assert calls == ["admin-token"]
    assert cli.main(["call", "principal.revoke", '{"subject":"bob"}']) == 0
    assert calls == ["admin-token", "admin-token"]


def test_other_operations_keep_the_owner_token(home, monkeypatch):
    calls = _capture_client(monkeypatch, None)
    assert cli.main(["call", "stats", "{}"]) == 0
    assert calls == ["owner-token"]
    assert cli.main(["call", "search.once", '{"query":"x"}']) == 0
    assert calls == ["owner-token", "owner-token"]


def test_environment_override_wins_for_both_paths(home, monkeypatch):
    monkeypatch.setenv("SEARCH_API_TOKEN", "env-token")
    calls = _capture_client(monkeypatch, None)
    assert cli.main(["call", "backup.create", "{}"]) == 0
    assert cli.main(["call", "stats", "{}"]) == 0
    assert calls == ["env-token", "env-token"]


def test_legacy_single_token_home_still_works(home, monkeypatch):
    # No admin.token: the one legacy token in api.token remains the admin token.
    (home / "admin.token").unlink()
    (home / "auth.json").write_text(
        '{"entries":[{"name":"legacy","sha256":"o","admin":true}]}', encoding="utf-8")
    calls = _capture_client(monkeypatch, None)
    assert cli.main(["call", "backup.create", "{}"]) == 0
    assert calls == ["owner-token"]
