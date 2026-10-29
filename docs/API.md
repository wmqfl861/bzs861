# searchd API（0.1.10 接手续作）

所有业务请求为 `POST /api/v0/call`，通过回环 HTTP 与 Bearer token 调用。Host/Origin、请求大小和超时检查继续保留。MCP、Python 启动器与 C++ CLI 复用同一核心；当前版本仍为 0.1.10，业务 schema15，当前HTTP取消保留接纳的实际验证见[本批记录](../validation/http_cancel_admission_20261025/README.md)。[上一批成员取消](../validation/direct_cancel_20261025/README.md)、共享读取、URI删除及阶段报告保留各自历史输入和结果。

```json
{"operation":"web.search","params":{"query":"断点续爬 实现","limit":10}}
```

成功响应信封包含 `request_id`、`api_version`、`status`、`result`、`errors` 和 `usage`。失败信封保留请求身份、失败状态和 `errors`，不要求存在 `result`；`usage`、`shared_read` 仅在核心提供执行详情时输出，缺少字段不能解释为零消耗。本版存在外部网络路径，不能沿用 0.0.2 的 external_requests 恒为零假设；实际次数、未知状态和错误按执行结果报告。模型执行仍未实现。传输层早期错误可能使用精简错误信封。

## HTTP 执行与背压

`searchd --threads 1..64` 控制IO线程，默认8；`--request-threads 1..32` 控制普通业务执行线程，省略时取 `min(threads,32)`。另设固定1个取消控制线程，启动ready报告 `control_threads=1`、`control_body_limit_bytes=4096`。业务调用不在IO回调中等待Worker RPC；两池各自限制正在执行与已接纳待开始的总数，没有额外等待额度，不能互借容量。选中的池满时，在进入Service、预留业务预算或派发Worker前返回HTTP 503 / `E_REQUEST_BACKPRESSURE`。

传输与认证检查通过后，正文不超过4096字节、严格JSON仅含 `operation="web.read.cancel"` 和 `params.read_id`（48位小写hex）的请求才选择控制池。未知字段、重复键、错类型、缺字段、无效ID或更大正文都按普通池接纳；Service仍重新解析原正文并检查owner、主体撤权与句柄。4096字节只限制保留槽分类，添加大量合法空白的取消仍可经普通池执行。`web.read.prepare`、`job.control`、`session.cancel`及其他操作不使用控制槽。

`GET /healthz` 在 IO 路径响应，不占业务槽，仍受 Host/Origin 检查和 64 个活动连接上限约束。它报告传输入口可响应，不证明来源可用、业务槽有余量或健康检查的时延 SLA。请求正文仍限 2 MiB、header 限 8 KiB；socket 读取与响应写入各有 5 秒期限，业务执行期限由对应操作决定，不能把 5 秒当作所有业务的整体期限。

已接纳请求在客户端断连后仍可能完成并提交；断连不自动取消或重新发送业务。需要取消的持久任务仍调用原取消接口，未知结果保留原计费/幂等语义。停止时禁止新业务接纳，并在销毁业务对象前等待已执行请求结束；不保证停止后还能收到响应。详情见 [HTTP 执行](HTTP_EXECUTION.md)。后台持久任务使用独立的四条固定通道，见[后台调度](BACKGROUND_DISPATCH.md)，不能把后台上限与同步 HTTP 上限混为一个额度。

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
| `web.search` | `query`；可选 `sources`（来源 ID 数组）、`category`、`limit`、`per_source`、`language`、`timeout_ms`、`deadline_ms`、`mode`、`max_sources` | 多源并发、状态、去重与索引族融合；只执行已审核且启用的来源 |
| `web.read` | `url`；可选 `freshness`、`max_age_s`、`max_bytes`、`start_byte`、`timeout_ms`、`read_id` | 公网读取和快照；带一次性句柄时支持成员取消；不放宽出站规则 |
| `web.read.prepare` | `{}` | 准备同 owner、当前进程的一次性取消句柄；不出站 |
| `web.read.cancel` | `read_id` | 取消该成员；仅最后活动成员退出才请求物理停止；控制接纳仍受业务池503背压 |
| `web.sources` | `{}` | 运行来源清单、审核/启用、冷却和请求状态；列出不等于可执行 |

直接读取的句柄期限、固定付款、提前取消的未知物理用量、提交与交付竞态、Worker兼容和重启边界见[直接成员取消合同](DIRECT_READ_CANCELLATION.md)。MCP提供 `prepare_read_url`、`cancel_read_url` 及 `read_url` 的可选 `read_id`。显式无效句柄原样交核心拒绝，不静默降级为普通读取。

随包 17 条来源登记包含 13 个适配器及 4 个后续候选，默认全部禁用。正常运行的每个启用来源需要 `contract` 对象，遵循原 `source-manifest` 合同，包含 `approved` 审核状态、`no_payment`、自动访问批准和当前有效审核日期。

