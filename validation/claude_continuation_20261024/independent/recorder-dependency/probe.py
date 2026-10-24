"""Exercise only the frozen recorder's record() in an isolated temporary repo."""
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import io
import json
import subprocess
import sys
import tempfile

OUT = Path(__file__).resolve().parent
checks = 0


def check(value, message):
    global checks
    checks += 1
    if not value:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    spec = spec_from_file_location('reviewed_capture', OUT / 'snapshot/run_capture.py')
    recorder = module_from_spec(spec)
    spec.loader.exec_module(recorder)
    records = []
    with tempfile.TemporaryDirectory(prefix='bzs-recorder-review-') as temp:
        root = Path(temp)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-c', 'user.name=Independent Probe', '-c', 'user.email=probe@example.invalid',
                        'commit', '--allow-empty', '-qm', 'isolated recorder fixture'], cwd=root, check=True)
        product = root / 'product.txt'
        binary = root / 'binary.fixture'
        product.write_bytes(b'product-before')
        binary.write_bytes(b'binary-before')
        recorder.ROOT = root
        recorder.OUT = OUT / 'runs'
        recorder.OUT.mkdir()
        # Enumeration is isolated; child execution, pipe capture, timeout,
        # post-command comparison and immutable record logic stay unchanged.
        recorder.inputs = lambda: {'product.txt': sha(product)}
        recorder.binaries = lambda: {'binary.fixture': sha(binary)}

        def call(stage, code, expected_exit=0, **kwargs):
            raised = None
            with redirect_stdout(io.StringIO()):
                try:
                    recorder.record(stage, 'child', [sys.executable, '-u', '-c', code], **kwargs)
                except SystemExit as error:
                    raised = error.code
            check(raised == (expected_exit or None), stage + ': expected wrapper exit')
            folder = recorder.OUT / stage
            record = json.loads((folder / 'child-command.json').read_bytes())
            check(record['log_sha256'] == sha(folder / record['log']), stage + ': byte hash matches record')
            check(record['execution_inputs_sha256'] == sha(folder / 'execution-inputs.json'), stage + ': input hash matches')
            records.append({'stage': stage, 'wrapper_exit': raised or 0, 'command': record})
            return record, (folder / record['log']).read_bytes()

        success, output = call('success', "import os; os.write(1, b'A'*65536+b'\\xff\\n'); os.write(2, b'B'*65536+b'\\n'); os.write(1,b'end\\n')")
        check(output == b'A'*65536+b'\xff\n'+b'B'*65536+b'\nend\n', 'combined stdout/stderr is complete and byte preserving')
        check(success['exit_code'] == 0 and success['source_inputs_unchanged'], 'success retains acceptance gates')
        check(not success['process_watchdog_expired'], 'success is not timeout')
        marker = root / 'duplicate-command-must-not-start'
        duplicate = False
        try:
            recorder.record('success', 'child', [sys.executable, '-c', f'from pathlib import Path;Path({str(marker)!r}).touch()'])
        except FileExistsError:
            duplicate = True
        check(duplicate and not marker.exists(), 'existing log prevents a second command execution')
        failure, output = call('nonzero', "print('intentional failure'); raise SystemExit(7)", expected_exit=7)
        check(failure['exit_code'] == 7 and output == b'intentional failure\n', 'nonzero child status and output retained')
        timeout, output = call('timeout', "import time;print('before timeout',flush=True);time.sleep(2)", expected_exit=124, timeout=0.2)
        check(timeout['exit_code'] == 124 and timeout['process_watchdog_expired'], 'timeout is rejected as 124')
        check(output.startswith(b'before timeout\n') and output.endswith(b'Audit command process watchdog expired.\n'), 'timeout preserves captured bytes and watchdog marker')
        drift, _ = call('source-drift', "from pathlib import Path;Path('product.txt').write_bytes(b'changed')", expected_exit=125)
        check(drift['exit_code'] == 0 and not drift['source_inputs_unchanged'], 'source drift fails wrapper despite successful child')
        binary_drift, _ = call('binary-drift', "from pathlib import Path;Path('binary.fixture').write_bytes(b'changed')", expected_exit=125)
        check(binary_drift['binaries_before_sha256'] != binary_drift['binaries_after_sha256'], 'non-build binary drift fails wrapper')
        check(binary_drift['source_inputs_unchanged'], 'binary rejection is independent of source drift')
    value = {'passed': True, 'checks': checks, 'scope': 'Five local child processes plus duplicate prevention exercise frozen record() only; isolated enumeration hooks; no production test gates claimed.',
             'recorder_sha256': sha(OUT / 'snapshot/run_capture.py'), 'probe_sha256': sha(Path(__file__)), 'cases': records}
    with (OUT / 'execution.json').open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    print(json.dumps({key: value[key] for key in ('passed', 'checks', 'scope', 'recorder_sha256', 'probe_sha256')}, indent=2))


if __name__ == '__main__':
    run()
