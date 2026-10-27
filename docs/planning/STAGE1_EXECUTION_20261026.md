# 阶段 1 执行说明：缺陷优先，待决项不默认采纳

适用仓库：`wmqfl861/bzs861`。依据用户最新指令及只读审核分支 `review/claude-2026-10-26` @ `f00bd67379c2f0586c914a5a175878a1be2751c1`。
本说明补充 v1.2 的执行次序，不重定义原工作包、依赖或验收；不把读完审核当作验收。

## 状态与顺序

| 顺序 | 缺陷 | 当前状态 | 退出条件 |
|---|---|---|---|
| 1 | P1-B | 代码提交 `17783391143478209105a300d8eb174feab7df85` 已发布；本提交自己的 CI 单独核对 | 2 线程下 16 个延迟读取和 16 个 stats 无失败、stats p95 <1 秒；队满/过期明确拒绝且无迟到副作用；重试成功/耗尽；真实 MCP stdio 24 并发本地搜索 |
| 2 | P1-A | 方案 A 已实现并通过本地定向复验；精确提交 CI 另查，D7 未决定 | 至少 4,000 篇、每篇至少 30 KB 的离线自然文本，最旧目标的短语、node.js、café 命中；短文档和词内子串回归保持；记录 Release 耗时 |
| 3 | P2-C | 已实现并通过本地复验；准确提交CI另查 | 008、Mediapartners-Google*、非 ASCII 无关 UA 不使整站 deny_all，诊断可见；自身/通配损坏仍拒绝 |
| 4 | P2-B 第 1、2 点 | 已实现；本地完整回归与成本复验见本批摘要，发布状态单独核对 | CJK/混合/符号/溢出回归；派生 gram 字节计入统计和配额；同语料成本比较；不升业务 schema |
| 5 | P2-D | 2026-10-27 重建并通过本地定向回归；准确提交CI另查 | 不同 origin 的批读并发、同 origin 礼貌间隔和现有崩溃恢复全部验证 |
| 6 | P2-A 非 D5 部分 | 收据/归档/新库回收已重建；本批增加显式旧库转换，整合门槛未全绿，仍未整体签收 | 无键收据不永久增长、归档字节可见且计配额、增量回收行为有实际证据；有键身份保留不变 |

P1-B 新增等待队列不是无限接纳：普通队列默认 `4 * request_threads`，本地队列默认 `max(4 * request_threads,16)`；显式 `--request-queue` 覆盖两者。独立取消槽保持 1 槽且不排队。只对执行前的明确 `503/E_REQUEST_BACKPRESSURE` 做最多 3 次、初始 10 秒窗口内退避的重试；不重试未知执行结果。整体业务时限不会因重试重置。

## D1–D7 决策状态

审核 D1 来源、D2 S2 冻结、D3 长期证据政策、D4 恢复、D5 身份保留、D6 抓取适配器、D7 短语语义，均为 **等待明确回复**。审核建议不等于用户同意。
当前每项 validation ≤50 KB、原始日志放 CI artifacts，是用户已明确给出的阶段 1 限制，不是替用户决定 D3 的长期政策。既有证据和历史不删除。
不扩展 B-24 是当前执行限制；不据此把原验收自动缩减或签收。没有用户决定就不实施 90 天保留、词序列语义、来源启用或解除备份隔离。

## 技术事实与验证边界

当前 `bzs-ascii-cjk/2` 分词器处理 ASCII 单词、同位 C++/C# token 与 CJK 单字/二元组，并非包装 unicode61；非 ASCII、非 CJK 的文字仍依赖补充 gram 候选。原蓝图技术表的 unicode61 描述不是当前实现事实。本阶段不借此扩大为 Unicode/NFKC/casefold 改造。

P1-A 的词级候选只能作优先层，不能作为所有子串结果的必选过滤；剩余预算的 gram 层和精确复核必须保留。不得只让三个样例命中却丢失词内子串、布尔分支、字段范围或权限/发布约束。

原始报告的性能数字只代表其输入和环境；本轮不沿用为本机成绩。每个产品提交记录实际测试名、命令、通过/失败/跳过。原始失败和后续复测分开，CI 运行中不标成功。
阶段 1 未整体完成，S0/S1/S2 和工作包签收数量不变。阶段 2 的来源选审/S1 签收准备等待 D1、D2，阶段 3 的证据迁移/恢复/保留期等待相应答复。

