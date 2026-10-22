# 0.1.9 五个接续增量的验证记录

本目录从已发布0.1.8 `2c536e9834c4897b0c70e3bd6651b765cc67f45e`、schema11接续。历史 `validation/s2_five_stages/` 等目录不覆盖；本轮实现与范围见 [接续计划](../../docs/handoff/S2_NEXT_FIVE_20261021.md)。五个增量是页面条件刷新、robots条件重验证、真实会话故障、外部来源消融前置、有限容量/公平性循环。

## 当前：共享JSON并发修复与完整复验

c14b3c3的Windows已12/12通过（sessions68.56秒），但同次Linux在真实ACK丢失恢复中报`E_SOURCE_REVIEW: Missing colon`。独立重现确认共享json-c序列化缓冲与非原子引用计数同时存在竞争。当前统一同步共享节点的native访问、引用生命周期和返回缓冲复制，新增两个并发回归；真实故障E2E文件逐字保持。

先读[json-race-report.json](json-race-report.json)、[冻结输入](json-race-execution-inputs.json)和[当前源码清单](json-race-source-manifest.json)。最终整合CTest19/19、0跳过、135.60秒，Python984、core82/714、sessions22/285；11套ASan/UBSan、34原合同、13计划守卫通过。独立同源3案11,200次操作零失败；新鲜真实旧库11→12升级再次验证39张表、默认值及隔离备份。作者、独立设计复核、最终库探针与独立迁移重跑分别归档，重叠计数不相加。

默认沙箱首次禁止本地socket，保留11原生通过、HTTP/E2E失败及Worker超时的完整日志；[环境记录](json-race-loopback-environment.json)说明获准回环后同输入/同Debug产物完整重跑。新报告不隐藏失败，也不预写未来发布提交自身CI。

## 历史：发布后的 Windows CI 修正

首次发布 `b88b76302e850499275b0a8ec2db366f282481f1` 的 Linux CI 通过；Windows 编译通过，12套中11套通过，`sessions` 在60.01秒超时，0跳过。原日志没有子用例输出，不能据此断言只是机器慢。原失败日志、独立观察、作者诊断和补丁复核在新增记录中保留。

后续修正只涉及三个执行输入：Windows `sessions` 的有限超时改为180秒（其他平台仍60秒）；测试主程序刷新逐案进度和耗时；纯存储4MiB背压用例的两次Store共用固定时钟，避免无关的会话/来源期限影响；Windows工作流对原12套开启详细日志。全部21个会话用例、283条断言、20来源×30命中和实际4MiB边界保留，真实期限/故障E2E不变。产品源码和重编后的核心库字节均与原冻结版本一致。

该次历史本地针对性结果为21项/283断言、0失败，CTest 12.07秒；它不替代新提交的完整Linux/Windows CI。该次记录见 [ci-followup-report.json](ci-followup-report.json)；其实际发布c14b3c3的Windows已通过，Linux失败则由当前JSON生产修复接续。原 `final-v2-*`、`final-report.json`、`final-source-manifest.json` 和 `delivery-audit.json` 继续绑定首次发布前的输入与结果，不追溯改写为后续测试成绩。

## 权威入口

| 文件 | 口径 |
| --- | --- |
| [json-race-report.json](json-race-report.json) | 当前JSON并发修复、完整最终执行、原失败与独立最终库验证 |
| [json-race-delivery-audit.json](json-race-delivery-audit.json) | 当前最终独立交付审计：157项检查通过；逐项核对输入、产物、命令、归档和历史保持 |
| [json-race-execution-inputs.json](json-race-execution-inputs.json)、[json-race-source-manifest.json](json-race-source-manifest.json) | 当前188执行输入和195源码/状态文件；相对c14仅4个执行文件改变 |
| [json-race-evidence.json](json-race-evidence.json) | 并发作者/独立探针/新鲜迁移/c14 Windows原始文本证据哈希 |
| [ci-followup-report.json](ci-followup-report.json) | 历史Windows超时、最小测试修正及当时针对性复验 |
| [ci-followup-execution-inputs.json](ci-followup-execution-inputs.json) | 修正后的188个冻结输入，只有三个执行文件不同 |
| [ci-followup-source-manifest.json](ci-followup-source-manifest.json) | 修正提交的195个源码/文档文件哈希 |
| [final-report.json](final-report.json) | 首次发布前完整本地命令、成绩、范围与执行输入绑定 |
| [final-review.json](final-review.json) | 作者/独立审查分工、发现、原失败及复验 |
| [delivery-audit.json](delivery-audit.json) | 最终交付的独立证据核对：执行输入、归档、命令日志与成绩交叉验证 |
| [final-v2-execution-inputs.json](final-v2-execution-inputs.json) | 188个冻结代码、测试、构建、配置、合同和文档输入 |
| [final-source-manifest.json](final-source-manifest.json) | 首次发布前的源码/文档文件哈希；由b88b763的Git树绑定 |
| [environment.json](environment.json) | 实际工具链、依赖、Python版本与构建条件 |
| [evidence-archive.json](evidence-archive.json) | 原作者、独立复核和准备日志的逐文件哈希 |
| [execution-revision.json](execution-revision.json) | v1→v2只修正文档歧义，代码/测试/配置不变；旧命令保留 |
| [final-v2-executable-restoration.json](final-v2-executable-restoration.json) | 首轮CTest失败后的生成产物权限检查；源码与二进制字节未变 |

