# bzs861 source probe

Run `e61529fac4944363beb02f012ad7a02e` — **completed**; schema 2.
Started 2026-11-14T10:48:12.222Z; checkpoint 2026-11-14T10:48:32.736Z.

| source | planned | attempts | answered rate | avg hits | avg ms | statuses | verdict |
|---|---:|---:|---:|---:|---:|---|---|
| marginalia | 3 | 1 | 0.0 | 0.0 | 1937 | rate_limited:1 | blocked |
| mwmbl | 3 | 3 | 1.0 | 7.7 | 880 | ok:3 | reachable |
| wikipedia_en | 3 | 3 | 1.0 | 7.0 | 843 | ok:3 | reachable |
| wikipedia_zh | 3 | 3 | 1.0 | 10.0 | 807 | empty:1, ok:2 | reachable |

`reachable` means at least 70% of reserved attempts produced valid parsed results or a valid empty result. A bot check or rate limit always yields `blocked`, even after earlier successes. `needs_work` includes failures, incomplete attempts and sources with no eligible queries. These are availability observations; they do not approve a source or measure ranking relevance.

Attempts are reserved in the checkpoint before adapter execution. Quota, stop and cooldown state belong only to this run and survive `--resume`; completed and uncertain attempts are never automatically resent or refunded. Separate output directories and searchd have separate ledgers. Attempt counts are source-query reservations, not exact redirect/HTTP exchange counts. `response.external_requests` records available HTTP usage separately. Queue time is excluded from average elapsed time.

## Unexecuted and uncertain work

- marginalia: service_stopped:rate_limited (2)

`probe_report.json` contains the complete request plan, exact input and parser identities, safe response metadata and up to ten full parsed hits per attempt. Files under `raw/` are immutable gzip responses bound by decompressed length and SHA-256. Request credential headers are not recorded. Raw responses retain their original content for offline parser replay; review them before sharing.
