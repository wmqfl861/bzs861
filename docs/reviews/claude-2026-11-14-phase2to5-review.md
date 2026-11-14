# 第四次复审：阶段 2–5 集成后的 main（claude-2026-11-12 审核线）

- 复审日期：2026-11-14
- 复审对象：`main` @ `5909228`（PR #24 合入后）。范围 `b85eb56..5909228`，即 PR #12 的合并和 PR #13–#24，共 13 个 merge commit
- 依据：上一份复审 `docs/reviews/claude-2026-11-13-phase1_6-rereview.md` 第 3、6 节；交接文档 `docs/reviews/response-claude-2026-11-12-phase4_5.md`；`docs/CURRENT.md`；`docs/reviews/codex-rulings-20261113-winstop.md`；各 PR 的描述；PR #13–#24 的 41 个提交和 `main` 上 12 个 merge commit 的全部 160 次 CI 尝试（`filter=all`）
- 方式：只读复审，没有修改任何已有分支。验证在临时 worktree 里完成，检出的是 `358ae38`（与 `5909228` 的代码完全相同，只差 PR #24 的两份文档）
- 存放位置：本文件单独推送在新分支 `review/claude-2026-11-14-phase2to5` 上（基于 `5909228`，只多这一个文件），路径为 `docs/reviews/claude-2026-11-14-phase2to5-review.md`。该分支只读，不要合并它
- **用户决定**：用户本轮再次要求"所有需要我决策的，都按你推荐的做"，所以第 5 节的推荐就是用户的决定。此前经 Codex 裁定并已执行的事项继续有效，除非本文另有说明

## 1. 结论

阶段 2 到阶段 5 的工作量很大，整体质量好：

- 阶段 2 完全按清单执行；
- CL-25、CL-26 按规格实现，而且在几处做得比规格更稳妥；
- CL-17、CL-20、CL-07、CL-22、CL-14、CL-19、CL-10、CL-11、CL-18 都已完成；
- 我以前的探针在新 `main` 上全部不回退，变异检查 M1–M3 全部被杀死，构建零告警。

但在用户运行 S0 之前，需要先做一个小批次（D22）。原因是下面四个问题：

- **CL-27（P2）**：`mwmbl_api` 适配器是按错误的响应格式写的，鉴权头也不对。不修的话，S0 里 mwmbl 每次都会报 `parse_error`。证据来自 mwmbl 官方仓库的源码。
- **CL-29（P2 流程 + P3 测试）**：Windows 上有 3 次 CI 失败没有按 D20 记录。其中 PR #16 是在一项检查为红的情况下合并的，合并 #19 后 `main` 的 CI 也红过一次；交接文档和 `docs/CURRENT.md` 却写着"首轮失败全记录"。失败的是 `test_probe.py` 的一个时序假设，我已复现原因并验证了修法。
- **CL-31（P3）**：CL-20 的 TLS 测试证书不符合 RFC 5280，在 Python 3.13 及以上的严格校验下有 2 个测试失败；CI 只测了 Python 3.12。
- **CL-28（P3）**：CL-08 拆分令牌之后，新建的 home 里 `bzs call backup.create` 会被拒绝（403）。我端到端复现过。

另有一项 P3（CL-30：`bzs doctor` 用黑名单而不是白名单判断，`create_auth.py` 先写入再加 ACL），可以放到 S0 之后。

| PR / 事项 | 任务 | 结论 | 说明 |
|---|---|---|---|
| #12（`c6ccfac`） | 阶段 2 合并 | **pass** | 两个父提交的 merge commit；之后 12 次合并也都是 merge commit，first-parent 链干净 |
| 阶段 2 清理 | D13 | **pass** | #5–#11 都有关闭说明；远端删除 18 个 `work/*`；5 个 `archive/` 附注标签指向原分支头；`review/*` 保留 6 个 |
| #13 | CL-05、流程规则 | **pass** | `docs/CURRENT.md` 成为唯一入口；蓝图写入了 10 条流程规则 |
| #14 | CL-26、CL-25 | **pass** | 见第 3 节 |
| #15 | CL-20、CL-07、CL-06、S0、accept_s1 | **pass，但 CL-06 引出 CL-27** | TLS、User-Agent、S0 命令和签收脚本都没问题；mwmbl 适配器有误 |
| #16 | CL-22 | **代码 pass，流程见 CL-29** | 合并时它自己 pull_request 轮的 windows-python 是红的，没有重跑 |
| #17 | CL-08 | **pass，附 CL-28、CL-30** | DACL 写法正确，双令牌逻辑正确 |
| #18 | CL-17 | **pass** | 见第 3 节 |
| #19 | CL-14 UTF-8 截断、CL-19 | **pass** | 合并后 `main` 的 windows-python 红了一次，见 CL-29 |
| #20 | CL-10 | **pass** | Windows 门禁的并集仍是 31 个测试 |
| #21 | CL-25 收尾 | **pass** | |
| #22 | CL-11 | **pass** | 改动行格式门禁比原计划重得多，见第 3 节 |
| #23 | CL-18 | **pass** | |
| #24 | 交接文档、CURRENT.md | **pass，附更正** | D20 记录不完整，见 CL-29 |

