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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(value)

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
