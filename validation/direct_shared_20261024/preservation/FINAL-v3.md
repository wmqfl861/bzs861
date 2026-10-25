# Final static preservation record: v3 / formal test v6

This is the final source-preservation record after the root-authorized capacity
fixture timeout configuration. It supersedes the v2 record for current-input
binding, while every earlier source, failure and audit record remains unchanged.
No product tests were run by this audit.

- Baseline: `346048b4ae329e750d15f1e35952aab4513e3912`; tree `458a6f321e94defd74616ba6ebbf2983252cb495`.
- Current formal `tests/direct_shared.cpp`: `4e94e5e4ed979ff7a67bb5aba34c8540f0a081cf987caf64869b55253fdf163e` (frozen v6, 23 source-declared groups).
- Report: `audit-v3.json`, SHA256 `abb2e80e1e9b7099cd41ac8de6b0cafaea49f1b187b371db1e0d4afa97dc6d21`.
- Re-runnable script: `audit_preservation_v3.py`, SHA256 `2258aa3a609468d8b7eba624cdc35de3b39edf8de5b982985636ebf5d44af36b`.
- Result: 38 static checks passed; zero discrepancies; zero runtime product tests executed.

All 3,590 historical validation paths match the before inventory, exact baseline
Git blob bytes and current file bytes. The 122,468-byte original `features` array
retains SHA256 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`.
The 24 planning files, 2 config files, 2 golden files, 5 fixtures, lock/version
inputs, schema migration and index marker retain their original bytes. Original
83 / 161 / 10 planning totals, 37 partial / 46 not_started / 0 accepted, 17 sources
with 0 enabled or approved, and 0 sites are unchanged.

The build-gate reverse comparisons and old-test assertion review described in
`README.md` also pass against this final input. There is one documented old
cache-only assertion compatibility adjustment, rather than a claim that every
old assertion byte is unchanged. All eight unsupported-profile fake branches
and the HeldWorker HTTP fixture extensions reverse exactly to their originals.

The v3 audit additionally proves the complete new-regression chain: removing the
23rd group from final v6 restores the 22-group v3 file; v4-to-v5 removes only the
incorrect new representation-policy assertion; v5-to-v6 changes only the two
capacity-member timeout values and synthetic RPC template. All v5 assertions,
all original 22 groups, production timeout rules and CTest watchdogs are retained.
The timeout adjustment follows the actual passing v5 Debug run (23 groups,
3,636 assertions, 0 failures; 20.429 seconds) recorded by root in
`../focus/direct-v4*`. This historical run is not a v6 runtime result.

For another audit after a later approved documentation/publication update, use a
new evidence filename; the script refuses to overwrite existing JSON or sidecars:

```sh
python3 validation/direct_shared_20261024/preservation/audit_preservation_v3.py --output validation/direct_shared_20261024/preservation/audit-v4.json
```

The original v1 audit's only failure was an incomplete allowed-change inventory
that omitted the final README update. Its script and failed record are retained;
v2 explicitly records that correction. No old validation file or failed runtime
was changed to obtain the passing result. Runtime Debug/ASan/HTTP/independent/CI
claims remain owned by their separate root or reviewer records.
