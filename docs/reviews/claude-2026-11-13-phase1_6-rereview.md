# 第三次复审：PR #12 阶段 1.6（claude-2026-11-12 审核）

- 复审日期：2026-11-13
- 复审对象：PR #12（草稿），head `b85eb56`，base `main` @ `1d8e80e`；本轮新增 2 个提交 `c7be1d7..b85eb56`
- 依据：上一份复审 `docs/reviews/claude-2026-11-13-phase1_5-rereview.md`（已由 `b85eb56` 原样入库）第 4、6 节；交接文档 `docs/reviews/response-claude-2026-11-12-phase1_5.md`；PR #12 的描述；各提交自己的 CI，包括所有重跑记录
- 方式：只读复审。没有合并 PR #12，没有修改任何已有分支。验证在临时 worktree（检出 `b85eb56`）里完成
- 存放位置：本文件单独推送在新分支 `review/claude-2026-11-13-phase1_6` 上（基于 `b85eb56`，只多这一个文件），路径为 `docs/reviews/claude-2026-11-13-phase1_6-rereview.md`。该分支只读，不要合并它
- **用户决定**：按 D17，本文第 5 节的推荐就是用户的决定

## 1. 结论

CL-23 和 CL-24 都通过。D12 的合并条件已经满足，现在可以进入阶段 2，合并 PR #12。

新发现两项，都不是阶段 1.x 引入的，不阻塞合并，但必须在阶段 3（在用户机器上做 S0 和签收）之前修好：

- **CL-25（P2）**：Windows 上停止进程树时偶发"stop not confirmed; record retained"。原因很可能是把父 PID 被复用的无关进程算进了这棵树。
- **CL-26（P3）**：Windows 上 `control_lock` 第一次创建锁文件时有一处写入竞争，导致并发添加 MCP 令牌偶发 `PermissionError`。

另外更正我上一份复审的一处错误：第 2 节的 CI 表只看了每个 job 的最后一次尝试，漏掉了 5 次被重跑掩盖的 Windows 首轮失败（见本文第 2 节）。本地 agent 在交接文档第 5 节如实记录了这些失败，这一点做得对。

| 任务 | 提交 | 结论 | 一句话理由 |
|---|---|---|---|
| CL-23 | `002c35c` | **pass** | 7 个文件恢复为原始字节，清单摘要重新 139/139 吻合；改动正好是 `.gitattributes` 3 行和 `ci.yml` 1 行；检查放过的 CRLF 条目正好是这 7 个 |
| CL-24 | `b85eb56` | **pass** | 交接文档补齐了三个红提交的原因和 run 链接、CL-12r/CL-14r 的修改前失败记录和新增测试名；复审文件逐字节入库；PR 描述已更新 |

## 2. 验证

| 项目 | 结果 |
|---|---|
| 7 个证据文件的换行属性 | 全部 `i/crlf w/crlf attr/-text` |
| 与 `6d550cd` 的字节比较 | `002c35c` 和 `b85eb56` 上都完全相同 |
| `git diff --ignore-cr-at-eol c7be1d7 002c35c` | 只有 `.gitattributes` 新增 3 行和 `.github/workflows/ci.yml` 改 1 行，内容与上一份复审给出的完全一致 |
| CRLF 检查 | 输出为空；被豁免的 CRLF 条目正好是这 7 个 |
| 再运行 `git add --renormalize .` | 没有任何改动 |
| 5 份清单里的摘要引用 | 139/139 吻合（`c7be1d7` 上是 128/139） |
| 复审文件入库 | blob `0a2eb8d`，与只读分支上的文件相同 |
| 改动范围 | `c7be1d7..b85eb56` 共 11 个文件：上面 2 个配置文件、7 个证据文件和 2 个文档，没有任何源码或测试改动，所以 `c7be1d7` 上的构建和测试结果（上一份复审第 2 节）仍然有效 |
| CI | `002c35c`：push 一轮三绿，首次尝试即通过。`b85eb56`：push 和 pull_request 两轮都三绿，没有重跑 |
| PR #12 | `mergeable: true`，`mergeable_state: clean`；`main` 没有分支保护 |
| 推送记录 | 都是快进；`main` 和所有 `review/*` 分支未被改动 |

