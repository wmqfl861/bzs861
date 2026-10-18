# S0：黄金查询、候选来源探测与离线回放

本批目标：0.1.2；S0、S1、S2 均未签署完整验收。17 条来源登记中，13 条已接入适配器、4 条为后续候选，默认全部禁用。S0 收集可达性、阻断、解析、延迟与真实结果样本；**探测结果不自动启用来源，也不等于来源审核通过。** 本轮实现与实际运行绑定见 [进度报告](../validation/s0_s2_progress/test_report.json)，有限云端探测见 [cloud_probe_summary.json](../validation/s0_s2_progress/cloud_probe_summary.json)。执行环境中的样本不能写成用户机器已完成 S0 验收。

## 固定黄金查询

[queries.json](../tests/golden/queries.json) 保留原 40 条查询的 ID、文字、分类、语言与适用能力，只新增明确的 split。当前划分为 **20 条 development、15 条 regression、5 条 holdout**；h01–h05 留出题保持保留。非留出查询在各原分类内按原顺序交替划入 development/regression，划分发生在本批相关性标签和排序评测之前。

[manifest.json](../tests/golden/manifest.json) 冻结输入 SHA-256、原查询内容指纹、40 个 ID 顺序、分类/语言/split 数量和留出 ID。文件哈希只兼容 LF/CRLF 检出换行差异；运行报告另记录实际原始字节哈希。加载器不会自动更新 manifest。修改固定数据需显式版本化、复核差异，不能重新生成 hash 后把改变当作原基线。

默认选题先匹配来源 capability，再按 category 确定性轮转，分类内保留原顺序；默认每源最多 8 条。h* 保留 ID 和显式 holdout 不参与默认探测，无匹配则不请求、不回退到无关 capability。实际选中 ID、分类、语言和 split 写入运行计划。

`--queries` 可指定旧式 `{ "queries": [{ "id": "q1", "query": "SQLite FTS5", "good_for": ["web"] }] }` 输入；它标记为未冻结，不冒充上述 40 条黄金集。加载器限制每个 JSON 输入至多 1 MiB、1–1000 条查询、单条文字至多 1000 字符/4096 UTF-8 字节；固定黄金集必须恰好 40 条。重复 JSON 键/ID、未知字段、错误类型和非法分类/split 等被拒绝。当前查询语言元数据为 zh/en，适用能力为 web/encyclopedia/papers/code/news；旧式输入省略 good_for 时沿用 web。

本批**没有人工相关性标签、qrels 或排序质量成绩**。黄金冻结对应 QA-01 / FT-008 与质量冻结前置 C-14；QA-02 / FT-042 的人工标注、nDCG@10、MRR、来源独有贡献、阈值和留出评测仍是后续工作。

## 前置条件

先按 [README](../README.md) 完成安装并确认本轮离线和安全回归，再选择要探测的来源。探测会向所选公开入口发送查询，保存返回页供本地复核；遵守本次探测的次数/间隔与 Retry-After，遇验证码或限流停止该来源服务预算，不绕过访问控制。

探测报告的 `quota_scope=this_probe_run` 表示预算只覆盖这次 probe。它与 searchd 部署账本分离，不合并正常搜索或多次 probe 的每日/共享配额；不能用这份报告声称全局额度已经扣账。重复运行仍需遵守来源实际配额，跨进程/跨次统一账本属于后续验收。

生产代理、`proxy_remote_dns` 和非空 `allow_cidrs` 被拒绝；回环假网站映射只属于受控测试。无法访问来源时保留实际错误，不用放宽策略伪装成可用。

## 显式运行

```bash
. .venv/bin/activate
bzs probe --sources config/sources.json --include-candidates --only marginalia wikipedia_en --per-source 2 --out probe-out
```

