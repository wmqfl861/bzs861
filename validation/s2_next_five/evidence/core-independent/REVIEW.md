# 条件刷新增量：独立核心审核

## 身份与输入

审核者：`qa_ablation` 代理。本代理是 QA 来源消融模块的作者，但不是本次 C++ HTTP 条件刷新、发布规则或 robots 回收实现的作者。核心审核期间没有修改主仓库文件；独立反例及证据写入本目录，修复和永久回归由主任务作者提交。

- 比较基线：`2c536e9834c4897b0c70e3bd6651b765cc67f45e`。
- 最初核心候选：`28b60fdf2836407a9a9d81faa0e2a20129017bf1`。
- 修复后冻结代码：`d9fba205a66b31aa89a761c3b7606cd456aaf1fa`。
- 冻结源文件清单及逐文件 SHA-256：`input-d9fba20.json`，文件 SHA-256 为 `4b7c295394849678daab4327ec633c1c1530feca32027ca82774e61b9af043fc`。采集时清单全部文件与冻结提交一致。
- 最初候选源文件清单：`input-28b60fd.json`。首次实际反例链接的库已包含单独告知的 `map_service` HTTP cache 协议兼容改动；因此不把首次执行冒称为纯 `28b60fd` 构建。

审查覆盖 `include/bzs/{http_cache,store,web}.hpp`、`src/{http_cache,store,web,map,map_service}.cpp`，以及后续修复涉及的 `src/{publication,retention}.cpp`、相关 schema 期望和测试接线。没有修改默认来源、站点清单、黄金查询或计划验收状态。

## 结论

在已审查的增量范围内，发现的 robots 最新拒绝决策回收问题已修正，原失败反例随后通过。另一代理提出的 nofollow 丢失风险，经本代理通过失败 HTTP 响应的协议路径独立验证，修复后未出现旧链接重新扩张。最终冻结代码重链后的 7 tests / 81 assertions 全部通过，未发现尚未解决的阻断项。实际执行与哈希见 `verification-d9fba20.json`，其文件 SHA-256 为 `455e1d9710c77f7b67cd95175779923fb5d4f95d9791ec248d5a9d98cccede5a`。

这份结论仅评价本次条件刷新增量和列出的边界，不是 S2、WEB、Crawl 或五阶段计划的整包签收。

## 发现及修正

### 1. 已证实：选择性任务回收可删除最新 robots 拒绝决策

旧 `compact_robots_locked` 只依据记录过期和是否被任务引用决定删除。可以通过正常公开 API 创建以下状态：较早的允许规则由长重试期限任务引用；较新的 HTTP 503 拒绝记录由短任务引用。七天保留期后，短任务可回收，长任务仍保留，导致较新的拒绝记录被删除。下一次 robots 刷新把旧的允许规则视为最新并发送其条件验证器。

独立 harness 使用 `submit_crawl`、执行、时钟注入和 `compact_jobs`，没有直接 SQL 写入或伪造租约。修复前第五项断言 `lease->conditional.is_null()` 失败。保留了修复前静态链接二进制、日志和输入哈希，参见 `counterexample-before-fix-20261022.json`。

修复将同一 owner/origin/manifest 的最新决策保护到其较早记录全部消失为止。没有引用且过期的较早记录仍可删除；有界下一轮可删除仅剩的最新记录，整个操作不超过传入删除上限。因此既阻止旧许可重新暴露，也允许完整过期历史最终释放容量。相同 harness 重链后第五项通过，参见 `counterexample-gc-fixed-20261022.json`。

### 2. 跨模块发现，独立复验：旧正文的 follow=true 不应覆盖后来 nofollow

另一代理发现：已经保留的正文允许 follow，之后一次失败 HTTP 响应持久化 nofollow，再收到不携带新指令的 304，可能沿用旧正文的 follow=true 扩张链接。

修复在构造条件刷新 base 和最终提交事务中，都与当前 `document_publication` 记录取限制交集，并在 Crawl 入队阶段读取实际落库 snapshot 的 follow 标志。这样既覆盖请求前的新限制，也覆盖请求过程中产生的新限制。

独立 supplemental harness 从完整 Crawl 正文开始，模拟 HTTP 503 携带 nofollow，再执行没有新指令的 304 Crawl。结果只有父页面的一次条件请求，没有子链接请求，最终 follow=false，正文与原有 evidence 引文保留。该用例通过，参见 `publication-v2-20261022.json`。这里不声称本代理独立复现了该问题的修复前失败；本代理独立验证了修复后的协议行为。

## 静态审查

