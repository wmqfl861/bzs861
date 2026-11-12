#!/usr/bin/env python3
"""Black-box regressions for mutation_check.sh using tiny build/test doubles.

Only compilation and native suites are faked; the real shell script mutates a
throwaway source file, resolves executables, captures logs and restores files.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/mutation_check.sh"
BASH = shutil.which("bash")
SUITES = (
    "bzs_session_tests", "bzs_site_publication_tests",
    "bzs_source_publication_tests", "bzs_tests", "bzs_batch_tests",
    "bzs_direct_shared_tests", "bzs_revocation_tests", "bzs_crawl_tests",
)
MUTANTS = ("M1_contributor_overflow", "M2_incomplete_records", "M3_legacy_marker")
GUARDS = (
    'if(urls.size()>24||contributing_urls.size()>600) { bindings.set("captured_mode",std::int64_t{2});return bindings; }',
    'if(incomplete) { bindings.set("captured_mode",std::int64_t{2});return bindings; }',
    'if(stored.has("legacy_crawl_unverified")&&stored.at("legacy_crawl_unverified").flag())bindings.set("legacy_crawl_unverified",true);',
)
SOURCE = "\n".join(GUARDS) + "\n"

BUILD_DOUBLE = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$FAKE_ROOT/build-calls"
label=baseline
if cmp -s "$FAKE_ROOT/original.cpp" "$FAKE_ROOT/repo/src/sessions.cpp"; then
  if [ -f "$FAKE_ROOT/build-labels" ]; then label=restored; fi
elif ! grep -Fq 'if(urls.size()>24||contributing_urls.size()>600)' "$FAKE_ROOT/repo/src/sessions.cpp"; then
  label=M1_contributor_overflow
elif ! grep -Fq 'if(incomplete)' "$FAKE_ROOT/repo/src/sessions.cpp"; then
  label=M2_incomplete_records
elif ! grep -Fq 'if(stored.has("legacy_crawl_unverified")' "$FAKE_ROOT/repo/src/sessions.cpp"; then
  label=M3_legacy_marker
fi
printf '%s\n' "$@" > "$FAKE_ROOT/args-$label"
printf '%s\n' "$label" >> "$FAKE_ROOT/build-labels"
printf '%s\n' "$label" > "$FAKE_ROOT/current-label"
cp "$FAKE_ROOT/repo/src/sessions.cpp" "$FAKE_ROOT/source-$label.cpp"
case "${FAKE_FAULT:-}:$label" in
  baseline-build:baseline|mutant-build:M1_*|restore-build:restored)
    printf 'simulated compiler error\n' >&2; exit 2 ;;
  signal:M1_*) kill -TERM "$PPID"; exit 0 ;;
  remove-executable:M1_*) rm "$FAKE_ROOT/build/Release/bzs_session_tests.exe" ;;
esac
'''

SUITE_DOUBLE = r'''#!/usr/bin/env bash
set -eu
read -r label < "$FAKE_ROOT/current-label"
suite=${0##*/}
suite=${suite%.exe}
printf '%s|%s|%s\n' "$label" "$suite" "$0" >> "$FAKE_ROOT/runs"
if [ "$suite" = bzs_session_tests ]; then
  if [ "$label" = baseline ] && [ "${FAKE_FAULT:-}" = baseline-test ]; then
    printf 'FAIL baseline_regression: check failed: baseline invariant\n'
    exit 1
  fi
  if [[ "$label" = M* ]]; then
    case "${FAKE_FAULT:-}" in
      survivor) exit 0 ;;
      runtime) printf 'fixture could not start\n' >&2; exit 1 ;;
      exception) printf 'FAIL fixture_exception: database unavailable\n'; exit 1 ;;
      mixed-error) printf 'FAIL fixture_assertion: check failed: invariant\nFAIL fixture_exception: database unavailable\n'; exit 1 ;;
      exit-code) printf 'FAIL fixture_error: check failed: incomplete run\n'; exit 2 ;;
      timeout) printf 'FAIL fixture_then_hang: check failed: incomplete run\n'; sleep 2; exit 1 ;;
    esac
    printf 'FAIL regression_%s: check failed: mutation invariant\n' "$label"
    exit 1
  fi
fi
printf 'PASS fixture_%s\n' "$suite"
'''


class MutationHarness(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(BASH, "These harness regressions require Bash")
        self.temp = tempfile.TemporaryDirectory(prefix="bzs mutation ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.source = self.repo / "src/sessions.cpp"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(SOURCE, encoding="utf-8", newline="\n")
        self.build = self.root / "build"
        self.build.mkdir()
        self.out = self.root / "logs"
        self.out.mkdir()
        self.cmake = self.root / "cmake-double"
        self.executable(self.cmake, BUILD_DOUBLE)
        for suite in SUITES:
            self.executable(self.build / suite, SUITE_DOUBLE)

    @staticmethod
    def executable(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def run_harness(self, fault="", config="Debug", **extra_env):
        self.original = self.source.read_bytes()
        (self.root / "original.cpp").write_bytes(self.original)
        env = dict(os.environ, FAKE_ROOT=self.root.as_posix(), FAKE_FAULT=fault,
                   MUTATION_OUTPUT_DIR=self.out.as_posix(),
                   MUTATION_SUITE_TIMEOUT="2")
        env.update(extra_env)
        return subprocess.run(
            [BASH, SCRIPT.as_posix(), self.repo.as_posix(), self.build.as_posix(),
             self.cmake.as_posix(), config], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=60,
        )

    def lines(self, name):
        path = self.root / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def assert_restored(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(Path(str(self.source) + ".orig").exists())
        self.assertFalse(list(self.source.parent.glob("*.mutation-*")))

    def multi_config(self):
        for suite in SUITES:
            self.executable(self.build / "Release" / (suite + ".exe"), SUITE_DOUBLE)
            # A stale root binary must never mask the configured binary's result.
            self.executable(self.build / suite,
                            '#!/usr/bin/env bash\nprintf "fallback executed\\n" >> "$FAKE_ROOT/fallback"\nexit 0\n')

    def assert_harness_error(self, fault, **env):
        result = self.run_harness(fault=fault, **env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("KILLED", result.stdout)
        self.assertIn("ERROR", result.stdout + result.stderr)
        self.assert_restored()
        return result

    def test_single_config_failure_log_preserves_assertion(self):
        result = self.run_harness()
        for mutant in MUTANTS:
            log = (self.out / f"run-{mutant}-bzs_session_tests.log").read_text()
            self.assertIn(f"FAIL regression_{mutant}: check failed:", log)
            self.assertNotIn("No such file or directory", log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_baseline_suite_failure_aborts_before_mutation(self):
        result = self.run_harness(fault="baseline-test")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.lines("build-labels"), ["baseline"])
        self.assertNotIn("KILLED", result.stdout)
        self.assertEqual(self.source.read_text(), SOURCE)
        self.assertFalse(Path(str(self.source) + ".orig").exists())

    def test_single_config_runs_once_per_suite_and_restores_build(self):
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.lines("build-labels"), ["baseline", *MUTANTS, "restored"])
        runs = [row.split("|")[:2] for row in self.lines("runs")]
        self.assertEqual(runs, [[label, suite] for label in ("baseline", *MUTANTS) for suite in SUITES])
        self.assert_restored()
        self.assertEqual((self.root / "source-restored.cpp").read_bytes(), self.original)
        # Real M1-M3 replacements are preserved and each starts from the baseline.
        for index, mutant in enumerate(MUTANTS):
            replacement = "" if index == 2 else "if(false) { }"
            expected = SOURCE.replace(GUARDS[index], replacement)
            self.assertEqual((self.root / f"source-{mutant}.cpp").read_text(), expected)
        for label in ("baseline", *MUTANTS, "restored"):
            args = self.lines(f"args-{label}")
            self.assertEqual(args[args.index("--config") + 1], "Debug")
            self.assertEqual(args[args.index("--parallel") + 1], "1")
            self.assertEqual(args[args.index("--target") + 1:], list(SUITES))

    def test_multi_config_uses_requested_config_and_exe_without_fallback(self):
        self.multi_config()
        result = self.run_harness(config="Release")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "fallback").exists())
        self.assertEqual(len(self.lines("runs")), len(SUITES) * 4)
        for row in self.lines("runs"):
            self.assertIn("/Release/", row)
            self.assertTrue(row.endswith(".exe"), row)
        for mutant in MUTANTS:
            self.assertIn(f"[{mutant}] KILLED", result.stdout)
        self.assert_restored()

    def test_single_config_exe_suffix_is_supported(self):
        for suite in SUITES:
            (self.build / suite).rename(self.build / (suite + ".exe"))
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.lines("runs")), len(SUITES) * 4)
        self.assertTrue(all(row.endswith(".exe") for row in self.lines("runs")))
        self.assert_restored()

    def test_disappearing_selected_executable_is_not_replaced_by_fallback(self):
        self.multi_config()
        self.assert_harness_error("remove-executable", config="Release")
        self.assertFalse((self.root / "fallback").exists())

    def test_missing_executable_is_harness_error(self):
        (self.build / SUITES[0]).unlink()
        self.assert_harness_error("")
        self.assertEqual(self.lines("build-labels"), ["baseline"])
        self.assertEqual(self.lines("runs"), [])

    def test_baseline_build_failure_aborts_before_mutation(self):
        self.assert_harness_error("baseline-build")
        self.assertEqual(self.lines("build-labels"), ["baseline"])
        self.assertEqual(self.lines("runs"), [])

    def test_mutant_build_error_is_not_killed_and_rebuilds_restored_source(self):
        self.assert_harness_error("mutant-build")
        self.assertEqual(self.lines("build-labels"), ["baseline", MUTANTS[0], "restored"])

    def test_non_assertion_exit_one_is_harness_error(self):
        self.assert_harness_error("runtime")

    def test_test_exception_is_not_an_assertion_kill(self):
        self.assert_harness_error("exception")

    def test_mixed_assertion_and_test_exception_is_harness_error(self):
        self.assert_harness_error("mixed-error")

    def test_error_exit_with_assertion_text_is_not_killed(self):
        self.assert_harness_error("exit-code")

    def test_timeout_after_assertion_is_not_killed(self):
        result = self.assert_harness_error("timeout", MUTATION_SUITE_TIMEOUT="0.2")
        self.assertIn("TIMEOUT", result.stdout + result.stderr)

    def test_surviving_mutant_fails_gate(self):
        result = self.run_harness(fault="survivor")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SURVIVED", result.stdout)
        self.assertNotIn("KILLED", result.stdout)
        self.assert_restored()

    def test_missing_mutation_pattern_fails_gate(self):
        self.source.write_text(SOURCE.replace(GUARDS[0], "// missing M1"), encoding="utf-8", newline="\n")
        result = self.assert_harness_error("")
        self.assertIn("PATTERN NOT APPLIED", result.stdout + result.stderr)
        self.assertFalse(any(label.startswith("M") for label in self.lines("build-labels")))

    def test_existing_backup_is_never_overwritten(self):
        backup = Path(str(self.source) + ".orig")
        backup.write_text("previous run recovery data\n")
        result = self.run_harness()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(backup.read_text(), "previous run recovery data\n")
        self.assertEqual(self.source.read_text(), SOURCE)
        self.assertEqual(self.lines("build-labels"), [])

    def test_existing_backup_directory_is_never_used(self):
        backup = Path(str(self.source) + ".orig")
        backup.mkdir()
        result = self.run_harness()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(backup.iterdir()), [])
        self.assertEqual(self.source.read_text(), SOURCE)
        self.assertEqual(self.lines("build-labels"), [])

    def test_term_restores_source_and_rebuilds(self):
        result = self.run_harness(fault="signal")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_restored()
        self.assertEqual(self.lines("build-labels"), ["baseline", MUTANTS[0], "restored"])

    def test_restore_build_failure_fails_gate_even_after_kills(self):
        result = self.run_harness(fault="restore-build")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ERROR", result.stdout + result.stderr)
        self.assertEqual(self.lines("build-labels")[-1], "restored")
        self.assert_restored()

    def test_output_directory_is_created(self):
        self.out.rmdir()
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.out / "build-baseline.log").is_file())
        self.assert_restored()


if __name__ == "__main__":
    unittest.main(verbosity=2)
