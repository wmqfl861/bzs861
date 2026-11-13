# 直接读取的进程内共享请求

同一个 `WebService` 内，兼容的并发 `web.read` 可以共用一次 `fetch.extract` 和一次正文或 304 元数据提交。每个调用随后独立取得自己的文本窗口，并重新通过权限、删除和发布检查。该能力也适用于经原 HTTP、CLI、MCP `read_url` 入口到达这一个 `WebService` 的调用；入口参数和同步返回方式保持不变。

本增量推进 `CACHE-01 / B-24 / FT-032`，数据库仍为 schema15，版本仍为 `0.1.10`，没有新增迁移或直接读取任务表。它与[持久批读共享请求](SHARED_READS.md)各自协调，不把普通直接读取转成 `read_many` 任务。

2026-10-25 接续增加了可选 `read_id` 及[成员取消合同](DIRECT_READ_CANCELLATION.md)。带句柄与原无句柄调用分组，完整身份、付款和每成员屏障保持；下文原 Worker 参数合同指无句柄路径，显式控制路径使用独立 RPC。

## 加入身份与容量

只有已经进入网络读取路径、取得有效当前请求配置证明的调用，才能登记或加入共享组。内存登记表属于一个 `WebService` 对象；同一进程中的另一个 `WebService`、另一个 Store 所属的服务实例或另一个 Core 进程都不会加入它。

共享键是下列完整身份的规范 JSON 摘要，不能只凭 URL 或快照 ID 相同合并：

| 身份部分 | 精确内容 |
| --- | --- |
| owner | 当前调用主体。不同 owner 不共享。 |
| 实际 Worker 请求 | `normalize_iri` 后的精确 URL、`timeout_ms`，以及存在时的完整 `conditional` 或 `redirect_conditional` 参数。 |
| 完整 `publication_fence` | 主体权限代际 `epoch`、捕获的全局删除序号 `sequence`、目标 URL 删除代际 `generation`、当前全局页面发布序号 `publication_sequence` 和当前快照 ID `snapshot_id`。 |
| 当前缓存捕获 | 已取到的缓存快照 ID，以及其完整 metadata 的规范 JSON 摘要；无缓存也有明确的空身份。 |
| 当前请求配置 | 本次 `fetch.profile` 返回并通过验证的 `request_profile`。 |

`publication_sequence` 是全局水位，不是目标页面专属代数。其他页面的限制变化也可能使后来的调用得到不同共享键。304 可以在同一快照 ID 上更新 HTTP metadata，因此共享键另外绑定 metadata 和实际条件读取参数。权限、删除、当前快照、缓存证明或请求配置变化后，不借用旧组的捕获。

精确 URL 不使用搜索去重键，也不使用[有限 URI 删除身份](URI_DELETION_IDENTITY.md)扩大共享范围。`start_byte`、`max_bytes` 和调用者的 `max_age_s` 属于各自的输出或新鲜度要求，不要求彼此相等；`freshness` 先决定是否进入网络路径。只要最终实际网络参数及其余身份完全相同，网络路径上的这些调用才可能共享。

每组最多累计 **32 个成员**，包含最初的付款成员。等待超时不会回收成员槽位。每个 `WebService` 最多登记 **64 个仍开放加入的组**；这个数字约束可加入的内存登记表，不是全进程的网络并发上限。组已满、登记表已满或证明不足的调用继续走原独立读取，不替换已有组，也不因容量不足扩大共享身份。HTTP 业务接纳仍受[请求执行与背压](HTTP_EXECUTION.md)约束。

## 请求配置证明与旧 Worker 兼容

网络 miss 或 refresh 路径先调用本地 `fetch.profile` RPC，RPC 超时为 2,000 ms。合格证明必须只有 `version` 与 `request_profile` 两个字段，版本为 `bzs-http-freshness-1`，profile 为 64 个小写十六进制字符。此 RPC 本身不访问来源。

这份证明是共享优化的条件。旧 Worker 不支持该方法、Worker 不可用，或证明格式无效时，直接读取保持独立执行；不会借用进程记忆中的旧 profile 来加入已有组。缓存命中原有的 profile 核对属于另外一条路径，继续服从[HTTP 新鲜度合同](HTTP_FRESHNESS.md)，不能据此把无效的共享证明当作缓存授权。

