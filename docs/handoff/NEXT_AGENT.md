# 2026-10-28 当前入口：PR #8 接续复核

当前输入为 `0c47a5f5539b47cbb5f53398ed6e41de5ba9eb69`。以[本批执行说明](../planning/OWNER_REVIEW_20261028.md)及 `validation/owner_review_20261028/result.json` 为准。此前本地 Debug 超时/缺依赖描述属于历史批次，不是当前新版本结果。

本批修复进程快照竞态、host容量/并发、异常值泄露和认证配置歧义，并增加 host health诊断、只读mcp-token check、gzip真实MCP端到端覆盖与Windows完整pytest门禁。Release全量42/42，内含Python1573通过/5平台跳过；新SHA远端CI另查。没有合并main或部署Windows11；不要把旧输入成功当新版本通过。

新增Windows完整pytest曾发现4个失败，已修复代码/旧测试并保留原日志；新提交自身CI必须重新确认，不能沿用早前输入成功。已退出的根即使句柄仍可打开，也保守拒绝自动清理其后代。

保留D1–D7、来源禁用、schema15及原签收数量。审核范围主要为最新Python/进程/Worker/认证/地图调用链；未逐行审计全部历史C++、评估/回放或独立执行全部故障场景。历史交接如下，不能作为当前发布指令：

---

# 2026-10-27 当前接续：离线维护、启动器、RPC

本批从 PR #5 的 `d29223551041f5d0f39fa4ca4c6ce936af5a89d9` 接续。输入PR的Linux/Windows运行40513707647已成功；不能把该成绩当成本批新代码CI。当前顺序见[三个模块计划](../planning/NEXT_THREE_MODULES_20261027.md)。

新增显式[旧库离线转换](../OFFLINE_STORAGE.md)、[启动器生命周期](../LAUNCHER_LIFECYCLE.md)及[Worker RPC边界](../WORKER_RPC_BOUNDARY.md)。旧库工具拒绝隔离备份和未知格式，内部预演逐表/rowid一致后才改写同一持锁原库；未验证结果保持停服。启动器旧数字PID不可自动接管。RPC认证和Origin仍先于解析。

**本地完整Debug CTest为37通过/5个进程超时，不是全绿。** 同一五个二进制独立诊断均通过，但sessions仍约90秒；原60秒门槛未改，合入仍需新SHA CI和超时核查。Python全量1497通过；仅新维护源/测试做了定向ASan/UBSan，未跑完整新sanitizer门禁。

本批验证以 `validation/next_three_modules_20261027/` 为准。准确远端SHA的CI、Windows实机行为和目标部署分开核对；本地自审不冒充外部签字。D1–D7仍待定，来源启用0、schema15、原83/161/10及37/46/0保持。下一步优先核对远端新CI和部署验证，再做允许范围内的持续运行；没有D1/D2就不跳到真实来源签收，没有D4/D5就不开放恢复或缩短身份保留。

以下保留上批交接，旧“未来转换”表示上批状态，不再是本批实现缺失：

---

# bzs861 recovery handoff — 2026-10-27

Base: main `1d8e80e5f1bd1b5f9ad8d1e5fc211c8cd403ca31`, tree
`5a423c16742a7dbfe4ef4e86c168311d157a096d`. PR #2 merged the preceding
P1-B/P1-A(A)/P2-C/P2-B(1,2) work. Do not confuse that with the unrecovered
five-node candidate previously described in chat: its old 38/38 and migration
claims are not evidence for this reconstruction.

This reconstruction is split into bounded background pools; unkeyed receipt
cleanup; archived-byte quota; incremental reclamation; and CI diagnostics/shards.
Read `validation/recovery_20261027/` summaries and the exact candidate's CI.
The local full-run report is supplied with the delivery archive, not committed
raw logs. All new summary files together are below 50 KB.

