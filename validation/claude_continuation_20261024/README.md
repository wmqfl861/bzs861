# Claude 审计续批：P2-5 / P2-6 / P2-7 / P2-9

本批从 GitHub main `59081e14fdda765844031e13f874e588334f1a36` 继续开发，基线 tree 为 `5d555fabc43180aa905e0581a301a3bafbeca1d5`。审计分支 `review/claude-2026-10-24` 的 `30f058557e3f7ebb873e9780759323b49cfe9636` 只读，不合并、不修改。

原八项复现、P1-1/P1-2/P1-3 和 P2-1 至 P2-4 的修复，以及上一批 P2-5 局部优化，保留在[上一批记录](../claude_followup_20261024/README.md)。这里记录后续四项改动；作者验证、独立审核、整合测试和基线历史分别绑定输入，不累加为产品验收数。

## 改动与正式回归

| 项目 | 本批结果 | 正式回归与保留边界 |
| --- | --- | --- |
| P2-5 | 精确匹配、评分和非 FTS 排序移出 Store 锁；匹配与评分共享惰性 ASCII 转换。返回/冻结前复核撤权、删除代际、快照、发布历史和容量。 | `tests/unit.cpp` 新增 9 项；SQLite 候选选择、单篇复制和最终投影仍持锁，不承诺任意操作零等待。详见[本地搜索并发](../../docs/LOCAL_SEARCH_CONCURRENCY.md)。 |
| P2-6 | fixture、read、map/crawl、持久来源搜索使用四条固定后台通道，各通道最多一个在途步骤。 | `tests/fairness.cpp` 新增 7 项，`tests/e2e_crawl.py` 新增 2 项；map/crawl 仍共用通道，同一类别内部仍串行。详见[后台调度](../../docs/BACKGROUND_DISPATCH.md)。 |
| P2-7 | HTTP IO 与有界业务线程池分开；默认 8 个业务槽，容量满在进入 Service 前返回 `503 E_REQUEST_BACKPRESSURE`。健康和传输检查留在 IO。 | `tests/integration.py` 的 `HTTPExecution` 新增 3 项；原 64 连接上限和 socket 期限保留。断连不自动取消或重发，健康检查没有任意负载 SLA。详见[HTTP 执行](../../docs/HTTP_EXECUTION.md)。 |
| P2-9 | 搜索来源的新 Retry-After/API backoff 最多 24 小时；Worker、Probe、Core、Store 分别约束并报告截断。合法绝对期限通过 settings 中的证明跨回拨、迟到响应和重启保留。 | `tests/unit.cpp` 新增 8 项，Python 新增 39 个参数化用例；旧记录只按保守的 365 天极端异常启发式修复，不足一年或时间有歧义的旧值可能继续保留。通用解析和 robots/map 政策不变。详见[来源退避](../../docs/SOURCE_BACKOFF.md)。 |

P2-8 的受支持恢复及撤销对账流程仍未实现，按用户要求保持备份隔离现状。P1/P2-1 至 P2-4 的正式回归继续包含在本批完整套件中。

## 逐项完整整合测试

每整合一项后均执行完整 CTest、完整 Python 测试目录和原有 15 个 ASan/UBSan 原生程序，保持 `detect_leaks=1:halt_on_error=1`、`halt_on_error=1:print_stacktrace=1`。所有 CTest 记录均为 27 项、零失败、零禁用、零跳过。

| 整合阶段 | CTest | pytest | sanitizer 程序 | 原生用例 / 断言 | 完成记录 |
| --- | --- | --- | --- | --- | --- |
| P2-7 | 27/27 | 1242 | 15/15 | 469 / 14407 | [result.json](p2-7/result.json) |
| P2-6 | 27/27 | 1242 | 15/15 | 476 / 14495 | [result.json](p2-6/result.json) |
| P2-5 | 27/27 | 1242 | 15/15 | 485 / 14891 | [result.json](p2-5/result.json) |
| P2-9，含全部四项产品改动 | 27/27 | 1281 | 15/15 | 493 / 15204 | [result.json](p2-9/result.json) |

最终完整产品输入见 [P2-9 execution-inputs.json](p2-9/execution-inputs.json)，SHA256 为 `664e800624784f7b364de1b7eb286a7d534e94abaafc9e586cc4dd5fad864c82`。其中核心程序为 105 用例、2034 断言；这些数字不是额外的一套测试。

完整产品测试之后，只应用已独立审过的八份交接/API 文档。后续 [专项检查](final-verification/result.json) 单独绑定文档差异、规划约束、Python 依赖和最终 `searchd` 的 HTTP sanitizer 生命周期；它不是又一轮完整套件。源码、正式测试、构建配置和记录器与最后完整套件的逐项相等关系见 [documentation-delta.json](final-verification/documentation-delta.json)。远端最终提交还由原 Linux/Windows CI 独立执行其既有门禁。

## 独立审核与最终组合复验

