# 0.1.4 有界正文抓取与发布规则验证

本批从已交付 0.1.3 `8a4e0f4cc630627ffa91c49598c66fec7fb4291d` 接续。完整报告见 [test_report.json](test_report.json)，来源树的 SHA-256 清单见 [source_manifest.json](source_manifest.json)，局部交叉复核见 [independent_review.json](independent_review.json)。旧版验证目录保持原样。

## 最终本地结果

| 套件 | 实际结果 |
|---|---|
| CTest | 10/10 通过，0 失败、0 跳过 |
| 原生 core / batch / map / crawl | 81 / 18 / 24 / 35 组；分别 713 / 439 / 462 / 1241 断言 |
| 原生 contracts | 55 组、269 断言，含 34 个原设计 fixture |
| HTTP / CLI | 20 项通过 |
| web / map / crawl E2E | 13 / 9 / 20 项；真实核心、Worker、CLI、stdio MCP 与合成回环站点 |
| Python Worker / MCP / 配置 / 探测回放 | 687 项通过 |
| ASan / UBSan / 泄漏 | core、batch、map、crawl、contracts 五个原生可执行文件通过 |
| 设计合同参考 / 规划回归 | 34 / 13 项；结构保留 83 包、161 映射、10 切片 |
| Linux 启动器 | 7 个真实命令、18 项检查；两端相同空站点文件和版本化发布能力，结束后进程/端口关闭 |
| Python 包与依赖 | 0.1.4 本地安装、pip check 通过，无依赖下载 |

34 个工作包部分实现、49 个未开始、0 个完整签收；S0/S1/S2 未签署完成。上述套件有交叠，不能相加成为产品完成度或原 161 项产品验收结果。

## 命令、源码与中间记录

`ctest-final.log` 是最终普通测试。每个日志配有 `*-command.json`，保存 argv、工作目录、UTC 开始时间、退出码及耗时。五套 sanitizer 独立保存检测开关，不能据此声称 Python/HTTP 也被 sanitizer 检测。`launcher_smoke.py` 与 JSON 保存隔离 Linux 生命周期，只停止本次临时配置中的进程，不记录凭据。

`e2e-crawl-initial.log` 是早期 17 项通过记录。`e2e-crawl-review.log` 保存后续 20 项运行中的一个测试辅助函数失败：该 helper 错将批量读取末页的整数 next_offset 当成还有下一页。它已改为遵守 has_more，原批量接口行为保留；最终 CTest 重新运行全部测试。后续任何中间执行均在报告单独列出，不能代替最终门槛。

`run_validation.py` 保存本批记录器。`execution_inputs.json` 在最终构建前冻结源码、测试、配置与构建输入，每次最终命令执行前后重新比对；相邻命令记录绑定日志和实际 Debug/ASan 二进制 SHA-256。原始输入冻结清单明确列出另行完成的产品文档，代码变更会使门槛失效。CTest 完成后曾出现文件系统可见日志尾部回到较早进度的问题；使用记录器已经返回的缺失尾部恢复后，完整文件 SHA-256 与原命令记录逐字节匹配。过程见 `ctest-log-restoration.json`，没有改写命令记录或推断测试成绩。`environment.json` 保存实际编译工具、Python 与锁定依赖版本。source_manifest 包含 validation 目录之外的仓库源码/文档；本报告另保存证据哈希。两者由包含报告的 Git 提交绑定，避免自身 SHA 循环。原规格/v1.1/backlog、黄金输入/manifest、默认来源/站点及依赖锁与输入提交逐字节相同；v1.2 切片只更新当前执行版本/报告路径，不变更验收文字、映射、依赖或估算。

## 范围与限制

正文测试覆盖递归/边/循环、物理请求计数、预算、robots/重定向、map/crawl 轮转、暂停后重启、owner/删除竞态、meta/响应头发布规则、失败后的撤索引、重定向逻辑与实际表示分离、旧会话的不可变身份，以及实际 CLI/MCP。原生测试额外覆盖逻辑配额、迁移、无效回执、并发预留、旧快照复用和冻结结果后续重新允许等状态组合。

本批没有真实搜索来源、真实站点、模型或付费调用，默认站点为空、17 条来源禁用。日历 fixture 只证明通用深度上限，暂停后进程重启不等于任意硬崩溃/断电演练。旧 schema5 已经覆盖且不再保存的重定向身份不能由升级恢复；新版本将表示身份固定在每条冻结搜索行；复用快照的抓取元数据仍可更新，而正文和发布策略保持不变。普通读取保留的截断预览不获索引/摘要/跟随许可，批量与爬取的字节预算失败仍如实失败。

完整爬虫/发布验收、专门 trap 策略、长期容量与 GC、真实来源 S0、人工 qrels/评分、SESSION-02、独立撤销日志/安全恢复、系统解析沙箱和完整 Windows Worker 打包仍待后续。历史0.1.2的两次 DNS 失败、0 HTTP 样本保持在原目录，不算本批可用性证据。

发布后必须核对最终提交自己的 Linux/Windows Actions。Linux 门槛为十套普通测试、五套原生 sanitizer、原设计合同和规划检查；Windows 为 core/batch/map/crawl/contracts/HTTP 六套。该报告生成于发布前，不预写未来自身 SHA 或未来 CI 成功。
