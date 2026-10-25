# Direct shared read: static preservation audit

This directory contains source and evidence preservation checks against baseline
`346048b4ae329e750d15f1e35952aab4513e3912` (tree
`458a6f321e94defd74616ba6ebbf2983252cb495`). These checks execute no product tests,
import no project modules, query no remote service, and change no product files.
The numbers below are file, source-declaration, or static-check counts; they must
not be added to native, HTTP, sanitizer, CI, or acceptance test totals.

## Final record

`audit-v2.json` passed all 38 static checks. Its SHA256 is
`f6d3dbf0d34bae30f19aab61ffa59b756350bde8c2a3405fead9cb4accd5b469`.
The executable audit source is `audit_preservation_v2.py`, SHA256
`bd25d27c68de310b1c7e3c8e2c669618b9a37fd322fb5f63d6284c78fa5de0b9`.

The report records the SHA256 of every old validation file from three independent
inputs: `old-validation-before.json`, the exact baseline Git blob, and the
current file. All 3,590 paths and all three hashes agree. Its remaining file
inventory records the baseline/current hashes of all 223 non-validation baseline
files, identifies the intended implementation/document changes, and rejects
changes outside the explicit scope.

The following original inputs remain byte-identical:

| Input | Verified preservation |
| --- | --- |
| Planning | All 24 files; 83 work packages, 161 acceptance items, 10 slices |
| Configuration | Both manifests; 17 sources, zero enabled/approved sources, zero sites |
| Golden data | Both files |
| Fixtures | All 5 files |
| Dependency/version inputs | Lock and test requirements, Python project and module |
| Database schema | Entire original migration function, version ceiling and final marker 15 |
| Index format | Original tokenizer header and `bzs-ascii-cjk/2+codepoint-grams/1` marker |
| Product version | CMake and both Python declarations remain 0.1.10 |
| Feature status | 37 partial, 46 not_started, zero accepted; no new milestone or slice acceptance |

The original top-level `features` JSON array is compared as raw UTF-8 value bytes,
including its internal whitespace. Its 83 entries occupy 122,468 bytes and retain
SHA256 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`.
Explanatory fields outside this array may change. Availability and original
progress metrics are compared separately with the baseline.

## Build gates and old test assertions

Removing only the new direct shared read target from CMake, its sanitizer target
from Presets, and its Linux/Windows workflow entries reproduces the complete
baseline bytes of all three files. Existing CTest declarations retain their
original order and names. The repeated conditional `python_worker` declaration
is retained as well; declaration counts are not executed-test counts.

Six existing native fixture files contain eight new `fetch.profile` unsupported
branches. Removing only those exact branches reproduces each full baseline file:
`unit.cpp`, `revalidation.cpp`, `revocation.cpp`, `site_publication.cpp`,
`crawl.cpp`, and `retention.cpp`. Every original assertion, callback, physical
request counter, test name, and remaining byte in these files is preserved.

There is one explicit old assertion compatibility change in
`cache_freshness.cpp`: after two earlier successful misses, `w.profiles==0`
becomes `w.profiles==profiles_before`, captured immediately before the
`cache_only` call. The modified assertion still requires that this operation
adds no profile call, returns `E_CACHE_STALE`, and leaves physical requests at
exactly two. The report records both expressions; it does not claim that every
old assertion is literally unchanged. Reversing this one adjustment restores
the entire original file.

The original `RunningServer`, `Integration`, and `HTTPExecution` class source in
`integration.py` remains byte-identical. Removing the three new receipt tests
and reversing only the enumerated HeldWorker fixture extensions reproduces the
whole baseline file. All other 49 original native/Python test source files are
byte-identical. Original native test names and order are separately checked.

The final `direct_shared.cpp` is bound to frozen v5, SHA256
`52c8c5201501b94584ac99602abb6f279ecb553cc61faac3043bd37badf4be44`.
Removing only the new 23rd group reproduces the complete original v3 input with
22 groups. The v4-to-v5 change is separately checked as deletion of exactly one
incorrect new-test assertion about a duplicate representation policy snippet.
See `../regression-author/direct-shared-v5-record.json` for the preserved failed
runtime and the test-oracle correction. This audit does not execute that group.

## Retained first result and audit revision

`audit_preservation.py` and `audit-v1.json` remain unchanged. The first run passed
all specific preservation checks but failed its extra repository scope check
because the initial document-change inventory omitted the final `README.md`
handoff update. That README diff updates the direct-read entry point, published
baseline, and feature table; it does not alter any protected planning/config,
feature-array, acceptance, version, schema, or source metric.

Revision 2 explicitly includes this intended README update and binds the final
v5 test input after the root-authorized correction to its newly added 23rd group.
Neither revision changes product code or rewrites old audit records. The v1
failure is not a product test failure and is not hidden by the v2 result.

## Re-run

From the repository root, choose a new evidence name:

```sh
python3 validation/direct_shared_20261024/preservation/audit_preservation_v2.py --output validation/direct_shared_20261024/preservation/audit-v3.json
```

The script refuses to overwrite the requested JSON or its `.sha256` sidecar.
It exits 0 only if every static check succeeds. A nonzero comparison result
still writes the full new evidence record. The baseline commit is pinned in
the script; current HEAD and all changed-source/document hashes are recorded.
All Git operations are local reads. Remote publication state and actual runtime
results must be assessed from their own root/independent/CI records.
