"""Prevent silently overwritten pytest/unittest cases from looking green."""
import ast
from pathlib import Path


def test_no_duplicate_test_definitions_in_the_same_scope():
    root = Path(__file__).resolve().parents[2]
    files = sorted((root / "python" / "tests").glob("*.py")) + sorted((root / "tests").glob("*.py"))
    duplicates = []
    for path in files:
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.Module, ast.ClassDef)):
                continue
            names = {}
            for node in scope.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    if node.name in names:
                        duplicates.append(f"{path.relative_to(root)}:{node.lineno}: {node.name} replaces line {names[node.name]}")
                    names[node.name] = node.lineno
    assert not duplicates, "\n".join(duplicates)
