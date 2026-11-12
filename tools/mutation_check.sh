#!/usr/bin/env bash
# tools/mutation_check.sh - mutation check for the fail-closed capture paths
# in src/sessions.cpp (CL-03).
#
# Disables each tightening statement (M1 contributor overflow, M2 incomplete
# stored-binding scan, M3 legacy crawl marker propagation), rebuilds the
# publication-sensitive native suites and reports which suite killed the
# mutant. Expected after CL-03: every mutation is KILLED. Commits touching
# the publication/revocation/deletion paths in src/sessions.cpp should attach
# this script's results to their description.
#
# Usage: tools/mutation_check.sh <repo> <build-dir> [cmake]
#   repo        path to the repository worktree (modified and restored)
#   build-dir   an already-configured CMake build directory
#   cmake       optional path to the cmake executable (default: cmake on PATH)
# The build directory is rebuilt in place; run this on a clean tree only.
set -u
REPO=${1:?usage: mutation_check.sh REPO BUILD-DIR [CMAKE]}
BUILD=${2:?usage: mutation_check.sh REPO BUILD-DIR [CMAKE]}
CMAKE=${3:-cmake}
OUT=${MUTATION_OUTPUT_DIR:-$(mktemp -d)}
SUITES="bzs_session_tests bzs_site_publication_tests bzs_source_publication_tests bzs_tests bzs_batch_tests bzs_direct_shared_tests bzs_revocation_tests bzs_crawl_tests"
F=$REPO/src/sessions.cpp
cp "$F" "$F.orig"
restore() { cp "$F.orig" "$F"; rm -f "$F.orig"; }
trap restore EXIT
echo "results in $OUT"

run_suites() {  # $1 = label
  local label=$1 killed=""
  for t in $SUITES; do
    if ("$BUILD/$t" >"$OUT/run-$label-$t.log" 2>&1 || "$BUILD/Debug/$t" >"$OUT/run-$label-$t.log" 2>&1); then :; else killed="$killed $t"; fi
  done
  if [ -n "$killed" ]; then echo "[$label] KILLED by:$killed"; else echo "[$label] SURVIVED (no suite failed)"; fi
}

# 0) unmodified baseline must pass everything, or the results mean nothing
echo "=== baseline (unmodified) ==="
"$CMAKE" --build "$BUILD" --target $SUITES >"$OUT/build-baseline.log" 2>&1 || { echo "baseline BUILD FAILED"; exit 1; }
run_suites baseline

declare -A MUT
# M1: too many contributing URLs -> captured_mode=2 (restrict) is skipped
MUT[M1_contributor_overflow]='s/if(urls.size()>24||contributing_urls.size()>600) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M2: incomplete stored binding scan -> captured_mode=2 is skipped
MUT[M2_incomplete_records]='s/if(incomplete) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M3: legacy crawl marker no longer propagated into the captured bindings
MUT[M3_legacy_marker]='s/if(stored.has("legacy_crawl_unverified")\&\&stored.at("legacy_crawl_unverified").flag())bindings.set("legacy_crawl_unverified",true);//'

for name in M1_contributor_overflow M2_incomplete_records M3_legacy_marker; do
  cp "$F.orig" "$F"
  sed -i "${MUT[$name]}" "$F"
  if cmp -s "$F" "$F.orig"; then echo "[$name] PATTERN NOT APPLIED (source changed? update tools/mutation_check.sh)"; continue; fi
  echo "=== mutation $name ==="
  if ! "$CMAKE" --build "$BUILD" --target $SUITES >"$OUT/build-$name.log" 2>&1; then echo "[$name] BUILD FAILED"; continue; fi
  run_suites "$name"
done
echo "ALL DONE"
