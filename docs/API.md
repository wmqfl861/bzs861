# searchd API（0.1.4 S2 crawl development candidate）

所有业务请求为 `POST /api/v0/call`，通过回环 HTTP 与 Bearer token 调用。Host/Origin、请求大小和超时检查继续保留。MCP、Python 启动器与 C++ CLI 复用同一核心；候选路径的真实验证结果见 [本轮报告](../validation/s2_crawl/test_report.json)。

```json
{"operation":"web.search","params":{"query":"断点续爬 实现","limit":10}}
```

响应信封包含 `request_id`、`api_version`、`status`、`result`、`errors` 和 `usage`。本版存在外部网络路径，不能沿用 0.0.2 的 external_requests 恒为零假设；实际次数、未知状态和错误按执行结果报告。模型执行仍未实现。传输层早期错误可能使用精简错误信封。

## 保留的原生合同

`contract.validate` 接收 `kind` 和 `config`；kind 为 `model-config`、`source-manifest` 或 `search-request`。响应结果为：

```json
{"kind":"model-config","validated":true,"applied":false,"effective_config":{}}
```

上面的 effective_config 只是信封形状，真实结果包含该合同的规范化字段。时钟与 `ContractLimits` 由服务端提供，不接受调用方的 `now_ms`、`validation_now_ms` 或 `limits`。配置对象上限 64 KiB；严格整数表示、角色继承、URI、日期/AND/eq 和 AST 规则继续保留，见 [CONFIGURATION](CONFIGURATION.md)。

校验不会保存、注册或启用来源，也不会调用模型；`applied=false` 与网络执行准入是不同层。C++ `searchctl validate <kind> <file>` 继续通过真实 HTTP 调用该操作。正式 search-request 通过校验不代表完整 v1 对象可以直接交给现有 v0 搜索执行。

## 网络操作

| 操作 | params | 边界 |
|---|---|---|
| `web.search` | `query`；可选 `sources`（来源 ID 数组）、`category`、`limit`、`per_source`、`language`、`timeout_ms` | 多源并发、状态、去重与索引族融合；只执行已审核且启用的来源 |
| `web.read` | `url`；可选 `freshness`、`max_age_s`、`max_bytes`、`start_byte`、`timeout_ms` | 公网读取和快照；不因来源结果或 MCP 调用自动放宽出站规则 |
| `web.sources` | `{}` | 运行来源清单、审核/启用、冷却和请求状态；列出不等于可执行 |

随包 17 条来源登记包含 13 个适配器及 4 个后续候选，默认全部禁用。正常运行的每个启用来源需要 `contract` 对象，遵循原 `source-manifest` 合同，包含 `approved` 审核状态、`no_payment`、自动访问批准和当前有效审核日期。

运行字段须与合同身份一致：`id`、`provider`（对应 `provider_id`）、`index_family`、能力、适配器、端点、启用和限额。运行类别 `encyclopedia` 对应合同 `web`，`papers` 对应 `scholar`；`params.adapter`、`params.base_url`、`min_interval_ms`、`daily_cap` 等不能借另一身份/端点或更宽配额执行。结构或身份错误在启动时返回 `E_SOURCE_CONFIG`；未批准或审核过期的执行返回 `E_SOURCE_REVIEW`（HTTP 409）。核心与 worker 都实施准入。

S0 候选探测属于显式工具路径：必须指定 `--include-candidates` 和非空 `--only` 来源 ID 列表；`--sources` 是清单文件路径，不是 ID 列表。它不修改正常准入或自动批准来源，详见 [S0_PROBE](S0_PROBE.md)。

### 搜索结果

`results[]` 包含排名、URL、标题、来源摘要、来源归属和索引族；摘要标记 `snippet_kind=search_engine_snippet`，不能作为已读正文。每个来源返回正常、空结果、跳过、冷却/配额、封禁、限流、解析失败或网络错误等状态，部分失败不能伪装成全量成功。

索引族独立性未知时保守控制贡献；当前 DuckDuckGo 与 Bing 共用索引族，不能因不同服务名重复计票。已支持的 site 条件在结果中复核；其余未支持条件不能静默丢弃。

