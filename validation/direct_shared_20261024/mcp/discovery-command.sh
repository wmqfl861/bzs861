.venv/bin/python - <<'PYDISCOVERY'
import asyncio
import importlib.util
import json
from pathlib import Path
from bzs import mcp_server

root = Path('/workspace/scratch/133513441190/bzs861')
evidence = root.parent / 'direct-read-mcp-evidence'
spec = importlib.util.spec_from_file_location('bzs._read_mcp_baseline', evidence / 'baseline-mcp_server.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)

async def inspect():
    results = []
    for admin in (False, True):
        old_tools = {tool.name: tool.model_dump(mode='json', by_alias=True) for tool in await baseline.build_server(admin_tools=admin).list_tools()}
        new_tools = {tool.name: tool.model_dump(mode='json', by_alias=True) for tool in await mcp_server.build_server(admin_tools=admin).list_tools()}
        assert old_tools.keys() == new_tools.keys()
        changed_descriptions = []
        for name in old_tools:
            old = dict(old_tools[name])
            new = dict(new_tools[name])
            old_description = old.pop('description')
            new_description = new.pop('description')
            assert old == new, name
            if old_description != new_description:
                changed_descriptions.append(name)
        assert changed_descriptions == ['read_url']
        results.append({'admin_tools': admin, 'tool_count': len(new_tools), 'tool_names': list(new_tools), 'schemas_and_annotations_identical': True, 'description_changes': changed_descriptions})
    return results

results = asyncio.run(inspect())
(evidence / 'discovery-review.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
PYDISCOVERY
