# 固定候选相关性评测与冻结

这是 QA-02 / FT-042 / C-14 的离线工具增量。它准备未标注模板、冻结输入/方法/语料/阈值、计算固定候选的 nDCG@10、MRR@10 和 Precision@10，并报告缺失证据。**工具不会生成人工标签，不修改原黄金集，不请求来源，不调用模型。**

原 40 条查询及 20 development / 15 regression / 5 holdout 保持不变。当前工具只支持 development/regression；没有留出导出或评分开关。相关性结果、来源可用性、解析一致性和完整产品发布验收是不同证据。

## 当前交付边界

- `bzs evaluation prepare` 接受固定候选 JSON，或身份一致的 schema 2 probe 记录，生成只含 `unjudged` / `grade:null` 的模板。
- `bzs evaluation freeze` 冻结包、方法、G0 声明和门槛；缺少环境、阈值或审查记录时仍保存明确 `blocked` 的冻结产物。
- `bzs evaluation run` 使用外部提供的标签计算分组指标及预先冻结的阈值检查。未知标签不会当作 0 分，失败请求不会当作成功 empty。
- 可比较同一语料、方法和标签下的两个排序运行；基线报告的原始 SHA-256 和容忍度必须预先写进 gate。
- 可用 `prepare --ablation-manifest` 预先绑定来源身份和外部 base/移除来源的运行配对，报告固定候选的来源归属、贡献桶及指标差值。没有外部运行时保持 blocked，不由截去基线结果来模拟消融。
- C++ fusion 的自动导出/回放连接器、留出评测、硬条件/引用正确率和独立人审身份认证尚未在本工具实现。生产融合仍由 C++ 执行，Python 只比较提供的名次。报告的完整 `release_gate` 始终为 `blocked`，相关失败计数为 `null`，不会填成 0。

首版支持固定候选相关性，不代表 QA-02、FT-042、C-14 或 S2 已完成。原工作包和增强路径继续保留。

## 准备与执行

下面使用已经存在的 probe 记录。工具只读取文件，不补抓网络样本：

```bash
bzs evaluation prepare --queries tests/golden/queries.json --probe-report probe-out/probe_report.json --out evaluation-pool
bzs evaluation freeze --queries tests/golden/queries.json --package evaluation-pool --gate evaluation-pool/gate.template.json --out evaluation-freeze
bzs evaluation run --queries tests/golden/queries.json --package evaluation-pool --freeze evaluation-freeze/freeze.json --labels evaluation-pool/labels.template.json --out evaluation-unjudged
```

第一条成功表示准备了未标注包。后两条使用未填写模板时预期退出 **3**，产物说明所缺条件。它们不代表门禁通过。所有输出目录必须是新目录；没有覆盖、自动修复 manifest 或原地更新基线的选项。

`python -m bzs.evaluation prepare|freeze|run` 提供相同入口。直接固定候选输入用 `--candidates input.json` 替代 `--probe-report`。默认同时选择 development 和 regression，也可用 `--splits development` 或 `--splits regression`；输入池必须准确覆盖所选查询，不能静默删去没有结果的题。

freeze 和 run 都需要通过 `--queries` 再次提供原始查询文件，才能通过相应的有限门禁。工具核对原字节及相邻黄金 manifest 的 SHA-256、冻结状态、版本，并要求包中的查询选择与原文件完整的非留出 split 投影完全一致，包括次序和全部元数据。只修改包内查询、split、数量或排序后重新计算包哈希，不能继续声称原始黄金集身份。未提供原文件时仍可生成诊断，但明确 `original_golden_not_verified` 并保持 blocked；run 也不会相信 freeze 中自报的验证成功。验证记录只包含哈希、状态及数量，不导出留出文本。

### probe 导入的额外检查

