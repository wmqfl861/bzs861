# 开发进度：0.1.3 S2 discovery development candidate

更新：2026-10-18。本批从已交付 0.1.2 的 `434a9dfa8ce26b86bf765c9d863c8607c3dabae0` 接续，推进 **ROBOTS-01 的实际请求准入与 MAP-01 的持久有界发现**。保留 0.1.2 的黄金查询、探测/回放及静态批量读取。输入基线不是后续发布 HEAD；本批源码、命令、测试和限制统一见 [本批报告](../validation/s2_discovery/test_report.json)。

## 状态口径与规划

v1.2 的 C++ 核心、Python Worker、stdio MCP 优先与单用户先行安排继续生效。**83 个工作包、161 项验收 ID 全部保留；32 项部分实现、51 项未开始、0 项完整签收。S0、S1、S2 均未签署完成。** 本批只把 ROBOTS-01 / MAP-01 从未开始推进为部分实现，测试数量不折算为工作包完成度。

[蓝图](planning/development_blueprint_v1.2.md)、[切片计划](planning/slices_v1.2.json)、[规划说明](planning/README.md)、[机器可读状态](implementation_status.json) 与当前交接入口已同步到 0.1.3。原依赖、估算来源、验收原文和未签收属性保留；418–720 人日仍是待重估的历史规划数，不能用来承诺剩余时间。

## 本批实现

| 范围 | 本批增加的实际行为 | 保留的验收边界 |
|---|---|---|
| ROBOTS-01 | 固定 BzsBot 策略；有效 robots 前置、持久缓存、每跳准入、过期/不可达/限流拒绝；Worker 在真正发送前复核 | 本项目版本化策略，包含明确的保守限制；未来自动正文抓取入口必须继续接入，没有完整搜索引擎扩展兼容或 304 更新实现 |
| MAP-01 | Sitemap/index、RSS 2/1、Atom、HTML/XHTML、owner 历史候选；保存 parent/source/depth/path_depth；只有 index/robots 声明可递归发现文档 | 候选标记 `body_read=false`，不会生成本次正文快照或索引；不宣称全站覆盖，`.gz` 归档不自动展开 |
| JOB-01 / STORE-01 | schema 5 持久队列、逐跳租约、先预留再请求、未知用量保守计费、同源配额与冷却；恢复不重发已完成/未知跳 | 跨节点、公平调度、断电与完整恢复验收未完成；HTTP 不承诺 exactly-once |
| SEC-01 / AUTH-01 | 独立空站点登记、精确文件哈希、审核有效期、固定 origin/path；逻辑与重定向目标删除屏障、历史原文档 ID/代际绑定 | 不新增真实批准站点；系统级解析沙箱、独立撤销日志和安全恢复仍待后续 |
| API-00 / OPS-00 | `map.sites` / `map.submit`；复用 `job.get/results/control`；MCP `list_sites` / `map_site`；启动器两端同文件并拒绝额外参数覆盖 | 完整 v1 执行合同、远程 MCP、流式事件与断线重取仍独立验收 |
| QA-01 / CORE-01 | 受控站点的真实进程 HTTP/CLI/MCP、请求计数、重启与缓存场景；新增原生 map 套件及对应 Windows 和 sanitizer 门槛 | 合成站点不产生 S0 真实来源结论或 QA-02 人工相关性成绩 |

`map.submit` 与 `read_many.submit` 分别表示有界发现和显式 URL 正文读取。map 的 HTML/feed/urlset 链接只作为候选，不自行转入读取。部署 `sites.json` 默认为空，与 17 条默认禁用的搜索来源清单分别审核。接口、硬上限、robots 缓存及 URI 策略见 [SITE_DISCOVERY](SITE_DISCOVERY.md) 和 [API](API.md)。

## 复核中修复的问题

