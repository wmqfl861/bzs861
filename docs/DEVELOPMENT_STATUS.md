# 开发进度：0.1.4 S2 crawl development candidate

更新：2026-10-18。本批从已交付 0.1.3 的 `8a4e0f4cc630627ffa91c49598c66fec7fb4291d` 接续，推进 **CRAWL-01 的有界正文前沿与 CRAWL-03 的索引、摘要发布规则**。保留机器人准入、站点发现、黄金输入、探测回放和显式批量读取。输入基线不是本批发布 HEAD；本批源码、命令、结果和限制统一见 [当前报告](../validation/s2_crawl/test_report.json)。

## 状态与规划

v1.2 的 C++ 核心、Python Worker、stdio MCP 优先与单用户先行安排继续生效。**83 个工作包、161 项验收 ID 全部保留；34 项部分实现、49 项未开始、0 项完整签收。S0、S1、S2 均未签署完成。** 本批只把 CRAWL-01 / CRAWL-03 从未开始推进为部分实现；测试数量不折算为工作包完成度。

[蓝图](planning/development_blueprint_v1.2.md)、[切片计划](planning/slices_v1.2.json)、[规划说明](planning/README.md)、[机器状态](implementation_status.json) 与交接入口同步到 0.1.4。原 ID、验收文字、依赖、估算及未签收属性保留；418–720 人日仍是待重估的历史规划数，不能用来承诺剩余时间。

## 本批实际行为

| 范围 | 本批实现 | 验收边界 |
|---|---|---|
| CRAWL-01 | 在持久单跳通道上增加静态正文 frontier、独立页预算、边关系、深度/查询变体/循环约束；map/crawl 共用持久轮转与同源配额 | 通用容量保护与受控深度限制已实现；专门日历 trap 分类、长时运行、断电与全包验收未完成 |
| CRAWL-03 | BzsBot 适用的 robots meta、X-Robots-Tag、noindex/nosnippet/nofollow、data-nosnippet；独立安全摘要和标题；刷新时撤索引并约束旧搜索分页 | 版本化的本项目策略；不宣称兼容所有搜索引擎扩展、全量历史扫描或法律审核自动化 |
| STORE-01 / JOB-01 | schema 6 保存正文状态、页计费、边与发布规则；正文和策略原子落库；限制性响应在失败路径也可撤下旧结果 | 不能把逻辑存储配额回退当作物理磁盘失败仍能提交；跨节点、完整 GC 与恢复验收独立保留 |
| SEC-01 / AUTH-01 | 保留 robots、精确站点文件身份、每跳出站策略、owner/租约/删除代际；逻辑 URL、实际响应 URL 与旧快照表示分别受发布约束 | 默认站点为空；系统级解析沙箱、独立撤销日志与安全恢复仍未完成 |
| API-00 / CONTRACT-01 | HTTP/CLI `crawl.submit` / `crawl.edges`；MCP `crawl_site` / `crawl_edges`，复用任务操作和正文回执；新工具校验原始 JSON 形状 | 完整 v1、远程多客户端 MCP、流式事件与断线重取仍单独验收 |
| QA-01 / CORE-01 | 新增原生 crawl 与实际进程 E2E；Windows 增加 crawl 阻塞门槛，原生 sanitizer 扩至五个可执行文件 | 合成站点不能产生真实来源可用性或人工相关性结论；Windows 完整 Worker 打包属于 S9 |

`map.submit` 只产出未读候选，`read_many.submit` 读取显式 URL 列表，`crawl.submit` 才递归读取有界静态正文。三者保留不同授权和结果合同。站点文件默认为空，17 条搜索来源默认禁用。参数、上限、页计费、边分页及发布规则见 [SITE_CRAWLING](SITE_CRAWLING.md) 和 [API](API.md)。

## 复核与修复

本批交叉复核发现并补充了跨入口、历史状态和协议边界：MCP 的预算类型/未知字段及字符串 `null` 可能被 SDK 改写；普通读取可能抹掉站点 `links_only` 上限；有效限制在空正文、解析失败或逻辑配额失败时可能未撤下旧索引；重定向后成功正文可能覆盖前一跳限制；逻辑 URL 改变重定向目标后，旧搜索分页可能失去原正文的发布身份。原生核心和真实进程测试分别覆盖这些路径。

另有单跳响应状态、隐藏跳转、同一回执内矛盾策略和链接边去重等复核修复。具体问题、作者和复核范围见 [independent_review.json](../validation/s2_crawl/independent_review.json)。这是分开的助手上下文进行的局部代码复核，不是外部人工或全产品独立签收。

## 验证范围

实际执行记录保存在 [validation/s2_crawl](../validation/s2_crawl/README.md)：十套普通 CTest、五个原生 ASan/UBSan/泄漏检查可执行文件、设计合同参考、规划防回退校验、依赖检查及 Linux 启动器生命周期。最终数字以 [test_report.json](../validation/s2_crawl/test_report.json) 为准；中间通过记录与最终执行分开，不混入原 161 项产品验收。

新真实进程链路使用明确映射到回环的合成 `.test` 站点，检查递归正文与边、物理请求计数、robots 拒绝、预算、暂停后的进程重启、同源轮转、删除竞态、owner 隔离、CLI/stdio MCP、发布刷新和旧搜索结果。日历 fixture 只证明通用深度上限；暂停后重启不等于任意时刻硬崩溃或断电测试。

Linux/Windows CI 必须检查最终发布提交对应的 [Actions](https://github.com/wmqfl861/bzs861/actions)。Windows 门槛为 core/batch/map/crawl/contracts 与 HTTP/CLI，完整 Python Worker 的 Windows 安装包仍属于 S9。

## 数据与后续工作

schema 6 在 schema 5 上增加正文前沿、页/边和发布身份；schema 1、两种 schema 2、schema 3/4/5 的兼容路径继续保留。隔离状态先于迁移检查；原快照、证据、索引、任务和删除语义保留。已交付到外部的内容不能收回，未实现独立撤销连续性的历史备份不得恢复为可服务状态。

0.1.3 [发现报告](../validation/s2_discovery/test_report.json) 和 0.1.2 [黄金/探测报告](../validation/s0_s2_progress/test_report.json) 保留原样。历史云端记录只有两次 DNS 失败、0 HTTP 响应；本批没有新增真实来源、真实站点或模型调用。S0 目标环境实测与独立审核、S1 全部标准、QA-02 人工 qrels/评分/阈值/留出评测、SESSION-02 断线重取与独立撤销恢复仍未完成。

下一批按 [NEXT_AGENT](handoff/NEXT_AGENT.md) 接续。保留全部模型、文档、多媒体、私人资料、订阅和 Windows 交付范围，不用当前切片缩减原蓝图。
