# 直接读取共享：独立核验

最终候选 v3 的独立检查通过。完整机器报告见 [independent-review.json](independent-review.json)，根整合门禁仍以父目录的最终报告为准。本目录没有修改生产、正式测试、旧 validation 或远端。

## 最终执行与精确身份

| 记录 | 场景 / 检查 | 结果 |
| --- | --- | --- |
| [主要原生边界](candidate-native-v3/summary.json) | 13 / 551 | 通过 |
| [补充原生边界](candidate-extra-v3/summary.json) | 3 / 99 | 通过 |
| [容量回归](capacity-candidate-v3/summary.json) | 2 / 29 | 通过 |
| [实际 HTTP、CoreClient、MCP](candidate-wire-v3/summary.json) | 12 / 137 | 通过 |
| [主要 ASan/UBSan 重放](candidate-native-asan-v3/summary.json) | 13 / 551 | 通过 |
| [补充 ASan/UBSan 重放](candidate-extra-asan-v3/summary.json) | 3 / 99 | 通过 |
| [容量 ASan/UBSan 重放](capacity-candidate-asan-v3/summary.json) | 2 / 29 | 通过 |

原生共有 18 个独立场景、每个最终构建 679 次检查。Sanitizer 使用 `detect_leaks=1:halt_on_error=1` 和 `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`，重放相同场景；12 个 wire 场景属于另一验证层。执行次数、旧版本复验和失败诊断均不增加蓝图验收项。

Debug 静态库 SHA-256 为 `eb4fcd85d839bd43e78382943c8d0a7c73f47c178ff528a5fcd20851cef6404f`，实际 searchd 为 `e873a92e499a70bf1e5daf1749c9510d2380756e56c84bff86045e8be11867f8`。Sanitizer 库 SHA-256 为 `50d9573e5c88e21768364f935cc52447bc68fbe825e2cf93d902a6a10906355c`。逐条命令、输入前后摘要、实际日志字节和编译链接身份保存在各记录目录；最终源码另存于 [source-review-v3](source-review-v3/binding.json)。所有最终运行输入在执行期保持不变。

## 验证覆盖

主要原生检查包括：共享 profile 变化仍实施缓存失效和有效 noindex 观察；304 只提交一次且绑定完整 metadata；成员交付前原 URL、最终 URL、中间 URL 的有限别名删除后复活；主体撤权；付款与订阅窗口错误隔离；未知 Worker 错误不重发；畸形用量不能跳过有效限制。

补充检查覆盖畸形实际 profile 与单独有效观察、Worker 伪造错误收据不能覆盖 Core 归属，以及订阅者在受控 Worker 迟迟不返回时于自己的等待边界退出。超时者只报告已知零增量与未知物理用量；后续付款成功不会改写已返回的错误收据。

Wire 检查使用实际冻结 searchd、真实 HTTP 调用、CoreClient 和 MCP 工具方法，并连接本机认证 Worker fixture；没有 mock 掉 `_call`，没有访问真实来源。它核对成功、缓存命中、已知失败、窗口错误、no-store、旧 Worker 无用量和未知 RPC 失败的字段传递。Wire 用例本身不证明并发合并，原生用例承担该证明。

## 原始失败与修正链

已发布基线 `346048b4` 的公开接口反例见 [baseline-v1](baseline-v1/summary.json)：两个受同步闸门控制的同 owner refresh 发出两次物理 fetch，两个成功响应都遗漏了 Worker 提供的直接读取用量。这是缺少能力的观察，不是基线产品回归。旧头文件和已验证静态库来源见 [baseline-source-provenance.json](baseline-source-provenance.json)。

首份独立 v1 程序保留了四个失败。前三项错误地把不可变历史 `snapshot.indexed` 当作当前搜索可见性；其源码和日志均未重写。[分类记录](v1-failure-classification.json)解释这一审阅 oracle 错误。另一个独立文件改用公开 `Store.search` 后，仍以同一 v1 库复验，剩下真实的“畸形用量抢先抛错，跳过有效 no-store/noindex”回归，见 [纠正 oracle 的原候选重放](candidate-native-v1-oracle-corrected/summary.json)。正确的限制期待没有被改成允许泄漏。

v2 修复了用量/profile 早退路径，原 13+3 和 wire12 均通过。随后源码审核发现新的整体快照复制上限。新增同一旧公开接口容量程序，在真实已发布基线的 legacy 单读和有效 profile 双读均成功；v2 两项均失败为 `E_JSON_LIMIT`，尽管 716,800 字节正文已保存。完整快照因 JSON 转义及重复 publication 文本达到 8,603,131 / 8,603,477 字节，见 [baseline 容量正例](capacity-baseline-v1/summary.json)和 [v2 原失败](capacity-candidate-v2/summary.json)。[阶段报告](review-through-v2.json)明确没有把 v2 作为最终可交付候选。

v3 只冻结窗口需要的九个字段，逐字段独立编码和解析，完整 metadata 与读取 guard 保留；不再复制未使用的 canonical_text/publication 聚合，不放大外部协议限制，也不修改 Json API。相同容量源码和原全部期望现通过 Debug 与 sanitizer。原 13+3 与 wire12 也按最终二进制重放，确认没有遗漏窗口或收据字段。

作者另行诊断的 Error 默认 Json 临时对象死锁位于父目录 focus 记录；本审阅检查了分离三参数/四参数构造函数的修复，不声称独立复现那次 core 挂起。只读归档核验器也曾因旧记录用 `binary`、新记录用 `path` 键而中止；[诊断](verifier-diagnostic-v1/diagnostic.json)及原核验源码保留，修正版兼容两种记录。这不是产品测试失败。

## 审核结论与界限

源码审核未发现阻塞本次同一 WebService 内直接读取增量的剩余问题。成员登记、关闭和等待不跨 Worker/Store 锁；付款归属固定；成功只提交一次；各成员在最终 Store 事务中分别核对权限、metadata、全部实际别名和发布历史。九字段 map 在完成标志的互斥发布后只读，每个调用得到自己的 Json 对象。

明确保留一个已有外层边界：`Service::dispatch` 可在 `WebService::read` 返回后再因撤权拒绝，这一极晚错误位于直接读取收据包装之外。文档把收据保证限定为该路径实际包装的失败。本报告不将其扩大为所有外层错误都保存收据的保证。

本目录只证明有限本地场景。未声明当前远端 CI 或 main 发布、真实网站批准、跨服务/进程或 direct/read_many 混合协调、公开 direct 单成员取消、可恢复直接执行账本，亦未签收完整 B-24/CACHE-01。临时二进制、SQLite 夹具与认证材料不作为归档交付物；完整文本、源码、冻结头文件和原日志均在清单中。

[最终只读完整性核对](records-final-audit.json)逐一校验所有已关闭记录；失败记录保持失败。最终归档清单见 [archive-manifest.json](archive-manifest.json)，它不包含自身，之后的根整合报告独立复核可再追加单独清单。
