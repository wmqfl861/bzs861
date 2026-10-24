"""Use real git enumeration and real file drift, without changing root files."""
from contextlib import redirect_stdout
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

OUT = Path(__file__).resolve().parent
OLD = Path('/tmp/bzs-recorder-independent/snapshot')
PREFIX = 'validation/claude_continuation_20261024/'
checks = 0


def check(value, reason):
    global checks
    checks += 1
    if not value:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(label, snapshot, should_bind):
    with tempfile.TemporaryDirectory(prefix='bzs-real-enumeration-') as temp:
        root = Path(temp)
        target = root / PREFIX
        target.mkdir(parents=True)
        for name in ('run.py', 'run_capture.py', 'guard.py', 'summarize_stage.py'):
            shutil.copyfile(snapshot / name, target / name)
        (root / 'product.txt').write_bytes(b'isolated source input')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', 'add', 'product.txt', 'validation'], cwd=root, check=True)
        subprocess.run(['git', '-c', 'user.name=Independent Probe', '-c', 'user.email=probe@example.invalid',
                        'commit', '-qm', 'isolated real enumeration fixture'], cwd=root, check=True)
        spec = spec_from_file_location('enumerated_' + label, target / 'run_capture.py')
        recorder = module_from_spec(spec)
        spec.loader.exec_module(recorder)
        # No enumeration override: __file__, ROOT, OUT, git ls-files and
        # dependency hash reads are the real reviewed implementation.
        initial = recorder.inputs()
        key = PREFIX + 'run.py'
        check((key in initial) == should_bind, label + ': actual dependency membership')
        for name in ('run_capture.py', 'guard.py', 'summarize_stage.py'):
            check(initial[PREFIX + name] == sha(target / name), label + ': existing script remains bound')
        check(initial['product.txt'] == sha(root / 'product.txt'), label + ': production source remains bound')
        if should_bind:
            check(initial[key] == sha(target / 'run.py'), 'new: imported dependency original bytes bound')
        command = [sys.executable, '-c',
                   "from pathlib import Path;p=Path('validation/claude_continuation_20261024/run.py');p.write_bytes(p.read_bytes()+b'\\n# independent dependency drift\\n')"]
        exit_code = 0
        with redirect_stdout(io.StringIO()):
            try:
                recorder.record('dependency-drift', 'child', command)
            except SystemExit as error:
                exit_code = error.code
        check(exit_code == (125 if should_bind else 0), label + ': wrapper acceptance follows real dependency change')
        folder = target / 'dependency-drift'
        record = json.loads((folder / 'child-command.json').read_bytes())
        check(record['exit_code'] == 0, label + ': child itself succeeded')
        check(record['source_inputs_unchanged'] != should_bind, label + ': metadata records dependency sensitivity')
        check(record['log_sha256'] == sha(folder / 'child.log'), label + ': command log still bound')
        shutil.copytree(folder, OUT / ('enumeration-' + label))
        return {'label': label, 'recorder_sha256': sha(snapshot / 'run_capture.py'),
                'dependency_in_manifest': key in initial, 'wrapper_exit': exit_code,
                'source_inputs_unchanged': record['source_inputs_unchanged']}


if __name__ == '__main__':
    results = [run_case('prior', OLD, False), run_case('corrected', OUT / 'snapshot', True)]
    value = {'passed': True, 'checks': checks, 'cases': results, 'scope': 'Unmodified inputs() enumerated real isolated Git repositories; only fixture run.py was changed by successful child commands. Root was read only.'}
    with (OUT / 'enumeration-execution.json').open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    print(json.dumps(value, indent=2))
