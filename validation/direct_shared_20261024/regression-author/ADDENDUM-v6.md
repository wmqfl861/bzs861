# Capacity fixture final configuration: v6

The root-run v5 Debug test succeeded with 23 groups, 3,636 assertions and zero
failures, but took 20.429 seconds (the new capacity case accounted for roughly
17 seconds). That regression checks accepted serialized size and exact content;
it is not a default-timeout performance test. Root therefore authorized the new
23rd group to configure the already-supported `timeout_ms=60000` for both readers,
with `65000` for its synthetic RPC template. Those three parameter changes are
the entire v5-to-v6 delta. No assertion, original group, production timeout rule
or CTest watchdog was changed.

Final v6 formal source SHA256:
`4e94e5e4ed979ff7a67bb5aba34c8540f0a081cf987caf64869b55253fdf163e`.
Its size is 49,250 bytes and its declared group count remains 23. The v6 record
binds the exact root v5 log, command, source input and measured duration.
Compiler syntax verification passed. The author did not execute corrected v6;
root owns the subsequent frozen Debug/ASan/final gates. Earlier v4 failure and
v5 success are evidence for their own inputs only.

The final preservation audit is in `../preservation/FINAL-v3.md`. Old v1/v2/v3
archive metadata and all v4/v5 records remain unchanged.