The audit also found and fixed a shutdown race: idle claimers waiting on Store
locks now recheck stop tokens inside their claim transactions. The old test
fault injector is bound to first admission, not a nondeterministic wake-up.
Original failures remain in external evidence; do not hide them by citing only
the later green run.

Background read/map/search widths default to 4/2/4 and may each be set to 1..8
with native `searchd --background-reads/--background-maps/--background-searches`.
The fixture worker stays single-threaded. Store leasing and Python Worker
connection pacing remain independent gates; a Core reservation timestamp is
not the same as the physical HTTP dispatch time. Do not assert a wall-clock
Worker interval from an approximate Core-side timing observation.

Keyed identities remain permanent. Unkeyed zero-usage local receipts are lazily
collected after payload expiry. Archive JSON is charged with snapshot payloads;
quota-blocked compaction keeps old details and explicitly reports blocking.
New empty stores use incremental auto-vacuum; public compaction reclaims at most
128 pages after logical commit. Old NONE/FULL stores are NOT converted and
report `requires_offline_conversion`. A safe explicit legacy conversion tool
and rowid/index preservation checks remain future work; no old migration result
is claimed here. Quarantined backups remain blocked.

All 63 original cache tests are preserved byte-for-byte and partitioned into
three mandatory suites; selector coverage and the actual uploaded JUnit file
are checked. Issue #3/#4 must not be closed merely because local tests pass:
inspect the new Linux/Windows CI artifacts. Never remove assertions, silently
skip slow cases or relabel a failed attempt as green.

D1–D7 remain pending. No source activation, B-24 expansion, proof-chain business
mechanism, business schema upgrade or history rewriting. Version0.1.10/schema15,
83 packages/161 acceptance IDs/10 slices and 37 partial/46 not_started/0 accepted
are unchanged. Do not claim the whole phase/product or target-device deployment.

## 2026-10-27 全代码复核追加

复核 PR #6 的完整变更后补两处防御：已处于 INCREMENTAL 的数据库在返回 `already_incremental` 前也执行 FTS 完整性核查；本机 CLI 对预期的文件/生命周期异常返回简短非零错误，不输出误导性的 Python traceback。新增 `already_incremental_still_checks_fts_integrity`（6组/133断言）及 CLI 错误输出测试。旧schema、隔离、身份保留与来源策略均未改变。

本轮本地复测：维护专项6组/133断言通过；启动器/存储 CLI 定向 pytest 14通过。当前容器缺少锁定依赖 `trafilatura` 和 `mcp`，所以该容器未运行完整 Python/Worker RPC 套件；准确提交的完整 Linux/Windows CI仍是合并门槛。测试失败/依赖缺失不记作通过。


---

# 2026-10-28 全代码审核接续（优先级高于上面的历史批次）

当前执行入口：docs/planning/CURRENT_EXECUTION_20261028.md。本交接只确认准确提交与门禁，不将局部测试等同于产品签收。

- PR #9 的审核头：8085b86a844699f0590d6dcab2ec17f8234c6b31，基于 PR #8 头 0c47a5f5539b47cbb5f53398ed6e41de5ba9eb69。
- 已确认 CI：原生 Linux/Windows run 40737010760 成功；完整 Windows Python safety run 40737010752 成功；Linux ASan/UBSan 在原生检查工作流内执行并通过。
- 本轮三个接续节点：OPS-00/SEC-01 host admission 聚合诊断；AUTH-01/OPS-00 只读 bzs mcp-token check 与并发注册表；MAP-01/API-00 gzip sitemap 端到端验收。所有均仍属于 partial 工作包。
- 全代码覆盖边界见 validation/windows11_takeover_20261028/audit-coverage.json。离线 evaluation/probe/replay 工具未端到端执行，crawl/map 长时交互未做 soak，MSan/TSan 未配置。
- D1–D7 仍待明确答复；来源启用 0，schema15，永久带键身份、B-24、隔离备份和原验收映射均不变。禁止把历史不同批次的状态计数混写成当前通过数。