联网结果尚未完整进入冻结会话或支持后续页，不能将本地分页能力当作联网分页。完整 SESSION-01/02 语义继续保留为后续验收。

### 读取结果与缓存

读取结果包括文档/快照 ID、来源与最终 URL、标题、版本、抓取时间、缓存状态、索引状态、文本窗口、元数据、抓取/解析状态和 `content_trust=untrusted_web_content`。文本窗口的起止是原文 UTF-8 字节边界。

`cache_first` 使用期限内缓存，`refresh` 重新获取，`cache_only` 不联网、无缓存则明确报错。noindex 内容可以在允许的读取路径查看但不进入本地索引。权限、删除和缓存新发布检查不能因命中缓存而跳过。

## 持久批量网页读取

`read_many.submit` 只处理调用方列出的 URL。它不从页面发现链接，不递归遍历网站。有界发现由 `map.submit` 提供，有界递归正文读取由 `crawl.submit` 单独提交；完整工作包仍须分别验收。

```json
{"operation":"read_many.submit","params":{"urls":["https://example.com/a","https://example.com/b"],"idempotency_key":"research_batch_1","max_requests":12,"max_bytes":16777216,"max_attempts":1}}
```

| 参数 | 默认值 | 允许范围和含义 |
|---|---|---|
| `urls` | 必填 | 1–20 个明确 URL；规范化后重复 URL 拒绝；保留协议、`www`、路径和查询差异 |
| `idempotency_key` | 必填 | 同一用户内唯一；相同参数重复提交返回原任务，改参数复用键返回 `E_IDEMPOTENCY_CONFLICT` |
| `timeout_ms` | 600000 | 1000–3600000；任务从创建开始的总期限，暂停不延长 |
| `per_page_timeout_ms` | 20000 | 1000–55000；每次读取尝试的时间上限，受剩余任务期限限制 |
| `max_requests` | URL 数量 × 18 | 1–1000；整项任务的 HTTP 请求尝试总上限 |
| `max_bytes` | URL 数量 × 8 MiB | 1–256 MiB；整项任务读取的解压后响应体字节总上限 |
| `max_attempts` | 1 | 1–5；每个输入 URL 最多尝试次数；大于 1 也不会自动重试失败项 |

请求预算包括每次已派发的 HTTP 尝试、HTTP 跳转和支持的软跳转；字节预算累计读取的响应体，包括跳转响应体。该字节数不是网络线上包含协议头和压缩数据的流量计数。核心在调用 worker 之前持久预留预算；收到可信完整用量后，仅释放未使用部分。超时、崩溃或丢失响应使结果未知时，整份预留继续计费，并在 `unknown_requests_charged` / `unknown_bytes_charged` 中体现。

提交立即返回 `job_id`、`kind=web.read_many`、状态、各状态条目数量、`budget` 和 `usage`。`budget.charged_*` 包含已知实际用量、未决预留和未知结果的保守计费；`usage.external_requests` / `content_bytes` 只表示已知用量，不能把它们单独当作总预算消耗。任务状态的 `usage.scope=job_lifetime` 表示任务累计记录；查询 `job.get` 本身不会再次发起这些 HTTP 请求，不能逐次相加轮询响应中的累计数字。

| 操作 | 参数 | 行为 |
|---|---|---|
| `job.get` | `job_id` | 查看持久状态、进度、总期限、剩余预算和已知/未知用量 |
| `job.results` | `job_id`；可选 `offset=0`、`limit=20` | 依输入顺序返回条目；`offset` 为 0–20，`limit` 为 1–20；用 `next_offset` / `has_more` 继续读取 |
| `job.control` | `job_id`、`action` | `pause`、`resume`、`cancel`；批量任务另外支持显式 `retry_failed` |

成功条目的 `receipt` 包含 `snapshot_id`、`document_id` 等快照收据；正文通过 `document.range` 读取。删除后 `available=false`，不再发布旧收据。失败条目有独立错误码和用量，其余尚未开始的输入可继续执行。状态页保留历史执行记录，不把失败项或未知结果算作成功。

