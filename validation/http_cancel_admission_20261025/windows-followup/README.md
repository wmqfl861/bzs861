# Windows direct_shared 进程超时后续

当前包含提交的验证入口为 [final-report.json](final-report.json)。本目录追加于HTTP取消保留槽已完成本地完整验证之后，保留父目录的原始输入、报告和日志。

## 已观察事实

上一批bedb45c的 [CI40180737461](https://github.com/wmqfl861/bzs861/actions/runs/40180737461) 为Linux成功、Windows17/18。唯一direct_shared在90.03秒达到整套进程watchdog；原runner缓冲stdout，没有逐例进度，因此没有证据说明具体停在哪一例。完整日志保存在 `windows-job-121131070653.json` 的content字段，原CRLF字符串可无损恢复。

独立[诊断](diagnosis.json)未找到明确的Windows专属锁循环。大量DB夹具、大快照和Windows Debug开销是合理解释，但不能据此认定根因；Gate、模拟Worker和join仍可能在错误路径阻塞。

## 追加改动与实际复测

只增加两个测试设施差异：native runner在main即时输出RUN/PASS/FAIL和每例耗时；CMake仅在WIN32下将direct_shared进程watchdog设为240秒。Linux仍为90秒。原34个测试主体、main之前全部字节、断言、业务期限与同步观察期限不变。

| 追加执行 | 结果 |
| --- | --- |
| Debug / ASan目标重建 | 均成功，输入稳定 |
| Debug direct_shared | 34组、10,259断言、0失败；20.702秒，CTest仍为90秒watchdog |
| ASan/UBSan direct_shared | 34组、10,259断言、0失败；67.123秒，开启泄漏检查，持久诊断为空 |
| 当前计划保护 | 13/13通过 |
| 综合保留检查 | 24项通过；149/151执行输入不变，唯一增量为上述两文件 |

本机Debug最慢的完整快照用例为17,013ms，ASan为54,373ms。两个执行有重叠，这些数字仅用于诊断；不能外推为Windows耗时或产品性能保证。新发布SHA的Windows结果尚未在本报告生成时运行，须检查34例逐项完成和原断言汇总；若再次停在RUN，应定位该例，不能无依据继续扩大上限。

父目录完整28套CTest（含1332 Python）、34项HTTP Debug/ASan及独立HTTP探针仍绑定其原输入。4个HTTP可执行文件与2个核心静态库字节不变；32个原生测试程序中仅2个direct_shared重建，其余30个保持。原完整门禁不冒充后来runner的重新执行。

4429份既有validation、24份规划、来源/站点配置、features原始数组、83/161/10与37/46/0数量、版本0.1.10/schema15保持。新证据和全部精确哈希见[保留证明](preservation.json)及最终报告，不改变任何工作包签收。
