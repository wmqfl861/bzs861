from pathlib import Path
import hashlib
import importlib.util
import json
import os
import stat
import sys

ROOT = Path('/workspace/scratch/e2371129386d/bzs861')
OUT = ROOT / 'validation/claude_followup_20261024/p2-5'
WORK = Path('/tmp/bzs-p2-5-final-release-benchmarks')
spec = importlib.util.spec_from_file_location('audit_recorder', ROOT / 'validation/claude_followup_20261024/run.py')
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
assert recorder.inputs() == json.loads((OUT / 'execution-inputs.json').read_text())['files']
assert json.loads((OUT / 'asan-command.json').read_text())['exit_code'] == 0
WORK.mkdir(exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

recorder.record('p2-5', 'configure-final-release', ['cmake', '--preset', 'release', '-DBZS_PYTHON=' + str(ROOT / '.venv/bin/python'), '-DBZS_REQUIRE_PYTHON_TESTS=ON'], build=True)
recorder.record('p2-5', 'build-final-release', ['cmake', '--build', 'build-release', '--target', 'bzs_core', '-j2'], build=True)
library = ROOT / 'build-release/libbzs_core.a'
library_hash = digest(library)
items = []
for name, filename, expected in [
    ('original', 'scan_blocking_original.cpp', '531278f68e06bbe09c4705eacf83c5a5799e5a9ec123c1d7a7d443dae8c3b618'),
    ('negative', 'scan_blocking_negative.cpp', 'fe1409cd7c011999cabb80a807a47fcb878f4256b07a51cb0ef0f4d86134d7d6'),
]:
    source = OUT / 'benchmark-inputs' / filename
    assert digest(source) == expected
    if name == 'original':
        assert digest(Path('/tmp/bzs-review/docs/reviews/claude-2026-10-24/scan_blocking.cpp')) == expected
    binary = WORK / ('scan_blocking_' + name)
    assert not binary.exists(), 'Refusing to overwrite an earlier benchmark binary'
    recorder.record('p2-5', 'benchmark-' + name + '-compile', [
        'g++', '-std=c++20', '-O2', '-I', 'include', str(source), str(library),
        '-lsqlite3', '-lcrypto', '-ljson-c', '-lxml2', '-pthread', '-o', str(binary),
    ])
    assert binary.read_bytes()[:4] == b'\x7fELF'
    binary_hash = digest(binary)
    mode_before = stat.S_IMODE(binary.stat().st_mode)
    binary.chmod(mode_before | 0o111)
    assert digest(binary) == binary_hash
    result = recorder.record('p2-5', 'benchmark-' + name, [str(binary), '2000', '204800'], timeout=900)
    assert digest(source) == expected and digest(binary) == binary_hash and digest(library) == library_hash
    items.append({
        'name': name, 'source': str(source.relative_to(ROOT)), 'source_sha256': expected,
        'binary': str(binary), 'binary_sha256': binary_hash,
        'mode_before': oct(mode_before), 'mode_after': oct(stat.S_IMODE(binary.stat().st_mode)),
        'command': 'benchmark-' + name + '-command.json', 'log': result['log'],
        'log_sha256': result['log_sha256'], 'exit_code': result['exit_code'],
        'output_lines': (OUT / result['log']).read_text().splitlines(),
    })

value = {
    'stage_inputs_sha256': digest(OUT / 'execution-inputs.json'),
    'release_library_sha256': library_hash,
    'documents': 2000, 'requested_body_bytes': 204800,
    'runs': items,
    'limitations': [
        'One observation per executable on this machine; no general ratio, SLA or timing regression threshold.',
        'The unchanged original absent-positive workload can be excluded by the P1-3 substring index. This does not isolate the lowercase patch.',
        'The all-NOT variant changes only the two queries and deliberately retains bounded scan and Store mutex blocking.',
        'The patch caches ASCII lowering only inside matches(); scoring, per-leaf scans and the Store-wide lock remain.',
        'The substring field limit counts distinct grams, not body codepoints or bytes.',
    ],
}
with (OUT / 'benchmark-results.json').open('x') as stream:
    json.dump(value, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps(value, ensure_ascii=False, indent=2))