暂停或取消会改变租约代数，旧执行不能发布快照；核心还会向 worker 协作取消当前尝试。已发出的 HTTP 请求无法撤回。恢复只继续尚未开始的条目；暂停时被打断的条目可能成为失败/未知项，不会被自动重发。`retry_failed` 需要任务仍在期限内、失败项还有授权尝试次数和剩余预算；不会清零用量或重新执行已成功条目。默认 `max_attempts=1` 时，没有第二次授权尝试。

每项快照、索引、收据、状态和预算确认在同一事务内提交，并复核 owner、权限代数、租约、期限和删除代数。schema 4 在既有 schema 3 上增加批量任务账本；历史 schema 1 / 两种 schema 2 的迁移仍保留。任务恢复保证旧执行不重复发布；外部 HTTP 行为不承诺 exactly-once。

MCP 对应工具为 `read_urls`、`get_job`、`job_results`、`control_job`。它们转发同一核心操作，不自行扩张预算。结果里的网页内容继续按不可信外部数据处理。

## 有界站点发现

`map.sites` 列出核心实际加载的站点审核登记及其 `manifest_sha256`。`map.submit` 创建 `kind=web.map` 的持久任务，解析 Sitemap/index、RSS、Atom、HTML/XHTML 并合入当前 owner 的可见历史候选。仅 Sitemap index 和 robots 声明可以扩张后续发现文档；普通链接只产出候选，不自动读取正文、建立快照、生成证据或索引。

```json
{"operation":"map.submit","params":{"site_id":"operator-reviewed-site","seeds":["https://reviewed.example/docs/sitemap.xml"],"scope_path":"/docs/","idempotency_key":"docs_map_1","max_requests":30,"max_bytes":16777216,"max_urls":200,"max_depth":3}}
```

示例的站点和 URL 是占位值。正常执行需要部署方在独立的 `sites.json` 中登记并启用审核有效的站点；随包文件为空。核心和 Worker 必须加载同一精确文件身份。站点审核、robots 允许和实际网络目标校验分别执行，普通调用不能注入审核结论、可信时钟或更宽配额。

| 参数 | 默认值 | 允许范围和含义 |
|---|---|---|
| `site_id` / `seeds` / `idempotency_key` | 必填 | 登记 ID、1–20 个明确发现入口、同一 owner 的幂等键 |
| `scope_path` | 站点登记范围 | 只能缩窄已审核路径，所有入口与后续 HTTP 跳都复核 |
| `timeout_ms` / `per_request_timeout_ms` | 600000 / 20000 | 分别为 1–3600000 / 1–55000 毫秒，暂停不延长任务总期限 |
| `max_requests` / `max_bytes` | 100 / 16777216 | 分别为 1–1000 次请求尝试 / 1–67108864 个解压后响应体字节 |
| `max_urls` / `max_depth` | 1000 / 3 | 分别为 1–1000 个去重 URL / 0–8 层发现深度；不是正文数量或路径层数 |

查询使用同一个 `job.get` / `job.results`；map 结果项包含 `type=candidate`、`url`、`parent`、`source`、`depth`、`path_depth` 和 `body_read=false`。`source` 可以是 `sitemap`、`sitemap_index`、`rss`、`atom`、`html` 或 `history`。历史项保留原文档身份和删除代数，URL 规范化后也不能绕过删除。历史正文可能已有其他读取收据，`body_read=false` 只描述本次发现行为。

map 分页每页最多 1000 项，`offset` 是候选位置，`next_offset=null` 表示当前没有下一页。已删除项会被隐藏，所以页内位置不必连续。`job.control` 支持 `pause`、`resume`、`cancel`；map 不接受批量读取专用的 `retry_failed`。已失败或结果未知的跳不自动重发。

核心在每个物理 HTTP 跳前持久预留一次请求和有界字节额度，robots 元数据、跳转和失败尝试均计入。可信已知用量只退还未用预留，未知结果按全份预留计费；`usage.scope=job_lifetime` 的累计数字不能按轮询次数相加。站点同源串行、最小间隔、24 小时窗口配额及冷却跨任务持久生效。完成当前队列不表示全站覆盖；范围、容量、深度和预算停止原因会保留。

