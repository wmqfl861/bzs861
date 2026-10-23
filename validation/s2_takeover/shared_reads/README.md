# Shared read author verification

This directory records the author candidate derived from the published
`3ef86c653a2e2085848d5cdb151d66392c6af49a` input. It is separate from historical
0.1.9 validation and from the parent agent's final integration/review.

`report.json` binds the changed source inputs, Debug/ASan libraries and test
executables, commands, and retained logs. The containing Git commit supplies
the final report identity; no future publication SHA or GitHub CI is asserted.

The final focused Debug run passed batch **30 cases / 801 assertions**,
retention **16 / 404**, and fairness **11 / 251**, with no skipped suites.
Twelve new batch cases exercise shared dispatch, payer/subscriber cancellation
and pause, last-subscriber cancellation, authority/request/budget boundaries,
unknown recovery, explicit retry, transactional rollback, bounded membership,
two SQLite connections, and retention of cost attribution.

The ASan/UBSan batch executable passed the same **30 / 801**, with leak
detection enabled. The initial sandbox run failed because LeakSanitizer cannot
operate under its ptrace supervisor; `asan-batch.log` and its command record
retain that environment failure. The approved run outside that supervisor is
recorded in `asan-batch-permitted.*`; leak detection was not disabled.

`initial-targeted.*` is the earlier successful smoke run before adding the last
two behavioral cases. It does not replace the final source-bound run.

This evidence uses a synthetic Worker, controlled clocks where specified, and
SQLite connections. It does not prove real-network performance, arbitrary
power loss, full CACHE-01/B-24 acceptance, or genuine schema12-to-13 upgrade of
the complete integrated tree. That upgrade remains a separate final-library
gate. The old schema-shape regressions remain compatibility checks and are not
presented as genuine old-version library evidence.

The supported behavior and limits are specified in
[`docs/SHARED_READS.md`](../../../docs/SHARED_READS.md).
