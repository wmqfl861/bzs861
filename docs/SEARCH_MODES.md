# Search execution presets (MODE-01, partial)

`search.modes` returns the locally available `fast`, `balanced`, and `deep`
presets. It does not contact a source and works without a configured Worker.
`web.search` and `web.search.start` accept an optional `mode` with one of these
three values. The presets cover source search only. They do not read result
pages, open a browser, call a model, or promise a level of answer quality.

## Budgets

| Mode | Maximum sources | Result limit | Candidates per source | Source timeout | Search deadline |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fast` | 2 | 10 | 5 | 4,000 ms | 20,000 ms |
| `balanced` | 4 | 20 | 10 | 8,000 ms | 60,000 ms |
| `deep` | 8 | 50 | 20 | 15,000 ms | 180,000 ms |

Each budget has version `bzs-search-mode-1`, scope `search_only`, and
`max_reads = max_model_calls = max_browser_calls = 0`. The result limit bounds
the fused result list; `per_source` bounds candidates accepted from each source.
`max_source_attempts` counts source-search operations, not individual HTTP
redirects within a source adapter. No mode retries failed source attempts or
starts additional sources to compensate for a source that failed admission.

Optional `max_sources`, `limit`, `per_source`, `timeout_ms`, and `deadline_ms`
overrides must be integers. An override can tighten the selected preset, but
cannot exceed it. Invalid or excessive values produce `E_SCHEMA`; adapters do
not silently clamp them. Source counts, result limits, and per-source candidate
limits have minimum 1; the public timeout and deadline minima are 1,000 ms.

Omitting `mode` preserves the previous source, candidate, and timeout defaults:
up to 20 sources, 10 candidates per source, and an 8,000 ms timeout. Synchronous
search defaults to 10 results and durable search to 20. The default deadline is
120,000 ms (already used by durable sessions and now also applied to direct
search). Legacy explicit ranges remain
available: at most 50 results, 30 candidates per source, a 30,000 ms timeout, and
a 600,000 ms deadline. The label `legacy` appears in output and is not an accepted
explicit `mode` value. `max_sources` can also tighten an otherwise legacy search.

## Source selection and existing restrictions

An explicit `sources` array is deduplicated without reordering. If its distinct
source count exceeds the budget, the request fails with `E_SCHEMA` before any
source dispatch. An empty explicit array is rejected; it never selects defaults.

For category/default selection, an explicit preset or `max_sources` selects the
first eligible source IDs in configured manifest order, up to its cap. Every
remaining eligible ID is reported in `omitted_sources`, in that same order.
This selection is deterministic and does not silently replace an unapproved,
unavailable, or exhausted selected source. The existing source review,
publication, shared quota, concurrency, politeness, and category rules still
apply. A preset does not enable any disabled source.

Search results include:

```json
{
  "effective_mode": "fast",
  "effective_budget": {
    "version": "bzs-search-mode-1",
    "scope": "search_only",
    "max_sources": 2,
    "selected_sources": 2,
    "max_source_attempts": 2,
    "limit": 10,
    "per_source": 5,
    "timeout_ms": 4000,
    "deadline_ms": 20000,
    "max_reads": 0,
    "max_model_calls": 0,
    "max_browser_calls": 0
  },
  "omitted_sources": ["another-configured-source"]
}
```

These are configured ceilings. Existing `sources` statuses and usage fields
describe actual admission and execution; a blocked or exhausted source does not
mean that its full budget was used. A cache hit retains the same effective
budget and does not dispatch sources. Cache identity includes the mode and
effective overrides, so a result from a broader grant cannot answer a narrower
one through cache reuse.

## Time and durable sessions

Synchronous execution uses a monotonic deadline spanning admission waits,
Worker dispatch, and result publication. Both the Worker timeout and its RPC
timeout are bounded by the remaining deadline. A late response is rejected with
`E_DEADLINE` and is not cached. A subsecond remaining Worker allowance is passed
as its exact positive millisecond value; the Worker does not round it up to a
second.

For durable sessions, the selected source list, budget, and omitted IDs are
stored with the request. `web.search.start`, `session.get`, and `search.page`
expose the same metadata after restart. The session deadline includes queue
time. Each leased source dispatch gets at most the session's remaining time;
any positive millisecond remainder may be dispatched, even below the public
1,000 ms request minimum. The original public request is validated separately;
an internal absolute monotonic deadline bounds its remainder without restarting
the allowance or rounding it up. An exhausted remainder fails before dispatch. Existing
lease, cancellation, owner, result-version, and publication fences still apply.
When the remaining search deadline limits the RPC timer, a typed Worker timeout
ends the search with `E_DEADLINE`. A durable session records one terminal `failed`
event and counts the leased attempt as unknown once, even if the integer RPC
timer fires just before the persisted wall-clock deadline. Pending sources are
not dispatched, and earlier frozen versions remain available. A shorter source
RPC timeout or an early unavailable Worker retains the existing `source_unknown`
handling and may proceed to the pending tail within the original deadline.
An in-flight dispatch uses its own monotonic clock; the persisted session
deadline remains a wall-clock deadline. A complete cross-restart clock-rollback
defense is not claimed by this increment.

Repeating a key with a changed explicit mode or budget produces the existing
`E_IDEMPOTENCY_CONFLICT`. Requests with neither `mode` nor `max_sources` keep
their previous normalized request and fingerprint, allowing an unchanged legacy
idempotency key to be replayed after migration. Output metadata for those older
requests is derived without rewriting the stored request JSON.

## API and adapters

Inspect the presets through the core:

```json
{"operation":"search.modes","params":{}}
```

Perform a bounded search, with optional tighter overrides:

```json
{
  "operation": "web.search",
  "params": {
    "query": "search engine indexing",
    "mode": "balanced",
    "max_sources": 2,
    "deadline_ms": 20000
  }
}
```

For a durable session, use `web.search.start` with these fields plus an explicit
`idempotency_key`, then use the existing session status, events, and page APIs.

The MCP adapter exposes `list_search_modes`, and mode/budget arguments on
`web_search` and `start_web_search`. Optional fields must be omitted to use
defaults: explicit nulls, numeric strings, booleans in integer fields, unknown
fields, and strings in place of a source array are rejected before core calls.
The core remains responsible for numeric ranges and source approvals.

The Python CLI supports:

```sh
bzs search-modes
bzs search "search engine indexing" --mode fast
bzs search "search engine indexing" --mode deep --max-sources 2 --deadline-ms 20000
```

Additional flags are `--limit`, `--per-source`, `--timeout-ms`, `--source ID`
(repeatable), and `--refresh`. It prints the effective budget and omitted IDs.
The native CLI supports `searchctl search-modes`; its existing `call` command
accepts complete core JSON requests for both synchronous and durable modes.

This is partial MODE-01 delivery. Automatic reading/research, model allocation,
browser fallback, and quality evidence for a named mode remain future work.
