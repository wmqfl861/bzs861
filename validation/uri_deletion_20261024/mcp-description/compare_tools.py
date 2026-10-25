"""Compare MCP's advertised contracts before/after the description-only fix."""
import asyncio
import ast
import hashlib
import importlib
import json
import pathlib
import subprocess
import sys
import types

ROOT = pathlib.Path('/workspace/scratch/133513441190/bzs861')
OUT = pathlib.Path(__file__).parent
PATH = 'python/bzs/mcp_server.py'
BASE = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
BEFORE = subprocess.check_output(['git', 'show', f'{BASE}:{PATH}'], cwd=ROOT)
AFTER = (ROOT / PATH).read_bytes()
TARGETS = {'web_search', 'start_web_search', 'list_sources'}

def normalized_ast(data):
    tree = ast.parse(data)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'INSTRUCTIONS' for t in node.targets):
            node.value = ast.Constant(value='<instructions>')
        if isinstance(node, ast.AsyncFunctionDef) and node.name in TARGETS:
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg == 'description':
                            keyword.value = ast.Constant(value='<description>')
    return ast.dump(tree, include_attributes=False)

async def main():
    assert normalized_ast(BEFORE) == normalized_ast(AFTER)
    baseline = types.ModuleType('bzs._baseline_mcp_description')
    baseline.__package__ = 'bzs'
    exec(compile(BEFORE, PATH, 'exec'), baseline.__dict__)
    current = importlib.import_module('bzs.mcp_server')
    async def no_core_calls(*args, **kwargs):
        raise AssertionError('Tool discovery must not contact the Core')
    baseline._call = current._call = no_core_calls
    results = []
    for admin_tools in (False, True):
        prior = await baseline.build_server(admin_tools=admin_tools).list_tools()
        actual = await current.build_server(admin_tools=admin_tools).list_tools()
        old = {tool.name: tool.model_dump() for tool in prior}
        new = {tool.name: tool.model_dump() for tool in actual}
        assert old.keys() == new.keys()
        changed = []
        for name in old:
            if old[name].get('description') != new[name].get('description'):
                changed.append(name)
            old[name].pop('description', None)
            new[name].pop('description', None)
        assert old == new, 'Tool schema, annotations or other non-description fields changed'
        assert set(changed) == TARGETS
        results.append({'admin_tools': admin_tools, 'tool_count': len(new),
                        'changed_descriptions': sorted(changed),
                        'schemas_annotations_and_names_identical': True})
    source_config = json.loads((ROOT / 'config/sources.json').read_text())
    sources = source_config['sources']
    assert len(sources) == 17 and not any(s['enabled'] for s in sources)
    report = {'baseline_commit': BASE, 'python': sys.version,
              'source_path': PATH, 'before_sha256': hashlib.sha256(BEFORE).hexdigest(),
              'after_sha256': hashlib.sha256(AFTER).hexdigest(),
              'description_only_ast': True, 'discovery_core_calls': 0,
              'source_requests': 0, 'registered_sources': len(sources),
              'enabled_sources': 0, 'results': results}
    (OUT / 'tool-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

asyncio.run(main())
