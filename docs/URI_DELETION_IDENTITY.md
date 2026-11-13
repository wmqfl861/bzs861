# URI 删除身份与 HTTP 复用屏障

本合同的删除键自 schema14 引入。当前 schema15 另外保存批读迟到订阅的派发超时与加入关闭状态，见[共享读取](SHARED_READS.md)；不改变本文的删除等价范围或原有身份。

schema14 为每个文档保存独立的 `documents.deletion_key`，用来检查同一 owner 下的 URL 别名是否已经删除。它补齐普通字符与 unreserved percent 编码的双向查询：例如删除 `https://example.com/%6Diddle` 后，指向 `https://example.com/middle` 的旧 HTTP 表示也必须重新检查删除屏障。

这个键只参与删除比较。请求仍使用原精确 URI，文档和快照仍保留原身份；不会根据该键从其他 URL 或其他 owner 查找正文。新鲜度、请求 profile、来源与站点准入、发布限制、预算和任务租约继续分别检查。

## 四个 URL 身份函数的格

仓库里有四个 URL 身份函数，用途不同、粗细不同（CL-18）：

| 函数 | 实现 | 用途 |
| --- | --- | --- |
| `dedup_key` | `src/web.cpp`（声明于 `include/bzs/web.hpp`） | 搜索结果融合组键（R-01 保守去重，进入搜索缓存键的 `dedup_version`） |
| `url_key`（即 `map_url`） | `src/sites.cpp` | 自动站点准入/文档键 |
| `read_identity`（即 `publication_url_identity`） | `src/web.cpp` | 发布绑定与 HTTP 缓存路由的普通 HTTP 客户端身份 |
| `deletion_url_key` | `src/util.cpp` | 删除屏障别名（本文其余部分） |

在三者都接受的公共定义域上，等价关系满足：

```
dedup_key ⊆ url_key ⊆ read_identity
即  dedup_key(a)==dedup_key(b)  =>  map_url(a)==map_url(b)
    map_url(a)==map_url(b)      =>  publication_url_identity(a)==publication_url_identity(b)
```

因此"同一融合组 ⇒ 发布检查同一身份"永远成立：R-01 之后同一融合组里的 URL 只差主机名 ASCII 大小写和默认端口，而这两个维度在 `url_key` 和 `read_identity` 里同样折叠。各函数的归一化对比：

| 维度 | dedup_key | url_key（map_url） | read_identity | deletion_url_key |
| --- | --- | --- | --- | --- |
| scheme 大小写 | 不折叠；非小写 `http://`/`https://` 前缀返回空哨兵 | 不折叠；且元数据准入只接受小写 http/https，其他 scheme 直接拒绝 | 同 url_key | 同 url_key；语法外整体退回精确原串 |
| 主机名大小写 | ASCII 折叠 | ASCII 折叠 | ASCII 折叠 | ASCII 折叠 |
| 默认端口 `:80`/`:443` | 仅去掉精确后缀（`:080` 不折叠） | 按数值解析折叠（`:080` 也折叠） | 按数值解析折叠 | 按数值解析折叠 |
| 片段 `#…` | 保留（SPA 路由差异不融合） | 去掉 | 去掉 | 去掉 |
| 空路径、末尾空 `?` | 保留差异 | 折叠为 `/`、去掉 | 折叠为 `/`、去掉 | 折叠为 `/`、去掉 |
| percent 转义 | 原样保留（十六进制大小写也视为不同） | 原样保留；路径中拒绝编码分隔符/点 | 解码 unreserved 与安全 sub-delims（路径 `-._~!$&'()*,=:;@`、查询 `-._~!$'()*,/:?@`），其余大写重编码（十六进制大小写折叠） | 仅解码 unreserved（其余大写重编码），比 read_identity 更细：`/%24x` 与 `/$x` 不同，read_identity 下二者相同 |
| 点段 `.`/`..` | 原样保留 | 直接拒绝（`E_SCOPE`） | 解析消解（`/a/../b` 与 `/b` 相同） | 原样保留 |
| 查询参数顺序与重复 | 保留 | 保留 | 保留 | 保留 |
| http 与 https | 区分 | 区分 | 区分 | 区分 |

