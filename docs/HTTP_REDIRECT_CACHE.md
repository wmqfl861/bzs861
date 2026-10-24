# 显式读取的 HTTP 跳转缓存协议 v1

本协议只扩展 `fetch.extract` 对显式 `web.read` / `web.read_many` 的表示复用。它不增加公开 API 参数，不修改来源或站点审批，不为自动 map/crawl 赋予跳转捷径，也不改变旧快照的证据身份。

## 支持范围

完整的 HTTP 301、302、303、307、308 链可以提供版本化回执。获得缓存复用资格时，每跳必须有明确、有效的正期限（max-age / s-maxage / Expires），整条链使用当前同一 read 请求 profile；最多五跳，证明的规范 JSON 最多 8192 字节。没有明确期限的跳转、HTML/JavaScript soft redirect、循环、重复 Location、无法完整绑定的响应以及旧 Worker 未提供证明的 payload 仍走完整读取，不获得跳转缓存资格。

最终响应仍受既有 HTTP freshness 和 validator 协议限制。没有 ETag 也可在全部链条新鲜时命中缓存；没有安全 validator 时刷新执行完整读取。no-cache / 零期限不能得到新鲜命中。private 只属于同一 owner，不跨 owner 复用；最终 private 表示是否提供条件请求仍遵守原有 validator 子集。

`cache_first` 与 `cache_only` 只有在每跳和最终响应同时新鲜、当前 profile 相符、快照仍是当前版本、全部 URL 的删除水位有效时才能返回缓存。期限按各段剩余有效时间的交集计算，调用方 max_age_s 也约束每段。本地 `fetch.profile` RPC 后再次检查期限，最终缓存读取在 Store 锁内重查快照、metadata hash、期限与 URL 删除水位。未命中时仍使用原有 `E_NOT_CACHED` / `E_CACHE_STALE` 行为。

缓存结果的 `current_age_ms` 仍以最终响应的年龄为锚；`freshness_lifetime_ms` 收紧到整条链的有效期交集，`redirect_hops` 说明参与约束的跳数。逻辑文档仍使用用户给出的原始 URL；本协议不建立全局 final-URL → snapshot 查找，也不让另一个 owner 或 URL 自动取得该正文。

URI 删除身份沿用既有精确 URL 与 `map_url` 规范化范围，包括主机大小写和默认端口。反向 percent 编码的不同存储身份尚未统一：例如单独删除 `/%6Diddle` 不等同于删除实际 `/middle`。本轮保留并记录该既存限制，没有迁移或合并历史 URL 身份。

## Worker → Core 证明

可选 `http_redirect_chain` 对象必须恰好包含：

| 字段 | 内容 |
|---|---|
| version | `bzs-http-redirect-chain-1` |
| request_url | 实际初始 GET 的精确 URI，与 Core 原始请求相同 |
| final_url | 实际最终表示的精确 URI |
| hops | 1–5 个按真实派发顺序排列的跳转响应 |

每个 hop 恰好包含 `url`、`target_url`、整数 `http_status`、整数 `content_bytes` 和完整的 `http_freshness`。`url` 必须等于前一跳的 target；末跳 target 必须等于最终 URI；所有 target 必须依次等于外层 `redirects`。所有 URI 都继续经过现有公网策略校验。`content_bytes` 是本应用实际消费的跳转正文，单链合计不超过 8 MiB，不冒充 socket 或 TLS 字节数。

每跳缓存字段在响应头到达时采样；response_delay_ms 只涵盖该跳从派发到响应头的时间，后续等候/正文消费通过驻留时间计入年龄。最终响应保持既有 freshness 采样路径。证明的 profile 必须与每跳及最终响应一致。

Worker 另行返回有界的 `http_redirect_observations`（最多 20 条，规范 JSON 最多 512 KiB），每条恰好包含实际 `url`、3xx `http_status`、`http_freshness`。即使后续准入、预算、网络或提取失败，这些已经收到的限制仍保留。Core 验证它们属于本次实际响应前缀且不超过已知请求用量。完整证明必须与这些观察逐条一致。

私有协议的编码大小按 Core `Json::canonical` 的 UTF-8 字节计数，包括字符串键和值中的斜杠转义；Worker 的大小预检采用同一口径，不改变实际 JSON 传输或全局序列化器。证明超过 8 KiB 或无法覆盖整个请求时，Worker 省略可选证明，仍返回本次完整读取结果与限制观察。Core 也只有在证明与既有 HTTP 元数据合计不超过 16 KiB 时才保存这个可选证明；不会因省略证明而省略 no-store 检查。证明计入原有 `http_cache_bytes` 和 owner 配额，更新、失效、回收、删除沿用现有事务记账，无新增表或 schema 迁移。

