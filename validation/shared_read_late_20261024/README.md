# 持久批读迟到订阅接续

**本批本地验证已完成：27/27 CTest、15/15 ASan/UBSan 通过，真实旧库迁移与独立反例复核通过。远端未发布。** 机器汇总见 [final-report.json](final-report.json)；本目录日期采用客户端 2026-10-24，命令记录中的执行时间明确使用 UTC。

本批从本地提交 `a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89` 接续，tree `e15b716766e9a2a787660ffd304df2102e4b994c`，开发分支为 `work/b24-late-subscribers-20261024`。这个基线已经包含上一批 URI 删除修正，但尚未推送。2026-10-25 02:30:18 UTC 的[只读核对](final/remote-main.json)确认远端 main 仍为 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`，tree `5a9977f4ec6d5aa6efa276ec1197e58b2777ff06`。历史 CI 40096268557 仅证明 ef45490，不证明未发布的 schema14/schema15 输入。

此前自动审批拒绝了 `git push origin HEAD:main`，理由是“继续开发”的指令没有明确授权更新默认分支。本批完成已授权的本地开发，没有重试或间接绕过该远端写入限制，也没有创建 tag、变更包版本或声称有新 CI 结果。版本仍为 0.1.10，数据库 schema 为 15。

## 实现范围

本增量推进 CACHE-01 / B-24 / FT-032 中的持久批读迟到订阅，完整语义见 [SHARED_READS.md](../../docs/SHARED_READS.md)。生产修改仅在 `include/bzs/store.hpp`、`src/store.cpp` 和 `src/batch.cpp`。

- 新 `read_many.submit` 任务的输入位置 0，可以在创建事务中加入同 owner、精确 URL、权限和删除历史一致、尚未结算的读取尝试。当前快照及**全局**页面发布序号必须与原捕获一致，剩余额度、有效超时和任务期限必须覆盖原派发条件。
- 原单付款者可以按需形成共享组；成员继承原 attempt、捕获和租约，不新增 Worker 请求、不扩大物理预算、不延长截止时间。累计最多 32 个成员，取消或暂停不释放历史槽位，幂等重提不重复加入。
- 跨 Store 的成员/存活判断与迟到加入通过 SQLite 写事务串行。最后成员离开、决定向 Worker 发送取消之前，先持久关闭加入；关闭不会因回拨重新开放，旧尝试也不能关闭后续重试。关闭不提前结算，原完成、失败和实际/未知用量路径保持。
- schema14→15 在事务中增加派发超时上限及加入关闭状态。旧运行项默认 0 / 1，缺少新加入所需的历史证明，因此只完成原租约与结算；新派发才捕获超时并开放加入。**升级前必须停止旧 Core**，不支持新旧程序同时使用升级库。

部分索引排除零预算订阅项和已完成历史。每次最多评估查询返回的 32 个匹配付款项，这不是 SQL 最多扫描 32 行的保证。持久有效租约也不是原 Core/Worker 进程仍活着的证明；丢失执行仍按原租约到期记未知，不自动重发。

## 实际执行结果

| 范围 | 结果 | 原始证据 |
| --- | --- | --- |
| 真实 a3/schema14 基线上的新批读回归 | 44 组、1,150 条断言；11 个新能力预期失败，原 30 组全部通过 | [基线 v2](regression/baseline-v2-result.json)、[完整日志](regression/baseline-v2-run.log) |
| 候选首次聚焦批读回归 | 44 组、1,456 条断言，0 失败 | [候选记录](regression/candidate-v1-result.json)、[完整日志](regression/candidate-v1-run.log) |
| 完整 CTest | 27/27，通过，无跳过或禁用；记录总用时 187.422 秒 | [命令及输入绑定](final/ctest-command.json)、[完整输出](final/ctest-detail.log)、[JUnit](final/ctest.xml) |
| CTest 中的原生组成 | 15 个目标、521 组、16,761 条断言 | [逐目标汇总](final-report.json) |
| CTest 中的 Python 组成 | 1,281 项通过 | [完整输出](final/ctest-detail.log) |
| ASan/UBSan | 15/15 目标，通过；同一批 521 组 / 16,761 条断言；保持 `detect_leaks=1` | [逐目标命令、日志与输入](asan/)、[机器汇总](final-report.json) |
| 原设计契约 | 34/34 | [输出](final/contracts.log)、[执行记录](final/contracts-command.json) |
| 计划与防护 | 83 包 / 161 映射 / 10 切片结构有效，13/13 防护检查 | [计划](final/plan.log)、[防护](final/plan-guard.log) |
| Python 依赖 | `pip check` 通过 | [记录](final/pip-check-command.json) |

原生和 Python 结果是 27 项 CTest 的组成部分；聚焦批读属于原生套件，sanitizer 是相同场景的重放。以上数字不相加，也不等于产品验收数量。JUnit 的单项 stdout 会截断，因此逐目标计数使用未截断的原始 `LastTest.log` 副本。

新增 14 组批读回归覆盖 Worker 回调内加入、单人变共享、已有组扩员、付款者/订阅者取消与暂停、取消 RPC 窗口、时钟回拨、精确匹配和预算/期限负例、权限/删除/快照/发布变化、32 个累计成员、跨 Store、重启未知结算、显式重试、失败一次计费、完成回执事务回滚及已结算尝试拒绝加入。原 30 组断言未被放宽；旧测试只作必需的 schema 数字更新。其余 7 个测试文件合计 13 处当前版本 14→15、2 处未来版本 15→16，batch 另有 1 处当前版本更新，见[测试保留记录](regression/test-preservation.json)与[文档/版本断言审查](documentation/shared-read-docs-review.json)。

## 独立审核与真实旧库升级

审核者只在独立目录编写探针、冻结库和审核记录，没有修改生产或正式测试。独立汇总见 [independent-review.json](independent/independent-review.json)，详细方法与限制见[独立说明](independent/README.md)。

| 独立检查 | 结果 |
| --- | --- |
| 真实旧 API 建库后 schema14→15 | 47 张旧表的原列/行内容保持；两个 FTS 虚拟接口由其影子数据覆盖，Core 连接完整性检查通过 |
| 旧运行任务兼容 | 单人、共享、付款者已取消而其他成员有效的旧尝试默认关闭迟到加入，仍可完成并归属原付款者；旧 pending 项取得新 attempt 后可以开放加入 |
| 升级边界 | 旧二进制拒绝 schema15，隔离备份仍被拒绝；注入迁移失败完整回滚，移除故障后可正常升级 |
| 独立行为探针 | 6 组；真实旧基线 131 检查 / 4 个预期能力失败，候选 163 检查 / 0 失败；其中一组包含 8 个不兼容对照 |
| 入组事务故障 | 单人惰性建组和已有组扩员两个分支、151 检查通过；成员 INSERT 失败后六张相关表及调度序号完全回滚，同幂等键重试成功，原请求只计费一次 |
| 独立 sanitizer | 上述迁移、6 组行为及 2 个回滚分支均使用同一冻结输入重放通过，保持泄漏检测；不增加唯一场景数 |

完整 CTest 与四项辅助门禁另经[只读核对](independent/integration-ctest-review-v1/ctest-and-auxiliary-review.json)。最终报告、sanitizer、归档和当前输入的收尾审核记录为 [final-local-review.json](independent/final-local-review.json)；初始独立汇总中的“整合审核待完成”保留为其生成时的真实状态，以后续收尾记录补足。

## 原失败与修正记录

所有首次失败和原始文件保留，成功记录使用新文件名，不覆写旧输出。

1. **环境恢复失败：**最初两次核心构建没有找到 `cmake`，没有启动编译；正式回归最初两次编译缺少 `sqlite3.h`，没有运行测试。恢复 Ubuntu 24.04 既定开发依赖后构建通过，项目锁文件没有变化。另恢复两个生成 ELF 的执行位，文件字节保持。记录见 [author](author/)、[环境](environment/)、[回归交付清单](regression/delivery-manifest.json)及[执行位核对](final/executable-preparation.json)。这些不计为产品失败或通过。
2. **真实旧能力反例：**正式基线 v1/v2 的 11 个失败及独立基线的 4 个失败均来自真实旧库缺少迟到订阅能力。正式 v2 只加强新 deadline 负例，使超时相同而期限仍不足；旧断言不变，v1 保留。候选首次正式运行通过后没有再修改测试。
3. **独立迁移诊断：**首次外部 Python SQLite `integrity_check` 对原封不动的 schema14 和升级后的 schema15 均出现 SQL logic error。改用 Core 已注册的连接后前后均通过。原失败、原库对照及 v2 诊断分别保留，没有据此修改生产代码。
4. **独立发布负例夹具：**首版策略缺少必需的 `version`，没有推进真实发布序号，导致候选探针一项失败。v2 使用有效策略，并先断言发布水位已变化，再检查拒绝加入；生产实现未改。首版失败仍在 `behavior-review-v1`。
5. **审核汇总脚本：**首版清点脚本把 `TEST` 宏定义计入测试组数。修正为只统计实际调用后保留首版失败；已执行探针、正式测试及其结果没有变化。

迁移 UPDATE、入组成员 INSERT、完成时项状态 UPDATE 的注入失败是有明确预期的负例；检查的是其回滚与结算行为，并非省略未通过的生产门禁。

## 输入、二进制与历史保留

每次完整运行都记录真实命令、环境、退出码、完整输出和 SHA256。CTest、四项辅助门禁及 15 项 sanitizer 的 **224 项输入清单相同，36 项二进制/CTest 配置在运行前后相同**。真实基线静态库 SHA256 `30ea0955517d8a2a585f2b01c4ba42fd1530ca4a9431284f74fa04caf2d80338` 与上一批 URI 最终 CTest 记录中的构建产物完全一致；使用真实 a3 头文件，没有用当前实现伪造旧行为。

运行结束后只更新了 `docs/implementation_status.json` 的历史 CI 范围/本地完成状态，以及 `docs/handoff/NEXT_AGENT.md` 的交接顺序。两项非运行文档的前后哈希见[文档变化](final/documentation-after-tests.json)，当前精确输入和二进制见[结束快照](final/post-run-inputs.json)。生产、正式测试、构建及配置与已测试输入一致。

`run.py` 不直接快照旧 validation，因此 CTest 使用的旧 `probe_http_policy.py` 由另行冻结的[运行前清单](old-validation-before.json)、真实 a3 Git blob 和[运行后保留检查](final/preservation.json)补充绑定；没有声称它来自 runner 的直接快照。旧 **3,193 项 validation** 与原 Git 内容及运行前 SHA256 全部一致。

作者、回归和独立记录保持角色分开。复制清单保留原始作者清单并记录逐文件字节哈希；只归档源码、头文件、文本日志和 JSON，不提交合成数据库、原生可执行文件或静态库。清单中保留的 scratch 二进制路径说明实际执行来源，不表示这些二进制已经入库。后续审核用独立追加清单归档。

暂存差异的空白检查提示来自原始 `environment/apt-install.log` 的终端 CR 字节，以及保留这些字节的首次检查输出 `final/diff-check-raw.log`。两份日志均保持原始字节；逐项排除它们后，代码、文档和其余证据的差异检查通过。首次记录见 [diff-check.json](final/diff-check.json)，完整归档后的检查与精确例外见 [diff-check-final.json](final/diff-check-final.json)。

## 未扩大的边界

17 条来源 / 0 启用 / 0 站点，83 包 / 161 验收 / 10 切片及 **37 partial / 46 not_started / 0 accepted** 保持。原 features JSON 字节、配置、黄金样本、计划、工作流、构建配置与锁文件保持；只读审计分支仍为 `30f058557e3f7ebb873e9780759323b49cfe9636`。来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份隔离、永久幂等身份和抓取适配器六项既定决定保持。

普通 `web.read`、map/crawl、搜索与不同 owner 不进入本次迟到加入通道。没有真实来源/站点访问、付费或模型调用。完整 B-24、普通直接读取与其他通道的共享协调、独立撤销 journal/安全恢复、目标环境来源审核、人工 qrels 与长期故障验收仍未完成。局部模拟 Worker、双 Store 及可控时钟证据，不替代真实公网、任意断电或多节点运行验证。