运行字段须与合同身份一致：`id`、`provider`（对应 `provider_id`）、`index_family`、能力、适配器、端点、启用和限额。运行类别 `encyclopedia` 对应合同 `web`，`papers` 对应 `scholar`；`params.adapter`、`params.base_url`、`min_interval_ms`、`daily_cap` 等不能借另一身份/端点或更宽配额执行。结构或身份错误在启动时返回 `E_SOURCE_CONFIG`；未批准或审核过期的执行返回 `E_SOURCE_REVIEW`（HTTP 409）。核心与 worker 都实施准入。

S0 候选探测属于显式工具路径：必须指定 `--include-candidates` 和非空 `--only` 来源 ID 列表；`--sources` 是清单文件路径，不是 ID 列表。它不修改正常准入或自动批准来源，详见 [S0_PROBE](S0_PROBE.md)。

### 搜索结果

搜索来源的新 `Retry-After` / API `backoff` 等待最多 24 小时，Core 与 Worker 都执行边界并在截断时返回诊断；配置冷却与合法已有期限继续生效。新报告中的 parse-error 退避最长 72 小时；已有可信绝对期限不因后来成功、限流或时钟回拨缩短，回拨后剩余时间可以超过 72 小时。无证明历史值仅按一年极端异常启发式保守修复，一年内或事件时间有歧义的旧值可能保留；准确条件及诊断字段以[来源退避合同](SOURCE_BACKOFF.md)为准。遇限流不自动重试或启用来源；站点/robots 的等待合同不因该搜索来源上限而改变。

`results[]` 包含排名、URL、标题、来源摘要、来源归属和索引族。仅在贡献来源的当前发布策略允许摘要时，返回 `snippet_kind=search_engine_snippet`；该摘要不能作为已读正文。任一贡献来源仅允许链接时，合并结果返回 `snippet_kind=publication_suppressed`、空摘要和以 URL 代替的标题；缓存与冻结结果同样受此限制，具体规则见 [来源发布策略](SOURCE_PUBLICATION.md)。每个来源返回正常、空结果、跳过、冷却/配额、封禁、限流、解析失败或网络错误等状态，部分失败不能伪装成全量成功。

索引族独立性未知时保守控制贡献；当前 DuckDuckGo 与 Bing 共用索引族，不能因不同服务名重复计票。已支持的 site 条件在结果中复核；其余未支持条件不能静默丢弃。

原同步 web.search 保持兼容；需要联网冻结版本、分页、断线重取和取消时使用 web.search.start。完整 SESSION-01/02 工作包仍未签收。

### 读取结果与缓存

读取结果包括文档/快照 ID、来源与最终 URL、标题、版本、抓取时间、缓存状态、索引状态、文本窗口、元数据、抓取/解析状态和 `content_trust=untrusted_web_content`。文本窗口的起止是原文 UTF-8 字节边界。no-store 的 200 全量响应返回瞬时变体：`transient="no_store"`，无文档/快照 ID、版本与 `cache` 对象，其余字段（URL、标题、窗口、元数据、抓取状态、`content_trust`、`usage`/`shared_read`）照常；该正文一次性交付，不落盘、不可复用。

普通 `web.read.max_bytes` 控制返回的 canonical 文本窗口，默认 20000、上限 262144 字节，不是 Worker 下载预算；`read_many`、map 和 crawl 的同名任务参数则限制累计下载的解压后响应体字节。

`cache_first` 仅复用按响应指令/年龄和当前请求profile证明新鲜的完整表示；`refresh` 显式刷新；`cache_only` 不访问来源，无快照报`E_NOT_CACHED`、需要验证报`E_CACHE_STALE`。noindex 内容可以在允许的读取路径查看但不进入本地索引。权限、删除和缓存新发布检查不能因命中缓存而跳过。

兼容的并发普通读取现在可在同一 `WebService` 中共享一次在途获取与一次提交。匹配包含 owner、精确规范化 URL、超时、权限/删除/发布捕获、快照与元数据/条件身份及已核实的 Worker 请求 profile；每个调用仍生成自己的窗口，参与本次网络工作的成员均为 `from_cache=false`。`cache_only` 不等待正在抓取的未来结果。每组最多累计 32 个成员，最多登记 64 个开放组；没有证明、超限或同线程重入时独立执行，RPC 返回后停止加入。完整边界见[直接共享读取](DIRECT_SHARED_READS.md)。

`web.read` 成功结果中的 `usage.scope=direct_read` 是本调用新增的物理用量：`payer` 报告 Worker 的实际请求数/响应体字节，`subscriber` 为零。网络结果另有 `shared_read`，包含进程内 `attempt_id`、`role`、`members`、`scope=process` 与 `physical_usage`；这是同一次工作的关联说明，不能把成员的关联用量再次相加。缺少可靠物理回执时，`physical_usage` 与 `payer` 的增量为 `known=false`、请求数/字节为 `null`；`subscriber` 增量仍为 `known=true`、零请求和零字节，同时保留未知物理关联。缓存命中是已知零用量且没有 `shared_read`。

抓取、提交或返回窗口失败时，HTTP 失败信封仍保持原 `status=failed` 和错误码，并在存在执行回执时附同形的 `usage/shared_read`，因此错误不表示没有消耗。CoreClient 保留这些详情；MCP `read_url` 成功时原样透传，失败时保持工具错误并附核心详情 JSON。进程内关联不是持久任务账本，不能据客户端断连推断未执行，也不能在重启后重新查询这份收据。可选成员取消使用 `web.read.prepare` / `web.read.cancel`；无句柄调用保持原路径，两类调用均不与 durable 批读或 map/crawl/search 混合。