任何实际中间响应的 no-store、非法 Cache-Control 或 Worker 标记 invalid 的原始字段，都会阻止本次原始 URL 读取产生新的持久正文，返回 `E_CACHE_POLICY`。这是本项目当前没有瞬时正文通道时的保守规则；并不声称 HTTP 标准禁止对另一个独立授权资源单独缓存。此前合法取得的正文仍可作为历史证据读取，其 HTTP 复用资格则按旧 snapshot ID + metadata hash 失效。失败的迟到回执不能删除后来成功刷新建立的新缓存身份。

## Core → Worker 刷新授权

原始 URL 的 `refresh` 始终从原始 URI 开始实际 GET，不从本地证明直接跳到 final。Core 可以发送新的、与旧 `conditional` 互斥的 `redirect_conditional` 私有对象：

| 字段 | 内容 |
|---|---|
| version | `bzs-http-redirect-conditional-1` |
| request_url | 原始精确 URI |
| redirects | 原证明中的完整 target URI 列表，1–5 项、无循环 |
| conditional | 现有六字段 `bzs-http-revalidation-1`，其中 url 必须是 final URI |

整个授权最多 8192 字节，未知字段、重复身份、错误类型、非 HTTP URI、用户信息、片段以及同时发送两种条件授权都在 attempt 留存或网络派发前拒绝。它不是公开 HTTP/MCP/CLI 参数。 Core 在提供可选 revalidation base 前同时检查实际授权的编码大小：已保存的证明和总 HTTP 元数据能容纳，不代表重复 URI 与 validator 的条件授权也能容纳。无法在该上限内编码时，本次已授权读取直接执行完整 GET，不先失败再重试，也不扩大上限；没有派发绑定条件就仍不能接受 304。

Worker 重新走每个实际 HTTP 跳转，保留原有 SSRF、连接约束、跨来源头清理、单一 deadline 和请求/正文预算。只有实际前缀与授权完全相同，且最终实际请求 profile 与 validator 的 profile 相符，才在精确 final URI 的这次 GET 上安装其自己的 If-None-Match / If-Modified-Since。条件头不会从前一跳继承；若 conditioned final 又返回跳转，下一 URI 同样不继承这些验证器。

配置 profile 改变、前缀改道或目标变化时，同一个原定读取取得完整表示，不进行额外重试。如果没有实际发送绑定条件却收到 304，继续拒绝。soft redirect 不延续这种授权。新的实际 Location 或失败的新跳转会使旧的、与该请求绑定的 HTTP 复用资格失效，包括此前没有跳转的直达表示。最终目标返回 503 或提取失败也不能让已经改道的旧直达正文继续作为新鲜缓存；历史正文仍保留。snapshot ID + metadata hash 的比较继续防止迟到失效覆盖更新后的表示。

## 304 提交与计费

跳转后的 304 必须同时带有完整新 HTTP 证明和 `redirect_revalidation` 回执。回执恰好包含 version、request_url、final_url、binding_id；version 为 `bzs-http-redirect-conditional-1`，binding 必须等于派发授权。Core 再次校验原始 URI、精确前缀、最终 URI、profile、验证器及已保存正文身份。

带 N 个跳转的 304 必须报告 **N+1 次实际 HTTP 请求**；最终 304 没有正文，但此前实际消费的 redirect body 字节仍计费，必须等于各 hop 的 content_bytes 之和。直接、无跳转的旧 304 继续要求一次请求与零正文。未知用量、失联、取消、任务租约及最后订阅的行为仍按现有保守账目处理，不据此获得免费请求或重试。

304 使用原快照的正文、原页面发布序号和原站点代际；在 Store 事务中校验当前 snapshot、旧 metadata hash、cache hash、owner、删除代数及当前全链发布限制。响应中新看到的 restrictive publication 只能收紧权限。quota 失败或并发刷新导致的 stale base 不部分提交，也不自动补发完整 GET。完整 200 才能以新的实际内容和原有授权规则建立新表示。

自动 map/crawl 仍使用既有单跳 `map.fetch`、站点清单、robots 准入和派发边界复核；它们不接受新的 redirect_conditional 授权。显式读取缓存也不会为自动抓取生成 robots 许可。

## 最终表示与整条链的发布范围

