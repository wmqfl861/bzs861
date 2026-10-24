import hashlib
import json
import pathlib
import sys
import time

import pytest

root = pathlib.Path('/workspace/scratch/e2371129386d/bzs861-next-retry')
out = pathlib.Path('/tmp/bzs-independent-next-retry')
inputs = sorted((root / 'python/bzs').rglob('*.py')) + [
    root / 'python/tests/test_sources.py', root / 'python/tests/test_probe.py',
    root / 'python/tests/fakeweb.py', out / 'test_worker_independent.py', pathlib.Path(__file__)
]
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
before = {str(path): sha(path) for path in inputs}
args = ['-q', str(out / 'test_worker_independent.py'),
        str(root / 'python/tests/test_probe.py') + '::test_probe_reports_bounded_retry_after_and_never_resumes_a_stopped_source',
        str(root / 'python/tests/test_probe.py') + '::test_shared_service_reservations_and_stops_survive_completed_resume',
        str(root / 'python/tests/test_probe.py') + '::test_resume_continues_only_unreserved_work_with_the_same_quota']
started = time.monotonic()
result = pytest.main(args)
loaded = {}
for name, module in sorted(sys.modules.items()):
    filename = getattr(module, '__file__', None)
    if filename and name.startswith(('bzs.', 'tests.')):
        path = pathlib.Path(filename).resolve()
        loaded[name] = {'path': str(path), 'sha256': sha(path)}
record = {'python': sys.executable, 'pytest_args': args, 'exit_code': int(result),
          'elapsed_seconds': round(time.monotonic() - started, 3), 'before': before,
          'after': {str(path): sha(path) for path in inputs}, 'loaded_modules': loaded}
(out / 'python-correction-v1.json').write_text(json.dumps(record, indent=2) + '\n')
raise SystemExit(result)