## 持久联网搜索会话

`web.search.start` 创建联网冻结会话。C++ 核心保存来源队列、执行身份、不可变结果版本、分页游标和元数据事件。原同步 `web.search` 兼容入口保留；需要分页、断线重取或取消时使用会话入口，本地 `search` 也接入状态/事件接口。

```json
{"operation":"web.search.start","params":{"query":"example","sources":["approved_source"],"idempotency_key":"search-001","limit":20,"per_source":10,"timeout_ms":8000,"deadline_ms":120000}}
```

来源 ID 是占位示例。必须使用当前已启用且审核有效的来源；默认 17 条来源全部禁用，创建会话不构成批准。

| 参数 | 默认值 | 范围与含义 |
|---|---|---|
| `query` | 必填 | 非空 UTF-8，最多 1000 字节 |
| `idempotency_key` | 必填 | 原标识符规则，最多 128 字节；复用须匹配规范化请求及有效来源配置身份 |
| `sources` | 按 category 选择 | 显式数组长度 1–20；来源须启用且审核有效，重复 ID 合并，首次出现次序执行 |
| `category` | web | 未提供 sources 时生效；web 选默认 web/encyclopedia，all 选默认来源，其余按能力匹配；仍须选出 1–20 个合法来源 |
| `limit` | 20 | 冻结版本最多 1–50 条结果 |
| `per_source` | 10 | 每来源最多 1–30 条候选 |
| `language` | 省略 | 非空、最多 16 字节，只含 ASCII 字母、连字符或下划线 |
| `timeout_ms` | 8000 | 单来源 RPC 预算 1000–30000 毫秒 |
| `deadline_ms` | 120000 | 从提交起的总排队/执行期限 1000–600000 毫秒，过期拒收发布 |

开始和状态响应包含 `session_id/kind/state/result_set_version/last_event_id/expires_at_ms/deadline_ms/dispatched_source_attempts/unknown_source_attempts/model_calls`，以及事件保留配置和建议轮询间隔。尚无冻结结果时版本是空字符串。**请求的 deadline_ms 是相对时长，响应同名字段是绝对截止时间。**

同 owner 同规范化请求/来源配置复用键返回原会话及 `reused=true`，不匹配返回 `E_IDEMPOTENCY_CONFLICT`（409）。旧键身份在会话到期后仍保留，通过当前来源准入后返回 `E_SESSION_EXPIRED`（410），不会重跑；新查询须用新键。当前来源审核或配置错误可能先被拒绝。

核心逐来源派发，每个完整来源响应结算后复用已有 C++ 索引族 RRF，形成新的不可变版本。不同版本可重排，既有版本与游标不重排。`completed` 表示队列已结算，不保证每源成功或有命中；还需检查错误和页面 partial。未知/丢失响应保留计数，不自动重发同一来源。

### 状态、事件与分页

| 操作 | 参数 | 作用 |
|---|---|---|
| `session.get` | session_id | 获取当前 owner 状态和最新版本，不请求来源 |
| `session.events` | session_id，after_event_id=0，limit=20 | 返回指定事件之后的元数据；limit 为 1–32，after_event_id 为 0–(2^62−1) |
| `search.page` | session_id，联网必填 result_set_version，可选 cursor、page_size=20 | page_size 为 1–100，联网单版本最多 50 条结果 |
| `session.cancel` | session_id | 停止未派发来源、封住在途发布，已完成版本保留至到期 |

```json
{"operation":"search.page","params":{"session_id":"SESSION_ID","result_set_version":"VERSION_ID","page_size":20}}
```

分页返回 hits、版本、has_more、partial 和 `coverage=external_search_snippets_unread`，有下一页才返回 next_cursor。命中的 position 是版本内固定位置，body_read=false。按 `(session_id,result_set_version,position)` 去重；同一游标可重复拉取，但不能换会话/版本。删除和发布约束可使旧页减少，稳定顺序不允许重放已撤内容。本地版本仍等于 session ID，原不传版本的本地分页兼容。

事件响应在状态之外返回 events、next_event_id、has_more、heartbeat_at_ms。事件仅含递增 event_id、kind、创建时间及适用时的版本 ID，不含查询、URL、标题、摘要或历史命中数。处理后保存 next_event_id，重连继续传入，以 `(session_id,event_id)` 去重。无新事件返回空数组和心跳，心跳不写日志；接口为有界拉取，不依赖持续连接。

每会话最多保留 64 事件、10 分钟。请求落在过期历史前返回 `E_EVENT_HISTORY_EXPIRED`（410）：先取 session.get 的最新状态/版本，重新分页，再从该状态的 last_event_id 拉取后续事件。超过最后事件的未来游标是 `E_EVENT_CURSOR`（400）。完整会话和版本最多保留一小时，会话到期须新键重新查询。

