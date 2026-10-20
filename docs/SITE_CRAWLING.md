# 有界正文爬取与页面发布规则

本页描述 **0.1.5** 的 `crawl.submit` 与 `crawl.edges`，对应蓝图 v1.2 的 **CRAWL-01 / CRAWL-03 部分实现**。它保留 0.1.4 的正文前沿、页预算、边与页面声明，在同一核心增加持久抓取陷阱防护和站点存量发布上限。完整工作包与 S2 尚未签收，当前证据见 [验证说明](../validation/s2_hardening/README.md)。

## 三种任务如何选择

| 操作 | 输入与扩张 | 结果 |
|---|---|---|
| `read_many.submit` | 1–20 个明确 URL；没有链接扩张 | 每个输入的读取状态与快照收据 |
| `map.submit` | 明确发现文档；Sitemap index、robots 声明可补充发现文档 | Sitemap、Feed、HTML、owner 历史候选，`body_read=false` |
| `crawl.submit` | 明确入口；在已审核同源路径内递归加入允许的页面链接 | 持久正文前沿、页面状态与快照、链接边、预算及停止原因 |

自动爬取不会把失败页面转到普通读取接口重试。所有自动内容请求都经过有效站点审核和 robots 准入，生产连接仍必须通过公网目标检查。`sites.json` 默认空，17 个搜索来源默认禁用；示例中的站点和 URL 只是占位值。站点配置、精确清单哈希、URI 比较和 robots 缓存合同沿用 [SITE_DISCOVERY](SITE_DISCOVERY.md)。

## 提交与预算

先调用 `map.sites` 查看部署实际加载的站点，再提交：

```json
{"operation":"crawl.submit","params":{"site_id":"operator-reviewed-site","seeds":["https://reviewed.example/docs/"],"scope_path":"/docs/","idempotency_key":"docs_crawl_1","max_pages":30,"max_urls":100,"max_depth":3,"max_requests":80,"max_bytes":16777216}}
```

| 参数 | 默认值 | 核心合同 |
|---|---|---|
| `site_id`、`seeds`、`idempotency_key` | 必填 | 站点 ID、1–20 个明确入口、同一 owner 的幂等键 |
| `scope_path` | 已审核路径 | 只能缩小登记范围；入口、入队、每个重定向和派发均复核 |
| `max_pages` | `min(100,max_urls)` | 1–1000，且不超过 `max_urls`；限制首次派发的逻辑正文资源 |
| `max_urls` | 1000 | 1–1000；发现资源和候选共用去重 URL 集合 |
| `max_depth` | 3 | 0–8；入口为 0，每次发现增加 1，与 URL 路径层数不同 |
| `max_requests` | 100 | 1–1000；包含 robots、重定向、失败的物理 HTTP 请求尝试 |
| `max_bytes` | 16777216 | 1–67108864；所有请求读取的解压后响应体字节 |
| `timeout_ms` | 600000 | 1–3600000；创建时固定总期限，暂停不延长 |
| `per_request_timeout_ms` | 20000 | 1–55000；每个物理请求受剩余总期限进一步约束 |

同一 owner、相同种类和完整规范化参数复用幂等键，返回原任务；修改参数或在 map/crawl 间复用键会返回冲突。普通 API 不接受审核结论、时钟、robots 记录、配额或重试次数覆盖。

页预算在首次派发正文资源前与请求、字节预算一同预留。一次逻辑页的 HTTP 重定向不增加第二页，仍逐跳消耗请求/字节。robots 和单独发现的 Sitemap index 元数据不计正文页；显式作为正文入口的 XML/Feed 会占页预算。可信回执证明该资源尚未发出任何 HTTP 时可以释放未使用的页预留；失败或未知的已派发页仍占预算。`used_pages` 不是成功页面数量，成功数另报 `succeeded_pages`。

请求/字节采用“已知用量加未知保守预留”的累计记账。未知结果不自动重发，也不能退成零用量。`usage.scope=job_lifetime` 表示整个任务累计数，轮询时不能重复相加。当前单跳正文最多 2 MiB，robots 最多 512 KiB；可用额度还受任务剩余预算限制。

## 正文前沿、边与范围

