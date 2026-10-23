# Independent review of Windows HTTP fixture connection cleanup

Reviewer: `/root/source_policy`. This reviewer did not author the revocation
increment or its connection-close correction. This directory contains separate
review evidence; it does not edit production code or the HTTP test fixture.

## Actual prior Windows result

GitHub Actions run `39945994760`, Windows job `120388675036`, checked the exact
published commit `a06e7e91f6a115b345ff1072095b80e15d193ad4` (tree
`fad2c76d98602ec076a8a0eb770ae23055deda25`). The Windows Debug build succeeded.
All 15 native suites and `http_cli` passed. `http_revocation` was the only failed
CTest suite: its two tests raised `PermissionError [WinError 32]` while deleting
`metadata.sqlite` in `tearDown`, after their HTTP assertions. Total CTest time was
347.59 seconds. The final job conclusion was failure at 11:48:24 UTC on 2026-10-23.

Job: <https://github.com/wmqfl861/bzs861/actions/runs/39945994760/job/120388675036>

The root integrator owns the full raw CI logs. This directory records the
independent portable reproduction and its exact input binding.

## Portable regression

`probe_sqlite_lifecycle.py` loads both the HTTP fixture and its integration helper
from the requested Git commit, without editing or checking out either file. It
uses real SQLite connections and keeps strong references to them so garbage
collection cannot hide an omitted `close()`. It checks successful return and a
SQL query exception. Both paths must preserve the original query behavior, the
read-only URI, and explicitly close the connection before returning or raising.

The probe closes all diagnostic handles before deleting its own temporary
directory, so the expected failure is reproducible on Windows as well as Unix.
It starts no HTTP server and does not simulate or claim a Windows CI run.

From a checkout containing this review script and the target commit:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 validation/s2_takeover/ci_followup_review/probe_sqlite_lifecycle.py --repo . --ref a06e7e91f6a115b345ff1072095b80e15d193ad4 > /tmp/bzs-before-lifecycle.json
```

**Expected old-code result: exit 1, two failures.**
`before-a06-lifecycle.json` is the actual frozen baseline run. Both SQL outcomes
and read-only URI checks pass; both explicit-close checks fail. The probe source
and source-file hashes are included in that output.

For the correction, rerun the identical script with only `--ref` changed and a
new output filename. The desired result is exit 0 with both cases passing. Keep
the old failure output unchanged. A fixed-ref pass establishes the helper's
resource lifecycle; final Windows CI remains a separate execution gate.