## 2. 独立验证

环境：Linux 容器，2 vCPU，Python 3.13.18，OpenSSL 3.5.9。

| 项目 | 结果 |
|---|---|
| Debug 构建 | 通过，**0 条告警**（原来 `tests/cache_freshness.cpp:333` 的告警已由 CL-19 修掉） |
| CTest | 43 个测试中 42 个通过。唯一失败的是 `python_worker`（即全量 pytest），原因见 CL-31 |
| pytest（CTest 内） | 1,831 通过、2 失败、23 跳过；失败的 2 个都在 `tests/test_fetch_tls.py` |
| 变异检查 | 基线通过；M1 被 `bzs_tests` 的 `contributor_overflow_tightens_capture_ceiling` 杀死（CL-19 之后表现为断言失败而不是异常）；M2、M3 被 `bzs_session_tests` 杀死；源码与构建已恢复 |
| R1（CL-02） | 8 行全部 `found=yes partial=false` |
| R2（CL-04） | 6 行全部 `REFUSED` |
| MCP 数组字符串 | 解码后以列表转发；非 JSON 字符串仍被拒绝 |
| CL-12 平手反例 | 两种来源顺序输出逐字节相同 |
| `backup.create` 端到端 | 新 home 里执行 `bzs init`、`bzs start` 后，`bzs call backup.create` 返回 `E_FORBIDDEN: Backup requires an administrator token`；换用 admin.token 后成功（148 页，quarantined）。见 CL-28 |
| `bzs doctor`（POSIX） | 各秘密文件 `mode 0o600`，结论正常 |
| probe 时序问题 | 把每次写报告人为延迟 0.1 秒，`test_resume_continues_only_unreserved_work_with_the_same_quota` 稳定失败，报错与 CI 完全一致；按 CL-29 的修法，延迟 0、0.1、0.5 秒下都通过，`test_probe.py` 45 个全过 |
| TLS 测试证书 | 按 CL-31 补上证书扩展后，6 个 TLS 测试在 Python 3.13 上全过；原证书下 2 个失败 |

**CI 全部尝试**（160 次，其中 11 次失败）：

| 提交 | job | run | 原因 | 是否记录 |
|---|---|---|---|---|
| `13d65f8`（#14 中间提交） | linux ×2 | `43393835809`、`43393851541` | Windows 专用的假 API 测试在 Linux 上运行 | 已记录（`8a8f6d9` 的提交说明） |
| `f157e7f`（#16 head） | windows-python（pull_request） | `43414366832` | `test_probe` 时序问题 | **未记录**；PR #16 带着这项红色检查合并 |
| `3dc8722`（合并 #19 后的 `main`） | windows-python（push） | `43431373105` | 同上 | **未记录** |
| `7ae0133`（#22） | linux ×2 | `43480860856`、`43480876964` | ruff 的哈希固定少了 manylinux 哈希 | 已记录 |
| `410a146`（#22） | linux ×2 | `43482325867`、`43482335786` | pip 续行语义 | 已记录 |
| `410a146`（#22） | windows-python（pull_request） | `43482335582` | `test_probe` 时序问题 | **未记录** |
| `040a57a`（#23） | linux ×2 | `43499671354`、`43499686222` | 改动行格式门禁首次拦截 | 已记录 |

从 2026-11-13 10:55（PR #15 合并）起，windows-python 一共跑了 33 次，有 3 次因为这个时序问题失败，约 9%。

## 3. 逐项复核要点

