import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('audit_sbom',Path(__file__).resolve().parents[2] / 'tools' / 'sbom_report.py')
sbom=importlib.util.module_from_spec(spec);spec.loader.exec_module(sbom)

@pytest.mark.parametrize('raw',['','  ','!!!','MIT!','MIT/','[MIT]','MIT,','MIT WITH Apache-2.0','MIT WITH MIT'])
def test_sbom_does_not_turn_invalid_text_into_an_spdx_expression(raw):
    assert not sbom.parse_spdx_expression(raw)[0]
    assert sbom.classify_license(raw)[0]=='non-spdx-text'

@pytest.mark.parametrize('raw',['Apache-2.0 WITH LLVM-exception','GPL-2.0-only WITH Classpath-exception-2.0'])
def test_sbom_exception_is_not_a_second_license(raw):
    valid,ids,compound=sbom.parse_spdx_expression(raw)
    assert valid
    assert ids==[raw.split()[0]]


@pytest.mark.parametrize("raw,ids,compound", [
    ("mit", ["MIT"], False),
    ("Apache-2.0 WITH llvm-EXCEPTION OR MIT", ["Apache-2.0", "MIT"], True),
    ("MIT AND BSD-3-Clause", ["MIT", "BSD-3-Clause"], True),
])
def test_curated_subset_preserves_license_ids_and_compound_flag(raw, ids, compound):
    assert sbom.parse_spdx_expression(raw) == (True, ids, compound)


@pytest.mark.parametrize("raw", ["MIT AND", "MIT WITH", "MIT OR OR MIT", "MIT with LLVM-exception",
    "MIT WITH LLVM-exception WITH LLVM-exception", "LicenseRef-unknown", "(MIT)", "MIT+",
    "MIT OR " * 300 + "MIT", " " * 4097, None])
def test_unrecognized_or_incomplete_evidence_is_not_promoted(raw):
    assert sbom.parse_spdx_expression(raw) == (False, [], False)
