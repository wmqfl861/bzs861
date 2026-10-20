# 当前规划与证据口径

更新：2026-10-20。输入基线为0.1.5（2007690c）；当前0.1.6为[五阶段开发](../handoff/S2_FIVE_STAGES_20261020.md)的第一检查点，关闭旧读取跨越新页面发布限制的竞争。仍为34包部分实现、49包未开始、0包完整签收；原83包、161验收、10切片保留。阶段二至五合并并验证后再计入交付范围。

| 文件 | 作用 |
|---|---|
| `development_blueprint_v1.2.md` | 当前混合架构、单用户/stdio 顺序与 S0–S9；本轮补回等效语义和发布门槛 |
| `slices_v1.2.json` | 83 包顺序/依赖、原估算来源、161 验收映射与整合约束 |
| `development_blueprint_v1.1.md`、`functional_spec_v0.3.md` | 原功能语义基线，保持原字节 |
| `requirements_and_backlog.json`、`development_plan_v1.1.xlsx` | 原 83 包与 161 验收，保留；按新映射分期执行，不降低验收性质 |
| `review/` | 原 v1.1 作者自审记录，不是本轮独立签收 |
| `schemas/`、`validation/`、`tools/validate_contracts.py` | 固定合同设计与 34 个历史 fixture；当前还有正式 C++ 实现和额外回归 |
| `tools/plan_v1_2.py` | 审查版计划的只读结构校验器；`check` 校验当前 JSON，旧 `--write` 明确拒绝并退出 2，不再生成文件 |
| [queries.json](../../tests/golden/queries.json)、[manifest.json](../../tests/golden/manifest.json) | 原 40 条查询冻结为 20 development / 15 regression / 5 holdout；有哈希、分类与分组元数据，没有人工相关性标签 |

原 v1.2 的 418–720 人日是随包规划估算；本轮保留安全/恢复等效要求后尚未重估，不用于承诺剩余工期。切片起始归属不等于该包所有增强路径必须已完成；每条验收映射写清所属阶段、门槛和证据状态。

在仓库根目录执行 `python3 docs/planning/tools/plan_v1_2.py check`（不带参数也执行只读校验）。当前 `slices_v1.2.json` 和蓝图是规划权威；调整时显式编辑并审查差异，工具不会重建、规范化或保存它们。校验覆盖 83 包/161 验收与原始清单的 ID 一致性、验收原文保留、切片/依赖无环、估算汇总及显式审查字段；通过只表示结构有效，不证明等效语义充分或产品已验收。可用 `check --plan <副本路径> --backlog <原清单路径>` 检查临时副本。

0.1.2 已补 QA-01 / FT-008 的黄金冻结、确定性分层选题与回放产物，以及 JOB-01 / BATCH-01 的显式静态列表读取。0.1.3 已增加 ROBOTS-01 / FT-081 与 MAP-01 / FT-027 的实际请求准入和持久有界发现，策略见 [站点发现](../SITE_DISCOVERY.md)。0.1.4 在同一通道上增加 CRAWL-01 / FT-028 的正文前沿、独立页预算、链接边和轮转，及 CRAWL-03 / FT-029、B-23、C-02 的页面索引/摘要声明、刷新撤发布与旧会话限制，详见 [正文爬取](../SITE_CRAWLING.md)。map 候选继续保持未读取语义，crawl 单独提交。

本批 0.1.5 增加明确阈值的日期、查询和重复分页防护，以及站点存量发布水位、历史表示绑定和实际崩溃/回执丢失回归，详见 [存量发布与升级](../SITE_PUBLICATION.md)。CRAWL-01/03 的广泛网站误判评估、长期恢复与公开集合等效验收仍待完成；有限启发式、四个实际故障边界与 owner 隔离搜索不能替代这些要求。QA-02 / FT-042 的人工标签、指标、阈值和留出评测，以及 SESSION-02 仍未开始。C-14 的完整质量冻结过程继续保留；冻结查询和合成站点测试不能代替相关性验收或真实来源 S0。当前 17 个搜索来源仍禁用，站点登记默认空。

实际情况见 [开发进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)、[下一位接手说明](../handoff/NEXT_AGENT.md) 和 [本批测试报告](../../validation/s2_five_stages/stage1-report.json)。[0.1.4 正文抓取报告](../../validation/s2_crawl/test_report.json)、[0.1.3 发现报告](../../validation/s2_discovery/test_report.json)、[0.1.2 报告](../../validation/s0_s2_progress/test_report.json)、[0.1.1 接收审查](../handoff/CLAUDE_S1_REVIEW_20261018.md) 与 [0.1.1 报告](../../validation/s1_review/test_report.json) 继续保留历史。
