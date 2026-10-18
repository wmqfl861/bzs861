# 有界站点发现与 robots 准入

本页描述 0.1.3 的 `map.submit` 实现切片，对应蓝图 v1.2 的 ROBOTS-01 / MAP-01。它从明确的入口文档发现 URL，保存候选和发现来源。Sitemap index 可以产生后续发现文档；Sitemap urlset、RSS、Atom 和 HTML 链接产生候选。**候选没有因为本次发现而获得正文快照或索引。** 完整正文递归抓取仍属于 CRAWL-01，公开索引与摘要规则属于 CRAWL-03。

## 站点审核与部署

`config/sites.json` 是独立的站点发现登记。默认内容为：

```json
{"version":1,"sites":[]}
```

正常搜索使用的 `sources.json` 和站点发现登记分别审核。一个搜索来源的审核不能批准任意搜索结果网站，robots 规则也不提供站点访问授权。随包 17 条搜索来源仍全部禁用，没有预先批准的真实发现站点。

每条站点登记包含 `id`、`enabled`、唯一 `origin`、`path_prefixes`、`review` 和 `limits`。`origin` 明确协议、主机和有效端口；同源不同 `site_id` 不能拆开请求额度。`review` 明确免费入口、自动访问批准、审核人、审核日期及有效期、条款 URL、缓存和链接再发布政策。`limits` 包含并发数、最小间隔、请求配额和配额窗口。这是部署方的可信配置，不接受普通 API 调用者提交 `approved=true`、替换审核时钟或放大硬限制。

登记最多 100 个站点，每站点 1–32 条路径。当前 `max_concurrency` 固定为 1，`min_interval_ms` 范围为 0–3600000，`request_quota` 为 1–1000000，`quota_window_seconds` 固定为 86400。执行还需要 `status=approved`、`payment_scheme=no_payment`、`automated_access=approved`、`cache_policy=allowed_with_limits`，并将 `redistribution_policy` 明确设为 `links_only` 或 `allowed_with_limits`。这些值记录部署方已经完成的审核，不自动证明审核事实。

通过 `searchd --sites FILE` 与 `bzs-worker --sites FILE` 指定同一文件。两端独立加载审核内容，并比较该文件的精确 SHA-256 身份。文件差异、审核过期和范围不匹配必须明确拒绝，不能回退为交互读取。`bzs init` 会为新安装创建空站点文件；旧安装缺少 `sites` 配置时继续使用空登记。添加配置不需要重新生成令牌。

启动器的 `worker_args` 不允许再次指定 `--sites`（包括等号形式及长选项缩写），此类配置会在启动任何进程前明确拒绝；站点文件统一通过 `config.json` 的 `sites` 字段选择。

```json
{
  "sites": "/absolute/path/to/reviewed-sites.json"
}
```

上面的片段是现有启动器 `config.json` 的新增字段示例，不是完整配置。`map.sites` / MCP `list_sites` 可以查看实际加载的登记和审核状态；查看不会批准或启用站点。

## 提交与读取结果

```json
{
  "operation": "map.submit",
  "params": {
    "site_id": "operator-reviewed-site",
    "seeds": ["https://reviewed.example/docs/sitemap.xml"],
    "scope_path": "/docs/",
    "idempotency_key": "docs_map_1",
    "max_requests": 30,
    "max_bytes": 16777216,
    "max_urls": 200,
    "max_depth": 3
  }
}
```

域名和站点 ID 是占位示例，必须替换成部署中已审核的配置。

| 参数 | 默认值 | 上限与含义 |
|---|---|---|
| `site_id` | 必填 | 已登记且启用、审核当前有效的站点 |
| `seeds` | 必填 | 1–20 个显式发现文档 URL；逐个复核范围 |
| `idempotency_key` | 必填 | 同一 owner 内相同参数复用原任务；改参数复用键拒绝 |
| `scope_path` | 已登记路径范围 | 只能缩窄范围；不能扩到另一 origin 或未审核路径 |
| `timeout_ms` | 600000 | 1–3600000；创建起的总期限，暂停不延长 |
| `per_request_timeout_ms` | 20000 | 1–55000；单跳剩余期限，同时受总期限约束 |
| `max_requests` | 100 | 1–1000；包含 robots 和所有 HTTP 跳转的请求尝试 |
| `max_bytes` | 16777216 | 1–67108864；应用实际读取的解压后响应体字节 |
| `max_urls` | 1000 | 1–1000；种子、发现文档和候选共用去重 URL 集合上限，不代表页面正文数量 |
| `max_depth` | 3 | 0–8；种子深度为 0，每次发现加 1，同时限制候选和后续发现文档；不是 URL 路径层数 |

使用 `job.get` 读取状态和累计预算，使用 `job.results` 分页读取候选。每项结果包含 `url`、`parent`、`source`、`depth`、`path_depth` 和 `body_read=false`。`body_read` 表示本次 map 没有生成正文读取收据；历史来源可能早已有独立的读取快照。历史候选只来自当前 owner 可见且未删除的数据，并占用同一 URL 上限。

