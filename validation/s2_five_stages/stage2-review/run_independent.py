import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path('/workspace/scratch/80f41eb198a3')
WORK = ROOT / 'bzs-five-stage-review/capacity'
REPO = ROOT / 'bzs861-capacity'
OLD = ROOT / 'bzs861/build-s2-five-stages-author/libbzs_core.a'
NEW = REPO / 'build-s2-capacity-author/libbzs_core.a'
SOURCE = WORK / 'review_retention.cpp'
TAG = 'independent-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


inputs = [Path(__file__), SOURCE, OLD, NEW]
inputs += sorted((WORK / 'baseline-headers/include/bzs').glob('*.hpp'))
inputs += sorted((REPO / 'include/bzs').glob('*.hpp'))
inputs += [REPO / name for name in ['src/retention.cpp', 'src/store.cpp', 'src/batch.cpp', 'src/map.cpp', 'src/crawl.cpp', 'src/service.cpp', 'src/publication.cpp', 'src/web.cpp', 'tests/retention.cpp', 'CMakeLists.txt']]
before = {str(path): digest(path) for path in inputs}
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
start = time.monotonic()
commands = []
results = []
log_path = WORK / (TAG + '.log')
record_path = WORK / (TAG + '-command.json')
if record_path.exists():
    raise RuntimeError('Refusing to replace previous command record')
with log_path.open('x') as log:
    def run(argv):
        log.write('COMMAND ' + json.dumps([str(value) for value in argv], ensure_ascii=False) + '\n')
        log.flush()
        result = subprocess.run(argv, cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log.write(result.stdout)
        log.write('EXIT ' + str(result.returncode) + '\n')
        log.flush()
        print(result.stdout, end='', flush=True)
        commands.append({'command': [str(value) for value in argv], 'exit_code': result.returncode})
        return result

    old_binary = WORK / 'review-retention-schema8-v1'
    new_binary = WORK / 'review-retention-schema9-v1'
    common = ['c++', '-std=c++20', '-g', '-Wall', '-Wextra', '-Wpedantic']
    libs = ['-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-pthread']
    built_old = run(common + ['-DBZS_RETENTION_PREPARE', '-I', WORK / 'baseline-headers/include', SOURCE, OLD] + libs + ['-o', old_binary])
    built_new = run(common + ['-I', REPO / 'include', SOURCE, NEW] + libs + ['-o', new_binary])
    if built_old.returncode == 0 and built_new.returncode == 0:
        for scenario in ['reads', 'robots']:
            with tempfile.TemporaryDirectory(prefix='bzs-retention-review-') as temporary:
                prepared = run([old_binary, 'prepare', scenario, temporary])
                upgraded = run([new_binary, 'upgrade', scenario, temporary]) if prepared.returncode == 0 else None
                results.append({'scenario': scenario, 'schema8_preparation_exit': prepared.returncode, 'schema9_upgrade_exit': upgraded.returncode if upgraded else None, 'passed': prepared.returncode == 0 and upgraded is not None and upgraded.returncode == 0})

after = {str(path): digest(path) for path in inputs}
record = {
    'started_at': started,
    'duration_s': round(time.monotonic() - start, 3),
    'cwd': str(REPO),
    'inputs': before,
    'inputs_unchanged': before == after,
    'changed_inputs': {path: {'before': before[path], 'after': after[path]} for path in before if before[path] != after[path]},
    'commands': commands,
    'scenarios': results,
    'log': log_path.name,
    'log_sha256': digest(log_path),
    'all_commands_succeeded': all(command['exit_code'] == 0 for command in commands),
    'temporary_databases_removed': True,
    'scope': 'Reviewer directly links the final schema8 Stage1 library to generate two real old databases using public APIs, then opens them with the candidate schema9 library. No database mutation SQL: the helper rejects all statements sqlite3_stmt_readonly does not confirm. Fake WorkerPort only; no network or models. This scoped review does not replace repository gates, true HTTP process tests, independent human approval, or original package acceptance.'
}
with record_path.open('x') as file:
    json.dump(record, file, ensure_ascii=False, indent=2)
    file.write('\n')
print(json.dumps({'record': str(record_path), 'scenarios': results, 'inputs_unchanged': before == after}, ensure_ascii=False), flush=True)
raise SystemExit(0 if record['all_commands_succeeded'] and record['inputs_unchanged'] and len(results) == 2 else 1)
