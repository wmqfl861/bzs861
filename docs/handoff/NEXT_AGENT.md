# 下一会话：Claude 第二轮阶段 1

更新：2026-10-26。唯一仓库 `wmqfl861/bzs861`。

## 当前输入与真实交付

- 阶段 1 第一项 P1-B：`17783391143478209105a300d8eb174feab7df85`，tree `55025a502fb02f048a40503de9d969982b9e6161`；直接父提交 `d62473d4d5265e43f350909e27e32ddb1c01f95a`。
- 交付分支 `work/project-audit-stage1-20261026`，PR #2 为本次实际建立的 P1-B PR。先前聊天所称的另一份审核 PR #2、39 位审核 SHA 没有成功发布，不能作为输入或测试依据。
- 先回读远端 main、交付分支和各自准确 SHA 的 CI。P1-B 的 push checks 为 run `40337770231`；此文不预写其结论。原始测试日志由 CI artifacts 留存。
- `review/claude-2026-10-26` 固定输入 `f00bd67379c2f0586c914a5a175878a1be2751c1`，只读、不修改、不合并。两份审核文档不复制到 validation。

## 接续顺序

先读[阶段 1 执行说明](../planning/STAGE1_EXECUTION_20261026.md)、[开发蓝图末尾的当前执行约束](../planning/development_blueprint_v1.2.md)和 [P1-B 摘要](../../validation/claude_round2_p1b_20261026/result.json)。

1. 收齐 P1-B 的真实回归和准确提交 CI；发现失败先修复，不能把排队或旧成功当通过。
2. P1-A 方案 A已完成本地定向复验，准确提交CI另查：词级优先候选 + 剩余预算 gram 补足、去重和精确复核。保留原始子串语义、词内匹配与 partial，加入长文档/旧文档验收。只有审核 D7 明确选择词序列，才实施方案 B。
3. P2-C已通过本地解析及真实Worker E2E复验，准确提交CI另查：只忽略无关 robots 产品 token，保留自身/通配损坏的拒绝行为。
4. P2-B 第 1、2 点已实现，验证与限制见本批摘要及`docs/SUBSTRING_INDEX.md`：CJK gram 减量、派生索引字节统计及配额；只重建派生索引，不升业务 schema。
5. P2-D：有界后台 lane 线程池，保留同 origin 礼貌间隔、租约及崩溃恢复语义。
6. P2-A 不涉及 D5 的部分：无键冻结搜索收据、归档字节与 incremental vacuum；不改变有键身份保留期。

每项缺陷一个产品提交；在提交说明中列测试名和实际 CTest/pytest 结果。新增 validation 每项不超过 50 KB，只留摘要；原始日志放 CI artifacts。这里的文档整理是交接修正，不是新缺陷实现或验收进展。

## 未授权的变化

审核 D1–D7 全部等待用户逐项明确回复，不能将“继续”当作同意推荐值。不要与原蓝图的架构决策 D1–D5 混淆。

不启用来源，不扩展跨 direct/durable/map/crawl/search 的共享，不新增证明链，不做非必要业务 schema 升级，不改写 Git 历史。只限制本阶段执行范围，不擅自改变 S2 状态、B-24 完整验收、备份隔离或永久幂等合同。

版本 0.1.10 / schema15；83 包、161 项验收、10 切片及 37 partial / 46 not_started / 0 accepted 保持。schema16 / join_read_url 未发布源码未取回，不纳入当前成果。云端设备部署未验证，不能写成与远端同 SHA 已交付。

旧交接内容保留在 Git 历史；不再将旧“下一步扩展 B-24”段落作为活动指令。

## 本轮三个节点的发布边界

本轮从已核验双平台CI成功的`88c770c5129f2bdc04d705434cb40e6f2b827fa6`继续；P1-A为`f8c5bdcf8eb1dd4340a0c578a2d4efc611b1fed4`，P2-C为`27d8643f5ade025fc62112c24f2bfa833ffb0615`，P2-B为随后的一项产品提交。以本文件所在提交及对应validation摘要核对最终输入。

截至编写，当前会话GitHub连接只暴露读动作，云端设备离线，未能执行本批push；本地分支`work/three-nodes`与远端交付分支必须分别陈述。不要把本地commit当成远端版本。恢复授权写入通道后，先核对远端仍在预期基线，再非强推发布这三个提交并运行新SHA自己的CI；若远端已前进，先比较差异。原review分支不得修改或合并。