- **阶段 2**：PR #5–#11 的关闭说明齐全，#10 写明了 4 个等价补丁和 3 个不再合入的记录提交。5 个 `archive/` 标签分别指向 `d80b8fc`、`13591de`、`e1bb34b`、`9e47746`、`902ea81`，都是原分支头，提交说明和标签说明都对得上。
- **CL-26**：按规格删除了空锁文件写入的两行。
- **CL-25**：诊断信息、两步打开句柄、排除"创建早于名义父进程"的条目、无句柄时用 `NtQuerySystemInformation` 读创建时间，都按规格实现了。我核对过 `SYSTEM_PROCESS_INFORMATION` 的 ctypes 布局：x64 下创建时间位于偏移 0x20，与 winternl.h 一致。在此之上还加了两点，都更稳妥：
  - 创建时间相等的进程从不发信号，只有一直持有的同一个查询句柄进入 signaled 状态才算确认退出；
  - TerminateProcess 被拒绝时，用同一句柄等剩余预算再判断。

  另外两点说明：诊断插桩默认关闭，但 `process_control.py` 里有 60 多处 `_forensics_on()` 判断，增加了阅读负担，处理办法见 D25；Windows 专用的假 API 测试在 Linux 上改为跳过，这部分覆盖现在只在 windows-python 上。
- **CL-20、CL-07**：TLS 上下文的写法和"自定义上下文必须保留校验"的检查都正确。`DEFAULT_UA` 已改为诚实标识。顺带一提：`python/bzs/worker/sources/base.py:26` 的 `API_USER_AGENT` 还把版本写死为 `0.1`，可以改用 `__version__`（可选）。
- **CL-06**：三份来源审核记录写得清楚，没有越权写成"已批准"；但 mwmbl 适配器有误，见 CL-27。审核记录指出的"每日 500 次与每月 1,000 次口径不一致"，我的决定见 D23。
- **S0 命令和 `tools/accept_s1.py`**：S0 命令只探测 D1 的四个来源，并且明确"退出 0 不等于获批"。签收脚本在隔离的 BZS_HOME 里自己启动服务，检测到启用状态下使用共享 key 会直接判 FAIL。这两项都没问题。
- **CL-22**：下限和回归测试都符合 D14。
- **CL-08**：DACL 的长度计算和对齐都正确（SID 长度恒为 4 的倍数）。`bzs init` 先保护 BZS_HOME 目录，再在其中写秘密文件，所以这些文件从创建起就继承了受保护的 DACL。MCP 默认使用非管理员令牌，旧的单令牌 home 继续可用。问题见 CL-28、CL-30。
- **CL-17**：分页备份每批持锁 64 页。同一连接上的写入会被 SQLite 自动同步到进行中的备份，期限到了就丢弃暂存副本，同一时间只允许一个备份。测试覆盖了备份暂停期间的检索和写入、并发第二个备份被拒，以及超时后不留部分文件。
- **CL-14、CL-19**：UTF-8 截断算法逐个分支核对过，正确；两处 CHECK 的写法、缩进修正都正确。
- **CL-10、CL-11**：TSan、Release 两个 job 和 `--require-hashes`、ruff 门禁都正确。改动行格式门禁可用，PR #23 上已经拦截过一次；但它连同测试共 1,169 行，原计划只是一条 `git clang-format --diff`。S1 签收前不要再扩展它；以后需要改动时，优先考虑换回简单做法。
- **CL-18**：文档里的包含关系、定义域限定和长度边界反例都写得准确，性质测试覆盖面足够。

## 4. 新发现

### CL-27（P2）mwmbl 适配器与真实的 v2 API 不符

**证据。** mwmbl 官方仓库 `main` @ `cce49fa`（2026-11-13）：

- `mwmbl/tinysearchengine/search.py:289` 起的 `_register_search_v2` 在描述里写明"returns results in SearXNG-compatible format"，返回的是对象 `SearchResponse`（`:94`）：`query`、`number_of_results`、`results`、`images`、`monthly_usage`、`monthly_limit`。
- `results` 的元素是 `SearchHit`（`:84`）：`url`、`title`、`title_highlights`、`content`、`content_highlights`、`engine`、`score`。`mwmbl/format.py:204-219` 的 `format_result_v2` 生成的 `title` 和 `content` 是纯字符串。
- 鉴权头是 `X-API-Key`（`search.py:338`），key 无效时返回 401（`:343`）。