**deletion_key 的位置。** `deletion_url_key` 不在这条链的任何一端：它与 `read_identity` 使用相同的 unreserved 解码和默认端口折叠，但不做点段消解，所以在点段维度上比 `read_identity` 更细；它又比 `url_key` 粗，因为 `%2f` 与 `%2F`、`%41` 与 `A` 在删除身份下相同而 `url_key` 保留区别。URL 不满足元数据语法（如带下划线的主机、大写 scheme、IPv6 字面量、userinfo）时它整体退回精确原串，此时它比所有函数都细，也与 `dedup_key` 不可比——`dedup_key` 相同的一对（仅主机大小写不同）可以带有不同的删除键。"dedup 相同 ⇒ 删除键相同"只在元数据语法之内成立；删除比较本身完全发生在这个键上，不影响上面的链条。

**定义域与空哨兵。** `dedup_key` 从不抛出：非 http/https、大写 scheme、userinfo（`@`）、空主机都返回空串，这是"无融合身份"哨兵，融合路径对空键直接跳过。`url_key` 与 `read_identity` 会按各自准入规则抛出（`normalize_iri`、`check_public_url`、`safe_path` 等）；在部署的融合路径里，准入（`normalize_iri`、`check_public_url`）先于 `dedup_key` 运行（`src/web.cpp`），所以同一融合键的成员只会被同时接受或同时拒绝，不存在"同组一半可准入、一半不可"的分裂；这是部署顺序的性质而非裸函数定理——`dedup_key` 不限长度而 `normalize_iri`/`metadata_host` 要求 ≤4096 字节，理论上存在 4096 边界处的同键反例，性质测试生成器（URL 最长约 60 字符）不覆盖该边界。链条断言在三者都接受的公共定义域上；对含点段的路径 `url_key` 拒绝而 `read_identity` 接受时，`dedup_key` 相同 ⇒ `read_identity` 相同仍直接成立（发布检查身份不依赖 `url_key` 准入）。

**R-01 语义说明。** 四个函数都区分 http 与 https：发布和删除的数据模型本来就把它们当成两个资源。R-01 之前较宽的融合让"一个变体上的限制挡住另一个变体"成为可能——例如旧爬取标记（legacy crawl marker）或封锁的发布身份能挡住另一 scheme 变体的命中——那只是因为两条结果被融合进了同一组。R-01 收紧融合后，组内所有成员共享同一个 `read_identity`：发布限制要么覆盖全组、要么全组不受影响，贡献者身份数也无法再靠组内变体凑出（见 `src/sessions.cpp` 中 captured_mode 上限的可达性注释）。所以 R-01 没有造成发布绕过，它让搜索行为与发布/删除的身份模型一致。任何改动这四个函数、使上述包含关系不再成立的提交都是规格回退。

**性质测试。** `tests/url_identity_lattice.cpp`（CTest 名 `url_identity_lattice`）用固定种子的确定性生成器覆盖 scheme/主机大小写/端口/路径/查询/片段/percent 转义/punycode 与 UTF-8 主机/尾斜杠/默认端口/大写 scheme，每个提交运行 12000 对随机 URL（含姊妹变异对与定向对），断言 `dedup_key(a)==dedup_key(b)` ⇒ `url_key(a)==url_key(b)` ⇒ `read_identity(a)==read_identity(b)`（含 `dedup ⇒ read` 直接断言与准入对称性）。本文件描述的两层包含是严格的：存在 dedup 不同而 url_key 相同的对（空路径与 `/`、片段、末尾 `?`、`:080`），也存在 url_key 不同而 read_identity 相同的对（查询中转义十六进制大小写、unreserved 转义）。