本批实现作者与跨模块复核分工记录在 [independent_review.json](../validation/s2_discovery/independent_review.json)。修复包括：客户端重写百分号编码使 robots 判定目标与实际请求不同；坏链接导致整份有效发现文档失败；重定向当前目标遗漏删除检查；历史 URL 规范化丢失原文档删除关联；缓存忽略响应年龄或显式过期；跨进程回执字段不一致丢失明确拒绝原因；启动器额外参数覆盖站点文件。

这些是有明确范围的代码交叉复核和回归，尚不是外部人工或全产品独立安全签收。已知非阻塞诊断限制也单独保留：重复提交退休的内部 attempt 时，原错误可能被核心归类为协议错误；历史用量仍收费、不会发布候选，当前核心不自动执行这种重放。

## 当前验证

当前命令、退出码、源码清单和环境均保存在独立的 [validation/s2_discovery](../validation/s2_discovery/README.md)。最终 8 套 CTest、四套原生 ASan/UBSan/泄漏检查、设计合同参考、规划防回退校验、依赖检查和实际启动器生命周期分别报告，不与原 161 项产品验收混合计数。最终数值以 [test_report.json](../validation/s2_discovery/test_report.json) 为准。

本机假站点链路覆盖：发现文档格式、候选不读正文、Sitemap 循环去重、robots 拒绝/不可达/429、越界与私有地址重定向、持久缓存和幂等任务重启、历史删除及别名删除、XML 外部实体拒绝、真正的 CLI/MCP 调用、两个进程审核文件不一致时零外部请求。Worker 另以真实回环响应检验编码原样发送、已过期缓存导致零内容请求、单跳限额、取消和退役请求用量。

Linux/Windows CI 必须检查最终提交对应的 [Actions](https://github.com/wmqfl861/bzs861/actions)。Windows 当前保留并扩充的是原生 core/batch/map/contracts 与 HTTP/CLI 阻塞门槛；完整 Python Worker 的 Windows 安装包仍属于 S9。

## 数据迁移与权限

schema 5 在 0.1.2 schema 4 上增加 map 任务、资源、候选、HTTP 跳账本、robots 缓存与同源请求状态。schema 1、两种不同来源的 schema 2、schema 3 和 schema 4 的兼容路径继续保留。隔离状态先于新表迁移检查；快照、证据、索引、删除和原任务不因新队列丢失。禁止用原 Claude S1 二进制盲开当前数据库。

权限、租约和删除屏障覆盖每次候选发布，历史 URL 的大小写、默认端口或 fragment 规范化不能切断原文档删除身份。已交付到外部的结果无法收回。更新解析器不自动清洗旧内容；没有最新撤销连续性的历史备份仍不得恢复为可服务状态。

## 历史证据与未完成项

0.1.2 的 [报告](../validation/s0_s2_progress/test_report.json)、40 条冻结黄金输入、probe schema 2 和离线 replay 保留不变。其云端记录只有 Marginalia 与 Wikipedia 英文各 1 次 DNS 失败，0 HTTP 响应；本批没有重复真实来源探测，也没有真实站点发现请求。该观察不能判断用户部署环境可用性或批准来源。0.1.1 的 [接收审查](handoff/CLAUDE_S1_REVIEW_20261018.md) 与 [报告](../validation/s1_review/test_report.json) 继续作为历史。

S0 仍需目标部署环境的真实证据和独立来源审核；S1 至少两个可用来源、相关性及全部标准仍待逐项验收。CRAWL-01 的正文 frontier、公平调度与 trap 处理，CRAWL-03 的 noindex/nosnippet/data-nosnippet/X-Robots-Tag、声明刷新及公开索引移除，QA-02 的人工 qrels/评分/冻结阈值/留出评测，SESSION-02 的断线重取和 BACKUP-00 的独立撤销/隔离恢复都不能由本批发现通道代替。

下一批按 [NEXT_AGENT](handoff/NEXT_AGENT.md) 接续，保留全部模型、文档、多媒体、私人资料、订阅和 Windows 交付范围。
