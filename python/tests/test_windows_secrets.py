"""CL-08: Windows secret-file DACLs, `bzs doctor`, and least-privilege tokens.

The DACL reader here is a deliberately independent ctypes implementation: the
tests must observe the real on-disk security descriptor, not the code under
test. POSIX-only behaviour (chmod 0600 in ``config._private_write``) is covered
by a pure rule test so the mode-based contract stays pinned on every platform.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bzs import cli, config

SYSTEM_SID = "S-1-5-18"
BROAD_SIDS = ("S-1-1-0", "S-1-5-32-545", "S-1-5-11")  # Everyone, BUILTIN\Users, Authenticated Users

needs_windows = pytest.mark.skipif(os.name != "nt", reason="Windows DACL behaviour")

if os.name == "nt":
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    _SE_DACL_PROTECTED = 0x1000

    _adv.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    _adv.GetNamedSecurityInfoW.argtypes = [
        wt.LPCWSTR, ctypes.c_int, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    _adv.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort),
                                                 ctypes.POINTER(ctypes.c_uint32)]
    _adv.GetSecurityDescriptorDacl.restype = ctypes.c_int
    _adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int)]
    _adv.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    _adv.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    _adv.ConvertSidToStringSidW.restype = ctypes.c_int
    _adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.GetCurrentProcess.restype = ctypes.c_void_p
    _adv.OpenProcessToken.restype = ctypes.c_int
    _adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    _adv.GetTokenInformation.restype = ctypes.c_int
    _adv.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.POINTER(ctypes.c_uint32)]
    _adv.GetLengthSid.restype = ctypes.c_uint32
    _adv.GetLengthSid.argtypes = [ctypes.c_void_p]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [("AceCount", ctypes.c_uint32), ("AclBytesInUse", ctypes.c_uint32),
                    ("AclBytesFree", ctypes.c_uint32)]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]

    def current_user_sid() -> str:
        token = ctypes.c_void_p()
        assert _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x8, ctypes.byref(token))
        try:
            needed = ctypes.c_uint32()
            _adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            assert _adv.GetTokenInformation(token, 1, buffer, needed.value, ctypes.byref(needed))
            sid = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents.User
            out = ctypes.c_void_p()
            assert _adv.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out))
            value = ctypes.c_wchar_p(out.value).value
            _k32.LocalFree(out)
            return value
        finally:
            _k32.CloseHandle(token)

    def read_dacl(path: Path) -> tuple[bool, list[tuple[str, int]]]:
        """Return (inheritance broken, [(sid, access mask)]) from the real security descriptor."""
        descriptor = ctypes.c_void_p()
        rc = _adv.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT,
                                        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
                                        None, None, None, None, ctypes.byref(descriptor))
        assert rc == 0, f"GetNamedSecurityInfoW failed with {rc}"
        try:
            control = ctypes.c_ushort()
            revision = ctypes.c_uint32()
            assert _adv.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
            present = ctypes.c_int()
            dacl = ctypes.c_void_p()
            defaulted = ctypes.c_int()
            assert _adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                  ctypes.byref(defaulted))
            assert present.value, "no DACL present"
            assert dacl.value, "NULL DACL grants everyone everything"
            info = _ACL_SIZE_INFORMATION()
            assert _adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2)
            entries = []
            for index in range(info.AceCount):
                ace = ctypes.c_void_p()
                assert _adv.GetAce(dacl, index, ctypes.byref(ace))
                header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
                assert header.AceType == 0, f"unexpected ACE type {header.AceType}"
                # ACCESS_ALLOWED_ACE = ACE_HEADER (4 bytes) + ACCESS_MASK (4) + SID
                base = ace.value
                mask = int.from_bytes(ctypes.string_at(base + 4, 4), "little")
                sid_ptr = ctypes.c_void_p()
                assert _adv.ConvertSidToStringSidW(ctypes.cast(base + 8, ctypes.c_void_p), ctypes.byref(sid_ptr))
                sid = ctypes.c_wchar_p(sid_ptr.value).value
                _k32.LocalFree(sid_ptr)
                entries.append((sid, mask))
            return bool(control.value & _SE_DACL_PROTECTED), entries
        finally:
            _k32.LocalFree(descriptor)

    def icacls_output(path: Path) -> str:
        result = subprocess.run(["icacls", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout


def _secret_paths(home: Path) -> list[Path]:
    names = [name for name in ("api.token", "admin.token", "worker.token", "auth.json")
             if (home / name).exists()]
    return [home / name for name in names]


@needs_windows
def test_init_breaks_inheritance_and_grants_only_user_and_system(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    user = current_user_sid()
    for path in [tmp_path, *_secret_paths(tmp_path)]:
        protected, entries = read_dacl(path)
        assert protected, f"{path.name}: inheritance must be broken (SE_DACL_PROTECTED)"
        sids = {sid for sid, _ in entries}
        assert user in sids, f"{path.name}: current user must keep full control"
        assert sids <= {user, SYSTEM_SID}, f"{path.name}: unexpected grantees {sids - {user, SYSTEM_SID}}"


@needs_windows
def test_init_dacl_matches_icacls_cross_check(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    user = current_user_sid()
    for path in [tmp_path, *_secret_paths(tmp_path)]:
        text = icacls_output(path)
        assert "(I)" not in text, f"{path.name}: icacls still reports inherited ACEs:\n{text}"
        for sid in BROAD_SIDS:
            assert sid not in text, f"{path.name}: icacls reports broad grant for {sid}"
        assert "(F)" in text, f"{path.name}: full-control grant missing from icacls output"


@needs_windows
def test_mcp_token_add_protects_clients_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    from bzs import mcp_auth
    mcp_auth.add_client("ci-client")
    clients = tmp_path / "mcp_clients.json"
    assert clients.exists()
    user = current_user_sid()
    protected, entries = read_dacl(clients)
    assert protected
    assert {sid for sid, _ in entries} <= {user, SYSTEM_SID} and user in {sid for sid, _ in entries}


@needs_windows
def test_create_auth_protects_written_secrets(tmp_path):
    output = tmp_path / "auth.json"
    env = dict(os.environ, PYTHONPATH=str(config.REPO_ROOT / "python"))  # primary ctypes path, not the icacls fallback
    result = subprocess.run([sys.executable, str(config.REPO_ROOT / "tools" / "create_auth.py"),
                             "--output", str(output), "--subject", "ci", "--admin"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    user = current_user_sid()
    for path in (output, output.with_suffix(".token")):
        protected, entries = read_dacl(path)
        assert protected, f"{path.name} keeps inherited ACEs"
        sids = {sid for sid, _ in entries}
        assert sids <= {user, SYSTEM_SID} and user in sids


@needs_windows
def test_probe_record_body_protects_written_evidence(tmp_path):
    from bzs import probe_state
    recorded = probe_state.record_body(tmp_path, "a000001", b"payload")
    path = tmp_path / "raw" / f"a000001-{recorded['body_sha256']}.body.gz"
    assert path.exists()
    user = current_user_sid()
    protected, entries = read_dacl(path)
    assert protected, "recorded evidence keeps inherited ACEs"
    sids = {sid for sid, _ in entries}
    assert sids <= {user, SYSTEM_SID} and user in sids


@needs_windows
def test_doctor_accepts_fresh_installation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    try:
        rc = cli.main(["doctor"])
    except SystemExit as exc:  # argparse rejects unknown subcommands before doctor exists
        pytest.fail(f"`bzs doctor` did not run (argparse exit {exc.code})")
    assert rc == 0, capsys.readouterr().out + capsys.readouterr().err


@needs_windows
def test_doctor_flags_deliberately_widened_secret_acl(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    widened = tmp_path / "api.token"
    granted = subprocess.run(["icacls", str(widened), "/grant", "*S-1-5-32-545:(R)"],
                             capture_output=True, text=True)
    assert granted.returncode == 0, granted.stderr
    try:
        rc = cli.main(["doctor"])
    except SystemExit as exc:
        pytest.fail(f"`bzs doctor` did not run (argparse exit {exc.code})")
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "S-1-5-32-545" in captured.out, captured.out
    assert "ERROR" in captured.out, captured.out


@needs_windows
def test_dacl_api_failure_keeps_files_and_reports_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    from bzs import winsecurity

    def broken_on_files(path, **_kwargs):
        # Let the home directory harden succeed so a secret gets written first;
        # every per-file protection then fails.
        if Path(path).is_dir():
            return
        raise winsecurity.WindowsSecurityError("injected failure")

    monkeypatch.setattr(winsecurity, "_protect_ctypes", broken_on_files)
    monkeypatch.setattr(winsecurity, "_protect_icacls", broken_on_files)
    with pytest.raises(RuntimeError, match="injected failure"):
        config.init()
    # Fail closed: the written secrets stay on disk, and the failure is loud.
    assert (tmp_path / "api.token").exists()


@needs_windows
def test_doctor_fails_closed_when_dacl_cannot_be_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    from bzs import winsecurity

    def broken(*_args, **_kwargs):
        raise winsecurity.WindowsSecurityError("injected read failure")

    monkeypatch.setattr(winsecurity, "read_dacl", broken)
    try:
        rc = cli.main(["doctor"])
    except SystemExit as exc:
        pytest.fail(f"`bzs doctor` did not run (argparse exit {exc.code})")
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "cannot read DACL" in captured.out, captured.out


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_init_creates_owner_and_admin_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    config.init()
    api = (tmp_path / "api.token").read_text(encoding="utf-8").strip()
    admin = (tmp_path / "admin.token").read_text(encoding="utf-8").strip()
    assert api and admin and api != admin
    entries = {entry["sha256"]: entry for entry in json.loads((tmp_path / "auth.json").read_text())["tokens"]}
    assert entries[_sha256(api)]["admin"] is False
    assert entries[_sha256(admin)]["admin"] is True
    # MCP/api.token default is the non-admin owner token; management CLI uses the admin one.
    assert config.mcp_token() == api
    assert config.admin_token() == admin


def test_mcp_prefers_non_admin_token_when_api_token_is_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    admin = "a" * 64
    owner = "b" * 64
    (tmp_path / "api.token").write_text(admin + "\n", encoding="utf-8")
    (tmp_path / "owner.token").write_text(owner + "\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": [
        {"sha256": _sha256(admin), "subject": "owner", "admin": True},
        {"sha256": _sha256(owner), "subject": "owner-agent", "admin": False},
    ]}), encoding="utf-8")
    assert config.mcp_token() == owner


def test_legacy_single_token_home_still_authenticates(tmp_path, monkeypatch):
    """A pre-CL-08 home keeps working: one admin token, no admin.token file."""
    monkeypatch.setenv("BZS_HOME", str(tmp_path))
    legacy = "c" * 64
    auth = {"tokens": [{"sha256": _sha256(legacy), "subject": "owner", "admin": True}]}
    (tmp_path / "api.token").write_text(legacy + "\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    assert config.api_token() == legacy
    assert config.mcp_token() == legacy
    assert config.admin_token() == legacy
    # The core authenticates digests from auth.json; the default pick must remain valid.
    assert _sha256(config.mcp_token()) in {entry["sha256"] for entry in auth["tokens"]}


def test_doctor_posix_rule_flags_group_and_world_readable():
    """Pin the POSIX half of the doctor rule on every platform (pure function)."""
    from bzs import doctor
    assert doctor.mode_problems(0o600) == []
    assert doctor.mode_problems(0o640) != []
    assert doctor.mode_problems(0o604) != []
    assert doctor.mode_problems(0o666) != []
