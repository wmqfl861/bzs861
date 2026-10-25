# HTTP取消接纳：独立审核

结论：**本次固定取消控制槽的限定范围内，没有尚未解决的阻断问题。** 最终机器记录见 [final-review.json](final-review.json)。审阅者未修改生产代码、正式测试、Git或旧validation。

| 层次 | 实际结果 | 记录 |
| --- | --- | --- |
| 真实旧版对照 | 1场景、6检查；普通池满时取消确实返回503且没有取消效果 | [baseline-v1-record.json](baseline-v1-record.json)、[原输出](baseline-v1.log) |
| 候选Debug黑盒 | 4场景、66检查通过 | [candidate-v1-record.json](candidate-v1-record.json)、[原输出](candidate-v1.log) |
| 候选ASan/UBSan HTTP进程 | 同4场景、66检查通过；开启泄漏检查 | [candidate-asan-v1-record.json](candidate-asan-v1-record.json)、[原输出](candidate-asan-v1.log) |

三次执行的 [http_probe.py](http_probe.py) 原逻辑和字节保持。每次声明的源码、库与二进制输入前后哈希一致。旧版结果是待交付能力尚未存在的对照，不是旧版回归失败；Debug与sanitizer的重复场景不能相加为新验收项。

独立探针实际覆盖4096/4097字节边界、合法转义字段名、解码后重复键、伪操作字符串、未知字段、错误类型、缺失参数与尾随内容；认证和传输错误优先、跨owner仍拒绝；普通池满时最后成员取消及计费；控制池自身满时即使普通池空闲也503、没有隐藏等待或延后取消；健康检查以及两池在途SIGTERM。

Sanitizer执行的是实际 `searchd`，因此包括本次改动所在的 `apps/searchd/main.cpp`。设置保持 `detect_leaks=1` 和 `halt_on_error=1`，诊断输出指向本目录的 `asan-diagnostics/`，实际未生成诊断文件。[asan_runner.py](asan_runner.py)只包装关闭观察，未改探针场景或断言；[四个进程全部退出0](asan-shutdowns.json)，各自stderr另存为 `asan-server-1.log` 至 `asan-server-4.log`，没有依赖已清理的临时日志证明结果。

源码审查确认：分类发生于原认证/传输检查之后且复用严格JSON解析；分类不访问Store或Worker；原正文仍交Service执行权限检查；两个有界执行池不借用容量；停止路径先关闭两池接纳再join，依赖对象活到线程退出。异常启动展开的所有权通过静态代码核对，本报告没有声称实施线程资源耗尽故障注入。

固定控制槽不保证64连接已满、控制槽被占用、IO无法调度或Store锁竞争时的即时取消。4096字节只限制控制分类，不替换2MiB API正文上限。`prepare`、持久任务控制和会话取消仍走普通池。原预算、付款、Worker、数据库、来源批准和恢复合同保持；完整`CACHE-01/B-24`及`C-15`均未因本目录的有限回归签收。
