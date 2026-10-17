# 启动版 API 合同 0.0.2

这是早期可执行子集，不是完整规划合同。所有业务请求都为 `POST /api/v0/call`。

头：`Authorization: Bearer ...`、`Content-Type: application/json`。Host 仅允许实际监听端口的 `127.0.0.1` 或 `localhost`。目前没有浏览器界面，所有 Origin 请求拒绝。

```json
{"operation":"search","params":{"query":"C++","page_size":10}}
```

响应包含 `request_id`、`api_version`、`status`、`result`、`errors` 和 `usage`。`usage.external_requests` 与 `usage.model_calls` 在本版始终为零，表示确实没有出站执行，不是未知费用记成零。语法/传输过大等早期错误可能返回精简错误信封。

| 操作 | params | 结果与边界 |
|---|---|---|
| capabilities | `{}` | 真实能力及未实现列表 |
| fixtures.list | `{}` | 固定测试源，不计入已审核真实来源数量 |
| fixtures.search | query | 只匹配 fixture 元数据；摘要不是已读正文 |
| fixture.ingest | fixture_id | 读取启动时批准的样例内容、提取和保存 |
| query.compile | query（字符串或 AST） | 查询 AST 与受支持的全局过滤 |
| contract.validate | kind、config | C++ 校验与规范化；返回 applied=false，不保存、不激活、不发出外部请求 |
| document.ingest | url、content、content_type、可选 title | 显式导入内容，url 是元数据，不联网 |
| document.read | snapshot_id | 固定快照与正文；每次检查用户 |
| document.delete | document_id | 移除正文、FTS、证据；使该用户旧会话及旧任务失效 |
| search | query、可选 max_candidates、limit、page_size | 创建冻结的授权本地结果集并返回第一页 |
| search.page | session_id、可选 cursor、page_size | 在固定版本翻页；cursor 绑定会话与用户 |
| find | snapshot_id、text、可选 limit | 字面查找，UTF-8 字节半开区间 |
| evidence.create | snapshot_id、start_byte、end_byte | 发布前检查字节边界；不会评价事实支持性 |
| evidence.read | evidence_id | 重新检查权限及快照可用性 |
| stats | `{}` | 当前用户的文档、快照和字节数量 |
| job.create | fixture_id、idempotency_key、可选 max_attempts、timeout_ms | 受限 fixture 单页任务，不递归爬站 |
| job.get | job_id | 进度、租约、尝试数及产物标识 |
| job.control | job_id、action=pause/resume/cancel | 状态机；在途暂停/取消可能等待租约退出 |
| backup.create | `{}` | 管理员才可使用；服务端固定目录，不接受客户端路径 |

`GET /healthz` 不要求令牌，只返回存活与运行配置，不返回内容或模型配置。

## 查询

支持 `AND`、`OR`、`NOT`、负词、括号、双引号短语、`intitle:`、`inbody:`。AND 优先于 OR。`C++`、`C#`、`std::expected` 等保留字面，不由语义扩展改写。

`site:` 与 `filetype:html|txt` 暂只支持正向顶层合取；与 OR/NOT/括号混用会明确报错，不悄悄改变含义。查询必须含文字表达式，不支持空 query 全库列举。字符串 AST 与 JSON AST 复杂度均受限制；`author`、`code` 等尚未索引的字段会报能力缺失。

对普通 ASCII 词项用 FTS5 召回并复核。FTS 使用独立的 ASCII 规范化候选副本，原始正文不变；这使混排 Unicode、组合字符与下划线的词边界和最终谓词保持一致。所有短语（包括单词短语）、中文子串、符号和复杂否定使用有界扫描。`retrieval` 与 `partial` 说明实际执行，结果不是全网索引，也不保证有界扫描中的完整召回。大小写只做 ASCII 折叠，完整 Unicode 语言分析未实现。

`"site:example.com"` 等引号内内容是正文短语；`site:"example.com" guide` 才是来源筛选。`intitle:"C++ guide"` 仍表示标题短语。搜索请求的全部选项在建立持久会话之前验证，错误选项不占用会话名额。

## 配置合同校验

`contract.validate` 的 `kind` 为 `model-config`、`source-manifest` 或 `search-request`，`config` 为对应的 JSON 对象。响应结果为 `{kind, validated: true, applied: false, effective_config: ...}`。鉴权要求与其他业务操作相同。

调用方不能传入审核时钟或部署上限。服务端使用自己的时钟和可信 `ContractLimits`；合同输入受 64 KiB 上限约束。校验不赋予来源审核真实性、模型访问权限或网络出站权限，不改变运行中的能力目录。固定合同的语义、与历史 Python 校验器的差异见 [CONFIGURATION.md](CONFIGURATION.md)。

## 数据和安全语义

- `ingested_at_ms` 是本系统导入时间，不是网页发布时间或实际网络抓取时间。
- `canonical_text` 按当前解析版本保存；证据 byte offset 使用 UTF-8 半开区间。
- `location_verified=true` 只保证引用位置正确；`semantic_support=not_evaluated` 明确尚未核验内容事实。
- 所有资料目前是所属用户的私有资料；管理员令牌可以创建全库备份，但不自动获得其他用户的文档读取接口权限。
- noindex 当前采取保守处理：可授权读取，但不进入本地搜索。公开/私人差异化发布策略尚未全部实现。
- 删除无法撤回已发给客户端的数据；未发布路径会重新检查权限。当前不支持授权委托、共享 ACL 或用户删除账户。

## 限额

请求 2 MiB、文档 1 MiB、JSON 嵌套 64 层、查询嵌套 16 层、HTML 遍历 5 万节点/128 层、候选 2000、冻结结果 500、页大小 100、每用户最多 64 个未过期会话（1 小时过期）、2000 快照/32 MiB 原始及正文内容、1000 个保留任务、全库 5 万证据、最多 8 个备份文件。

SQLite 还有最大页数保护。这些是启动版保守限制，不是性能承诺或正式全局配额系统；达到限制明确失败，不自动删除用户资料。

## 常见错误

`E_JSON`、`E_JSON_DUPLICATE`、`E_JSON_LIMIT`、`E_JSON_RANGE`、`E_SCHEMA`、`E_AUTH`、`E_FORBIDDEN`、`E_NOT_FOUND`、`E_QUERY_SYNTAX`、`E_QUERY_LIMIT`、`E_CAPABILITY_UNAVAILABLE`、`E_SESSION_EXPIRED`、`E_SESSION_INVALIDATED`、`E_CURSOR`、`E_IDEMPOTENCY_CONFLICT`、`E_JOB_STATE`、`E_EVIDENCE_RANGE`、`E_QUOTA`、`E_STORAGE_FULL`、`E_RESTORE_QUARANTINED`。

配置合同另有 `E_CONTRACT`（语义矛盾）和 `E_CONTRACT_LIMIT`（复杂度或部署上限）；结构错误仍为 `E_SCHEMA`。

JSON 禁止重复键、无效 UTF-8、孤立 surrogate、NaN/Infinity、超出有符号 64 位的整型、尾随内容与未知字段。JSON 对象字段顺序不作为业务身份；禁止请求自行提交 owner 冒充身份。