| 范围 | 非作者审核及证据 |
| --- | --- |
| P2-5 语义和并发 | [原独立审核](independent/search-lock/review-result.json)：48 个查询 × 182 个文档的 8736 组新旧匹配/评分结果一致；包括零分匹配、权限与发布历史、跨句柄、容量及不分配收据。最终组合的 [Debug/ASan 重放](independent/search-lock-final/debug-asan-review.json) 分别通过相同 19 场景、191 检查，不累加为 38 个不同场景。 |
| P2-6 与 P2-7 | [最终组合二进制审核](independent/final-http-dispatch/review.json)：HTTP 39 检查、后台组合 50 检查全部通过。默认 8 个同步 RPC 保持占用时，后台 read/map/search 各自第二项仍推进；各类别峰值 1、总 RPC 峰值 11。取消任务零尝试，已知用量不重复计入；断连、停止、恢复和 503 无业务副作用均有实际探针。 |
| P2-9 | [独立审核](independent/retry-after/review-result.json)：最终修正的协议、证明及时间矩阵共 72 个 native 场景，在 Debug 与 sanitizer 分别通过；另有 9 项 Python 检查。真实旧 590、被否决首版与修正版分别绑定，不将不同输入的结果替换。 |
| 四项合并 | [独立合并核对](independent/integration/merge-checks.json)：22 个相关文件恰为四作者改动并集，18 个单作者文件逐字节一致；Store/unit 双向三方重建一致；HTTP handle 和 WorkerPort 注释/常量只保留审核过的组合。 |
| 文档 | [独立文档审核](independent/documentation/README.md)：8 文件重放一致，83 features 原始 JSON 字节及 11 个既有历史对象保持，138 条链接核对通过。 |

最终组合 Debug `searchd` 的 SHA256 是 `88f646c2b091f5d2e6178730fb16a7187c64aa0aa25d84a604e59ab65d8bb4a2`。上述 HTTP/后台复验直接复制运行该二进制，执行前后 221 个非 validation 产品输入、fixture 和二进制保持。搜索探针使用与最终库配套的头文件重新链接，避免 Store 布局变化造成新旧 ABI 混用。

性能观察采用原 NOT 变体、真实 590 Release 与最终组合 Release 的相邻同机运行：2000 篇，正文目标每篇 204800 字节，沿用原驱动的填充及文档编号规则。两组都走 `bounded_scan_recent`。完整[性能记录](independent/search-lock-final/final-release/evidence/performance-result.json)保留驱动、头文件/库配对、编译参数、原始日志及前后哈希。

| 查询 | 旧 / 新搜索耗时 | 旧 / 新并发 `stats()` 最长等待 |
| --- | --- | --- |
| 单 NOT 短语 | 2567 / 640 ms | 2566 / 150 ms |
| 四 NOT 短语 AND | 2573 / 1202 ms | 2573 / 157 ms |

这些是本机合成数据各一次观察，没有重复采样、置信区间或性能通过阈值，不推导生产延迟或吞吐保证。两个完整进程各约 53 秒，包含数据/索引构建；该时间不是查询耗时。确定性的并发正确性证据来自前述独立闸门场景。

## 原失败和证据限制

- P2-7 首次 sanitizer 命令退出 0，但日志只保存到第九个程序中段，完整性不合格；[原记录](p2-7/validation-status.json) 保留并明确排除。相同产品输入、相同 15 份原生二进制改用完整字节捕获后重跑通过，采用 [p2-7-asan-repeat](p2-7-asan-repeat/asan-command.json) 的完整结果，不把原截断日志算成全套通过。
- 捕获器关闭日志后才生成完成记录；不声称写文件本身是原子替换。独立审核发现 guard 导入的 `run.py` 未被旧清单自动枚举；P2-6 保留原冻结记录并补充事后比对，新版本直接绑定依赖，[44 项独立检查](independent/recorder-dependency/review.json) 验证拒绝依赖漂移。非 UTF-8 探针输出用 base64 无损保存，copy-manifest 同时列原字节和存储字节 SHA。
- P2-9 首版 `743f79d` 会在时钟回拨后误缩短合法 72 小时期限，不能接受。原失败和 24 场景矩阵保留；修正 `a30231a` 增加合法期限证明与保守旧值处理。可从真实 590 用[首版补丁](author/retry_after/first-attempt-product.patch)及[修正补丁](author/retry_after/clock-correction-product.patch)重建，不依赖未发布作者提交；[重建核对](author/retry_after/product-input-reconstruction.json)确认所有非 validation Git blobs 一致。首版补丁仅作为反例材料。
- 作者首次 LSan 在受 ptrace 限制的环境退出，原日志保留；相同字节随后在允许的原生环境、开启泄漏检测后通过。独立 P2-5 的初版站点夹具与 P2-6 的 SQLite 清理夹具错误也保留，修正仅针对探针，没有放宽产品政策。
- 上一批 reverse-percent URI 身份边界的两个反例仍是未解决记录，本批未宣称修复，也未把旧六例说成全部通过。

## 保留决定与交接

来源选择、审核及启用、S2 范围/状态及是否冻结、validation 入库、备份恢复、永久幂等身份保留、抓取适配器均保持原决定。配置仍是 17 个登记来源、0 个启用来源、0 个站点；没有真实来源探测或自动启用。schema 13、索引格式 `bzs-ascii-cjk/2+codepoint-grams/1`、版本 0.1.10 保持，不新建 tag 或 release。

83 个工作包仍为 37 partial、46 not_started、0 accepted；161 个验收映射、10 个切片不变。2051 个旧 validation 文件完整保持；新增材料只在本目录。蓝图签收、P2-8 恢复流程、旧 URI 反例和其余未开发范围不因这些测试通过而算完成。

详细机器回执见 [final-report.json](final-report.json)，分项本地/GitHub 提交和相同 tree 的映射见 [commit-map.json](commit-map.json)。发布使用预期 main 的非强推更新；审计分支另行核对保持原 SHA。

远端 CI 以**最终发布提交**所绑定的 [GitHub Actions checks](https://github.com/wmqfl861/bzs861/actions/workflows/ci.yml) 为记录，交付消息给出准确 SHA 和 run。这里的 [基线 run 40067206826](https://github.com/wmqfl861/bzs861/actions/runs/40067206826) 及 [原 API 记录](baseline-ci/run.json) 仅证明 590 的历史状态，不替代本轮成绩。本回执不制造包含自身最终 SHA/CI 结果的自引用提交。