### 更正：被重跑掩盖的 Windows 首轮失败

我上一份复审只用 GitHub 默认的"最新一次尝试"视图看 CI，所以把下面这些 job 都记成了"成功"。按全部尝试（`filter=all`）重新核对，阶段 1.5 的提交上有 5 次首轮失败，重跑后才变绿：

| 提交 | job | 首轮失败的测试 | 报错要点 |
|---|---|---|---|
| `2d50aeb` | windows-python `131306127561` | `test_process_tree.py::TestRealWindowsProcesses::test_stop_owned_kills_the_whole_real_tree` | `pid 628`、`pid 748`：`identity unavailable (_ProcessDenied); not signalled` |
| `b66f367` | windows-python `131359225735` | `test_mcp_auth.py::test_concurrent_adds_never_lose_tokens` | `PermissionError(13, 'Permission denied')` |
| `4d19e0c` | windows `131359528545` | `launcher_lifecycle`：`test_ready_stop_and_private_record` | `pid 6660`：`_ProcessDenied`；`pid 6708: creation does not bind to parent 6648` |
| `c7be1d7` | windows-python `131429993637` | 同第一行 | `pid 648`、`pid 772`：`_ProcessDenied` |
| `c7be1d7` | windows `131429994170` | `launcher_lifecycle`：`test_bad_readiness_and_oversize_are_cleaned` | 清理之后记录文件仍然存在 |

交接文档第 2 节还记录了：本地 Windows 上两次全量 CTest 都是 41/42，失败的都是 `launcher_lifecycle`（两次失败的子测试不同）。以后我核对 CI 时会查看所有尝试。

## 3. 新发现

### CL-25（P2）Windows 上停止进程树偶发"stop not confirmed"：父 PID 被复用的无关进程被算进了树

**证据。** 上表 5 次失败里有 4 次出在 `_win_terminate_tree`（`python/bzs/process_control.py:172-291`）把某些 PID 报为"未确认"。本地两次全量运行失败的也是 `launcher_lifecycle`，很可能是同一原因，但交接文档没有记下报错原文。以 `test_stop_owned_kills_the_whole_real_tree` 为例，这棵树只有三个进程：venv 启动桩、真正的解释器和叶子进程（`python/tests/test_process_tree.py:303-316`），都是测试自己以同一用户启动的普通进程，正常情况下都能打开。报错里的 PID（628/748、648/772）却都打不开，所以它们更可能是被误当成"子进程"的额外条目。

**原因**（根据代码和日志分析；这是 Windows 专有的问题，我在 Linux 上无法复现）：

1. 这棵树是按 Toolhelp32 快照里的父 PID 拼出来的（`process_control.py:216-222`）。在 Windows 上，父进程退出后，子进程记录的父 PID 不会更新，而 PID 会被复用。所以任何一个"父进程已经不在"的进程，只要它那个已死父进程的 PID 恰好等于我们根进程的 PID，就会出现在快照里，看起来像根进程的子进程。开机时就有现成的例子：每个会话的 smss.exe 实例启动 csrss.exe 和 wininit.exe（或 winlogon.exe）之后就退出，这些系统进程的父进程从此不存在。CI 每次都是刚开机的虚拟机，628/748 这样的小 PID 很像这类进程。
2. 这样的条目一律被记成"未确认"。打不开时记为 `identity unavailable`（`:245-246`）；能打开时，它的创建时间早于根进程，记为 `creation does not bind`（`:239-241`）。无论哪种，`stop_owned` 都会抛出"stop not confirmed; record retained"（`:587-589`），`spawn` 失败后的清理也会保留记录。
3. 打开进程时一次申请了查询、同步和终止三种权限（`:78`、`:131`）。只要有一种被拒绝（例如对 SYSTEM 进程或受保护进程申请终止权限），就连创建时间都读不到，明显无关的进程也就没法排除。

**影响。** 这是"安全但误报"：没有误杀任何进程。但在 Windows 上，`stop` 和启动失败后的清理会随机报告失败并保留记录；下一次 `start` 会因为这条记录报"managed root exited; descendant state requires operator review"而拒绝启动（`process_control.py:374-376`），只能人工处理。用户机器上进程多、PID 复用频繁，本地两次全量 CTest 都出了这个问题，所以阶段 3 在用户机器上签收之前必须修好。

