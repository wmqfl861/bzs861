# 最终文档与接口一致性审计

状态：v5 冻结输入的只读静态审计已关闭。全部绑定文件的实际 SHA-256 与 `build/candidate-v5-inputs.json` 一致，逐文件记录见 `all-current-inputs-v5.json`。本审计没有运行原生、Python、CTest 或 ASan 测试，也没有请求外部服务；不把正在执行的门禁或未来 CI 计作成功。

## 已关闭的文档问题

- 保留 `features` 数组原始字节，在数组外明确 `candidate_increment` 与 `remaining` 是历史候选文本；当前批读迟到加入和 direct 范围分别以顶层 scope 和合同为准。
- README 已移除把旧本地候选、ef45490 和发布审批阻塞当作当前状态的表述，采用已发布基线及本轮直接读取范围。
- API、交接入口、开发状态和机器状态只承诺透传已有执行详情。成功与失败信封的字段资格、subscriber 已知零增量与未知物理关联已明确区分。
- API 的旧 304 段明确直达与有授权跳转的计费差异，以及普通 direct 无持久预留、未知为 null 的边界。
- 新大正文修正与合同一致：共享组只独立编码 snapshot_id、document_id、url、title、revision、indexed、site_publication、meta、links 九个字段；正文仍由 Store::range 读取，不再构造重复正文和发布片段的聚合快照解析输入。

对应原始问题、父线程应用的补丁及精确前后摘要保留于本目录。`audit-final-v5.json` 逐项记录实现位置与核对结论，未改写最初审计输入或旧失败证据。

## 合同核对结论

完整 owner/fence/精确 URI/实际请求参数/profile/缓存 metadata 身份，32 累计成员、64 个开放加入组，RPC 完成关闭加入、同线程重入独立执行，与当前实现一致。64 是开放登记容量，并非总物理请求上限。

可选 profile 失败走独立网络路径，多人成功正文及 304 的真实 profile 必须匹配；单成员保留旧 Worker 合同。订阅者单调等待超时不会自动重发或向 direct 尝试发 fetch.cancel。共享不跨 WebService，也不与持久 read_many、map、crawl 或 search 混合。

每个网络结果窗口在 Store 的 BEGIN IMMEDIATE 事务中重新检查主体代际、删除和全部实际别名、准确 metadata、当前及历史发布投影。相同快照 ID 的后续 304 metadata 不与旧物理收据混合；正文只提交一次，窗口独立。no-store 仍报错，畸形收据不绕过独立有效的限制性观察。

FT-032 / B-24 正确映射 CACHE-01；FT-019 仍属 MODE-01。原 features 数组为 122468 bytes，SHA-256 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`，与基线完全相同。规划字节、83 包 / 161 验收 / 10 切片、37 partial / 46 not_started / 0 accepted、0.1.10 / schema15、17 来源 / 0 启用 / 0 站点保持。当前文档没有预填本轮运行成功数量；历史成绩仍由历史标题限定。

## 明确保留的边界

`Service::dispatch` 在 WebService 返回后仍执行主体有效性终检。该外层检查失败时可能返回无 Error.details 的错误，即使物理工作已经发生。因此完整 HTTP 失败面并不保证都能取回消耗详情；缺字段既不是零消耗，也不是安全重试证明。此轮仅把该边界写清，没有新增持久 direct 用量账本或修复这项外层收据缺口。

跨实例/进程协调、direct 与持久通道的统一共享、direct 单成员取消、重启后的执行收据恢复和完整 CACHE-01/B-24 签收仍未完成。本审计不代替这些能力的运行验收。

所有已存在的本地链接已核对；本批父线程待生成的 README 与 final-report 是明确允许的报告占位，不作为缺陷。远端 Checks 未由本次只读审计重新查询，实际门禁和发布结果只以父线程绑定的运行记录为准。