HTML/XHTML 的同一次响应既提供正文，也提供链接发现。支持静态 HTML、明确文本类型以及已完整验证的 Sitemap/Feed XML；XML 作为正文入口时保留文本表示，后续发现文档按类型处理。XML 禁止 DTD、实体展开和外部资源访问。不执行 JavaScript，不引入浏览器、登录态或 PDF 解析。

前沿按规范化 URI 去重，保留既有 path/query 编码与查询顺序；fragment 不形成新页面。每个 origin/path 最多保留 4 个查询变体。深度、URL 数、页数、请求、字节和总期限共同约束循环与可无限增长的链接结构。此外，0.1.5 在任务中固定 `bzs-crawl-trap-1`，以持久观测识别下表的有限模式；它不声称识别所有网站陷阱。

### 专门抓取陷阱规则

| 模式 | 当前停止条件 |
|---|---|
| 同模板的有效日期（`YYYY-MM-DD` / `YYYY_MM_DD` / `YYYY/MM/DD`） | 最多 16 个不同日期；同一模板 parent→child 连续前进或后退一天最多 6 步 |
| 日历上下文中的月份 | `calendar/archive/events/agenda` 等明确路径上下文可识别有效年月，按同样的日期族和相邻步数限制 |
| 查询维度 | 自动发现 URL 最多 8 个非分页键、16 个查询参数对 |
| 等价参数排列 | 相同规范化比较键的第 3 个不同 URI 被拒绝；不同键排序仅用于比较，同名键的值顺序仍保留 |
| 多过滤参数组合 | 同一稳定路径模板最多 8 个不同的多过滤参数组合，`/page/N` 在分类时属于同一路径族 |
| 重复分页正文 | 明确 `/page/N` 或 `page/offset/start/cursor` 查询族中，3 个不同实际 URL 的完整 canonical 正文完全相同且至少 256 字节，停止该族后续自动扩张 |

这些分类键不替换 HTTP URL、robots 路径、scope 检查、前沿去重或文档身份。只对比较键解码 unreserved 字节并规范百分号大小写，保留实际路径和查询顺序。文章 slug、普通数字 ID 和版本路径不会仅因包含数字就被合并；启发式仍可能限制合法但高度规则化的导航，停止原因供调用者判断覆盖边界。

明确提交的入口 URL 可读取并计入观测，继续受原 scope/robots/预算约束；由入口产生的自动重定向目标需要独立检查，除非目标本身也明确列在入口中。重复正文证据达到阈值后，派发前还会检查已经入队的非入口页面，不让先前排队绕过新证据。达到重复证据阈值本身不等于部分覆盖，确实拒绝扩张时才记录 `partial`。

每个任务保留策略版本、阈值和至多 2000 个 URL 观测，实例至多 70000 个；重启保留这些计数和拒绝理由。升级的旧任务从仍保留的前沿、HTTP 跳及完整正文做一次有界补齐，旧已入队的日期/query准入保留并计入观测，派发前重查的是后来形成的重复正文证据。无法完整补齐或补齐需要超过容量时明确报告原因，不静默重置成空历史；不一致/损坏的超界历史会终结剩余pending，不反复阻塞其他任务。正常0.1.4的URL/请求各1000上限本已约束到2000观测，超界回归属于合成容错验证。该记录不是通用长期 GC。

`job.get.trap_policy` 返回版本、阈值、观测计数和按边统计的停止原因；受 trap 拒绝的 `crawl.edges` 项附带 `trap` 对象：`version/rule/reason/family_sha256/observed/limit`。具体错误为 `E_CRAWL_TRAP_DATE_SEQUENCE`、`E_CRAWL_TRAP_DATE_FAMILY`、`E_CRAWL_TRAP_QUERY_DIMENSIONS`、`E_CRAWL_TRAP_QUERY_PERMUTATIONS`、`E_CRAWL_TRAP_QUERY_COMBINATIONS`、`E_CRAWL_TRAP_DUPLICATE_PAGINATION`；容量、历史不足或不支持的策略版本分别明确报告。

新任务创建时固定策略；旧 schema 6 任务首次恢复调度前的 `trap_policy` 可为 `null`，单纯幂等重提不会补建。已结束且不再调度的旧任务可以保持 `null`，不能因此推断它已经通过本版陷阱规则。

