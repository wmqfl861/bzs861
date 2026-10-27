#!/usr/bin/env python3
import re
import tempfile
import unittest
from pathlib import Path
from verify_ctest_junit import verify

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["cache_freshness_1", "cache_freshness_2", "cache_freshness_3", "cache_shard_contract", "ci_reporting"]


class Reporting(unittest.TestCase):
    def test_complete_and_incomplete_or_failed_reports(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ctest.xml"
            good = "<testsuite>" + "".join(f'<testcase name="{n}"/>' for n in NAMES) + "</testsuite>"
            p.write_text(good)
            self.assertEqual(verify(p)["tests"], 5)
            for bad in [good.replace('name="cache_freshness_1"', 'name="omitted"'),
                        good.replace('name="cache_freshness_2"', 'name="cache_freshness_1"'),
                        "<testsuite/>", *[good.replace('/>', f'><{tag}/></testcase>', 1) for tag in ("failure", "error", "skipped")]]:
                p.write_text(bad)
                with self.assertRaises(ValueError):
                    verify(p)
            p.unlink()
            with self.assertRaises(FileNotFoundError):
                verify(p)

    def test_ci_keeps_all_shards_and_uses_absolute_junit_outputs(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn('--output-junit "$PWD/build/ctest-results.xml"', workflow)
        self.assertIn('--output-junit "$env:GITHUB_WORKSPACE/build-win/ctest-results.xml"', workflow)
        windows = workflow.split('ctest --test-dir build-win', 1)[1].splitlines()[0]
        pattern = re.search(r'-R "([^"]+)"', windows).group(1)
        for name in NAMES + ["background_pool", "receipt_retention", "archive_quota", "incremental_storage"]:
            self.assertRegex(name, pattern)
        self.assertEqual(workflow.count("if: always()"), 2)
        self.assertIn("verify_ctest_junit.py", workflow)
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn("foreach(shard RANGE 1 3)", cmake)
        self.assertNotIn("DISABLED TRUE", cmake)


if __name__ == "__main__":
    unittest.main()