MCP 对应 `list_sites` / `map_site`，并复用三个 job 查询/控制工具；C++ CLI 可以用 `searchctl call request.json` 调用同一操作。robots 缓存规则、URI 合同、存储上限和部署配置见 [SITE_DISCOVERY](SITE_DISCOVERY.md)。

## 有界正文爬取

`crawl.submit` 返回 `kind=web.crawl`，使用相同的已审核站点、固定 BzsBot robots 准入、持久请求/字节预算和单跳队列。它从入口读取正文，并把允许的同源范围内链接加入前沿；同一响应同时产生正文和发现结果。自动请求不转到普通 `web.read` 绕过准入。

```json
{"operation":"crawl.submit","params":{"site_id":"operator-reviewed-site","seeds":["https://reviewed.example/docs/"],"scope_path":"/docs/","idempotency_key":"docs_crawl_1","max_pages":30,"max_urls":100,"max_depth":3,"max_requests":80,"max_bytes":16777216}}
```

参数沿用 map 的 `site_id/seeds/idempotency_key/scope_path/timeout_ms/per_request_timeout_ms/max_requests/max_bytes/max_urls/max_depth`。新增 `max_pages` 范围 1–1000 且不超过 `max_urls`，省略时取 `min(100,max_urls)`。它限制已派发的逻辑正文页；重定向仍是同一页，robots/单独发现文档独立消耗请求而不计正文页。失败和未知的实际派发仍占页预算。

| 操作 | 输入与返回 |
|---|---|
| `job.get` | 除共同预算、状态外，提供 `max_pages/used_pages/remaining_pages/page_count/succeeded_pages/failed_pages/skipped_pages/edge_count` |
| `job.results` | `offset` 0–1000、`limit` 1–1000；返回 `type=page`、URL/最终 URL、parent/source/depth、state、available/body_read、snapshot_id/document_id、publication 和错误 |
| `crawl.edges` | `job_id`，可选 `offset=0`（0–10000）、`limit=20`（1–1000）；返回 parent/url/source/depth/followed/reason 的持久边 |
| `job.control` | `pause/resume/cancel`；crawl 不接受 `retry_failed`，也不自动重发失败/未知请求 |

结果和边均使用 `items`、`has_more`、`next_offset`；有删除过滤时位置可以不连续。`followed` 是前沿扩张决定，不是正文成功收据。重复页面按 URL 去重，边按 parent/URL/source 去重。已成功条目提供快照收据，正文用 `document.range` 读取；权限或删除限制仍在发布前检查。

map/crawl 使用持久轮转顺序，并共享同源通道、间隔、配额与冷却。任务停止检查 `partial/stop_reason`；`succeeded` 不承诺全站覆盖。页、URL、深度、查询变体、请求、字节与边保留上限、迁移和精确状态语义见 [SITE_CRAWLING](SITE_CRAWLING.md)。

MCP 对应 `crawl_site` 与 `crawl_edges`；新工具在 SDK 类型转换之前拒绝未知字段和非严格 JSON 类型，显式 `null/true/"2"` 不能替代整数预算。可选参数应省略以采用核心默认值；有效类型的零值原样交给核心拒绝。数值范围、审核和业务逻辑仍只有核心一套。

### 索引和摘要刷新

`bzs-publication-1` 识别通用及 BzsBot/bzs861 适用的 noindex、nosnippet、nofollow、none 与 data-nosnippet；重复 `X-Robots-Tag` 保留 agent 范围并合并限制。其他爬虫专属规则不控制本产品，完整参数化搜索引擎扩展尚未实现。

正文快照与允许的摘要/安全标题分开保存。当前发布限制约束新搜索及旧冻结会话；noindex 变化在页面提交时撤索引和旧结果，nosnippet/data-nosnippet 限制不会从 canonical 正文补回片段。受限制摘要不提供伪造的 canonical 命中字节偏移。站点 `links_only` 限制同样约束普通网页/批量读取刷新。

即使新正文解析失败或无法保留，收到的有效禁止声明也通过独立的限制观察收紧已有发布状态；失败响应不能放开原限制。读取失败、预算和错误仍照常报告，不能因此伪造正文成功。owner 显式快照/证据读取与搜索发布分别执行，旧内容已经发送到外部时无法收回。