每页重新检查 owner、授权代际、删除、页面规则、已知完整 URL 链与站点上限。融合保留真实 URL 贡献者，去重键不能当政策身份。每行最多绑定 24 个 URL 和 24 条历史表示；无法完整绑定则禁止发布。形成版本时已经生效的删除/noindex/nosnippet 同样固定，后来完整读取、重新导入或重定向变化不能恢复旧版本。

来源派发检查当前配置身份；分页重新校验冻结来源合同有效期。它不是独立的来源撤销日志，不证明跨备份撤销连续性。授权变化或审核超期返回 `E_SESSION_INVALIDATED`（409）；其他 owner 为 `E_NOT_FOUND`（404）。

### 取消、背压与映射

取消停止未完成来源，已在途的有限 RPC 可以继续至超时，但不能再发布。重复取消或取消已完成会话不新增取消事件，已完成版本保留，响应含 completed_results_preserved=true。dispatched_source_attempts / unknown_source_attempts 为核心尝试及其未知子集，不是精确实际 HTTP 成功数，也不应按轮询累加。

未过期本地/联网完整会话共用每 owner 64、部署 256 上限。单联网会话请求、审核合同、来源载荷及全部冻结行最多 4 MiB；单来源保存载荷最多 256 KiB。过期完整数据可清理，永久身份/尝试摘要继续保留，不计入活动会话容量。达到活动或载荷上限返回 `E_SESSION_BACKPRESSURE`（429），不是无限容量。

缺少或不匹配版本为 `E_RESULT_VERSION`（400），错会话/版本游标为 `E_CURSOR`（400）。MCP 工具为 start_web_search、get_search_session、search_session_events、search_session_page、cancel_search_session，在 SDK 转换前检查原始 JSON 类型和未知字段，不把字符串来源列表、布尔预算或显式 null 转成默认请求。

这部分实现 SESSION-01/02；完整验收、真实长期来源运行、任意断电和分布式部署仍需取证，事件日志不解除恢复隔离。详见 [SEARCH_SESSIONS](SEARCH_SESSIONS.md)。

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

成功条目的 `receipt` 包含 `snapshot_id`、`document_id` 等快照收据；正文通过 `document.range` 读取。删除后 `available=false`，不再发布旧收据。no-store 条目以 `state=succeeded` 终结但不建快照：`available=false`，`receipt` 仅携带 `transient="no_store"` 标记（无快照身份），用量照记且 `retry_failed` 不重跑。失败条目有独立错误码和用量，其余尚未开始的输入可继续执行。状态页保留历史执行记录，不把失败项或未知结果算作成功。`used_pages` 按成功条目计数，交付瞬时的成功条目同样计入；瞬时正文无快照可读，该计数不等于可读正文的数量。

兼容任务可在派发事务中共享一次物理读取；新 `read_many.submit` 任务的首项也可在创建事务内加入尚开放、未结算的原尝试。迟到加入要求同 owner、权限代际、任务创建删除序号、精确 URL 和目标删除代际相同，预算覆盖原预留、期限覆盖原租约、有效超时兼容，且当前快照与当前全局页面发布序号（`publication_sequence`）严格等于原捕获。每次物理尝试最多累计 32 个成员，取消不释放槽位；幂等重提、其他既存 pending 项、暂停项和显式重试不通过提交入口迟到加入。不能加入的新任务按原调度继续排队，普通 `web.read`、map/crawl 和来源搜索不参与。完整合同见[共享读取](SHARED_READS.md)。

暂停或取消会改变被操作任务的租约代数，使其旧执行失去发布资格。共享组仍有有效成员时不取消物理请求；最后有效成员离开时，核心在跨 Store 写事务中先持久关闭新加入，再向 worker 协作取消当前尝试。关闭只限制新加入，不提前结算原预留，也不剥夺其他仍有效成员的收据资格。已发出的 HTTP 请求无法撤回。恢复只继续尚未开始的条目；暂停时被打断的条目可能成为失败/未知项，不会被自动重发。`retry_failed` 需要任务仍在期限内、失败项还有授权尝试次数和剩余预算；不会清零用量或重新执行已成功条目。默认 `max_attempts=1` 时，没有第二次授权尝试。

每项快照、索引、收据、状态和预算确认在同一事务内提交，并复核 owner、权限代数、租约、期限和删除代数。schema 4 在既有 schema 3 上增加批量任务账本；历史 schema 1 / 两种 schema 2 的迁移仍保留。任务恢复保证旧执行不重复发布；外部 HTTP 行为不承诺 exactly-once。

MCP 对应工具为 `read_urls`、`get_job`、`job_results`、`control_job`。它们转发同一核心操作，不自行扩张预算。结果里的网页内容继续按不可信外部数据处理。

## 终态任务明细回收

`jobs.compact` 接收可选整数 limit（默认16、范围1–64），只处理当前 owner 已稳定结算的终态 fixture.ingest/web.read_many/web.map/web.crawl。创建、原截止和最后租约时间都须超过固定七天窗口；活动、暂停、未结清预留不可回收。没有 owner、days 或清空全部参数。

```json
{"operation":"jobs.compact","params":{"limit":16}}
```

