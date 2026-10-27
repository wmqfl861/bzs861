#!/usr/bin/env python3
"""Fail closed if the mandatory shards omit/duplicate any original cache test."""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess


def check(executable: str, source: Path) -> dict:
    expected = re.findall(r"^TEST\((\w+)\)", source.read_text(encoding="utf-8"), re.M)
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("invalid original test registrations")
    def listing(*args):
        return subprocess.check_output([executable, "--list", *args], text=True, timeout=10).splitlines()
    if listing() != expected:
        raise ValueError("runner is not the complete original suite")
    shards = [listing("--shard", str(i), "3") for i in range(1, 4)]
    if Counter(n for shard in shards for n in shard) != Counter(expected) or not all(shards):
        raise ValueError("shard membership must be a disjoint, complete partition")
    for args in [("--shard", "0", "3"), ("--shard", "4", "3"), ("--shard", "1", "0"),
                 ("--shard", "1", "9999"), ("--shard", "1", "3x"), ("--shard", "1"), ("--unknown",)]:
        result = subprocess.run([executable, *args], capture_output=True, timeout=10)
        if result.returncode != 2 or result.stdout:
            raise ValueError("invalid selector must fail before running any test")
    return {"original_cases": len(expected), "shards": [len(s) for s in shards], "invalid_selectors_rejected": 7}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--executable", required=True)
    p.add_argument("--source", required=True, type=Path)
    a = p.parse_args()
    print(check(a.executable, a.source))
