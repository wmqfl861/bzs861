# Claude audit follow-up — 2026-10-24

## Baseline

Freshly rebuilt `44253a7` equals the audited main. All supplied reproductions were rerun unchanged; P2-2 adds an offline 25-assertion observation. Original review files and branch remain read only. Command logs, source hashes, binary hashes and exit statuses are retained under `baseline/`.

| Item | Baseline | Observation |
| --- | --- | --- |
| P1-1 | 仍成立 | 10,001 次冻结搜索 E_SESSION_BACKPRESSURE，30 天后仍拒绝；历史 jobs 被全量计数。原 jobs 复现只创建未完成 queued 任务，不证明未完成任务应该压缩；正式回归将补实际终态压缩。 |
| P1-2 | 仍成立 | 每秒一次的第 65 次冻结本地搜索 E_QUOTA；MCP search_local 使用此路径，却不暴露会话以供继续分页。 |
| P1-3 | 仍成立 | 2101 文档中，早期目标只有 machine learning 词项查询召回；短语、C++、node.js、café、Москва 扫描最近 2000 后漏召回并标 partial。 |
| P2-1 | 仍成立 | 相同可缓存响应在无 Vary 时 fresh，Vary: Accept-Encoding 时 unsupported_vary。 |
| P2-2 | 仍成立 | 补充真实 Store/WebService + 离线 Worker 复现：原 URL 的两次 cache_first 和 refresh 均重新抓取，cache_only E_CACHE_STALE；25 个安全及归属对照通过。缺少逐跳缓存证明，不能直接放开现有保护。 |
| P2-3 | 仍成立 | 360Spider、Sogou web spider、Googlebot-Image/1.0 无关 UA 令整个 robots 变为 deny_all；Baiduspider 对照正常。 |
| P2-4 | 仍成立 | 声明 UTF-8 的英语正文夹 0x92 后落到 GB18030，It’s 被误解为 It抯；中文对照使用 UTF-8 replacement。 |
| P2-5 | 仍成立 | Release 默认 2000×200 KiB：单短语 search/stats 阻塞各 1450 ms，四 OR 短语各 2961 ms；每叶重复 lowercase 和 Store 总锁均仍在。 |

The initial missing-CMake failure is retained separately from the successful clean dependency restoration and builds. Neither the baseline reproduction programs nor later regression assertions are skipped.

## Required gates

Each issue is integrated in the requested order, with its own source-input manifest, full CTest, full Python pytest and all 15 native ASan/UBSan executables. `run.py` refuses changed inputs within one stage and records actual command results.

## Preserved decisions

No source enablement, S2 policy change, evidence-retention policy change, backup/restore relaxation, permanent identity deletion, or scraper removal.