原 ID/幂等键/指纹/终态/累计预算和已知未知用量永久保留。job.get 增加 `retention`（version=bzs-job-retention-1、details_available=false、compacted_at_ms、minimum_retention_ms）；同参数重提通过当前准入后返回原摘要，不重做请求。job.results、crawl.edges、job.control 对原 owner 返回 `E_JOB_RESULTS_EXPIRED`（410），其他 owner 仍404。

结果含 compacted_jobs、deleted_detail_rows、deleted_robots_records、has_more_jobs、retained_job_keys 与 limits。摘要最多8192字节；未压缩任务每owner10000、部署50000（limits.job_capacity_scope=unarchived_details），压缩后永久身份不占该配额。fixture 压缩后 result=null，快照独立保留。robots 仅在过期且没有保留准入/回执引用时删除，单次最多min(64,limit×4)；has_more_jobs不表示robots积压。新任务接纳前另有最多8项有界维护。

维护不请求网络、不删正文/快照/证据、不清零未知计费、配额/冷却、派发序号及删除/发布水位。释放逻辑容量和SQLite可复用页，不承诺文件立即缩小。CLI为 `bzs jobs compact --limit 16`，MCP为 compact_job_history；重复调用可能删下一批，操作本身不标记幂等。详见 [JOB_RETENTION](JOB_RETENTION.md)。

全局未压缩任务达到 50000 时，新任务接纳在同一事务内另做最多 8 项跨 owner 维护；仍须满足完整七天、稳定终态及无未结算预留规则，包含已撤权主体的合格历史。该内部维护不向发起者返回他人的摘要、不解除撤权，公共 `jobs.compact` 仍只处理自己的任务。这不是通用全局 GC，其他有界明细容量保持原约束。

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

批量读取与 map/crawl 分别使用持久队列；成功入队和租约预留在同一事务分配递增票号并排到相应队尾，同键重提不重新排队。符合条件的旧任务不会被持续新任务反复越过。map/crawl 仍共享同源通道、间隔、配额与冷却，具体保证和旧历史顺序迁移限制见 [TASK_SCHEDULING](TASK_SCHEDULING.md)。任务停止检查 `partial/stop_reason`；`succeeded` 不承诺全站覆盖。页、URL、深度、查询变体、请求、字节与边保留上限、迁移和精确状态语义见 [SITE_CRAWLING](SITE_CRAWLING.md)。

MCP 对应 `crawl_site` 与 `crawl_edges`；新工具在 SDK 类型转换之前拒绝未知字段和非严格 JSON 类型，显式 `null/true/"2"` 不能替代整数预算。可选参数应省略以采用核心默认值；有效类型的零值原样交给核心拒绝。数值范围、审核和业务逻辑仍只有核心一套。

### 索引和摘要刷新

`bzs-publication-1` 识别通用及 BzsBot/bzs861 适用的 noindex、nosnippet、nofollow、none 与 data-nosnippet；重复 `X-Robots-Tag` 保留 agent 范围并合并限制。其他爬虫专属规则不控制本产品，完整参数化搜索引擎扩展尚未实现。

正文快照与允许的摘要/安全标题分开保存。当前发布限制约束新搜索及旧冻结会话；noindex 变化在页面提交时撤索引和旧结果，nosnippet/data-nosnippet 限制不会从 canonical 正文补回片段。受限制摘要不提供伪造的 canonical 命中字节偏移。站点 `links_only` 限制同样约束普通网页/批量读取刷新。

即使新正文解析失败或无法保留，收到的有效禁止声明也通过独立的限制观察收紧已有发布状态；失败响应不能放开原限制。读取失败、预算和错误仍照常报告，不能因此伪造正文成功。owner 显式快照/证据读取与搜索发布分别执行，旧内容已经发送到外部时无法收回。

普通读取的 `download_truncated` 预览不获索引、摘要或跟随许可；批量和 crawl 仍保持字节预算失败语义。旧 Worker 兼容回包同样检查已报告的截断、206 和非成功 HTTP 状态，不能用不完整正文解除原限制。真实派发身份与索引/摘要水位持久化，晚到旧允许响应不能跨过新限制或删除；完整后发读取可以形成新表示，旧冻结表示不复活。重定向中间页的响应规则绑定实际 URL，目标拒绝不会丢掉之前已收到的限制，允许的最终页也不能覆盖入口规则。

## 本地操作

本地搜索在选取有界候选后，逐篇复制正文并在 Store 锁外进行精确匹配、评分及排序；匹配和非 FTS 评分共享每个字段的惰性 ASCII 折叠。候选 SQL、单篇复制和最终可见性/摘要投影仍持必要的锁。返回或创建会话前重查主体/owner、删除代际、保留快照及当前和历史发布约束；候选失效会被剔除并标记 `partial`，不自动重跑查询。冻结会话容量在最终事务再检查，`search.once` 仍不创建持久身份。细节见[搜索并发](LOCAL_SEARCH_CONCURRENCY.md)。

本地查询使用原 bzs/2 词项索引和子串辅助索引；短语、符号字面量、非 ASCII 子串及 URL 字段先取安全候选，再由同一精确匹配器验证。混合 AND 查询在候选上限前求两类候选的交集，词项 BM25 和匹配语义保持不变。辅助路径标为 `fts5_substring_verified`；只有无法构造安全正向候选的表达式仍用 `bounded_scan_recent`。候选和返回上限仍生效，预算不足时 `partial=true`，不承诺无限召回。