导入要求原始查询字节、黄金 manifest、选项哈希和当前解析器代码/依赖身份一致。它复用已有有界报告/响应体校验，再调用离线解析器比较所选查询的全部保存结果，最多每来源每查询 10 条；只保存 top3 的记录不够。没有 Fetcher 被构建。旧解析器身份发生变化时拒绝导入，不能用“允许变化”把旧结果默认为新基线。

每个来源单独成为一个 system，保留原始次序，不执行 RRF。未请求、pending、reserved、uncertain、skipped 保留为 `unavailable`；已完成网络失败保留为 `failed`。它们不等同于已完成的 `empty`。probe 的 capability 选题可能只覆盖部分所选查询，报告会保留这些覆盖缺口。

probe 只有标题/摘要时，标签依据就是这份结果表示，不是未经读取的目标网页全文。文档身份绑定精确 URL、标题和摘要内容；不同表示不会自动共享标签。没有实现跨来源的语义页面归并，也不以此报告“来源独有全文贡献”。来源登记状态不会被导入操作批准或启用。

## 包内文件

| 文件 | 内容 |
|---|---|
| `package.json` | 包版本及 `pool.json`、`method.json` 的原始字节 SHA-256 |
| `pool.json` | 查询身份与非留出选择、固定文档证据、每题候选全集、各 system 的排序/状态、输入来源身份 |
| `method.json` | 精确版本化的指标、分母、缺标和重复处理规则 |
| `labels.template.json` | 每个 query/document 对的未标注记录；绑定语料和方法哈希 |
| `gate.template.json` | G0 环境/审查人和阈值模板；未提供的值保留 null |
| `observed_environment.json` | 实际工具源码、bzs/Python 版本、系统和架构；不冒充用户目标机器 |
| `preparation.json` | 选择数量、语料身份和 `quality_gate:blocked` |

标签绑定的 `corpus_sha256` 包含查询、文档内容、候选全集和样本 origin，不包含排序结果。排序改变时要创建新包和新 freeze；只要语料/方法完全相同，原标签可以复用。改正文、查询、候选全集或方法都会使旧标签不再匹配。

当前方法版本为 `bzs-relevance-2`，其中登记可选的 `bzs-source-ablation-1` 合同。原有 nDCG/MRR/Precision 数学定义保持不变；方法身份发生变化，所以旧 `bzs-relevance-1` 包会被拒绝，需要重新 prepare/freeze 并按新方法绑定真实标签，不能将旧冻结记录当作本轮记录。候选、标签、包的 JSON schema 仍为 1。

### 固定候选输入格式

顶层固定字段为 `schema_version:1`、`kind:"bzs-fixed-candidate-input"`、`queries_sha256`、`golden_manifest_sha256`、`origin`、`ranking_identity`、`documents`、`pools`、`runs`。不接受未知字段或隐式类型转换。

| 字段 | 合同 |
|---|---|
| `queries_sha256` | 与 `golden.load_golden` 返回的原始输入字节摘要相同；不是自行重新计算后修改黄金 manifest |
| `golden_manifest_sha256` | 冻结集的原 manifest 字节摘要；旧式合成查询 fixture 为 null，保持未冻结 |
| `origin` | `synthetic` 或 `recorded`。这是输入方声明，程序不认证其真实性；synthetic 无法通过真实质量门禁 |
| `ranking_identity` | `{name,code_sha256,config_sha256}`；缺失的代码/配置摘要用 null 并阻断，不默认补权重或排序身份 |
| `documents` | `{document_id,url,title,text,evidence_sha256}`；证据摘要必须等于精确 `{url,title,text}` 的规范 JSON 摘要 |
| `pools` | `{query_id,document_ids}`；每个所选 query 都要有显式全集，允许已知的空池 |
| `runs` | `{system_id,query_id,status,document_ids,reason}`；数组次序就是名次，结果必须来自该 query 的池 |

`status` 为 `ok/empty/failed/unavailable`。只有 ok 可以有非空结果；empty 必须为已知空结果；failed/unavailable 必须给原因。缺少 system/query 行会在报告中保留为覆盖缺口。重复文档 ID、相同证据换 ID、同一排序内重复精确 URL 都拒绝。此处不做新的 URL 规范化或生产排序去重。

