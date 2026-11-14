# 来源审核记录：mwmbl（通用网页搜索 API）

| 字段 | 值 |
|---|---|
| 来源 ID | `mwmbl`（config/sources.json） |
| 端点 | `https://api.mwmbl.org/api/v2/search/?q=`（v2 公开搜索 API） |
| 适配器 | `mwmbl_api`（python/bzs/worker/sources/apis.py `Mwmbl`） |
| 类型 / 索引族 | API / `mwmbl`（与 marginalia 互相独立的通用网页索引族） |
| 审核日期 | 2026-11-13 |
| 到期日期 | 2026-12-13（到期或启用前需复核；本记录不是启用许可） |
| approved_by | NOT-ENABLED（占位：待用户完成 S0 实测后填写；本批保持 `enabled:false`） |
| 条款 URL | <https://developer.mwmbl.org/> |

本记录属于 CL-06 离线准备批次：编写过程中**未做任何网络访问**，事实来自
2026-11-12 的外部条款复核（review-main.md 第 4 节 CL-06、第 7 节 D1，及其引用的
developer.mwmbl.org 页面）。启用决定（D1）与 S0 探测由用户执行（docs/S0_PROBE.md）。

## 配额事实（原样记录）

> 匿名访问：每月 1,000 次请求、1 请求/秒。免费 API key：每月 2,000 次请求、
> 5 请求/秒。超出配额返回 HTTP 429。按量付费需要主动开启，不开启即为硬上限
> （符合本项目"不设付费回退"的约束）。来源：developer.mwmbl.org（2026-11-12 复核）。

**配额口径（D23 已统一）：** config/sources.json 中 mwmbl 的
`daily_cap=30/天`（`contract.limits.request_quota=30`、`quota_window_seconds=86400`、
`min_interval_ms=1000` 保持）。30/天 × 31 天 = 930 ≤ 匿名 1,000/月，保证最长月份
也不超匿名月配额；用户申请免费 key（2,000/月）后改为 60/天。不在 mwmbl 账户上
设置消费上限——上游代码里消费上限为 0 时，月上限就是免费额度本身（硬上限，
不设付费回退）。来源：第四轮复审第 5 节 D23（docs/reviews/claude-2026-11-14-
phase2to5-review.md）。

## 访问与鉴权

- S0 阶段按 D15 使用**匿名额度**（不设 `BZS_MWMBL_KEY` 时适配器不带任何
  鉴权头）；之后可申请免费 key 提高月配额（D1：先匿名、后免费 key，
  不开启按量付费）。
- 适配器支持可选环境变量 `BZS_MWMBL_KEY`，存在时以 **`X-API-Key` 头**发送
  （CL-27 更正：`Authorization: Bearer` 是错误形式，mwmbl 不读该头）。无效
  key 返回 **HTTP 401**，适配器判独立的 `auth_failed` 状态并停止该来源的
  剩余预算（经既有错误冷却栅栏）。

## 响应形状与离线夹具

**CL-27 更正（2026-11-14）：** 实际 v2 响应是 **SearXNG 兼容的对象**
（`SearchResponse`：`query`/`results`/`images`/`monthly_usage`/`monthly_limit`），
`results` 成员为 `{url, title, content, engine, score, ...}`，其中 `title` 与
`content` 是**纯字符串**；鉴权头为 `X-API-Key`，无效 key 返回 401。事实来源：
mwmbl 上游仓库提交 `cce49fa`（2026-11-13；`mwmbl/tinysearchengine/search.py`
的 `_register_search_v2`/`SearchResponse`/`SearchHit`、`mwmbl/format.py` 的
`format_result_v2`）。此前的"顶层数组 + extract + Bearer"是按文档页臆测的错误
格式，已全部更正。

离线夹具（python/tests/fixtures/sources/mwmbl/）已按上游 schema 重写
（`search_ok.json` 为 SearchResponse 形状、结果带 `engine` 字段），本批仍未
做真实抓取；S0 必须确认实际形状，若不一致需同步更新夹具与 `Mwmbl.parse`。
形状异常（顶层非对象、`results` 非数组、成员非对象、缺 title/url、
`content` 非字符串、重复 JSON 键、非有限数值）一律判 `parse_error`，
绝不当作"无结果"。

**注意 `engine` 字段可能为 `wikipedia`**：mwmbl 会混入维基百科来源的结果。
跨来源去重（dedup_key 融合）会处理这部分重叠，无需在适配器内过滤。

## 429 与每源状态

- HTTP 429 → `rate_limited`，遵守 Retry-After；worker 以
  `shared_budget_key=mwmbl` 设置单源冷却栅栏（同预算后续请求 `skipped`，
  到期自动恢复；见 tests/test_sources_mwmbl.py 的状态机测试）。
- v2 只文档化了 `q` 参数，适配器不发送未文档化的 limit 参数，本地截断结果。

## 缓存与再分发

**2026-11-15 补证（D-S0-3 要求的缓存许可依据，在线复核官方页面）：**

- **mwmbl.org/terms（2026-11-15 复核）**：数据集以 **CC BY-NC-SA 4.0** 提供。
  原文："Data submitted via the Crawling API is made available under the
  CC BY-NC-SA 4.0 license, **consistent with the rest of the Mwmbl
  dataset**."；爬取器收集的数据"considered part of the curated dataset and is
  likewise made available under the CC BY-NC-SA 4.0"（除非受法律或第三方
  权利限制）。软件本身为 AGPL-3.0（代码许可，与数据许可分开）。
- **developer.mwmbl.org（2026-11-15 复核）**：未提及 API 响应缓存的专门
  条款；配额事实与上文一致（匿名 1,000/月、1 req/s 按 IP；免费 key
  2,000/月硬上限、5 req/s；按量 $5/1,000 需主动开启且有消费上限；超额
  429），并新增事实：**配额每月 1 日重置**（"Quotas reset on the first of
  each month"）。
- **mwmbl.org 首页**：仅软件 AGPL-3.0 声明与版权行，未涉数据再分发。

**口径推断（待审核方核定，非本记录自批）**：搜索响应内容派生自 CC
BY-NC-SA 4.0 数据集；本项目自用（非商业）+ **个人本地缓存** 在 NC 许可
范围内；对外输出按 `links_only`（title=URL、snippet 为空）不再分发数据集
内容，URL 本身不构成版权表达。若未来输出摘要/标题，须落实 CC BY-NC-SA
的署名、非商业与相同方式共享义务并重新审核。terms 页主文面向爬取贡献
API，数据集许可条款为全库一致性表述；**不存在专门针对搜索 API 缓存的
许可句**——依据来自数据集许可，不是来自明示的 API 缓存许可。

`cache_policy`、`redistribution_policy` 的最终落库值由审核方在复审时
按上述证据签署；本记录不构成启用许可。

## 结论

候选（candidate），未启用。S0 裁决（D-S0-3，2026-11-14）：三次真实成功
响应解析通过，但 hold——待 F6 修复（401 经 search 路径归类 auth_failed，
已在本批交付）与本节数据集许可依据审核签署后方可考虑 `enabled:true`；
匿名额度维持 D23 口径（30/天）。启用前提：审核方对上述缓存/再分发口径
签署 + approved_by 填写 + 前置修复合入。