辅助索引每字段最多 65536 个不同的 1–3 codepoint gram，字段超限以显式 overflow 标记继续进入原文复核；不会静默截掉后半正文。仅折叠 ASCII 大小写，不做 Unicode casefold、去重音或规范化。索引内容是可重建派生数据，快照和证据字节不变。

已知旧 bzs/2 索引会在一个事务中补建辅助表并更新既有 `fts_tokenizer_version` 兼容标记；该辅助迁移最初保持业务 schema13，后续 schema14 增加删除身份，当前 schema15 再增加共享读取的派发超时与加入关闭状态，失败则整体回滚。升级前停止旧 Core，升级后的库拒绝旧二进制重新打开；此闸门不支持新旧进程混用同一数据库。备份恢复隔离规则保持原状。

MCP `search_local` 使用 `search.once`，返回 `returned_count` 与 `result_mode=single`（替代原 `total_frozen`）。连续单次调用不占用冻结会话配额；需稳定分页的调用仍使用 `search` / `search.page`，其配额与有效期不变。两条路径复用相同的 owner、删除、站点/页面发布约束及摘要处理。

| 操作 | 说明 |
|---|---|
| `search` / `search.page` | 字符串或当前 AST 子集；冻结结果与稳定游标，页大小和候选/结果上限由服务端检查 |
| `search.once` | 单次本地查询：query，max_candidates=2000（1–2000），limit=10（1–100）；返回 hits、returned_count、result_mode=single、partial 和检索信息，不创建会话或游标 |
| `query.compile` | AND/OR/NOT、短语、字段与已支持顶层 site/filetype 过滤；未索引字段明确拒绝 |
| `document.ingest` / `document.read` / `document.delete` | 显式导入、读取与按文档删除；导入 URL 为元数据，不是任意服务器文件读取 |
| `document.range` / `find` | UTF-8 字节窗口续读、精确查找和上下文 |
| `evidence.create` / `evidence.read` | 快照、权限与字节区间复核；`semantic_support=not_evaluated` |
| `capabilities` / `stats` | 当前运行能力与用量，不发布尚未实现能力 |
| `fixtures.*` / `fixture.ingest` / `backup.create` | 保留离线 fixture 和任务路径；备份强制隔离，没有安全恢复发布流程 |

核心先校验搜索参数再创建会话；非法请求不能污染未过期会话额度。原引号指令、ASCII/Unicode 边界和精确字面回归继续执行。中文索引/BM25 与原文片段为既有能力，本轮保留回归；实际召回路径及不完整结果必须明确标注。

删除对象的正文、片段、证据和检索计数不可继续发布；无关文档可以保留可用会话。真实权限变化仍需失效或重新鉴权，在途任务不得复活删除对象。当前业务 schema 为15，增加共享读取的派发超时与加入关闭状态，并保留 schema14 的独立 URI 删除比较键、schema7 的站点发布/URL绑定/trap、schema8 的页面响应和派发身份、schema9 的任务摘要/robots引用、schema10 的持久队列、schema11 的联网会话/版本/事件及后续 HTTP 元数据、来源发布、主体撤权和共享读取结构。子串辅助索引通过既有 `fts_tokenizer_version` 兼容标记单独迁移；升级前须停止旧 Core，不支持新旧格式进程混用。原 schema1、两种历史schema2及后续增量迁移继续保留，实际执行覆盖须查本轮验证记录，不能据版本号推导全部升级路径已经验收。

## worker RPC 与错误

核心经独立令牌的回环 `POST /rpc` 调用 `health`、`search.multi`、`fetch.extract`、`fetch.cancel` 等内部操作。批量读取的 `fetch.extract` 携带唯一 `attempt_id`、`max_requests` 和 `max_content_bytes`，响应带 `usage.external_requests` / `content_bytes`。worker 再次校验来源与网络目标，不写数据库，也不提供公开的代理/网段放行接口。生产代理、远程 DNS 放宽、非空 allow_cidrs、无鉴权 MCP HTTP 被拒绝。

内部 `map.fetch` 每次最多执行一个 HTTP 请求，不自动跟随重定向或软跳转。参数绑定 `attempt_id`、站点清单哈希、目标、种类、期限和单跳预算；内容请求还必须带有效 robots 记录。Worker 在真正派发前再次核验审核和 robots，响应带实际用量及准入决策；核心严格验证回执并负责持久化、下一跳和删除/权限屏障。没有 HTTP 响应的明确拒绝允许 `http_status=null`，不能因此丢弃原拒绝错误或误报已经收到响应。

crawl 的 `kind=page` 回包返回受限的 `page`、`publication` 与同响应 `discovery`。失败响应若已观察到适用禁止声明，可携带只收紧发布的 `publication_observation`；该记录不授予请求、索引或摘要权限。`fetch.extract` 的普通读取共享页面策略；两种路径都必须在核心通过结构、URL、状态、用量及版本复核。

`fetch.extract` 另以有界 `redirect_publication_observations` 保存 HTTP/软跳转的限制性记录，每项绑定 `url/http_status/publication`。最终正文策略与逻辑入口规则分别保存，内核只接受实际已到达的合法响应身份；未请求或被拒绝的目标不是发布授权。

