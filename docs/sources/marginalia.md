# 来源审核记录：marginalia（通用网页搜索 API）

| 字段 | 值 |
|---|---|
| 来源 ID | `marginalia`（config/sources.json） |
| 端点 | `https://api2.marginalia-search.com/search`（`API-Key` 请求头） |
| 适配器 | `marginalia_api`（python/bzs/worker/sources/apis.py `Marginalia`） |
| 类型 / 索引族 | API / `marginalia`（与 mwmbl 互相独立的通用网页索引族） |
| 审核日期 | 2026-11-13 |
| 到期日期 | 2026-12-13（到期或启用前需复核；本记录不是启用许可） |
| approved_by | NOT-ENABLED（占位：待用户完成 S0 实测后填写；本批保持 `enabled:false`） |
| 条款 URL | <https://about.marginalia-search.com/article/api>（页面更新 2025-12-08） |

本记录属于 CL-06 离线准备批次：编写过程中**未做任何网络访问**，事实来自
2026-11-12 的外部条款复核（review-main.md 第 4 节 CL-06、第 7 节 D1；阶段 1.5
复审 D15）。

## 配额事实（原样记录）

> 免费非商业 key 需发邮件申请（条款页给出的申请方式）；共享 key `public` 可
> 用于试验，但经常被限流（HTTP 503）。来源：about.marginalia-search.com/article/api
> （2026-11-12 复核，页面更新日期 2025-12-08）。

**D15（阶段 1.5 复审已定）：** 用户自己的 key 尚未到达时，S0 探测可先用官方
共享 key `public`，且必须在探测报告中注明；S1 签收（tools/accept_s1.py）必须
使用用户自己的 key。当前适配器在 `BZS_MARGINALIA_KEY` 未设置时会回退到
`public`（apis.py `Marginalia.request`），accept_s1.py 需检测并阻止"缺 key
自动回退 public"（计划 v2 修正 F3）。

## 访问与鉴权

- `API-Key` 请求头携带 key；无 key 时适配器回退共享 key `public`（仅限试验/
  S0 语义，见上）。UA 使用诚实的 `API_USER_AGENT`（base.py），符合项目原则。
- 503（共享 key 限流）→ `rate_limited`，10 分钟冷却，不绕过。

## 缓存与再分发

条款页对结果数据标注 **CC-BY-NC-SA 4.0**（署名—非商业—相同方式共享）：
缓存与再分发结果须附署名并保持非商业用途。本项目为个人非商业搜索工具，配置
中按 `redistribution_policy=links_only`（仅链接）记录；`cache_policy` 维持
`unverified`，S0 后复核再定。启用后在任何对外输出中不得去掉 Marginalia 署名。

## 响应形状

适配器解析 `{"results": [{url, title, description}]}`；HTTP 200 携带
`{"error": ...}` 的应用层错误判 `upstream_error`，形状异常判 `parse_error`
（tests/test_sources.py 已覆盖）。

## 结论

候选（candidate），未启用。免费 key 需用户发邮件申请（D1）。启用前提：用户
key 到位 + S0 实测通过 + 缓存/再分发条款复核 + approved_by 由用户（或其委托
审核）签署。
