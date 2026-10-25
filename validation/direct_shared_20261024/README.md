# 普通直接读取共享：本批验证记录

本批从已发布 main **346048b4ae329e750d15f1e35952aab4513e3912**、tree **458a6f321e94defd74616ba6ebbf2983252cb495** 接续，推进 **CACHE-01 / FT-032 / B-24**。FT-019 对应 MODE-01，不是本批读取共享的验收映射。实现合同见[直接读取共享](../../docs/DIRECT_SHARED_READS.md)，最终机器结果见 [final-report.json](final-report.json)。

**本地最终门禁已通过。** 本文件与机器报告在本批提交、发布之前生成；用户已明确允许推送 main，旧批次的发布审批阻塞属于历史。发布后仍须核对实际远端 commit/tree 及其自己的 push CI，不把基线 CI 当作本批成绩。基线的 Linux / Windows CI 40157161849 已完成，原始记录按字节保存在 [published-baseline](published-baseline/manifest.json)。

## 实现范围

- 同一个 WebService 中，身份完全兼容的普通 web.read 共用一次 fetch.extract 和一次表示提交。身份包括 owner、精确 URL、实际 Worker 参数、完整权限/删除/发布捕获、缓存快照及 metadata 摘要、本次有效配置证明。
- 一组累计最多 32 人，同一实例最多登记 64 个仍开放加入的组；这不是全进程物理并发上限。RPC 返回即关闭加入，容量不足或旧 Worker 无有效配置证明时保持独立读取；不借用记忆中的证明，不自动重试。
- 多人成功结果核对 Worker 实际 profile；每个成员单独构造窗口，并在 Store 事务内重查权限、删除、metadata 和发布历史。付款成员窗口失败不污染其他成员。304 只提交一次，也不能拼接后来更新的 metadata。
- 付款者承担物理增量，订阅者为已知零增量；关联物理用量不能重复相加。未知保持 null/known=false，失败不冒充零消耗。HTTP / CoreClient / MCP 透传已有执行详情，工具名称、参数和数量保持。
- 组内只冻结窗口所需的九个字段，每个字段单独序列化。正文仍由 Store::range 读取，避免完整快照中重复正文和发布片段引入新的聚合 JSON 上限。

普通直接读取仍为同步接口，与持久 read_many 各自协调；Worker 参数、60 秒公开超时上限、max_bytes 的输出窗口含义和原截断处理保持。版本仍为 **0.1.10**，schema 仍为 **15**，没有新增迁移或 tag。

## 最终执行与计数

| 验证层 | 实际结果 | 记录 |
| --- | --- | --- |
| 完整 CTest | **28/28**，零失败、零禁用、零跳过，207.694 秒；嵌套组件也无实际跳过 | [命令及输入绑定](final/ctest-v2-command.json)、[完整输出](final/ctest-v2-detail.log)、[JUnit](final/ctest-v2-junit.xml) |
| CTest 内原生部分 | **16 个目标 / 544 组 / 20,397 次断言** | [机器汇总](final-report.json) |
| CTest 内 Python 部分 | **1,294 项通过** | [完整输出](final/ctest-v2-detail.log) |
| 正式新增回归 | **23 组直接共享 / 3,636 次断言**，另有 13 项 MCP 测试、3 项 HTTP 测试；均已包含在上述门禁内 | [原生源码](../../tests/direct_shared.cpp)、[MCP 源码](../../python/tests/test_mcp_read.py)、[HTTP 源码](../../tests/integration.py) |
| ASan / UBSan | **16/16 个目标通过**，重放同 544 组 / 20,397 次断言，保持 detect_leaks=1 和 halt_on_error=1 | [最终选择及失败保留说明](final/sanitizer-closure.json) |
| 原设计合同 | **34/34** | [合同执行](final/contracts-v2-command.json) |
| 规划保护 | **13/13**；原结构 83 包 / 161 验收 / 10 切片有效 | [保护执行](final/plan-guard-v2-command.json)、[只读结构检查](final/plan-v2.log) |
| 锁定依赖 | pip check 成功 | [依赖执行](final/pip-check-v2-command.json) |

原生、Python、HTTP 和端到端组件属于 28 套 CTest 的内部组成，不能再加到套件总数上。作者聚焦、全量、独立重放和 sanitizer 也不能相加成新产品场景或蓝图验收项。JUnit 对部分 stdout 明确截断，因此原生数量来自完整 LastTest.log 副本；[独立核对](independent/closed-ctest-review-v2.json)证明其全部 28 段 stdout 与已绑定哈希的 verbose 日志一致。

