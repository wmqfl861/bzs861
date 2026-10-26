# Cancellation retention candidate handoff

Recorded: 2026-10-26 UTC (2026-10-25 evening in America/Los_Angeles).
Repository: `wmqfl861/bzs861` only. Published input: `6410d272a2adefe25386af766da9d389a8a4d347` / tree `0a614980a651ba457eab0ef97931a40b80644d6f`.

This independent candidate follows [development blueprint v1.2](../planning/development_blueprint_v1.2.md), CACHE-01 / FT-032 / B-24, by fixing two durable Worker cancellation boundaries. It does not claim the broader sharing milestone or change the existing plan. The original main handoff remains historical input for this candidate, not proof of its verification.

Read [scope and evidence](../../validation/cancel_retention_20261026/README.md), [report](../../validation/cancel_retention_20261026/report.json), and the [complete production patch](../../validation/cancel_retention_20261026/implementation.patch). Exact original failure logs are preserved. Only `_attempt_id` and `fetch_cancel` change; the new test module has 54 cases. Local execution is explicitly AST-method/RPC-handler isolation, not full package integration or an independent reviewer signoff.

Publication sequence: publish `work/cancel-retention-20261026`, run the existing unmodified Linux/Windows checks on that exact SHA, inspect failures, and only then fast-forward `main` if its fresh head still matches the expected parent. Never replace a concurrently advanced main. CI conclusions must be recorded after observation, not inferred from baseline run 40183102494.

If the prior session's unpublished schema16 / `join_read_url` implementation becomes available, inspect its actual source and commits before combining this patch. None of that unavailable work was recovered, replaced, or included here. The present tree deliberately keeps schema15 and all baseline history. Cloud-computer verification and full product acceptance remain outstanding.

After integration, resume the existing B-24 compatibility work with budget, timeout, redirect-body, truncation, immutable payer, permission/publication and cancellation boundaries intact. This patch does not add shared dispatches across direct/durable/map/crawl/search, persistent direct-read recovery, independent revocation continuity, source approval or human relevance judgments.
