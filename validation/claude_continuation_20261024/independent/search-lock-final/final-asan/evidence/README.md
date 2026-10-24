# 最终组合 ASan/UBSan/LSan：P2-5 独立重放

最终产品提交 `e066753af9bd8888eefa5d694e83f677d09a48a5`，执行清单 `664e800624784f7b364de1b7eb286a7d534e94abaafc9e586cc4dd5fad864c82`。只读复制最终头文件和 ASan 库后重链同一 `probe.cpp`，**19 场景 / 191 检查通过**，没有 sanitizer 诊断。`ASAN_OPTIONS=detect_leaks=1:halt_on_error=1`，`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`；直接使用非 ptrace 原生环境运行，没有关闭泄漏检测。

43 份生产输入全部匹配 root 冻结清单，复制、编译及执行前后哈希保持；原始库和头与副本继续一致。详细命令和原始输出见 `compile-probe-asan.json` 与 `run-probe-asan-native.json`。此记录不包含最终 Release 性能成绩，也不把与 Debug 重复的检查累加为产品验收。
