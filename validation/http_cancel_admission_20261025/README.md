# HTTP 取消操作的保留接纳 · 2026-10-25

本批从已发布 `bedb45c89d293ab25a8a481df6c8300558225b0c` / tree `24b2df08c69dd2937fd6ed54bacd77bd655f7b19` 接续，生产变更仅在 `apps/searchd/main.cpp`。上一批直接共享读取、成员取消、Core/Worker控制协议和原始验证材料保持。版本仍为 **0.1.10 / schema15**。

## Windows CI 后续与当前包含范围

发布本批前，上一个bedb45c提交的CI已结束：Linux成功，Windows **17/18套通过**，`direct_shared` 在 **90.03秒**达到整套进程watchdog。原stdout全缓冲，没有任何逐例进度，无法据此确认卡在哪一例或证明根因。完整Windows日志以保留CRLF的JSON字符串保存于 `windows-followup/windows-job-121131070653.json`；独立诊断见 [diagnosis.json](windows-followup/diagnosis.json)。

因此本次包含提交另增加两个测试设施变更：`tests/direct_shared.cpp` 仅在main增加逐例RUN/PASS/FAIL的即时flush和steady_clock耗时；`CMakeLists.txt` 仅把Windows direct_shared整体watchdog设为240秒，Linux90秒保持。原34个测试主体、main之前全部字节、断言、业务期限和同步观察期限不变。该调整用于获得完整执行或可定位的超时证据，不预先宣称Windows根因已修复。

**当前最终输入与增量复测以 [windows-followup/final-report.json](windows-followup/final-report.json) 为准。** 下方原 [final-report.json](final-report.json)、完整28套CTest、32个原生二进制未变及原13项preservation描述的是HTTP本体完成时的准确输入，均保留原文件。后续重建的两个direct_shared二进制及新Debug/ASan执行单独绑定；其余生产与HTTP二进制未变。下一次准确发布SHA的双平台CI必须另行确认，旧失败不覆盖。

## 实现与合同

新增由主程序拥有的固定 **1 个控制执行槽**。原 Host、Origin、路由、Bearer认证及Content-Type检查通过后，仅对不超过 **4096字节** 的完整正文使用原严格JSON解析器分类：顶层仅 `operation` / `params`，操作为 `web.read.cancel`，params仅含48位小写hex的 `read_id`。重复键（包括解码后相同的键）、未知字段、错误类型、伪操作字符串、尾随数据及超限正文不能借用保留槽。

普通池与控制池分别限制执行中和已接纳待开始的总数，不能互借，无溢出队列。控制池已满时即使普通池空闲也返回原503 / `E_REQUEST_BACKPRESSURE`，被拒请求不进入Service、不延迟执行。分类不访问Store/Worker，原正文仍由Service重新解析并检查主体、owner及句柄。两个池在销毁Service、Store、Worker和IO对象前停止并join。

完整合同见[HTTP执行](../../docs/HTTP_EXECUTION.md)和[成员取消](../../docs/DIRECT_READ_CANCELLATION.md)。4096字节只限制保留槽资格，普通API正文仍限2MiB；更大的合法取消请求按普通请求处理。`web.read.prepare`、`job.control`、`session.cancel`及其他操作没有新增保留能力。原64连接上限、IO调度、Store争用和控制槽本身饱和仍会限制接纳或完成，没有取消时延SLA。

## HTTP 本体阶段实际完成的本地验证

准确命令、开始时间、耗时、源码输入和日志SHA-256见各 `local/*.json`；总报告见 [final-report.json](final-report.json)。

| 执行 | 实际结果 | 证据 |
| --- | --- | --- |
| Debug构建 | 通过，输入未变 | `local/build-debug-v1.json` |
| 正式HTTP/CLI聚焦 | **34/34**，无失败或跳过 | `local/http-debug-v2.json`、同名log |
| 完整CTest | **28/28**，无失败、禁用或跳过，命令耗时209.836秒 | `local/final-ctest-v1.json`、XML与完整detail.log |
| CTest内Python Worker/MCP | **1332 passed** | `local/final-ctest-v1-detail.log` |
| CTest内原生direct共享/取消 | **34组 / 10,259断言**，0失败 | 同上；原生测试本批未修改 |
| 实际HTTP程序ASan/UBSan | **34/34**，启用泄漏检测，持久诊断目录无诊断文件 | `local/http-asan-v1.json`、同名log |
| 原设计合同 | **34/34** | `local/plan-contracts-v1.json` |
| 计划保护 | **13/13**，规划输入保持 | `local/plan-guard-v1.json` |
| Python依赖检查 | 无损坏依赖 | `local/dependency-check-v1.json` |

