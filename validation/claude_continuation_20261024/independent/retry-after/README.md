# P2-9 独立审核

审核者：`search_lock` 独立子代理，不是 P2-9 作者。最终审签对象为 `a30231a802ab05f0bb3f821c51ef03d156d796d8`，其母提交是首版 `743f79d72698897041b8eb1cc2db85dc5ee6b990`；整体基线为 `59081e14fdda765844031e13f874e588334f1a36`。

**结论：首版发现的阻断已修复，最终对象在本次独立审核范围内通过，没有遗留阻断项。** 本结论不替代集成后的完整 CTest、pytest、15 个 ASan/UBSan 目标或 GitHub CI。

## 发现并推动修复的实际问题

首版依据“剩余时间超过 72 小时”自动修复旧冷却。合法 parse-error 报告建立 72 小时绝对期限后，墙上时钟回拨一小时，剩余时间变成 73 小时，首版便误认其为旧异常：纯 parse-error 的原期限被缩短一小时；有晚到 429 时被缩短 49 小时。原生 590 两例均保持合法期限，而 743 两例均失败。原源码、退出码、库与程序哈希及原输出保存在 `clock_rollback.cpp`、`clock-rollback-result.json` 和相应日志中。

随后扩大为 24 个独立时序场景：`source_states` / `source_admit` / `source_report` 三种入口，双 Store 句柄或重新打开数据库，晚到 429 有无，以及 admission 与 report 同时或相隔 60 秒。后者把回拨点落在 admission 和 report 之间，说明仅取 `max(now, last_request_at, last_ok_at)` 也不能证明旧期限非法。`matrix-before-fix.json` 对同一个 `clock_matrix.cpp` 记录了真实 590 **24/24 通过**、首版 743 **24/24 失败**。没有覆盖或删除首轮失败。

最终修复将新 bounded report 的合法绝对期限以辅助证明与报告一同提交，未知旧 SQL MAX 不会被纳入新证明。旧无证明数据使用明确记录的一年极端异常启发式；未来事件或其他歧义保留。最终 Debug 与 ASan/UBSan 对同一 24 场景均全部通过。

## 独立执行结果

| 执行 | 范围 | 最终结果 |
| --- | --- | --- |
| `clock_matrix.cpp` | 三入口、双句柄/重启、晚到响应、请求与报告间回拨 | Debug 与 ASan/UBSan 各 24 场景通过 |
| `protocol.cpp` | 新旧 Worker 数字、布尔、字符串、浮点、空值与整数极值；24h 边界；共享预算跨 owner；精确到期；parse72h MAX；截断诊断 | Debug 与 ASan/UBSan 各 24 场景 / 252 检查通过 |
| `proof_legacy.cpp` | 延迟两年才报告后回拨至请求之后，三入口与重启仍保护新证明；一年 ±1 边界；未来事件；未知 200 天 MAX 不获新证明；修复追溯跨成功和重启保持 | Debug 与 ASan/UBSan 各 24 场景 / 233 检查通过 |
| `test_worker_independent.py` | 两个已在途共享服务适配器的长429与晚到空结果/短429/parse-error；不重发、精确到期；通用 HTTP 和 robots 长等待对照 | 6/6 通过 |
| 重新执行正式 Probe 用例 | 截断诊断且 stopped source 不重发、持久 reservation / stop 的 resume、仅继续未预留工作 | 3/3 通过 |

最终独立 native 总计 **72 场景**，Debug 和 ASan/UBSan 各运行一次；最终 Python **9/9**。Python 新增测试由审核者独立编写，3 个 Probe 用例来自正式套件。`python-correction-v1.json` 记录 24 个实际加载模块全部来自被审工作树，前后输入哈希一致。早期 `python-v1.log` 保留首次 6 用例运行；最终输入绑定以 `python-correction-v1.json` 为准。

ASan/UBSan 保持 `ASAN_OPTIONS=detect_leaks=1:halt_on_error=1` 与 `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`。通过获准的无 ptrace 本地执行环境直接执行冻结探针，三组均退出 0；没有关闭 leak checks、修改产品构建或跳过失败。编译命令、冻结库/程序/源码哈希、执行参数、时间和日志分别见每组 `*-correction-v1-asan-compile.json` 与 `*-correction-v1-asan.json`。

## 静态与证据核查

检查了 Core 的独立数值约束、Worker 释放 semaphore 前安装共享预算 fence、Probe 持久停止/恢复路径、Store MAX 与事务、proof 只累计已限制报告、晚到成功、饱和算术及旧数据修复的固定锚点。通用 Retry-After 解析器和 robots/map 产品代码没有被这次来源上限改动。来源审核与启用、配额、身份和六项保留决策保持。

`final-commit-binding.json` 将本次使用的 **74 个生产、头、测试、文档输入**与最终提交逐文件核对，全部相同。`header-and-baseline-binding.json` 将 590、743、最终版各 16 个头文件与对应 Git 逐字节核对，避免使用新布局头文件链接旧库。作者声称的旧 Python 32 模块哈希也逐 Git 590 核实一致。最终 Debug 库 SHA256 为 `c777930bbaab84631aca5b85904497b4d3f083fc3f96c13e881438c5f73c9bac`；ASan 库为 `9ddd7d58d165eb55490ca877c5a01c0102ce314e96168ddcc99c29e878ab2396`。

另核看了作者真实旧库 seed → 新库 read → 旧库 reopen 的证据：旧版实际产生 `999999999000` 毫秒等待，新版一小时后得到 `82800000` 毫秒，旧程序重新打开仍为相同期限，且计数和请求时间保留。该实际旧程序生产证据在作者目录；本目录 `proof_legacy.cpp` 的历史边界使用显式 SQLite 夹具，不冒称它们全部由旧版产生。

## 结果边界

`settings` 辅助证明不改变 schema13 的表结构。无证明旧数据缺少响应时间，365 天规则仍是保守启发式，不能恢复全部历史：不足一年或存在未来事件等歧义的存量冷却可能继续保留；当前文档已如实说明。`cooldown_last_repair` 保存最近一次修复追溯，不是无限事件日志。

这些测试使用离线 Worker、受控时钟和临时数据库，没有启用或访问真实搜索来源。集成后的全量门禁和发布判断由 root 单独记录。本目录仅保存可重跑的源文件、命令/哈希绑定和原始日志，不提交临时静态库、可执行文件或数据库。