规范 JSON 使用 UTF-8、按键排序、紧凑分隔符、`ensure_ascii=False`、拒绝非有限数字；`evidence_sha256` 针对没有末尾换行的规范 JSON。文件身份针对文件实际字节，保留换行差异。

## 来源贡献与外部消融配对

这是 QA-02 的固定候选前置工具，遵守 SEARCH-03 / B-08 / C-07 的来源身份边界。它不运行生产 RRF，也不证明输入者提供的外部运行确实执行过。需要由 C++ 或已有外部导出过程先保存完整来源候选和实际 base/ablation 输出，再用下面的可选入口固定这份材料：

```bash
bzs evaluation prepare --queries tests/golden/queries.json --candidates external-candidates.json --ablation-manifest source-ablation.json --out evaluation-with-ablation
bzs evaluation freeze --queries tests/golden/queries.json --package evaluation-with-ablation --gate reviewed-gate.json --out evaluation-with-ablation-freeze
bzs evaluation run --queries tests/golden/queries.json --package evaluation-with-ablation --freeze evaluation-with-ablation-freeze/freeze.json --labels human-judgments.json --out evaluation-with-ablation-result
```

上述文件是需要外部实际提供的材料，不是仓库内已有真实成绩。来源审批、人类 qrels、外部排名的真实性不能靠填写一个名称或 SHA-256 获得认证；本工具不会自动采集、启用来源、补标签或执行联网消融。只有来源 top10 的 probe 记录本身不能代替融合后的 base/ablation 输出。

### Manifest schema 1

顶层只接受以下字段，不接受未知字段或类型转换：

| 字段 | 合同 |
|---|---|
| `schema_version` / `kind` | 整数 `1` / `bzs-source-ablation-manifest` |
| `input_sha256` | 本次候选输入文件的原始字节 SHA-256；必须与 prepare 实际读取的输入相同 |
| `method` | `bzs.evaluation_ablation.METHOD` 的完整精确对象；不得改成剩余来源的池、缩小 IDCG 或跳过漏标 |
| `sources` | 非空、有序的来源身份数组；来源顺序也固定外部运行的参与来源顺序 |
| `base` | 完整来源集合的外部运行身份对象 |
| `ablations` | 非空的预先声明移除计划和外部运行身份数组 |

每个 `sources` 元素固定包含 `source_id`、`system_id`、`provider`、`capability`、`logical_service_id`、`index_family`、`configuration_sha256`。`system_id` 指向候选输入 `runs` 中该来源实际返回的候选次序和状态。来源 ID 和来源 system ID 都必须唯一；多个 adapter 应通过不同来源记录引用同一个稳定服务身份，不得伪造服务键来拆票。配置摘要未知时写 null，freeze/run 都会明确阻塞。

贡献桶只用于说明候选归属，不实现融合权重：

- 已知 `index_family` 按 **family + capability** 合并。同族两个来源返回同一证据表示时，该表示只有一个族桶归属。
- `index_family:"unknown"` 按 **provider + capability + logical_service_id** 归属。同一服务的多个 adapter 共用桶；不同未知服务保持不同桶。
- 同一 provider/capability/logical_service_id 不允许声明相互矛盾的 index_family。
- 桶 ID 为 `b_` 加结构化身份对象的规范 JSON SHA-256，避免冒号等分隔字符引起碰撞。可调用 `contribution_identity(source)` 和 `contribution_bucket(source)` 取得相同身份。

**检索渠道贡献不是独立证据数。** unknown 桶不能算作已证实独立索引；已知族名也仍是外部身份声明。报告的 `independent_evidence_count` 保持 null、`source_approval` 保持 false。这里按精确 URL/标题/文本表示识别文档，不能把不同片段或版本自动解释为独立网页、全文或语义独有贡献。

