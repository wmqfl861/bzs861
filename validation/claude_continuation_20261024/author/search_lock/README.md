# P2-5 作者实现与验证

基线为 `59081e14fdda765844031e13f874e588334f1a36`。本目录为作者证据；独立审核及 root 合并后的完整 CTest / pytest / 15 原生 sanitizer 门禁另记，不用五个聚焦目标冒充完整验收。

## 实现

候选查询只保留有界身份/排序元数据，逐篇复制不可变正文后关闭 SQLite 语句、释放 Store 锁，再精确匹配、评分和排序。候选上限、BM25 与原稳定排序保持。新纯函数 `match_score` 在一次匹配及评分中共享惰性 ASCII 转换；旧 `score_match` API 及其正叶 title/body 评分、field 处理和上限保持，纯 NOT 仍得 0 分。

最终事务重查主体/epoch、owner、删除代际、快照存在、当前及冻结表示的 index/page/site 约束，并复查活动会话容量。开始时的发布限制水位和冻结 redirect bindings 防止匹配期间 `noindex/nosnippet → allow` 恢复旧结果。被删除/裁剪/收紧的候选剔除并标记 partial；不重跑、不自动选入新快照。文档见 `docs/LOCAL_SEARCH_CONCURRENCY.md`。

## 正式回归与实际运行

`tests/unit.cpp` 增加 9 项正式用例，覆盖明确分值金标、三种候选路径的锁/事务释放、删除后重新导入、保留与裁剪旧版本、同/跨 Store 句柄撤权、并发占用最后会话名额、来源政策隔离、站点及 logical/final/intermediate 页身份的收紧后放宽。

- v3 Debug：core、site_publication、retention、sessions、revocation **5/5**；见 `focused-v3.json`。core 为 **97/97，1721 断言**。
- v3 ASan/UBSan：相同五个原生目标全部通过，保持 `detect_leaks=1:halt_on_error=1` 及 UBSan halt；见 `asan-v3-native.json`。五个执行程序及库的 SHA、日志 SHA、实际命令和不变源码均有记录。
- `inputs-v3.json` 绑定六个改动文件，包括说明文档；其余编译输入来自未改的上述基线。此后的独立审核/集成记录应绑定自己的完整输入。
- 私有测试闸门无公共 setter、配置或协议入口。正式测试在匹配阶段暂停，另一线程必须先完成操作，再解除暂停；5 秒仅是死锁 watchdog。

## 旧路径的失败见证

`barrier-regression.cpp` 的正式 helper/case 函数体逐字节取自 `tests/unit.cpp`，只有独立 main/无关用例集合不同；绑定见 `barrier-regression-inputs.json`。

`baseline-instrumentation.patch` 只给 590 头文件及旧匹配位置加入同一个私有闸门，不改变锁/事务/结果语义。旧 src 全库按这一完整布局重编，**没有把新头与未改旧库混合链接**。旧实现同一回归在 `progressed` 断言失败；新实现 **48 断言通过**，命令/库/头/程序/日志绑定见 `barrier-pair.json`。这是明确标注的 instrumentation-only 历史反例；未改的真实旧 Release 复现另列如下。

重放时从 590 archive 出 `CMakeLists.txt src include apps`，应用上述小 patch，`cmake -S <旧树> -B <旧树>/build -G Ninja -DBZS_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Debug` 后构建 bzs_core，再按 `barrier-pair.json` 的编译参数链接 `barrier-regression.cpp`。新树用同一个 probe、当前完整头和当前完整库。`baseline-barrier-fail.*` 是此前同函数体、保留未执行原用例注册的首次失败，也保留原样。

## 同源 Release 观察

原 NOT 变体程序（SHA256 `fe1409cd7c011999cabb80a807a47fcb878f4256b07a51cb0ef0f4d86134d7d6`）、2000 × 200 KiB、每组各一次。旧真实 Release 库 SHA 为 `385bff9799a53e147ff8905679a7d40b0c116a9951e33ec168e7a1aa7f7ef813`，使用 590 原头文件；新库 SHA 为 `f2c8c39d93182bd406a823c666d2b415a464f5ae36c781d7552e7817d914f30e`，使用完整新头。具体日志与命令见 `baseline-negative-release.*`、`fixed-negative-release.*`。

| 查询 | 旧 search / 最长 stats 等待 | 新 search / 最长 stats 等待 |
| --- | ---: | ---: |
| 单 NOT 短语 | 2130 / 2129 ms | 627 / 145 ms |
| 四 NOT 短语 AND | 2630 / 2629 ms | 1147 / 158 ms |

这里证明长匹配/评分阶段释放锁。SQLite 候选选择、单篇复制、最终投影和摘要仍有锁，不声称零等待、通用加速比例、SLA 或生产吞吐验收。两组仍是 `bounded_scan_recent`。

## 保留的失败

- v1 作者来源隔离测试误用未显式配置的 `SourceRegistry()`，触发原有 `Source activation requires an explicit registry`；改为显式空 manifest，v2 通过。原 v1 日志/输入保持。
- v3 首次 sanitizer 在默认 sandbox 的 ptrace 环境触发 LeakSanitizer fatal，退出 1；该运行不记通过，见 `asan-v3.json`。获准原生进程环境后，以完全相同输入和二进制执行五个目标，原失败不改写、泄漏检查不关闭。

审计分支、历史 validation、来源启用、备份/恢复和其他六项保留决定均未修改；没有 schema、tokenizer marker、版本或完整工作包签收变化。