`job.results` 的 map 分页参数为 `offset` 与 `limit`，每页最多 1000 项；`next_offset` 为空表示当前没有下一页。执行过程中候选只在核心事务提交后出现，结果里的网页字段仍是不可信外部数据。MCP `map_site` 只转发参数，复用 `get_job`、`job_results` 和 `control_job`；不会在适配层补造审核或扩大预算。

## robots 与每跳请求

爬虫标识固定为 **BzsBot**，HTTP User-Agent 说明有界站点发现用途。规则实现以 [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) 为依据，并固定为可追溯的本项目策略版本。它合并匹配的专属 group，只有没有专属 group 才使用 `*`；规则比较包含路径与查询，不包含 fragment。百分号形式按比较规则规范化，字面 `%2A` / `%24` 与 `*` / 末尾 `$` 控制符分开，等长规则优先 allow。

本版明确采用保守处理：无法安全解析的编码或适用规则拒绝放行；规则体上限为 512 KiB，最多 32768 条规则，完整缓存记录最多 768 KiB。最长规则的比较权重为规范化 URI 比较表示中的字面 octet 数，不计通配与终止控制符。固定策略及其边界由 fixture 回归，不能据此宣称与任何搜索引擎的全部扩展行为相同。

核心持久保存 robots 状态和版本，实际内容请求前由 Worker 再核验目标、站点身份、期限和规则。每个 HTTP 跳单独派发；跳转回到核心调度后重新检查范围与准入。`web.read` 和静态 `read_many.submit` 保留原有显式读取合同，自动发现任务不能把自己转换成这两种操作绕过检查。

| robots 观察 | 本版处理 |
|---|---|
| 完整、可解析的 2xx 文本 | 保存适用规则；允许缓存最多 24 小时，并受更短 HTTP 新鲜度限制 |
| 404 / 410 | 本版视为未提供规则，保存有期限的 `allow_all`；仍需站点审核及出站检查 |
| 401 / 403 | `deny_all`，负缓存 1 小时，并保留站点冷却 |
| 429 | `deny_all`，遵循 `Retry-After`；至少冷却 10 分钟，缓存最长 24 小时 |
| 不可达、5xx、截断、206、非法类型或损坏规则 | `deny_all`，通常负缓存 5 分钟，不发内容请求 |
| HTTP 重定向 | 最多 5 个同源重定向，每跳重新调度并计费；跨源拒绝 |

缓存许可同时检查 `Cache-Control`、`Age`、`Date` 和 `Expires`。响应年龄及已耗请求时间只会缩短许可；明确过期或无效的缓存元数据、`no-store`、`no-cache`、零有效期以及无法绑定的 `Vary` 维度会改为短期拒绝并报告需要重新验证。本版没有条件请求/304 更新实现，不能使用陈旧允许记录。无效的显式缓存元数据不会静默回退为 24 小时许可。

规范化只统一协议/主机/有效端口和去除 fragment，已编码的 ASCII path/query 在规则判断与真正 HTTP 请求中保持一致。为防止路径范围歧义，发现通道拒绝点路径段、编码的点/斜杠/反斜杠和嵌套百分号编码；路径 scope 本身不允许百分号编码。该限制不改变旧版显式读取的 URL 合同。

## 预算、恢复与停止原因

请求发送前核心先持久化租约和预算预留。robots 请求、跳转响应体、失败连接尝试和成功发现都在同一任务预算内。收到完整且有效的用量后，只退回未使用的预留；请求结果未知时保留预留计费，不能因为崩溃、暂停或响应丢失重置成零。

核心唯一地维护发现队列，并在入队、派发和重定向处复核范围。去除 fragment 后去重，防止 Sitemap index 自循环；每个 origin/path 最多 4 个查询变体。URL、请求、字节或深度上限到达时保留已提交结果并报告对应停止原因。即便已处理当前队列，也只表示本次明确范围和发现方法内的队列状态，不能推导“已发现全站”。

发现存储另有全局保留上限：512 个 map 任务、20000 条资源、20000 条候选、50000 条 HTTP 跳记录和 512 条 robots 缓存记录。达到保留上限时明确停止或拒绝；本版没有自动删审计记录的 GC。过期缓存不能继续放行，且不会因为任务结束而重置已计费的站点请求窗口。

暂停、恢复、取消和授权变化沿用核心持久任务边界。未知的已派发步骤不会自动重发；取消不能收回已经送出的 HTTP 请求。发现候选与正文抓取分别计数，不以候选量推导抓取成功率或索引覆盖率。

## 验证与后续范围

本批使用受控 `.test` 站点和真实进程验证网络请求计数、robots 拒绝、跳转范围、Sitemap/Feed/HTML、历史、重启与 MCP。测试专用回环映射不能在生产模式启用；XML 解析禁止 DTD/实体展开和外部资源读取。每个发现响应最多读取 2 MiB，解析器硬边界另限制树深、元素数和候选条目；HTTP 内容编码使用现有有界解压，`.gz` 归档不会自动展开。

实际执行命令、结果、源码身份和限制以 [本批报告](../validation/s2_discovery/test_report.json) 为准。没有用合成站点替代真实来源 S0，没有新增人工 qrels 或相关性质量分数。ROBOTS-01 / MAP-01 仍按完整工作包验收要求单独判断；递归正文 frontier、CRAWL-03、会话断线重取、独立撤销日志及隔离恢复继续保留。
