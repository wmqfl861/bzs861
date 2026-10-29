# 当前执行与全代码审核计划（2026-10-28）

## 当前输入与仓库状态

仓库：wmqfl861/bzs861
当前审核输入：PR #8 头 0c47a5f5539b47cbb5f53398ed6e41de5ba9eb69
当前审核提交：8085b86a844699f0590d6dcab2ec17f8234c6b31
当前分支：work/project-audit-owner-review-20261028
PR #9：https://github.com/wmqfl861/bzs861/pull/9
main 仍为 1d8e80e5f1bd1b5f9ad8d1e5fc211c8cd403ca31；PR #9 叠加于 PR #8，不等于 main 已包含这批成果。

## 准确提交门禁

- 原生 Linux/Windows 检查 run 40737010760：成功，包含 Linux ASan/UBSan 与规划保护。
- 完整 Windows Python safety run 40737010752：成功；实际 Windows 进程树测试为强制用例。
- 原始失败 run 40733581522、40735654661 及附件继续保留；不以旧失败冒充当前失败，也不删除它们。

## 本轮已交付（仍不代表整包签收）

1. Windows 进程树控制：句柄固定、根/候选身份核验、复查当前父链；未知退出状态保留记录，不误杀不能证明归属的进程。对抗同权限伪造与快照前已消失中间进程留下的孤儿不在保证范围内。
2. Worker host admission：注册表硬上限 4096，持有者/等待者/获取前引用分别计数，只回收真正空闲项；超容量在出站前拒绝。统计是单 Worker 本地诊断，不是全局/分布式配额。
3. 认证注册表：有界读取、严格字段/类型、重复键和重复身份拒绝，add/revoke 锁内读改写；新增只读 bzs mcp-token check，不输出令牌或摘要。
4. sitemap gzip：既有实现经 Worker→Core→stdio MCP 端到端验收，覆盖嵌套、循环/重复、范围/robots、幂等重放和损坏归档不发布部分候选。

新增节点归属 MAP-01、OPS-00、SEC-01、AUTH-01、API-00，均仍是部分实现。不能把这些节点的测试通过改写为整个工作包或 S1/S2 完成。

## 蓝图一致性修正

- 架构表中的 A1–A5 是历史架构选择；阶段文档的 D1–D7 是仍待用户答复的业务/政策决策。二者编号不得混用。
- 2026-10-27 三模块计划应视为历史批次；本文件是 2026-10-28 当前入口。
- 83 个工作包、161 个验收映射、10 个切片保持；完整签收 0。结构检查不代表产品验收。
- 当前状态权威来源是 docs/planning/slices_v1.2.json、需求基线及逐项验收证据。历史批次的 36/47/0、37/46/0 仅保留为历史快照，不作为当前测试成绩。

## 未完成审核范围与下一步

1. 对 evaluation/probe/replay 离线评估工具做端到端检查，核对输入冻结、恢复身份、uncertain 请求不重发及证据字节。
2. 对 crawl/map 长时间交互、取消、同源礼貌间隔和停止响应做有界 soak；不启用真实来源，使用隔离回环站点。
3. 对 web.cpp 缓存锁跨 Store 事务的序列化、search_registry_identity 重算及 publication SQL JSON 解析进行同输入基准；只有量化收益且不改变锁序/事务/语义时才优化。
4. 继续审查 mcp_auth/HTTP transport 的授权边界与错误响应，但不启用远程 MCP、不扩大工具权限。
5. MSan/TSan 当前未配置，不宣称通过；保留现有 Linux ASan/UBSan 门禁。

## 不变约束

D1–D7 仍待用户明确答复；来源启用 0；业务 schema 15；永久带键身份、B-24 范围、owner/删除/撤权屏障与隔离备份保持。不得修改 83/161/10 的定义、删除历史失败证据或把结构校验当产品验收。每个 validation 摘要小于 50KB，原始日志放 CI artifacts。


## 2026-10-28 后续开发：离线证据输出的原子发布

