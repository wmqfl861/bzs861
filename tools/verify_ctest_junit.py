#!/usr/bin/env python3
"""Validate the exact file CI uploads, including non-skipping required shards."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def verify(path: Path, minimum: int = 1) -> dict:
    root = ET.parse(path).getroot()
    tests = list(root.iter("testcase"))
    names = [test.get("name", "") for test in tests]
    if len(tests) < minimum or any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("JUnit has missing or duplicate tests")
    required = {"cache_freshness_1", "cache_freshness_2", "cache_freshness_3", "cache_shard_contract", "ci_reporting"}
    if not required.issubset(names):
        raise ValueError("JUnit omits mandatory cache shards or diagnostic checks")
    for test in tests:
        if any(test.find(tag) is not None for tag in ("failure", "error", "skipped")) or test.get("status") in ("fail", "failed", "notrun", "disabled"):
            raise ValueError("JUnit contains a failed or skipped test")
    return {"tests": len(tests), "failed": 0, "skipped": 0, "xml": str(path)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path)
    p.add_argument("--minimum", type=int, default=1)
    a = p.parse_args()
    print(verify(a.path, a.minimum))