Worker 返回后，若最终累计成员数大于 1，能够提交的成功正文或 304 结果必须携带合法 `http_freshness`，其中实际 `request_profile` 必须等于加入时的证明。缺少证明返回 `E_WORKER_PROTOCOL`；证明合法但配置已变化返回 `E_READ_PROFILE_CHANGED`。核心不会用旧正文冒充成功，也不会自动重新抓取。

只有一个成员时，原来允许的旧 Worker 响应合同保持；不能证明共享不等于旧独立读取被禁用。响应显式给出的 HTTP、用量或发布证明仍须通过各自校验，单成员不豁免畸形字段。

## 派发、关闭加入与等待

第一个调用是固定付款成员，由它执行一次普通 `fetch.extract`。其他兼容调用只等待这次尝试。网络调用和等待期间都不持有协调锁或 Store 事务。

`fetch.extract` 返回时立即从登记表移除该组，固定正常完成时的累计成员数，然后才继续解析、验证和提交；异常返回也关闭加入。关闭后保留已有成员使用的组对象，直到它们各自结束。新的调用不能读取一份已完成的内存结果或失败缓存；它按自己的缓存与网络路径重新判断。关闭加入也不代表正文已提交。

Worker 回调可能在同一线程内重入 `web.read`。若找到的组由当前线程派发，重入调用保持独立执行，不等待自己，也不替换原组。这一分支同样不复用外层调用尚未提交的 304。

普通直接读取的超时参数保持默认 20,000 ms、范围 1,000–60,000 ms。付款成员传给 Worker 的 RPC 等待上限仍为 `timeout_ms + 5,000 ms`；订阅成员在加入后使用 `steady_clock`，以自己的 `timeout_ms + 5,000 ms` 为等待上限。这个等待上限不是包括前置 profile、Store 访问和 HTTP 传输在内的端到端期限。

订阅成员等待超时返回 `E_WORKER_TIMEOUT`，不自动重发，不发送 `fetch.cancel`，也不终止其他成员的执行。它仍占原累计槽位，物理尝试继续按原 Worker 超时和提交屏障结束。超时错误中的 `members` 是生成该错误时已加入的累计人数；正常完成时则使用关闭加入后固定的人数。

无句柄调用不提供单成员取消；可通过新的 `web.read.prepare` 准备一次性句柄，并用 `web.read.cancel` 控制带句柄读取，详见[取消合同](DIRECT_READ_CANCELLATION.md)。没有改用 `job.control`。客户端断连、成员错误或等待超时不能解释为物理请求未发生。

## 保留普通直接读取的 Worker 合同

发给 Worker 的参数仍为原直接读取的 URL、超时和可选条件读取证明。核心不附加持久批读的 `attempt_id`、请求/字节 grant、任务 deadline 或租约字段；返回的共享 `attempt_id` 是本次进程内执行的关联标识，不是 Worker 持久租约。

`max_bytes` 仍只控制返回的 UTF-8 文本窗口，不能当作网络下载预算。普通 direct 的 60 秒参数上限和原有截断响应处理保留，没有套用持久批读的 55 秒上限或其硬预算执行方式。截断正文仍须满足原有明确的限制发布策略，不能建立可复用 validator 或重定向表示证明，也不能解除旧发布限制。

每次尝试沿用 Worker 实际返回的请求与字节收据；共享不会增加出站授权、建立持久预算预留或产生自动重试次数。

## 一次提交与每个成员的交付屏障

付款成员只解析并提交一次成功表示，304 的绑定校验和 metadata 更新也只执行一次。Store 提交继续验证 owner、主体代际、删除代际、所有相关 URL 别名、当前或历史发布限制，以及 304 所绑定的当前快照和 metadata。正文、索引和该表示的 metadata 由原 Store 事务原子提交；缓存失效、限制性观察和成员读取另有各自事务，不能把它们误认为只有一次数据库访问。

