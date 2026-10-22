# Real schema 11 to schema 12 migration review

`migration_driver.cpp` is compiled twice against frozen copies of the actual
old/new public headers and static libraries. It does not edit SQL business
records or lower `PRAGMA user_version`. Every SQL statement issued directly by
the driver must satisfy `sqlite3_stmt_readonly`; all fixture creation and
post-migration changes use public Store/WebService APIs.

The old library is from commit `2c536e9834c4897b0c70e3bd6651b765cc67f45e`.
The new library is from the code freeze at
`d9fba205a66b31aa89a761c3b7606cd456aaf1fa`. Library capabilities returned 0.1.8
and 0.1.9 respectively. Schema 11 is observed before the new library opens the
database; schema 12 is observed after its normal Store constructor migrates it.

Exact compiler commands, source/library hashes, output logs and exit codes are
in `compile_old_final.json` and `compile_new_final.json`. Both builds used C++20,
Debug flags and `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror`.
They link SQLite, libxml2, json-c, OpenSSL Crypto and pthread, without sanitizers.

From this directory, repeat using a fresh output path:

```bash
./migration-old /tmp/bzs-schema12-review-fresh
./migration-new /tmp/bzs-schema12-review-fresh
```

The old executable rejects an existing `store.db`; the new executable requires
schema 11 before opening. The recorded `actual-db/store.db` has already been
migrated, so it is not a repeat target. `store-schema11-original.db` is the exact
closed schema-11 file copied before migration, with its SHA-256 bound in
`frozen_inputs.json`.

The fixture includes imported and web snapshots, evidence, deletion, local and
persistent web sessions, eight jobs across two owners, an aged compacted
permanent key, running/queued/paused tasks, terminal receipts, and robots
`rules` / `allow_all` records. The new executable first compares every old
column and row of 39 tables, then checks API behavior, new safe defaults,
full-200-before-304 behavior, retained robots references and backup quarantine.

All HTTP/source responses are explicit offline WorkerPort fixtures. No real
external fetches or provider approvals are established. The clock advances
eight days for retention and later two one-hour TTL intervals; the run does not
claim real-time soak, process interruption or physical power-loss durability.

`REVIEW_RESULT.json` contains the scoped outcome and artifact hashes. The first
old compilation's driver-only warnings are retained in `compile_old_initial.*`;
the final strict builds are warning-free. Old creation and new verification
both succeeded on their first execution.
