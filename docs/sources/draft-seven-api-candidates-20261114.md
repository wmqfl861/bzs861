# 7 个免费 API 来源审核记录草案（随 S0 结果一并提请审核方）

按现有来源审核记录格式（docs/sources/mwmbl.md 同款）起草。所有事实来自 2026-11-14 的官方文档/条款页；**未做任何网络请求实测**（探测留给 S0 轮次）。待审核方核对后填 approved_by 并决定激活。

> 重要发现：7 个候选分两组——**4 个已有实现好的适配器**（arxiv/stackoverflow/github/hackernews），**3 个仍是占位符**（gdelt/openalex/crossref，adapter=*`_planned`，无代码，需按 CL-06 模式实现）。

---

## 1. arxiv（学术论文，适配器已实现）

| 字段 | 值 |
|---|---|
| 端点 | `http://export.arxiv.org/api/query`（Atom XML） |
| 条款依据 | [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html) |
| 自动化访问 | **明确允许**（条款原文："enable platforms and projects that extend the discoverability of arXiv e-prints"，可构建搜索工具） |
| 频率限制 | **1 次请求/3 秒，单连接**（所有机器合计）。当前配置 `min_interval_ms=3000` 已合规；`daily_cap=1000` 建议下调至 300（300×3s=15 分钟满负荷即可满足日常） |
| API key | 无需 |
| 许可 | **元数据 CC0 1.0**（无限制复用）；e-print 本体有版权（我们只存元数据+链接，不存 PDF） |
| 署名 | 不强制；条款鼓励链接到 arXiv 摘要页 |
| 其他 | 须遵守 arXiv 行为准则，不规避限速 |

## 2. stackoverflow（代码问答，适配器已实现）

