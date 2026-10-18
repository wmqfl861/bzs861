# bzs861 · 当前开发入口

**更新：2026-10-18｜阶段：0.1.1 S1 review candidate｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本轮把 Claude S1 混合架构增量整合到已交付的 0.0.2 主分支，保留原生配置合同、用户隔离、不可变快照、证据和隔离备份。候选实现、本轮离线验证、真实来源可用性和完整工作包验收分别记录；S0 与 S1 尚未签署完成。

| 先看什么 | 路径 |
|---|---|
| 本轮输入、问题与整合边界 | [S1 接收审查](docs/handoff/CLAUDE_S1_REVIEW_20261018.md) |
| 安装、运行与 MCP | [README](README.md) |
| 实际状态与本轮验证 | [开发进度](docs/DEVELOPMENT_STATUS.md)、[机器可读状态](docs/implementation_status.json)、[本轮报告](validation/s1_review/test_report.json) |
| API 与保留的原生配置合同 | [API](docs/API.md)、[配置合同](docs/CONFIGURATION.md) |
| 显式候选来源探测 | [S0 来源实测](docs/S0_PROBE.md) |
| 切片顺序与验收映射 | [蓝图 v1.2](docs/planning/development_blueprint_v1.2.md)、[切片数据](docs/planning/slices_v1.2.json) |
| 下一批任务与有效决策 | [NEXT_AGENT](docs/handoff/NEXT_AGENT.md)、[DECISIONS](docs/handoff/DECISIONS.md) |
| 已交付的 0.0.2 历史 | [2026-10-17 接手记录](docs/handoff/TAKEOVER_20261017.md) |
| 原始 0.0.1 交接与上传背景 | [原交接书](docs/handoff/HANDOFF.md)、[原上传指南](docs/handoff/MANUAL_UPLOAD.md) |
| 远端与两条开发历史 | [GitHub 说明](docs/GITHUB_HANDOFF.md) |

83 个工作包和 161 项验收 ID 全部保留。v1.2 采用 C++ 核心、Python worker、stdio MCP 优先、单用户先行的排序；权限、删除、恢复、断线重取和来源审核语义继续有效。使用其他库或传输机制需要等效验证，不能只保留工作包名称。

只有经过审核并显式启用的来源可以进入正常搜索。随包 13 个未核验适配器默认禁用，S0 候选探测必须显式选择；成功率不代替审核。不使用 SearXNG、Scrapling、Firecrawl、Exa 成品后端，不设付费搜索或抓取回退；用户配置的模型执行属于后续功能。

当前远端状态须重新读取，不能把 2026-10-17 的空仓库观察当作现状。外层私人聊天、凭据、数据库和原始 bundle 不属于公开源码交付。历史日志不能代替当前提交的测试和 GitHub Actions。