## 等价范围

实现入口为 [`deletion_url_key`](../src/util.cpp)。对已有元数据校验可接受的 HTTP/HTTPS URI，它采用以下有限规则：

| 内容 | 删除身份中的处理 | 示例 |
| --- | --- | --- |
| 主机名 | ASCII 大小写折叠 | `EXAMPLE.com` 与 `example.com` 相同 |
| 端口 | 按十进制规范化；省略协议的默认端口 | HTTPS 的 `:443` 与省略端口相同；`:8443` 保留 |
| 空路径、片段、空查询 | 空路径补 `/`，去掉 `#fragment`，省略末尾空 `?` | `https://example.com`、`https://example.com/?`、`https://example.com/#part` 相同 |
| unreserved 字节 | `%HH` 解码为 `A–Z`、`a–z`、`0–9`、`-._~` 中的对应字符；十六进制大小写不影响结果 | `/%6Diddle` 与 `/middle` 相同；`?q=%41` 与 `?q=A` 相同 |
| 其他 percent 编码 | 保留编码，仅将十六进制字母转为大写 | `/%2f` 与 `/%2F` 相同，均与 `//` 不同 |

规则不会解码 reserved 分隔符，也不重复解码。`%2F` 与 `/`、`%3F` 与 `?`、`%25` 与 `%`、`%26` 与 `&` 都保持不同；`/%256Diddle` 不会变为 `/middle`。查询参数顺序和重复项、路径及查询的字符大小写、HTTP 与 HTTPS、非默认端口都继续区分。

点段不做路径消解。`/a/../b` 与 `/b` 保持不同；`/a/%2E%2E/b` 只将 unreserved 编码解为 `/a/../b`。这个比较函数不采用 HTTP 客户端的完整 requoting/路径处理规则，也不替代自动站点准入使用的 `map_url`。

历史元数据中若路径或查询含有畸形 `%` 转义，或 URI 不满足现有元数据校验，整个原始字符串作为精确键保留。不会只规范化前半段，也不会因这类历史行使迁移失败。该兼容处理不增加网络读取可接受的 URI 类型。

## 持久化与升级

schema13 → schema14 在既有迁移事务中执行：

1. 向 `documents` 增加普通文本列 `deletion_key`。
2. 从每条文档的原始 `url` 计算并回填该列，包括已删除、仅保留删除水位的历史文档。
3. 建立普通复合索引 `documents_deletion_key(owner,deletion_key,id)`，随后更新 schema 版本为 14。

它不是依赖连接内自定义函数的表达式索引。新文档插入时保存相同派生键；每次检查通过 owner 和删除键定位别名，不对全部历史 URL 做逐行归一化扫描。单次检查最多接受 1,000 个命中的文档别名，超过上限按既有 `E_QUOTA` 边界拒绝。

升级前必须停止所有使用旧格式的 Core。旧 Core 再次打开 schema14 数据库时应以 `E_SCHEMA_VERSION` 拒绝；该版本检查不能代替停止已经打开数据库的旧进程。不得让新旧 Core 混用同一运行数据库。备份仍遵守既有隔离规则，schema14 不提供撤销连续性证明或新的恢复流程。

迁移不合并或重命名 `document_id`、原始 URL、快照、证据、任务/搜索永久幂等身份，也不改变 `url_key`、`representation_key` 或发布策略的身份规则。删除代数、删除序号与墓碑保留；重新导入某个别名不会把原来的删除历史归零。

## 缓存、验证器与提交

别名查询位于 [`Store::alias_fences_locked`](../src/publication.cpp)。它只检查同一 owner，要求命中文档当前未删除，且该文档的删除代数不晚于本次表示或任务保存的删除序号。不同 owner 下相同的删除键不会互相撤销。