最终 sanitizer 选择为 **14 个 v4 成功执行 + 2 个 v5 补跑执行**。v4 的两个目标在启动子进程之前因执行位缺失报错；核对它们与成功构建的内容哈希完全一致后，只恢复执行位再运行。v4 驱动的 all_passed=false 原记录保留，不能称其整个批次直接成功；最终结论逐个绑定 [sanitizer-closure.json](final/sanitizer-closure.json) 中的 16 份实际成功记录。

## 精确输入和独立审核

[最终输入](final/post-run-inputs.json)绑定 **227 份非 validation 源码、测试、文档和配置及 recorder**，以及 **38 个二进制/生成配置文件**的 SHA-256。最终 CTest、16 个选定 sanitizer 执行、四项附加门禁均使用相同输入和二进制内容，运行期没有源码变化。两次最终构建记录为 [Debug](build/candidate-v5-command.json) 和 [ASan](build/asan-v2-command.json)；sanitizer 执行位修复没有改变二进制内容。

| 构建对象 | SHA-256 |
| --- | --- |
| Debug 静态库 | eb4fcd85d839bd43e78382943c8d0a7c73f47c178ff528a5fcd20851cef6404f |
| ASan 静态库 | 50d9573e5c88e21768364f935cc52447bc68fbe825e2cf93d902a6a10906355c |
| searchd | e873a92e499a70bf1e5daf1749c9510d2380756e56c84bff86045e8be11867f8 |

[独立审核](independent/independent-review.json)与正式测试作者分开。最终 13 个主边界场景、3 个补充场景和 2 个容量场景合计 **18 场景 / 679 检查**，Debug 及相同场景 ASan / UBSan 均通过；实际 searchd → 回环 Worker → CoreClient / MCP 的 **12 场景 / 137 检查**通过。后者属于传输与适配层，不增加原生共享场景总数。

独立验证包括同一次尝试只提交一次、已知/未知收据、窗口隔离、等待超时、旧 no-store 的迟到绑定、撤权/删除/发布屏障、Worker 详情注入不能伪造 Core 收据，以及真实旧库与候选的大容量对照。原独立源码、命令、日志、输入、链接库和检查数量见[独立材料说明](independent/README.md)；原执行材料封存清单为 [archive-manifest.json](independent/archive-manifest.json)。

旧能力基线使用真实已发布 346 的头文件和静态库，库哈希为 35e9e32d2e4b87091ac3aa2688f278a45bee79226bd39cf551ac01edb67d58d0；它与此前最终构建绑定，来源见[基线证明](independent/baseline-source-provenance.json)。旧版两个并发读取确实各自获取且不返回新增收据，这是缺少新能力的对照，不是候选缺陷。

## 原失败与修正记录

所有阶段按不同文件名保留，不用后来的成功覆盖原失败。下列类别彼此不同：

| 记录 | 真实原因及处理 |
| --- | --- |
| configure-v1 启动失败 | recorder 的位置参数写法错误，使 --build 被当成子命令；CMake 未启动。修正调用顺序后 configure-v2 成功。原启动错误保留在 build/。 |
| candidate-v1 构建与 retained-read-contracts-v1 | 构建本身结束，但其他输入仍在整合，不能作封闭最终构建。保留的 core 还出现真正超时；相关记录未计入最终通过。 |
| [core-hang-analysis](focus/core-hang-analysis.json) | Error 新增默认 Json 临时参数在旧 Json 校验锁内析构，造成重复加锁。改为分别提供原三参数和带详情构造函数；原 JSON 测试及最终全量通过。 |
| 独立 candidate-native-v1 与 corrected oracle | 初次 4 项失败中，3 项错误地拿历史 snapshot.indexed 判断当前搜索发布。用真实 Store.search 修正新审阅器后，在同一旧候选库上仍保留一项真实失败：畸形用量过早抛错，绕过合法限制。原始与纠正结果均保留。 |
| 用量 / profile 限制路径 | 延迟错误传播，使独立有效的缓存撤销和 noindex 观察先按原屏障应用；畸形 freshness 也不能在缓存谓词中再次过早返回。正式第 21、22 组及独立对照覆盖修复。 |
| [ctest-v1](final/ctest-v1-analysis.json) | 26/28；crawl 的 11 个旧场景与 retention 的 1 个场景在旧 fake 方法断言前失败，因为未识别新增可选 fetch.profile。只加类型化不支持分支，保留原断言、计数、回调；最终原场景全部通过。 |
| [容量旧版](independent/capacity-baseline-v1/summary.json) / [v2 失败](independent/capacity-candidate-v2/summary.json) / [v3 修复](independent/capacity-candidate-v3/summary.json) | 合法 716,800 字节正文在旧版两条路径成功；v2 的完整快照聚合 JSON 超过 8 MiB，提交后 E_JSON_LIMIT。改为九个独立字段后，同源容量场景和正式第 23 组通过。 |
| 正式容量回归 v4 → v5 → v6 | 新测试先错误要求可为空的 representation_publication 必须有 snippet_text；只删除这一条新断言。随后根据 v5 真实 20.429 秒，将仅容量组的两个读取显式配置为原有 60 秒上限，以验证容量而非 sanitizer 性能；原 22 组、生产超时和 CTest 90 秒门禁保持。见 [v6 记录](regression-author/direct-shared-v6-record.json)。 |
| MCP 新测试初次期待 | 新断言漏掉 SDK 自带的 ToolError 前缀；只修正新测试期待，随后 13 个新增测试及一项旧错误测试通过。全量 Python 再次包含它们。见 [MCP 归档](mcp/manifest.json)。 |
| [受限 sanitizer v3](asan/restricted-environment-analysis-v3.json) | /proc 进程检查被环境限制，8 份已关闭执行报告 LeakSanitizer fatal；驱动停止时另有 1 份未关闭记录，不能算通过。在有正常进程可见性的环境中保持同一检测选项重跑。 |
| sanitizer v4 两项启动失败 | revalidation 与 cache_freshness 的本地二进制缺少执行位，Popen 在子进程启动前失败。日志为空、原始 traceback 未单独完整捕获，记录没有伪造子进程退出值；验证哈希后仅修复权限，两项 v5 实际执行通过。见[最终闭合记录](final/sanitizer-closure.json)。 |
| 审阅器自身修正 | 保留审计首版漏列 README 的合法更新；文档审计先读取到后来修正前的测试 hash；CTest 审阅器先错误要求被截断的 JUnit stdout 完整相等。保留原记录，以各自最终审计为准，不把这些误判算作产品缺陷或产品测试。 |

