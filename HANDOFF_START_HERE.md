# bzs861 · 当前开发入口

**更新：2026-10-23｜当前范围：0.1.10 接手后的五项增量｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本轮从已发布0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`、schema12接续。基线的Linux/Windows [CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547) 已核对成功；最终输入须另核对自己的精确提交CI。schema13组合来源发布、主体撤权及共享请求迁移，保留0.1.9的JSON并发修复与条件重验证。

本轮生产实现的 `final-v2` 本地门禁为27/27套CTest（Python 1,049项）、15/15套ASan/UBSan，均无跳过；真实schema12→13升级与原合同/计划/历史保持检查通过。首次发布 `a06e7e91f6a115b345ff1072095b80e15d193ad4` 的 [CI 39945994760](https://github.com/wmqfl861/bzs861/actions/runs/39945994760) 为Linux成功、Windows构建成功但测试16/17通过；唯一失败是撤权HTTP测试未显式关闭SQLite连接，导致Windows临时目录清理失败。

随后以 `199eb635ca90c657dff673fbf88bed44e7c4b40b` 发布测试连接释放修正及两条资源回归，该次生产代码不变，证据见[HTTP清理后续报告](validation/s2_takeover/ci-followup-report.json)。其 [CI 39950116698](https://github.com/wmqfl861/bzs861/actions/runs/39950116698) 的Windows17/17通过，Linux为26/27：原一秒恢复测试出现额外source_unknown事件。当前修正整体期限触发的RPC超时分类及原子终态结算，完整新验证见[截止时间后续报告](validation/s2_takeover/deadline-followup-report.json)。原E2E、旧报告和两次CI失败证据保留；最新提交仍须核对自己的实际Linux/Windows。

| 入口 | 内容 |
| --- | --- |
| [本轮五项范围](docs/handoff/S2_TAKEOVER_20261023.md) | 接手来源、原工作包与验收映射、实施边界 |
| [当前进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json) | 实际实现、未完成部分和本轮验证状态 |
| [生产实现报告](validation/s2_takeover/final-report.json)、[HTTP清理报告](validation/s2_takeover/ci-followup-report.json)、[当前截止时间报告](validation/s2_takeover/deadline-followup-report.json) | 各次输入独立冻结，保留原失败、命令和独立复核；当前以最后一份报告为准 |
| [来源发布](docs/SOURCE_PUBLICATION.md)、[主体撤权](docs/PRINCIPAL_REVOCATION.md) | 持久政策收紧、管理员入口与恢复边界 |
| [HTTP新鲜度](docs/HTTP_FRESHNESS.md)、[共享读取](docs/SHARED_READS.md)、[搜索预设](docs/SEARCH_MODES.md) | 缓存与取消语义、预算和公开接口 |
| [蓝图](docs/planning/development_blueprint_v1.2.md)、[切片计划](docs/planning/slices_v1.2.json) | 原83包/161验收/10切片，依赖与验收语义保留 |
| [接手顺序](docs/handoff/NEXT_AGENT.md)、[GitHub历史](docs/GITHUB_HANDOFF.md) | 发布流程、真实远端SHA与精确CI核对 |
| [0.1.9历史报告](validation/s2_next_five/json-race-report.json) | 上一轮与JSON修复输入，原字节保留 |

当前为 **37包部分实现、46包未开始、0包完整签收**；只有MODE-01由未开始进入部分实现。默认17条搜索来源禁用，站点登记为空。合成HTTP与状态机回归不证明生产可用性、人工相关性或长期性能。独立撤销连续性没有证明时，恢复副本继续隔离。

发布前报告由包含它的真实Git提交绑定，不预写未来自身SHA或未来CI成功。交付时读取实际提交的Linux/Windows工作流，历史成绩只证明对应历史输入。
