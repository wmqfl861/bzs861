# 下一会话接手顺序

## 当前接续：普通直接读取的显式成员取消

更新：2026-10-25。本批从本地 `f4878be865a92dd640a14d471f0fb9725e7b0796` 接手；开始时远端仍为 `346048b4ae329e750d15f1e35952aab4513e3912`。本次发布需包含两批代码与原验证材料，以实际远端 tree、完整 SHA 和该输入的 CI 为准。用户允许推送 main 的授权保持。

1. 先读[当前入口](../../HANDOFF_START_HERE.md)、[取消合同](../DIRECT_READ_CANCELLATION.md)、[机器状态](../implementation_status.json)和[本批记录](../../validation/direct_cancel_20261025/README.md)。不要重做已存在的URI、迟到批读或普通共享读取。
2. 核对显式句柄一次性/owner隔离、1024容量、steady_clock期限和活动句柄不驱逐；预取消不出站，重复读取不能变成新派发。显式无效MCP句柄由Core拒绝，不静默降级。
3. 核对payer固定归属、subscriber早退未知物理收据、最后成员退出先闭加入后一次协作取消；保留32累计成员和64开放组。Worker独立control_id不复用durable grant；普通60秒、截断和重验证合同保持。
4. 取消成功阻断该成员交付；全部取消在提交准入前已生效则不新提交正文，但保留有效限制观察。准入检查与Store提交之间的竞态不承诺回滚。已结算成功再取消不影响新尝试。
5. 核对原失败、作者聚焦、独立C++/真实回环Worker探针、完整CTest和sanitizer的准确输入；不把旧通过或套件数量当作B-24整包签收。发布后核对新SHA自己的Linux/Windows结果。
6. 下一步按蓝图设计跨直接/持久/发现/爬取/搜索的共享兼容边界，尤其预算、超时、截断、重定向正文和付款身份；可同时推进HTTP控制操作的保留接纳能力。独立撤销journal/安全恢复、真实来源审批、人审qrels和长期故障验收继续按原要求取证。

版本仍0.1.10/schema15；六项保留决定和来源/站点及规划数量不变。唯一发布目标 `wmqfl861/bzs861`，以准确远端父SHA非强推；审计分支保持只读。

## 以下为上一会话交接历史

## 当前接续：普通直接读取合并与同步用量回执

