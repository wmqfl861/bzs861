# 来源审核记录：wikipedia（百科 API，wikipedia_en / wikipedia_zh）

| 字段 | 值 |
|---|---|
| 来源 ID | `wikipedia_en`、`wikipedia_zh`（config/sources.json） |
| 端点 | `https://en.wikipedia.org/w/api.php`、`https://zh.wikipedia.org/w/api.php` |
| 适配器 | `wikipedia_api`（python/bzs/worker/sources/apis.py `Wikipedia`） |
| 类型 / 索引族 | API / `wikipedia_en`、`wikipedia_zh`（补充来源，D1） |
| 审核日期 | 2026-11-13 |
| 到期日期 | 2026-12-13（到期或启用前需复核；本记录不是启用许可） |
| approved_by | NOT-ENABLED（占位：待用户完成 S0 实测后填写；本批保持 `enabled:false`） |
| 条款 URL | UA 政策 <https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>；API 礼仪 <https://www.mediawiki.org/wiki/API:Etiquette> |

本记录属于 CL-06 离线准备批次：编写过程中**未做任何网络访问**，事实来自
2026-11-12 的外部复核（review-main.md 第 4 节 CL-06、第 7 节 D1，及引用的
wikitech-l 公告与 Tech News）。

## 配额事实（原样记录）

> Wikimedia 自 2026 年 3 月起对所有 API 实施全局限流：没有合规 User-Agent
> 的匿名请求只给很低的额度；2026 年 4 月起，有身份标识的流量（包括 OAuth
> 认证）可获得更高额度。来源：wikitech-l 公告（2026-03-02，
> mail-archive.com/wikitech-l@lists.wikimedia.org/msg97202.html）与
> Tech News 2026 第 11 周（diff.wikimedia.org/2026/03/09/tech-news-2026-week-11/）。

无按次计费层；额度为全局限流口径，配置中的 `daily_cap`（en 2000/天、zh 由
清单定义）只是本项目自设的本地礼貌上限，不代表上游承诺。

## User-Agent 政策（项目合规性）

Wikimedia UA 政策要求自动化流量携带可识别的工具名/版本与联系方式。本项目 API
来源统一使用 `API_USER_AGENT`（python/bzs/worker/sources/base.py）：
`bzs861/0.1 (personal search tool; https://github.com/wmqfl861/bzs861)` —— 含
工具名/版本与项目网址，**符合政策**。不得对此来源启用浏览器伪装 UA。

## 访问行为

- 匿名访问（无 key、无 OAuth 计划）；合规 UA 是获得可用额度的前提。
- 适配器已处理 MediaWiki 应用层退避：HTTP 200 携带
  `{"error": {"code": "maxlag"|"ratelimited"|...}}` → `rate_limited` 并遵守
  backoff/Retry-After（apis.py `JsonApi.json`）；HTTP 429 同样映射
  `rate_limited`。每源独立冷却（`shared_budget_key=wikimedia` 共享 en/zh 预算）。

## 缓存与再分发

维基百科正文内容按 CC-BY-SA 4.0 授权（署名—相同方式共享；见
<https://en.wikipedia.org/wiki/Wikipedia:Copyrights>）。本项目仅将搜索摘要
片段与结果链接存入本地索引、按 `links_only` 记录再分发口径；对外输出如含
正文片段须附署名与许可说明。`cache_policy` 维持 `unverified`，S0 后复核。

## 结论

候选（candidate），未启用。作为 D1 的补充来源（marginalia + mwmbl 之外的
encyclopedia 能力）。启用前提：用户 S0 实测通过（合规 UA 下的可用额度）+
缓存/再分发口径复核 + approved_by 由用户（或其委托审核）签署。
