"""Run named formal HTTP cases against an explicitly selected server binary."""
from pathlib import Path
import argparse
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import integration

parser = argparse.ArgumentParser()
parser.add_argument("--server", required=True)
parser.add_argument("--cli", required=True)
parser.add_argument("--fixtures", default=str(ROOT / "tests/fixtures"))
parser.add_argument("--test", action="append", required=True)
options = parser.parse_args()
integration.CONFIG = options
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name, integration)
                           for name in options.test)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