`base` 和每个 ablation 的 `system` 对象固定包含：

| 字段 | 合同 |
|---|---|
| `system_id` | 引用候选 `runs` 的实际外部排名；不得复用来源 system 或另一比较 system |
| `source_ids` | base 是全部来源的原顺序；ablation 是仅去掉声明来源后的剩余顺序，允许移除后为空 |
| `implementation` | `cpp`，或仅供显式 synthetic 输入的 `synthetic_fixture`；recorded 输入只接受 `cpp` 声明 |
| `code_sha256` | 实际外部排名程序身份；base 与候选 ranking_identity 的代码摘要一致 |
| `parameters_sha256` | 排名参数身份，排除本次声明移除的来源集合；base 与候选 ranking_identity 的 config 摘要一致 |
| `environment_sha256` | 外部运行环境记录的内容摘要；它与评测程序自身的 runtime/environment 分开 |
| `run_sha256` | 完整查询选择、参与来源次序及实际运行状态/名次的规范摘要 |

配对双方的 implementation、代码、参数和环境必须相同，变化限于声明的来源集合。未知摘要允许 null，但相应配对不可评分通过，freeze 和 run 均保持 blocked。所有外部 system 必须与来源 system 分开，manifest 必须解释候选输入中每个已提供的 system；不得悄悄忽略另一个已提供来源。

`run_sha256` 可使用 `external_run_digest(pool, system_id, source_ids)` 生成。此处 `pool` 至少包含 `input`（完整候选对象）和 `selection`（原黄金输入的完整所选非留出投影）。摘要对象精确为：

```json
{
  "system_id": "external-system-id",
  "source_ids": ["participating-source-id"],
  "query_runs": [
    {"query_id": "selected-query-id", "run": {"system_id": "external-system-id", "query_id": "selected-query-id", "status": "empty", "document_ids": [], "reason": null}}
  ]
}
```

`query_runs` 严格按完整 `selection` 顺序包含每个查询；对应记录缺失时 `run:null`。完整外部 system/query 行没有被截到前十再计算摘要。这个函数只是绑定已存在的输出和缺失状态，不生成排名；给缺失行计算了摘要也不能解除阻塞。上例只是结构说明，不能将未知/失败结果填成 empty。

每个 `ablations` 元素只包含 `ablation_id`、`removal_kind`、`removed_source_ids`、`system`。`removal_kind:"source"` 恰好移除一个来源；`removal_kind:"contribution_bucket"` 必须移除一个已知族或未知稳定服务桶的**全部且仅有**来源。移除列表无重复且保留 sources 次序，不能只删同族的一个 adapter 后声称已移除该族。ID 和移除计划均不得重复。

manifest 原始文件摘要及完整内容嵌入 `pool.json.source_ablation`，受 package/pool/freeze 身份约束。来源身份、配对、次序或外部输出改变后必须重新 prepare/freeze；原 gate 不能重用。冻结时间仍是操作方声明，不证明计划早于外部排名执行或任何人查看数据。没有在看到留出结果后调参的运行路径。

### 贡献、指标差值与缺失证据

每题来源归属来自**已提供 source runs**，为池中每个 document 记录 source IDs 和贡献桶。报告分别列出每个渠道、每个桶返回的表示数、仅该渠道/桶贡献的表示数，以及已标相关的独有表示数。source 发生失败或缺失时，不能据剩余来源断言“独有”；这些计数保持 null。池中没有任何来源归属的文档仍保留在原池并逐条列出，阻塞比较，不会被移走。

只要存活来源的输出都已知，外部排名就必须只引用这些来源提供的证据；在移除某来源/族后仍返回只来自已移除来源的表示，会拒绝整个包。未完成来源则是未知，不会被当作已知空来源。