本项目的适配器 `python/bzs/worker/sources/apis.py:220` 起的 `Mwmbl` 却：

- 要求顶层是数组，否则报 `parse_error`（`:250`）；
- 读取 `extract` 字段（`:261`），而 v2 叫 `content`；
- key 放在 `Authorization: Bearer` 里发送（`:235`），mwmbl 根本不读这个头，所以带 key 也会被当作匿名请求。

**原因。** 夹具说明（`python/tests/fixtures/sources/mwmbl/README.md`）已经承认，夹具是"按文档手工录制"的，没有抓过真实响应。v1 接口确实返回数组，但 v1 里的 title 和 extract 都是分段数组，所以这份夹具和 v1、v2 都对不上。

**影响。** 真实响应全部被判为 `parse_error`，S0 里 mwmbl 会整源失败，用户要多跑一轮。

**修改**（一个提交，`fix(CL-27): parse the SearXNG-shaped mwmbl v2 response`）：

1. 解析对象：顶层必须是对象，`results` 必须是数组（保留不超过 1,000 条的上限）；逐条读取 `url`（必填，只接受 http/https）、`title`（必填，字符串）、`content`（可空，字符串）；其他字段忽略。
2. key 改为 `X-API-Key` 头发送；HTTP 401 抛出独立的状态（例如 `auth_failed`），并停止该来源的剩余预算。
3. 夹具按上游 schema 重写：`search_ok.json` 用 `SearchResponse` 的结构，结果元素带 `engine`；另加顶层是数组（旧格式）应被拒绝、缺 `results`、`title` 不是字符串三个反例。
4. `docs/sources/mwmbl.md`：更正响应格式和鉴权头，记录 401；说明 `engine` 可能是 `wikipedia`，也就是 mwmbl 会混入维基百科来源的结果，去重会处理这部分重叠；写明本次事实的来源是上游仓库的提交 `cce49fa`。
5. `config/sources.json` 的 mwmbl 配额按 D23 改。

**测试**：先写这些测试并确认在当前代码上失败；修改后通过。

### CL-28（P3）拆分令牌之后，`bzs call backup.create` 在新 home 里被拒绝

**证据。** 见第 2 节的端到端结果。`cmd_call`（`python/bzs/cli.py:239`）通过 `_client(cfg)` 使用 `api.token`（`:243`）。CL-08 之后，新 home 的 `api.token` 是非管理员的 owner 令牌，而核心要求 `backup.create` 必须用管理员令牌。文档里也没有说明怎么做备份。

**修改**：`cmd_call` 遇到只能由管理员执行的操作（`backup.create`、`principal.revoke`）时改用 `config.admin_token()`，其他操作仍用 owner 令牌。补一个测试：在新 home 里，这两个操作带的是 admin 令牌，其他操作带的是 owner 令牌。`docs/WINDOWS.md` 写明备份要用 admin 令牌，并说明 `bzs call` 会自动选择。

### CL-29（P2 流程，P3 测试）Windows 上有 3 次失败没有记录；`test_probe` 有时序假设

**证据。** 见第 2 节的 CI 表：3 次 windows-python 失败都是 `test_resume_continues_only_unreserved_work_with_the_same_quota`，都没有记录。交接文档写着 #16、#19"首轮失败：无"，`docs/CURRENT.md` 写着"首轮失败按 D20 全记录"。PR #16 是在它自己的一项检查为红、而且没有重跑的情况下合并的，这违反了"CI 绿后合并"的规则。

**原因。** 探测的节奏按"距上一次请求开始的时间"计算（`python/bzs/probe.py:253,266-268`），测试里间隔只有 200 毫秒。第一条查询完成后要写三次检查点并 fsync，如果这部分 I/O 超过 200 毫秒（Windows runner 上会发生），就不需要再等，于是第二条查询之前没有任何 await。测试的取消因此落在第二条查询之后，计划状态变成 `completed, completed, skipped`，断言失败（`python/tests/test_probe.py:184`）。产品行为没有问题：真实适配器都有网络 I/O，一定会让出事件循环。

**修改：**

