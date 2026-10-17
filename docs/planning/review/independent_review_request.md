# 独立审核任务书（尚未执行）

你是独立的架构与实施计划审核者。须由真实的独立人员或单独执行的Agent完成；不得由同一会话角色改名冒充。不要修改交付文件。

## 输入冻结
第一次仅阅读baseline/中的蓝图v1.0、规格v0.2、原计划工作簿、任务JSON及Schema；输入哈希见review/input_manifest.json。先不要读取作者的findings_and_decisions或v1.1，避免沿袭作者结论。记录本次review_run_id、reviewer身份/模型配置引用、开始/结束时间、上下文隔离方式与输入哈希，不包含密钥或私有思维链。

## 第一轮交付
重点检查任务依赖和阶段可交付性、模型可选合同、免费来源可行性、浏览器隔离、索引读写与更新、权限与恢复、存储/网络/模型预算、订阅语义、跨平台与工量。每项发现提供severity、文件位置/工作包、能复现的例子、修订建议、证据和不确定性。不得编造已经运行软件、真实来源或性能结果。没有发现也可如实报告。

## 第二轮复核
冻结第一轮原报告后，再阅读蓝图v1.1、规格v0.3、作者裁决与合同验证报告，逐项判定resolved/partial/unresolved/new_issue。资料通过不代表产品实现通过。不要求与作者结论一致，不因已有采纳决定而省略验证。

## 输出格式
review_run_id / reviewer_id / independent_context_description / input_hashes /
execution_scope / evidence / findings[] / unresolved_risks / stage_gate_recommendation。

任何未实际测试项标not_tested。发送模型前使用用户批准的配置与材料出站策略；不能把“模型可用”视为已经获得任意端点或私人材料外发权限。
