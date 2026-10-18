# 当前规划与证据口径

更新：2026-10-18。已交付基线为 0.1.1 S1 review candidate；本批目标为 0.1.2 的 S0 证据与 S2 静态批量读取切片。S0/S1/S2 均未签署完整验收；计划不代表实现或验收。

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

本批 QA-01 / FT-008 补黄金冻结、确定性分层选题与回放产物；C-14 仍要求完整质量冻结过程，QA-02 / FT-042 的人工标签、指标、阈值和留出评测保持未完成。S2 的显式静态列表读取按 JOB-01 / BATCH-01 的依赖推进，不能当成 robots、发现或递归爬虫已交付。

实际情况见 [开发进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)、[本批交接](../handoff/S0_GOLDEN_20261018.md) 和 [本批测试报告](../../validation/s0_s2_progress/test_report.json)。[0.1.1 接收审查](../handoff/CLAUDE_S1_REVIEW_20261018.md) 与 [0.1.1 报告](../../validation/s1_review/test_report.json) 继续保留历史。
