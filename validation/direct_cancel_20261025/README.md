# 直接读取成员取消：本批验证

本批接手 `f4878be865a92dd640a14d471f0fb9725e7b0796`，tree `edce78afc1c122b269f6468345fe13114f5c6070`。接手时真实 main 为 `346048b4ae329e750d15f1e35952aab4513e3912`；上一会话的普通直接共享读取尚未发布。本次发布包含两批代码，保留原共享读取及其全部历史材料。

范围为 `CACHE-01 / FT-032 / B-24` 的可选直接成员取消。行为、接口、容量和未完成边界见[合同](../../docs/DIRECT_READ_CANCELLATION.md)。版本仍为0.1.10，schema15，没有数据库迁移。用户既有“允许推送main”授权保持。

## 最终本地结果

准确命令、执行输入、日志和二进制SHA-256见[机器报告](final-report.json)。

| 门禁 | 已实际执行的结果 |
| --- | --- |
| 完整 CTest，冻结输入v2 | **28/28通过，0失败、0跳过、0禁用**；216.193秒 |
| CTest内Python Worker/MCP | **1332通过**；不是28套之外的额外套件 |
| 正式直接共享与取消目标 | **34组、10259断言通过**；包含原23组和新增11组 |
| 正式HTTP/CLI目标 | **30条通过**；包含新增4条取消与接纳测试 |
| 全部原生ASan/UBSan | **16/16通过**；开启泄漏检查和遇错停止，301.756秒 |
| 相邻Worker/适配器聚焦 | **298通过**；包含最终Worker退休期限与MCP无效句柄修正 |
| 原合同及计划门禁 | 原34合同、13项计划保护通过；83包/161验收映射/10切片结构保持 |
| 依赖 | `pip check`通过 |

CTest完整输出保存在[完整日志](local/final-ctest-v2-detail.log)，[JUnit](local/ctest-v2.xml)仅用于套件状态，CTest自身会截短部分成功用例输出。最终v2的全部记录执行输入前后一致。

sanitizer期间最后一轮Python Worker退休期限和MCP测试修正仍在进行，因此其广义输入清单如实标记Python文件变化；**所有实际编译和执行的原生源码、原生测试、构建配置与二进制均未变化，并等于最终输入**。这些原生程序不会加载修改中的Python文件。其后完整CTest v2验证最终冻结Python，不把原生成绩当作Python回归。具体文件差异与全部16目标结果在机器报告中单独记录。

## 独立审核

[独立报告](independent/final-review.json)由未修改生产及正式测试的审核者完成，结论是文档限定范围内没有未解决阻断项。

- Debug原生：12场景、110检查通过。
- 同一原生探针ASan/UBSan：同12场景、110检查通过。
- 真实已认证回环Worker RPC：4场景、21检查通过，包括第二跳流式取消、2次请求/239字节收据、noindex/nosnippet/no-store观察和无自动重试。

五项发现已关闭：全取消后的提交准入、RPC无回包时保留协作取消、最后主体复查只透传直接读取回执、MCP空句柄不降级、Worker重复取消不延长退休期限。最后一项在独立RPC执行后只改一个表达式；审核者精确比对了源码变化并审阅两项作者旧失败/新通过，未冒称重跑旧RPC。最终Python全量结果来自CTest v2。

独立、作者、sanitizer与正式套件存在重复场景，不能相加为产品验收数量。

## 原失败与修正

1. 本地生成的少数ELF产物初始为0644，直接读取测试及首次CTest中的revalidation无法启动。只修复执行位，前后文件字节和哈希一致；[直接记录](local/executable-mode.json)、[Debug模式记录](local/debug-executable-modes.json)、[ASan二进制记录](local/asan-binaries.json)保留原状态。没有改测试断言或进程超时。
2. [完整CTest首轮](local/final-ctest-v1.json)保留一项未启动，以及新增MCP测试误读`json=`而实际请求使用`data=`的错误。修正测试对实际编码的检查后，完整v2全部通过。
3. Python作者最早的工作目录、回环socket沙箱和SDK注解属性错误保留在[作者报告](python-author-report.json)。独立Worker同样保留[沙箱启动失败](independent/worker-probe-v1-record.json)；后续通过正常执行批准进行回环验证。
4. Worker退休期限的两项回归先在旧表达式上实际失败，修正后通过；原失败及相邻MCP测试错误保留在[TTL补充](python-ttl-followup/addendum.json)。

上述记录没有被最终通过覆盖，也不被当作已经执行成功的场景。

## 保持项与发布核对

[保留检查](preservation.json)确认上一输入的**4365份validation文件原字节保持**，蓝图及原features数据未变，来源/站点配置未改。原37 partial /46 not_started /0 accepted、83包/161验收/10切片保持；本批不签收完整B-24或CACHE-01。

终端没有GitHub写入凭据，因此用已连接GitHub API构造原字节相同的tree。上一会话本地f4878be已经重建为同tree的暂存提交`fffd2148334fa1931b6ef7257a4b893ad1d54446`，记录见[f487暂存回执](f487-staging.json)。暂存对象不等于已经移动main。

本报告在最终提交与远端ref更新前生成，不预写未来SHA或CI结果。发布时以准确旧main SHA和`force=false`更新；发布后重新读取实际commit/tree、检查该SHA自己的[Linux/Windows Actions](https://github.com/wmqfl861/bzs861/actions)，并把当前工作目录取到同一远端SHA。旧CI只证明旧输入。

## 继续开发边界

此增量只覆盖同一WebService、同owner的显式控制直接读取组；原无句柄路径独立保持。HTTP没有取消保留槽，503表示控制未受理；付款者原调用可能等待物理结算；取消不回滚已通过提交准入的存储事务。没有跨通道/跨实例共享、持久直接回执、重启恢复或网络恰好一次保证。独立撤销journal、安全恢复、真实来源及人审qrels、长期故障验收继续按原蓝图推进。