新增 \`artifact_output.write_new_directory\`：evaluation package 与 replay report 先在同一父目录的 staging 目录中写完，逐文件 flush/fsync，再在稳定 SQLite sidecar 锁内一次性重命名发布。任何文件写入失败时清理 staging，最终目标目录不留下半成品；同名既有结果仍拒绝覆盖。锁文件保留，避免删除锁文件造成并发锁分裂。

新增 \`test_prepare_output_failure_never_publishes_a_partial_package\` 与 \`test_replay_output_failure_never_publishes_partial_report_directory\`，模拟第二个文件写入时磁盘失败，验证最终目录不存在、staging 被清理，恢复后可用同一路径重新运行。该变更不触发网络、不创建标签、不放宽 holdout/质量门槛。准确提交的完整 CI 是最终验收条件。

## 2026-10-29：离线证据可靠性五节点增量

本批继续在既有离线评估/回放链上开发，不启用来源、不调用模型、不生成真实相关性标签：

1. Probe 失败信息脱敏：SourceError 只记录受限状态码，其他适配器异常只记录异常类别；不把异常文本、Authorization 片段或 URL 查询参数写入持久报告。
2. 跨平台输出路径校验：最终目录与文件名拒绝斜杠/反斜杠、ADS 冒号、NUL、尾随点/空格和 Windows 保留设备名，避免 POSIX 生成 Windows 不可移植或危险的产物名。
3. 并发发布合同：两个并发发布者争用同一目标时最多一个成功，最终 JSON/Markdown 必须属于同一发布者且完整；另一方明确拒绝。
4. 锁文件 fail-closed：发布锁若不是普通文件（例如同名目录）则拒绝发布，不能绕过锁继续写产物。
5. 目录发布持久性：POSIX 在发布前 fsync staging 目录，重命名后 fsync 父目录；父目录同步失败时明确报告“已发布但持久性未确认”，不谎称回滚。Windows Python 无可移植目录 fsync API，本实现只保证文件 flush/fsync 与同卷目录重命名，不宣称抵抗 Windows 物理断电。

新增/更新测试：
- `test_probe_persists_error_class_not_secret_exception_text`（SourceError 与普通异常两种情况）
- `test_artifact_output_rejects_cross_platform_unsafe_names`
- `test_artifact_output_rejects_device_directory_name`
- `test_concurrent_publishers_produce_exactly_one_complete_output`
- `test_artifact_output_rejects_nonregular_publication_lock`
- `test_parent_directory_flush_failure_reports_published_state`
- 保留前批 `test_prepare_output_failure_never_publishes_a_partial_package`、`test_replay_output_failure_never_publishes_partial_report_directory` 与离线链路端到端测试。

当前 head 的测试门禁必须以 GitHub Actions 实际结果为准；不得将 CI pending 写成通过。若失败，修复后再推送。

## 2026-10-29 三个大模块接续

A. 离线证据与产物发布：Probe错误脱敏、Evaluation/Replay原子发布、跨平台路径、并发发布及离线证据链门禁。

B. Crawl/Map有界持续交互：新增同源循环回归，分别执行48次map尝试（42实际dispatch、6取消前拒绝）和40次page尝试（36实际dispatch、4取消前拒绝），核对用量、重放、候选/页面产物、host admission和收据增长。这是短时确定性回归，不代表长时间soak或真实来源验收。

C. 本地搜索性能基准：新增`python -m bzs.local_benchmark --query "..." --iterations 20 --warmup 3`，仅向127.0.0.1的`search`操作发送请求，不调用`web.search`。记录延迟分布与查询哈希，不保存查询正文、响应正文或token，不宣称缓存命中。先提供可重复基线工具，待在目标Windows 11/同配置环境实测后才决定是否优化缓存锁/SQL。

对应测试：`test_map_sustained_same_origin_hops_keep_admission_bounded_and_cancel_replay_free`、`test_crawl_sustained_page_hops_keep_usage_and_cancelled_attempts_separate`、`test_benchmark_is_loopback_only_and_never_records_credentials_or_body`及参数边界测试。完整CI仍是合入门槛。
