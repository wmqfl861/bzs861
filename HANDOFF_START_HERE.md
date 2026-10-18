# bzs861 · 当前开发入口

**更新：2026-10-18｜阶段：0.1.3 S2 discovery development candidate｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本批从已交付 0.1.2 接续，增加 robots 实际请求准入和持久有界站点发现。保留黄金查询/探测回放、静态批量读取、原生配置合同、owner 隔离、不可变快照、证据和隔离备份。蓝图、计划、实现状态与当前交接已同步；S0/S1/S2 仍未完整签收。

| 先看什么 | 路径 |
|---|---|
| 本批功能、修复和限制 | [S2 交接](docs/handoff/S2_DISCOVERY_20261018.md)、[验证说明](validation/s2_discovery/README.md) |
| 站点登记、robots 和发现使用 | [SITE_DISCOVERY](docs/SITE_DISCOVERY.md)、[API](docs/API.md) |
| 安装、运行与 MCP | [README](README.md) |
| 当前进度与实际执行 | [开发进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json)、[本批报告](validation/s2_discovery/test_report.json) |
| 蓝图和开发计划 | [蓝图 v1.2](docs/planning/development_blueprint_v1.2.md)、[83 包 / 161 验收映射](docs/planning/slices_v1.2.json)、[规划说明](docs/planning/README.md) |
| 下一批任务与决策 | [NEXT_AGENT](docs/handoff/NEXT_AGENT.md)、[DECISIONS](docs/handoff/DECISIONS.md) |
| 0.1.2 黄金集、探测与批量读取历史 | [S0_GOLDEN](docs/handoff/S0_GOLDEN_20261018.md)、[0.1.2 报告](validation/s0_s2_progress/test_report.json) |
| Claude S1 更新的接收与整合 | [0.1.1 接收审查](docs/handoff/CLAUDE_S1_REVIEW_20261018.md) |
| 原生合同与安全边界 | [CONFIGURATION](docs/CONFIGURATION.md)、[SECURITY](docs/SECURITY.md) |
| 原始交接与 Git 历史 | [GitHub 说明](docs/GITHUB_HANDOFF.md)、[2026-10-17 接手](docs/handoff/TAKEOVER_20261017.md)、[原交接书](docs/handoff/HANDOFF.md) |

当前 **32 项部分实现、51 项未开始、0 项完整签收**。本批新增部分实现的是 ROBOTS-01 / MAP-01，完整正文爬取、公开索引声明、人工相关性、断线重取及隔离恢复继续按原要求推进。不要用合成站点或测试总数推导产品验收。

站点登记默认空，17 条搜索来源默认禁用。查看登记不批准访问；robots 允许也不能替代有效站点审核。没有付费搜索或抓取回退。外层私人聊天、凭据、数据库、未审探测原始内容和原始 bundle 不属于公开交付。

每次接手重新读取远端 main 与当前工作区，不能把历史输入 SHA 当作最新 HEAD。交付核对精确提交及其 Linux/Windows CI，历史日志仅证明它们绑定的旧版本。
