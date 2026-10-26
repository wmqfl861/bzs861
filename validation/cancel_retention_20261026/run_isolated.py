"""Run the focused tests against exact production AST nodes, without startup.

This is a LIMITED validation route for an environment without the full checkout
or trafilatura/MCP dependencies. It is NOT a Worker import, startup, TCP, database,
Core, whole-suite or sanitizer test. CI must run the ordinary package tests.

Usage: python run_isolated.py --source server.py --tests test_worker_cancel_retention.py
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
import types

from aiohttp import web
import pytest


METHODS = frozenset({
    "health", "search_multi", "fetch_profile", "start", "close",
    "_attempt_id", "_prune_attempts", "fetch_cancel", "fetch_extract", "map_fetch",
    "_direct_control_id", "_prune_direct_controls", "fetch_cancel_direct", "fetch_extract_direct",
})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--tests", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.read_bytes()
    original = ast.parse(source, filename=str(args.source))
    worker = next(item for item in original.body if isinstance(item, ast.ClassDef) and item.name == "Worker")
    selected = [item for item in worker.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in METHODS]
    if {item.name for item in selected} != METHODS:
        raise RuntimeError("source does not contain every required original method")
    worker.body = selected
    # Original methods and make_app are not rewritten. Only imports and unrelated
    # startup/extraction methods are excluded from this explicitly limited run.
    chosen = [item for item in original.body
              if (isinstance(item, ast.ImportFrom) and item.module == "__future__")
              or (isinstance(item, ast.ClassDef) and item.name in {"Worker", "ManifestMismatch"})
              or (isinstance(item, ast.FunctionDef) and item.name == "make_app")]
    subset = ast.Module(body=chosen, type_ignores=[])
    ast.fix_missing_locations(subset)
    root = types.ModuleType("bzs")
    root.__path__ = []
    package = types.ModuleType("bzs.worker")
    package.__path__ = []
    module = types.ModuleType("bzs.worker.server")
    module.__file__ = str(args.source)
    module.__dict__.update(asyncio=asyncio, re=re, hmac=hmac, json=json, web=web)
    root.worker = package
    package.server = module
    sys.modules.update({"bzs": root, "bzs.worker": package, "bzs.worker.server": module})
    exec(compile(subset, str(args.source), "exec"), module.__dict__)
    print(json.dumps({
        "scope": "exact-method AST isolation; NOT complete package integration",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_git_blob": hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest(),
        "tests_sha256": hashlib.sha256(args.tests.read_bytes()).hexdigest(),
        "python": sys.version,
    }, sort_keys=True), flush=True)
    return pytest.main([str(args.tests), "-q", "-o", "asyncio_mode=auto", "--tb=short"])


if __name__ == "__main__":
    raise SystemExit(main())