更新：2026-10-24。基线为已发布 `346048b4ae329e750d15f1e35952aab4513e3912` / tree `458a6f321e94defd74616ba6ebbf2983252cb495`，其 [CI 40157161849](https://github.com/wmqfl861/bzs861/actions/runs/40157161849) 已双平台成功。此前用户已明确允许推送 main；下文发布前的自动审批阻塞已解决。当前准确包含提交及其 CI 须按实际 Git / Actions 核对，不预写未来结果。

1. 先读[当前入口](../../HANDOFF_START_HERE.md)、[直接共享合同](../DIRECT_SHARED_READS.md)、[机器状态](../implementation_status.json)和[本批验证记录](../../validation/direct_shared_20261024/README.md)。原 schema14/15 补丁已在基线，不重复实现。原 83 包 / 161 验收的定义不变；FT-019 属于 MODE-01，本批映射是 FT-032 / B-24。
2. 核对共享只覆盖同一 WebService 的兼容普通 direct 读取；完整 fence、精确 URI、请求超时、缓存元数据 / 条件身份和已证明 profile 参与匹配。最多 32 累计成员、64 个开放组，同线程重入和超限独立执行。Worker RPC 返回立即关闭加入，完成结果不成为额外缓存。
3. 保留每成员独立窗口、Store 返回事务和完整元数据绑定。no-cache 的新响应可返回；删除、撤权、页面/站点收紧不能因内存里已有正文而绕过。304 只提交一次，同 snapshot 后续 metadata 变化不得与旧物理回执混用。
4. 核对成功和错误的 usage/shared_read 在 Core、HTTP、CoreClient、MCP 透传；payer 承担一次物理增量，subscriber 新增为零但保留关联用量，缺回执明确 unknown。直接读取没有持久预算账本，不提供重启后用量查询或网络恰好一次。
5. 本批保留初次核心 Json 错误构造死锁、独立 malformed usage/限制观察反例、夹具 oracle 修正与后续闭合。按[最终记录](../../validation/direct_shared_20261024/final-report.json)核对源码/二进制/日志和独立复核；不得覆盖原失败或把独立探针与正式套件相加为验收数量。
6. 下一步按蓝图设计直接读取的显式成员取消、共享物理尝试与 durable/map/crawl/search 的兼容条件及更广取消矩阵。直接接口和持久批读的 timeout、预算、截断、重定向响应体、付款身份均不同，不能直接塞入 batch attempt_id 改变合同。完整 B-24/CACHE-01、独立撤销 journal/安全恢复、真实来源、人审 qrels 与长期故障验收仍未签收。

版本仍 0.1.10，schema15；默认来源/站点和六项保留决定不变。唯一发布目标 `wmqfl861/bzs861`，以准确远端父 SHA 非强推，发布后核对完整 tree 和该 SHA 的双平台 CI；审计分支保持只读。

## 以下为上轮接手记录（发布前历史）

下文“本轮 / 当前 / 未发布 / 审批阻塞”均限于其对应历史记录，当前状态以上文为准。

## 当前接续：schema15 持久批读迟到订阅

更新：2026-10-24。本地基线 `a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89` / tree `e15b716766e9a2a787660ffd304df2102e4b994c` 是上轮已验证、未推送的 URI 修正，不是远端 main。实际远端 main 仍为 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`。本轮版本仍为 0.1.10，当前业务格式升为15；默认分支发布的自动审批阻塞维持，不重试或绕过远端写入，不声明新 CI。

1. 先核对[当前入口](../../HANDOFF_START_HERE.md)、[共享读取合同](../SHARED_READS.md)、[机器状态](../implementation_status.json)和[本批验证记录](../../validation/shared_read_late_20261024/README.md)，区分基线反例、作者聚焦、独立审核/迁移与最终整合输入。只报告已完成执行，不把旧 CI、测试套件存在或静态检查当作候选运行成功。
2. 核查迟到加入仅在新 `read_many.submit` 创建事务处理首项；同 owner、权限/删除历史、精确 URL、预算、原 lease 和有效超时必须兼容，当前快照与当前全局页面发布序号（`publication_sequence`）必须严格等于原捕获。`dispatch_timeout_ms` 是派发时捕获的上限，实际开跑可按 deadline 收紧。部分索引只含 running/open/正预留付款项，最多评估 32 个返回候选，不保证 SQL 只扫描 32 行。
3. 保留单人升组、原付款任务、原租约与累计 32 人上限；原付款者取消而仍有有效成员时可继续加入，取消成员不释放槽位。不得把新提交的迟到加入扩成抓取其他既存 pending/retry 项、普通 `web.read` 或 crawl/search。
4. 核查跨 Store 的 `BEGIN IMMEDIATE` 关闭事务与最后成员取消：先持久关闭加入，再发协作取消；回拨不重新开放，旧尝试不能关闭新重试。关闭只禁止加入，不剥夺已有成员原本有效的收据、不提前结算预留。保持提交时逐成员权限、删除和发布复核。
5. 升级前停止旧 Core。真实 schema14 → 15 副本升级须保留旧业务列、成员与账本；旧运行项默认派发超时 0 / 关闭 1，只完成原结算，不接迟到。旧二进制拒绝、失败回滚与备份隔离分别核对。持久未过期 lease 不证明原进程活着，丢失执行仍等待原租约到期记未知，不自动重发。
6. 本批本地门禁已关闭，先核对[最终记录](../../validation/shared_read_late_20261024/final-report.json)及包含提交，再按原蓝图推进普通直接读取、crawl/search 的共享协调及更广取消矩阵。完整 B-24、独立撤销 journal/安全恢复、目标环境来源实测、人审 qrels 和长期故障验收仍未完成；不重复上轮 URI 补丁，也不改写旧失败和验证材料。

来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份隔离、永久幂等身份保留和抓取适配器六项决定保持。17 条来源 / 0 启用 / 0 站点，83 包 / 161 验收 / 10 切片与 37 partial / 46 not_started / 0 accepted 均保持。唯一目标是 `wmqfl861/bzs861`，审计分支只读；下文历史发布步骤不覆盖本轮远端写入阻塞。

## 上一批 URI 接手顺序（ef45490 → a3b3f6e2，本地未推送历史）

以下“本批/当前/本轮”只指 `uri_deletion_20261024`。该批已在本地基线完成，迟到订阅、当前 schema15 和验证范围以上文为准；更早段落按各自标题限定为历史。

更新：2026-10-24。本批接手基线 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`，版本 0.1.10，数据库格式升为14。

1. 先核对 [当前入口](../../HANDOFF_START_HERE.md)、[删除身份合同](../URI_DELETION_IDENTITY.md)、[机器状态](../implementation_status.json)与[本批验证](../../validation/uri_deletion_20261024/README.md)，再核对实际包含提交及其 CI。不要将基线 CI 或聚焦验证当作最终整合结果。
2. 检查 `documents.deletion_key` 历史回填、保留墓碑、原文档/证据身份、跨句柄缓存及在途200/304删除回归。保留 reserved 编码、owner 与 query 的不等价对照。
3. 旧精确直达表示缺少 fence 时以序号0检查所有删除历史；原兼容测试必须保留。跳转链继续要求持久证明。旧首次误拒直接缓存的候选失败与后续修正分开记录。
4. 升级前停止所有旧 Core；真实旧库升级、旧二进制拒绝、事务回滚与隔离备份分别验证。原版本号不等于新数据库可由旧二进制使用。
5. 本批完成后按原蓝图继续 B-24 的直接读取/迟到订阅与更广取消矩阵等剩余 S2 工作；来源实测、人工 qrels 与备份恢复继续遵循原门槛。

来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份隔离、永久幂等身份保留和抓取适配器六项决定保持。17 条来源 / 0 启用 / 0 站点，83 包 / 161 验收 / 10 切片与 37 partial / 46 not_started / 0 accepted 均保持。唯一推送目标是 `wmqfl861/bzs861`，审计分支只读；发布前核对准确父 SHA，非强推后检查实际 tree 与该 SHA 的 Linux/Windows。

## 上一批接手顺序（并发/来源退避，历史）

以下“当前/本批”限于 `claude_continuation_20261024`，不替代本轮 schema14 规则。

更新：2026-10-24。当前接续 Claude 审计 P2-5 / P2-6 / P2-7 / P2-9，版本仍为 0.1.10，唯一推送目标为 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。本批基线 `59081e14fdda765844031e13f874e588334f1a36`，tree `5d555fabc43180aa905e0581a301a3bafbeca1d5`；其 [CI 40067206826](https://github.com/wmqfl861/bzs861/actions/runs/40067206826) 只证明上一批输入。

## 当前接手步骤

1. 先读 [HANDOFF_START_HERE](../../HANDOFF_START_HERE.md)、[当前进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)及[本批验证记录](../../validation/claude_continuation_20261024/README.md)，核对实际工作树、包含提交、最终输入与该提交的 Checks。不得把作者聚焦或上一批 CI 当作最终整合验证。
2. 按 [搜索并发](../LOCAL_SEARCH_CONCURRENCY.md)、[后台调度](../BACKGROUND_DISPATCH.md)、[HTTP 执行](../HTTP_EXECUTION.md)、[来源退避](../SOURCE_BACKOFF.md) 核对合同。P2-5 保留短 SQL/正文复制/最终投影锁；P2-6 只隔离固定类别；P2-7 满额 503 且断连不自动取消；都不承诺任意并行或健康检查 SLA。
3. 阅读本批原始失败与独立审核。P2-9 首版 `743f79d` 的时钟回拨反例是已确认逻辑问题；只有与最终源码绑定的修正后执行才支持结论。新响应 24 小时上限、正常 parse-error 72 小时、可信绝对期限与无证明历史记录的一年极端异常启发式，按最终 SOURCE_BACKOFF 逐项核对；一年内或事件时间有歧义的旧值可能保留。
4. 根据本批记录的剩余项继续开发。上一批 P1-1/2/3、P2-1..4 与 P2-5 初次字段复用已是基线的一部分，不重复套旧补丁；`review/claude-2026-10-24` / `30f058557e3f7ebb873e9780759323b49cfe9636` 始终只读，不合并、不改文件。

本批沿用 schema13 和 `bzs-ascii-cjk/2+codepoint-grams/1`，不新增 tag。若从旧格式升级仍须停止旧 Core；备份没有独立撤销连续性证明则继续隔离。六项保留决定与 17 条来源全部禁用、0 站点、83 包 / 161 验收 / 10 切片、37 partial / 46 not_started / 0 accepted 均保持。

## 上一批 Claude 审计交接（历史）

以下原文中“本批 / 当前”只指 `claude_followup_20261024`。更早的 schema12→13、截止时间及 Windows watchdog 记录继续按原阶段保留。

更新：2026-10-24。当前接续 Claude 审计修正，已发布版本仍为0.1.10，唯一推送目标为[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。先读 HANDOFF_START_HERE.md、[当前进度](../DEVELOPMENT_STATUS.md)、[本批基线结论](../../validation/claude_followup_20261024/baseline-findings.json)、[本批验证记录](../../validation/claude_followup_20261024/)和[机器状态](../implementation_status.json)，再核对实际包含提交与其CI。上一轮 schema12→13、截止时间与 Windows watchdog 报告保留为历史，不代替本批验证。

本批基线为 `44253a752f945fdfab9810192e583b39fe5f6d83`，只读审计材料为 `30f058557e3f7ebb873e9780759323b49cfe9636`。修复顺序为 P1-2→P1-1→P1-3→P2-1..4，P2-5 原补丁评审后接纳；先核查已有阶段与剩余工作，不重写原失败或审计材料。本批实际输入、执行结果、准确提交与双平台 CI 见本批验证记录。

本地单次查询用 `search.once`；需要稳定分页仍用 `search/search.page`。永久任务/搜索幂等身份保留，明细容量独立计数。子串索引升级仍是 schema13，但必须先停止旧 Core，不能让新旧格式进程混用数据库；真实旧库升级及隔离证据见本批独立 p1-3 记录。显式跳转缓存的逐跳准入、计费、删除和原始/最终发布范围见 [HTTP_REDIRECT_CACHE](../HTTP_REDIRECT_CACHE.md)；反向 percent 身份等价仍是已记录的既存边界。

以下为上一轮接口和历史证据，段内“当前/本次”均限于对应历史修正。

## 上一轮基线与保留接口

本轮接手基线为0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`，tree `d5219c5d58e8dfef48a97f08df5b71dbc9b538f1`，CI39725326547成功。当前schema13组合来源发布、主体撤权与共享批读。真实schema12建库升级的可重放探针、旧39表摘要和备份隔离证据在validation/s2_takeover/migration，不能用手造旧表或修改user_version替代。

五项合同分别见SOURCE_PUBLICATION、PRINCIPAL_REVOCATION、HTTP_FRESHNESS、SHARED_READS、SEARCH_MODES。主体撤权是持久且不可重启用的管理员操作；MCP默认不暴露，显式--admin-tools仅允许stdio。旧缓存、会话和在途提交需要重查来源/主体/页面/删除限制。来源后来放宽不恢复旧结果，主体撤权也不随来源重启用而恢复。

缓存的原始限制独立于validators。no-store无新持久快照；截断证据可以保留但不作为新鲜缓存；cache_only不访问来源，无证明则拒绝。profile RPC后再检期限，提取不能重置响应年龄。共享读取只覆盖同owner且派发时已排队的兼容read_many，付款者取消不取消其余订阅，实际/未知用量只计一次。未知执行不自动重发。

搜索模式只执行搜索，fast/balanced/deep返回实际配置上限与omitted_sources；所有读取/模型/浏览器预算为0。旧无mode请求的normalized JSON与幂等身份保留，不能为了扩功能悄悄改变已有key。

生产实现的原本地冻结输入是validation/s2_takeover/final-v2-execution-inputs.json（222个文件）；27/27套CTest、15/15套ASan/UBSan及Python 1,049项通过，无跳过。原1秒真实截止时间E2E保持字节一致；公开参数最小1000毫秒不代表内部剩余预算也必须1000毫秒，任何仍为正的余量由绝对steady-clock期限收紧。旧mode reviewer的相反假设已在search_modes_deadline_review明确撤回。首轮失败不得删掉或改写为通过。

首次发布a06e7e9的CI39945994760中Linux通过，Windows构建成功、16/17套通过；http_revocation两例均在清理metadata.sqlite时遇到WinError32。Python SQLite事务上下文退出不等于连接关闭，该次HTTP修正只将rows()改为显式关闭，并在同一CI测试脚本加入成功/异常两条连接释放回归。该次保留生产代码、两条撤权HTTP业务测试与严格清理；对应冻结为ci-followup-execution-inputs.json。原v2不改写或冒充新测试输入，该HTTP修正发布为199eb635，Windows实际17/17通过，包含四项撤权HTTP测试；该成绩不代替其后的生产修正。

HTTP修正提交199eb635的CI39950116698又在Linux原恢复test_06出现queued/source_unknown/failed事件竞态。当前整体期限限制的RPC超时归为E_DEADLINE，并由Store对有效lease原子终止且只计一次unknown；普通较短来源超时仍可在原预算内继续尾部。原E2E字节不变，新5项native和独立6场景验证末来源、取消/撤权/旧epoch、冻结版本及普通错误对照。以deadline-followup-execution-inputs.json和对应报告绑定的新生产库为准；旧final-v2及HTTP聚焦成绩不代替当前全量验证，前两次失败不得改写。

生产期限修正发布为fcb58e4679a5bbdff17319b7b4ebff7019733eab，CI39954357148的Linux27/27与15套sanitizer通过；Windows16/17，crawl在45.02秒被整个测试进程watchdog终止，旧缓冲输出不能确认已完成用例。当前修正仅为Windows设置core90秒、crawl/site_publication120秒，并让crawl逐例即时输出RUN/PASS和耗时。45秒设置沿用自35例时期，现有54例；没有把该进程上限定义为产品性能验收。原用例/断言、业务期限、持久化设置和生产源码保持，Linux属性保持。本轮只重编crawl测试并执行聚焦Debug/ASan及保持检查，旧完整生产验证不改写为新执行；所有历史证据保留，新SHA仍须核对实际双平台CI及Windows有效时限。

## 接续开发

先查本批记录中的未完成项与准确提交验证，再按原蓝图处理剩余 S2 工作；六项保留决定不变，S2 范围、状态及是否冻结的决定保持现状。完整B-24仍需直接读取、crawl/search、迟到订阅与更广取消矩阵；AUTH-01仍需独立撤销journal、恢复连续性及远程多客户端隔离；MODE-01后续读取/模型能力必须有真实实现与独立预算，不可仅改标签。

QA-02先取得真实人工标签/裁决与目标环境证据，冻结方法/阈值后才比较质量；不得AI填人审、看holdout调参或用合成成绩冒充真实消融。S0保留真实来源审核/实测门槛；广泛长时、物理断电、跨owner集合、跨节点与依赖完整性不由有限回归代替。

## 验证与发布约束

保留原83包/161验收/10切片、依赖、估算与验收含义；当前37部分/46未开始/0完整签收。17搜索来源默认禁用、站点为空，原黄金字节及20/15/5划分不变。业务逻辑不加入平台系统调用；Core/Worker/MCP职责不变，独立撤销连续性未证明则备份继续隔离。

新source_publication/search_modes/cache_freshness/revocation原生目标须留在Linux sanitizer与Windows门槛；完整Linux CTest包括真实缓存/撤权HTTP及原恢复套件。缺依赖、跳过或不完整日志不能记作通过。按源文件与二进制哈希记录作者、独立、升级和最终执行，保留原失败。

所有历史validation、v1.1规格/backlog/schema/锁不改写；不用新的报告冒充旧运行。以准确父SHA非强制推送，远端前进先审差异；获取实际tree对齐本地，再查该SHA的Linux/Windows。公开仓库不含私人聊天、凭据、运行数据库或未批准外部原始内容。
