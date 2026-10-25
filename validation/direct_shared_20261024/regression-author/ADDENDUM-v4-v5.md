# Capacity regression additions: v4 and v5

The original v1/v2/v3 inputs, records, manifest and checksums are unchanged.
This addendum documents the later capacity regression and preserves its first
failed runtime without attributing that runtime to the test author.

Frozen v4 adds only the 23rd group,
`large_valid_snapshots_preserve_legacy_and_shared_read_windows`. It checks a
700 KiB control-character body for both a legacy single reader and two compatible
shared readers. The Worker response remains below its 24 MiB wire limit, while
the legitimate complete Store snapshot exceeds 8 MiB after JSON escaping and
publication duplication. Both cases require the original text, exact windows,
one physical request, one snapshot/revision, and correct payer/subscriber usage.

The v4 source hash is
`705f757a17ea5624eb44805e6b0cb16a3a51d66dd5f132cd90f99887701c6d22`.
The root's focused candidate-v3 runtime then reported 23 groups, 3,589 assertions
and one failure, `Missing field: snippet_text`, in this new group. Its original
log, command record, and full input binding are copied byte-for-byte as
`direct-shared-v4-failure-root-v3*`; their original paths and hashes appear in
`direct-shared-v5-record.json`. The recorded command exits 8 because one of its
three selected CTest targets failed. These are root execution records copied
for provenance, not tests newly executed by this author.

The failing assertion incorrectly required a second
`representation_publication.snippet_text`. A direct page can legally store an
empty separate representation policy when its original publication already
governs the same representation. Root authorized deleting only that one new
assertion. The original `publication.snippet_text` and `canonical_text` assertions,
the snapshot-over-8-MiB assertion, both windows, one-fetch/one-snapshot checks,
and every usage assertion remain mandatory.

Frozen v5 is the exact v4 source with that single new assertion removed. Its hash
is `52c8c5201501b94584ac99602abb6f279ecb553cc61faac3043bd37badf4be44`.
Removing the added 23rd group reproduces every byte of the original 22-group v3
source. The change is a new-test oracle correction, not a product fix; no
production file was edited by this adjustment. Both v4 and v5 passed the
recorded compiler syntax check. Corrected runtime results belong to the root's
subsequent focused/final records.

`SHA256SUMS-v4` covers the original v4 candidate and static record.
`SHA256SUMS-v5` covers the corrected frozen input, its static/application record,
and the three original failed-runtime copies. The v4 application authorization
and unchanged-original-group comparison are in
`direct-shared-v4-application.json`.
