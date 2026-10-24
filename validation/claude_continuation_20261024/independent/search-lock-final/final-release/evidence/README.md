# P2-5 最终组合 Release：相邻单次观察

使用真实 590 旧 Release 库与最终组合 Release 库、各自匹配的头文件，同一个未修改 NOT/4NOT 驱动以 `g++ -O2 -std=c++20` 重编。两份 Core 均为 Release `-O3 -DNDEBUG`。参数均为 `2000 204800`，在同机按旧→新顺序各执行一次，不重试。

| 查询 | 590 搜索 / stats 最长等待 | 最终组合搜索 / stats 最长等待 |
| --- | --- | --- |
| 单 NOT 短语 | 2567 / 2566 ms | 640 / 150 ms |
| 四 NOT 短语 AND | 2573 / 2573 ms | 1202 / 157 ms |

两组查询均走 `bounded_scan_recent`。这些数字是本机合成数据的各一次观察，没有通过阈值、重复采样、置信区间或 SLA。原程序保留 20 ms 启动等待和 5 ms stats 采样；最大等待不是固定上界。完整进程的约 53 秒包括数据和索引构建，不能当作查询延迟。

原始日志、执行命令、源/库/驱动哈希及限制见 `performance-result.json`、`run-performance-old.json` 与 `run-performance-new.json`。43 份生产输入与先前 Debug/ASan 的清单相同，仅最终文档使整批清单更新为 `208c82be68801bd297045f6b92cc7b4b34bd006e3fee89f83f7b86fd3653ab90`；编译与运行前后输入保持一致。确定性的并发与发布栅栏结论来自另存的 19 场景独立重放，不从性能数字推导。
