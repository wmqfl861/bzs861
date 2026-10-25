# bzs861 · 当前开发入口

**更新：2026-10-24｜当前增量：普通直接读取的在途合并与用量回执｜0.1.10 / schema15。**

本批从已发布 main `346048b4ae329e750d15f1e35952aab4513e3912`、tree `458a6f321e94defd74616ba6ebbf2983252cb495` 接续。URI 删除修正已发布为 `21797f07`，迟到批读已发布为 `346048b4`；它们分别与本地 `a3b3f6e2`、`fca5c1e9` 的完整 tree 相同。[基线 CI 40157161849](https://github.com/wmqfl861/bzs861/actions/runs/40157161849) 的 Linux / Windows 已成功，只证明这一基线。用户后续明确允许推送 main，下面旧段落的“未发布 / 审批阻塞”属于发布前历史，不能继续当作当前阻塞。

本轮按 v1.2 蓝图推进 **CACHE-01 / FT-032 / B-24**。同一 `WebService` 中，兼容的普通 `web.read` 共享一次 Worker 获取和一次正文提交；每个调用保持自己的窗口、缓存解释与返回前权限投影。网络成功响应及由直接读取路径包装的失败保留执行回执；付款方与订阅方的新增用量分别表示，未知保持未知。缓存命中只报告已知零增量；前置校验及外层未携带详情的错误不制造共享回执。详见[直接读取合同](docs/DIRECT_SHARED_READS.md)、[本批验证记录](validation/direct_shared_20261024/README.md)和[接手顺序](docs/handoff/NEXT_AGENT.md)。

本批新增验证与原失败按实际输入分别记录，最终整合结果以本批报告为准；不把基线 CI、作者聚焦或测试数量当作完整产品验收。版本和数据库格式不变；跨实例 / direct 与 durable 混合 / map-crawl-search 共享、直接请求公开取消句柄、独立撤销 journal 与恢复连续性仍待推进。

六项保留决定、17 条来源 / 0 启用 / 0 站点、83 包 / 161 验收 / 10 切片、37 partial / 46 not_started / 0 accepted 均保持；审计分支 `review/claude-2026-10-24` / `30f058557e3f7ebb873e9780759323b49cfe9636` 只读。

## 上一批开发入口（发布前历史）

以下内容保留其当时状态；当前发布与继续开发依据以上文为准。

**更新：2026-10-24｜当前开发：持久批读迟到订阅｜版本 0.1.10 / schema15｜本地候选，尚未发布。**

本批从上轮已本地验证、未推送的 `a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89` 接续，tree `e15b716766e9a2a787660ffd304df2102e4b994c`。实际远端 main 仍为 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`；其历史 CI 只证明该旧输入。默认分支发布的自动审批阻塞维持，本轮不重试或绕过远端写入，不预写新提交、tag 或 CI 成功。

- 新 `read_many.submit` 任务首项可以加入原未结算尝试；保留同 owner、精确 URL、权限/删除历史、预算、期限和有效超时约束，当前快照和当前全局页面发布序号（`publication_sequence`）必须与原捕获一致。
- 支持原单人尝试按需形成共享组，固定原付款任务和原租约，累计最多 32 个成员，取消不释放槽位；不抢其他既存 pending/retry 项。
- 跨 Store 写事务先持久关闭加入，再发最后成员的协作取消；关闭不会因回拨复活，不提前结算，也不剥夺已有成员原本有效的收据。
- schema14 → 15 新增派发超时上限和加入关闭字段；旧运行项默认 0 / 1，按原结算路径完成但不接迟到。升级前停止旧 Core，旧程序打开升级库返回 `E_SCHEMA_VERSION`。

先读[共享读取合同](docs/SHARED_READS.md)、[本批验证记录](validation/shared_read_late_20261024/README.md)和[下一会话顺序](docs/handoff/NEXT_AGENT.md)。本轮基线反例、作者回归、独立审核、真实旧库升级与整合执行分别记录，不把旧通过或套件存在当作当前验证成功。持久租约不证明原进程仍活着，丢失执行仍到期记未知且不自动重发；普通直接读取、crawl/search 与不同 owner 不纳入本次共享。

来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份隔离、永久幂等身份保留和抓取适配器六项决定保持。17 条来源 / 0 启用 / 0 站点，83 包 / 161 验收 / 10 切片与 37 partial / 46 not_started / 0 accepted 均保持。审计分支 `review/claude-2026-10-24` / `30f058557e3f7ebb873e9780759323b49cfe9636` 继续只读。

## 上一批 URI 删除屏障（ef45490 → a3b3f6e2，本地未推送历史）

以下“当前/本批”只指 `uri_deletion_20261024`。上轮本地提交已经验证，但没有发布到远端 main；后续状态、schema15 与远端写入限制以上文为准。

**更新：2026-10-24｜当前开发：URI 别名删除屏障与 MCP 来源说明｜版本 0.1.10 / schema14。**

本批接手真实 main `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`，tree `5a9977f4ec6d5aa6efa276ec1197e58b2777ff06`；其 [CI 40096268557](https://github.com/wmqfl861/bzs861/actions/runs/40096268557) 已核对双平台成功。旧 P1/P2 修复及 P2-5/6/7/9 接续已在基线，不重复应用旧补丁。本批实测、原失败、真实旧库升级及独立审核见[验证记录](validation/uri_deletion_20261024/README.md)。

- 新增独立删除键，按 owner 和有限 URI 等价查询历史别名与墓碑；直达、跳转缓存、验证器与提交检查保持同一规则。
- schema13 → 14 在事务内回填旧记录；先停止旧 Core。旧程序重新打开升级库会被版本门禁拒绝。
- 原始 URI、文档/快照 ID、历史证据、发布键和幂等身份保留；reserved 编码、不同 query 与 owner 不合并。
- MCP 说明准确反映当前部署的已审核启用来源；默认来源配置仍为空启用列表。

实现边界见 [URI 删除身份](docs/URI_DELETION_IDENTITY.md)；下一步见[交接顺序](docs/handoff/NEXT_AGENT.md)。来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份隔离、永久幂等身份保留和抓取适配器六项决定保持。17 条来源 / 0 启用 / 0 站点，83 包 / 161 验收 / 10 切片与 37 partial / 46 not_started / 0 accepted 均保持。审计分支 `review/claude-2026-10-24` / `30f058557e3f7ebb873e9780759323b49cfe9636` 继续只读。

## 上一批并发与来源退避接续（59081e1 → ef45490，历史）

以下“本批/当前”只指 `claude_continuation_20261024` 的旧输入。

**更新：2026-10-24｜当前开发：Claude 审计接续 P2-5 / P2-6 / P2-7 / P2-9｜版本仍为 0.1.10｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本批基线是已发布 main `59081e14fdda765844031e13f874e588334f1a36`，tree `5d555fabc43180aa905e0581a301a3bafbeca1d5`；该基线的 [CI 40067206826](https://github.com/wmqfl861/bzs861/actions/runs/40067206826) 已核对 Linux / Windows 成功。这个成绩只证明上一批输入。本批作者测试、独立审核、原始失败、最终整合输入及实际执行结果见[本批验证记录](validation/claude_continuation_20261024/README.md)；发布后的实际 commit/tree 与对应 Checks 必须另行核对，不预写未来 CI 成功。

| 本批范围 | 实现边界与入口 |
| --- | --- |
| P2-5 本地搜索锁 | 精确匹配、共享 ASCII 折叠、评分和排序移出 Store 锁；SQL、逐篇正文复制及最终发布投影仍有必要的锁。见[搜索并发](docs/LOCAL_SEARCH_CONCURRENCY.md)。 |
| P2-6 后台调度 | fixture、read_many、map/crawl、持久搜索分为四条固定通道；类别内轮转、共享读取租约、预算与撤权栅栏保留。见[后台调度](docs/BACKGROUND_DISPATCH.md)。 |
| P2-7 HTTP 执行 | 有界业务线程池与 IO 分离，满额在进入 Service 前返回 503；健康检查走 IO，保留连接和 socket 期限。见[HTTP 执行](docs/HTTP_EXECUTION.md)。 |
| P2-9 来源退避 | 新限流等待有上限，保留合法已有退避；首版时钟回拨误缩期限的独立反例及后续修正分开记录。最终策略见[来源退避](docs/SOURCE_BACKOFF.md)。 |

本批沿用 schema13 和 `bzs-ascii-cjk/2+codepoint-grams/1`，不新增版本 tag。来源选审启用、S2 范围/状态及是否冻结、validation 入库政策、备份恢复隔离、永久幂等身份保留和抓取适配器六项决定保持。17 条来源全部禁用，站点登记为 0；原 83 包 / 161 验收 / 10 切片及 **37 partial / 46 not_started / 0 accepted** 不变。先读[下一会话顺序](docs/handoff/NEXT_AGENT.md)与[机器状态](docs/implementation_status.json)。

## 上一批 Claude 审计修正（44253a7 → 59081e1，历史）

以下保留上一批原交接说明；其中“本批 / 当前”指 `validation/claude_followup_20261024` 对应输入。再下一节的 0.1.10 五项增量及 CI 修正属于更早历史，均不代替本批 continuation 的验证。

**更新：2026-10-24｜当前开发：Claude 审计后续修正｜已发布版本仍为 0.1.10｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本批从 `44253a752f945fdfab9810192e583b39fe5f6d83` 接续。审计分支 `review/claude-2026-10-24` 的 `30f058557e3f7ebb873e9780759323b49cfe9636` 仅作只读材料，没有合并；八项原问题的实测与语义限定见[基线结论](validation/claude_followup_20261024/baseline-findings.json)。按 P1-2、P1-1、P1-3、P2-1 至 P2-4 的顺序修复，P2-5 的原查询补丁另经评审。逐项回归、独立反例、失败记录和准确执行输入见[本批验证记录](validation/claude_followup_20261024/)。

当前代码涉及无持久会话的 `search.once`、永久身份与活动明细容量分离、词项与子串候选索引、仅 Accept-Encoding 的 Vary 支持、显式读取的逐跳缓存证明及 robots/字符集/查询匹配修正。接口与边界见 [API](docs/API.md)、[任务保留](docs/JOB_RETENTION.md)、[HTTP 跳转缓存](docs/HTTP_REDIRECT_CACHE.md)；各项实现、实际执行结果、准确提交与 CI 见本批验证记录。

来源选审启用、S2 范围、状态及是否冻结的决定、validation 入库政策、backup 隔离、永久幂等身份保留与抓取适配器六项决定保持。17 条来源仍全部禁用，站点登记为空；S0/S1/S2 均未完整签收。

## 0.1.10 历史基线与验证

以下记录上一轮五项增量及其后续修正，不代替本批新输入的验证。

本轮从已发布0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`、schema12接续。基线的Linux/Windows [CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547) 已核对成功；最终输入须另核对自己的精确提交CI。schema13组合来源发布、主体撤权及共享请求迁移，保留0.1.9的JSON并发修复与条件重验证。

本轮生产实现的 `final-v2` 本地门禁为27/27套CTest（Python 1,049项）、15/15套ASan/UBSan，均无跳过；真实schema12→13升级与原合同/计划/历史保持检查通过。首次发布 `a06e7e91f6a115b345ff1072095b80e15d193ad4` 的 [CI 39945994760](https://github.com/wmqfl861/bzs861/actions/runs/39945994760) 为Linux成功、Windows构建成功但测试16/17通过；唯一失败是撤权HTTP测试未显式关闭SQLite连接，导致Windows临时目录清理失败。

随后以 `199eb635ca90c657dff673fbf88bed44e7c4b40b` 发布测试连接释放修正及两条资源回归，该次生产代码不变，证据见[HTTP清理后续报告](validation/s2_takeover/ci-followup-report.json)。其 [CI 39950116698](https://github.com/wmqfl861/bzs861/actions/runs/39950116698) 的Windows17/17通过，Linux为26/27：原一秒恢复测试出现额外source_unknown事件。整体期限触发的RPC超时分类及原子终态结算随后修正，完整验证见[截止时间后续报告](validation/s2_takeover/deadline-followup-report.json)，原E2E保持。

该生产修正发布为 `fcb58e4679a5bbdff17319b7b4ebff7019733eab`，其 [CI 39954357148](https://github.com/wmqfl861/bzs861/actions/runs/39954357148) 的Linux27/27及15套sanitizer通过；Windows16/17，唯一失败为crawl整个进程45.02秒超时，无法从旧缓冲输出确认完成到了哪个用例。该次仅为Windows设置core90秒、crawl和site_publication120秒的有限watchdog，并增加crawl逐例即时RUN/PASS与耗时。54个原用例、断言、生产源码和二进制保持，Linux属性不变；[该次报告](validation/s2_takeover/windows-watchdog-report.json)分别记录本轮聚焦验证、未变生产库的历史全量成绩及原失败。后续提交仍须取得自己的实际双平台CI，不据调整后的进程上限宣称产品性能验收完成。

| 入口 | 内容 |
| --- | --- |
| [上一轮五项范围](docs/handoff/S2_TAKEOVER_20261023.md) | 接手来源、原工作包与验收映射、实施边界 |
| [当前进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json) | 实际实现、未完成部分和本轮验证状态 |
| [历史 Windows watchdog 报告](validation/s2_takeover/windows-watchdog-report.json)、[截止时间报告](validation/s2_takeover/deadline-followup-report.json) | 该次测试设施修正与前次生产全量验证分开记录；原失败、命令和独立复核保留 |
| [来源发布](docs/SOURCE_PUBLICATION.md)、[主体撤权](docs/PRINCIPAL_REVOCATION.md) | 持久政策收紧、管理员入口与恢复边界 |
| [HTTP新鲜度](docs/HTTP_FRESHNESS.md)、[共享读取](docs/SHARED_READS.md)、[搜索预设](docs/SEARCH_MODES.md) | 缓存与取消语义、预算和公开接口 |
| [蓝图](docs/planning/development_blueprint_v1.2.md)、[切片计划](docs/planning/slices_v1.2.json) | 原83包/161验收/10切片，依赖与验收语义保留 |
| [接手顺序](docs/handoff/NEXT_AGENT.md)、[GitHub历史](docs/GITHUB_HANDOFF.md) | 发布流程、真实远端SHA与精确CI核对 |
| [0.1.9历史报告](validation/s2_next_five/json-race-report.json) | 上一轮与JSON修复输入，原字节保留 |

当前为 **37包部分实现、46包未开始、0包完整签收**；只有MODE-01由未开始进入部分实现。默认17条搜索来源禁用，站点登记为空。合成HTTP与状态机回归不证明生产可用性、人工相关性或长期性能。独立撤销连续性没有证明时，恢复副本继续隔离。

各期报告分别绑定真实源码、二进制、执行日志和提交；本批准确 SHA 与 Linux/Windows CI 见本批验证记录，历史成绩只证明对应历史输入。
