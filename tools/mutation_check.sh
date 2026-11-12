#!/usr/bin/env bash
# tools/mutation_check.sh - mutation check for the fail-closed capture paths
# in src/sessions.cpp (CL-03).
#
# Disables each tightening statement (M1 contributor overflow, M2 incomplete
# stored-binding scan, M3 legacy crawl marker propagation), rebuilds the
# publication-sensitive native suites and reports which suite killed the
# mutant. Success requires a passing baseline and test-level kills (a
# `FAIL <test>: ...` line with exit 1) for M1-M3.
#
# Usage: tools/mutation_check.sh <repo> <build-dir> [cmake] [config]
#   repo        isolated worktree, with no concurrent writers/build consumers
#   build-dir   already-configured CMake build directory for that worktree
#   cmake       optional executable (default: cmake on PATH)
#   config      explicit build configuration (default: Debug)
# MUTATION_OUTPUT_DIR selects the log directory (default: a new temp directory).
# MUTATION_SUITE_TIMEOUT is a positive number of seconds (default: 600).
# Requires Bash and GNU timeout. The source and affected build targets are
# restored on exit, including errors and HUP/INT/TERM. SIGKILL/power loss cannot
# be trapped: preserve/recover sessions.cpp.mutation-backup before another run.
set -euo pipefail
REPO=${1:?usage: mutation_check.sh REPO BUILD-DIR [CMAKE] [CONFIG]}
BUILD=${2:?usage: mutation_check.sh REPO BUILD-DIR [CMAKE] [CONFIG]}
CMAKE=${3:-cmake}
CONFIG=${4:-Debug}
SUITE_TIMEOUT=${MUTATION_SUITE_TIMEOUT:-600}
SUITES=(bzs_session_tests bzs_site_publication_tests bzs_source_publication_tests bzs_tests bzs_batch_tests bzs_direct_shared_tests bzs_revocation_tests bzs_crawl_tests)
F=$REPO/src/sessions.cpp
BACKUP_DIR=$F.mutation-backup
BACKUP=$BACKUP_DIR/sessions.cpp

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$SUITE_TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ && "$SUITE_TIMEOUT" =~ [1-9] ]] || fail 'MUTATION_SUITE_TIMEOUT must be positive seconds'
command -v timeout >/dev/null || fail 'GNU timeout is required'
[[ -f "$F" && ! -L "$F" ]] || fail "source is not a regular non-symlink file: $F"
# Refuse legacy recovery files too. An atomic directory reservation prevents
# concurrent invocations from replacing each other's only original copy.
[[ ! -e "$F.orig" && ! -L "$F.orig" ]] || fail "existing backup must be recovered first: $F.orig"
OUT=${MUTATION_OUTPUT_DIR:-$(mktemp -d)}
mkdir -p "$OUT"
mkdir "$BACKUP_DIR" || fail "cannot reserve backup (recover any previous run first): $BACKUP_DIR"
source_dirty=0
build_dirty=0

build_targets() {
  "$CMAKE" --build "$BUILD" --config "$CONFIG" --parallel 1 --target "${SUITES[@]}" >"$OUT/build-$1.log" 2>&1
}

restore() {
  local status=$?
  trap - EXIT
  # Let recovery finish even if a second interrupt arrives.
  trap '' HUP INT TERM
  if (( source_dirty )); then
    if ! cp "$BACKUP" "$F"; then
      printf 'ERROR: source restore failed; original retained at %s\n' "$BACKUP" >&2
      exit 1
    fi
  fi
  if (( build_dirty )); then
    if build_targets restored; then
      printf '[restored] source and affected build targets restored\n'
    else
      printf 'ERROR: restored-source BUILD FAILED; rebuild before using %s (see %s/build-restored.log)\n' "$BUILD" "$OUT" >&2
      status=1
    fi
  fi
  if ! rm -f "$BACKUP" || ! rmdir "$BACKUP_DIR"; then
    printf 'ERROR: could not remove owned backup: %s\n' "$BACKUP_DIR" >&2
    status=1
  fi
  exit "$status"
}
trap restore EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cp "$F" "$BACKUP"
printf 'results in %s (config %s)\n' "$OUT" "$CONFIG"

