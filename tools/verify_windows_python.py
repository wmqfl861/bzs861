#!/usr/bin/env python3
"""Reject missing, skipped or failed Windows Python/process-tree coverage."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

REQUIRED = {
    'test_stop_owned_kills_the_whole_real_tree',
    'test_unrelated_process_survives_a_tree_stop',
    'test_stop_owned_preserves_record_after_root_wait',
    'test_stop_owned_preserves_record_when_root_exited_before_wait',
    'test_pid_reused_during_snapshot_enumeration_cannot_redirect_signal',
    'test_host_limit_held_under_real_fetch_churn',
    'test_internal_diagnostic_does_not_leak_exception_text',
    'test_cli_auth_check_is_readonly_and_does_not_expose_digests',
}


def verify(path: Path, minimum: int = 1533) -> dict:
    cases = list(ET.parse(path).getroot().iter('testcase'))
    identities = [(c.get('classname'), c.get('name')) for c in cases]
    if len(cases) < minimum or len(set(identities)) != len(cases):
        raise ValueError('Missing or duplicate Windows Python cases')
    if not REQUIRED.issubset(c.get('name') for c in cases):
        raise ValueError('Required Windows safety cases missing')
    for case in cases:
        if any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')):
            raise ValueError('Windows Python cases failed or were skipped')
    return {'passed': len(cases), 'failed': 0, 'skipped': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(verify(args.path))
