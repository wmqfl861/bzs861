# bzs861 · 当前开发入口

**更新：2026-10-24｜当前开发：Claude 审计后续修正｜已发布版本仍为 0.1.10｜目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。**

本批从 `44253a752f945fdfab9810192e583b39fe5f6d83` 接续。审计分支 `review/claude-2026-10-24` 的 `30f058557e3f7ebb873e9780759323b49cfe9636` 仅作只读材料，没有合并；八项原问题的实测与语义限定见[基线结论](validation/claude_followup_20261024/baseline-findings.json)。按 P1-2、P1-1、P1-3、P2-1 至 P2-4 的顺序修复，P2-5 的原查询补丁另经评审。逐项回归、独立反例、失败记录和准确执行输入见[本批验证记录](validation/claude_followup_20261024/)。

当前代码涉及无持久会话的 `search.once`、永久身份与活动明细容量分离、词项与子串候选索引、仅 Accept-Encoding 的 Vary 支持、显式读取的逐跳缓存证明及 robots/字符集/查询匹配修正。接口与边界见 [API](docs/API.md)、[任务保留](docs/JOB_RETENTION.md)、[HTTP 跳转缓存](docs/HTTP_REDIRECT_CACHE.md)；各项实现、实际执行结果、准确提交与 CI 见本批验证记录。

来源选审启用、S2 范围、状态及是否冻结的决定、validation 入库政策、backup 隔离、永久幂等身份保留与抓取适配器六项决定保持。17 条来源仍全部禁用，站点登记为空；S0/S1/S2 均未完整签收。

## 0.1.10 历史基线与验证

以下记录上一轮五项增量及其后续修正，不代替本批新输入的验证。

本轮从已发布0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`、schema12接续。基线的Linux/Windows [CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547) 已核对成功；最终输入须另核对自己的精确提交CI。schema13组合来源发布、主体撤权及共享请求迁移，保留0.1.9的JSON并发修复与条件重验证。

本轮生产实现的 `final-v2` 本地门禁为27/27套CTest（Python 1,049项）、15/15套ASan/UBSan，均无跳过；真实schema12→13升级与原合同/计划/历史保持检查通过。首次发布 `a06e7e91f6a115b345ff1072095b80e15d193ad4` 的 [CI 39945994760](https://github.com/wmqfl861/bzs861/actions/runs/39945994760) 为Linux成功、Windows构建成功但测试16/17通过；唯一失败是撤权HTTP测试未显式关闭SQLite连接，导致Windows临时目录清理失败。

随后以 `199eb635ca90c657dff673fbf88bed44e7c4b40b` 发布测试连接释放修正及两条资源回归，该次生产代码不变，证据见[HTTP清理后续报告](validation/s2_takeover/ci-followup-report.json)。其 [CI 39950116698](https://github.com/wmqfl861/bzs861/actions/runs/39950116698) 的Windows17/17通过，Linux为26/27：原一秒恢复测试出现额外source_unknown事件。整体期限触发的RPC超时分类及原子终态结算随后修正，完整验证见[截止时间后续报告](validation/s2_takeover/deadline-followup-report.json)，原E2E保持。

该生产修正发布为 `fcb58e4679a5bbdff17319b7b4ebff7019733eab`，其 [CI 39954357148](https://github.com/wmqfl861/bzs861/actions/runs/39954357148) 的Linux27/27及15套sanitizer通过；Windows16/17，唯一失败为crawl整个进程45.02秒超时，无法从旧缓冲输出确认完成到了哪个用例。该次仅为Windows设置core90秒、crawl和site_publication120秒的有限watchdog，并增加crawl逐例即时RUN/PASS与耗时。54个原用例、断言、生产源码和二进制保持，Linux属性不变；[该次报告](validation/s2_takeover/windows-watchdog-report.json)分别记录本轮聚焦验证、未变生产库的历史全量成绩及原失败。后续提交仍须取得自己的实际双平台CI，不据调整后的进程上限宣称产品性能验收完成。

| 入口 | 内容 |
| --- | --- |
| [上一轮五项范围](docs/handoff/S2_TAKEOVER_20261023.md) | 接手来源、原工作包与验收映射、实施边界 |
| [当前进度](docs/DEVELOPMENT_STATUS.md)、[机器状态](docs/implementation_status.json) | 实际实现、未完成部分和本轮验证状态 |
| [历史 Windows watchdog 报告](validation/s2_takeover/windows-watchdog-report.json)、[截止时间报告](validation/s2_takeover/deadline-followup-report.json) | 该次测试设施修正与前次生产全量验证分开记录；原失败、命令和独立复核保留 |
| [来源发布](docs/SOURCE_PUBLICATION.md)、[主体撤权](docs/PRINCIPAL_REVOCATION.md) | 持久政策收紧、管理员入口与恢复边界 |
| [HTTP新鲜度](docs/HTTP_FRESHNESS.md)、[共享读取](docs/SHARED_READS.md)、[搜索预设](docs/SEARCH_MODES.md) | 缓存与取消语义、预算和公开接口 |
| [蓝图](docs/planning/development_blueprint_v1.2.md)、[切片计划](docs/planning/slices_v1.2.json) | 原83包/161验收/10切片，依赖与验收语义保留 |
| [接手顺序](docs/handoff/NEXT_AGENT.md)、[GitHub历史](docs/GITHUB_HANDOFF.md) | 发布流程、真实远端SHA与精确CI核对 |
| [0.1.9历史报告](validation/s2_next_five/json-race-report.json) | 上一轮与JSON修复输入，原字节保留 |

当前为 **37包部分实现、46包未开始、0包完整签收**；只有MODE-01由未开始进入部分实现。默认17条搜索来源禁用，站点登记为空。合成HTTP与状态机回归不证明生产可用性、人工相关性或长期性能。独立撤销连续性没有证明时，恢复副本继续隔离。

各期报告分别绑定真实源码、二进制、执行日志和提交；本批准确 SHA 与 Linux/Windows CI 见本批验证记录，历史成绩只证明对应历史输入。
