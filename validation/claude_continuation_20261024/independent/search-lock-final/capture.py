#!/usr/bin/env python3
"""Capture final-combination probe commands without changing a source worktree."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_at(path: Path) -> dict[str, str]:
    if path.is_file():
        return {str(path): digest(path)}
    if path.is_dir():
        return {str(p): digest(p) for p in sorted(path.rglob('*')) if p.is_file()}
    raise FileNotFoundError(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--input', type=Path, action='append', default=[])
    parser.add_argument('--artifact', type=Path, action='append', default=[])
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not argv or '/' in args.label or args.label in {'.', '..'}:
        parser.error('A safe label and a command after -- are required')
    args.repo = args.repo.resolve()
    args.out = args.out.resolve()
    if not args.out.is_relative_to(Path('/tmp/bzs-continuation-final-search-lock')):
        parser.error('Evidence must remain in the dedicated /tmp directory')
    args.out.mkdir(parents=True, exist_ok=True)
    record = args.out / (args.label + '.json')
    log = args.out / (args.label + '.log')
    if record.exists() or log.exists():
        raise FileExistsError('Refusing to replace prior execution evidence')

    source_paths = [args.repo / p for p in ('src', 'include', 'apps', 'CMakeLists.txt', 'CMakePresets.json')]

    def sources() -> dict[str, str]:
        values: dict[str, str] = {}
        for path in source_paths:
            values.update(files_at(path))
        return values

    def inputs() -> dict[str, str]:
        values: dict[str, str] = {}
        for path in args.input:
            values.update(files_at(path.resolve()))
        return values

    def artifacts() -> dict[str, str | None]:
        return {str(p.resolve()): digest(p) if p.is_file() else None for p in args.artifact}

    def head() -> str:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.repo, text=True).strip()

    data = {
        'scope': 'Final-combination independent P2-5 replay; exact command and frozen code/input binding',
        'argv': argv,
        'cwd': str(args.repo),
        'utc_started': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'head_before': head(),
        'sources_before': sources(),
        'inputs_before': inputs(),
        'artifacts_before': artifacts(),
        'environment': {key: os.environ[key] for key in ('ASAN_OPTIONS', 'UBSAN_OPTIONS') if key in os.environ},
        'watchdog_seconds': args.timeout,
        'runner_sha256': digest(Path(__file__).resolve()),
    }
    started = time.monotonic()
    timed_out = False
    with log.open('x', encoding='utf-8') as stream:
        try:
            process = subprocess.run(argv, cwd=args.repo, stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
            stream.write('\nCOMMAND WATCHDOG EXPIRED\n')
    data.update(
        exit_code=code,
        process_watchdog_expired=timed_out,
        elapsed_seconds=round(time.monotonic() - started, 3),
        head_after=head(),
        sources_after=sources(),
        inputs_after=inputs(),
        artifacts_after=artifacts(),
        log_sha256=digest(log),
    )
    data['sources_unchanged'] = data['sources_before'] == data['sources_after']
    data['inputs_unchanged'] = data['inputs_before'] == data['inputs_after']
    with record.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: data[key] for key in ('argv', 'exit_code', 'elapsed_seconds', 'sources_unchanged', 'inputs_unchanged')}))
    print(log.read_text()[-10000:])
    return code if data['sources_unchanged'] and data['inputs_unchanged'] else 90


if __name__ == '__main__':
    sys.exit(main())
