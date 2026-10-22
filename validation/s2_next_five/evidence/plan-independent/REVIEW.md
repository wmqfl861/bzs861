# 最终计划保持与文档一致性：独立只读审查

## 结论与范围

与已发布基线 `2c536e9834c4897b0c70e3bd6651b765cc67f45e` 相比，当前主工作树保留原 83 工作包、10 切片、161 验收的定义、正文、依赖、估算与必须延后完成的语义，状态仍为 **36 partial / 47 not_started / 0 accepted**。保护文件的哈希及文件集合核对均通过。

本次未运行产品测试，未修改主仓库，未读取或解析黄金查询和 holdout 文本。检查的是当前未提交文档的内容与哈希；父任务后续的 final-v2 冻结、完整运行成绩、发布提交与 CI 仍应单独绑定实际输入。

唯一指出的问题是 HTTP 文档对抓取前沿的措辞歧义，父任务已确认将在 final-v2 前修正。最新同步的 `DEVELOPMENT_STATUS` 与 `NEXT_AGENT` 已复看，未发现其他具体矛盾，也没有预写最终门槛或未来 CI 成功。

## 结构与语义核对

| 对象 | 独立观察 |
| --- | --- |
| 原始 backlog | `requirements_and_backlog.json` 原字节不变；83 features 与 161 acceptance 完整对象不变。 |
| 83 工作包 | `slices_v1.2.json.packages` 逐个完整对象相等，包括顺序、ID、名称、切片、依赖、估算、审查约束及未签收状态。 |
| 10 切片 | `slices` 完整对象和顺序不变；总估算不变。 |
| 161 验收映射 | 除每条 `execution_status` 的版本从 `not_signed_off_for_0.1.8` 改为 `not_signed_off_for_0.1.9` 外，原验收正文、适用性、等效要求、feature/slice 关联和 evidence 字段全部相同。 |
| 切片 JSON 的其他变化 | 仅 `review_revision`、`implementation_stage`、`scope_policy.current_report`。连同上述 161 条，共 164 个允许的叶字段变化，没有额外变化。 |
| implementation status | 83 条 feature 的身份、状态、切片、基线验收 ID、保留主线说明及整包签收标志一致。没有新增 accepted 或切片签收。 |
| mandatory deferred semantics | 对全部 83 条 feature 比较字段存在性和完整值；其中 17 条显式字段原值保留，其余 66 条字段存在性不变。 |
| 当前剩余工作说明 | CACHE-01、ROBOTS-01、QA-02 的 3 条 `remaining` 更新反映本轮局部实现；仍明确保留完整 HTTP/共享取消、完整 robots 验收、真实 qrels/消融/留出及完整 release gate 等未完成要求，没有修改原验收或 mandatory 字段。 |
| 蓝图 | 原第 1 节开始至上一轮执行附录的正文保持不变；顶部仅更新本轮版本/日期/报告链接，末尾追加本轮执行说明。 |

结构保持由单独脚本进行对象比较和哈希验证，不依赖只统计 ID 数量或单独检查依赖无环。

## 原字节保持

| 文件组 | 数量 | 结果 |
| --- | ---: | --- |
| `validation/s2_five_stages/` 历史证据 | 241 | 文件集合完全一致，所有文件 SHA-256 相同。 |
| `tests/golden/` | 2 | 文件集合恰好为 manifest 和 queries，SHA-256 均相同；只哈希，不解码、解析或导出。 |
| 其余应保持的规划源文件 | 21 | 原规格、v1.1 蓝图/计划、backlog、review、schema、工具和合同样例原字节保持。 |
| 默认来源与站点配置 | 2 | `config/sources.json` 与 `config/sites.json` 原字节相同。另只读解析配置确认 17 条来源全部明确 `enabled=false`，站点为 `[]`。 |

黄金输入哈希：

```text
tests/golden/manifest.json
a9a471b885c5cb04b76d08b1c6a8b0db93334dea14d78f92aa1b7908093391c5

tests/golden/queries.json
52948aca968cb581e7828988f80a8043f419799938cc1fb6fa54d43a60d2b798
```

## 文档一致性

审读 `HTTP_REVALIDATION`、当前蓝图、本轮 S2_NEXT 计划及最新同步的 `DEVELOPMENT_STATUS`、`NEXT_AGENT`。

唯一问题 DOC-01：`HTTP_REVALIDATION.md` 的“核心负责的持久绑定”第 4 条写“304 不扩大抓取前沿”，但下文及已审查行为允许使用完整保留链接、按当前任务父节点和深度重建 frontier。frontier 本身可以增加候选；不能放宽的是当前权限、范围或预算。建议表述为：

> 304 不放宽抓取权限、站点范围或预算；只使用已验证的保留链接按当前任务深度重建，并重新核验当前限制。

父任务已确认该修正。这属于文档歧义，不是新增运行逻辑问题。本审查记录保存修正前的文档哈希，修正后由 final-v2 输入单独绑定。

其余说明与已审查范围一致：304 不解除旧正文发布限制；过期 robots 表示不能直接准入；最新决策回收不能暴露旧许可；五个增量均属于已有 partial 包；Python 不复制生产 RRF；全池 IDCG 与缺失证据阻塞保留；真实质量、长期运行、共享取消、独立撤销连续性和隔离恢复没有被局部回归替代。C-13 与 B-39 仍分别对应依赖完整性、多节点租约/限流，未把有限容量循环算成这两项通过。

本次文档审查没有重新执行文档引用的升级、原生、Worker 或进程测试，也不单凭文字认证这些执行数字。最终成绩段当前明确 pending，父任务将据真实运行填写。

## 证据

- `plan-preservation.json`：17 个结构/保持条件全部成立，包含逐文件旧/新哈希、允许的 164 处叶字段变化及进度字段差异组。SHA-256：`e8497ac00f20d46c5b0cd1c6cd63bfc45ad8ce9b5097bee305fe090bacfd9580`。
- `document-review.json`：最新同步文档的输入哈希、唯一措辞问题及限定范围。SHA-256：`1c86083df8ec3fd862b3f163824a475a466ab963fb7db0856169cc59289fa1d5`。
- `check_plan_preservation.py`：实际只读比较脚本；只向本审核目录写入记录，不改主仓库。

本报告不构成 S0/S1/S2、任一工作包或 161 项原验收的整包签收。
