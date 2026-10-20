"""Record an authorized validation command with optional frozen execution inputs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
OUT = REPO/'validation/s2_hardening'
# User-facing prose may be finalized after tests. Contract fixtures, schemas,
# planning execution data, tools and all code/tests/configuration stay frozen.
POST_VALIDATION_DOCUMENTS = {
    'README.md','HANDOFF_START_HERE.md','docs/DEVELOPMENT_STATUS.md','docs/implementation_status.json',
    'docs/GITHUB_HANDOFF.md','docs/API.md','docs/SECURITY.md','docs/SITE_CRAWLING.md','docs/SITE_DISCOVERY.md','docs/SITE_PUBLICATION.md','docs/CONFIGURATION.md',
    'docs/handoff/NEXT_AGENT.md','docs/handoff/S2_HARDENING_20261019.md',
    'docs/planning/development_blueprint_v1.2.md','docs/planning/README.md',
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def execution_inputs():
    files = subprocess.check_output(['git','ls-files','-z'],cwd=REPO).split(b'\0')
    files += subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=REPO).split(b'\0')
    rels = {value.decode() for value in files if value and not value.startswith(b'validation/')}
    rels -= POST_VALIDATION_DOCUMENTS
    rels.update({'validation/s2_hardening/launcher_smoke.py','validation/s2_hardening/run_validation.py'})
    return {rel:sha(REPO/rel) for rel in sorted(rels)}

def binaries():
    names = ['searchd','searchctl','bzs_tests','bzs_batch_tests','bzs_map_tests','bzs_crawl_tests','bzs_site_publication_tests','bzs_contract_tests','CTestTestfile.cmake']
    return {str(path.relative_to(REPO)):sha(path)
            for directory in ('build-s2-hardening-review','build-s2-hardening-asan','build-s1-review')
            for name in names if (path:=REPO/directory/name).is_file()}

parser = argparse.ArgumentParser()
parser.add_argument('--name')
parser.add_argument('--cwd', default=str(REPO))
parser.add_argument('--asan', action='store_true')
parser.add_argument('--freeze-inputs', action='store_true')
parser.add_argument('--inputs', default='validation/s2_hardening/execution_inputs.json')
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
OUT.mkdir(parents=True,exist_ok=True)
manifest = REPO/args.inputs
if args.freeze_inputs:
    record = {'schema_version':1,'project':'bzs861','version':'0.1.5',
              'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope':'Frozen runtime/build/test/configuration inputs, including the launcher smoke script; separately identified prose may be finalized after execution.',
              'post_validation_document_paths':sorted(POST_VALIDATION_DOCUMENTS),
              'files':execution_inputs()}
    with manifest.open('x',encoding='utf-8') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({'manifest':str(manifest),'sha256':sha(manifest),'files':len(record['files'])}))
    sys.exit(0)
command = args.command[1:] if args.command[:1] == ['--'] else args.command
if not command or not args.name or '/' in args.name or '\\' in args.name:
    parser.error('a safe record name and command are required')
env = dict(os.environ)
recorded_env = {}
if args.asan:
    recorded_env = {'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1',
                    'UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
    env.update(recorded_env)
frozen = None
if manifest.is_file():
    frozen = json.loads(manifest.read_bytes())
    if execution_inputs() != frozen['files']:
        parser.error('execution inputs differ from the frozen manifest; review and freeze a new named revision before running final gates')
manifest_hash = sha(manifest) if frozen else None
binary_before = binaries()
start = time.monotonic()
started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
log_path = OUT/(args.name+'.log')
with log_path.open('x',encoding='utf-8') as log:
    result = subprocess.run(command,cwd=args.cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
inputs_unchanged = frozen is None or (execution_inputs()==frozen['files'] and sha(manifest)==manifest_hash)
record = {'argv':command,'cwd':args.cwd,'environment_overrides':recorded_env,
          'started_at_utc':started_at,'exit_code':result.returncode,
          'elapsed_seconds':round(time.monotonic()-start,3),'log':log_path.name,
          'log_sha256':sha(log_path),'runner_sha256':sha(Path(__file__)),
          'execution_inputs':{'manifest':args.inputs,'sha256':manifest_hash,'unchanged_before_and_after':inputs_unchanged} if frozen else None,
          'binaries_before_sha256':binary_before,'binaries_after_sha256':binaries()}
with (OUT/(args.name+'-command.json')).open('x',encoding='utf-8') as stream:
    json.dump(record,stream,indent=2)
    stream.write('\n')
print(json.dumps({key:value for key,value in record.items() if key not in ('binaries_before_sha256','binaries_after_sha256')}),flush=True)
print('\n'.join(log_path.read_text(encoding='utf-8',errors='replace').splitlines()[-35:]),flush=True)
if not inputs_unchanged:
    print('Execution inputs changed during the command; this run is not a final verification gate.',file=sys.stderr)
sys.exit(result.returncode or (0 if inputs_unchanged else 125))
