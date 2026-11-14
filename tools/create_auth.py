#!/usr/bin/env python3
"""Create local token files without overwriting existing secrets. No network use."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

def _load_winsecurity():
    """Return the repository's winsecurity module (CL-30 / RF2).

    Every run must protect and verify through the same SID/DACL
    implementation; the old icacls-by-account-name fallback could not prove a
    whitelist DACL. Prefers an importable bzs package; otherwise loads
    python/bzs/winsecurity.py directly from the repository tree (the module
    has no intra-package imports). Returns None when neither works; callers
    fail closed instead of writing an unverified secret.
    """
    try:
        from bzs import winsecurity
        return winsecurity
    except ImportError:
        pass
    import importlib.util
    candidate = Path(__file__).resolve().parents[1] / 'python' / 'bzs' / 'winsecurity.py'
    if not candidate.is_file():
        return None
    spec = importlib.util.spec_from_file_location('bzs._winsecurity_standalone', candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def write_exclusive(path: Path, value: str) -> None:
    """Create ``path`` exclusively, protect it, and only then write the secret.

    CL-30: the previous order wrote the secret first and applied the ACL
    afterwards, so a protection failure left an unprotected secret on disk.
    Now the file is created empty and exclusive (refusing to overwrite), the
    restrictive ACL is applied and verified, and the secret is written only
    after that succeeds. On any failure the still-empty file is removed
    best-effort and the error is raised; no secret is ever written without a
    confirmed restrictive ACL. On POSIX the 0o600 creation mode already
    restricts readers to the owner.

    RF1: the secret is written through the same descriptor that was created
    exclusively and verified - never by reopening the path - so a directory
    entry swapped between verification and the write can neither receive the
    secret nor be overwritten.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == 'nt':
            protect_windows(path)
            verify_windows(path)
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(value)

def protect_windows(path: Path) -> None:
    """CL-08: on Windows the 0o600 mode above is ignored; set a protected DACL.

    Uses the repository's winsecurity implementation (ctypes, icacls only as
    its internal fallback writer). Any failure is fatal and loud: the operator
    is told the ACL could not be protected.
    """
    module = _load_winsecurity()
    if module is None:
        raise SystemExit(f'E_LOCAL_SECURITY: cannot load the ACL implementation to protect {path}; '
                         'refusing to write the secret (fail closed)')
    module.protect_path(path)

def verify_windows(path: Path) -> None:
    """CL-30: confirm the restrictive ACL before any secret is written.

    Re-reads the real DACL through ``winsecurity.audit_path`` (SID-level,
    both for the ctypes protection route and its icacls fallback writer) and
    requires ``protected is True`` with zero problems (RF3: an un-protected
    DACL is a failure, not a pass). A file whose protection cannot be
    confirmed never receives the secret.
    """
    module = _load_winsecurity()
    if module is None:
        raise SystemExit(f'E_LOCAL_SECURITY: cannot load the ACL implementation to verify {path}; '
                         'refusing to write the secret (fail closed)')
    protected, problems = module.audit_path(path)
    if protected is not True or problems:
        raise SystemExit(f'E_LOCAL_SECURITY: could not confirm a restrictive ACL on {path}: '
                         f'{"; ".join(problems) or "DACL is not protected"}')

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('.local/auth.json'))
    p.add_argument('--subject', default='local-user')
    p.add_argument('--admin', action='store_true')
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', args.subject):
        p.error('subject must contain 1..128 letters/digits/._-')
    token_path = args.output.with_suffix('.token')
    if args.output.exists() or token_path.exists():
        p.error('refusing to overwrite an existing auth/token file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        args.output.parent.chmod(0o700)
    token = secrets.token_hex(32)
    config = {'tokens': [{'subject': args.subject, 'admin': args.admin,
                         'sha256': hashlib.sha256(token.encode()).hexdigest()}]}
    write_exclusive(token_path, token + '\n')
    try:
        write_exclusive(args.output, json.dumps(config, indent=2) + '\n')
    except Exception:
        token_path.unlink(missing_ok=True)
        raise
    print(f'Authentication digest file: {args.output}')
    print(f'Private bearer token file: {token_path} (never commit or paste this token)')
    print('Set SEARCH_API_TOKEN from the token file before using searchctl.')

if __name__ == '__main__':
    main()
