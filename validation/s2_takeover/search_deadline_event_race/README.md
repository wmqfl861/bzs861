# Search deadline and terminal-event race

Author validation of the minimal production correction based on
`199eb635ca90c657dff673fbf88bed44e7c4b40b`. This directory preserves both the
deterministic old-code failure and the corrected results. The independent
reviewer and the final integrated CI run have separate evidence.

## Failure and correction

The RPC timer receives the floor, in milliseconds, of the remaining monotonic
search deadline. When that remaining deadline limits the RPC, its timer may
return `E_WORKER_TIMEOUT` while the durable wall clock is still one millisecond
before its deadline. Previously, `Store::fail_search` recorded every error as
`source_unknown` and requeued a pending tail. Subsequent wall-clock settlement
then recorded `failed`, producing the extra event seen by the original recovery
test. An explicit `E_DEADLINE` on the final source could instead incorrectly
complete the session.

The correction has two parts:

- A typed `E_WORKER_TIMEOUT` becomes `E_DEADLINE` only when the total remaining
  deadline was the limiting RPC grant. The existing transport emits this code
  for its timer; remote Worker errors have a different code. No timeout is
  rounded up and no deadline is reset.
- For a still-current lease, `Store::fail_search(E_DEADLINE)` atomically records
  terminal `failed`, advances the epoch, counts one unknown dispatched attempt,
  and records one `failed` event. Pending sources are not dispatched. The prior
  result version, deadline, budget, and used-request count are preserved. The
  existing owner/auth/lease guard runs before any mutation.

Ordinary shorter source RPC timeouts and early Worker unavailability retain
their previous `source_unknown` handling. A pending tail can still run within
the original deadline. The typed-error distinction is tested even when an
early unavailable Worker has a deadline-limited RPC grant.

## Actual validation

| Capture | Result | What it establishes |
| --- | --- | --- |
| `before-native` | 22 cases, 307 reached assertions, 3 failures | Unmodified production code at the base revision reproduces both legacy and explicit-mode extra events, plus final-source false completion. |
| `after-native` | 22 cases, 382 assertions, 0 failures | Same frozen test bytes pass after the correction; source-error controls and stale callbacks remain valid. |
| `after-sessions` | 22 cases, 285 assertions, 0 failures | Existing native session behavior remains valid. |
| `after-original-test06` | Fixture startup error | Default sandbox prevented the existing localhost fixture from binding; retained as environment failure evidence. |
| `after-original-test06-loopback` | 1 real HTTP case passed | Unchanged original deadline case, with loopback permission, observes one attempt, one unknown, one source HTTP request, and two events. |
| `after-original-recovery-suite` | 6 real HTTP cases passed | All original recovery expectations pass without test changes, skips, or retries. |

The deterministic new cases do not sleep or introduce production test hooks.
They use the pre-existing fake Store clock and WorkerPort boundary to deliver a
typed timeout at durable deadline minus one millisecond. Before the production
change, both modes print `queued, source_unknown, failed`; afterward they print
`queued, failed`. The last-source case preserves an earlier frozen version and
page across Store restart, checks `queued, result_version, failed`, and rejects
duplicate/late callbacks without another unknown charge. Control cases prove
that a shorter source timeout and early unavailable Worker continue to the next
source, while an earlier lease cannot fail the new lease.

The exact original `tests/e2e_session_recovery.py` SHA-256 remains
`cbd5a068e50596431658a3107ef7404e6c5b7fc5331ccabef300142c4b9059d5`.
Its bytes match the base revision. No public parameter minimum, cache contract,
schema, adapter, source policy, CMake target, or CI selection is changed here.
This author validation did not rerun the full 27-suite gate, sanitizers, or
Windows; the root integration run owns those results.

## Reproduction

`capture.py` records the command, timestamps, exit status, current base revision,
relevant source hashes, whether they match that base, and the full output. It
refuses to overwrite an existing label. Use a fresh label for every run.

```sh
cmake -S . -B /tmp/bzs-deadline-review -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/bzs-deadline-review --target bzs_search_mode_tests bzs_session_tests searchd -j 2
python3 validation/s2_takeover/search_deadline_event_race/capture.py --label replay-native -- /tmp/bzs-deadline-review/bzs_search_mode_tests
python3 validation/s2_takeover/search_deadline_event_race/capture.py --label replay-sessions -- /tmp/bzs-deadline-review/bzs_session_tests
python3 validation/s2_takeover/search_deadline_event_race/capture.py --label replay-recovery -- /path/to/pinned/venv/bin/python tests/e2e_session_recovery.py --server /tmp/bzs-deadline-review/searchd --python /path/to/pinned/venv/bin/python --repo "$PWD"
```

The real recovery fixture requires permission to bind ephemeral loopback ports
and the project's pinned Python test dependencies. It does not use external
services, fixture database mutations, or an injected clock. To reproduce the
old-code native failure, create a separate checkout of the base revision, copy
only the frozen `tests/search_modes.cpp` from this change, build its existing
`bzs_search_mode_tests` target, and run it; keep that checkout separate from the
corrected production inputs.
