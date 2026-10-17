# bzs861 · 当前开发入口

**接手日期：2026-10-17。当前版本为 0.0.2 离线接手批次。目标仓库：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

先读 [本轮接手记录](docs/handoff/TAKEOVER_20261017.md)，了解独立复核、修复、C++ 合同与当前验证结果。下面的原交接材料保留 0.0.1 的历史背景。

这是可运行的离线搜索实现，联网功能仍在后续计划。原两次 Git 提交保留在交接包的 bundle 和本地离线分支；当前源码包含后续修复。远端通过已授权的 GitHub 连接提交，产生新的提交 ID，详见接手记录。

| 先看什么 | 路径 |
|---|---|
| 上传到 GitHub 的步骤 | [手动上传指南](docs/handoff/MANUAL_UPLOAD.md) |
| 接手开发所需的当前状态 | [开发交接书](docs/handoff/HANDOFF.md) |
| 可给下一位开发者／Agent 的任务书 | [接续开发指令](docs/handoff/NEXT_AGENT.md) |
| 最终需求与已废止建议 | [决策与纠正记录](docs/handoff/DECISIONS.md) |
| 编译、运行和测试 | [README](README.md) |
| 当前真实实现与未完成项 | [开发进度](docs/DEVELOPMENT_STATUS.md)、[机器可读状态](docs/implementation_status.json) |
| 最新计划 | [蓝图 v1.1](docs/planning/development_blueprint_v1.1.md)、[规格 v0.3](docs/planning/functional_spec_v0.3.md)、[工作簿](docs/planning/development_plan_v1.1.xlsx) |

**上传时应选择本目录里的文件和子目录，使 `CMakeLists.txt`、`README.md`、`apps/`、`src/` 等出现在仓库根目录。不要上传外层个人交接包，更不要默认公开聊天记录。**

本目录不包含聊天全文、Git bundle、编译目录、运行数据库和个人认证文件。聊天与 Git 历史放在外层完整资料包中单独留存。

本轮已通过 GitHub 连接和 Git 协议核验 `wmqfl861/bzs861`，接手时没有远端分支。当前交付状态见接手记录；`validation/delivery_status.json` 仅记录上一轮对旧仓库的失败尝试。

归档中早期建议可能已过时；当前有效边界是：独立实现，不使用 SearXNG／Scrapling／Firecrawl／Exa 成品后端；搜索和抓取等外部服务仅接经审核的免费入口；用户配置的本地或远程模型允许使用。本轮已执行独立子 Agent 复核；范围和未验收部分见接手记录，不等于完整产品通过独立验收。
