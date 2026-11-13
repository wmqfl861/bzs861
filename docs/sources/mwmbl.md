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

**与当前配置的差异（两个事实同时记录）：** config/sources.json 中 mwmbl 的
`daily_cap=500/天`（`contract.limits.request_quota=500`、
`quota_window_seconds=86400`），与上述**按月**配额口径不一致（匿名 1,000/月
约合日均 33 次）。CL-06 离线批次不改配置；启用前需在用户的 S0/启用决定中统一
配额口径（可能需要下调 daily_cap 或按月记账）。

## 访问与鉴权

- S0 阶段按 D15 使用**匿名额度**（不设 `BZS_MWMBL_KEY` 时适配器不带
  Authorization 头）；之后可申请免费 key 提高月配额（D1：先匿名、后免费 key，
  不开启按量付费）。
- 适配器支持可选环境变量 `BZS_MWMBL_KEY`，存在时以 `Authorization: Bearer`
  头发送。该头部形式与确切响应形状均需 S0 实测确认。

## 响应形状与离线夹具

developer.mwmbl.org 文档化的响应为**顶层 JSON 数组**，成员为
`{title, url, extract}` 对象。离线夹具（python/tests/fixtures/sources/mwmbl/）
按该文档手工录制，本批未做真实抓取；S0 必须确认实际形状，若不一致需同步更新
夹具与 `Mwmbl.parse`。形状异常（非数组、成员非对象、缺 title/url、重复 JSON
键、非有限数值）一律判 `parse_error`，绝不当作"无结果"。

## 429 与每源状态

- HTTP 429 → `rate_limited`，遵守 Retry-After；worker 以
  `shared_budget_key=mwmbl` 设置单源冷却栅栏（同预算后续请求 `skipped`，
  到期自动恢复；见 tests/test_sources_mwmbl.py 的状态机测试）。
- v2 只文档化了 `q` 参数，适配器不发送未文档化的 limit 参数，本地截断结果。

## 缓存与再分发

developer 页面未见明确的缓存/再分发条款：`cache_policy`、
`redistribution_policy` 在配置中维持 `unverified`，**不构成允许结论**；S0
抓取样本回来后复核，再进入启用决定。

## 结论

候选（candidate），未启用。离线解析/状态测试通过；条款额度事实如上。启用前提：
用户 S0 实测通过 + 配额口径统一 + 缓存/再分发条款复核 + approved_by 由用户
（或其委托审核）签署。
