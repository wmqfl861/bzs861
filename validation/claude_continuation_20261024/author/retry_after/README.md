# P2-9 作者验证

**首版与修正的区分：** 下方“实现与边界”“实际执行”记录的是首版 `743f79d`。独立审核随后复现了合法 72 小时期限在回拨后被误缩短；首版不能作为最终验收。原运行文件和下方历史说明均保留。最终语义以 `docs/SOURCE_BACKOFF.md` 与文末“回拨修正”记录为准。

基线：`59081e14fdda765844031e13f874e588334f1a36`。只修改独立工作树，审计分支与全部既有 validation 文件保持。

## 实现与边界

搜索 SourceError / Worker 共享服务栅栏 / Probe / Core / Store 对单次限流退避采用 24 小时上限，Core 不信任旧 Worker 的超长数值；增加显式截断诊断。正常秒数和 HTTP-date 保留，API backoff 采用同一搜索专属边界。通用解析器及 robots/map 代码不变。Worker 校验与 Core 对齐的三项整数冷却配置范围。

新退避仍与既存期限取 MAX，合法 parse-error 72 小时与晚到成功/429 保持。旧记录只在剩余期限明确超过原合法最大 72 小时时自动修复，使用最后请求时间为固定锚点并持久写回；24–72 小时模糊存量保留。没有有效旧时间戳则仅首次修复使用当前时刻。日配额、次数、身份、审核、来源启用及 schema 保持。

完整行为、兼容字段与诊断保留范围见 `docs/SOURCE_BACKOFF.md`。`source_states` 的旧记录修复有事务，因此该状态读取现在可能写入调度修正。

## 实际执行

| 运行 | 结果 |
| --- | --- |
| `baseline-native`：同一新正式 unit.cpp 链接保留的旧库 | 89/92，3 个新用例失败，1346 断言；原控制与合法等待保留用例通过 |
| `native`：新 Debug | 92/92，1465 断言 |
| `baseline-python`：新正式用例 + Git 逐文件核对的旧 Python 包 | 38 失败 / 1 通过 / 82 未选择；失败同时包含实际超长值、旧配置未校验及新增诊断字段不存在，不能把每个失败都说成单独复现了 31 年问题 |
| `current-python-focused` | 新增 39 参数化用例全通过 |
| `python-sources-and-probe` | sources 与 probe 完整相关套件 121/121，通过真实回环夹具；未选择的其他 Python 套件不算已跑 |
| `asan-native` 首次 | 默认沙箱下 LeakSanitizer 因 ptrace 限制 fatal，退出 1；原失败保留，不能算通过 |
| `asan-native-approved` | 相同构建、开启 detect_leaks/halt_on_error，在获准环境 92/92、1465 断言通过 |

`legacy_probe.cpp` 用真实旧库创建调度记录，输出 `999999999000` 毫秒等待；一小时后的新库读取修复为固定旧请求时刻 + 24 小时，剩余 `82800000` 毫秒。随后旧程序重新打开得到相同已修复状态，证实持久写回。失败次数、最后请求时间、当天计数和最后状态均保留；只有期限及异常诊断改变。没有手造 user_version 或解除隔离。

正式回归位于 `tests/unit.cpp`、`python/tests/test_sources.py`、`python/tests/test_probe.py`。覆盖溢出、边界时间、旧/新 Worker、共享服务多 owner、精确到期、重启与不滑动续期、合法 72 小时 MAX、HTTP-date/API backoff、probe 停止后不重发、robots 未受搜索上限影响。

## 输入绑定与证据

每个命令对应同名 `.json` / `.log`，含 argv、cwd、退出码、耗时及日志哈希。命令记录的 `inputs` 是作者工作树快照，包含新回归；旧代码运行的真实生产输入另由 `baseline-inputs.json` 与 `execution-binding.json` 明确绑定：旧 Python 32 个模块逐字节匹配基线 Git，旧 native 链接单独保留的库，两个新 Python 测试与正式文件一致。`artifacts-final.json` 绑定最终 Debug / ASan 与探针文件。

这些是作者的聚焦验证；完整 CTest、完整 pytest、原 15 个 ASan/UBSan 程序、独立复核及最终 GitHub CI 由集成阶段单独记录。本目录不据作者成绩宣称整批或整项目验收完成。

## 回拨修正

独立 reviewer 用真实旧库与首版 `743f79d` 对照发现：合法 parse-error 72 小时期限遇一小时回拨，首版会缩短一小时；若期间有晚到 429，则缩短 49 小时。原探针和记录保留为 `clock-rollback-before-fix.cpp` / `.json`。仅把请求时间作为锚点不能解决“请求发出一分钟后报告 72 小时，随后回拨到两者之间”的反例。

修正后的 Store 在现有 `settings` 中持久保存每个共享来源预算由有界新报告形成的合法最大绝对期限。该证明只来自本次合法报告，不把未知旧 `MAX` 盖章；晚到成功不会清除，状态修改与证明同一事务。历史记录只按文档规定的 **365 天极端异常启发式**保守修复，同时检查当前时间和最近已知事件。存在未来事件或不足一年等歧义时保留；不宣称从旧列可以证明所有旧值非法。修复记录保留原截止时间、锚点、观察时间与原因，跨成功和重启保留。表结构和 schema13 不变。

新增正式用例覆盖回拨至请求之前、请求与报告之间、两年回拨、晚到成功/429、重启、未知旧 MAX 不获证明、模糊 180 天旧值保留、未来事件时间保留与恢复、持久修复诊断，以及证明写入失败使报告或读取修复整体回滚。

| 修正运行 | 结果 |
| --- | --- |
| `clock-fix-743`：当前正式 unit.cpp 链接独立保存的首版库 | 92/96，4 个新增用例失败；其中回拨和保守旧值用例直接复现错误，另两项也检验首版尚不存在的证明/事务合同 |
| `clock-fix-debug` | 96/96，1638 断言 |
| `clock-fix-asan` | 同一生产/测试输入，ASan/UBSan 与 LSan 开启，96/96，1638 断言 |
| `clock-fix-legacy-old-seed` → `clock-fix-legacy-current` → `clock-fix-legacy-old-reopen` | 真实 590 旧库创建新 DB，等待 999999999000 毫秒；修正库在一小时后写回 82800000 毫秒，原程序重开得到相同值，原期限和原因也已持久记录 |

`clock-fix-artifacts.json` 绑定本轮产品、正式测试、Debug/ASan 产物、旧库与探针的哈希，并解释旧代码命令实际链接输入；原 `artifacts-final.json` 仍指首版，不替换。此次修正仅改 Store 与 native 回归，Python 字节与前述 121/121 作者运行一致。整合后全套门禁由主工作树另行执行和绑定。
