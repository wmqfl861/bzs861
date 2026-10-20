# bzs861 · 当前开发入口

**更新：2026-10-19｜阶段：0.1.5 S2 crawl hardening candidate｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本批从已交付 0.1.4 接续，修复站点规则收紧后的存量发布问题，增加持久抓取陷阱防护和真实崩溃/回执丢失回归。保留 robots 与站点发现、黄金输入/探测回放、显式批量读取、原生合同、owner 隔离、不可变快照和证据。蓝图、计划、状态与交接同步；S0/S1/S2 仍未完整签收。

| 先看什么 | 路径 |
|---|---|
| 本批功能、修复和限制 | [本批交接](docs/handoff/S2_HARDENING_20261019.md)、[验证说明](validation/s2_hardening/README.md) |
| 抓取与发布规则 | [SITE_CRAWLING](docs/SITE_CRAWLING.md)、[SITE_PUBLICATION](docs/SITE_PUBLICATION.md)、[API](docs/API.md) |
| 站点登记、robots 与发现 | [SITE_DISCOVERY](docs/SITE_DISCOVERY.md) |
| 安装、运行与 MCP | [README](README.md) |
| 进度与实际执行 | [开发进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json)、[当前报告](validation/s2_hardening/test_report.json) |
| 蓝图和开发计划 | [蓝图 v1.2](docs/planning/development_blueprint_v1.2.md)、[83 包 / 161 验收映射](docs/planning/slices_v1.2.json)、[规划说明](docs/planning/README.md) |
| 接续顺序 | [NEXT_AGENT](docs/handoff/NEXT_AGENT.md)、[DECISIONS](docs/handoff/DECISIONS.md) |
| 0.1.4 正文抓取历史 | [抓取交接](docs/handoff/S2_CRAWL_20261018.md)、[0.1.4 报告](validation/s2_crawl/test_report.json) |
| 0.1.3 发现历史 | [发现交接](docs/handoff/S2_DISCOVERY_20261018.md)、[0.1.3 报告](validation/s2_discovery/test_report.json) |
| 0.1.2 黄金、探测和批量读取历史 | [S0_GOLDEN](docs/handoff/S0_GOLDEN_20261018.md)、[0.1.2 报告](validation/s0_s2_progress/test_report.json) |
| Claude S1 更新的接收与整合 | [0.1.1 接收审查](docs/handoff/CLAUDE_S1_REVIEW_20261018.md) |
| 配置、安全与 Git 历史 | [CONFIGURATION](docs/CONFIGURATION.md)、[SECURITY](docs/SECURITY.md)、[GitHub 说明](docs/GITHUB_HANDOFF.md) |

当前 **34 项部分实现、49 项未开始、0 项完整签收**。CRAWL-01 / CRAWL-03 本批在已有部分实现上继续推进；完整爬虫验收、真实来源审核、人工相关性、断线重取及隔离恢复继续按原要求推进。不要用合成站点或测试总数推导产品验收。

站点登记默认空，17 条搜索来源默认禁用。查看登记不批准访问；robots 允许不能替代有效站点审核。没有付费搜索或抓取回退。私人聊天、凭据、数据库、未审探测原始内容和原始 bundle 不属于公开交付。

每次接手重新读取远端 main 与当前工作区，不能把历史输入 SHA 当作最新 HEAD。交付核对精确提交及其 Linux/Windows CI，历史日志只证明它们绑定的旧版本。
