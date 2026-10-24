# GitHub 交付与开发历史

更新：2026-10-24。用户授权修复、继续开发并适时推送的唯一目标为 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。旧文档中的其他所有者路径是历史，不能作为本轮推送目标。

## 当前审计后续开发

本批从已发布 main `44253a752f945fdfab9810192e583b39fe5f6d83` 接续，已发布版本仍为0.1.10。`review/claude-2026-10-24` 的 `30f058557e3f7ebb873e9780759323b49cfe9636` 只读，不合并或修改审计材料；实际复现见[基线结论](../validation/claude_followup_20261024/baseline-findings.json)。八项修正按约定顺序推进，每项保留正式回归、完整门禁、独立反例与原始失败，准确结果和提交表见[本批验证记录](../validation/claude_followup_20261024/)。

本批准确输入、执行结果、提交与 CI 见本批验证记录。发布前须核对真实远端父提交、精确冻结输入和工作树；非强制更新后再核对实际 commit/tree 及该 SHA 的 Linux/Windows 结果。来源选审启用、validation 入库、backup 隔离、永久幂等保留与抓取适配器决定不变；S2 范围、状态及是否冻结的决定保持现状。

## 已交付输入与历史来源

上一轮范围为 **0.1.10 接手后的五项增量**。本轮起点是0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`，tree `d5219c5d58e8dfef48a97f08df5b71dbc9b538f1`；其[CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547)已核对Linux/Windows成功。该成绩只证明基线。本轮范围见[接手计划](handoff/S2_TAKEOVER_20261023.md)与[当前报告](../validation/s2_takeover/windows-watchdog-report.json)，发布前报告不预写未来自身SHA或CI成功。

| 已交付输入 / 历史来源 | 提交 | 说明 |
|---|---|---|
| 0.1.10 截止时间原子结算修正 | `fcb58e4679a5bbdff17319b7b4ebff7019733eab` | CI 39954357148：Linux27/27、15套sanitizer及合同/计划通过；Windows16/17，crawl在45.02秒进程超时，无用例完成输出；另14套native完成364例/10,308断言，HTTP20/20和4/4；完整失败日志保留 |
| 0.1.10 Windows HTTP清理修正 | `199eb635ca90c657dff673fbf88bed44e7c4b40b` | CI 39950116698：Linux26/27，原一秒恢复测试多出source_unknown事件；Windows17/17通过（340.84秒），撤权HTTP4/4及严格连接清理通过；本次追加截止时间生产修正，原日志保留 |
| 0.1.10 首次发布 | `a06e7e91f6a115b345ff1072095b80e15d193ad4` | CI 39945994760：Linux通过；Windows构建成功、16/17套通过，唯一失败为HTTP测试未关闭SQLite连接导致临时目录清理失败；原日志完整保留 |
| 0.1.9 JSON并发修正 | `3ef86c653a2e2085848d5cdb151d66392c6af49a` | CI 39725326547：Linux/Windows成功；本轮真实输入基线、schema12 |
| 0.1.9 Windows测试修正 | `c14b3c3c214bf9def1f8080b9125816aa0f56735` | CI 39721279784：Windows12/12通过；Linux真实ACK恢复暴露JSON竞争，当前补丁修复 |
| 0.1.9 首次发布 | `b88b76302e850499275b0a8ec2db366f282481f1` | CI 39718214744：Linux通过；Windows sessions 60秒超时，原日志保留 |
| 0.1.8 main | `2c536e9834c4897b0c70e3bd6651b765cc67f45e` | 历史五阶段集成，schema11 |
| 0.1.6 第一检查点 | `27966ffe61c16964c45d595e3ee2689115815217` | 读取顺序屏障、旧Worker完整性与独立复核；历史第一阶段检查点 |
| 0.1.5 main | `2007690c9403b8ae593101488de1adf228608284` | 持久发布约束、抓取陷阱与真实进程恢复回归 |
| 0.1.4 main | `90ca7e93f9c96a43ff8d99b237638e4aa2aa2c63` | 有界正文前沿、页预算、链接边、页面发布规则；tree `b51e0f126c49727506ad2cc08ff3912d59108703` |
| 0.1.3 main | `8a4e0f4cc630627ffa91c49598c66fec7fb4291d` | 版本化 robots 准入、独立站点登记、持久有界发现、每跳预算与配置身份 |
| 0.1.2 main | `434a9dfa8ce26b86bf765c9d863c8607c3dabae0` | 可恢复 probe、黄金冻结、离线 replay、持久显式批量读取 |
| 0.1.1 main | `426c431fd3b39390564045e31316e79ca3f27483` | 审查并整合 Claude S1 混合架构；保留先前原生修复和合同 |
| 0.0.2 main | `95710ba30d9495645de0e0966746446c0e70cdc9` | 原生配置合同、修复及平台回归 |
| 原 Claude S1 bundle | `2b121fcc6bb47973a4315b6684d429651626025c` | 原 `dev/s1-hybrid-workers` 候选输入；不是当前 HEAD |

0.1.4 的 [CI 39207218575](https://github.com/wmqfl861/bzs861/actions/runs/39207218575)、0.1.3 的 [CI 39125426011](https://github.com/wmqfl861/bzs861/actions/runs/39125426011) 与 0.1.2 的 [CI 39113748316](https://github.com/wmqfl861/bzs861/actions/runs/39113748316) 在各自交付时核对 Linux/Windows 成功，它们只证明对应历史提交；后续版本必须查询最终发布提交自己的工作流。Claude ZIP/bundle 和逐 blob 核对见 [接收审查](handoff/CLAUDE_S1_REVIEW_20261018.md)。

交付前读取远端 main 与预期旧 SHA，先完成代码、复核和实际测试，再以非强制更新发布。提交的 tree 必须与本地已验证内容相同；远端前进时先检查新差异，不覆盖未知修改。发布后获取实际 commit/tree，将本地副本对齐，并核对对应 Linux/Windows 的实际步骤和结论。

各期报告由包含它的实际 Git 提交绑定；准确 SHA 与 CI 以各期发布记录为准。

0.1.10首次发布之后的连接释放修正只改变tests/http_revocation.py及说明/证据，生产实现和CI门槛不变。它使用独立的[后续报告](../validation/s2_takeover/ci-followup-report.json)与冻结输入，不覆盖原final-v2。该次HTTP修正发布以a06e7e9为精确父提交，已核对199eb635的实际tree及Linux/Windows：Windows通过，Linux另有期限事件失败；原Windows失败仍原样保留。

截止时间修正从199eb635继续，生产变更为src/web.cpp与src/sessions.cpp的超时分类和原子终态结算；追加5项search_modes原生回归及说明。[截止时间报告](../validation/s2_takeover/deadline-followup-report.json)绑定完整重新验证，原final-v2、HTTP清理报告及全部既有validation保留。已发布为fcb58e46，其实际Linux通过、Windows进程超时结果见上表。

44253a7 从fcb58e46修正Windows测试watchdog与crawl输出观察性：仅CMakeLists.txt中的Windows core90/crawl120/site_publication120秒和tests/crawl.cpp的计时/即时输出，生产源码、原54例及断言不变。新[watchdog报告](../validation/s2_takeover/windows-watchdog-report.json)绑定聚焦验证及原始失败；不以新的进程上限宣称性能验收。该次发布以完整fcb58e4679a5bbdff17319b7b4ebff7019733eab为父提交；对应Windows时限及双平台结果只证明该次输入。本批实际父提交和 CI 必须重新核对。

## 原始历史为何不同

2026-10-17 首次接手时，目标仓库经授权连接和 Git 协议核验为空。通过已授权 GitHub 连接初始化远端，因此形成新的提交历史。原始两次提交不是该初始化提交的父历史：

- `7266ce42c64c0e5d273448d6fa49274ac02e5359`：原 0.0.1 离线实现。
- `b801b08db7b9d56108d8ea516923075007ab99f2`：原失败推送说明。

原 bundle 在交接材料中保留，Claude S1 沿原历史发展。0.1.1 已处理两条来源的三方差异；后续增量从已整合 main 接续，不再次用旧 bundle 替换整个目录。

## 证据分期

`validation/test_report.json` 描述 0.0.1；`validation/takeover/` 绑定 0.0.2；`validation/s1_test_report.json` 为 Claude 0.1.0 随包声明；`validation/s1_review/` 绑定 0.1.1；`validation/s0_s2_progress/` 绑定 0.1.2；`validation/s2_discovery/` 绑定 0.1.3；`validation/s2_crawl/` 绑定 0.1.4；0.1.5 为 `validation/s2_hardening/`；0.1.8五阶段为 `validation/s2_five_stages/`；0.1.9接续及JSON修复为 `validation/s2_next_five/`；上一轮0.1.10为 `validation/s2_takeover/`；当前审计后续为 `validation/claude_followup_20261024/`。旧证据不覆盖、不改名冒充新运行。

固定依赖、工作流存在、本地通过、GitHub 实际成功分别核对。公开仓库不包含私人聊天、令牌、运行数据库、用户探测的未审原始内容或外层 bundle。