| 路径 | 检查点与结果 |
| --- | --- |
| 直达页面的 `cache_first` / `cache_only` | 返回前在 Store 锁内复核当前快照、元数据哈希、TTL，以及原始和最终 URI 的别名删除屏障；被撤销的旧缓存不能命中 |
| 完整 HTTP 跳转链缓存 | 在相同检查外，逐一复核已保存的中间和最终 target URI；只删除链中某个等价别名也会使旧链失去缓存复用资格 |
| 直达或跳转条件请求 | 有 `publication_fence` 时使用原表示的持久删除序号；旧精确直达表示可按下述序号 0 兼容规则检查。无法证明仍有效时不给出旧验证器绑定，本次已授权刷新走完整读取 |
| 在途 200 / 304 提交 | 在原有事务、owner、逻辑 URL 代数和租约检查中复核链上别名；期间发生的删除使提交按 `E_TARGET_DELETED` 拒绝，已知请求和字节用量仍沿用原有结算 |

删除序号按原表示区别处理：

- 已保存 `publication_fence` 的表示使用其中的原删除序号，不能改用本次请求的新序号来恢复旧正文。
- 只有 `final_url` 精确等于原始请求 URL、且 `redirects` 为空的旧直达表示，缺少 `publication_fence` 时才可使用保守的检查序号 **0**。这要求同一 owner、同一删除身份的全部已存别名均未删除，且没有任何删除历史。新鲜度、profile、完整正文等其他复用条件仍须满足。
- 跳转表示缺少 `publication_fence` 时继续拒绝复用，不能套用直达兼容规则。

序号 0 只用于当次检查，不生成或写回历史 `publication_fence`，也不从当前时间推测旧读取发生的时点。等价组一旦有过删除，重新导入也不能使缺少证明的旧直达表示再次获得缓存或 304 资格。需要一次新的显式完整读取，在当前等价组和其他准入条件允许时建立真实的新删除屏障。

不能通过该检查时，`cache_only` 保守报 `E_CACHE_STALE`；`cache_first` 可以在同一次已授权读取中转为完整 GET，`refresh` 同样不给出失效的旧条件。这是本次读取的首次来源派发，不是失败后的自动重试。

本地 `fetch.profile` RPC 之后，最终缓存读取仍在 Store 锁内检查上述条件。因此，profile 核验期间的别名删除也不能返回旧正文。TTL 有效与删除证明有效是分别必要的条件。

重新导入已删除别名可以恢复该文档自身的当前状态，但不恢复删除前表示的缓存或 304 资格，也不使删除前已排队或在途的任务重新有效。后续新发起的完整读取仍须满足当前等价组状态、任务创建序号及其他准入条件。仅更换 URL 编码不能绕过仍保留的墓碑。

## 历史读取与实施边界

`document.delete` 继续删除所指定的文档，按原规则处理其快照和证据。此次改动没有把整个删除键等价组变成一份合并文档，也没有批量删除其他别名的既有快照。另一个别名中已经保存的历史快照或证据，仍按该 owner 的原有权限和按 ID 读取规则处理；HTTP 缓存复用资格被收回，不等同于对这些历史记录执行物理擦除。

本合同不扩展 URL 发布权限等价、HTTP 请求匹配、索引去重或站点范围。自动 map/crawl 的入口、robots、任务调度和候选集合继续使用各自原有合同；经过公共提交路径的别名删除检查使用本页的有限比较规则。这不构成整个 map/crawl 生命周期或所有历史 API 都已按删除键统一的声明。

当前来源审核/启用、S2 范围和签收状态、validation 入库、备份隔离、永久幂等身份保留及抓取适配器决定继续有效。旧 reverse-percent 反例和执行日志保留为历史，不能改写为新版本的通过证据。本页描述实现合同；实际回归、升级验证和 CI 结果以对应源码输入的本轮验证记录为准。

相关接口见 [HTTP 新鲜度](HTTP_FRESHNESS.md)、[条件重验证](HTTP_REVALIDATION.md)和[HTTP 跳转缓存](HTTP_REDIRECT_CACHE.md)。
