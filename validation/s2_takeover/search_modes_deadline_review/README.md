# Correction of the MODE-01 deadline review

**Verified fixed on integrated commit `ff193ba1000912ce400d2ed4503917f823be0aae`.**
The same corrected probe that failed before the fix passed **4 cases / 59
assertions** afterward. The unchanged original real-process one-second recovery
case passed in **1.611 seconds**: one real fixture source HTTP request, one
unknown attempt, two events, no result version, and no tail-source dispatch.
The reviewed library and server hashes are recorded in `report.json` and were
rechecked against the main integration build after execution.

## Withdrawn assumption and preserved evidence

The first review incorrectly expected any durable request with less than
1,000 ms remaining to stop before dispatch. That expectation confused the
**public request minimum** with the **remaining execution allowance** of an
already admitted request. A valid 1,000 ms request normally has less than that
remaining by the time it is dispatched; the added check rejected normal work.

The original `e2e_session_recovery.py` one-second deadline case exposed this
compatibility regression during the complete integration gate. The initial
review's no-blocker conclusion is withdrawn for this path. Passing the former
`review_subsecond_persistent_remainder_never_calls_worker` case was evidence of
the incorrect assumption, not proof of correct behavior.

The previous probe, command logs and `report.json` are retained byte-for-byte in
`../search_modes_review/`. Its README now links to this correction. The new probe
was run against the pre-fix integrated library first: **two of four cases
failed**, because the 500 ms and late-result scenarios never reached the Worker.
The failed output and input hashes are retained here.

## Correct requirement and reviewed design

Public `timeout_ms` and `deadline_ms` still require at least 1,000 ms. An admitted
durable request can dispatch with a positive 1–999 ms remainder. Its Worker and
RPC timeouts must not exceed the actual remaining allowance; the remainder must
not be rounded upward, reset to the original grant, or restored during argument
construction and validation.

The fix carries an **absolute monotonic deadline** separately from the public
JSON. `execute_search` captures its monotonic starting point before reading the
persisted deadline, forms that absolute limit, and retains the original valid
public timeout/deadline fields. `search` validates those original fields and
uses the earlier of the public deadline and the internal absolute deadline.
Existing checks still prevent expired dispatch and late result publication.
No HTTP/MCP parameter exposes this internal allowance.

`deadline_cases.cpp` executes four corrected review cases, using the author's
offline fixture helpers:

1. Public 1/500/999 ms values remain invalid for both public fields, and unknown
   JSON fields cannot inject an internal remaining-time allowance.
2. Both legacy and explicit fast requests dispatched with 500 ms remaining
   call the Worker with positive Worker/RPC timeouts of at most 500 ms, retain
   their original stored deadline and public budget, and can complete on time.
3. A truly expired lease calls no Worker and publishes no result version.
4. A late response is rejected after the remaining allowance, with one unknown
   attempt, no result version, and no tail-source dispatch.

The original real-process `test_06_real_deadline_rejects_late_response_and_never_dispatches_tail`
must also pass without editing its test source. That case observes actual
loopback source HTTP, one unknown attempt, retained receipts and events, and no
tail dispatch. It complements the controlled-clock probe rather than replacing
it. Final run commands, hashes and results are recorded alongside this file.

The first real-process invocation could not start its fake source because the
default sandbox denied loopback socket binding. That failure is retained in
`final-original-test06.log`. The identical test command and server bytes passed
after automatic approval enabled the local loopback fixture. The test source
hash is identical to the original schema12 baseline; no test timing or assertion
was relaxed. No external source was contacted.

## Review limits

This corrects the specific MODE-01 regression. It is not full MODE-01 acceptance,
a shared-read independent review, or a claim of clock-rollback protection across
restart. The reviewer authored the shared-read increment, did not author MODE-01,
and owns the incorrect initial review assumption and this correction.
