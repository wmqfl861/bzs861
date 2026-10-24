# P2-5 最终组合独立复验

输入绑定与结果汇总见 `debug-asan-review.json`。同一个未修改探针对最终组合 Debug 与 ASan/UBSan（开启 LSan）分别重链，各运行 **19 场景 / 191 检查**并通过；不能将重复执行累加成 38 个不同场景。两组复制清单、命令、生产输入和探针哈希、原始日志均分别保留。

集中归档清单为 `archive-manifest-debug-asan.json`；其中只列源码、JSON、Markdown 和日志，不复制静态库或 ELF。库与探针二进制身份保留在执行记录中。最终 Release 性能复验尚未在本记录中执行。
