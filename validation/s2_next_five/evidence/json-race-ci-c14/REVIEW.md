# Independent Windows CI verification: c14b3c3

The actual Windows job [119635050132](https://github.com/wmqfl861/bzs861/actions/runs/39721279784/job/119635050132) completed successfully. The raw checkout `git log -1 --format=%H` output is exactly `c14b3c3c214bf9def1f8080b9125816aa0f56735`.

- Dependency installation and Build succeeded.
- All 12 selected CTest suites passed; 0 failed, 0 timed out and 0 skipped/not run. Total real time: 306.74 seconds.
- `sessions`: 21 RUN and 21 PASS lines match the exact commit's registered test names and order; 283 assertions, 0 failures; CTest elapsed 68.56 seconds.
- The complete 4 MiB storage/backpressure fixture passed in 60000 ms. The actual CTest verbose log shows a 180-second sessions timeout.

`review.json` contains each suite's elapsed time, every sessions subcase, checkout proof, step status, scope and hashes. `job-119635050132.log` is the complete connector-decoded log saved as UTF-8 bytes with original CRLF preserved. Copy it as bytes; do not normalize its line endings. `job-log-response.json` retains the full connector response. `archive-text-files.json` lists byte hashes for the copyable evidence.

## Scope

This confirms only the Windows job on c14b3c3. The same workflow run's Linux job failed. The later JSON correction at 79df581 was not included in this job and needs its own final CI. The original b88b763 Windows timeout remains preserved separately; this passing run does not retrospectively establish the sole cause of that original timeout.

No workflow was rerun or cancelled by this reviewer, and no main repository files were modified.
