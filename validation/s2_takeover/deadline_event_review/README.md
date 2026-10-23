# Independent review of terminal search-deadline settlement

This review approves the narrow production change in
`93ffe39b0dc95e591cbc05500947cd5ef7bb9349` for integration. The same frozen
Store probe reproduces two deadline defects with the exact published
`199eb635ca90c657dff673fbf88bed44e7c4b40b` runtime and passes all six cases
with the corrected author's runtime. This is local evidence; it does not
claim a new remote CI result or replace the integrator's final-library run.

Reviewer `/root/handoff_audit` did not author this deadline fix. The reviewer
previously authored the bounded shared-read increment and the genuine old-schema
migration probe; those features are not independently approved by this review.
Only new files in this directory are part of this evidence commit.

## Reproduced failure and reviewed invariant

The preserved Linux CI log in `../deadline_followup/initial-linux.log` records
run `39950116698`, job `120402244733`, on published commit `199eb635...`:
the original recovery suite's `test_06` expected `queued, failed` and observed
`queued, source_unknown, failed`. That log proves an intermediate nonterminal
settlement occurred. It does not measure the two clocks or prove a particular
submillisecond offset.

Source inspection identifies a reproducible mechanism: the integer RPC timeout
can consume the entire remaining monotonic budget while the persisted Store
wall-clock deadline is still live. Before the fix, both that transport timeout
and an explicit `E_DEADLINE` could become `source_unknown`, briefly queueing the
tail. A deadline on the last source could instead become `completed`.

The fix classifies only typed `E_WORKER_TIMEOUT` when the RPC grant equals the
remaining search budget as `E_DEADLINE`. The existing post-response deadline
check remains. `Store::fail_search` then commits `failed`, an epoch increment,
one unknown-attempt charge, one terminal event, and the receipt in one
transaction. The live-lease/auth-epoch guard remains ahead of that mutation.
Ordinary earlier transport failures retain their existing source-fallback
behavior. The fix adds no retry, sleep, increased timeout, deadline reset,
schema change, or modification to the original recovery E2E test.

## Frozen comparison

| Run | Library SHA-256 | Actual process result | Meaning |
| --- | --- | --- | --- |
| `baseline-199-v2` | `2db27c784e150282bcee6f7bf2a2c1f2f0921af8bbe31e99f791130e49339785` | Exit 1; 4 passed, 2 failed; 65 assertions | Expected negative control reproduces both deadline defects; all four controls pass. |
| `corrected-author` | `d98167aa71f9cd9efc1a048e5c9f1e5d45ab6b7267c13d2d18c65a3ef83ff41e` | Exit 0; 6 passed, 0 failed; 92 assertions | Terminal deadline, prior-winner protection, and accounting checks pass. |

Both runs use `probe-freeze-v2.json`, with probe SHA-256
`1ec5939ec09f505b270f4efd44445519b149d58e8b38273d4d889c822afb6425`
and runner SHA-256
`68ef7901df13f9818304bfd2e88b56b7c85b13a6d5ace8074c97cd1b7b03dde7`.
The wrapper's successful exit on the baseline means the expected negative
control matched; the baseline product probe itself failed.

The corrected library was captured while its author's HEAD still identified
`199eb635...`, before the final author commit. Its execution record deliberately
retains that fact. `author-commit-binding.json` separately verifies that the
captured production-file, patch, and header hashes match committed `93ffe39...`.
It also rechecks the immutable old and corrected library snapshots. The library
was not rebuilt by this reviewer; the independent check relinks the frozen probe
against the explicitly captured exact library.

| Case | Persistent invariant checked |
| --- | --- |
| Explicit deadline at Store clock `D - 1 ms` | Immediate failed state and `queued, failed`; unknown and dispatched counts stay at one; pending tail never leases; repeated callbacks and reopen do not add events or charges. |
| Deadline on last source | Failed state, original frozen version and hit contents retained, unknown counted once, no late publication, state retained across reopen. |
| Cancellation already committed by another Store | Cancelled state and event win; late deadline/result callbacks cannot change receipt, source states, or event sequence; reopen retains the winner. |
| Principal revocation already committed by another Store | Revocation event and cancelled state win; late callback cannot publish; owner access remains denied; repeated revoke and reopen do not change accounting. |
| Lost lease with a newer epoch active | Old deadline/result callbacks cannot kill the replacement source lease; the new source completes without retrying the unknown one. |
| Ordinary worker timeout | Existing `source_unknown` and pending-tail behavior remains; the distinct tail source can complete with two dispatched attempts and one unknown attempt. |

The probe uses an explicit deterministic Store clock, synthetic reviewed source
fixtures, and public Store mutations. SQLite access in the probe is diagnostic
SELECT only. There are no Worker calls, network requests, or sleeps. Temporary
databases and compiled binaries are outside the repository; the probe removes
its temporary database directories. This checks sequential transaction winners
through separate Store instances and persistence across reopen, not arbitrary
thread interleavings or physical power loss.

The transport classification itself was inspected independently in source.
Runtime coverage for the WebService/RPC boundary and the original loopback E2E
belongs to the author's separate `../search_deadline_event_race/` evidence and
the integrator's final run. This probe does not claim to execute that boundary.

## Preserved fixture preparation failure

The first run, `baseline-199` with `probe-freeze-v1.json`, failed all six fixture
setups before any product assertions because the direct Store fixture omitted
`activate_source_publication(registry)`, which WebService normally performs.
It ran zero assertions and is not product-regression evidence. Its logs and
record are preserved unchanged; the exact original source is
`fixture-v1-missing-activation.cpp`. The only fixture correction before freeze
v2 was explicit source-registry activation at the six setup sites. No product
expectation was relaxed. The meaningful baseline and corrected comparison both
use the same v2 source.

## Replay and provenance

`run_probe.py` verifies every captured header/library hash, the named source
freeze, and unchanged inputs after execution. It refuses to overwrite an
existing log, record, or executable. Each record includes the actual compile and
execution argv, exit status, UTC timestamps, log hashes, and binary hash.

For an existing captured snapshot, choose a new label for every replay:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 validation/s2_takeover/deadline_event_review/run_probe.py \
  --snapshot /absolute/path/to/captured-runtime \
  --label final-integrator-replay --expected-failed 0 --freeze probe-freeze-v2.json
```

A snapshot is an external directory containing `libbzs_core.a`, the matching
`include/bzs/` files, and `capture.json`. The capture JSON must contain
`copied_inputs`, a mapping of snapshot-relative library/header names to their
SHA-256 values, plus the actual source/build identity. The exact snapshot
contents used here are embedded in the execution records. Copy bytes and verify
their hashes before replay; never repoint the old baseline snapshot to a mutable
build output. Final integrated-library results require a fresh label and record.

`review.json` summarizes the independent conclusions and scope.
`author-commit-binding.json` records the later commit binding and checksums of
the preserved execution evidence. Original `tests/e2e_session_recovery.py` has
SHA-256 `cbd5a068e50596431658a3107ef7404e6c5b7fc5331ccabef300142c4b9059d5`
in baseline `3ef86c65...`, published `199eb635...`, and the reviewed fix.