- `--sources` 指向清单文件路径，不是来源名称列表。
- `--only` 是非空来源 ID 列表；候选探测必须明确指定这些 ID。
- `--include-candidates` 显式允许本次探测未审核候选，不改变正常核心或 worker 的准入状态。
- 不带候选开关时，只能使用正常准入允许的来源；不能通过筛选 ID 或另一 RPC 绕过审核。

实际间隔、次数和超时以 `bzs probe --help` 与运行报告为准。完整 S1 标准需至少 2 个实际可用来源，包含至少 1 个真实通用网页源；数量和成功率只是其中一部分。

## 中断、预留与恢复

schema 2 报告保存运行身份、逐项计划和预算。每次请求先持久预留；计划状态区分 pending、reserved、completed、uncertain、skipped。无法确认是否已经派发的请求保留为 uncertain，不自动重发，也不当作免费请求退还预留。报告写明没有执行的原因，不把无匹配查询或预算停止从来源清单中静默删掉。

中断后可以在相同输出目录使用 `--resume`，并保留原清单、查询和选项，例如为上面的命令追加 `--resume`。恢复需要输入哈希、选项、解析器代码与依赖身份一致；completed/uncertain 项不会自动再发。恢复继续的是同一次 probe 的账本，跨次 probe 和 searchd 部署账本仍然分离。

## 输出与解释

工具在输出目录生成 `probe_report.json` / `probe_report.md` 以及 `raw/` 下的响应样本。schema 2 JSON 包含 run_id、status、identity、options、sources、plan、rows、budgets、environment、summary。identity 绑定来源清单、查询原始字节、黄金 manifest、选项、解析器代码及依赖；每个响应记录安全元数据、响应体 SHA-256 和至多 10 条完整解析结果。响应体用 gzip 存储，哈希与长度核对的是解压后的原始响应体，供后续回放和标注使用。

| 结论 | 解释与下一步 |
|---|---|
| `reachable` | 本次样本达到探测阈值，只表示有限样本的可达性；继续核对来源审核和结果质量，不自动启用 |
| `blocked` | 验证码、封禁或限流；保持停用或冷却，保留证据 |
| `needs_work` | 请求、结果或解析不足；用样本修复并回归 |
| 网络/配置错误 | 记录真实原因，不等于来源已失效或已可用 |

报告中的可用率不能证明条款允许、免费状态、自动化访问、缓存/再发布条件或索引族独立性。正式启用需在运行来源的 `contract` 中提供符合 [source-manifest Schema](planning/schemas/source-manifest.schema.json) 的完整审核和有效日期，并与运行身份、端点及配额一致。正常请求由核心和 worker 各自校验。

CLI 只有报告中存在实际 ok/empty 响应才返回 0；全部失败或没有请求返回 1，输入错误返回 2，中断返回 130。退出 0 不是来源批准或 S0/S1 验收通过。来源出现阻断/限流时，其结论优先于先前成功样本，不被平均成功率覆盖。

## 离线解析回放

```bash
bzs replay --report probe-out/probe_report.json --out replay-out
```

回放只读取已保存证据并调用离线解析器，`network_requests=0`；不会补抓缺失样本。默认严格核对原解析器代码与依赖身份；不同版本需显式 `--allow-parser-change` 才进入比较，差异仍写入报告，不能改成默认通过。输出目录非空会拒绝，避免覆盖上次证据。

回放状态为 passed、failed 或 no_replayable_rows，summary 记录 replayed/matched/mismatches/skipped；只有 passed 返回 0。无可回放样本不能称解析回归通过。离线解析一致性、来源可用性和人工相关性是三种不同证据。

## 样本处理与验收

原始响应是外部不可信数据，可能包含地区信息、追踪参数或页面个性化内容。先本地检查、去除不适合公开的内容，再选取必要样本进入测试；不要把整个探测目录默认提交到 Git。

每个真实来源的结论应绑定清单版本、审核日期、环境和具体查询报告；条款或接口变更要重新核验。S0 完成后仍需对照 [S1 全部标准](planning/development_blueprint_v1.2.md)，不能以探测工具运行一次代替整个切片验收。