指标继续复用既有固定候选评分结果，计算 **base − ablation**。nDCG 的 IDCG 和所有标签要求始终使用**原本完整冻结的每题候选池**，包括已移除来源独有的文档。正差值表示这一固定材料中移除后该指标降低；负值表示相反方向。报告不自动把差值声明为真实质量提升或给它添加事后通过阈值。

每个配对按 development/regression、category、language 保留完整计划查询数、可比较数、双方状态计数和指标差值。只要组内一题缺运行、缺源归属、缺身份或缺标，该组完整指标差值为 null；`diagnostic_compared_subset` 另列明确分母的诊断均值，不能当作完整结果。

未提供 manifest 时 `source_ablation` 为 blocked / missing_ablation_manifest，不阻止旧式固定候选有限门禁诊断，但完整 release gate 仍阻塞。提供 manifest 后，其未满足项也加入有限 `quality_gate.blocked_reasons`。synthetic 材料可以产生明确标记的算术诊断，仍因缺真实人类证据保持 blocked。外部材料完整且标签声明满足时，消融部分仅记为 `measured`；它不等于原 QA-02/FT-042/C-14 签收，完整 release gate 继续缺留出、约束/引用证据及独立人审认证。

## 人工标签与审查记录

每条 judgment 包含 `query_id/document_id/evidence_sha256/grade/origin/assessor_id/assessed_at/note`。模板全部未判，不自动填负例。

- `origin=unjudged` 要求 grade、assessor_id、assessed_at 全为 null。
- `origin=human` 表示输入者声明由人类标注；需要整数等级 0–3、标注者标识和带时区的时间。程序只验证记录结构与证据身份，不能通过名字证明这个人实际参与过。
- `origin=synthetic` 只用于显式合成素材/数学测试；可以计算诊断分数，不能当作真实人工质量成绩。
- 同一对重复或有冲突的标签拒绝。先完成真实裁决并保存新版本，不能依赖“最后一条覆盖”。

完整 `release_gate` 继续要求实际独立审查，而不是把程序里的角色名当成独立人审。不得用 AI 生成的等级或虚构标注者填完真实标签。真实材料的版权、隐私、来源审核与公开存储范围仍由原来源合同约束；本工具不会自动上传或发布包。

## G0、基线与阈值

gate 绑定准确的 package、pool、corpus、method 哈希。环境声明包括 id、hardware、software_sha256、command；只有实际运行时软件摘要自动写入模板，其余未知项仍为 null。review 包含 reviewer_id、reviewed_at、evidence_sha256，作为外部审查声明而不是身份认证。

固定阈值字段为：

- `ndcg_at_10_min`、`mrr_at_10_min`、`precision_at_10_min`；
- `judgment_coverage_min`、`query_coverage_min`、`answer_coverage_min`；
- `abstention_rate_max`。

数值只能是有限的 0–1；没有默认通过阈值。阈值用于每个 system 的各 split 总组；语言和 category 子组另行完整报告，首版没有隐式给子组套用另一套门槛。未知阈值、没有正相关标签、没有回答样本、缺少候选全集、未完成查询、未标注或非真实人工标签均不能通过。覆盖阈值不能覆盖方法中“缺标阻断”的规则。

G0 应先登记场景/版本/方法和语料；M2 在已声明环境采基线；M3 前完成实际审查并冻结非回归和覆盖门槛。当前工具没有留出运行路径，因此不能用它读取留出成绩后继续调参。原黄金集保留在仓库中不等于工具可以证明任何人从未看过它。

`non_regression` 默认全部 null。启用时必须提供原 baseline report 的精确 SHA-256，以及三项 `*_max_drop`。运行时再传 `--baseline`，不能看完结果临时指定一个没冻结的基线。比较需要同一方法、语料、标签字节、评测工具和声明环境；允许排序代码/结果改变，并把它们作为两个独立输入记录。容忍度作用于“基线减当前值”。基线自身未通过有限质量门禁时，数值差仍可用于合成诊断，但不能据此通过新质量门禁。