合同错误保持 `E_SCHEMA`、`E_CONTRACT`、`E_CONTRACT_LIMIT`。来源错误为 `E_SOURCE_CONFIG` / `E_SOURCE_REVIEW`；发现路径另报告站点审核、清单不一致、范围或 robots 拒绝等独立原因。其余网页、权限、游标、证据、任务和隔离错误通过统一信封报告。每个新增错误的具体状态码与行为以本轮 API 回归绑定，不将候选实现视为冻结的完整 v1 合同。

## 0.1.5 的存量发布与 trap 可观察状态

`job.get` 的 crawl 结果新增 `trap_policy`，包含 `bzs-crawl-trap-1` 版本、固定阈值、任务期观测数量及拒绝边计数。`crawl.edges` 对 trap 拒绝项返回 `trap`：版本、规则、原因、分类族 SHA-256、观测值与阈值；被派发前复核撤回的边会更新 `followed=false` 和原因。URL 本身不因分类而改写。阈值和例外见 [SITE_CRAWLING](SITE_CRAWLING.md#专门抓取陷阱规则)。

新任务创建时固定 `trap_policy`。旧 schema 6 任务在首次恢复调度前可能返回 `null`；幂等重提不会单独补建策略，已结束且不再调度的旧任务可以一直为 `null`。恢复时补建策略不代表旧有全部准入地址重新执行了新规则。

快照信息/`web.read` 的 `site_publication` 返回版本、`mode`（`allowed/links_only/withdrawn`）、当前代际和 artifact 代际。`read_many` 回执的同名对象返回当前版本/模式/代际；crawl 条目及回执通过当前 `indexed`、`publication` 和 diagnostics 表达站点限制。正文读取状态和快照身份按原含义保留。显式 `searchd --sites` 在没有 Worker 的启动中同样生效；`search` 和 `search.page` 无需新的网页请求就会执行收紧后的上限。

`state` 与 `partial` 分别表达终结状态和覆盖完整性：发生失败跳的任务可以 `state=failed, partial=true`，其中成功条目的快照仍可读取；被有界规则截断的无失败前沿可以 `state=succeeded, partial=true`。未知请求不自动重发，幂等调用返回原任务和累计用量，不把轮询用量相加。

升级后缺少可靠历史链的旧网页结果可能需要新查询或完整重读，owner 原正文和证据保留。完整配置与迁移边界见 [SITE_PUBLICATION](SITE_PUBLICATION.md)。

## 页面与 robots 条件重验证（0.1.9）

已有 `web.read` 刷新、`read_many.submit` 和 `crawl.submit` 内部可使用条件请求，公开参数与MCP工具不增加私有validator输入。完整且可重用的200表示由核心保存精确URI/profile、快照及缓存元数据身份；304只复用该已保存正文，提交时再核对当前表示、删除、租约和发布限制。结果/快照收据可带 `revalidated=true`，但不保证每次刷新都走304。

已知用量的直达304计1个实际HTTP请求、0正文响应字节；具有完整路由授权的N跳304计N+1次请求，并计入已消费的跳转响应体，详见[跳转缓存](HTTP_REDIRECT_CACHE.md)。未知执行在持久任务中按既有保守预留结算；普通直接读取没有该预留账本，物理回执保留 `known=false` 和 `null`，订阅者仍只报告零增量。未经绑定、validator冲突、缺少合法路由证明的跳转304或并发替换失败，不在同一次授权中额外补抓完整GET。未保存完整可重建链接的crawl页面采用原完整请求路径。

robots过期记录仅可作为绑定旧表示重新验证，不能直接放行正文。新304必须保留相同rules/body/清单身份并取得正有效期；较新的拒绝不会因旧记录仍被引用而在回收后消失。schema12的元数据与容量记账、完整合同及保守缓存子集见 [HTTP_REVALIDATION](HTTP_REVALIDATION.md)。

## 0.1.10 接口与持久状态

`search.modes`接收空参数，离线返回fast/balanced/deep预算。`web.search`和`web.search.start`可选`mode`、`max_sources`与更紧的现有预算字段，返回`effective_mode/effective_budget/omitted_sources`；显式sources超上限拒绝，默认选择按清单顺序截断并报告。无mode保持旧默认和持久幂等身份。具体上限和单调/持久期限边界见[搜索预设](SEARCH_MODES.md)，所有自动读取/模型/浏览器预算为0。

管理员操作`principal.revoke`只接受`subject`，持久阻断该主体的全部令牌、新旧数据访问和在途发布；重复撤权幂等，自我撤权拒绝，现阶段没有重新启用接口。CLI为`revoke-principal SUBJECT`；MCP默认不暴露，只有stdio显式`--admin-tools`可启用。HTTP MCP共享Core凭据的入口拒绝此开关。权限与计费细节见[主体撤权](PRINCIPAL_REVOCATION.md)。

同 owner 兼容 `read_many` 在派发事务中可共享一次实际请求，schema15 进一步允许兼容新任务首项迟到加入。各订阅者保持独立任务身份/回执与取消，原付款者承担实际或未知用量，付款者取消但仍有其他有效成员时归属不变、原尝试仍可接纳兼容成员。订阅者 `associated_*` 仅表示关联，不是可相加的消耗；压缩后归属仍保留。每次物理尝试最多累计 32 个成员，详见[共享读取](SHARED_READS.md)。

原始HTTP缓存字段与ETag/Last-Modified分别传递。所有响应路径的no-store/收紧都不能留下较宽旧缓存资格；`web.read`与`read_many`对no-store的200全量响应交付一次性瞬时正文（`transient="no_store"`，无快照/复用元数据），不会先落盘再删除；crawl/map与304重验证仍报`E_CACHE_POLICY`。截断正文的既有取证路径保留，但不获得新鲜缓存资格。失败不隐式陈旧回退。`cache`解释年龄、期限和原因，瞬时结果无`cache`对象，详见[HTTP新鲜度](HTTP_FRESHNESS.md)。

schema13在12基础上组合来源发布规则、主体撤权和共享读取结构，保留原数据与账本。来源清单显式激活持久政策，旧缓存/冻结会话与在途来源回复每次重查；后续放宽不复活旧摘要。真实旧库升级、旧HTTP字段默认不新鲜、备份继续隔离的实际证据见当前报告；版本号不等于所有历史路径或安全恢复已经验收。

## schema14：URI 删除比较与兼容

`document.delete` 的目标仍是指定文档 ID；其他别名文档和历史证据不合并。schema14 新增派生删除键，按 owner 统一 unreserved percent 编码、主机/默认端口等有限等价；reserved 编码与查询结构保持区分。旧墓碑也参与比较。`web.read` 的缓存、直接/跳转条件请求及在途200/304提交复核别名删除序号，重导入不恢复旧表示资格。

旧精确直达表示没有持久 fence 时只可用保守序号0检查，任何等价删除历史均阻断；跳转表示仍需要真实旧 fence。`cache_only` 失效时报 `E_CACHE_STALE`，本次已授权 `cache_first` / `refresh` 可执行完整读取，不用失效的旧验证器。来源政策、外部用量结算和任务租约保持。

升级前停止旧 Core；升级事务保留原业务列、快照、证据与幂等身份，只支持 schema13 及以前格式的旧程序不能打开 schema14。完整有限规则与历史读取边界见 [URI 删除身份](URI_DELETION_IDENTITY.md)。MCP的搜索说明已与当前部署来源状态对应，工具名称和参数保持。

## schema15：持久批读迟到订阅

`read_job_items` 增加 `dispatch_timeout_ms INTEGER NOT NULL DEFAULT 0` 和 `sharing_closed INTEGER NOT NULL DEFAULT 1`。新物理 lease 派发时捕获超时上限并开放加入；`execute_read` 实际开跑还可按剩余 deadline 收紧超时。旧 schema14 运行项保留 0 / 1，不接纳迟到订阅，原租约、成员、预留与完成/结算路径保持。

迟到成员继承原发布捕获和租约，不新增物理请求或改变付款人。部分索引只包含 running/open/正请求预留的付款项；每次最多评估 32 个返回候选，不承诺 SQL 只扫描 32 行。加入与关闭在 `BEGIN IMMEDIATE` 事务中协调，关闭对同一尝试不可逆，时钟回拨不会重新开放。持久租约不证明原执行进程存活；执行丢失仍按原租约到期记录未知，不自动重发。

升级前停止旧 Core，旧二进制打开 schema15 返回 `E_SCHEMA_VERSION`，不支持新旧进程混用；备份隔离和永久幂等身份保持。完整规则见[共享读取](SHARED_READS.md)，实际升级与回归范围见[本批验证记录](../validation/shared_read_late_20261024/README.md)，不由版本号推导验证成功。

### 2026-10-27 storage maintenance recovery

Local frozen searches have no idempotency key. Their receipt exists while the
session/stream payload is retained; after expiry, bounded lazy settlement can
remove zero-usage orphan receipts. A collected local session returns 404.
Keyed web search receipts and job identities retain their original permanent
replay contract. Each settlement removes at most 512 old local receipts;
a frozen search has two settlement phases. Charged/unknown work is not collected.

`stats` now exposes `archive_bytes`, `payload_bytes`, `quota_payload_bytes` and
`payload_over_quota`. Snapshot payload and archive JSON share the existing
owner byte quota; substring tokens still have their independent quota.
`content_bytes` remains snapshot-only. None of these logical counters is the
physical SQLite file size. Archive bytes are calculated over retained owner
rows, not maintained in a second mutable business ledger.

`jobs.compact` returns `archive_quota_blocked_jobs`; if a summary cannot fit,
its detail rows and permanent key are preserved. Operators must resolve capacity,
not treat the blocked record as compacted. After a public compaction commits,
incremental stores attempt at most 128 free pages of physical reclamation and
report `storage_reclamation` (`completed` or `deferred`). Live WAL readers can
delay file shrink. Existing NONE/FULL databases return
`requires_offline_conversion`: this batch does not implement a legacy full
conversion tool and never runs a full VACUUM during startup. Backup quarantine
and the business schema15 remain unchanged.
