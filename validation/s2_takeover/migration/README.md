# Genuine schema 12 → 13 migration verification

This directory supplies a replayable integration probe. It creates the old
database through the real **0.1.9** library from commit
`3ef86c653a2e2085848d5cdb151d66392c6af49a`, then opens a copy with the new library.
It does not create a new database and lower its `PRAGMA user_version`.

## Review boundary and current evidence

The verifier authored the shared `read_many` increment, so these results are
**not independent review of shared reads**. The verifier did not author source
publication or principal revocation and separately checked their interaction.
The parent integrator owns final frozen-input reruns and release acceptance.

`preliminary/` records the initial combined-schema13 library run, before the
final HTTP cache corrections and MODE-01 integration. It passed **310 assertions
creating schema12** and **1,184 assertions upgrading/checking schema13**. This is
preliminary evidence, not a claim that a later final commit passed. The copied
new static library hash is recorded; `source_head` in a command record is the
working directory HEAD observed when the command ran, and does not relabel an
earlier copied library as a later revision. See `preliminary/summary.json`.

All Worker responses are explicit offline fixtures. The old fixture used seven
Worker calls and the upgrade exercise one; neither used external networking.

## What the probe checks

Before opening the old database with the new library, it records the old
table names, columns, row counts and SHA-256 hashes of sorted, canonical rows.
The new library must preserve **every original column of all 39 old tables**
exactly; new tables and appended columns are inspected separately. SQL used by
the probe is guarded with `sqlite3_stmt_readonly`; business records are created
through public APIs rather than SQL fixture injection.

The real old-library fixture contains:

- Imported and fetched snapshots, HTTP validators, pinned evidence, a deleted
  document/snapshot and its deletion generation.
- Terminal reads/crawls, a permanent archived idempotency key, an outcome-unknown
  read with conserved charge, a running read with a live reservation, and queued
  and paused reads/maps/crawls.
- Local and persistent web sessions, frozen results, events and source usage.
- Robots records for two owners, including a reviewed allow-all response.
- A real old backup that remains quarantined and schema12 after a rejected open.

After migration, and before exercising new behavior, the probe checks old public
field values recursively, allowing additive response fields. Source activation
has an explicit expected difference: the old web page is denied until the same
original reviewed source manifest is activated. Matching old sessions then keep
their original fields and old omitted-mode idempotency keys. Owner isolation,
deleted data, unknown/active reservations, archived idempotency and backup
quarantine remain effective. Old cache validators alone are not assumed fresh:
`cache_only` returns `E_CACHE_STALE` without calling the Worker, while explicit
evidence remains readable.

Only after those preservation checks does the probe exercise schema13 behavior:

- One physical shared read, cancelling its payer without cancelling the other
  subscriber; one physical charge and correct subscriber attribution.
- Durable principal revocation, repeated-revoke idempotency and denied access.
- Source `links_only` suppression of an existing web page, unchanged imported
  snapshot, principal revocation, then restoration of the original source
  manifest. Source restoration must not restore that principal's web session,
  local session or evidence access. Existing read charges remain unchanged and
  another owner remains usable.
- New backup quarantine, `integrity_check=ok`, and zero foreign-key violations.

The probe checks one populated old database and these defined transitions. It
does not certify all schema histories, live services, safe backup restoration,
full B-24, or a final release by itself.

## Replay against frozen inputs

Build the exact baseline separately and build the candidate after its code
freeze. The already prepared baseline worktree is
`/workspace/scratch/e2371129386d/bzs861-migration-old`.

```sh
cmake -S /path/to/clean-baseline -B /path/to/clean-baseline/build-migration-old -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBZS_BUILD_TESTS=OFF
cmake --build /path/to/clean-baseline/build-migration-old --target bzs_core -j 2
cmake --build /path/to/candidate/build --target bzs_core -j 2

python3 validation/s2_takeover/migration/replay.py \
  --old-root /path/to/clean-baseline \
  --new-root /path/to/candidate \
  --work-dir /path/to/new-empty-migration-run \
  --records-dir validation/s2_takeover/migration/final
```

Both output directories must be new; the script refuses to overwrite evidence.
It freezes each library and its headers, compiles the probe twice with warnings
as errors, recreates the genuine schema12 input, upgrades only a copy, records
binary/library/source/DB hashes, and fails if either source or library input
changes during verification. The parent should bind the final report to the
candidate build record as well; a copied library is not proof by itself that its
source was rebuilt. Generated DBs, libraries and executables remain in the work
directory, while reproducible text evidence goes in the records directory.

The command recorder stores exact argv, working directory, exit status, UTC
times, source HEAD, tracked patch hash, input hashes and complete output. A
failed command remains in its own record. The preliminary compiler rejected two
unused old-only helpers under `-Werror`; `[[maybe_unused]]` fixed that harness
issue. An initial recorder argument-order mistake and a generated executable
mode issue prevented process launch; these are also disclosed in the preliminary
summary. The first driver source is retained byte-for-byte so the original
old-input provenance hash can be reconstructed.
