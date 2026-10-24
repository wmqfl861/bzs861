# 私有 HTTP 条件重验证合同 v1

本页定义 `bzs-http-revalidation-1` 的核心 → Worker 私有协议。它用于减少已绑定完整页面和 robots 表示的重复下载，不新增来源、站点许可、公开 API 参数或重试权限。既有不带条件字段的 Worker 请求继续有效。

HTTP 语义依据 [RFC 9110 §13.1 条件请求](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1)、[§15.4.5 304](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4.5) 和 [RFC 9111 §4.3.4 更新已存响应](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.3.4)。本协议采用其中较小、保守的子集；下面的 `Vary`、缓存指令及日期限制是本项目边界，不声称 HTTP 标准禁止其他形式。

## 请求合同

`fetch.extract`、`map.fetch(kind=page)` 和 `map.fetch(kind=robots)` 可携带可选 `conditional` 对象。`map.fetch(kind=auto|sitemap)` 暂不支持条件请求。

对象必须恰好包含以下 **6 个字段**，不接受未知字段、`null`、数字代替字符串或布尔值代替预算整数：

| 字段 | 类型与含义 |
|---|---|
| `version` | 字符串，固定为 `bzs-http-revalidation-1` |
| `binding_id` | 64 个小写十六进制字符；核心给出的完整表示绑定身份 |
| `url` | 1–4096 个 ASCII 字符，精确 HTTP URI，不含控制字符或空格 |
| `etag` | 空串或一个不超过 1024 字符的 ASCII entity-tag，可带 `W/`；拒绝列表、通配符、重复字段、注入字符 |
| `last_modified` | 空串或一个规范的 29 字符 IMF-fixdate；日期、星期与 GMT 格式必须一致，年份不早于 1601 |
| `request_profile` | 64 个小写十六进制字符，标识原始请求的实际表示选择 |

`etag` 和 `last_modified` 至少一个非空。提供两者时同时发送 `If-None-Match` 和 `If-Modified-Since`。核心不得从客户端参数接受或转发这些私有绑定。

Worker 用稳定 JSON 编码对 `version`、GET 方法和所有显式请求头的名称/值计算 SHA-256；头名称转小写，忽略两个资源验证条件头。实际请求显式固定 `Accept-Encoding: gzip, deflate`，避免可选解码依赖变化造成隐式身份变化。UA、Accept、Accept-Language 或未来其他显式请求头变化都会改变 profile。Worker 不使用 cookie jar，也不从环境加载代理。

若 URI 与实际请求目标不同，Worker 在网络前返回 `E_CONDITIONAL_MISMATCH`。若只有当前配置的 profile 变化，Worker 在网络前撤去条件头，让**本次原定 GET**取得完整表示；不增加第二次请求。此时服务端若仍返回 304，按无绑定的 304 拒绝。原始字段或 robots 旧表示不合法时，不通过这个分支绕过校验。

新条件读取严格限制顶层参数为 `url`、`timeout_ms`、`attempt_id`、`max_requests`、`max_content_bytes`、`conditional`。同步读取可沿用原来的非持久预算合同；持久读取仍要求完整、合法的预算和 attempt ID。

## 响应元数据

仅可复用的**完整 200** 表示附带 `http_cache`。它必须恰好包含以下 **10 个字符串字段**：

| 字段 | 含义 |
|---|---|
| `version` | `bzs-http-revalidation-1` |
| `url` | 实际响应的精确 URI |
| `request_profile` | 本次实际请求的 profile |
| `etag` | 一个验证器或空串 |
| `last_modified` | 规范 HTTP 日期或空串 |
| `cache_control` | 有界的原始合并 Cache-Control 字段，最多 4096 字符，或空串 |
| `vary` | 空串表示缺失，或最多 4096 字符、仅含 Accept-Encoding 的 ASCII 列表 |
| `date` | 规范 HTTP 日期或空串 |
| `age` | 1–12 个十进制数字或空串 |
| `expires` | 规范 HTTP 日期或空串 |

缺失的 HTTP 字段用空串表示。所有日期统一采用不早于 1601 年的规范 IMF-fixdate 子集。显式提供但为空的 ETag、Last-Modified、Date、Age、Expires、Cache-Control、Vary 字段无效。重复的单值字段保留为可检测的重复值，不取“最后一个”；Cache-Control 与 Vary 作为列表字段合并后统一检查。