每条最终命令的 `*-command.json` 保存完整argv、cwd、开始时间、退出码、日志哈希、冻结清单哈希，以及运行前后的二进制摘要。源码在命令前后必须与冻结输入一致。只在清单中明确列出的7份状态/入口说明允许在实际运行后补写结果；它们由最终源码清单和包含报告的Git树绑定。

`d9fba205a66b31aa89a761c3b7606cd456aaf1fa` 是本地代码冻结提交，用于本轮作者与独立复核的来源记录，不冒充GitHub发布SHA。最终发布包含相同运行代码，文件身份由冻结清单核对；本地候选提交和连接器实际创建的发布提交可以不同。包含本报告的实际远端提交及其Linux/Windows工作流须在发布后读取，不在此预写自身SHA或未来成绩。

## 首次发布前的完整本地结果

22条成功最终命令绑定同一final-v2清单：**19/19套CTest、0跳过；Python984项；11/11套原生ASan/UBSan且开启泄漏检测；34项原合同、13项计划守卫及依赖/历史保持检查通过**。完整CTest为138.33秒。初轮失败记录另保留，不计入成功命令；细项与二进制身份以final-report及原命令为准。

## 证据分工

- `evidence/conditional-worker/`：Worker作者343项相关回归、日期修正后97项条件测试；另有独立核心集成审查与同一nofollow harness修复前/后/冻结构建两案。三个数字有不同输入，不相加。
- `evidence/core-independent/`：另一代理的5项事务/robots回收反例和2项publication协议路径，最终7项/81断言。原robots失败、publication测试初版的无效snapshot ID假设均保留。
- `evidence/session-author/`：6个真实进程会话故障用例，以及原生4MiB背压；真实SIGKILL次数不当作独立场景数。
- `evidence/capacity-author/`：4个有限循环、4800断言；1779次提交、280次租约、39次Store重开、268次合成Worker调用只是观察计数，不是长期吞吐成绩。
- `evidence/qa-author/`、`evidence/qa-independent/`：作者128项评测回归及独立20个合成探针。独立探针自己构造3查询/12文档，包含全池IDCG、缺源/失败/empty、身份/冻结篡改和release gate；没有读holdout文本或制造真人标签。
- `evidence/schema12-migration/`：同一driver分别链接真实0.1.8旧库和0.1.9冻结新库，真实schema11→12。39张旧表原有列和数据摘要一致，新字段安全默认、完整200前不条件请求、永久key、旧/新备份隔离均复核。
- `evidence/plan-independent/`：83包/10切片/161验收及17条显式mandatory语义保持，241份上一轮历史文件与黄金文件只比较字节哈希；默认配置未批准新来源。
- `preparation/`：初期构建、fixture字段/条款URL修正、原始失败和环境准备日志。它们不是最终成功门槛。

原证据逐字节复制，保留原机器路径、执行参数与失败说明。库、可执行文件、虚拟环境、运行数据库、backup文件不放进公开仓库；其哈希可在记录中查到。复现迁移时按记录编译driver，使用当前发布源码和真实旧版本头文件/库，选择新的临时输出目录；不要在已迁移数据库上伪装再次升级。

## 实际测试边界

`tests/e2e_revalidation.py` 的本机真实HTTP→Worker→Core验证200→304、Core重启、批读、robots与父子页面、错validator、同源跳转条件隔离和失败限制观察。`tests/e2e_session_recovery.py` 使用实际进程/透明代理与实时时钟，SQL仅观察，不改租约或把API取消当崩溃。

原生事务、容量、过期与robots历史用例使用明确可控时钟及合成Worker；迁移也使用离线WorkerPort。有限重复循环和8轮条件刷新不等同长期公网运行。独立子代理复核不等同真实人类相关性标签或完整产品签收。

默认17来源全部禁用、站点为空；40条黄金查询原字节及20/15/5划分不变。本轮没有真实来源批准、目标机器性能、人工qrels、真实消融质量结论或完整161项验收。工作包状态保持36 partial /47 not_started /0 accepted；共享取消B-24、跨节点B-39、依赖完整性C-13、独立撤销连续性与隔离恢复仍按原计划取证。

## 复现最终门槛

按仓库README安装固定依赖，用CMake构建Debug目标并指定已安装Worker测试依赖的Python；执行完整 `ctest --output-on-failure -V`。原生sanitizer需构建CMake `asan-core`目标，保留 `ASAN_OPTIONS=detect_leaks=1:halt_on_error=1` 与 `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`。新增revalidation/capacity_cycles和既有9个原生目标均须执行。

冻结记录器可通过 `--inputs validation/s2_next_five/json-race-execution-inputs.json --name <新名称> -- <实际命令>` 包装执行；不能覆盖既有日志。原固定合同、只读计划校验、13项计划守卫、依赖检查与 `check_preservation.py` 一并执行。旧原文件内容与默认配置保持检查只读取字节和必要元数据，不用holdout文本调参。

首轮完整CTest返回失败，生成的debug会话测试文件实际为0644；记录权限恢复与哈希保持后重新执行完整CTest。首轮stdout与LastTest记录不完整，保留原文件，不据此推算未显示套件成绩。后续运行在内存中收齐CTest输出后一次写入日志，并保存JUnit。准确结果和命令名以最终报告为准。

本次ASan首轮链接还遇到一个非ELF的生成sessions.cpp.o中间文件。保存原始失败、异常文件哈希与独立观察后，仅移除此生成文件，由相同冻结源码/配置重编并重链，再完成11套sanitizer。未根据此现象臆测编译器、内存或文件系统根因；详见[产物诊断](json-race-asan-artifact.json)。