map 和 crawl 共用持久化的调度次序，已服务任务排到仍可执行任务之后；同源通道、最小间隔、24 小时窗口配额与冷却继续跨任务生效。这里实现的是单实例 map/crawl 任务轮转，不是跨节点或所有任务类型的公平性保证。

```json
{"operation":"job.get","params":{"job_id":"返回的任务ID"}}
{"operation":"job.results","params":{"job_id":"返回的任务ID","offset":0,"limit":20}}
{"operation":"crawl.edges","params":{"job_id":"返回的任务ID","offset":0,"limit":20}}
```

`job.results` 返回 `type=page` 的前沿条目，包括入口 URL、最终 URL、parent/source/depth、状态、错误、可用性、正文读取标记、快照/文档 ID 和页面发布决定。成功的收据不内嵌完整正文；用 `document.range` 或 MCP `read_range` 读取 owner 有权访问的快照。删除后不再提供可用旧收据。

`crawl.edges` 返回 parent、目标 URL、source、depth、`followed` 与 reason。`followed` 表示链接被允许纳入前沿，不等于页面成功读取；成功与失败以页面条目为准。边按 parent/目标/source 去重，同一页面可以保留多个来源。每项任务最多 10000 条边，实例共 50000 条；容量耗尽会限制扩张并保留 partial 原因。被解析器丢弃的恶意链接、`rel=nofollow` 或整页 nofollow 不保证形成可枚举的边，实际不会因此派发请求。

两种分页每页最多 1000 项，以返回的 `next_offset` 继续。删除可使位置不连续。任务 `succeeded` 只表示当前有界前沿无失败地结束；同时检查 `partial`、`stop_reason`、过滤数和剩余预算，不代表全站覆盖。

## 页面索引与摘要策略

页面发布策略固定为 **`bzs-publication-1`**，与 robots 的请求许可分开。采用适用于本产品的明确规则：通用 `robots` 元标签、未限定 agent 的 `X-Robots-Tag`，以及 `BzsBot` / 兼容别名 `bzs861` 生效；其他爬虫的专属声明不控制本产品。

| 声明 | 当前行为 |
|---|---|
| `noindex` | 当前内容不能进入默认检索；刷新后同步撤除相关索引及旧冻结会话中的结果 |
| `nosnippet` | 允许保留受审正文与索引，搜索不发布正文摘要和命中高亮 |
| `nofollow` | 本次响应的链接不扩张前沿 |
| `none` | 等价于 `noindex,nofollow` |
| `data-nosnippet` | 从独立的摘要 DOM 移除标记元素，重新提取摘要文本和安全标题；owner 的 canonical 正文保持完整 |
| `max-snippet:0` | 本产品保守按 `nosnippet` 处理；其他参数化搜索引擎扩展尚未实现 |
| 站点审核 `links_only` | 禁止正文摘要与由页面内容提取的标题发布；普通网页/批量读取刷新也不能扩大该限制 |

重复响应头保留每个字段的 agent 范围；多个适用声明取更严格的结果，后来的 `index/follow/all` 不能清除已有禁止。通用语法参考 [Google 的页面声明文档](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag)，这里定义的是 BzsBot 自己的版本化策略，不宣称完整复制 Google 的规则。`data-nosnippet` 对本产品的所有元素生效；`unavailable_after`、非零 `max-snippet`、图像/视频预览等扩展不在本批实现范围内。

摘要文本有独立存储，不能从 canonical 正文回填被删除的片段。受限制摘要不携带伪造的原文命中字节偏移；需要精确证据时，owner 显式读取原快照再调用原有 `find` / `evidence.create`。刷新限制也约束旧搜索会话，避免分页继续发布先前的正文或标题；后来重新允许不复活已撤下的旧会话内容。

重定向链分别约束逻辑入口和实际页面，后续允许正文不能覆盖同一链已观察到的入口限制。冻结搜索结果绑定当时正文的最终 URL；入口以后指向另一页面，旧结果仍遵守原页面后续的限制。新页面的独立允许策略不因此失效。