declare -A EXECUTABLES
resolve_suites() {
  local t candidate
  for t in "${SUITES[@]}"; do
    # Prefer the requested multi-config layout; select once after the baseline
    # build, not by retrying a failed suite at another path. Check .exe first
    # because Git Bash also accepts a suffix-less path for a Windows executable.
    for candidate in "$BUILD/$CONFIG/$t.exe" "$BUILD/$CONFIG/$t" "$BUILD/$t.exe" "$BUILD/$t"; do
      if [[ -f "$candidate" && -x "$candidate" ]]; then
        EXECUTABLES[$t]=$candidate
        break
      fi
    done
    [[ -n "${EXECUTABLES[$t]:-}" ]] || fail "missing executable for $t (config $CONFIG)"
  done
}

run_suites() {  # $1 = label; only test-level failures count as kills
  local label=$1 t log rc
  local killed=()
  for t in "${SUITES[@]}"; do
    log=$OUT/run-$label-$t.log
    rc=0
    timeout --kill-after=5 "$SUITE_TIMEOUT" "${EXECUTABLES[$t]}" >"$log" 2>&1 || rc=$?
    if (( rc == 0 )); then continue; fi
    if (( rc == 124 || rc == 137 )); then
      fail "[$label] $t TIMEOUT (exit $rc; see $log)"
    fi
    if [[ "$label" == baseline ]]; then
      fail "[baseline] $t FAILED (exit $rc; see $log); no mutations run"
    fi
    # These native harnesses return 1 for failed tests and print one
    # `FAIL <test>: <reason>` line per failure - for failed CHECK assertions
    # ("check failed: ...") and for uncaught test exceptions alike (e.g. a
    # Json at() "Missing field: ..." killed M1 in CI run 43268736001, with no
    # "check failed:" text anywhere). Any such line with exit 1 is a
    # test-level kill; launch errors, crashes, timeouts and exit-1 runs
    # without FAIL lines stay harness errors. The complete log is kept.
    local fail_line='^FAIL [A-Za-z0-9_]+: '
    if (( rc != 1 )) || ! grep -Eq "$fail_line" "$log"; then
      fail "[$label] $t harness failure (exit $rc, not a test-level kill; see $log)"
    fi
    printf '[%s] %s killed tests: %s\n' "$label" "$t" \
      "$(grep -E "$fail_line" "$log" | sed -E 's/^FAIL ([A-Za-z0-9_]+):.*/\1/' | paste -sd' ' -)"
    killed+=("$t")
  done
  if [[ "$label" == baseline ]]; then
    printf '[baseline] PASS\n'
  elif (( ${#killed[@]} )); then
    printf '[%s] KILLED by: %s\n' "$label" "${killed[*]}"
  else
    printf '[%s] SURVIVED (no test failed)\n' "$label"
    return 1
  fi
}

# 0) unmodified baseline must pass everything, or the results mean nothing
printf '=== baseline (unmodified) ===\n'
build_targets baseline || fail "baseline BUILD FAILED (see $OUT/build-baseline.log)"
resolve_suites
run_suites baseline

declare -A MUT
# M1: too many contributing URLs -> captured_mode=2 (restrict) is skipped
MUT[M1_contributor_overflow]='s/if(urls.size()>24||contributing_urls.size()>600) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M2: incomplete stored binding scan -> captured_mode=2 is skipped
MUT[M2_incomplete_records]='s/if(incomplete) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M3: legacy crawl marker no longer propagated into the captured bindings
MUT[M3_legacy_marker]='s/if(stored.has("legacy_crawl_unverified")\&\&stored.at("legacy_crawl_unverified").flag())bindings.set("legacy_crawl_unverified",true);//'

status=0
for name in M1_contributor_overflow M2_incomplete_records M3_legacy_marker; do
  source_dirty=1
  sed "${MUT[$name]}" "$BACKUP" > "$F"
  if cmp -s "$F" "$BACKUP"; then
    fail "[$name] PATTERN NOT APPLIED (source changed? update tools/mutation_check.sh)"
  fi
  printf '=== mutation %s ===\n' "$name"
  build_dirty=1
  build_targets "$name" || fail "[$name] BUILD FAILED (see $OUT/build-$name.log)"
  run_suites "$name" || status=1
done
exit "$status"