本阶段只允许缺失或仅含 `Accept-Encoding` 的 `Vary`，大小写和重复项可接受；空项、通配符、其他或混合选型仍拒绝。实际 Accept-Encoding 由精确请求 profile 绑定。本阶段不保存存在不支持的 `Vary`、`no-store`、`no-cache`、`private`、零 max-age/s-maxage、冲突或重复年龄指令、非法日期、重复验证器或超界字段的条件元数据。没有可用验证器、206、截断、提取失败、空正文或未支持类型也不形成新的条件基底。这些元数据问题不会把已经完整取得、且原本可读的 200 正文改成网络失败；它只不能成为后续条件请求的基础。

对于 robots，只有完整 200、保留规则并具有正有效期的 `rules` 记录能附带元数据。404/410 的历史允许策略、失败产生的 deny 记录和因 no-cache/max-age=0 清空规则的记录都不符合这个条件。

## 304 的独立结果

成功的 304 返回 `status=not_modified`，保留原来的 `http_status=304`、`final_url`、重定向信息及用量，并附带：

- 原样回显的 `binding_id`；
- 相同 URI/profile 的 `http_cache`；
- 页面响应中新观察到的限制性 `publication_observation`（如有）；
- 自动页面请求的真实 `robots_decision`；
- robots 请求更新后的 `robots` 记录。

304 不返回 `html`、`text`、`page`、`discovery`、新的允许型 publication 或虚构正文。缺少 ETag/Last-Modified 时沿用派发验证器；服务端显式给出 ETag 时必须与旧 opaque-tag 弱比较相同，显式给出的 Last-Modified 与已有日期不得冲突。核心可以采用更严格的字符串相等策略，但不能接受不同 opaque-tag。

304 的其他缺失 HTTP 字段仍返回空串。持久化时，核心只用非空字段更新旧 `http_cache`，不能通过省略字段删除原来的缓存限制。完整 200 则建立一份新的元数据；如果新 200 不再具有可用元数据，应清理旧的验证器绑定。

以下情况返回 `E_HTTP_REVALIDATION`，不自动执行完整 GET：没有派发条件、profile 变化后收到 304、没有完整 HTTP 链授权的 HTTP redirect 后收到 304、任何 soft redirect 后收到 304、验证器冲突、不支持的 Vary、非法缓存字段、截断或出现正文。即使结果失败，已经收到的 noindex/nosnippet 等限制仍向核心报告。

条件头在**每次 HTTP 跳转后移除，包括同源跳转**，soft redirect 后也不继承。自动页面/robots 请求仍是单个 HTTP hop，下一跳由核心另行派发并重新核验站点、robots 与预算。

## robots 旧表示与内容准入分离

条件 robots 请求还必须携带 `robots_revalidation_base`，对象恰好包含：

| 字段 | 对象内容 |
|---|---|
| `record` | 完整的旧 `bzs-robots-1` rules 记录 |
| `http_cache` | 完整的旧 10 字段元数据 |

实际 `record` 和 `http_cache` 都必须是对象。旧 record 所有原有字段、规则编码、body SHA、origin 和时间上下界必须有效，state 必须为 `rules`；它的有效期允许已经结束。旧元数据的 URI、profile、ETag、Last-Modified 必须与本次 conditional 精确对应，且原始记录曾有可信的正缓存有效期。

该字段只供重新验证已有表示使用；它不能替代页面请求的 `robots` 准入记录。过期的旧表示仍不能派发内容请求。旧表示结构会在实际 metadata 派发边界再次核验；页面请求则在站点等待、主机等待之后再次检查当前准入记录是否仍有效。

匹配的 304 将新非空缓存头合并进旧元数据，再结合 Date、Age、Expires、响应耗时和 24 小时上限计算有效期。只更新时间，保留原来的规则、body SHA、sitemap 与诊断信息，不凭 304 放宽旧规则。合并后得不到正有效期时返回错误和 `deny_all` 记录。无旧表示的 304 同样只形成拒绝记录，不得延长过期允许策略。

## 核心负责的持久绑定

Worker 不能凭私有请求中的 `binding_id` 查找或恢复正文。核心必须独立保证以下条件，并在提交时重新检查：