组中只保存窗口所需快照字段的独立序列化 JSON，不把可变 JSON 对象交给并发成员共用。各成员独立重建这份描述，正文由 `Store::range` 读取；不再复制正文与发布片段的聚合 JSON，避免给合法旧响应新增聚合字节或节点上限。各成员使用自己的 `start_byte`、`max_bytes` 和 `max_age_s` 构造响应。付款成员的窗口参数错误不会使已经提交的表示失败，其他成员的有效窗口仍可返回；订阅成员的窗口错误也不污染付款成员。

每个网络结果窗口都携带内部 `bzs-http-read-result-1` guard，进入 `Store::range` 的 **`BEGIN IMMEDIATE` 事务**。事务在同一观察点内完成以下检查和投影，并与其他 Store 句柄的写事务串行：

1. 主体仍有效，当前 `epoch` 等于该调用的捕获，原 URL 删除代际未变化；快照仍属于当前 owner 且正文仍可读取。
2. 实际读取快照的 URL 和完整 metadata 摘要与提交所得描述一致。相同快照 ID 上后来发生的 304 metadata 更新会返回 `E_REVALIDATION_STALE`，不把两次物理尝试的字段或收据拼在一起。
3. 用捕获的删除序号重新检查原 URL、最终 URL 以及全部中间重定向 URL 的有限删除身份。删除后再重新导入也不能使旧调用重新取得交付资格。
4. 重新计算当前页面与站点发布投影，并合入快照 `page_publication_sequence` 之后的发布限制历史。即使后来出现允许响应，旧结果也不能恢复已经被历史限制封闭的发布资格。

事务把最新 `indexed` 和 `site_publication` 投影返回给窗口响应。它不把 `noindex`、`links_only` 等发布限制擅自改成所有 owner 历史正文都不可读取的规则；按 ID 访问、证据和引用继续使用原权限与发布合同。此事务不执行 Worker RPC 或等待其他成员。

因此，共用同一份已提交表示不承诺所有成员得到相同的最终成功状态。提交后发生撤权、删除、metadata 更新或发布限制时，稍后的成员仍须独立通过当时的屏障。物理请求用量也不会因为某个成员交付失败而被清零。

## 用量、关联收据与失败信封

网络路径结果增加下列字段；即使因为不兼容而独立执行，也有自己的 `payer`、`members=1` 收据。`shared_read` 的存在本身不证明发生了多人合并。

| 字段 | 含义 |
| --- | --- |
| `usage.scope` | 固定为 `direct_read`。 |
| `usage.external_requests` / `content_bytes` | 归属于当前调用的增量用量；付款成员承担物理用量，订阅成员为 0。未知付款用量保留 `null`。 |
| `usage.model_calls` | 固定为 0。 |
| `usage.known` | 付款成员是否有可靠物理收据；订阅成员的增量 0 为已知，不代表物理尝试已知。 |
| `shared_read.attempt_id` | 本次尝试的 48 位小写十六进制关联 ID。 |
| `shared_read.role` / `members` | `payer` 或 `subscriber`，以及本次尝试累计成员数。付款归属不转移。 |
| `shared_read.scope` | 固定为 `process`；实际共享仍局限于同一个 `WebService`。 |
| `shared_read.physical_usage` | 整次物理尝试的 `external_requests`、`content_bytes` 和 `known`，供各成员关联同一次工作。 |

同组成员的 `physical_usage` 指向同一份用量，**不能跨成员相加，也不能再加到 `usage` 上**。例如订阅者可以同时具有已知增量 `0 / 0` 和关联物理用量 `null / null / known=false`；这不是零次出站成功。

缺少 Worker 用量收据、RPC 未得到可靠回包或成员等待超时时，物理用量保持未知。显式用量必须是非负整数，不能以零请求携带正字节；成功正文不能声称零物理消耗，普通直接 304 必须报告一次请求和零正文字节。畸形收据返回协议错误，不能把无法确认的物理用量填成零。

缓存命中的成功响应为已知零增量，且没有 `shared_read`。网络共享的成功成员仍为 `from_cache=false`，即使结果来自条件读取后的 304；`cache` 描述该表示的 HTTP 新鲜度，不能把订阅者误标成缓存命中。