| 字段 | 值 |
|---|---|
| 端点 | `https://api.stackexchange.com/2.3/search/advanced`（JSON） |
| 条款依据 | [Stack Exchange API 文档](https://api.stackexchange.com/docs/throttle) |
| 自动化访问 | **允许**（API 为应用设计）；>30 req/s 视为滥用直接切断 |
| 频率限制 | **10,000 次/天**（默认配额；无 token 按共享 IP 计）。当前 `daily_cap=250` 远低于此 ✓ |
| API key | 无需；注册应用 key 可获得独立配额（推荐后续注册） |
| 许可 | 用户内容 **CC BY-SA 4.0**（摘要仅存片段+链接，不整篇存储；再分发须同许可署名——记录 `redistribution_policy` 时注意） |
| 其他 | 遵守响应中的 `backoff` 字段；相同请求缓存 1 分钟 |

## 3. github（代码仓库，适配器已实现）

| 字段 | 值 |
|---|---|
| 端点 | `https://api.github.com/search/repositories`（JSON） |
| 条款依据 | [GitHub REST API 限速文档](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) |
| 自动化访问 | **允许**（认证后）；限制：5000 req/h（token）、100 并发、搜索端点 30 req/min（认证） |
| 频率限制 | 认证 5000/h；未认证仅 60/h（**必须用 token**）。当前 `min_interval_ms=6000`、`daily_cap=500` 合规（搜索端点 30/min 是真实约束） |
| API key | **需要**个人访问 token（免费）。用户已有 GitHub 账号，零成本 |
| 许可 | 仓库元数据按各自仓库许可；API 返回的搜索结果本身无额外限制 |
| 其他 | 超限返回 403/429 + `retry-after`；反复超限会被封禁 |

## 4. hackernews（社区讨论，适配器已实现）

| 字段 | 值 |
|---|---|
| 端点 | `https://hn.algolia.com/api/v1/search`（JSON，Algolia 运营的官方搜索 API） |
| 条款依据 | [hn.algolia.com/api](https://hn.algolia.com/api)（页面含 terms of use） |
| 自动化访问 | **允许**（免费公共 API） |
| 频率限制 | **10,000 次/小时/IP**（从早期 1,000/h 已放宽）。当前 `daily_cap=1000` 建议下调至 500 |
| API key | 无需 |
| 署名 | terms of use 要求注明 Hacker News 为数据源 |
| 许可 | HN 内容由用户提交（版权各异）；只存标题+链接+片段，不存全文 |

## 5. gdelt（全球新闻，**占位符，需实现适配器**）

| 字段 | 值 |
|---|---|
| 端点 | `https://api.gdeltproject.org/api/v2/doc/doc`（JSON/CSV） |
| 条款依据 | GDELT 项目主页及 [DOC 2.0 API 公告](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)；免费开放数据集 |
| 自动化访问 | 允许（免费无 key）；**未找到正式条款页**——审核方需确认官方 terms |
| 频率限制 | 无正式文档；社区经验约 1 次/5 秒，超量自动节流。建议 `min_interval_ms=5000`、`daily_cap=200` |
| 单次限制 | 默认 75 条/查询，最大 250（`maxrecords` 参数） |
| API key | 无需 |
| 署名 | 预期注明 GDELT 为数据源 |
| 许可 | 数据来自全球新闻媒体（版权各异）；只存标题+链接，不存文章全文 |
| **实现** | `gdelt_planned` → 需按 CL-06 mwmbl 模式实现 `gdelt_api` 适配器 |

## 6. openalex（学术图谱，**占位符，需实现适配器**）

| 字段 | 值 | 
|---|---|
| 端点 | `https://api.openalex.org/works`（JSON） |
| 条款依据 | [OpenAlex 帮助中心](https://help.openalex.org/api/authentication)、[限速公告 2025-11](https://openalex.org/blog/announcing-changes-to-rest-api-rate-limits) |
| 自动化访问 | **允许**（免费层） |
| 频率限制 | **2025-11 起改为积分制**：免费 100,000 积分/天、100 req/s 上限；无 key 10 req/s。旧的 `mailto` 礼貌池**已废弃**——适配器需支持 API key（可选，免费注册） |
| API key | 可选（免费注册可提高速率；匿名 10 req/s 已够用。当前 `min_interval_ms=1000` 合规） |
| 许可 | **全部元数据 CC0**（无限制复用） |
| **实现** | `openalex_planned` → 需实现适配器；须用新的 key 认证模型而非 mailto |

## 7. crossref（学术DOI元数据，**占位符，需实现适配器**）

| 字段 | 值 |
|---|---|
| 端点 | `https://api.crossref.org/works`（JSON） |
| 条款依据 | [CrossRef REST API 文档](https://github.com/CrossRef/rest-api-doc)、[认证文档 2025-10](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication) |
| 自动化访问 | **允许**；请求中带 `mailto`（参数或 User-Agent）进入礼貌池（更高速率+可联系） |
| 频率限制 | 分池限速（公共池/礼貌池），响应带 `x-rate-limit` 头；2025-11 更新了限速与并发限制。建议 `min_interval_ms=1000`、`daily_cap=300` + `mailto` 参数 |
| API key | 无需（mailto 即可） |
| 许可 | **元数据 CC0**（公共领域贡献） |
| **实现** | `crossref_planned` → 需实现适配器（mailto 进 User-Agent 或查询参数） |

---

## 汇总建议

| 来源 | 适配器 | 激活前需做 | 用户侧成本 |
|---|---|---|---|
| arxiv | ✅ 已有 | 审核确认；daily_cap 或调 300 | 零 |
| stackoverflow | ✅ 已有 | 审核确认 | 零（后续可选注册） |
| github | ✅ 已有 | 审核确认 + 用户 token | 生成一个 PAT（免费） |
| hackernews | ✅ 已有 | 审核确认；cap 或调 500 | 零 |
| gdelt | ❌ 占位 | 审核确认 + 实现适配器 + S0 探测 | 零 |
| openalex | ❌ 占位 | 同上（新 key 模型） | 零（key 可选） |
| crossref | ❌ 占位 | 同上（mailto） | 零（mailto 即可） |

配置现状（已核对）：4 个已实现候选的 `min_interval_ms` 与条款一致；7 个全部 `enabled=false`，激活前按审核流程逐个过。3 个占位符的适配器实现按 CL-06 mwmbl 先例（离线夹具测试+S0），每源约半天。

> 来源：[arXiv API TOU](https://info.arxiv.org/help/api/tou.html)、[Stack Exchange API](https://api.stackexchange.com/docs/throttle)、[GitHub REST API Rate Limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)、[hn.algolia.com/api](https://hn.algolia.com/api)、[GDELT DOC 2.0 公告](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)、[OpenAlex 认证](https://help.openalex.org/api/authentication)与[限速变更公告](https://openalex.org/blog/announcing-changes-to-rest-api-rate-limits)、[CrossRef rest-api-doc](https://github.com/CrossRef/rest-api-doc)
