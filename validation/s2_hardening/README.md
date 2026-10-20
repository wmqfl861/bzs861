# 0.1.5 S2 crawl hardening 验证

输入基线为已交付的 0.1.4：`90ca7e93f9c96a43ff8d99b237638e4aa2aa2c63`，tree `b51e0f126c49727506ad2cc08ff3912d59108703`。本目录只记录本轮增量；历史验证目录保持原样。准确命令、时间、结果和 SHA-256 见 [test_report.json](test_report.json)，范围复核见 [independent_review.json](independent_review.json)。

## 最终运行结果

最终普通 CTest **12/12 通过，0 失败、0 跳过**，耗时 99.40 秒。以下数量来自 [实际日志](ctest-final.log)，各套之间可能覆盖相同语义，不相加为产品验收项。

| 测试套 | 通过用例 | 原生断言 |
|---|---:|---:|
| core | 81 | 713 |
| batch | 18 | 439 |
| map | 24 | 462 |
| crawl | 51 | 2550 |
| site_publication | 24 | 285 |
| contracts | 55 | 269 |
| HTTP / CLI | 20 | — |
| 网页 E2E | 13 | — |
| map E2E | 9 | — |
| crawl E2E | 24 | — |
| 真实进程恢复 E2E | 8 | — |
| Python Worker | 687 | — |

六个原生可执行程序另行通过 ASan、UBSan 与泄漏检查，使用 `detect_leaks=1:halt_on_error=1` 和 `halt_on_error=1:print_stacktrace=1`。HTTP、E2E 与 Python 使用普通构建，没有宣称 Python 获得原生 sanitizer 覆盖。

设计合同参考 34 例、规划防回退 13 例通过；结构检查保留 83 个工作包、161 个验收映射和 10 个切片。固定 Python 依赖检查通过。实际 Linux 启动器完成 init/start/status、站点、能力、来源查询及 stop 共 7 条命令，18 个断言通过，测试拥有的两个进程和端口均已清理，见 [launcher_smoke.json](launcher_smoke.json)。

## 本轮修复与新增证据

站点清单显式激活、审核到期和历史范围撤回在离线重启后仍约束已存储结果与冻结分页。逻辑、中间、最终 URL 绑定和读取代际不可变；收紧后再放宽不复活旧正文或旧冻结结果。普通 FTS、非 FTS 候选在 LIMIT/计数前复核发布资格，物理 FTS 清理每事务最多检查 128 个当前文档。owner 正文与证据按原权限保留。升级说明见 [SITE_PUBLICATION](../../docs/SITE_PUBLICATION.md)。

新增日期/月份族、连续日期导航、query 维度/排列/组合、重复分页完整正文的持久陷阱限制，并保留实际 URL、正常文章/数字 ID/版本路径负例和结构化停止原因。策略是有限启发式，不是全网站分类或近似正文去重；明确入口的豁免不会传给自动重定向。

恢复 E2E 在父进程回环 RPC 代理的明确边界真正 SIGKILL 核心，等待实际租约到期，不写数据库、不调用 pause/resume：派发前；Worker 完整回包但核心尚未收到；同一恢复中删除尚未派发目标；任务完成但客户端丢失异步 accepted 回执。未知执行按完整请求/字节预留计费，不自动重发；已提交快照/边/证据保持，其他未派发尾部继续。另有四例在真实 crawl 后收紧清单并离线重启，核对零新增 HTTP 下的存量发布约束。

## 基线、修正和独立复核