已经进入这次网络尝试、由直接读取路径包装的失败通过 C++ `Error.details` 保留 `usage` 和 `shared_read`，包括有效收据对应的失败与未知执行。HTTP 失败信封继续保留原错误码、消息和 HTTP 状态，并在详情存在时输出这两个字段；前置校验或其他没有执行详情的旧错误不制造共享收据。2026-10-25 接续补齐了外层 `Service::dispatch` 最后主体复查：`web.read` 已有用量详情在撤权失败时仍保留。传输断开、进程退出或无回包仍不能保证客户端取回收据，缺少回执不能解释为零消耗或安全自动重发。

Python `CoreError` 仅从失败 body 提取 object 类型的 `usage`、`shared_read`。MCP `_call` 保持 `ToolError` 和原错误文本，有详情时才在后面追加一行紧凑、保留 Unicode 的 JSON；无详情错误文本保持原样。成功的 `read_url` 仅在 Core 返回字段存在时原样透传 `usage`、`shared_read`、`cache`，不重算未知值、角色或成员用量。

## 缓存与限制性观察

共享不放宽[HTTP 新鲜度](HTTP_FRESHNESS.md)、[条件重验证](HTTP_REVALIDATION.md)或[重定向缓存](HTTP_REDIRECT_CACHE.md)合同。`no-store` 的 200 全量响应按共享读取模型交付瞬时正文：同一 flight 的已准入成员获得同一物理正文与 `same_physical_receipt` 语义，付款方一次计费，结果带 `transient="no_store"` 且无快照身份；不变量是零留存（不供未来请求复用），而不是零成员交付。窗口化在内存中投影（快照/发布投影等价，逐成员复核撤销/删除屏障），不会因为已有订阅者而持久保存禁止存储的表示；304 与既有成员失败路径仍按原合同拒绝或计费。

缓存限制或路由变化导致的失效绑定本次原缓存快照与 metadata 摘要，迟到失败不能撤销另一调用后来提交的新表示。用量或 profile 收据错误也不能绕过独立有效的缓存撤销和限制性发布观察：已单独验证的原地址、中间跳或最终表示限制继续在原权限和删除屏障下生效。无效正文不能发布；有效限制也不能借此变成放宽旧策略的许可。

## 验证位置与未完成边界

实现位于 [`WebService::read`](../src/web.cpp)、[`Store::range`](../src/store.cpp)和[发布屏障](../src/publication.cpp)；正式并发回归位于 [`tests/direct_shared.cpp`](../tests/direct_shared.cpp)，MCP 透传回归位于 [`python/tests/test_mcp_read.py`](../python/tests/test_mcp_read.py)。本批基线、修正过程、独立核对和最终门禁以[验证记录](../validation/direct_shared_20261024/README.md)及[机器报告](../validation/direct_shared_20261024/final-report.json)中的实际输入、命令和结果为准。本文不预填未来 CI、运行成功数量或发布 SHA。

本能力不覆盖跨 `WebService` / 跨进程协调、direct 与持久 `read_many` 混合共享、map/crawl/search 共享或重启后的执行账本。可选句柄的单成员取消已由[后续增量](DIRECT_READ_CANCELLATION.md)实现；完整跨通道取消矩阵尚未签收。进程内 `attempt_id` 和关联收据不构成可恢复租约；重启或响应丢失后不能凭它断言执行未发生、恢复用量或安全自动重发。持久执行未知恢复、独立撤销 journal 和长期故障演练仍按原蓝图推进。

来源选择、审核与启用、原 S2 范围和冻结决定、validation 入库、备份恢复隔离、永久幂等身份保留及抓取适配器决定保持。默认仍为 **17 条登记来源 / 0 启用 / 0 站点**。原 **83 个工作包 / 161 个验收项 / 10 个切片**及 **37 partial / 46 not_started / 0 accepted** 保持；完整 `B-24` 与 `CACHE-01` 未签收。受控 Worker、内存协调和 SQLite 并发回归只证明各自场景，不替代真实来源批准、生产负载、任意故障或完整产品验收。
