# 下一批任务：0.1.1 S1 review candidate

继续 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。用户已授权按新蓝图修复并继续开发；当前整合已交付 main 与 Claude S1，不能用单个包覆盖另一条历史。

## 先读取

1. 真实远端 main、当前分支/工作区、[本轮接收审查](CLAUDE_S1_REVIEW_20261018.md)。接收时 main 为 95710ba，bundle 为 2b121fc，这些不是未来 HEAD。
2. [README](../../README.md)、[状态](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)、[本轮报告](../../validation/s1_review/test_report.json)。
3. [API](../API.md)、[CONFIGURATION](../CONFIGURATION.md)、[SECURITY](../SECURITY.md)。
4. [蓝图 v1.2](../planning/development_blueprint_v1.2.md)、[切片/验收映射](../planning/slices_v1.2.json)、保留的 v1.1/v0.3 与原 backlog。

## 当前边界

阶段为 S1 review candidate；完整工作包签收为 0，S0 真实来源与 S1 全部标准未完成。保留三类原生合同、离线导入/检索/证据、用户隔离、任务与隔离备份，整合 Python worker/MCP、中文/BM25 与联网候选路径。不要把随包 implemented 当完整验收，也不把历史日志写成本轮成绩。

正常来源需 contract 审核并启用，17 条登记默认禁用。S0 probe 必须显式 include-candidates 和非空 only；sources 是文件路径。生产 proxy、remote DNS、allow_cidrs 放宽拒绝；假网站回环仅在受控测试中使用。

## 接续顺序

1. 先核对本轮源码哈希与对应 GitHub CI；本地 81 核心 / 55 合同 / 20 HTTP / 12 E2E / 122 Python 及原生 sanitizer 已通过，保留这些回归。
2. 保留 schema 3 对 schema 1 和两种历史 schema 2 的迁移、快照/证据/删除/会话/隔离回归；处理既有数据库前核对其来源并保护原始数据，禁止用原 S1 二进制盲开当前数据库。
3. 核对核心/worker 来源合同身份与有效审核、跨源凭据剥离、出站边界、Retry-After 和索引族贡献。
4. 核对真实提交的 Linux/Windows Actions、固定依赖和失败结果；不以 skipped 或非阻塞失败冒充通过。
5. 显式完成 S0 候选来源探测，另完成来源审核；至少 2 个实际可用来源含真实通用网页源，再逐项判断全部 S1 标准。
6. S2 继续 robots、任务/爬虫、质量、会话重取及独立撤销/隔离恢复；无撤销连续性不能恢复服务。后续全部范围按依赖推进。

## 长期约束

业务与权限只维护一套核心实现，MCP/HTTP/CLI 不复制规则；worker 不写数据库。模型增强未来可使用用户配置的本地/远程模型，但不开放网页到模型内网白名单。来源只接经过审核的免费入口，不绕过限制或付费回退。

传输/存储机制替换保留等效验收：MCP 通知不自动满足断线重取，单库向量也需维度/身份/权限/恢复验证。原 v1.1、规格、Schema 和历史证据保持原状；修改当前规划必须保留全部 ID 与验收映射。私人输入、凭据和运行数据库不公开。
