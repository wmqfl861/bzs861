# Windows 11 takeover validation — 2026-10-28

Input `8a7d552bb3199dce1333e22cde3d4f666d3e21df` (PR #7 head, CI 40617784255
success confirmed for that input only). Work branch
`work/windows11-audit-20261028-1100`; eight commits, each an isolated defect
fix with a reproduced counterexample, minimal fix, regression test and re-run.

Results on this machine (MSVC 2022 17.14, vcpkg @ 6719fd4d, Python 3.12.10
.venv, ACP 65001): see windows11-results.json and audit-coverage.json.
Raw CTest/pytest/live logs are kept outside the repository per the 50 KB
validation rule (local delivery directory on the Windows machine).

Fixes (see docs/handoff/WINDOWS11_TAKEOVER.md for full narrative):
6fcf790 launcher rollback isolation + stop-every-component + exit races;
8478df2 SQLite UTF-8 paths on Windows + fts_integrity_checked marker +
unicode/space path test group; 98f7b31 fixture lane E_SHUTDOWN on stop;
0e761a9 pytest ids inside the Windows 32767-char env cap; 4422e2e venv
launcher-stub interpreter-tree termination; cc704c3 fetch.profile timeout
fallback for drop-instead-of-refuse hosts + e2e tree kills + explicit sqlite
close + test_10 wall-clock decoupling; 6f80450 measured Windows watchdog
raise for direct_shared/sessions; b93d6fe SO_EXCLUSIVEADDRUSE port double-bind
refusal.

No production source, site or credential was touched; sources remain
disabled; schema15 unchanged; D1–D7 remain pending; PR #5/#6/#7 untouched.