| 关注点 | 审查判断及边界 |
| --- | --- |
| 条件请求身份 | 仅当前、未删除、完整且无重定向的 web snapshot 可提供 base。绑定包含 owner、准确 URL、snapshot、完整 metadata SHA 和 cache SHA；提交时重新核验，不能只凭旧 ETag 复用别的正文。 |
| 并发修改与删除 | 事务内检查删除 generation、current snapshot 和 metadata，拒绝过时 304；同 snapshot 的另一次 metadata 刷新也会改变绑定。 |
| 旧正文发布边界 | 304 使用原正文 publication sequence 和 site generation。当前发布限制在 base 与提交时再次合并；完整 200 才能在新的合法边界恢复发布。 |
| robots 最新性与引用 | 先选择最新决策，再判断新鲜度和条件刷新资格。304 必须匹配精确记录、绑定和规则；任务准入与回执引用 pin 精确记录。修复后的回收不能暴露较旧决策。 |
| schema 12 与容量 | 新增 `snapshots.http_cache_bytes` 和 robots HTTP cache 字段；正文/缓存 metadata 用量纳入 owner 配额。更新、失败回滚、移除和删除路径有对应计费。 |
| 请求及字节预算 | 304 仍消耗真实的一次请求，正文 0 字节；已知未消费的字节预留可释放。并未把请求失败当作空内容或免请求。 |
| Crawl 链接复用 | 仅完整、有界、已验证的保留 HTML 链接可重建 frontier；使用当前租约深度重新执行 scope、trap 和 follow 检查。不会直接复用旧 frontier 深度。 |
| HTTP metadata | 精确版本及字段，验证 ETag、日期、profile、URL 和有界 ASCII 内容；拒绝不允许的 Cache-Control/Vary 条件。新实现保守拒绝部分复杂扩展语法，这里不宣称实现了完整通用 HTTP 缓存。 |
| Worker/C++ 接口 | `map_service` 接受 Worker 的顶层 `http_cache`，若嵌套副本同时存在则要求一致；未放宽 body/publication 身份校验。 |

## 独立实际执行

以下均为本代理编写反例、实际编译并运行。复用了冻结的合成 Worker/临时库测试夹具；没有真实站点请求。

| 独立用例 | 结果 |
| --- | --- |
| 同 snapshot 并发 metadata 刷新使旧请求失效 | 通过；外层请求得到 `E_REVALIDATION_STALE`，新 metadata 保留。 |
| 304 请求过程中新增 noindex | 通过；旧正文没有重新公开索引。 |
| 304 metadata 增长撞到精确 owner 配额 | 通过；`E_QUOTA`，旧 metadata 与用量原样保留。 |
| 304 后完整 200 清除过时 binding metadata | 通过；只退回对应 metadata 用量，随后删除后用量为零。 |
| 选择性任务回收后的最新 robots 拒绝 | 修复前失败；修复后通过。 |
| 失败响应 nofollow 后无新指令的 304 Crawl | 通过；无子链接扩张，旧正文/evidence 保留。 |
| 站点 reject→approve 后 304 旧正文 | 通过；旧正文保持不索引，后续完整 200 可恢复。 |

首组 5 tests / 51 assertions。补充组 2 tests / 30 assertions。修复后的中间构建及最终冻结构建均为 7 tests / 81 assertions / 0 failures。最终实际链接库 SHA-256 为 `27340749be005770281d50aea3cccbd50a2f2a9b768808e37fb3c9471761dfbf`，编译运行前后保持一致；源文件清单前后均匹配冻结提交。`verification-d9fba20.json` 保存实际编译命令、库哈希、二进制哈希、源文件匹配及运行日志。

补充 harness 的 v1 曾额外断言“限制收紧也必须保持 snapshot_id”。publication 是表示身份的一部分，该假设不成立，两个断言因此失败；这不是产品缺陷。v1 源文件、二进制、日志保留。v2 改为检查保留正文、旧 evidence、实际发布权限及允许完整 200 恢复，避免把错误测试假设掩盖成修复。

## 审查边界

- 本代理的独立执行只有上述 7 项；主任务作者执行的 21 项原生 revalidation、6 项真实 Worker 协议回归及其他整套测试，不重复计为本代理独立执行。
- 这里使用合成 WorkerPort、临时 SQLite 数据库和可注入时钟。没有声称真实来源批准、实时 robots/站点可用性或生产 HTTP 成功率。
- 没有做 Windows、长时间负载、电源中断、完整多进程故障注入或所有 HTTP 缓存语法兼容性验收。
- 独立反例没有通过 SQL 写入篡改业务状态；少数用量及记录计数检查使用只读 SQL。
- 当前调度对同一 origin 串行授权；本报告没有把嵌套 metadata 回调测试描述成并行 robots 调度实测。
- 未进行 qrels、人类标签、holdout 或搜索质量评估，未把本次局部实现记为计划工作包完成。
