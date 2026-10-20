import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path('/workspace/scratch/80f41eb198a3')
WORK = ROOT / 'bzs-five-stage-review/final-upgrade'
ORIGINAL = ROOT / 'bzs-five-stage-review/capacity'
REPO = ROOT / 'bzs861'
OLD = REPO / 'build-s2-five-stages-author/libbzs_core.a'
NEW = REPO / 'build-s2-five-stages/libbzs_core.a'
OLD_SOURCE = ORIGINAL / 'review_retention.cpp'
SOURCE = WORK / 'review_retention_schema11.cpp'
MANIFEST = REPO / 'validation/s2_five_stages/final-v1-execution-inputs.json'
TAG = 'final-schema8-to11-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


frozen = json.loads(MANIFEST.read_text())
for relative, expected in frozen['files'].items():
    assert digest(REPO / relative) == expected, relative
assert digest(OLD) == '7b6a7fbb9b9ecd904b2d87697914fccf17bab6473a8d1c42c9cb816cf49dc0a5'
assert digest(OLD_SOURCE) == '5f4d0780c031dec23a92ac19d6b71da0a131202760cc2f2453dc0afec342e0af'
inputs = [Path(__file__), WORK / 'preparation.json', SOURCE, OLD_SOURCE, OLD, NEW, MANIFEST,
          ORIGINAL / 'independent-review.json', ORIGINAL / 'independent-v1-command.json']
inputs += sorted((ORIGINAL / 'baseline-headers/include/bzs').glob('*.hpp'))
inputs += [REPO / relative for relative in frozen['files']]
before = {str(path): digest(path) for path in inputs}
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
start = time.monotonic()
commands = []
results = []
binaries = []
log_path = WORK / (TAG + '.log')
record_path = WORK / (TAG + '-command.json')
assert not record_path.exists()
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

    old_binary = WORK / 'review-retention-schema8-final-v1'
    new_binary = WORK / 'review-retention-schema11-final-v1'
    common = ['c++', '-std=c++20', '-g', '-Wall', '-Wextra', '-Wpedantic']
    libs = ['-lsqlite3', '-lcrypto', '-lxml2', '-ljson-c', '-pthread']
    built_old = run(common + ['-DBZS_RETENTION_PREPARE', '-I', ORIGINAL / 'baseline-headers/include', OLD_SOURCE, OLD] + libs + ['-o', old_binary])
    built_new = run(common + ['-I', REPO / 'include', SOURCE, NEW] + libs + ['-o', new_binary])
    if built_old.returncode == 0 and built_new.returncode == 0:
        binaries = [old_binary, new_binary]
        for scenario in ['reads', 'robots']:
            with tempfile.TemporaryDirectory(prefix='bzs-final-upgrade-') as temporary:
                prepared = run([old_binary, 'prepare', scenario, temporary])
                upgraded = run([new_binary, 'upgrade', scenario, temporary]) if prepared.returncode == 0 else None
                def assertions(result):
                    found = re.search(r'independent retention assertions=(\d+), failures=0', result.stdout) if result else None
                    return int(found[1]) if found else None
                results.append({'scenario': scenario, 'schema8_preparation_exit': prepared.returncode,
                                'schema11_upgrade_exit': upgraded.returncode if upgraded else None,
                                'preparation_assertions': assertions(prepared), 'candidate_assertions': assertions(upgraded),
                                'passed': prepared.returncode == 0 and upgraded is not None and upgraded.returncode == 0})

after = {str(path): digest(path) for path in inputs}
record = {
    'started_at': started,
    'duration_s': round(time.monotonic() - start, 3),
    'cwd': str(REPO),
    'inputs': before,
    'inputs_unchanged': before == after,
    'changed_inputs': {path: {'before': before[path], 'after': after[path]} for path in before if before[path] != after[path]},
    'root_execution_input_manifest': str(MANIFEST),
    'root_execution_input_sha256': digest(MANIFEST),
    'root_execution_input_files': len(frozen['files']),
    'compiled_binaries_sha256': {str(path): digest(path) for path in binaries},
    'commands': commands,
    'scenarios': results,
    'log': log_path.name,
    'log_sha256': digest(log_path),
    'all_commands_succeeded': all(command['exit_code'] == 0 for command in commands),
    'temporary_databases_removed': True,
    'scenario_counting': 'This reruns the same two original retention review scenarios against final integrated schema11; it adds no distinct product acceptance cases. The earlier119 assertions are not counted again as new coverage.',
    'scope': 'Genuine original schema8 library and unchanged original producer source create two databases through public APIs. The final integrated schema11 library opens them using the original reviewer checks with only schema expectations and output labels changed. SQL helper requires sqlite3_stmt_readonly. Fake in-process WorkerPort only; zero actual HTTP/model calls. This closes the specific schema8->9->10->11 integration regression risk; it does not replace repository gates or confer full product acceptance.'
}
with record_path.open('x') as file:
    json.dump(record, file, ensure_ascii=False, indent=2)
    file.write('\n')
print(json.dumps({'record': str(record_path), 'scenarios': results, 'inputs_unchanged': before == after}, ensure_ascii=False), flush=True)
raise SystemExit(0 if record['all_commands_succeeded'] and record['inputs_unchanged'] and len(results) == 2 and all(s['passed'] for s in results) else 1)