普通 `web.read` 既有下载上限可能留下带 `download_truncated` 的可读预览；这种不完整响应保守关闭索引、摘要和跟随许可，不能凭已收到的允许前缀恢复旧发布。批量读取和 crawl 仍按各自字节预算报告失败。即使后续重定向目标被拒绝，先前实际响应中有效的限制也不会被丢弃。

站点规则还会持久约束已存快照、索引和冻结会话，即使离线重启也生效；激活、省略/空清单、收紧后放宽、完整链与升级影响详见 [SITE_PUBLICATION](SITE_PUBLICATION.md)。站点 `links_only` 采用保守的整条读取链上限。若原入口的审核已使 Worker 清空目标页摘要，本批不会反推被清除的摘要；直接读取目标 URL 时再按它自身适用的审核和页面规则判断。

只有已有 owner 隔离的索引和结果接口开放，本批没有创建跨 owner 公共索引或网页发布服务。robots 允许不表示可以索引，索引允许也不表示拥有缓存和再发布审核资格。已经交付到外部的结果无法撤回。

## 持久化、恢复与迁移

schema 7 在原正文前沿、边、页记账与页面发布状态上，增加持久站点 scope、水位、完整 URL 绑定、冻结行版本和 trap 观察。继续支持 schema 1、两种历史 schema 2、3、4、5、6，保留 owner 原快照、证据、任务和删除代数；隔离状态仍先拒绝可服务启动。

旧 schema 的部分抓取快照与冻结页缺少可靠历史链，首次站点激活可能保守撤发布，需要新查询或完整重读；完整正文和证据保留。迁移不会补造已经被覆盖的历史，具体边界见 [升级说明](SITE_PUBLICATION.md#schema-7-升级影响)。

`job.control` 支持 `pause/resume/cancel`；crawl 不接受 `retry_failed`。暂停或取消改变租约代数，旧响应无法提交正文、索引、边或收据；恢复继续尚未派发的前沿。已发出的请求无法撤回，结果未知时仍保留保守记账。权限、逻辑入口、当前重定向目标及旧 URL 别名的删除身份都参与发布检查；显式重新导入不能让旧任务越过删除代数。

当前全局保留上限继续包含 512 个 map/crawl 任务、20000 个发现资源、20000 个候选、50000 个 HTTP 跳和 512 个 robots 记录，另有边、trap 观察和站点历史 scope 的独立上限。没有通用自动审计 GC，不能手工删除账本来绕过容量。

## MCP 与验证边界

新增 `crawl_site` 和 `crawl_edges`，复用 `get_job/job_results/control_job/read_range/search_local`；CLI 使用 `searchctl call request.json` 或 `bzs call` 调同一核心。新 crawl 工具在 SDK 转换参数之前拒绝未知字段和错误 JSON 类型，不能把 `true`、`"2"` 或显式 `null` 当作数值预算；省略可选字段才使用默认值。`scope_path` 必须以 `/` 开头，字符串 `"null"` 不能被转换成省略范围。审核、实际范围与数值上限继续由核心校验。

实际受控站点测试包括递归正文与循环、页/请求/字节/深度/变体限制、每跳 robots 与重定向、暂停后重启、同源轮转、noindex 与摘要刷新、旧会话撤发布、删除别名、owner 隔离以及真正 HTTP/CLI/MCP 调用。新增实际进程用例验证专门日期/查询/重复分页规则及有限文章负例；原生测试另验证存储和租约状态机；具体运行、失败修复与精确源码见 [本批报告](../validation/s2_hardening/test_report.json)。

0.1.5 另以真正 `SIGKILL` 覆盖派发前、Worker 完整响应丢失、待派发目标删除，以及任务已提交但 accepted 回执丢失。测试等待实际租约到期，不靠数据库写入或暂停/恢复制造崩溃；核对未知预留、零重发、保留快照/证据和幂等重取。它只覆盖这些实际边界，不等于每条 SQLite 指令、物理断电或长期故障矩阵。合成站点不提供真实来源可用性或人工相关性成绩。真实 S0、QA-02、SESSION-02、系统级解析沙箱、独立撤销日志与安全恢复、跨节点调度和长期压力仍按原蓝图继续验收。