完整CTest已经重新执行16个原生套件。该批只修改HTTP main，原32个Debug/ASan原生测试可执行文件的字节哈希均与上一批验证报告一致；**本批没有把上一批16套原生sanitizer成绩重新标成新执行**。本批单独构建并运行实际ASan/UBSan `searchd` / `searchctl`，覆盖新增的HTTP接纳和线程生命周期。命令记录包含 `detect_leaks=1`、`halt_on_error=1`及持久 `log_path`，避免临时server.log清理隐藏诊断。

HTTP基线原30个方法中的29个正文逐字保留。唯一更新的旧合同方法为 `test_cancel_is_subject_to_existing_http_business_backpressure`，明确更名/重写为 `test_reserved_cancel_bypasses_full_business_pool_without_lending_its_slot`；另新增4个方法，当前共34个。新增SIGTERM关闭检查是POSIX专属，Windows明确跳过这一条；不把Windows强制结束进程当作graceful join证明。精确共享成员已加入的并发屏障仍由既有原生测试承担，HTTP测试不通过sleep或profile调用推断这一状态。

## 独立复核与基线对照

[独立报告](independent/final-review.json)没有开放阻断问题，原始探针、记录、stderr及manifest均保留。

| 输入 | 实际结果 | 含义 |
| --- | --- | --- |
| 已发布bedb45c的原searchd | **1场景 / 6检查** | 普通1槽被占满时取消503且没有业务效果；证明新保留能力在基线中尚不存在 |
| Debug候选 | **4场景 / 66检查**，通过 | 严格分类、4096/4097、传输优先、owner隔离、两池独立背压、双池在途关闭 |
| ASan/UBSan候选 | 同一探针 **4场景 / 66检查**，通过 | 4个searchd正常关闭均exit0，持久诊断为空，stderr分别保存 |

独立输入前后哈希一致。合法转义字段名可进入控制槽，解码后重复键不可进入；控制槽饱和时被拒的第二取消没有排队，后续同句柄的 `cache_only` 读取返回 `E_NOT_CACHED` 而非 `E_CANCELLED`，证明被拒取消没有改变句柄。正式HTTP回归另验证该情况下同句柄可完成受控Worker读取。正常与信号退出已实测，线程资源耗尽仅做静态路径审查，没有故障注入。独立、聚焦和完整套件存在交叉，不相加为新产品验收数。

## 原失败与环境修正

- `local/http-debug-v1.*`：首个调用误传不存在的仓库根 `fixtures` 目录，产生14个测试/初始化错误。改用真实 `tests/fixtures` 后重跑为v2；未为此改动产品或测试逻辑，原失败保留。
- `local/asan-cli-executable-mode.json`：构建得到的ASan searchctl缺少执行位（0644），运行前恢复为0755；前后文件SHA-256相同。没有用权限修正冒充二进制重建。

## HTTP 本体阶段保留证明与验收边界

[preservation.json](preservation.json)记录实际13项保留检查，复现脚本为 `check_preservation.py`：

- 基线已有 **4,429份validation文件 / 47,147,174字节**全部逐字节保持，旧失败未覆盖。
- 24份规划文件、2份来源/站点配置、67份其他既有测试/夹具和3份版本/schema来源文件保持。
- 原features数组 **83项 / 122,468字节**保持，原数组SHA-256为 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`。
- **83包 / 161验收 / 10切片、37 partial / 46 not_started / 0 accepted、17来源 / 0启用 / 0站点**保持。

相关映射为CACHE-01 / FT-032 / B-24、API-00 / FT-078 / C-01与CONTRACT-01 / FT-003的部分实现。本批不签收完整B-24、CACHE-01或C-15，不增加跨direct/durable/map/crawl/search共享、直接读取持久恢复、真实来源审批、人工qrels或撤销连续性。六项保留决定和只读审计分支保持。

## 发布与CI绑定

本报告在发布本批之前生成；包含它的实际Git提交绑定当前内容。本地完成的门禁与远端发布/CI是不同事实，不能预写未来SHA或未来CI成功。已发布基线的[CI 40180737461](https://github.com/wmqfl861/bzs861/actions/runs/40180737461)仅证明bedb45c；其留存材料在 `baseline-ci/`。

用户已授权推送main。发布时须核对远端父SHA，使用 `expected_sha` 和 `force=false`，验证GitHub新tree与本地已验证tree相同，取回远端真实提交并对齐当前目录；随后查询该准确提交自己的Linux/Windows结果。不创建新tag，不提交运行数据库、凭据、虚拟环境或构建二进制。
