# HTTP revocation SQLite lifecycle correction

Base: `a06e7e91f6a115b345ff1072095b80e15d193ad4`.

The first published Windows CI reached the HTTP revocation suite, then failed
strict temporary-directory cleanup because `metadata.sqlite` still had an open
test-side connection. The helper used the SQLite transaction context manager,
which does not close its connection on exit.

## Change and retained behavior

`tests/http_revocation.py::RevocationHTTP.rows` now wraps its existing read-only
connection in `contextlib.closing`. The same query result is returned on success;
the original query exception propagates on failure. Both exits close the
connection. The URI remains `mode=ro`, with `uri=True`.

The two existing HTTP authorization/restart cases and strict `tearDown` are
unchanged. `preservation.json` compares their ASTs with the published base and
confirms that only `rows` changed among the existing class methods. There are no
cleanup retries, sleeps, ignored errors, or production-code changes.

Two new tests in a separate `RowsConnectionLifecycle` class use a real SQLite
database and retain the returned real connection. A subsequent `execute` must
raise `sqlite3.ProgrammingError` because the connection is already closed. One
case verifies a successful result; the other verifies the original SQL exception
and the closed connection. Strong references prevent garbage collection from
masking the defect. Test cleanups also close retained connections after the assertion
so an intentionally failing baseline can still remove its fixture directory.

These regressions run through the existing `http_revocation` CTest entry on both
Linux and Windows. That Python script now executes **4 tests instead of 2**. CTest
suite counts remain **27 on Linux and 17 in the Windows selection**.

## Focused local evidence

| Record | Result |
| --- | --- |
| `baseline-lifecycle.json` / `.log` | Both new lifecycle cases fail against the original `rows` implementation: **0/2**, expected exit 1. |
| `fixed-http-revocation.json` / `.log` | Original two HTTP cases plus both lifecycle regressions: **4/4**, exit 0. |
| `fixed-http-cli.json` / `.log` | Unchanged HTTP/CLI suite: **20/20**, exit 0. |

The baseline run adds the new tests and imports but retains the original helper.
Its source SHA is recorded before applying the one-line helper correction.
Successful run records bind all test/fixture inputs and the reused server/CLI
binaries before and after execution; both report `inputs_unchanged: true`.
Rebuilding unchanged C++ code was unnecessary for this test-only correction.

The focused runs used Linux and actual loopback HTTP. They do not claim a Windows
rerun result. The parent integration will publish the reviewed fix and evaluate
its exact-SHA CI separately. The original failed CI and its business-test outcome
remain distinct from these focused regression records.

To run only the deterministic regressions from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python -m unittest http_revocation.RowsConnectionLifecycle -v
```

The two full HTTP script command lines, binary paths, interpreter versions, and
SHA-256 values are recorded in their corresponding JSON files. This correction
does not change the 83-work-package / 161-acceptance-item planning scope.
