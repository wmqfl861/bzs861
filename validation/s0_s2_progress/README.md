# 0.1.2：S0 证据与 S2 显式批量读取验证

日期：2026-10-18。输入为已交付 0.1.1 的 [`426c431`](https://github.com/wmqfl861/bzs861/commit/426c431fd3b39390564045e31316e79ca3f27483)。本批为 **S0/S2 development candidate**，本地完整回归与三套原生 sanitizer 通过；真实云端来源尝试均在 DNS 阶段失败，尚无真实来源可用性验收。

完整结果见 [test_report.json](test_report.json)，实际环境见 [environment.json](environment.json)。[source_manifest.json](source_manifest.json) 绑定本批代码、配置、测试与文档的字节哈希，报告另绑定本目录证据。包含这些文件的 Git 提交确定最终版本；发布后的 CI 必须查询该提交，不能用输入提交或其他提交的结果替代。

## 本批完成的实现

- **可恢复来源探测**：schema 2 报告绑定清单、黄金查询、选项、解析器实现和依赖；请求前落盘预留，同目录互斥，逐项保存完成、不确定和未发送原因。恢复不自动重发已完成或执行结果未知的请求。
- **冻结黄金输入与离线回放**：保留原 40 条查询内容，新增 20 development / 15 regression / 5 holdout 和冻结 manifest；按能力与分类确定性选题。回放比较保存的完整结果，检查正文长度/哈希和解析器身份，不连接网络；没有可回放行不能记为通过。
- **持久批量读取**：1–20 个显式 URL，按输入顺序返回逐项状态与收据，支持暂停、恢复、取消和显式失败重试。任务跨重启保留进度、截止时间、尝试次数及请求/内容字节预算。
- **发布与预算边界**：每次外部读取前持久预留额度；已知用量退款，未知执行保守保留额度。HTTP 与软跳转共用预算。结果入库、索引、收据和用量结算处于同一事务，重新核对租约、权限、取消和删除代际。
- **接口与迁移**：新增 MCP `read_urls`、`get_job`、`job_results`、`control_job` 及原生 CLI 对应入口；schema 4 增量迁移保留 schema 1、两种历史 schema 2 与 schema 3 的内容、证据和隔离边界。

API 参数、默认额度、字节计量范围和显式重试语义见 [docs/API.md](../../docs/API.md)。本批处理明确 URL 列表，链接发现、robots 调度和递归爬取仍待后续工作。

## 实际执行结果

| 验证范围 | 本批结果 | 证据 |
|---|---:|---|
| 原生核心 | 81 用例、713 断言通过 | [ctest.log](ctest.log) |
| 新增批量任务 | 18 用例、439 断言通过 | [ctest.log](ctest.log) |
| 原生合同 | 55 用例、269 断言通过 | [ctest.log](ctest.log) |
| HTTP / CLI 进程集成 | 20 用例通过 | [ctest.log](ctest.log) |
| worker / core / MCP 端到端 | 13 用例通过 | [ctest.log](ctest.log) |
| Python worker / probe / replay / golden / MCP | 302 用例通过 | [ctest.log](ctest.log) |
| ASan / UBSan / 泄漏检测 | 核心、批量、合同三套全部通过 | [core](asan-core.log)、[batch](asan-batch.log)、[contracts](asan-contracts.log) |
| 设计合同参考 | 34 用例通过，与原生合同存在交叉覆盖 | [design-contracts.log](design-contracts.log) |
| 规划工具回归 | 13 用例通过 | [plan_guard.json](plan_guard.json) |
| 计划完整性 | 83 包、161 映射、10 切片保留 | [plan-check.log](plan-check.log) |
| Python 依赖一致性 | `pip check` 通过 | [environment.json](environment.json) |

普通 CTest **6/6 通过，18.75 秒**。各次命令的 argv、工作目录、UTC 时间、退出码和耗时保存在相邻的 `*-command.json`。原生 sanitizer 开启 `detect_leaks=1`；HTTP、端到端和 Python 使用普通构建。不同套件有交叉覆盖，不把它们相加作为产品验收总数。

端到端实际验证了 10 个显式 URL 中 9 项成功、1 个 PDF 独立失败，保持输入顺序并且幂等提交不重发；MCP 链为 `read_urls → get_job → job_results → read_range`。故障回归包括暂停后跨重启恢复、旧租约/撤权/删除后的结果拒绝发布，以及收据写入故障时快照与索引一起回滚。

独立启动器完整生命周期本次未重跑；其旧 7 条命令、16 个断言保留于 0.1.1 历史报告，不记成本版成绩。新增 probe/replay CLI 已实测，原生 CLI 与 MCP 在本批进程集成套件中覆盖。

## 有限云端来源观察

本批只选择 Marginalia 与 Wikipedia 英文 API，每来源计划最多 2 题。实际各尝试 1 题，首次失败后停止该来源剩余请求。

| 来源 | 实际尝试 | HTTP 响应 | 结果 |
|---|---:|---:|---|
| Marginalia | 1 | 0 | `ClientConnectorDNSError` |
| Wikipedia 英文 | 1 | 0 | `ClientConnectorDNSError` |

总计 **2 次派发尝试、0 个上游 HTTP 响应、0 字节正文、0 条搜索结果，2 道计划题未发送**。请求计量包含 DNS/连接失败的已派发尝试，不表示上游服务器收到请求。probe CLI 返回 1，如实表示所有尝试均失败。没有付费回退或模型调用；默认来源清单与输入基线字节一致，17 条仍全部禁用。

[云端摘要](cloud_probe_summary.json)、[原 probe 元数据](cloud_probe/probe_report.json) 和 [离线回放](cloud_replay/replay_report.json) 保留证据。原报告经检查只有公共黄金查询、空响应体和安全错误元数据，没有原始网页、凭据或运行数据库。离线回放 2/2 匹配、网络请求 0，匹配的仅是两条网络错误状态，未取得有效来源结果样本。

这次观察不能判断来源在用户部署网络上是否可达，不能替代来源条款审核、人工相关性标签或完整 S0 验收。后续需在目标部署环境使用冻结输入取得真实响应，再分别完成来源审核和质量评测。

## 交叉复核与保留问题

[independent_review.json](independent_review.json) 区分实现作者、自测、主代理正式执行和其他上下文的只读交叉复核。已修复六个具体问题：缺响应回放误通过、截断 429 丢失退避、解析器注册表未入指纹、应用层 backoff 缩短、结束 attempt 用量被清零、恰满字节预算的延迟 EOF 误判。复核覆盖上述边界，不代表外部人工或全产品独立签收。

本目录保留一轮早期共享 alias 测试失败及修复后的 55 项中间运行。失败来自内存测试 handler 缺少异步调度点；增加测试让出事件循环后通过，生产调度没有据此改变。后续新增回归及最终实现由完整 302 项 Python 测试覆盖。

机器状态保持 **30 项部分实现、53 项未开始、0 项完整签收**；BATCH-01 是本批唯一从未开始推进到部分实现的工作包。83 个工作包与 161 项验收 ID 均保留，QA-02、SESSION-02 仍未开始，S0/S1/S2 均未签署完成。真实来源审核、人工质量基线、会话断线重取、独立撤销日志与恢复授权、系统级解析沙箱、长期压力及断电验收继续按计划推进。

Linux CI 包含全部六套普通 CTest、三套原生 sanitizer、合同参考及规划检查；Windows CI 新增阻塞的 batch 门槛，保留核心、合同、HTTP/CLI。Windows 的完整 worker 打包仍属 S9。发布后查看 [GitHub Actions](https://github.com/wmqfl861/bzs861/actions) 中同一提交的实际结果。

0.1.1 的 [历史报告](../s1_review/test_report.json) 和更早证据保持原样，不能代替本批执行。
