# Independent review of the search-snippet fixture correction

The final-v1 complete run exposed an outdated expectation in
`EndToEnd.test_02_web_search_merges_sources`: it expected
`snippet_kind=search_engine_snippet`, while the shared fakeweb manifest grants
only `redistribution_policy=links_only` for every source.

## Contract judgment

The source publication barrier is correct. `contract_mode` maps a valid
links-only source review to mode 1. `source_hit_mode_locked` takes the most
restrictive mode among all contributors to a fused hit, and
`project_source_search` replaces its title with the URL, clears its snippet,
and sets `snippet_kind=publication_suppressed`. This is consistent with
`docs/SOURCE_PUBLICATION.md` and the existing positive, negative, mixed-source,
cached, frozen-session, restart, and in-flight native cases.

The review therefore approved the test-author's correction:

- The shared `python/tests/fakeweb.py` manifest remains links-only.
- The original HTTP test retains its merging, source attribution, independent
  index-family, deduplication, and result-presence checks. It now checks
  suppressed snippets and URL titles for **every** hit, and repeats the query
  to verify an identical cached projection.
- The new positive HTTP test starts an independent Stack with a new temporary
  state directory and a newly generated manifest. Its source reviews explicitly
  allow redistribution with limits before either Core or Worker starts.
- The positive test requires a nonempty snippet, a title different from the
  URL, `search_engine_snippet`, and the expected two fused source identities.
  Its cached replay must preserve the same results.
- Stack construction occurs inside a cleanup-protected block, including
  partial-startup failures.
- The API search-result description now states the permitting and links-only
  branches rather than promising snippets independently of source permission.

No production source-publication protection was changed for this correction.
The broader project's final-v1 failed evidence remains historical evidence;
this review does not replace or relabel it as a passing full-suite run.

## Independent execution

`f03-fixture-v1.{json,log}` records the exact command, before/after hashes, and
real HTTP → Worker → Core execution. Both focused tests passed in **1.262 s**,
with no failures or errors and
`inputs_unchanged_during_command=true`.

The implementation checkout was
`f03dc7fdf11cccf8a6a908a2ec4b3c76bde4e554`. The reviewed test and API edits were
not yet committed, so they are bound by file hashes in the execution record:

| Input | SHA-256 |
|---|---|
| `tests/e2e_web.py` | `68fec875519846092327c40e56ed56ef61ea182e197857e6d19fcc963b4ab6f6` |
| `python/tests/fakeweb.py` | `e6634ebb86c557c6b5d24f422a977ca0fad56ea48d86c7e8f10d012150c44000` |
| `docs/API.md` | `d1a195205e83803193cfc086174b80d60ece2fe156339fc00c1e70c3afd4bcbf` |
| Core executable | `a7fad95ead7739ec284cba5524ce3be3cf71f7d2e7c0ef93a3db716e0714f871` |

The JSON also fingerprints the implementation, headers, and Worker modules.
The runner requires a fresh evidence prefix and never overwrites existing
records. All requests are confined to local fixture and RPC sockets; no public
origin, production permission, or database content is modified.

This review found no remaining blocker in the fixture correction. The MODE
deadline correction and the final complete debug/sanitizer/platform gates are
separate validation responsibilities.
