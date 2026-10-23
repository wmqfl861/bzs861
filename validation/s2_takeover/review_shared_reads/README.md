# Independent shared-read review

Reviewer: the source-publication/search-mode agent, independent of the shared-read
implementation author. Reviewed integrated commit
`fbc385f545826d62df2f7a0e0a3a9935368e1d3f`, including the shared-read implementation
introduced on the main branch by `f5e0373`. The review made no production-code
changes. Author validation remains in `validation/s2_takeover/shared_reads/`.

## Code findings

No blocking correctness issue was found in the reviewed boundaries:

- Co-dispatch occurs inside the same SQLite writer transaction as the payer's
  reservation. Matching requires the same owner, authorization epoch, creation
  deletion sequence, exact URL, item deletion generation, compatible deadline,
  effective timeout, attempts, and sufficient remaining grant. The captured
  current snapshot and page-publication sequence are copied to each subscriber.
- Each job retains a separate lease epoch. Pausing or cancelling a member only
  removes that member's publication eligibility. The physical attempt is
  cancelled only when no live member remains. The original payer retains its
  reservation even if it stops subscribing.
- Settlement is guarded by the physical attempt, payer identity, current item
  attempt, and unsettled state. Known usage is reconciled once to the payer;
  unknown execution retains that payer's complete grant. Associated usage on a
  subscriber is explicitly separate from its charged physical usage.
- Snapshot creation, all member receipts, shared settlement, and job transitions
  share one writer transaction. Retry replaces the item's current attempt without
  replacing historical group membership. Compaction first freezes the permanent
  summary and retains attribution while another member still has details.

## Additional execution probes

The reviewer built `bzs_core` from the fixed integrated commit in a separate
checkout, then linked `probe.cpp` against that library. No author test or fixture
was modified. All three probes passed, with **99 assertions and zero failures**:

1. A three-member group cancels its payer and pauses a second member while the
   third remains live. During that in-flight read, a `nosnippet` observation is
   followed by a newly permitted current representation. Only the live subscriber
   receives a receipt; the old response remains snippet-restricted and cannot
   replace the newer current representation. Only the original payer is charged,
   and a duplicate completion cannot charge again.
2. A shared attempt survives close/reopen and expires with unknown execution.
   Its former subscriber explicitly retries and becomes the payer of a new
   shared group. Late completion and failure calls from the original attempt
   cannot alter the current group. Both generations retain correct accounting;
   subsequent incremental compaction preserves permanent historical summaries
   and removes the unreferenced settled groups.
3. A SQLite trigger rejects the second successful member receipt after the first
   receipt and accounting writes have occurred in the transaction. The first
   receipt, snapshot, and provisional accounting all roll back. Failure
   settlement charges the single physical request once and leaves no member with
   a receipt or reserved grant.

`probe-first.log` is retained. That initial reviewer fixture supplied a versioned
publication policy without its required `http_status`; the core rejected the
malformed response. The fixture was corrected to report HTTP 200. No production
guard was changed. `probe-build.log` also retains the original fixture's compiler
warning; the final build has no warnings.

## Limits

This is an independent bounded review and interaction test, not complete
CACHE-01/B-24 acceptance. Recovery uses orderly Store close/reopen and a controlled
clock, not a power-loss experiment. The scope remains at most 32 already queued
same-owner members, with no late joining and no cross-kind request sharing. The
full integrated Linux, Windows, sanitizer, and transport gates are separate.
`report.json` records the exact reviewed source hashes, compiler/link commands,
library and probe hashes, and raw-log hashes.

To relink against a final Linux build from the repository root, write new logs:

```sh
c++ -std=c++20 -I include validation/s2_takeover/review_shared_reads/probe.cpp build/libbzs_core.a -lsqlite3 -lxml2 -lcrypto -ljson-c -pthread -o /tmp/bzs-shared-review-final
/tmp/bzs-shared-review-final tests/fixtures
```

The fixture-directory argument avoids depending on the reviewer's checkout path.
Final integration replays should retain their own binary/source bindings and
logs; the reviewer's original logs must remain unchanged.