普通读取的 `download_truncated` 预览不获索引、摘要或跟随许可；批量和 crawl 仍保持字节预算失败语义。重定向中间页的响应规则绑定实际 URL，目标拒绝不会丢掉之前已收到的限制，允许的最终页也不能覆盖入口规则。

## 本地操作

| 操作 | 说明 |
|---|---|
| `search` / `search.page` | 字符串或当前 AST 子集；冻结结果与稳定游标，页大小和候选/结果上限由服务端检查 |
| `query.compile` | AND/OR/NOT、短语、字段与已支持顶层 site/filetype 过滤；未索引字段明确拒绝 |
| `document.ingest` / `document.read` / `document.delete` | 显式导入、读取与按文档删除；导入 URL 为元数据，不是任意服务器文件读取 |
| `document.range` / `find` | UTF-8 字节窗口续读、精确查找和上下文 |
| `evidence.create` / `evidence.read` | 快照、权限与字节区间复核；`semantic_support=not_evaluated` |
| `capabilities` / `stats` | 当前运行能力与用量，不发布尚未实现能力 |
| `fixtures.*` / `fixture.ingest` / `backup.create` | 保留离线 fixture 和任务路径；备份强制隔离，没有安全恢复发布流程 |

核心先校验搜索参数再创建会话；非法请求不能污染未过期会话额度。原引号指令、ASCII/Unicode 边界和精确字面回归继续执行。中文索引/BM25 与原文片段为既有能力，本轮保留回归；实际召回路径及不完整结果必须明确标注。

删除对象的正文、片段、证据和检索计数不可继续发布；无关文档可以保留可用会话。真实权限变化仍需失效或重新鉴权，在途任务不得复活删除对象。schema 6 增加正文前沿/边/页记账、调度次序与发布状态；保留 schema 1、两种历史 schema 2、schema 3/4/5 的迁移，具体覆盖见本轮报告。

## worker RPC 与错误

核心经独立令牌的回环 `POST /rpc` 调用 `health`、`search.multi`、`fetch.extract`、`fetch.cancel` 等内部操作。批量读取的 `fetch.extract` 携带唯一 `attempt_id`、`max_requests` 和 `max_content_bytes`，响应带 `usage.external_requests` / `content_bytes`。worker 再次校验来源与网络目标，不写数据库，也不提供公开的代理/网段放行接口。生产代理、远程 DNS 放宽、非空 allow_cidrs、无鉴权 MCP HTTP 被拒绝。

内部 `map.fetch` 每次最多执行一个 HTTP 请求，不自动跟随重定向或软跳转。参数绑定 `attempt_id`、站点清单哈希、目标、种类、期限和单跳预算；内容请求还必须带有效 robots 记录。Worker 在真正派发前再次核验审核和 robots，响应带实际用量及准入决策；核心严格验证回执并负责持久化、下一跳和删除/权限屏障。没有 HTTP 响应的明确拒绝允许 `http_status=null`，不能因此丢弃原拒绝错误或误报已经收到响应。

本批增加 `kind=page`，返回受限的 `page`、`publication` 与同响应 `discovery`。失败响应若已观察到适用禁止声明，可携带只收紧发布的 `publication_observation`；该记录不授予请求、索引或摘要权限。`fetch.extract` 的普通读取共享页面策略；两种路径都必须在核心通过结构、URL、状态、用量及版本复核。

`fetch.extract` 另以有界 `redirect_publication_observations` 保存 HTTP/软跳转的限制性记录，每项绑定 `url/http_status/publication`。最终正文策略与逻辑入口规则分别保存，内核只接受实际已到达的合法响应身份；未请求或被拒绝的目标不是发布授权。

合同错误保持 `E_SCHEMA`、`E_CONTRACT`、`E_CONTRACT_LIMIT`。来源错误为 `E_SOURCE_CONFIG` / `E_SOURCE_REVIEW`；发现路径另报告站点审核、清单不一致、范围或 robots 拒绝等独立原因。其余网页、权限、游标、证据、任务和隔离错误通过统一信封报告。每个新增错误的具体状态码与行为以本轮 API 回归绑定，不将候选实现视为冻结的完整 v1 合同。