- [baseline-binary.json](baseline-binary.json) 将保留的 0.1.4 searchd SHA-256 与其原提交构建记录对齐。[修正后的基线日志](e2e-recovery-baseline-corrected.log)中四个崩溃/回执场景通过，四个离线清单收紧场景按预期失败。这四个失败才是本次真实进程基线复现的产品缺陷；崩溃恢复部分扩展的是已有机制的故障证据。
- 初次恢复夹具错误地将 failed+partial 预期为 partial 状态，并误判一个 depth=0 成功任务。原始失败日志与 [修正记录](recovery-harness-correction.json) 保留，不把夹具错误算成产品缺陷。基线运行使用当时的测试/fixture 版本，当前 Python 包元数据的版本变化另行说明。
- [原生旧版发布基线](site-publication-baseline.json)的三例预期失败作为补充；该临时原生可执行文件没有保留精确哈希，不冒充前一项已绑定的 searchd 二进制证据。
- 独立上下文复核用保留的 0.1.4 源码/库实际创建 schema 6 数据库，再以最终根构建的库升级。2 个真实跨版本迁移场景和 8 个已提交/在途、rejected/links_only、原范围/新子范围组合全部通过。源文件、两端库、测试二进制和原始数据库均有前后哈希；数据库和可执行文件不提交到仓库。复现与最初 harness 编译类型错误的留档见 [migration_review](migration_review/README.md)。
- 独立复核只对声明的范围负责；广泛 CTest/sanitizer/启动器由根执行者运行，复核者检查记录与输入绑定。作者验证、中间构建和最终门槛分别归档，不重复计数。

## 输入与执行绑定

[execution_inputs-v2.json](execution_inputs-v2.json) 固定 153 个运行、构建、测试、配置和合同输入。每条最终门槛的 `*-command.json` 记录命令前后清单一致、执行二进制 SHA-256、时间、退出码及日志 SHA-256。

第一版 [execution_inputs.json](execution_inputs.json) 与其中间构建保留。最终只追加一处运行时修正：crawl 回执内部不再重复触发外层已经执行的 FTS sweep，见 [execution-revision.json](execution-revision.json)。最终构建、12 套 CTest、6 个 sanitizer 和启动器全部使用 v2。Python editable 安装是在第一版清单下完成；两版 Python 输入完全相同，最终依赖检查与全部运行门槛均在 v2 下完成。

编译前后记录会附带同机其他构建目录的观测；并行构建时其他目录可能正处于链接过程。构建输出绑定只使用该命令负责的目录；最终测试使用的对应二进制必须与该目录最终构建输出及测试前后记录一致，不把无关目录的中途哈希当成被测产物。

[source_manifest.json](source_manifest.json) 覆盖最终非验证文件，包括在测试后按真实结果补全的指定文档。[环境记录](environment.json) 保存最终工具链与 Python 0.1.5 包状态，安装前的原始观察另存 [environment-preparation.json](environment-preparation.json)。报告单独绑定证据文件；包含这些文件的 Git 提交绑定源码与报告，避免未来自身 SHA 的循环引用。

## 复现与发布

按仓库 README 安装编译依赖和固定 Python 依赖。`debug-configure-release-command.json`、`asan-configure-release-command.json` 保存本次实际配置参数；可在自己的虚拟环境和构建目录运行等效配置，然后执行：

```bash
cmake --build build-s2-hardening-review -j1
ctest --test-dir build-s2-hardening-review --output-on-failure -V
```

sanitizer 的六条精确命令见各自 `asan-*-command.json`。独立跨版本复核需要单独从基线提交构建旧库，不能仅把当前数据库的 schema 数字改小。原始日志不可覆盖；`run_validation.py` 使用独占创建，复跑时选择新 `--name`，源码有变化时创建新的命名执行清单。

发布前报告不预写未来 CI 成功。推送后按包含本目录的精确提交查 [GitHub Actions](https://github.com/wmqfl861/bzs861/actions)：Linux 要求 12 套普通测试和 6 个原生 sanitizer；Windows 要求 core/batch/map/crawl/site_publication/contracts/HTTP-CLI 七套阻塞测试。完整 Windows Worker 安装包仍是后续范围。

**产品状态仍为 34 项部分实现、49 项未开始、0 项完整签收，S0/S1/S2 均未签收。** 默认空站点与 17 条禁用搜索来源不变。本批产品路径的真实来源、生产站点、付费服务和模型请求均为零；本机合成 HTTP 不等于生产准入或真实相关性成绩。有限 SIGKILL 边界不是任意指令、物理断电或长期压力验收；通用 GC、独立撤销日志、安全恢复、解析系统沙箱、人工 qrels、SESSION-02 和后续平台交付继续保留。
