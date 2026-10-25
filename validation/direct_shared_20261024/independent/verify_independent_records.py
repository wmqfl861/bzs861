#!/usr/bin/env python3
"""Read-only byte/binding audit; does not execute any product or probe."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = HERE / args.output
    if output.parent != HERE or output.exists(): parser.error('Use a new filename directly in the independent directory')
    issues: list[dict] = []
    groups: list[dict] = []
    for manifest in sorted(HERE.glob('*/artifact-sha256.json')):
        folder = manifest.parent
        entries = json.loads(manifest.read_text())
        checked = 0
        for name, value in entries.items():
            path = HERE / name
            expected = value['sha256'] if isinstance(value, dict) else value
            match = path.is_file() and digest(path) == expected
            if isinstance(value, dict) and path.is_file() and 'bytes' in value:
                match = match and path.stat().st_size == value['bytes']
            checked += 1
            if not match: issues.append({'group': folder.name, 'kind': 'artifact_mismatch', 'path': name})
        commands = []
        for command in sorted(folder.glob('*-command.json')):
            record = json.loads(command.read_text())
            log = Path(record['log_path'])
            matches = log.is_file() and digest(log) == record['log_sha256']
            if 'log_bytes' in record and log.is_file(): matches = matches and log.stat().st_size == record['log_bytes']
            if not matches: issues.append({'group': folder.name, 'kind': 'command_log_mismatch', 'path': str(log)})
            before, after = record.get('inputs_before'), record.get('inputs_after')
            if before is not None and after is not None:
                changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
                if changed != sorted(record.get('changed_inputs', [])):
                    issues.append({'group': folder.name, 'kind': 'changed_input_list_mismatch', 'path': str(command)})
            commands.append({'record': command.name, 'record_sha256': digest(command), 'exit_code': record['exit_code'],
                             'execution_record_valid': record.get('execution_record_valid'),
                             'log_sha256_matches': matches, 'changed_inputs': record.get('changed_inputs', [])})
        binary_record = folder / 'binary-binding.json'
        binary = None
        if binary_record.exists():
            binding = json.loads(binary_record.read_text())
            path = Path(binding['path']); matches = path.is_file() and digest(path) == binding['sha256']
            binary = {'sha256': binding['sha256'], 'current_frozen_binary_matches': matches}
            if not matches: issues.append({'group': folder.name, 'kind': 'frozen_binary_mismatch', 'path': str(path)})
            for name, expected in binding.get('linked_libraries', {}).items():
                path = Path(name)
                if not path.is_file() or digest(path) != expected:
                    issues.append({'group': folder.name, 'kind': 'linked_library_changed', 'path': name})
        summary_path = folder / 'summary.json'
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
        groups.append({'group': folder.name, 'artifact_count_checked': checked,
                       'artifact_manifest_sha256': digest(manifest), 'commands': commands, 'binary': binary,
                       'summary_sha256': digest(summary_path) if summary_path.exists() else None,
                       'summary_status': summary.get('status') if summary else None,
                       'cases': summary.get('cases') if summary else None,
                       'checks': summary.get('checks') if summary else None,
                       'failures': summary.get('failures') if summary else None})
    report = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'verifier_sha256': digest(Path(__file__).resolve()),
              'verification_kind': 'read_only_archive_integrity_not_test_execution',
              'scope': 'Closed independent manifests, original command/log bytes, before/after declarations, temporary frozen binaries and linked libraries. Historical failed product runs remain failed; a matching archive is not a product pass.',
              'groups': groups, 'issues': issues, 'archive_integrity_passed': not issues}
    with output.open('x', encoding='utf-8') as stream: json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'groups': len(groups), 'issues': issues, 'archive_integrity_passed': not issues, 'report': str(output)}, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == '__main__':
    raise SystemExit(main())