**修改**（一个提交 `fix(CL-25): exclude stale-parent processes from the Windows stop tree`）：

1. 诊断：每条未确认记录都带上进程映像名和双方的创建时间。快照结构体里本来就有映像名字段（`:111`），现在读到后被丢弃了。
2. 分两步打开：先只申请 `PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE`，读出创建时间，判断它是否属于这棵树；确认属于之后，在仍持有查询句柄的情况下再按 PID 申请 `PROCESS_TERMINATE`。句柄打开期间 PID 不会被复用，所以再次打开的一定是同一个进程。
3. 创建时间严格早于它名义上的父进程的条目，必然不是这个父进程的后代，直接排除：不发信号，也不计入未确认。如果连查询句柄都拿不到，就用 `NtQuerySystemInformation(SystemProcessInformation)` 读这个 PID 的创建时间再按同样规则判断，这一步不需要打开进程；仍然读不到的，才计入未确认。
4. 其余不变：已确认是成员、但无法终止或无法确认退出的进程，仍然计入未确认；创建时间相等的条目维持现有处理。真正的后代一定在父进程之后创建，所以这条排除规则不会漏掉真正的成员。

**测试。**

- 用可注入的假 API（`_win_terminate_tree` 已有 `api=` 参数）补单测：(a) 父 PID 被复用的旧进程、打开被拒：被排除，停止被确认，从未对它调用终止；(b) 同样的旧进程、可以打开：被排除；(c) 真正的子进程（创建时间更晚）、查询被拒：仍计入未确认；(d) 真正的子进程、终止被拒：仍计入未确认。
- 在用户的 Windows 机器上：`python/tests/test_process_tree.py` 与 `tests/launcher_lifecycle.py` 各连续跑 50 次，0 失败；全量 CTest 连续两次 42/42。

### CL-26（P3）Windows 上 `control_lock` 首次创建锁文件时的写入竞争

**证据。** `b66f367` 的 windows-python 首轮：`test_concurrent_adds_never_lose_tokens` 收到 `PermissionError(13, 'Permission denied')`；交接文档记录本地也出现过一次。

**原因。** `control_lock` 的 Windows 分支（`python/bzs/process_control.py:407-418`）在锁文件为空时先写入一个字节（`:409-410`），再调用 `msvcrt.locking`，而这次写入不在重试范围内。24 个线程同时在一个新锁文件上抢锁时，线程 B 可能刚看到文件大小为 0，线程 A 就已经锁住了第 0 个字节；B 接着写入，撞上 A 的字节锁，Windows 返回锁冲突，Python 报成 `PermissionError(13)`。这个异常直接从 `control_lock` 抛出，没有被重试。Windows 允许锁定文件末尾之后的字节，所以这次写入本来就不需要。仓库里没有任何代码读取锁文件的内容。

**修改**（一个提交 `fix(CL-26): lock without writing to the Windows lock file`）：删除 `:409-410` 这两行。

**测试**（仅 Windows）：先创建一个空的锁文件，用一个 fd 通过 `msvcrt.locking` 锁住第 0 个字节，但不写入；再用另一个 fd 调用 `control_lock(path, timeout_s=0.2)`，必须抛出 `RuntimeError("another launcher operation is running")`，而不是 `PermissionError`。另外在本地把 `test_concurrent_adds_never_lose_tokens` 连续跑 50 次，0 失败。

### 对交接文档第 5 节建议的意见

交接文档建议把这类失败并入阶段 4，与 CL-10 一起处理，办法是测试重试或者把 runner 目录排除出 Defender 扫描。不采纳。证据指向的是上面两处代码，测试重试只会把问题藏起来。如果修完之后仍有失败，再结合 CL-10 的 Windows 测量一起分析。

## 4. 其他说明

