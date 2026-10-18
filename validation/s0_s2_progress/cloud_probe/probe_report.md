# bzs861 source probe

Run `f8eb97723c4147e5bed7311eb26ab74d` — **completed**; schema 2.
Started 2026-10-18T08:27:11.005Z; checkpoint 2026-10-18T08:27:16.023Z.

| source | planned | attempts | answered rate | avg hits | avg ms | statuses | verdict |
|---|---:|---:|---:|---:|---:|---|---|
| marginalia | 2 | 1 | 0.0 | 0.0 | 3 | network_error:1 | needs_work |
| wikipedia_en | 2 | 1 | 0.0 | 0.0 | 5006 | network_error:1 | needs_work |

`reachable` means at least 70% of reserved attempts produced valid parsed results or a valid empty result. A bot check or rate limit always yields `blocked`, even after earlier successes. `needs_work` includes failures, incomplete attempts and sources with no eligible queries. These are availability observations; they do not approve a source or measure ranking relevance.

Attempts are reserved in the checkpoint before adapter execution. Quota, stop and cooldown state belong only to this run and survive `--resume`; completed and uncertain attempts are never automatically resent or refunded. Separate output directories and searchd have separate ledgers. Attempt counts are source-query reservations, not exact redirect/HTTP exchange counts. `response.external_requests` records available HTTP usage separately. Queue time is excluded from average elapsed time.

## Unexecuted and uncertain work

- marginalia: service_stopped:network_error (1)
- wikipedia_en: service_stopped:network_error (1)

`probe_report.json` contains the complete request plan, exact input and parser identities, safe response metadata and up to ten full parsed hits per attempt. Files under `raw/` are immutable gzip responses bound by decompressed length and SHA-256. Request credential headers are not recorded. Raw responses retain their original content for offline parser replay; review them before sharing.
