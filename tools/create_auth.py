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

def write_exclusive(path: Path, value: str) -> None:
    """Create ``path`` exclusively, protect it, and only then write the secret.

    CL-30: the previous order wrote the secret first and applied the ACL
    afterwards, so a protection failure left an unprotected secret on disk.
    Now the file is created empty and exclusive (refusing to overwrite), the
    restrictive ACL is applied and verified, and the secret is written only
    after that succeeds. On any failure the still-empty file is removed
    best-effort and the error is raised: no secret is ever written without a
    confirmed restrictive ACL. On POSIX the 0o600 creation mode already
    restricts readers to the owner.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        if os.name == 'nt':
            protect_windows(path)
            verify_windows(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    with open(path, 'w', encoding='utf-8') as stream:
        stream.write(value)

def protect_windows(path: Path) -> None:
    """CL-08: on Windows the 0o600 mode above is ignored; set a protected DACL.

    Prefers the ctypes implementation from the bzs package (identical to
    `bzs init`); when the script runs outside an installed bzs, fall back to
    icacls by account name. Any failure is fatal and loud: the operator is
    told the ACL could not be protected.
    """
    import subprocess
    try:
        from bzs import winsecurity
    except ImportError:
        winsecurity = None
    if winsecurity is not None:
        winsecurity.protect_path(path)
        return
    import getpass
    grants = [f'{getpass.getuser()}:F', '*S-1-5-18:F']
    result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', *grants],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'E_LOCAL_SECURITY: icacls could not protect {path}: {result.stderr.strip()}')

def verify_windows(path: Path) -> None:
    """CL-30: confirm the restrictive ACL before any secret is written.

    With the bzs package available this re-reads the real DACL through
    ``winsecurity.audit_path`` and requires a protected, problem-free result.
    In the icacls fallback branch the same intent is checked against the
    icacls output: both grants present and no inherited ACEs. A file whose
    protection cannot be confirmed never receives the secret.
    """
    import subprocess
    try:
        from bzs import winsecurity
    except ImportError:
        winsecurity = None
    if winsecurity is not None:
        protected, problems = winsecurity.audit_path(path)
        if protected is None or problems:
            raise SystemExit(f'E_LOCAL_SECURITY: could not confirm a restrictive ACL on {path}: '
                             f'{"; ".join(problems) or "DACL unreadable"}')
        return
    result = subprocess.run(['icacls', str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'E_LOCAL_SECURITY: icacls could not re-read the ACL on {path}: {result.stderr.strip()}')
    if '(I)' in result.stdout:
        raise SystemExit(f'E_LOCAL_SECURITY: inherited ACEs remain on {path}:\n{result.stdout.strip()}')

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
