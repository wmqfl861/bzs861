# bzs861 · 当前开发入口

**更新：2026-10-23｜当前范围：0.1.10 接手后的五项增量｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本轮从已发布0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`、schema12接续。基线的Linux/Windows [CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547) 已核对成功；最终输入须另核对自己的精确提交CI。schema13组合来源发布、主体撤权及共享请求迁移，保留0.1.9的JSON并发修复与条件重验证。

本轮生产实现的 `final-v2` 本地门禁为27/27套CTest（Python 1,049项）、15/15套ASan/UBSan，均无跳过；真实schema12→13升级与原合同/计划/历史保持检查通过。首次发布 `a06e7e91f6a115b345ff1072095b80e15d193ad4` 的 [CI 39945994760](https://github.com/wmqfl861/bzs861/actions/runs/39945994760) 为Linux成功、Windows构建成功但测试16/17通过；唯一失败是撤权HTTP测试未显式关闭SQLite连接，导致Windows临时目录清理失败。

当前追加测试辅助函数的连接释放修正，以及成功/查询异常两条资源生命周期回归；生产代码不变。新输入和聚焦验证见[CI后续报告](validation/s2_takeover/ci-followup-report.json)。原 `final-v2` 成绩只绑定原输入，原失败与修正证据全部保留；修正提交自己的Linux/Windows结果仍须按其精确SHA查询。

| 入口 | 内容 |
| --- | --- |
| [本轮五项范围](docs/handoff/S2_TAKEOVER_20261023.md) | 接手来源、原工作包与验收映射、实施边界 |
| [当前进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json) | 实际实现、未完成部分和本轮验证状态 |
| [验证说明](validation/s2_takeover/README.md)、[生产实现报告](validation/s2_takeover/final-report.json)、[CI后续报告](validation/s2_takeover/ci-followup-report.json) | 原v2与测试修正的输入分开冻结，保留命令、失败与独立复核 |
| [来源发布](docs/SOURCE_PUBLICATION.md)、[主体撤权](docs/PRINCIPAL_REVOCATION.md) | 持久政策收紧、管理员入口与恢复边界 |
| [HTTP新鲜度](docs/HTTP_FRESHNESS.md)、[共享读取](docs/SHARED_READS.md)、[搜索预设](docs/SEARCH_MODES.md) | 缓存与取消语义、预算和公开接口 |
| [蓝图](docs/planning/development_blueprint_v1.2.md)、[切片计划](docs/planning/slices_v1.2.json) | 原83包/161验收/10切片，依赖与验收语义保留 |
| [接手顺序](docs/handoff/NEXT_AGENT.md)、[GitHub历史](docs/GITHUB_HANDOFF.md) | 发布流程、真实远端SHA与精确CI核对 |
| [0.1.9历史报告](validation/s2_next_five/json-race-report.json) | 上一轮与JSON修复输入，原字节保留 |

当前为 **37包部分实现、46包未开始、0包完整签收**；只有MODE-01由未开始进入部分实现。默认17条搜索来源禁用，站点登记为空。合成HTTP与状态机回归不证明生产可用性、人工相关性或长期性能。独立撤销连续性没有证明时，恢复副本继续隔离。

发布前报告由包含它的真实Git提交绑定，不预写未来自身SHA或未来CI成功。交付时读取实际提交的Linux/Windows工作流，历史成绩只证明对应历史输入。
