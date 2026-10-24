# 下一会话接手顺序

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
