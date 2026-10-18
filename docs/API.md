# searchd API（0.1.1 S1 review candidate）

所有业务请求为 `POST /api/v0/call`，通过回环 HTTP 与 Bearer token 调用。Host/Origin、请求大小和超时检查继续保留。MCP、Python 启动器与 C++ CLI 复用同一核心；候选路径的真实验证结果见 [本轮报告](../validation/s1_review/test_report.json)。

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

## 本地操作

| 操作 | 说明 |
|---|---|
| `search` / `search.page` | 字符串或当前 AST 子集；冻结结果与稳定游标，页大小和候选/结果上限由服务端检查 |
| `query.compile` | AND/OR/NOT、短语、字段与已支持顶层 site/filetype 过滤；未索引字段明确拒绝 |
| `document.ingest` / `document.read` / `document.delete` | 显式导入、读取与按文档删除；导入 URL 为元数据，不是任意服务器文件读取 |
| `document.range` / `find` | UTF-8 字节窗口续读、精确查找和上下文 |
| `evidence.create` / `evidence.read` | 快照、权限与字节区间复核；`semantic_support=not_evaluated` |
| `capabilities` / `stats` | 当前运行能力与用量，不发布尚未实现能力 |
| `fixtures.*` / `fixture.ingest` / `job.*` / `backup.create` | 保留离线 fixture 和任务路径；备份强制隔离，没有安全恢复发布流程 |

核心先校验搜索参数再创建会话；非法请求不能污染未过期会话额度。原引号指令、ASCII/Unicode 边界和精确字面回归继续执行。中文索引/BM25 与原文片段为本轮增量，实际召回路径及不完整结果必须明确标注。

删除对象的正文、片段、证据和计数不可继续发布；无关文档可以保留可用会话。真实权限变化仍需失效或重新鉴权，在途任务不得复活删除对象。schema 3 迁移承接两种历史 schema 2；具体覆盖见本轮报告。

## worker RPC 与错误

核心经独立令牌的回环 `POST /rpc` 调用 `health`、`search.multi`、`fetch.extract` 等内部操作。worker 再次校验来源与网络目标，不写数据库，也不提供公开的代理/网段放行接口。生产代理、远程 DNS 放宽、非空 allow_cidrs、无鉴权 MCP HTTP 被拒绝。

合同错误保持 `E_SCHEMA`、`E_CONTRACT`、`E_CONTRACT_LIMIT`。来源错误为 `E_SOURCE_CONFIG` / `E_SOURCE_REVIEW`；其余网页、权限、游标、证据、任务和隔离错误通过统一信封报告。每个新增错误的具体状态码与行为以本轮 API 回归绑定，不将候选实现视为冻结的完整 v1 合同。
