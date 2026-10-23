# Independent review of the MODE-01 increment

> **Correction (2026-10-23):** The initial no-blocker conclusion below is
> withdrawn for the deadline path. The reviewer incorrectly treated the public
> 1,000 ms minimum as a minimum for an admitted request's remaining time. The
> original 1-second recovery test exposed the resulting compatibility regression.
> This directory's probe, logs and report remain unchanged as historical evidence.
> See [the corrected deadline review](../search_modes_deadline_review/README.md).

Reviewed commit: `81337026084de2f9d503db8fdc0099c0f75720bf`.

**No blocking issue found within the documented source-search-only scope.**
This is a scoped review, not acceptance of the full MODE-01 package or a release.
The reviewer did not author MODE-01, source publication or principal revocation;
the reviewer did author shared `read_many` and does not independently sign off
that component.

## Findings and evidence

The `fast` / `balanced` / `deep` constants are exposed through `search.modes`.
All five numeric override dimensions tighten their selected preset. Explicit
source lists exceeding the cap fail; default/category selection is deterministic
and reports omitted IDs. Source reviews, shared quotas and publication checks
still run for selected sources. The cache key contains the effective mode and
budget, and cached output distinguishes those ceilings from actual usage.

Durable requests preserve their selected list and budget. The inspection found
no change to normalized JSON for requests without either `mode` or
`max_sources`; final integrated genuine-schema12 migration replay remains the
runtime proof for those old idempotency keys. Direct search uses a monotonic
deadline; Worker RPC and source adapter execution receive the remaining budget.
The Worker preserves subsecond values instead of rounding them up. Persisted
deadlines remain wall-clock based, as the feature documentation discloses.

The MCP raw-argument gate and CLI mapping preserve explicit grants without
silently expanding or clamping them. Independent execution of the adapter and
session adapter tests passed **80 tests, zero skips**.

Four additional reviewer-selected cases passed **30 assertions**:

1. A durable request with a fused limit below `per_source` keeps the fused result
   limit, candidate bound and source-attempt cap separately correct.
2. Tightening any of the five budget fields under an existing durable key is an
   idempotency conflict, with no Worker dispatch.
3. A durable dispatch with less than 1,000 ms remaining fails before calling the
   Worker and publishes no result version.
4. A politeness wait exceeding a tightened deadline fails before another Worker
   dispatch.

`reviewer_cases.cpp` reuses the author's offline fixture helpers and executes
only the separately selected `review_` cases. Exact compile and run commands,
source/library/executable hashes, output and timestamps are retained. The first
compile used the wrong relative reviewer-source path; the failure is preserved
and the succeeding command uses an absolute path. No product code changed.

These tests use offline fixtures and zero external requests. They do not verify
named-mode quality, automatic reading, models, browsers, or a full clock rollback
defense across restart. Complete final Linux/Windows and sanitizer checks remain
part of the parent's frozen integration gate.

To repeat the new cases, use the recorded compile command with the final
candidate's `include`, `tests`, `fixtures` and `libbzs_core.a` paths, then run its
generated executable. The candidate must contain `tests/search_modes.cpp`.
