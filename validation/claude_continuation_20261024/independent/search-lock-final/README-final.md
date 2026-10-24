# P2-5 最终组合独立证据入口

最终汇总为 `final-review.json`。Debug 与 ASan/UBSan（开启 LSan）分别对最终组合库重链原探针，各通过 19 场景 / 191 检查。两次是相同场景的重复覆盖，不相加为不同场景或产品验收。

Release 使用同一个原 NOT/4NOT 驱动、各自匹配的旧/新头库，在同机旧→新相邻各一次：单 NOT 搜索/并发 stats 最长等待为旧 2567/2566 ms、新 640/150 ms；四 NOT 为旧 2573/2573 ms、新 1202/157 ms。精确条件及原始日志见 `final-release/evidence/performance-result.json`。这是单次观察，没有性能 SLA 或统计结论。

`archive-manifest-final.json` 是集中归档清单，列出源码、JSON、Markdown 和日志的源路径、相对目标、字节数和 SHA256，不包含库或 ELF。先前 `archive-manifest-debug-asan.json` 和原命令/报告原样保留；旧失败也仍位于其原阶段证据中。43 份编译生产输入在 Debug/ASan 清单与最终文档清单之间一致。
