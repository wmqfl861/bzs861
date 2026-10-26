# Durable cancellation retention hardening — 2026-10-26 UTC

This is a narrow CACHE-01 / FT-032 / B-24 continuation of the existing v1.2 blueprint, not completion of cross-channel sharing or full product acceptance. Base: `6410d272a2adefe25386af766da9d389a8a4d347`, tree `0a614980a651ba457eab0ef97931a40b80644d6f`.

## Change and retained boundaries

The durable `fetch.cancel` route previously renewed a retired receipt to “now + 120 seconds” on every duplicate cancellation, unlike `fetch.cancel_direct`. It also accepted unknown keys, and non-object input could produce an internal error. The candidate changes only `Worker._attempt_id` and `Worker.fetch_cancel`:

- Reject non-object identities and cancellation envelopes containing anything other than `attempt_id`, before pruning or changing any registry.
- Keep the first retirement deadline and cumulative request/body-byte receipt on duplicate cancellations. The initial retention window remains 120 seconds.
- Keep the combined 2,048 active/retired capacity, non-eviction of running attempts, and independent ordinary-read control namespace.

Expired Worker receipts may be removed; cancelling an expired identity starts a new finite zero-usage cancellation record. This is NOT a permanent replay guarantee, not a durable usage query, and not a replacement for Core's persistent attempt/epoch fences. Cancellation remains cooperative and does not imply immediate network interruption or a refund. No SQL, migration, sharing membership, payer, Core authorization, source approval or HTTP capacity rule changes.

## Reproduction and actual execution

See [report.json](report.json) for source/blob/byte bindings and [implementation.patch](implementation.patch) for the complete production diff. All other Worker method ASTs were compared and are unchanged. Existing tests and historical validation are not modified.

| Input | Focused result | Evidence |
| --- | --- | --- |
| Exact published baseline server | 31 passed, 23 failed, 0 skipped; exit 1 | `baseline.log.gz` (complete original log, gzip mtime 0) |
| Candidate server | 54 passed, 0 failed, 0 skipped; exit 0 | `candidate.log` |

Tests cover malformed input without mutation, exact expiry, duplicate cancellation, full-capacity admission, active-attempt preservation, batch/map/direct replay receipts, namespace isolation, gathered duplicate requests, RPC error classification, and authorization-before-JSON handling.

**Local execution was limited:** the current environment lacks the full checkout and required startup dependencies. [run_isolated.py](run_isolated.py) executes unchanged production AST methods plus the actual RPC handler with real aiohttp response objects. It does not import/start the complete Worker package, open TCP connections, initialize Core or a database, run CTest/sanitizers, or constitute independent external review. The tests themselves import the normal package when run in the complete CI environment; the isolation runner is only retained evidence tooling.

Normal checkout command:

```sh
python -m pytest python/tests/test_worker_cancel_retention.py
```

Limited reproduction (not a substitute for normal CI):

```sh
python validation/cancel_retention_20261026/run_isolated.py --source python/bzs/worker/server.py --tests python/tests/test_worker_cancel_retention.py
```

The successful baseline Actions run `40183102494` is historical evidence for `6410d27` only. Candidate CI must be queried for its own exact commit; no result is pre-recorded here.

## Handoff and publication

Publish the candidate to `work/cancel-retention-20261026` first. Do not advance `main` until the candidate's own required checks pass and the fresh parent SHA matches; use a non-force update. See [handoff](../../docs/handoff/CANCEL_RETENTION_20261026.md).

Earlier conversation notes mention unpublished schema16 dispatch identity and `join_read_url` work. Those source bytes were unavailable here: this candidate neither contains nor supersedes them. Preserve them separately and reconcile against actual commits before integration.

Version 0.1.10 / schema15, 83 packages / 161 acceptance IDs / 10 slices, source/site configuration and accepted-package counts remain unchanged. No external source or paid model request was made by these tests.
