# Independent cache-policy review, 2026-10-23

The principal-revocation implementer reviewed a different contributor's HTTP
freshness increment against RFC 9111 sections 3, 4.2.3 and 4.3.4. This directory
preserves failing counterexamples and the first successful verification of the
author's corrections. It does not claim a full release gate.

## Reproducible regressions

`probe_http_policy.py` is registered permanently as CTest
`e2e_cache_policy_review`. It starts a real local HTTP origin, real Python Worker,
and real C++ Core. It never contacts a public origin or edits SQLite contents.

| Case | Before fixes | After first fixes |
|---|---|---|
| HTTP 203 + no-store, direct read | Persisted one forbidden snapshot | Pass |
| HTTP 203 + no-store, durable batch | Persisted one forbidden snapshot | Pass; one request charged |
| HTTP 203 + no-store, crawl | Persisted one forbidden snapshot | Pass; two requests charged |
| Empty extracted 200 + no-store, crawl | Persisted raw HTML/title despite policy | Pass |
| Rejected 304 + no-cache | Old cache_only response remained fresh | Pass |
| Rejected 304 + max-age=0 | Old cache_only response remained fresh | Pass |
| Repeated Pragma: no-cache then extension | Last field hid no-cache; default seven-day reuse | Pass |
| Truncated 200 without no-store | E_CACHE_POLICY wrongly blocked owner prefix | Pass; nonpublishing prefix retained, never fresh |
| Repeated Date | Pass; never fresh | Pass |
| Repeated Age: 120 then 0 | Pass; never used only final zero | Pass |

`baseline-10.{json,log}` ran all ten cases against the unchanged
`f5e03730337de9eaf7f68132a40d64005efe1d57` Worker, with C++ executable SHA-256
`494285b16e4dec29b872a392d5364ecc892f9cf27637af6fab816c0f40e70472`.
Eight failed and two passed. `fixed-10-v1.{json,log}` then passed all ten against
the author's first corrections. Both records assert
`inputs_unchanged_during_command=true` and include full input and output hashes.
`before-fixes.log` is the preceding seven-case exploratory run, retained as
historical evidence rather than replacing the reproducible ten-case record.

Two additional permanent native cases in `tests/cache_freshness.cpp` advance
the controlled clock while `fetch.profile` is pending. They require cache_only
to reject a representation that expired during the local RPC, and cache_first
to fetch anew. `native-profile-before.{json,log}` preserves their two failures
while the other eleven native cases passed. It was linked after the first HTTP
fixes and before the profile-expiry correction.

The permanent pytest `python/tests/test_http_freshness_review.py` makes a real
fixture request and advances a deterministic clock by two seconds during HTML
extraction. With Age=2 and max-age=3, parsing must not replace the response's
receipt time and turn four seconds of age into two. `response-clock-before.log`
records the observed timestamp reset and failed assertion.

## Final independent verification

The author corrected the implementation; this reviewer then repeated all three
focused groups against integrated source commit
`d68cbebb9b9a63e90b07b17038092f7dbfea6a1f`:

| Evidence | Result | Scope |
|---|---|---|
| `final-d68-http.{json,log}` | 10/10 passed, 8.413 s | All real HTTP regressions above |
| `final-d68-native.{json,log}` | 13/13 passed, 80 assertions | Freshness arithmetic, bounds, owner/profile/restart, legacy records, invalidation, 304 merge, profile-expiry races |
| `final-d68-clock.{json,log}` | 1/1 passed, 0.38 s | Parsing preserves response receipt time and Age residence |

All three records have `inputs_unchanged_during_command=true`. Their input maps
include exact implementation/test hashes and the Git commit. The tested Core
executable SHA-256 is
`a7fad95ead7739ec284cba5524ce3be3cf71f7d2e7c0ef93a3db716e0714f871`;
the native test executable is
`ce1922ee72d8360c00d9f1c11b7c860c69d927df9dd201503778f750fb52c835`.
The linked Core library SHA-256 is
`bb64867b8b8c65453502dbd1cd4eb3745910e43e4b2e55987d1a75ec5902c896`.

No remaining blocker was found in the reviewed cache paths after these
corrections. This is a focused independent review, not a substitute for the
project's complete native, Python, HTTP, sanitizer, and platform release gates.
No-store is still checked before the incomplete-body metadata omission, so
retaining a bounded prefix does not exempt it from a prohibiting response
policy. Incomplete bodies carry no freshness metadata and cannot become an
implicit fresh-cache hit. Unsupported restrictive 304 responses may require a
later explicit full read; they cannot preserve the previous permissive reuse.

## Running

The source corrections are deliberately owned by the implementation author;
this review commit contains tests, CTest registration, and evidence only.
Use a unique prefix so earlier evidence cannot be overwritten:

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/venv/bin/python \
  validation/s2_takeover/cache_review/run_review.py \
  --repo /path/to/repo --server /path/to/build/searchd \
  --python /path/to/venv/bin/python --prefix final-review
```

Run the native `cache_freshness` CTest and the response-clock pytest as well.
`run_focused_review.py --repo ... --build ... --python ... --prefix ...` records
those two commands with immutable logs and before/after input hashes.
The dependency-backed local environment used for these records is
`/workspace/scratch/80f41eb198a3/.venv-bzs-continuation/bin/python`.
Loopback permission is required in the managed sandbox. Missing Python Worker
dependencies are reported as CTest skip code 77, never as passing tests.

## Reviewed limits

Known-profile offline reuse remains scoped to the current Core process, as
documented by the author. A new Core process must obtain the Worker profile.
Redirect-chain policy is not a fresh-cache reuse path. Legacy records without
freshness remain ineligible for implicit reuse. Response policy does not grant
principal access, site/robots admission, or publishing permission. The durable
batch/crawl counterexamples also verify that a storage rejection preserves
known physical request usage.

Reference: [RFC 9111](https://www.rfc-editor.org/rfc/rfc9111.html).