## P1-A 本批实现

词级优先层按现有 bzs/2 tokenizer 和 BM25 取候选，剩余候选预算再从原路径补足；跨层按文档去重，最终统一用原 `match_score` 排序（并非改变短语为词序列）。优先表达式绝不是后备层的必选过滤。权限、发布、删除、快照与最终复核规则保持。总正文复核数仍受 `max_candidates` 约束，任何层未取尽均如实 `partial=true`；不声称任意查询都能在有限预算下完整召回。

新增 `long_document_recall` 覆盖4,001篇≥30KB文本及三个饱和gram反例；`priority_fallback_semantics`覆盖词内子串、字段、OR/NOT、owner与跨层去重。自然语言测试种子为本项目新编写的约2KB文本，确定性扩展/切片，无网络或第三方语料下载。正文、证据和业务schema不变。

## P2-C 本批实现

无关的数字产品名（008/360）、外语产品名以及Mediapartners-Google*被保留为“不匹配的组成员”，不修复成匹配token，也不把其规则泄漏给相邻组。空值、损坏的自身标识和以星号开头的损坏通配标识仍拒绝；UTF-8、控制字符和适用规则的失败关闭策略不变。原测试中360/爬虫的“不可分类即拒绝”预期有意移至新无关组测试；自身/通配拒绝用例保留并增加BzsBot*与插入Unicode的自身损坏例。

本次只修解析误拒，不启用站点或来源，不改变robots/站点授权关系、缓存身份或业务schema。

## P2-B 本批实现与当前接续

CJK窗口不再写入gram，混合叶子以必要CJK位置条件及非CJK gram组成候选超集；词级分词器和精确匹配语义不变。新增独立owner派生索引字节预算及stats，事务内更新/删除/撤回计量，首次导入不扫描不存在的旧索引。派生格式从gram/1到gram/2，业务schema15不变；低配额旧库重建保留原文，收紧发布不因原有超限而失败。合同见[子串索引说明](../SUBSTRING_INDEX.md)。

本轮三个产品节点为P1-A、P2-C、P2-B(1/2)，各一提交；之后只接续P2-D，再P2-A非D5部分。D1–D7依旧待定。作者复核与本地测试不等于独立外审或准确远端SHA的CI；发布受阻时保留提交及补丁，不宣称已推送。

## 2026-10-27 reconstruction supersedes stale delivery notes

Main `1d8e80e5f1bd1b5f9ad8d1e5fc211c8cd403ca31` already contains the preceding
three-node delivery. The later five-node source was not recovered, so its
unverified numbers are not reused. This recovery implements and tests P2-D and
the P2-A non-D5 receipt/quota/new-store reclamation work, plus mandatory cache
sharding/JUnit fixes. See [current handoff](../handoff/NEXT_AGENT.md).

P2-A is not fully closed for legacy storage conversion: existing NONE/FULL
stores remain unchanged and report `requires_offline_conversion`. No silent
full-file rewrite or unverified rowid/FTS repair is shipped. A future explicit
conversion tool must prove ordinary-table contents, rowid/index correspondence,
key/evidence preservation and quarantine without relaxing D4/D5. CI artifact
and Windows stability confirmation are separate from local implementation.
Original package definitions and all pending owner decisions remain unchanged.

审核另修复了后台停止期间等锁线程继续领取的竞态；fixture/read/map/search 的领取事务复查 stop_token，保留原租约及用量规则。原故障注入改为绑定首次准入，而非并发唤醒的随机赢家；新反例、原失败和最终整合复验分开保留。此补充不增加业务证明链或改 RPC 合同。

## 2026-10-27 后续三个模块

从PR #5准确输入接续，先补P2-A旧库显式离线转换，再做既有OPS-00启动器及API-00/CONTRACT-01输入边界。详见[NEXT_THREE_MODULES_20261027.md](NEXT_THREE_MODULES_20261027.md)。上文“旧库转换未实现”是前一批记录；本批提供停服、排它锁、临时预演、rowid/FTS保持和原库原子改写，不提供隔离恢复。完整P2-A及产品验收仍不能由局部测试推导。