1. 验证器来自同一 owner、精确 URI、请求 profile 和完整原表示，绑定原快照/解析结果及缓存元数据身份。
2. 派发到提交之间的删除代数、当前快照、旧缓存身份、任务租约和 attempt 仍有效；不同 owner 或更新后的快照不能误用旧 304。
3. 304 只能复用原正文的发布序号，不能用本次较新的派发序号消除原正文产生后发生的 noindex/nosnippet、站点上限或删除。
4. 当前自动请求仍满足站点审核、精确清单、URI 范围及新鲜 robots 准入；304 不放宽抓取权限、站点范围或预算。按新任务深度重建 discovery 时，只能使用已完整保存且可安全重建的原发现集合，并复核当前限制。
5. 已知 304 仍记一个实际 HTTP 请求、零响应体字节。丢失或无法解释的回执继续采用原保守预算规则，不把未知用量退为零，也不自动重发。

本协议不改变默认空站点清单、17 个禁用搜索来源、审批要求或既有发布/验收证据。它不等于完整 HTTP 缓存实现，也不表示整个开发阶段已验收。

## 聚焦验证

`python/tests/test_http_revalidation.py` 使用本地 HTTP fixture 覆盖完整 200 → 304、ETag/Last-Modified、重复/非法字段、无绑定 304、配置切换、重定向、单跳计费、robots 过期/主机等待复核、旧规则绑定与 TTL 合并。`python/tests/test_fetch.py` 另覆盖所有资源条件头的跳转隔离及重复头保留。完整核心事务、重启/恢复及跨 owner 验证由核心测试和真实 Worker 集成测试补齐。

## schema12、容量与核心提交

schema12仅在既有schema11上追加 `robots_cache.http_cache_json`（旧行默认 `{}`）和 `snapshots.http_cache_bytes`（旧行默认0）。真实旧库的页面没有验证器身份，升级后先取得完整200才能建立条件基底；升级不从正文、ETag猜测或历史URL重建不存在的绑定。旧备份隔离在迁移前仍生效。

快照记录的 `http_cache`、私有 `http_revalidation` 与完整发现标记的规范JSON总量最多16KiB，计入owner逻辑存储配额。相同正文的元数据增长先检查并收费，收缩退还差额，删除/旧快照回收一起退款；配额失败整体回滚。304不生成虚构正文，收紧publication时可以形成新的受限快照身份，原正文、旧快照和证据仍保持不变。

核心派发及提交都合并当前document publication上限，包括此前失败响应、或本次请求在途期间观察到的 `nofollow`。304仍沿用原完整正文的页面序号与站点代际；站点重新批准或较新的请求序号不能替旧正文解除限制。crawl按实际保存快照的follow状态决定扩张。

仅当原HTML/text发现集合完整、至多512条、无截断、每条均可与已存解析链接匹配、无跳转链且物理/逻辑URI相同，crawl页面才可使用304并重建发现。重建使用新任务的父节点和深度，仍受当前robots、nofollow、预算、trap和范围限制。普通map发现以及sitemap不从页面304补造发现结果。

robots只检查同owner/origin/清单的最新记录再判断有效期，不向后搜索仍未到期的旧允许记录。回收按旧记录优先、总数有界执行：若较旧记录仍存在（尤其被活动准入/回执引用），最新决策须保留；全部旧引用退役并回收后，最新无引用过期记录也可清理，不永久泄漏缓存头。

`tests/revalidation.cpp`覆盖持久绑定、同快照元数据竞争、配额回滚、nofollow与回收反例及8轮刷新；`tests/e2e_revalidation.py`通过真实Core/Worker/HTTP验证直接读取重启、批读、robots和父子页面304、错误validator、跳转条件头隔离及失败限制观察。独立复核与真实schema11升级的范围以本轮验证报告为准。

## 0.1.10 独立新鲜度补充

条件验证器协议仍保守，页面缓存限制改由独立`http_freshness`记录约束。没有ETag不代表允许默认缓存；no-store、no-cache、Age/Date/Expires等具体行为见[HTTP_FRESHNESS](HTTP_FRESHNESS.md)。该补充不改变304的原正文权限、删除、发布和表示身份屏障。

## 完整 HTTP 跳转链授权

显式 `fetch.extract` 新增与直接 `conditional` 互斥的 `bzs-http-redirect-conditional-1` 包装；只有已保存完整逐跳证明、重新从原 URI 走完相同实际前缀，才能在精确 final URI 使用其自身 validator。它不是把条件头从前一跳继承到下一跳。链 304 按 N+1 个实际请求和已消费的 redirect body 字节计费，继续使用同一正文绑定与旧发布序号。旧直接协议、无证明 redirect、soft redirect、自动 map/crawl 单跳准入均保持原边界，详见 [HTTP_REDIRECT_CACHE](HTTP_REDIRECT_CACHE.md)。
