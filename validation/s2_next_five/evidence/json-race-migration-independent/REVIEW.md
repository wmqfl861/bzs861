# Independent rerun of the existing schema 11 → 12 migration driver

## Outcome

Passed on newly created `fresh-db`, with the unchanged driver originally authored by `session_faults`. This is an independent execution and artifact comparison; no new test design is claimed.

- Old executable: verified baseline `2c536e9834c4897b0c70e3bd6651b765cc67f45e`; runtime 0.1.8, schema 11; 308 logged assertions; 7 offline fixture calls.
- New executable: independently compiled against frozen headers and library from `79df58127b3458dc3b270f37e727e5d8277c8544`; runtime 0.1.9, schema 12; 334 logged assertions; 12 offline fixture calls.
- Strict C++20 Debug compile, including `-Werror`, completed successfully with an empty warning/error log. Both executions completed successfully on their first attempt.
- A closed schema-11 database copy was saved and hashed before the new executable opened the live test database. The path did not exist at input freeze; the old executable also rejects a preexisting `store.db`.
- All 39 legacy tables retain identical old column lists, row counts and canonical row hashes immediately after migration. The detailed comparison is in `REVIEW_RESULT.json`.
- Five old snapshots receive `http_cache_bytes=0`; two old robots records receive `http_cache_json={}`. The original driver checks retained public API behavior, prior deletion/owner restrictions, full-200-before-304 behavior, preserved robots references and quarantined backups.
- Final integrity check is `ok`, foreign-key violations are 0, and actual network requests are 0.

## Reproduction and provenance

`compile-new.json`, `old-create.json` and `new-verify.json` preserve the exact argv, timestamps, cwd, source commit, input hashes, elapsed time, exit status and matching raw log hash. `frozen-inputs.json` binds the original driver, verified old executable, new library, headers and production sources. `pre-migration-snapshot.json` binds the closed schema-11 database before migration. All file hashes are in `REVIEW_RESULT.json`.

Use another completely fresh directory when repeating. This `fresh-db/store.db` is already schema 12 and cannot serve as a schema-11 input again. The original `schema12-migration/actual-db` was neither reused nor changed.

The JSON output files record 306/332 assertions before their two output-file checks. The successful process logs record the final 308/334.

## Scope

The old executable was reused with its independently checked hash. Only the new executable was compiled in this rerun. All WorkerPort responses are offline fixtures, and time is controlled. This does not establish real-provider approvals, real-time soak, process-interruption or power-loss durability, or acceptance of the full work package. No main repository files were changed by this reviewer.

`archive-text-files.json` lists the text artifacts suitable for repository archiving. Binary databases, executables and the static library remain in the evidence directory and are bound by hashes.
