# Search deadline compatibility correction

This author correction addresses the real compatibility regression found by the
parent's final v1 integration gate. MODE-01 had reapplied the public 1,000 ms
minimum to a durable request's remaining execution time. A valid request with a
1,000 ms initial deadline would usually have less than 1,000 ms left by dispatch
and could therefore never reach its first source.

The previous assumption that every subsecond remainder should prevent dispatch
was incorrect. Historical author and independent-review logs are preserved.
The original `tests/e2e_session_recovery.py` is unchanged, including the test 06
gate-entry assertion, request counts, events, and no-tail-dispatch assertions.

## Production correction

`execute_search` captures a steady-clock start before reading the remaining
persisted wall-clock budget. A positive remainder becomes an internal absolute
steady-clock deadline. The original public `timeout_ms` and `deadline_ms` values
remain unchanged when validated by `search_mode`.

`WebService::search` takes that deadline through an optional fourth C++ argument,
outside public JSON. It uses the earlier of the public request's deadline and
the internal absolute deadline. Argument preparation and validation time consume
this allowance; they do not restart it. Worker and RPC timeouts remain bounded
by the actual positive millisecond remainder. Zero/expired time prevents dispatch,
and late response publication remains fenced. Public timeout/deadline values
below 1,000 ms are still rejected. No automatic retry or deadline reset was added.

## Evidence

- The two new native legacy/fast cases each start with a valid 1,000 ms deadline,
  consume 500 ms before dispatch, then require exactly one Worker call with both
  Worker and RPC timeouts at most 500 ms. They also verify unchanged persistent
  deadline and budget metadata. Both failed against the pre-fix production code;
  that run is preserved as `native-regression-before.log`.
- Four new cases include these two successful subsecond dispatches, rejection of
  public 500 ms timeout/deadline values, and no dispatch/publication after expiry.
  Final search-mode suite: **17 cases, 182 assertions, zero failures**.
- The original native session suite and mode suite both pass: **2 suites**,
  17.57 seconds, no skips.
- The unchanged original real recovery test 06 passes by itself: **1 test**,
  1.598 seconds. It observes one real local source HTTP request, one dispatched
  attempt, one unknown attempt, two events, and no published result version.
- The complete unchanged real recovery suite passes: **6 tests**, 26.924 seconds,
  no failures or errors. These use real local core/Worker processes, loopback
  source HTTP, process SIGKILL where specified, and read-only database observation;
  the fixture does not inject clocks or mutate the database.

`report.json` binds commands, source and binary hashes, logs, and the unchanged
E2E script hash. This is the isolated author's compatibility fix verification;
the parent will separately bind full final integration gates. It does not claim
power-loss coverage or an additional author sanitizer run for this correction.
