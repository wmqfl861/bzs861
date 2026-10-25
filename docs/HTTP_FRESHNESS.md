# 页面 HTTP 缓存新鲜度（0.1.10）

条件验证器和缓存策略分开记录。无 ETag / Last-Modified 的完整响应仍可包含 `no-store`、`no-cache`、`max-age` 等限制，不能因为没有条件元数据而回落为七天新鲜。Worker 搬运有界原始字段，C++ 决定是否允许持久保存和是否能复用。

语义依据 [RFC 9111 §4.2](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.2) 和 [§5.2](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2)。这是保守子集，不是完整代理缓存实现。

## 行为

`cache_first` 仅复用可证明新鲜的同 owner、精确 URI 和请求 profile 表示。年龄包括 Date / Age、网络响应耗时以及存储后的驻留时间；响应 max-age、s-maxage、Expires 和调用方 max_age_s 共同取较严格上限。没有显式期限但完整报告了缓存字段的响应，采用调用方的有界期限（默认七天）。零期限、no-cache、Pragma no-cache、不支持的 Vary、非法日期/年龄、时钟回退或未知旧版元数据不能获得默认新鲜度。

`cache_only` 不请求来源；没有快照返回 `E_NOT_CACHED`，已有表示需要验证时返回 `E_CACHE_STALE`。它不是读取任意历史证据的接口；已取得的快照可继续通过 `document.read/range` 按权限读取。`refresh` 总是发起一次已授权刷新；网络失败不会悄悄返回过期正文。must-revalidate 与其他指令都不能触发未授权陈旧回退。

请求 profile 由本地 Worker 的 `fetch.profile` 只读 RPC 核实，该 RPC 不联网到来源。配置改变后不复用旧 profile。当前核心进程已经核验 profile 时，Worker 暂时不可达仍能使用该 profile 下的新鲜缓存；新启动的核心没有这个证明时会如实报告 Worker 不可用。原始请求有重定向而未保存完整跳转缓存策略时，不通过目标页面期限复用整个跳转请求。

`private` 可保存在同 owner 的私有快照中，不跨 owner 共享。`no-store` 和无法安全解释的 Cache-Control 不创建持久快照，本阶段返回 `E_CACHE_POLICY`；瞬时正文读取尚未实现。响应之前已有的合法快照仍是历史证据，但其 HTTP 复用元数据被失效。失效绑定派发时的快照和元数据哈希，迟到失败不能清除后来成功刷新的表示。批读/爬取同样在核心提交时禁止保存 no-store 正文，并保留实际请求/字节计费。

`no-cache` / max-age=0 仍可保留证据快照，但每次 HTTP 读取需要重新取得响应。本阶段条件验证器协议继续采用0.1.9保守子集，因此这些响应会执行一次完整 GET，不自动重试。允许的304合并未显式替换的缓存指令，并更新时间；任何新响应报告不可复用的指令都会按原快照/元数据身份失效旧复用资格，即使304协议或提取后来失败。已陈旧的304可能保守报`E_REVALIDATION_STALE`，下一次显式读取才发完整GET，不自动追加网络请求。旧 Worker 未报告新鲜度时不将旧元数据当作已更新。

## 私有元数据

`http_freshness` 的版本为 `bzs-http-freshness-1`，包含精确 `url`、`request_profile`，六个有界原始字段 `cache_control/vary/date/age/expires/pragma`，`received_at_ms`、`response_delay_ms` 和 `invalid`。所有字段都必须存在、类型严格；调用方不能传入。核心记录的字节并入既有 HTTP 元数据配额，旧schema12行不虚构新元数据。

结果的 `cache` 对象解释 fresh、store_allowed、reason、current_age_ms 和 freshness_lifetime_ms。完整协议与304的权限/发布屏障继续见 [HTTP_REVALIDATION](HTTP_REVALIDATION.md)。本次不实现通用 Vary 选型、跨用户缓存、瞬时 no-store 流、stale-if-error 或外部代理缓存。

所有HTTP状态在解析之前搬运一次原始缓存字段，完整203/空正文crawl和失败304都不能遗漏限制。重复Pragma按列表保留；重复Date/Age等单值字段保持可检测的无效表示，不取最后一个覆盖前项。接收时间只采样一次，正文提取期间的驻留时间不会归零；本地profile RPC结束后再次核验TTL。已有的截断正文证据可在显式非发布规则下保留，但不会保存可复用的新鲜度或条件验证器。

## Accept-Encoding 选型

页面新鲜度和条件验证只支持缺失的 Vary，或仅由 Accept-Encoding 组成的有界 ASCII 列表。字段名大小写和重复项不改变含义；通配符、其他选型、混合列表、空项、显式空字段及超界字段仍不能获得缓存复用。Accept-Encoding 已包含在实际请求 profile 中；当前 profile 必须与已存表示相同，改变编码选择会使旧缓存失效。304 省略 Vary 时保留原选择，不能抹去既有约束。

robots 的新鲜规则沿用固定 BzsBot User-Agent、text/plain Accept，并允许固定 gzip/deflate Accept-Encoding；其版本化策略身份依赖这些固定选择。新鲜 robots 记录本身不另存 profile，未来使这些头可配置或改变默认值时，必须更新策略身份或增加显式绑定。条件重验证仍要求精确 profile，且只接受上面的较窄 Vary 子集。

## 完整 HTTP 跳转链补充

显式读取现在可以依据 `bzs-http-redirect-chain-1` 复用完整 HTTP 跳转链：每跳有明确有效期限、精确 URI 与当前 profile，整体期限取交集。没有证明的旧 payload 和 soft redirect 仍保守。刷新仍从原始 URI 重新走实际跳转，仅在精确 final URI 安装其自身验证器；中途 no-store、删除、发布限制与全部实际用量不会因缓存省略。私有协议、边界及回归位置见 [HTTP_REDIRECT_CACHE](HTTP_REDIRECT_CACHE.md)。

## 缓存命中的当前权限投影

直达缓存与具备完整证明的 HTTP 跳转缓存都在返回前，在 Store 锁内复核当前快照、元数据身份、有效期和相应删除屏障，并按当前发布权限返回 `indexed`。schema14 的 `(owner,deletion_key,id)` 索引同时检查原始与最终 URI 的有限等价别名；跳转缓存还检查链中每个 target。已存 `publication_fence` 时使用原删除序号。旧表示仅在 `final_url` 精确等于原始 URL、`redirects` 为空、且没有 `publication_fence` 时，允许按保守检查序号 0 兼容复用；同 owner 的等价组必须没有任何删除历史。这个检查不写入或补造历史证明，跳转表示也不能使用该兼容分支。

删除屏障检查失败时，`cache_only` 返回 `E_CACHE_STALE`；`cache_first` 可在本次已授权读取中转为完整 GET。曾有删除历史的旧无证明直达表示，需要新的完整读取在当前准入条件允许时建立真实的新屏障。

`fetch.profile` 只确认当前 Worker 配置，不产生 origin 请求；其间发生的别名删除、发布限制或并发刷新仍会在最终命中时被发现。删除后重新导入别名不会恢复旧缓存资格。HTTP 复用与按 ID 读取历史快照的权限仍分开处理；本次不合并文档或批量删除其他别名的历史正文，`document.read` 与默认 `read_range` 保持原有语义。等价范围、历史墓碑回填及停止旧 Core 后升级的要求见 [URI 删除身份](URI_DELETION_IDENTITY.md)。