- 门槛中"CTest 不少于 42 个用例"指的是不少于 42 个用例**并且全部通过**。CI 上已满足（Linux 42/42，Windows 子集 31/31）；用户机器上的全量 CTest 要等 CL-25 修好后才能满足，这是阶段 2.5 的验收项。
- CL-25 和 CL-26 涉及的代码来自 10 月 27、28 日的批次（`3a9a464`、`7e438ea`、`253b4d1`、`52b8418`），`main` 上还没有这些代码。合并 PR #12 不会让开发线变差，修复会在合并之后通过新的 PR 进入 `main`。

## 5. 决策（按 D17：全部按审核方推荐执行）

D1–D17 不变。本轮新增：

| # | 决定 | 理由 |
|---|---|---|
| D18 | 现在执行阶段 2：合并 PR #12 | D12 的条件已满足（`002c35c`、`b85eb56` 首次尝试即三绿）；CL-25、CL-26 不是阶段 1.x 引入的，在开发线上早已存在；继续推迟合并，99 个提交就一直不在 `main` 上 |
| D19 | CL-26、CL-25 作为阶段 2.5，在阶段 3 之前完成：从合并后的 `main` 开新分支，提一个 PR | 阶段 3 要在用户的 Windows 机器上反复启停服务（S0、`tools/accept_s1.py`），停止不可靠会让签收结果不可信 |
| D20 | CI 重跑规则：失败的 job 可以重跑，但重跑前要把首轮失败的 job 链接和失败的测试名写进交接文档；审核方核对所有尝试。CL-25、CL-26 修好之前，这两类已知特征的失败允许重跑；其他任何失败都要先查明原因 | 重跑本身没问题，不留记录才会掩盖问题；这次我自己也因为只看最后一次尝试而漏看了 |
| D21 | 不采纳测试重试或排除 Defender 扫描来应对这些失败 | 证据指向代码问题；如果修完后仍有失败，再结合 CL-10 分析 |

## 6. 下一步（给本地 agent 的任务单）

规则不变：每个任务一个提交，提交信息以任务 ID 开头，提交说明写明新增测试名和验证结果；只认该提交自己的 CI。

**阶段 2（现在做）**

按上一份复审第 6 节的阶段 2 执行，内容不变：合并 PR #12（merge commit）→ 先留言关闭 #5–#11 → 按清单清理分支（不在 `main` 中的 5 个先打 `archive/` 标签）→ 建立 `docs/CURRENT.md` → 把流程规则写进蓝图。补充三点：

1. `docs/CURRENT.md` 的"下一步"写阶段 2.5（CL-26、CL-25），"待决事项"写"无"，并写进 D20 的重跑规则。
2. 蓝图里的流程规则加上 D20。
3. 合并之后，所有新工作都从 `main` 开新分支，每批一个 PR，CI 绿后合并。

**阶段 2.5（阶段 2 完成后做）**

从 `main` 开分支 `work/windows-stop-20261113`：先 CL-26，再 CL-25，每项一个提交，然后提一个 PR 到 `main`。

验收：两项各自的测试先失败、修改后通过；第 3 节列出的循环测试 0 失败；用户机器上全量 CTest 连续两次 42/42；CI 三绿且不需要重跑。

**阶段 3、4、5**

按上一份复审第 6 节执行，不变。

**明确不做（不变）**：扩展 B-24；新增"证明链"类机制；不必要的 schema 升级；改写 git 历史；启用抓取型来源；伪装成浏览器的 User-Agent；为了让测试通过而删除或放宽断言。

## 7. 本次复审用到的方法（供复核）

- CL-23：在 `b85eb56` 的临时 worktree 里逐项运行上一份复审第 4 节列出的验收命令；摘要核对的做法同上一份复审第 7 节。
- CI：`gh api "repos/wmqfl861/bzs861/commits/<sha>/check-runs?filter=all"` 列出每个提交的全部尝试，再用 `gh api repos/wmqfl861/bzs861/actions/jobs/<job>/logs` 下载失败 job 的日志，查看失败的测试和报错。
- CL-25、CL-26：阅读 `python/bzs/process_control.py`、`python/tests/test_process_tree.py`、`python/tests/test_mcp_auth.py`，对照上面 5 份失败日志中的报错；Windows 行为依据微软文档：进程访问权限（受保护进程禁止的权限列表）和 `LockFile`（允许锁定文件末尾之后的字节）。
