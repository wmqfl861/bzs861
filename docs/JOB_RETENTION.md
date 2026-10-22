# 终态任务明细回收与永久摘要

`jobs.compact` 回收当前已认证 owner 下已经稳定终结的 `web.read_many`、`web.map` 和 `web.crawl` 明细，释放后续任务可使用的逻辑容量。原任务身份、幂等键、输入指纹和结算摘要永久保留；压缩后的旧任务不会重新派发请求。该操作不请求来源、不调用模型，也不读取正文。

## 调用方式

```json
{"operation":"jobs.compact","params":{"limit":16}}
```

`limit` 默认 16，允许 1–64，表示本次最多压缩的任务数。省略使用默认值；零、负数、超界数、布尔值、数字字符串、显式 null 和未知字段均拒绝。owner 来自认证身份，不能指定他人，也没有缩短保留期的参数。

CLI 和 MCP 映射同一核心操作：

```bash
bzs jobs compact --limit 16
```

MCP 工具为 `compact_job_history`，在 SDK 转换前检查原始 JSON 类型，数值范围由核心决定。它声明会删除明细，重复调用可能回收下一批，因此不标记为幂等。C++ CLI 可通过 `searchctl call request.json` 调用相同请求。

没有联网 Worker 的核心同样提供回收。回收不会主动恢复或终结未结算任务；不确定执行须经正常恢复流程先结算，才可判断是否合格。

## 回收资格

固定保留期至少 **7 天（604800000 毫秒）**。核心同时要求 `created_at`、原始 `deadline`、`lease_until` 均早于或等于“当前核心时间减七天”，相当于从三者最大值计算。不能把“七天前取消”直接视为到期，原截止时间可能更晚。

任务还须符合以下条件：

- 类型为 `web.read_many/web.map/web.crawl`，状态为 `succeeded/failed/cancelled`。
- 没有 pending/running 条目、运行中的物理请求跳或未结清请求/字节预留。
- 原预算账本完整，已知用量加未知结果保守计费与 charged 总数一致。

排队、运行、暂停及中断后尚未结算的任务不会压缩。未知结果不能借回收退成零用量；原恢复流程须保留整份不确定预留计费。时间回退不会让未来截止时间成为可回收对象。固定 fixture/import 任务不在本次明细回收范围内，其永久身份仍计入共同键上限。

新任务接纳前另做一次同 owner 有界维护，最多压缩 8 项。该维护与接纳处于同一事务，后续接纳失败可使维护回滚；需要独立恢复空间时调用 `jobs.compact`。自动维护不保证一次清空任意积压。

## 回收后的接口

`job.get` 返回原 `job_id`、kind、终态、截止时间、派发/进度统计、`budget` 和 `usage`，并增加：

```json
{
  "retention": {
    "version": "bzs-job-retention-1",
    "details_available": false,
    "compacted_at_ms": 1792454400000,
    "minimum_retention_ms": 604800000
  }
}
```

示例时间只展示字段形状。单份摘要最多 8192 字节，保留已知/未知请求与字节、charged/remaining 预算、页统计、partial/stop_reason 等聚合结果。map/crawl 原有站点 ID、origin、scope 和清单身份继续保留；逐项 URL、候选、请求回包、正文和快照 ID 不存入摘要。

原 owner 同参数重用幂等键，在通过入口当前准入后返回原摘要及 `reused=true`。改变参数、类型或绑定配置仍为 `E_IDEMPOTENCY_CONFLICT`（409）。当前站点审核失效等错误可能先于幂等查询返回，但不会创建另一份旧键任务。

`job.results`、对应 `crawl.edges` 和 `job.control` 返回 **`E_JOB_RESULTS_EXPIRED`（410）**，应改读 `job.get`。已压缩明细不能恢复、重试或取消；另一个 owner 查询仍为 `E_NOT_FOUND`（404），不通过 410 暴露存在性。

`usage.scope=job_lifetime` 仍指任务累计用量，轮询不再次消耗这些请求，不能逐次相加。摘要的 `body_read` 等表示历史执行状态，不是当前正文读取授权；已有快照和证据仍经各自接口重新鉴权。

## 返回统计

| 字段 | 含义 |
|---|---|
| `version` | `bzs-job-retention-1` |
| `minimum_retention_ms` | 604800000 |
| `limit` | 本次有效任务上限 |
| `compacted_jobs` | 已保存永久摘要并删除明细的任务数 |
| `deleted_detail_rows` | 删除的条目、预算、前沿、候选、请求跳、边与 trap 观察行数 |
| `deleted_robots_records` | 删除的无引用过期 robots 记录数 |
| `has_more_jobs` | 当前 owner 还有可压缩任务；不表示 robots 积压 |
| `retained_job_keys` | 当前 owner 全部任务身份数，包含已压缩和未压缩任务 |
| `limits` | 永久键、单批任务/robots 和摘要硬上限 |
| `external_requests/model_calls` | 维护操作均为 0 |

可以按 `has_more_jobs` 分批继续调用。并发执行会改变后续资格，一次返回零不表示以后永远没有可回收对象。

## robots、引用和事务

robots 到期后必须没有保留请求跳引用才可成为回收候选；最新决策在任何较旧记录仍存在时继续保留，避免回收较新拒绝后重现旧允许状态。回收先处理无引用旧记录，在总量上限内可再次处理已无旧记录的过期最新头。检查同时覆盖实际使用该记录的准入引用，以及成功抓取 robots 后保存的回执引用。显式维护最多删除 `min(64,limit×4)` 条；robots 接纳达到原缓存容量边界时另有最多 64 条的同 owner 清理。

快照、正文、证据和活动会话引用不是本次删除对象，原快照保留/引用规则独立生效。页面/站点发布历史、owner 删除水位、同源配额/冷却、派发序号均保留。清理不能扩大准入、抹去计费或让旧内容重新发布。

摘要写入与明细删除在同一 SQLite 写事务中完成；任一步失败整体回滚。schema 9 增加摘要字段及 robots 引用索引，后续集成 schema 保留这些迁移。历史缺失的业务证据不能由升级补造，隔离副本仍先拒绝服务。

## 容量与验收边界

| 对象 | 硬上限 |
|---|---:|
| 同一 owner 永久任务身份 | 10000 |
| 部署全部永久任务身份 | 50000 |
| 一次维护任务数 | 64 |
| 一次维护 robots 数 | 64 |
| 单份永久摘要 | 8192 字节 |

原 map/crawl 明细、资源、候选、请求跳、边和 trap 容量继续生效。回收释放逻辑容量及 SQLite 可复用页，不承诺文件立即缩小，也不执行 `VACUUM`。永久身份最终仍可能到顶，新任务返回 `E_QUOTA`（429），不能手工删键或重置水位绕过。

该增量部分实现 STORE-01/JOB-01/CRAWL-01，不等于通用长期 GC、任意断电、分布式调度或安全恢复已验收。任务摘要和页面水位不替代独立撤销日志。实际执行以 [本轮验证记录](../validation/s2_next_five/README.md) 绑定的源码、命令和日志为准。

0.1.9的 `tests/capacity_cycles.cpp` 通过公开API反复触达512任务明细上限、维护后重新接纳，并检查永久键不复用、跨owner拒绝、持续到达轮转、未知预留结算及robots准入/回执pin。测试使用明确可控时钟和有限循环，SQL只读观察；不能把这些计数标成真实长期容量或多节点验收。
