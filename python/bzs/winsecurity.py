"""Windows ACL protection for local secret files (CL-08).

Secrets written by this package (API tokens, auth digests, the MCP client
registry, recorded probe evidence) are stored under a protected DACL:
inheritance from the parent directory is broken and only the current user SID
and LOCAL_SYSTEM keep full control. Everything is done through ctypes; the
``icacls`` tool is used only as a fallback writer. No new dependencies.

This module is security-sensitive and fails closed: every failure raises
:class:`WindowsSecurityError` so a caller keeps the written file and reports
the failure honestly instead of continuing with a permissive ACL. Only import
this module on Windows (``os.name == "nt"``); the DLLs are loaded lazily so a
stray import on another platform stays harmless until a function is called.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
from pathlib import Path

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
# Display names for diagnostics only (CL-30). The audit itself is allowlist
# based: any allow-ACE that grants read to a principal outside
# _allowed_read_sids() is flagged, so an unknown SID can never slip through by
# merely being absent from this name map.
WELL_KNOWN_SID_NAMES = {
    "S-1-1-0": "Everyone",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-18": "NT AUTHORITY\\SYSTEM",
    "S-1-5-32-544": "BUILTIN\\Administrators",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-32-546": "BUILTIN\\Guests",
    "S-1-15-2-1": "ALL APPLICATION PACKAGES",
}

_ACCESS_ALLOWED_ACE_TYPE = 0x0
_ACL_REVISION = 2
_GENERIC_ALL = 0x10000000
_GENERIC_READ = 0x80000000
# FILE_READ_DATA|FILE_READ_EA|FILE_READ_ATTRIBUTES plus STANDARD_RIGHTS_READ and SYNCHRONIZE.
_FILE_READ_BITS = 0x120089
_DACL_SECURITY_INFORMATION = 0x4
_OWNER_SECURITY_INFORMATION = 0x1
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_SE_FILE_OBJECT = 1
_NO_INHERITANCE = 0x0
_SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1


class WindowsSecurityError(RuntimeError):
    """A Windows security API failed.

    The affected file must be kept as-is; the failure is reported through this
    error instead of being swallowed (fail closed).
    """


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort)]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [("AceCount", ctypes.c_uint32), ("AclBytesInUse", ctypes.c_uint32), ("AclBytesFree", ctypes.c_uint32)]


class _TOKEN_USER_LAYOUT(ctypes.Structure):
    _fields_ = [("User", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]


_apis: dict[str, object] | None = None
_user_sid: dict[str, object] | None = None


def _load_apis() -> dict[str, object]:
    global _apis
    if _apis is None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            advapi32.GetNamedSecurityInfoW.restype = ctypes.c_uint32
            advapi32.GetNamedSecurityInfoW.argtypes = [
                wt.LPCWSTR, ctypes.c_int, ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ]
            advapi32.GetSecurityDescriptorControl.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_uint32)]
            advapi32.GetSecurityDescriptorDacl.restype = ctypes.c_int
            advapi32.GetSecurityDescriptorDacl.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_int)]
            advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
            advapi32.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
            advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
            advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            advapi32.ConvertStringSidToSidW.restype = ctypes.c_int
            advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
            advapi32.GetLengthSid.restype = ctypes.c_uint32
            advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
            advapi32.InitializeAcl.restype = ctypes.c_int
            advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
            advapi32.AddAccessAllowedAceEx.restype = ctypes.c_int
            advapi32.AddAccessAllowedAceEx.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            advapi32.SetNamedSecurityInfoW.restype = ctypes.c_uint32
            advapi32.SetNamedSecurityInfoW.argtypes = [
                wt.LPCWSTR, ctypes.c_int, ctypes.c_uint32,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            advapi32.OpenProcessToken.restype = ctypes.c_int
            advapi32.OpenProcessToken.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
            advapi32.GetTokenInformation.restype = ctypes.c_int
            advapi32.GetTokenInformation.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        except OSError as exc:
            raise WindowsSecurityError(f"E_LOCAL_SECURITY: cannot load Windows security APIs: {exc}") from exc
        _apis = {"kernel32": kernel32, "advapi32": advapi32}
    return _apis


def _sid_bytes(sid_source) -> ctypes.Array:
    """Return an owned copy of a SID (the source buffer may be transient)."""
    advapi32 = _load_apis()["advapi32"]
    length = advapi32.GetLengthSid(sid_source)
    if not length:
        raise WindowsSecurityError(f"E_LOCAL_SECURITY: GetLengthSid failed ({ctypes.get_last_error()})")
    buffer = ctypes.create_string_buffer(length)
    ctypes.memmove(buffer, sid_source, length)
    return buffer


def _current_user() -> dict[str, object]:
    """Return {"sid": owned SID bytes, "sid_string": S-...} of the process user (cached)."""
    global _user_sid
    if _user_sid is None:
        kernel32, advapi32 = _load_apis()["kernel32"], _load_apis()["advapi32"]
        token = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            raise WindowsSecurityError(f"E_LOCAL_SECURITY: OpenProcessToken failed ({ctypes.get_last_error()})")
        try:
            needed = ctypes.c_uint32()
            advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
            info = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(token, _TOKEN_USER, info, needed.value, ctypes.byref(needed)):
                raise WindowsSecurityError(
                    f"E_LOCAL_SECURITY: GetTokenInformation failed ({ctypes.get_last_error()})")
            sid = ctypes.cast(info, ctypes.POINTER(_TOKEN_USER_LAYOUT)).contents.User
            if not sid:
                raise WindowsSecurityError("E_LOCAL_SECURITY: process token has no user SID")
            sid_copy = _sid_bytes(sid)
            out = ctypes.c_void_p()
            if not advapi32.ConvertSidToStringSidW(sid_copy, ctypes.byref(out)):
                raise WindowsSecurityError(
                    f"E_LOCAL_SECURITY: ConvertSidToStringSidW failed ({ctypes.get_last_error()})")
            try:
                sid_string = ctypes.c_wchar_p(out.value).value
            finally:
                kernel32.LocalFree(out)
            if not sid_string:
                raise WindowsSecurityError("E_LOCAL_SECURITY: ConvertSidToStringSidW returned no SID string")
            _user_sid = {"sid": sid_copy, "sid_string": sid_string}
        finally:
            kernel32.CloseHandle(token)
    return _user_sid


def _system_sid_bytes() -> ctypes.Array:
    advapi32 = _load_apis()["advapi32"]
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(SYSTEM_SID, ctypes.byref(sid)):
        raise WindowsSecurityError(
            f"E_LOCAL_SECURITY: ConvertStringSidToSidW(SYSTEM) failed ({ctypes.get_last_error()})")
    try:
        return _sid_bytes(sid)
    finally:
        _load_apis()["kernel32"].LocalFree(sid)


def _protect_ctypes(path: Path, *, is_dir: bool | None = None) -> None:
    """Set a protected DACL granting full control to the current user and SYSTEM only."""
    advapi32 = _load_apis()["advapi32"]
    user = _current_user()
    system = _system_sid_bytes()
    if is_dir is None:
        is_dir = path.is_dir()
    inheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT if is_dir else _NO_INHERITANCE
    # ACL header (8 bytes) plus each allow ACE (4 header + 4 mask + SID), aligned to 4.
    size = 64 + 8 + advapi32.GetLengthSid(user["sid"]) + 8 + advapi32.GetLengthSid(system)
    acl = ctypes.create_string_buffer(size)
    if not advapi32.InitializeAcl(acl, size, _ACL_REVISION):
        raise WindowsSecurityError(f"E_LOCAL_SECURITY: InitializeAcl failed ({ctypes.get_last_error()})")
    for sid in (user["sid"], system):
        if not advapi32.AddAccessAllowedAceEx(acl, _ACL_REVISION, inheritance, _GENERIC_ALL, sid):
            raise WindowsSecurityError(f"E_LOCAL_SECURITY: AddAccessAllowedAceEx failed ({ctypes.get_last_error()})")
    rc = advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT,
                                        _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                                        None, None, ctypes.cast(acl, ctypes.c_void_p), None)
    if rc:
        raise WindowsSecurityError(f"E_LOCAL_SECURITY: SetNamedSecurityInfoW({path}) failed (winerror {rc})")


def _protect_icacls(path: Path) -> None:
    """Fallback writer when the ctypes path is unavailable; same DACL intent."""
    if path.is_dir():
        grants = [f"*{_current_user()['sid_string']}:(OI)(CI)F", f"*{SYSTEM_SID}:(OI)(CI)F"]
    else:
        grants = [f"*{_current_user()['sid_string']}:F", f"*{SYSTEM_SID}:F"]
    try:
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsSecurityError(f"E_LOCAL_SECURITY: icacls fallback for {path} failed: {exc}") from exc
    if result.returncode != 0:
        raise WindowsSecurityError(
            f"E_LOCAL_SECURITY: icacls fallback for {path} failed ({result.returncode}): {result.stderr.strip()}")


def protect_path(path: Path) -> None:
    """Break DACL inheritance on ``path``; keep only the current user and SYSTEM.

    ctypes first; ``icacls`` only as a fallback writer. Raises on failure so the
    caller can keep the file and report honestly (fail closed).
    """
    path = Path(path)
    try:
        _protect_ctypes(path)
    except WindowsSecurityError:
        # The ctypes path already raised for a real reason; still try the one
        # documented fallback before surfacing the failure to the caller.
        try:
            _protect_icacls(path)
        except WindowsSecurityError:
            raise


def read_dacl(path: Path) -> tuple[bool, list[tuple[str, int, int, int]]]:
    """Read a file's real DACL.

    Returns (inheritance broken, [(sid, access mask, ace type, ace flags)]).
    Raises :class:`WindowsSecurityError` when the descriptor cannot be read or
    has no usable DACL - callers must treat that as "cannot verify", never as
    "healthy" (fail closed).
    """
    path = Path(path)
    advapi32 = _load_apis()["advapi32"]
    descriptor = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT,
                                        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
                                        None, None, None, None, ctypes.byref(descriptor))
    if rc:
        raise WindowsSecurityError(f"E_LOCAL_SECURITY: GetNamedSecurityInfoW({path}) failed (winerror {rc})")
    try:
        control = ctypes.c_ushort()
        revision = ctypes.c_uint32()
        if not advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise WindowsSecurityError(
                f"E_LOCAL_SECURITY: GetSecurityDescriptorControl({path}) failed ({ctypes.get_last_error()})")
        present = ctypes.c_int()
        dacl = ctypes.c_void_p()
        defaulted = ctypes.c_int()
        if not advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                  ctypes.byref(defaulted)):
            raise WindowsSecurityError(
                f"E_LOCAL_SECURITY: GetSecurityDescriptorDacl({path}) failed ({ctypes.get_last_error()})")
        if not present.value:
            raise WindowsSecurityError(f"E_LOCAL_SECURITY: {path} has no DACL")
        if not dacl.value:
            # A NULL DACL grants everyone full control; never report it as healthy.
            raise WindowsSecurityError(f"E_LOCAL_SECURITY: {path} has a NULL DACL (everyone full control)")
        info = _ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise WindowsSecurityError(
                f"E_LOCAL_SECURITY: GetAclInformation({path}) failed ({ctypes.get_last_error()})")
        entries: list[tuple[str, int, int, int]] = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise WindowsSecurityError(f"E_LOCAL_SECURITY: GetAce({path}) failed ({ctypes.get_last_error()})")
            base = ace.value
            header = _ACE_HEADER.from_buffer_copy(ctypes.string_at(base, ctypes.sizeof(_ACE_HEADER)))
            mask = int.from_bytes(ctypes.string_at(base + 4, 4), "little")
            sid_out = ctypes.c_void_p()
            if not advapi32.ConvertSidToStringSidW(ctypes.cast(base + 8, ctypes.c_void_p), ctypes.byref(sid_out)):
                raise WindowsSecurityError(
                    f"E_LOCAL_SECURITY: ConvertSidToStringSidW({path}) failed ({ctypes.get_last_error()})")
            try:
                sid = ctypes.c_wchar_p(sid_out.value).value
            finally:
                _load_apis()["kernel32"].LocalFree(sid_out)
            entries.append((sid, mask, header.AceType, header.AceFlags))
        return bool(control.value & _SE_DACL_PROTECTED), entries
    finally:
        _load_apis()["kernel32"].LocalFree(descriptor)


def _grants_read(mask: int) -> bool:
    return bool(mask & (_GENERIC_READ | _FILE_READ_BITS | _GENERIC_ALL))


def _allowed_read_sids() -> set[str]:
    """Only these principals may keep read access to a secret (CL-30).

    The current process user, LOCAL_SYSTEM and BUILTIN\\Administrators. A
    failure to determine the current user is fatal: without it the allowlist
    cannot be evaluated, so callers must treat the whole audit as failed
    (fail closed) instead of guessing.
    """
    return {SYSTEM_SID, ADMINISTRATORS_SID, _current_user()["sid_string"]}


def audit_path(path: Path) -> tuple[bool | None, list[str]]:
    """Doctor helper: (inheritance broken?, problems).

    ``protected`` is None when the DACL could not be read; unreadable is a
    problem in itself, never a pass.

    CL-30: the check is allowlist based. Any access-allowed ACE that grants
    read to a principal other than the current user, SYSTEM or
    BUILTIN\\Administrators is a problem, whatever the SID is; deny ACEs and
    allow-ACEs without read bits restrict rather than widen, so they are not
    flagged.
    """
    path = Path(path)
    try:
        protected, entries = read_dacl(path)
    except WindowsSecurityError as exc:
        return None, [f"cannot read DACL: {exc}"]
    try:
        allowed = _allowed_read_sids()
    except WindowsSecurityError as exc:
        return None, [f"cannot determine the current user SID: {exc}"]
    problems = []
    for sid, mask, ace_type, _flags in entries:
        if ace_type != _ACCESS_ALLOWED_ACE_TYPE or not _grants_read(mask):
            continue
        if sid not in allowed:
            name = WELL_KNOWN_SID_NAMES.get(sid, "unrecognized principal")
            problems.append(f"{name} ({sid}) can read this secret")
    return protected, problems
