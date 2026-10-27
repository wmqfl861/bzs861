# bzs861 recovery handoff — 2026-10-27

Base: main `1d8e80e5f1bd1b5f9ad8d1e5fc211c8cd403ca31`, tree
`5a423c16742a7dbfe4ef4e86c168311d157a096d`. PR #2 merged the preceding
P1-B/P1-A(A)/P2-C/P2-B(1,2) work. Do not confuse that with the unrecovered
five-node candidate previously described in chat: its old 38/38 and migration
claims are not evidence for this reconstruction.

This reconstruction is split into bounded background pools; unkeyed receipt
cleanup; archived-byte quota; incremental reclamation; and CI diagnostics/shards.
Read `validation/recovery_20261027/` summaries and the exact candidate's CI.
The local full-run report is supplied with the delivery archive, not committed
raw logs. All new summary files together are below 50 KB.

The audit also found and fixed a shutdown race: idle claimers waiting on Store
locks now recheck stop tokens inside their claim transactions. The old test
fault injector is bound to first admission, not a nondeterministic wake-up.
Original failures remain in external evidence; do not hide them by citing only
the later green run.

Background read/map/search widths default to 4/2/4 and may each be set to 1..8
with native `searchd --background-reads/--background-maps/--background-searches`.
The fixture worker stays single-threaded. Store leasing and Python Worker
connection pacing remain independent gates; a Core reservation timestamp is
not the same as the physical HTTP dispatch time. Do not assert a wall-clock
Worker interval from an approximate Core-side timing observation.

Keyed identities remain permanent. Unkeyed zero-usage local receipts are lazily
collected after payload expiry. Archive JSON is charged with snapshot payloads;
quota-blocked compaction keeps old details and explicitly reports blocking.
New empty stores use incremental auto-vacuum; public compaction reclaims at most
128 pages after logical commit. Old NONE/FULL stores are NOT converted and
report `requires_offline_conversion`. A safe explicit legacy conversion tool
and rowid/index preservation checks remain future work; no old migration result
is claimed here. Quarantined backups remain blocked.

All 63 original cache tests are preserved byte-for-byte and partitioned into
three mandatory suites; selector coverage and the actual uploaded JUnit file
are checked. Issue #3/#4 must not be closed merely because local tests pass:
inspect the new Linux/Windows CI artifacts. Never remove assertions, silently
skip slow cases or relabel a failed attempt as green.

D1–D7 remain pending. No source activation, B-24 expansion, proof-chain business
mechanism, business schema upgrade or history rewriting. Version0.1.10/schema15,
83 packages/161 acceptance IDs/10 slices and 37 partial/46 not_started/0 accepted
are unchanged. Do not claim the whole phase/product or target-device deployment.
