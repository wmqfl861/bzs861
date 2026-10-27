# P1-B — bounded HTTP admission

Ordinary execution has a bounded queue; local calls have two reserved threads.
The strict 1-slot direct cancellation lane remains unqueued and independent.
Queued expiry removes work before Service, with retry_after_ms in503 responses.
CoreClient repeats identical bytes only for explicit admission backpressure,
at most3 retries within a10-second start window; unknown outcomes never retry.

Baseline held reads caused16/16 stats refusals. Final focused CTest3/3 and
Python1400 passed;14 new client cases,4 real HTTP/MCP scenarios and the
executor lifecycle test are included. Initial p95 failure was not erased.
See result.json for actual commands/input hashes and limitations.
Raw CI output is retained by linux-tests/windows-tests artifacts.
No complete work package is signed off. D1-D7 remain pending.
