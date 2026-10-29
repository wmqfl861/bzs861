"""Make the new native Windows Python gate resistant to missing/skipped tests."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

spec = importlib.util.spec_from_file_location('windows_report', Path(__file__).parents[2]/'tools'/'verify_windows_python.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('bad', [None, 'missing', 'duplicate', 'skipped', 'failure', 'error', 'too-few'])
def test_windows_evidence_gate(tmp_path, bad):
    root = ET.Element('testsuite')
    for name in sorted(module.REQUIRED):
        ET.SubElement(root, 'testcase', {'name':name,'classname':'test'})
    if bad == 'missing': root.remove(root[-1])
    if bad == 'duplicate': root.append(root[-1])
    if bad in {'failure', 'error', 'skipped'}: ET.SubElement(root[-1], bad)
    path=tmp_path/'results.xml';ET.ElementTree(root).write(path)
    if bad:
        with pytest.raises(ValueError): module.verify(path, minimum=100 if bad == 'too-few' else 1)
    else:
        assert module.verify(path,minimum=1)['passed'] == len(module.REQUIRED)
