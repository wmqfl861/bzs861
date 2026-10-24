# 最终组合 Debug：P2-5 独立重放

最终产品提交 `e066753af9bd8888eefa5d694e83f677d09a48a5`，执行输入清单 `664e800624784f7b364de1b7eb286a7d534e94abaafc9e586cc4dd5fad864c82`。只读复制最终头文件及 Debug 库后，重链原 `probe.cpp` 并运行，**19 场景 / 191 检查通过**。编译与执行前后，生产源文件、复制的头/库、探针及原始冻结清单哈希均保持；原始库与副本继续一致。

本记录只证明 Debug 独立重放，不包含最终 ASan 或 Release 性能成绩。详细命令、哈希、分组与边界见 `review-result.json`、`compile-probe-debug.json` 和 `run-probe-debug.json`；旧首次测试初始化错误及修正仍保留在原独立记录中。没有修改 root 的源码、产物或待审核文档。
