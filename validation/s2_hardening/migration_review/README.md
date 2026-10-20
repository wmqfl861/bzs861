# Independent schema 6 and publication-generation review

This directory records a narrow, independent native review of the 0.1.5 changes.
It is separate from the author's native suite and the release CTest, HTTP, crash,
Python, and sanitizer gates. Its synthetic `WorkerPort` replies make no network
requests. Test databases and temporary executables are deleted after each run;
none is retained here.

## How the historical database is obtained

`create_schema6.cpp` is compiled with the headers and crawl-test helpers extracted
from published commit `90ca7e93f9c96a43ff8d99b237638e4aa2aa2c63` (0.1.4), together
with the preserved 0.1.4 static library. It invokes the actual old Store and crawl
APIs. It does not construct a current database and downgrade its schema, alter
SQL tables, or fabricate a migration result. The generator checks
`PRAGMA user_version == 6` before exiting.

The approved historical scope is `/map`. The synthetic physical chain is
`/map/docs/a` → `/map/transit/x` → `/map/docs/b`, under `https://map.example`.
The old crawl implementation stores the final body and an existing frozen search
result, but its final snapshot's `meta.redirects` is empty. This demonstrates the
missing immutable intermediate identity in an actual old-version database.
The generator uses the fixed native clock `1700000000000`; the upgrader uses the
same clock so that the historical session remains live.

The recorder archives the baseline commit's source inventory and SHA-256 values,
and records the actual old library's hash. It does not assert deterministic
rebuild equivalence between an arbitrary supplied library and that source commit.
On a new checkout, obtain a baseline library by building that commit separately;
keep it separate from the current build. Runtime schema and fixture assertions
still apply.

## Two migration checks

`verify_upgrade.cpp` makes separate copies of the pristine historical database:

| Activation after automatic schema 7 migration | Expected relationship |
|---|---|
| Narrow `/map` to `/map/docs` | The old final URL remains inside the new scope, but the unverified legacy crawl chain is conservatively withdrawn. |
| Activate an explicit empty registry | Legacy crawl evidence triggers conservative withdrawal even though no historical scope plaintext was available in schema 6. |

For both cases the harness requires all of the following:

1. Schema migration alone, before explicit site-policy activation, preserves the
   old current search result and old frozen result.
2. Explicit activation hides the old current result, old frozen row, and indexed
   flag in the old crawl receipt; the owner snapshot and evidence remain intact.
3. Closing and reopening the database without another activation preserves that
   withdrawal.
4. One complete synthetic ordinary read creates a new eligible snapshot. Only the
   new current result is restored; the old frozen row, old snapshot's indexed
   flag, and old crawl receipt remain restricted.

This validates conservative gating of information that schema 6 did not retain.
It does not claim recovery of the missing intermediate identity. The pristine
database hash is checked before and after the two copied-database scenarios.

## Eight generation and in-flight checks

`verify_publication_epoch.cpp` independently covers a second review finding. An
allowing configuration at generation 1 becomes rejected or links-only at
generation 2. A metadata-only manifest change leaves the restriction in force
at generation 3. The complete body obtained at generation 3 must remain restricted
when publication is allowed again at generation 4.

The eight combinations are rejected/links-only × relaxation after body commit or
inside the synthetic Worker's return path × the original scope or a newly added
more specific scope. The in-flight case uses another live Store connection to
activate the allowing manifest before the old response reaches ingestion.

Each combination checks FTS search, a non-FTS `NOT absent` query, frozen output,
private canonical content, and indexed status. The non-FTS check is necessary:
absence of an FTS row can otherwise conceal an incorrectly revived publication
eligibility flag. A subsequent complete allowing read must create a new snapshot
and restore only new current output. Each combination makes exactly two synthetic
worker calls. These eight scenarios are not counted as eight additional CTest
suites or as real HTTP concurrency tests.

## Reproduce and interpret the records

Run from the repository root with a fresh output name:

```bash
python3 validation/s2_hardening/migration_review/run_review.py \
  --baseline-lib build-s1-review/libbzs_core.a \
  --current-lib build-s2-policy-author/libbzs_core.a \
  --run-name author-corrected
```

The recorder uses a C++20 compiler and the same native link dependencies as this
repository (`sqlite3`, OpenSSL Crypto, `libxml2`, `json-c`, and threads). It records
the full compile/execution argv and working directories, exit statuses, log hashes,
baseline headers/source hashes, actual static-library hashes, and review source
hashes. Current native sources, headers, CMake input, review sources, and both
libraries must be unchanged from before compilation until after execution. Each
execution's actual executable hashes are checked before and after the scenarios.
The output directory is created exclusively, so rerunning requires a different
`--run-name`. Temporary paths in the command records identify that execution;
the recorder recreates fresh temporary directories on the next run.

`author-corrected/review.json` is the successful author-library verification:
two migration scenarios and eight publication-generation combinations passed,
with unchanged inputs. `author-final/` preserves an earlier harness-only compile
failure caused by one C++ `auto` declaration mixing a JSON and a string value.
That first attempt executed no scenario and is not a passing gate. The declaration
was corrected, and the complete recorded run was repeated under the new name.

`final-root/review.json` records the completed release-library rerun using
`build-s2-hardening-review/libbzs_core.a`. Both migration scenarios and all eight
publication-generation combinations passed. The final native library SHA-256 is
`0385b54988c2c7e8794f684dc0a4db56cc88fb189cd3033dccd265d0b8fb22e7`.
Its 32 current native source inputs match the final execution manifest v2. The
final run also adds explicit creator assertions for the observed robots/A/X/B
call sequence, one charged body page, final URL B, and missing old redirects.
The successful author and failed earlier records remain intact. An author-library
record by itself does not establish which binary passed the final release gates.

## Related reviewed boundaries

The production fix combines immutable captured publication mode with monotonic
scope restriction watermarks. On relaxation the applicable watermark also seals
the previous global generation, closing late responses from a restriction that
persisted across metadata-only changes. Scope narrowing still preserves an
unrestricted retained child region. Imports retain their own source semantics;
owner private snapshots and evidence are not the search-publication surface.

Deployment activation is explicit. An already-open stale Store cannot replace a
newer active manifest; constructing a new Store and explicitly activating another
manifest remains an operator action. This review does not turn that CAS into a
distributed deployment authority or an external revocation log. Existing backup
quarantine and independent deletion requirements remain in force.
