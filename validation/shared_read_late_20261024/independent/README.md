# B-24 迟到订阅：独立迁移、取消与事务审核

本目录由独立审核者生成。只读取候选生产代码与正式测试；探针、合成数据库和证据均写在独立目录。未修改生产代码、正式测试、原规划、既有 validation 或远端。

审核的真实旧库来自本地基线 `a3b3f6e2eec12d3e4d18aa34dbd48c396125cd89`，schema14。候选为 schema15 的持久 `read_many` 迟到订阅增量。完整结论和哈希见 [independent-review.json](independent-review.json)，逐文件归档名单见 [archive-manifest.json](archive-manifest.json)。本目录不宣称全产品验收、真实网络验证或远端 CI；最终整合套件由父级记录另行绑定。

## 已执行的独立验证

| 验证 | 真实输入与结果 |
|---|---|
| 旧库迁移 | 通过旧 a3 API 建立真实 schema14 数据库，再用候选升级为15；47 张普通表与 FTS 影子表的旧列逐行内容完全相同，2 个虚拟表接口由其影子数据覆盖 |
| 旧运行项 | 默认 `dispatch_timeout_ms=0` / `sharing_closed=1`；单人、共享、付款者已取消但成员有效的旧请求仍可按原路径完成及结算，迟到任务不加入旧尝试 |
| 新尝试 | 旧排队任务在新版本派发时保存 timeout 并开放加入；兼容新任务可共享该次新尝试 |
| 迁移门禁 | 旧二进制拒绝 schema15；隔离备份仍拒绝启动；注入迁移 UPDATE 故障后 schema、旧列内容和新增索引均回滚，去除夹具故障后升级成功 |
| 独立行为反例 | 同一 v2 探针在真实旧库为 6 组 / 131 检查 / 4 个预期失败；候选为 6 组 / 163 检查 / 0 失败 |
| 入组事务回滚 | 另建探针覆盖单人升组和已有组扩员，共 2 分支 / 151 检查 / 0 失败；定向中止 late member INSERT 后六表逐行不变，移除故障后同键重提成功，原请求只计费一次 |
| ASan / UBSan | 以相同探针重放真实迁移、6 组行为及2分支事务故障，全部退出0；保持泄漏检测开启，冻结输入、库、二进制及生产文件前后相同 |

六组行为覆盖：在途单人升组及幂等；原有效 timeout 被 deadline 截短的正例；付款者取消后其他成员有效仍允许迟到加入；过期关闭后时钟回拨不重新加入；旧 lease 的关闭 CAS 不影响新尝试；owner、精确 URI、timeout、请求额度、字节额度、首项顺序、当前快照和全局发布水位八种不兼容对照。

入组故障探针对 `read_shared_members` 的新成员 INSERT 注入定向失败。比较 `jobs`、`read_job_budgets`、`read_job_items`、`read_shared_attempts`、`read_shared_members`、`settings` 全部行；确认新 job、预算、成员、组计数及 dispatch 序号没有部分提交。原付款项保持 open/live；恢复夹具后相同幂等键正常加入，原物理请求完成并由原付款任务承担一次用量。

Sanitizer 是同场景重放，不能再次累计为新的验收案例。环境固定为：

```text
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
```

## 首次失败及修正范围

两个首次独立记录均保留原源、命令、日志和结果，没有替换成最终成功记录。

1. `migration-review-v1` 的外部 Python SQLite 连接在 `PRAGMA integrity_check` 返回 SQL logic error。对未升级的真实 schema14 基线和升级库均可观察到同样错误，不能据此判定候选回归。v2 使用注册了 Core SQLite 能力的真实 Core 连接检查，旧库和新库均返回 `ok`。外部诊断连接差异的完整内部根因没有在本次任务中进一步隔离。
2. `behavior-review-v1` 的独立 publication 夹具传入缺少版本字段的策略，没有实际推进 `publication_sequence`，候选因此正确允许加入，导致探针一项失败。v2 仅改为合法版本化策略，并在提交前断言发布序号确已增加。生产候选未改变，原 v1 留存。

对应入口：

- [migration-review-v1/review-result.json](migration-review-v1/review-result.json) 和 [外部诊断观察](migration-review-v1/external-sqlite-integrity-observation.json)
- [migration-review-v2/review-result.json](migration-review-v2/review-result.json)
- [behavior-review-v1/review-result.json](behavior-review-v1/review-result.json)
- [behavior-review-v2/review-result.json](behavior-review-v2/review-result.json)
- [admission-rollback-review-v1/review-result.json](admission-rollback-review-v1/review-result.json)
- [sanitizer-review-v1/review-result.json](sanitizer-review-v1/review-result.json)

## 静态审查与保留边界

独立核对三个生产文件，迟到加入、成员有效性检查与关闭在 SQLite 写事务中串行；关闭 CAS 绑定原 payer/job/position/attempt/epoch。关闭仅限制新加入，原完成、失败、发布及账本结算函数不依赖该标记。现有30组 batch 回归的输入和断言保留，只有当前 schema 期望升级；其他七份测试文件只调整当前和未来 schema 门禁数字。

实际保留性检查见 [source-and-history-preservation.json](source-and-history-preservation.json)：3193 项旧 validation 同时匹配运行前快照及 a3 Git blob；原计划与来源/站点配置保持，仍为17来源全部禁用、0站点、83工作包/161验收以及37 partial/46 not_started/0 accepted。各命令日志、冻结探针及 sanitizer 输入绑定见 [evidence-integrity-review.json](evidence-integrity-review.json)。

以下边界仍需保持：

- 本增量只覆盖持久 `read_many` 的新任务首项，普通 `web.read`、map/crawl/search 的共享协调继续在范围外。
- 持久未过期 lease 不证明原进程存活；执行丢失仍等待原到期规则作未知结算，不自动重新派发。
- 关闭对新加入不可逆；本轮没有改变原有成员在时钟回拨后的完成或计费规则。
- 最多评估32个查询返回的候选；SQL可能检查更多同URI开放项。32名成员是累计上限，取消不回收槽位。
- 全局 `publication_sequence` 相等属于保守条件，其他页面的限制变化也可能阻止加入。
- 升级前停止旧Core。重新启动的版本拒绝不代表已经打开数据库的新旧进程可以安全混用。

## 归档

归档 `archive-manifest.json` 中 `included_files` 的所有文件，并归档 manifest 本身；父级复制清单应另行绑定该 manifest。保留源文件、冻结头文件、原始 stdout/stderr、命令、输入、内容摘要和报告。编译二进制、静态库、合成 SQLite 及其临时文件不入仓；其内容哈希和重建探针已保留。归档后不修改既有失败记录。