1. 测试（只改测试，已验证）：在被中断的那一轮里，把 `probe_module._now_ms` 冻结为一个常数，这样不管 I/O 多慢，第二条查询前都还欠着完整的 200 毫秒间隔，取消一定落在等待期间；恢复运行之前把它改回真实时钟。注意不要用 `monkeypatch.undo()`，它会把 `scripted` 夹具的离线适配器一起撤销。
2. 记录更正：新建 `docs/reviews/response-claude-2026-11-14-pre-s0.md`，补上上表 3 次失败的 run、测试名和原因（引用本节）；把 `docs/CURRENT.md` 里"首轮失败按 D20 全记录"改为如实的描述。旧的交接文档不改。
3. 流程：见 D28。

### CL-30（P3）`bzs doctor` 用的是黑名单；`create_auth.py` 先写入再加 ACL

- `bzs doctor` 只检查三个 SID 是否可读（`python/bzs/winsecurity.py:24` 的 `BROAD_READ_SIDS`，判断在 `:312-314`）。授予 INTERACTIVE（S-1-5-4）、Guests、ALL APPLICATION PACKAGES 或其他某个具体用户读取权限的 ACE 都会被当成正常。改为白名单：只允许当前用户、SYSTEM 和 BUILTIN\Administrators，其他任何主体有读取权限都报错。
- `tools/create_auth.py:12-17` 先写入令牌再设置 DACL；如果目标目录没有保护，令牌会有一小段时间以继承的 ACL 存在。改为先创建空文件、设置 DACL，再写入内容。BZS_HOME 里的文件不受影响，因为 `bzs init` 先保护了目录。

### CL-31（P3）TLS 测试证书不符合 RFC 5280；CI 只测 Python 3.12

- **证据。** Python 3.13 起，`ssl.create_default_context()` 默认启用 `VERIFY_X509_STRICT`。我在 Python 3.13.18 / OpenSSL 3.5.9 上运行时，`tests/test_fetch_tls.py` 有 2 个测试报 `certificate verify failed: Missing Authority Key Identifier`。CI 在 Linux 和 Windows 上用的都是 Python 3.12（日志里的 wheel 标记都是 cp312），锁文件也写着"Python 3.12 dependency set"，而 `python/pyproject.toml` 声明的是 `requires-python = ">=3.10"`。在 3.13 上，全量 pytest 只有这 2 个失败。
- **修改**：`_certificate`（`python/tests/test_fetch_tls.py:67-78`）给 CA 和服务器证书都加上 SubjectKeyIdentifier 和 AuthorityKeyIdentifier，并给 CA 加上 KeyUsage（keyCertSign、cRLSign，critical）。我验证过，改后 6 个 TLS 测试在 3.13 上全部通过。README 写明 CI 实测的是 Python 3.12，3.13 及以上的 CI 见 D27。

## 5. 决策（按用户指示：全部按审核方推荐执行）

D1–D21 不变，此前经 Codex 裁定并已执行的事项继续有效。本轮新增：

| # | 决定 | 理由 |
|---|---|---|
| D22 | 用户运行 S0 之前，先做"S0 前修复"批次：CL-27 → CL-29 → CL-31 → CL-28，从 `main` 开一个分支、提一个 PR | CL-27 不修，S0 必然白跑一轮；其余三项都很小，一起做可以让 CI 和交接记录在 S0 前回到干净状态 |
| D23 | mwmbl 配额：匿名时 `daily_cap` 和 `request_quota` 都设为 30（窗口 86400 秒），`min_interval_ms` 保持 1000；用户以后申请了免费 key，就改为 60。不在 mwmbl 账户上设置消费上限，保持免费额度为硬上限。在 S0 和来源审核完成前，`enabled` 保持 false | 匿名额度是每月 1,000 次，每天 30 次能保证 31 天的月份也不超过 930 次；免费 key 是每月 2,000 次。上游代码里消费上限为 0 时，月上限就是免费额度 |
| D24 | CL-30 放到 S0 结果回来之后、运行 `tools/accept_s1.py` 之前，单独一个小批次 | 不影响 S0；但签收前应当修好 |
| D25 | CL-25 的诊断插桩：S1 签收后，如果 CI 连续 14 天没有再出现签名 3 类失败，就用一个提交删掉；在此之前保留 | 当初保留它是为了在失败时取证；稳定之后就只剩阅读负担 |
| D26 | CL-13、CL-15、CL-16、CL-19 其余部分：S1 签收前都不做。签收后先做 CL-13 的测量，CL-15 也只测量；CL-19 的 FTS 提前终止要测量证明有必要才做；CL-16 永远不单独开批次 | 遵守 S1 冻结规则 |
| D27 | Python 3.13 及以上：S1 签收后，加一个只跑 pytest 的 3.13 CI 任务，并配一份带 cp313 哈希的锁文件；在此之前，README 写明实测版本是 3.12 | 现在只有 CL-31 一处不兼容，修掉后风险很小；签收前不再扩展 CI |
| D28 | D20 落地：写交接文档或合并 PR 之前，列出这一批每个提交以及合并后 `main` 的全部非成功尝试（命令见第 7 节），逐条记录。有红色检查、又没有记录并重跑转绿的 PR 不得合并；合并后 `main` 变红，当天记录并开修复任务 | 本轮的漏记都出在"只看 PR 最后状态"上 |