freeze 是内容与运行身份记录，不是密码学签名或可信时间服务。重新创建 freeze 是显式的新记录；不能把哈希本身宣传为独立审查、真实人类或冻结时间的证明。

## 数学约定与缺失数据

使用 `gain=2^grade-1`、折扣 `log2(rank+1)`。nDCG@10 的理想排序来自**整个冻结的该题候选池**，不是本次返回前十；漏标池中未返回的候选也会使 IDCG 不完整，主指标保持 null。没有相关候选时 nDCG 约定为 0，并单列 `no_relevant_queries`。

Precision@10 的分母固定为 10，结果不足十条也不缩小分母。MRR@10 只看前十内第一个 grade≥1 的结果；不存在则为 0。它不冒充不截断的 MRR。没有受控完整相关全集，故不报告全网召回率。

主指标按每题宏平均，development/regression 分开。任何题不可评分时，该 split 的完整主指标为 null；`diagnostic_scored_subset` 会同时给出可评子集的分母和诊断均值，不能把子集均值当完整通过成绩。

- `query_coverage` = 成功 ok/empty 数 ÷ 计划查询数。
- `answer_coverage` = ok 数 ÷ 计划查询数。
- `abstention_rate` = 已知 empty 数 ÷ 计划查询数；失败不伪装为弃答。
- `judgment_coverage` = 有等级的 pair 数 ÷ 固定池 pair 数，同时分别报告 human/synthetic/unjudged/absent。

报告保留每题状态、每 split / category / language 的分组、缺标对及全部阈值检查；查询文本与正文不会复制到评分摘要。

## 退出码与验收解释

| 操作/状态 | 退出码 | 含义 |
|---|---:|---|
| prepare prepared | 0 | 成功准备未标注模板；quality gate 仍 blocked |
| freeze frozen | 0 | 完成该份明确声明的冻结；不表示已经评分或人审认证 |
| run passed | 0 | 有限固定候选门禁满足已声明合同；完整 release_gate 仍 blocked |
| run failed | 1 | 至少一项已知数值违反冻结门槛；其他未知条件仍单列 |
| 输入/证据/身份无效 | 2 | 不生成一个“成功”报告 |
| freeze/run blocked | 3 | 标签、门槛、环境、覆盖或有效基线尚不足 |

## 有界行为与测试

JSON 文件上限 64 MiB，小型 manifest/gate/freeze 上限 1 MiB，嵌套上限 32；最多 20,000 个文档、80,000 个 query/document 对、200 个 system、每题 2,000 个候选。probe 自身继续使用原有报告/响应体上限。重复键、NaN/Infinity、布尔数值、错误 Unicode、符号链接输入、非普通文件以及原输出覆盖被拒绝。

可选 ablation manifest 上限 1 MiB，来源/base/ablation 的声明 system 总数不超过 200，计划的 system/query 对及来源候选归属关系分别不超过 80,000；缺失的 system 也计入计划上限。输出继续受 64 MiB 限制。

`python/tests/test_evaluation.py` 使用单独合成查询和显式 synthetic 标签。测试包括手算指标、固定分母、全池 IDCG、漏标/冲突、empty/失败/未知区分、输入与软件漂移、修改查询投影后重算哈希、原文件缺失或字节变化、门槛缺失、留出拒绝、来源完整前十重放、证据损坏、无外发连接以及真实 CLI 进程的预期 blocked 退出码。这些合同测试不提供真实来源可用性、人类相关性或用户目标机器性能成绩。

`python/tests/test_evaluation_ablation.py` 另外覆盖外部配对、保留原 IDCG、被移除但未标文档、同族与未知服务归属、缺源不能提高独有计数、外部 empty/failed/missing 区分、完整 query-run 摘要、移除范围/名次越界、缺身份阻塞、包冻结漂移和 CLI 未标注输出。fixture 的排名及相关性等级都是显式 synthetic；没有调用 Python RRF 或新增真实人类标签。