新证明路径分别维护整条读取链的 aggregate policy 和最终 URI 自身的 representation policy。原存储省略独立 final policy 时，含义是它与当时存储的 aggregate 相同；Store 必须在叠加任何当前原始/中间 URL 限制之前恢复这个 final 基底。之后，中间响应的 noindex、原始 URL 的站点 links_only 或全链历史只收紧 aggregate。final policy 只接收精确最终 URI 自身的当前限制、历史及最终 304 观察。重复 304 仍保持这个范围划分；最终 URI 自己返回 noindex 时，两种表示都继续受限。

Worker 的 200 `publication` 保持原有整条读取链的站点范围：原始、中间与最终 URL 的站点限制都收紧这个 aggregate 字段。中间响应的 HTTP robots 限制仍通过独立观察传递，不改变这个既有字段的语义。完整 HTTP receipt、`status=ok`、HTTP 200 且没有截断时，若最终表示自身的策略与 aggregate 不同，Worker 另发私有 `representation_publication`，恰好包含 `version`（`bzs-http-representation-publication-1`）、`url`（实际精确 final URI）和 `publication`（既有 `bzs-publication-1` 最终策略）。相同策略省略重复字段，Core 兼容旧 payload 的保守解释。

Core 严格校验 wrapper 字段、版本、最终身份和完整 receipt；aggregate 不得放宽 final 的限制，final 也不得放宽实际最终响应观察。无证明、错误、empty_content、非 200 或截断响应不能携带该字段，304 同样禁止。这个字段只证明发布范围，不授予缓存资格；完整但 no-cache 的链可正确区分范围，缓存仍需另过原 TTL 和缓存条件。既有 24 MiB Worker RPC 上限不变，相同策略省略避免重复最多 1 MiB 的片段；需要区分时保留完整 final 策略，不用省略权限语义来换取传输空间。

已验证的完整 200 receipt（包括相等策略省略 wrapper 的情况）若因 no-cache、零期限或 16 KiB 元数据预算而不保存缓存证明，Core 仍在本次提交中生成一个内部 `validated_final_publication_scope`（精确 version 和 final URI），供 Store 在叠加任何原始/中间历史前保存原 final 基底并单独计算其历史约束。Worker 顶层或其 `metadata` 内同名数据不能生成这个标记。Store 校验后消费并在持久化前移除它；最终策略按原 `representation_publication_json` 字节计费，标记不新增永久元数据，也不占用或扩大 HTTP 缓存预算。未保存缓存证明时仍无 cache_only 命中或 routed 304 授权。

新路由授权下的 304 `publication_observation` 继续只代表最终响应自身的限制。Core 的 `decode_publication` 和持久 site bindings 对 aggregate 形成原始、全部中间及最终 URL 的完整链约束，因此原 URL 的返回与搜索不会获得更宽权限。旧的无证明 Worker payload 保留原保守范围。

当前与冻结搜索结果继续按各自 URL 身份投影：A 跳转到 C 时，A 的限制不能被误当作 C 自身的限制，撤销独立读取 C 的文档或冻结命中。存储仅在全部当前和历史约束处理完成、两份 policy 完全相同时省略重复 final policy，保留无变化 304 的原快照身份。


## 回归位置

`tests/cache_freshness.cpp` 增量覆盖原 URL 命中、重启、交集期限、profile 改变、旧无证明 payload、no-store、模拟 Worker 链 304 账目、目标删除与 profile-RPC 间竞态、原正文发布限制、metadata 竞争、配额回滚、协议边界和元数据退款；并验证旧直达改道失败的 CAS 失效、原始/最终发布范围、重复 304、派发前和进行中的限制、独立 C 当前/冻结搜索以及最终自身 noindex。

`python/tests/test_http_redirect_cache.py` 使用本地 HTTP fixture 覆盖实际逐跳回执、final-only 条件、改道后重汇 final、conditioned final 再跳转、profile 改变、无绑定 304、soft redirect、SSRF 拒绝、预算边界、重复 Location 以及严格私有授权，以及真实 Worker 的原始/最终站点限制范围。

`tests/e2e_cache_freshness.py` 的既有 CTest 目标增加真实 Core + Worker + 本地 HTTP 链：原 URL 的 cache_only/cache_first 与重启命中不增加 origin 请求，refresh 仅向最终 URI 发送其验证器，read_many 的链 304 逐跳计费并保留原正文，原 URL 缓存保持 owner 隔离；还复现直达改道到失败目标后的旧缓存失效。没有新增 CTest 目标。

旧的直接 304、redirect 条件头隔离、自动 robots 准入、单一 deadline 与全套 native/Python/ASan 门禁仍必须通过；本文不是验收通过声明。
