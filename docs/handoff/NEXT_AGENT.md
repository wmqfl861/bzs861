# 下一批任务：0.1.2 S0/S2 development candidate

继续 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。用户已授权按 v1.2 蓝图修复、继续开发并适时 push。本批输入为已交付 0.1.1 的 `426c431`，增量目标为 S0 黄金与探测证据、S2 显式批量读取；原 main 与 Claude S1 的来源记录继续保留。

## 先读取

1. 真实远端 main、当前分支/工作区、[本批交接](S0_GOLDEN_20261018.md) 与 [0.1.1 接收审查](CLAUDE_S1_REVIEW_20261018.md)。历史输入 95710ba / 2b121fc / 426c431 都不是后续发布 HEAD。
2. [README](../../README.md)、[状态](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)、[本批报告](../../validation/s0_s2_progress/test_report.json)。
3. [API](../API.md)、[CONFIGURATION](../CONFIGURATION.md)、[SECURITY](../SECURITY.md)。
4. [蓝图 v1.2](../planning/development_blueprint_v1.2.md)、[切片/验收映射](../planning/slices_v1.2.json)、保留的 v1.1/v0.3 与原 backlog。

## 当前边界

阶段为 S0/S2 development candidate；83 个工作包、161 项验收全部保留，当前 30 项部分实现、53 项未开始、0 项完整签收，S0/S1/S2 均未完成。本批 BATCH-01 推进为部分实现，QA-02 与 SESSION-02 仍未开始。保留三类原生合同、离线导入/检索/证据、用户隔离、任务与隔离备份、Python worker/MCP、中文/BM25 与联网候选路径。不要把随包 implemented 当完整验收，也不把历史日志写成本批成绩。

正常来源需 contract 审核并启用，17 条登记默认禁用。S0 probe 必须显式 include-candidates 和非空 only；sources 是文件路径。生产 proxy、remote DNS、allow_cidrs 放宽拒绝；假网站回环仅在受控测试中使用。

黄金查询 40 条的原文字和 ID 不变，manifest 冻结为 20 development / 15 regression / 5 holdout；默认按 capability/category 选题，不选 h* 留出，不用无关能力补足数量。不得在观察质量结果后静默改题、改 split 或重算 manifest。当前没有人工 qrels、nDCG/MRR 成绩或 QA-02 签收。

probe schema 2 绑定输入、解析器和依赖；预留先落盘，恢复不自动重发 completed/uncertain。预算只覆盖同一次 probe。离线 replay 不发网络请求、默认拒绝解析器身份变化；`--allow-parser-change` 仅允许显式比较，不能忽略证据损坏或解析差异。有限云端可达性记录不等于用户机器 S0，也不改变正式来源审核状态。

S2 当前候选只处理最多 20 个显式 URL 的静态读取列表，使用 `read_many.submit`、`job.get/results/control`。它不发现链接或递归爬取；robots、map、crawler、会话重取、独立撤销日志仍待对应验收。API 的未知用量保守计费、失败项显式重试和旧执行发布屏障不能被后续简化。

## 接续顺序

1. 先核对本批源码哈希与对应 GitHub CI。0.1.1 报告中的 81 核心 / 55 合同 / 20 HTTP / 12 E2E / 122 Python 和原生 sanitizer 仅是历史基线；当前结果由新报告绑定，不预设沿用旧计数。
2. 保留本批 schema 4 对 schema 1、两种历史 schema 2 与 schema 3 的迁移，以及快照/证据/删除/会话/隔离回归；处理既有数据库前核对其来源并保护原始数据，禁止用原 S1 二进制盲开当前数据库。
3. 核对核心/worker 来源合同身份与有效审核、跨源凭据剥离、出站边界、Retry-After 和索引族贡献。
4. 核对真实提交的 Linux/Windows Actions、固定依赖和失败结果；不以 skipped 或非阻塞失败冒充通过。
5. 按 [S0_PROBE](../S0_PROBE.md) 用冻结输入在目标部署环境完成候选来源探测和离线解析回放，另完成来源审核；至少 2 个实际可用来源含真实通用网页源，再逐项判断全部 S1 标准。云端样本另看 [摘要](../../validation/s0_s2_progress/cloud_probe_summary.json)，不要扩张其环境结论。
6. QA-02 先建立真实人工标注、冻结阈值和留出评测，补来源独有贡献与失败分类；不能把解析回放一致性当相关性。
7. S2 在静态批量任务候选基础上继续 robots 前置、发现/爬虫、会话重取及独立撤销/隔离恢复；无撤销连续性不能恢复服务。后续全部范围按依赖推进。

## 长期约束

业务与权限只维护一套核心实现，MCP/HTTP/CLI 不复制规则；worker 不写数据库。模型增强未来可使用用户配置的本地/远程模型，但不开放网页到模型内网白名单。来源只接经过审核的免费入口，不绕过限制或付费回退。

传输/存储机制替换保留等效验收：MCP 通知不自动满足断线重取，单库向量也需维度/身份/权限/恢复验证。原 v1.1、规格、Schema 和历史证据保持原状；修改当前规划必须保留全部 ID 与验收映射。私人输入、凭据和运行数据库不公开。