## 6. 下一步（给本地 agent 的任务单）

规则不变：每个任务一个提交，提交信息以任务 ID 开头，提交说明写明新增测试名和验证结果；只认每个提交自己的 CI，并按 D28 核对全部尝试。

**S0 前修复（现在做）**

从 `main` 开分支 `work/pre-s0-fixes-20261114`，依次提交：

1. CL-27（第 4 节）。
2. CL-29：测试修复一个提交；记录更正（新建 `docs/reviews/response-claude-2026-11-14-pre-s0.md`，并改 `docs/CURRENT.md`）一个提交。
3. CL-31。
4. CL-28。
5. 最后一个提交：把本文件原样存为 `docs/reviews/claude-2026-11-14-phase2to5-review.md`（从只读分支 `review/claude-2026-11-14-phase2to5` 取），同时更新 `docs/CURRENT.md`：下一步写"用户运行 S0"，待决事项写"无"。

提一个 PR 到 `main`，CI 绿后合并。

验收：

- 每项新测试都先失败、修改后通过；
- CTest 43 个全部通过；Linux 和 Windows 上的 pytest 都是 0 失败，Windows 通过数不少于 1,855；
- 在用户的 Windows 机器上把 `test_resume_continues_only_unreserved_work_with_the_same_quota` 连续跑 50 次，0 失败；
- 按 D28 核对全部 CI 尝试，并记录在交接文档里。

**S0（用户）**：上面的 PR 合并后，用户按 `docs/S0_COMMAND.md` 在自己机器上运行，把结果目录交回审核。

**S0 之后**：审核方审 S0 结果，给出启用决定 → CL-30 小批次 → 按审核结论启用来源 → 用户在自己机器上运行 `tools/accept_s1.py`。

**明确不做（不变）**：扩展 B-24；新增"证明链"类机制；不必要的 schema 升级；改写 git 历史；启用抓取型来源；伪装成浏览器的 User-Agent；为了让测试通过而删除或放宽断言。另按 D26，S1 签收前不做任何测量类和重构类任务。

## 7. 本次复审用到的方法（供复核）

- **CI 全部尝试**：对每个提交运行

  ```
  gh api "repos/wmqfl861/bzs861/commits/<sha>/check-runs?per_page=100&filter=all" --jq '.check_runs[] | select(.conclusion != "success") | "\(.name) \(.conclusion) \(.details_url)"'
  ```

  再用 `gh api repos/wmqfl861/bzs861/actions/jobs/<job>/logs` 下载失败的日志，查看失败的测试。D28 要求的核对就是对这一批的每个提交和合并后 `main` 的 merge commit 运行这条命令。
- **mwmbl**：浅克隆公开仓库 `github.com/mwmbl/mwmbl`（`cce49fa`），只阅读、不运行，读的是 `mwmbl/tinysearchengine/search.py`、`mwmbl/format.py`、`mwmbl/pricing.py`。
- **probe 时序**：写一个 pytest 插件，在 `bzs.probe.write_report` 前 `time.sleep(SLOW_CHECKPOINT_S)`，分别用 0、0.1、0.5 秒运行该测试，修改前后各跑一遍。
- **TLS**：在 Python 3.13.18 上运行 `tests/test_fetch_tls.py`，比较原证书和补上扩展后的证书。
- **`backup.create`**：在临时 BZS_HOME 中依次运行 `bzs init`、`bzs start`、`bzs call backup.create`，再用 `SEARCH_API_TOKEN=<admin.token 的内容>` 运行一次，最后 `bzs stop`。
- **其余**：构建、CTest、变异检查和 R1/R2 等探针的做法同前几份复审。
