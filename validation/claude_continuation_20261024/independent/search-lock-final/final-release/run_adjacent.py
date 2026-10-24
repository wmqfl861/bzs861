#!/usr/bin/env python3
"""Run the authorized single old-to-final Release observation, without retries."""
from pathlib import Path
import subprocess
import sys

base = Path('/tmp/bzs-continuation-final-search-lock')
out = base / 'final-release'
root = Path('/workspace/scratch/e2371129386d/bzs861')
for variant in ('old', 'new'):
    print('RUN', variant, 'Release: 2000 x 204800, unchanged NOT/4NOT driver', flush=True)
    argv = [
        sys.executable, str(base / 'capture.py'),
        '--repo', str(root), '--out', str(out / 'evidence'),
        '--label', 'run-performance-' + variant,
        '--input', str(out / variant / 'include'),
        '--input', str(out / variant / 'libbzs_core.a'),
        '--input', str(out / variant / 'scan-negative'),
        '--input', str(out / 'scan_blocking_negative.cpp'),
        '--input', str(Path(__file__).resolve()),
        '--input', str(root / 'build-release/libbzs_core.a'),
        '--input', str(root / 'validation/claude_continuation_20261024/final-verification/execution-inputs.json'),
        '--artifact', str(out / variant / 'scan-negative'),
        '--', str(out / variant / 'scan-negative'), '2000', '204800',
    ]
    result = subprocess.run(argv)
    if result.returncode:
        sys.exit(result.returncode)