早期一次宽范围 MCP 运行在受限环境中中断，完整 stdout 与确切 argv 无法恢复；[缺口说明](mcp/interrupted-broad-run.json)原样保留，不将其写成通过。最终 pytest 的完整命令、输入和输出另行闭合。

## 保留项与剩余边界

[保留审计](preservation/audit-v3.json)的 38 项静态检查通过；**3,590 份旧 validation**的预冻结 SHA-256、基线 Git blob 和当前文件完全一致。features 数组 **122,468 字节**保持原始字节，SHA-256 为 f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d。原 planning、config、golden、fixtures、依赖锁、版本和完整迁移函数保持；构建门禁精确逆删新增 direct_shared 项后可还原基线。

六个旧 fake 文件中的八个 profile 不支持分支可精确逆删，整文件恢复基线。唯一旧断言语义适配是 cache_only 的 profile 计数改为相对调用前值，仍要求这次 cache_only 不调用 profile、物理请求数不增加；未声称所有旧断言字节都不变。旧 HTTP 三个类和其余旧测试的保留证明见[审计说明](preservation/FINAL-v3.md)。

来源选择/审核/启用、S2 范围/状态/冻结、validation 入库、备份恢复隔离、永久幂等身份、抓取适配器六项决定保持。默认 **17 条来源 / 0 启用 / 0 站点**，原 **83 包 / 161 验收 / 10 切片**及 **37 partial / 46 not_started / 0 accepted**保持；review/claude-2026-10-24 的 30f058557e3f7ebb873e9780759323b49cfe9636 只读。

本轮不包含跨实例/跨进程、direct 与 durable 混合或 map/crawl/search 的共享；没有公开 direct 取消 ID、持久执行/计费账本或重启恢复保证。外层 Service::dispatch 在 WebService 返回后再次检查主体时，仍可能在直接路径包装之外抛出无详情错误，不能据缺少详情推断零消耗；本批没有宣称修复此边界。完整 CACHE-01 / B-24 未签收，受控离线测试不代表真实来源审核、生产负载或任意故障验收。

## 复核与重跑入口

- [run.py](run.py)记录实际 argv、UTC 时间、环境覆盖、全部声明输入、构建产物和完整 stdout/stderr，拒绝覆盖同名记录。选项放在 stage/name 之前。
- [run_sanitizers.py](run_sanitizers.py)读取原 asan-core 的全部目标，不弱化 sanitizer 选项；当前完整闭合以显式选择记录为准。
- [summarize.py](summarize.py)验证最终记录与当前字节一致后产生机器报告；报告本身不是测试，现有输出不允许覆盖。
- [保留审计脚本](preservation/audit_preservation_v3.py)可使用新的 --output 文件名复核；静态检查不能计成产品测试。
- [文档/接口最终审计](docs-audit/audit-final-v5.json)绑定最终 227 份输入，明确收据资格、未知与零、历史 features 文本、304、共享容量和九字段交付，不预写未来 CI。
