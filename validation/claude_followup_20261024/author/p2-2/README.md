# P2-2 作者证据包

本目录只保存 UTF-8 文本证据，供主会话复制到仓库 validation。没有二进制、数据库、虚拟环境、构建产物、原树/改树完整源码副本，也没有独立 reviewer 的结果副本。补丁内必要的新增文件内容属于原始 diff。

## 阅读顺序

1. 先读 `CORRECTIONS.json`：P2-2 v2 把 Worker 200 publication 改成 final-only 的判断是错误的，原有 aggregate 契约由完整门禁揭露。后续修复恢复原契约，没有放松原测试。
2. `integrated/` 按主会话实际应用顺序保存五份冻结 patch、manifest 和作者定向预检日志。完整 v2 是基于 P1/P2-1 基线的补丁，其后四份 followup 依次叠加；不要把它们重复用于当前已集成工作树。
3. `historical/not-integrated-v1/` 保存未直接集成的旧草案和原始预检，包括 socket 权限 setup 错误与初次 C++ 缺少 include 的诊断。它们不是最终输入的成绩。
4. `EVIDENCE_INDEX.json` 给出来源路径、相对成员路径、SHA256、大小和角色；`SHA256SUMS` 校验全部其他包内文件。

## 已有作者预检与边界

最后聚合范围修复的 Python overlay 预检为 118 passed in 1.92s，包含原样整份 test_crawl_worker.py。三份 C++ 文件仅做 syntax-only 并通过；native 注册数为51，原44函数未改。作者没有链接或执行此最终 native 输入，也没有执行最终完整 CTest/pytest/ASan。

200 的 publication 保持整条读取链的站点 aggregate。严格 wrapper 区分最终表示，304 observation 继续只代表最终响应。完整 receipt 的一次性 Core→Store scope 标记在历史约束前保存 final 基底，在入库前消费移除；它不授予缓存/304资格。既有24 MiB RPC、8 KiB证明、16 KiB HTTP元数据上限不变。

原本的局部通过报告保持原样，不能作为后来完整门禁通过的证明。完整门禁原始输出及最终判定由 root 单独保存；独立复审也由对应 reviewer 单独记录。当前作者证据不宣称本阶段已验收或新冻结S2。
