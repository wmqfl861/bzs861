# 复审结论：PR #12（claude-2026-11-12 审核的阶段 0 与阶段 1）

- 复审日期：2026-11-12
- 复审对象：PR #12（草稿），head `6d550cd`（代码头 `0e23363`），base `main` @ `1d8e80e`；本轮新增 9 个提交 `a2e5e4f..6d550cd`
- 依据：审核报告 `review/claude-2026-11-12`（`f821c30`）第 4 节各条"验收"与 `claude-2026-11-12-tasks.json` 的 acceptance；本地 agent 交接文档 `docs/reviews/response-claude-2026-11-12-phase1.md`
- 方式：只读复审。没有合并 PR #12，没有修改任何已有分支（`main`、`work/review-2487934-a`、`review/claude-2026-11-12` 都未改动）。所有验证都在一个临时 worktree（检出 `6d550cd`）里完成
- 存放位置：应用户要求，本文件单独推送在新分支 `review/claude-2026-11-12-phase1` 上（基于 `6d550cd`，只多这一个文件），路径为 `docs/reviews/claude-2026-11-12-phase1-rereview.md`。该分支只读，不要合并它
- **用户决定**：用户已于 2026-11-12 22:56 答复"所有需要作决策的地方，都按照你推荐的做"。D1–D9 按本文第 5 节的最终推荐值执行；其中 D8 根据本次复审增加了一个前置条件

## 1. 结论

阶段 1 的 7 项全部落实，质量高。本地 agent 对 M2、M3 可达性的分析比原审核报告更深入。下面每一项我都独立复现过，结论一致。

需要返工的有 4 项（CL-04 为 P2；CL-12、CL-14、CL-03 为 P3）；另有两项新发现（CL-21 为 P2，CL-22 为 P3）。两项 P2（CL-21、CL-04r）完成并且 CI 全绿后，就可以按 D8 合并 PR #12。

| 任务 | 提交 | 结论 | 一句话理由 |
|---|---|---|---|
| CL-04 | `b1c5b7c` | **rework（P2）** | R2 的 6 行全部被拒绝，参数扫描测试也扎实；但必填的列表参数现在不再接受 JSON 编码的数组字符串（相对 `a2e5e4f` 是兼容回退，见 CL-04r），报错文案也不适用于必填参数 |
| CL-01a | `d1301d7`、`9927b13` | **pass** | 与 `40e2afd`、`352b006` 的 patch-id 一致，保留原作者，两个测试通过 |
| CL-02 | `7a8a1a0` | **pass** | R1 全部 `found=yes`；三条 SQL 的插入位置、绑定顺序和 `partial` 推理都已核对；回归测试对每一层都有判别力 |
| CL-12 | `b188eca` | **rework（P3）** | 两个来源返回同一 URL 且 rank 相同时，输出仍取决于来源顺序（已用反例复现，见 CL-12r） |
| CL-14 | `3a82153` | **rework（P3）** | 4 条后台通道都传 `nullptr`，日志里没有异常类型和消息；缺少一个真实后台通道失败的端到端测试（见 CL-14r） |
| CL-03 | `0476be4` | **pass，附 P3 修正** | M1、M2、M3 独立复跑全部被杀死，并且都是被预期的测试杀死的；但有两处注释与实现不符，其中一处引用了不存在的测试名，变异脚本在 Linux 上会覆盖日志（见 CL-03r） |
| CL-09a | `0e23363` | **pass，附 P2 新发现** | 验收全部满足；但移动后的文件被整篇转成了 CRLF（见 CL-21） |
| 交接文档 | `6d550cd` | **pass** | 内容与我的复现一致；它说"M2 没有完整重跑脚本"，这一项已由本次复审补齐 |

## 2. 独立验证（Linux 容器，2 vCPU；与上一轮相同的锁版依赖）

| 项目 | 结果 |
|---|---|
| Debug 构建 | 通过，只有 1 条已知告警 `tests/cache_freshness.cpp:333` |
| CTest | 42/42 通过，用时 272 秒 |
| pytest | 1,709 通过、5 跳过（Windows 专用用例），与本地 agent 在 Windows 上 1,714 全部通过一致 |
| 套件用例数 | `bzs_tests` 120 个、2,398 条断言；`bzs_session_tests` 25 个、310 条断言，与交接文档一致 |
| R1（CL-02） | `site_partial`（N=8/30，`max_candidates=1/2`，词项和短语）6 行全部 `found=yes partial=false` |
| R2（CL-04） | `mcp_null_scope.py` 6 行全部 `REFUSED` |
| 变异检查 | `tools/mutation_check.sh` 完整运行（3 分 39 秒）：基线 8 个套件全部通过；`M1 KILLED by bzs_tests`、`M2 KILLED by bzs_session_tests`、`M3 KILLED by bzs_session_tests`；跑完后源码已恢复，`git status` 干净 |
| 杀死变异的具体测试（逐个手工复核） | M1 → `contributor_overflow_tightens_capture_ceiling`；M2 → `binding_history_overflow_tightens_capture_ceiling`（仅此一个）；M3 → `legacy_crawl_marker_on_variant_document_tightens_capture`，以及重写后的 `incomplete_contributor_binding_keeps_captured_ceiling_with_legacy_crawl_marker` |
| CI | `0e23363` 和 `6d550cd` 的 linux、windows、windows-python 都是 success（每个提交各有 push 和 pull_request 两轮） |
| 改动范围 | 21 个文件，逐一对应到任务；每个提交只改动本任务的文件 |

## 3. 逐项复核与返工要求

### CL-02（pass）

- 核对了 host 片段的插入位置：三条候选 SQL 里第一个 `" ORDER BY"` 就是结尾那个，子串子查询里没有 `ORDER BY`。绑定顺序 `words/owner, owner, [substr/owner], hosts×4, LIMIT` 与 SQL 文本一致。
- priority 层复用了带 host 条件的 `word_sql`。两层共用 `seen` 和 `candidates`：第二层窗口里最多有 k 行已见过，剩余容量是 `max_candidates−k`，所以 `LIMIT max_candidates+1` 仍能保证"截断必报 `partial`"。
- `content_fts` 每篇文档只有一行（`src/store.cpp:723,756`，先删后插），窗口不会被重复行占满。
- 回归测试有判别力：如果回退层的 SQL 漏了 host 条件，其他站点的行会进入窗口，使 `partial` 变成 true，断言就会失败。
- 交接文档第 7 节第 2 条（有界扫描路径不能与 `site:` 组合，所以改为锁定语法边界）：同意。

### CL-03（pass）→ CL-03r（P3）

同意交接文档第 5 节对 M2 判别性构造的分析：25 个主机名大小写变体在 `documents` 里是 25 行，`url_key` 相同，所以只会触发 `records>24`；它们的发布身份相同，不会碰到身份数 `>24` 的子句。也同意 M3 的分析。补充一个观察：禁用 M3 时，重写后的 `incomplete_contributor_binding...` 也会失败（`hits.size()==23` 不成立）。这说明一旦标记经 `activate_site_publication` 生效，网页会话分页时**只能**靠这条传播路径带上标记，与交接文档里 `sessions.cpp:414` 的说法一致。

需要修正：

1. `src/sessions.cpp:301-304` 的注释写的是"已删除记录快照上的标记……由 `legacy_crawl_marker_on_deleted_history_tightens_capture` 锁定"。**这个测试不存在**；实际的测试是 `legacy_crawl_marker_on_variant_document_tightens_capture`。而且扫描 SQL 是对 `current_snapshot` 的内连接（`src/sessions.cpp:291`，`JOIN snapshots s ON s.id=d.current_snapshot`），erase 之后 `current_snapshot` 为 NULL，已删除记录根本进不了扫描。这正是交接文档第 5 节自己得出的结论。请把注释改为"同一 `url_key` 下存活的变体文档"场景，并写对测试名。
2. `src/sessions.cpp:293-295` 写"扫描也计入同一 `url_key` 下的已删除历史"，理由同上，不成立。改为"同一 `url_key` 下的多个存活文档（例如主机名大小写变体）"。
3. （可选）`incomplete_contributor_binding_keeps_captured_ceiling_with_legacy_crawl_marker` 现在测的是"已存快照上的旧爬取标记恰好挡住这一个 URL"，和"贡献者绑定不完整"已经无关。建议改名，例如 `legacy_crawl_marker_on_stored_snapshot_blocks_exactly_that_url`。
4. `tools/mutation_check.sh:32`：在单配置生成器（Linux/Ninja）下，`"$BUILD/$t"` 失败（也就是变异被杀死）后，会接着执行不存在的 `"$BUILD/Debug/$t"`，把真正的失败日志覆盖掉。我复跑时，三个被杀死套件的日志都只剩一行 `No such file or directory`。请先判断可执行文件在哪个路径，只运行一次。另外，基线有套件失败时应 `exit 1`（`:40` 目前只打印结果）；建议每个套件加超时（例如 `timeout 600`）。

验收：两处注释与实现一致，引用的测试名都存在（`grep` 能找到）；在 Linux 上运行 `tools/mutation_check.sh`，被杀死套件的日志里能看到 `FAIL <测试名>`；人为让基线失败时，脚本以非零码退出。

### CL-04（rework）→ CL-04r（P2）

R2 的 6 行全部被拒绝，参数扫描测试覆盖了 25 个工具，未知字段一律拒绝，schema 也去掉了 null 类型。这些都通过。交接文档第 7 节第 1 条（显式 `null` 收紧，`test_mcp_direct_cancel.py` 原来的 `[None]` 用例改为期望拒绝）：同意。这与 `crawl_site` 原有的守卫一致，不算删断言换绿灯。

问题出在 MCP SDK 的预解析上。当参数类型不是 `str`、而值是字符串时，SDK 会做 `json.loads`，并在结果是列表、字典、None 或布尔值时替换原值。SDK 源码注释专门写了某些桌面客户端几乎总是把列表当成 JSON 字符串发送（`mcp/server/mcpserver/utilities/func_metadata.py:234-236,254-266`）。新守卫对 `list[str]` 要求 `type(value) is list`（`python/bzs/mcp_server.py:130-133`），于是这类调用全部被拒绝：

| 调用 | `a2e5e4f` | PR #12 |
|---|---|---|
| `read_urls(urls='["https://a.example/"]')` | 转发，`urls=["https://a.example/"]` | **拒绝**：`invalid JSON type for urls; omit it to use its default` |
| `map_site(seeds='["https://a.example/docs/"]')` | 转发，`seeds=[...]` | **拒绝**（同上） |
| `read_urls(urls=["https://a.example/"])` | 转发 | 转发 |

把字符串解码成字符串数组，结果正好就是调用方的本意，不会放宽任何范围或预算。CL-04 要防的只是 `"null"`→None、`true`→1、丢弃未知字段这三类改写。所以拒绝数组字符串不带来安全收益，却破坏了现有客户端。另外，`urls`、`seeds` 是必填参数，"omit it to use its default"（省略即用默认值）这句提示对它们是错的。

修改：

1. 对 `list[str]` 参数：值是 `str` 时先 `json.loads`；只有结果是元素全为 `str` 的列表时才接受，并把**解码后的列表**传给 `super().call_tool`；`'["a",1]'`、`'"x"'`、`'null'`、`'{}'` 一律拒绝。
2. 报错文案：必填参数用 "must be a JSON array of strings" 或 "invalid JSON type for {key}"，不要写 "omit it"；可选参数保留现有文案。
3. 参数扫描测试补两类用例：所有 `list[str]` 参数接受数组字符串、并以列表形式转发给核心；上面列出的几种非法字符串都被拒绝。

验收：上表前两行在修改后都"转发且为列表"；新用例覆盖 `list_tools()` 里全部 `list[str]` 参数；R2 的 6 行仍然全部被拒绝。

### CL-12（rework）→ CL-12r（P3）

反例（两个来源对同一 URL 都给 rank 1，标题和摘要不同）：

```
order x,y -> title=title from x | snippet=snippet from x
order y,x -> title=title from y | snippet=snippet from y
canonical outputs identical: NO  <-- order-dependent
```

原因：供体键是 `(rank, url)`（`src/web.cpp:340`），相同 URL 加相同 rank 时无法分出先后，于是先到的胜出。这违反了本提交写进 `docs/API.md` 的合同（"胜者按（rank, URL）确定，与来源到达顺序无关"）。现有排列测试里平手的两条命中用的是不同的 URL 字符串（大写与小写），所以没有覆盖这种情形。在实际运行中，同步路径按准入顺序处理、会话路径按 `ORDER BY position` 处理，结果是确定的；但用户换一下 `sources` 的传入顺序，标题和摘要就会变。

修改：把供体键扩展为全序 `(rank, url, source_id, title, snippet)`，同步更新 `docs/API.md` 的措辞，并把这个反例加进 `fuse_output_is_independent_of_source_order`（同一 URL、同一 rank、两个来源，两种顺序输出逐字节一致）。

交接文档第 7 节第 3 条（等 rank 时按字典序，大写在前）：可以接受。可选的改进是，等 rank 时优先展示规范形式（小写主机、无默认端口），这不是必须的。

### CL-14（rework）→ CL-14r（P3）

searchd 那两处传入了真实的异常对象，没有问题。但 4 条后台通道（`src/service.cpp:421,424,428,448,449,465,466,479,480`）调用的都是 `log_internal("service.lane_...", nullptr)`。所以最需要诊断信息的地方（任务卡在租约里、失败状态写不进去），日志里恒为 `"type":"unknown"`，也没有消息。现有测试是直接调用 `log_internal`，没有让一条真实的后台通道失败过。

修改：

1. 增加一个 `log_current_internal(where)`，在 catch 块内调用。实现为 `try { throw; } catch(const std::exception& e) { log_internal(where,&e); } catch(...) { log_internal(where,nullptr); }`，用 `throw;` 重抛当前正在处理的异常，从而取回异常对象。后台通道的 `catch(...)` 都改用它。
2. 新增一个端到端测试：让某条后台通道的一个任务抛出 `std::runtime_error`（可以通过测试钩子或注入失败的 worker），断言捕获到的事件 `where` 正确、`type` 不是 `unknown`、`message` 正确，计数加 1。
3. （可选）截断 `what()` 时按 UTF-8 字符边界截断。

另外，`include/bzs/diagnostics.hpp:8` 声明"永远不包含 URL 和查询"。我检查了现有 `Error` 和 `require` 的消息，没有一处拼接 URL 或查询，所以这条声明目前成立。建议把它写进 `docs/SECURITY.md`，作为约束：今后新增的异常消息不得拼接 URL、查询、正文或令牌。

交接文档第 7 节第 4 条（`typeid().name()` 的输出因平台而异）：同意。

### CL-09a（pass）

`rg -l "class RequestExecutor"` 只命中 `include/bzs/request_executor.hpp`，另外还命中交接文档本身，因为文档里引用了这条命令的原文，不算源码副本。现在已经没有测试引用顶层 `validation/`（CMake 里剩下的 `docs/planning/validation/contract_cases.json` 属于另一个目录）。`e2e_cache_policy_review` 在新位置运行通过。换行符问题见 CL-21。

### CL-01a（pass）

`40e2afd`→`d1301d7`、`352b006`→`9927b13` 的 `git patch-id --stable` 相同，作者和日期都保留，两个测试通过。

## 4. 新发现

### CL-21（P2）PR #12 把两个文件整篇转成了 CRLF

**证据。**

| 文件 | `a2e5e4f` | PR #12 | 忽略换行符后的真实改动 |
|---|---|---|---|
| `tests/sessions.cpp` | 312 行，全部 LF | 433 行，**全部 CRLF**（`0476be4` 引入） | +130/−9（git 原始 diff 显示 +433/−312） |
| `tests/probe_http_policy.py` | 199 行，全部 LF（原路径在 validation/ 下） | 199 行，**全部 CRLF**（`0e23363` 引入） | 只有 1 行（`parents[3]`→`parents[1]`） |

另外，`a2e5e4f` 之前就有两个 CRLF 文件：`python/tests/test_launcher_boundaries.py`、`python/tests/test_worker_rpc_boundary.py`。仓库的 `.editorconfig` 规定 `end_of_line = lf`，但 `.gitattributes` 没有任何换行规范化规则，仓库里也没有需要 CRLF 的 Windows 脚本（`.ps1`、`.bat`、`.cmd` 都没有）。

**影响。** GitHub 上 PR #12 的 CL-03 diff 显示为整个文件重写，审阅者必须加 `--ignore-cr-at-eol` 才能看出真实改动；`tests/sessions.cpp` 的 blame 历史全部指向 `0476be4`；只要 agent 在 Windows 上编辑文件，就还会再次发生。

**修改**（不改写历史，单独提交一个 `chore(CL-21): normalize line endings to LF`）：

1. `.gitattributes` 增加 `* text=auto eol=lf`，并显式标注二进制文件，例如 `*.xlsx binary`、`*.png binary`。`docs/planning/development_plan_v1.1.xlsx` 本来就含 `\r` 字节，不能被当成文本处理。
2. 运行 `git add --renormalize .` 后提交。这个提交在 `git diff --ignore-cr-at-eol` 下应当是空的。
3. CI 增加检查：`git ls-files --eol | awk '$1=="i/crlf"'` 输出不为空就失败。
4. 本地 Windows 工作树设置 `git config core.autocrlf false`（或 `input`）。

**验收。** `git ls-files --eol | grep -c "i/crlf"` 为 0；规范化提交在 `--ignore-cr-at-eol` 下没有任何改动；CI 新增的检查通过。

### CL-22（P3）A4/R-04（aiohttp 解压缩资源边界）的判定：现在的锁定版本已有保护，补版本下限和回归测试

本地 agent 在交接文档第 7 节第 7 条问这项要不要处理。我的判定：

- 当前锁定的 aiohttp 3.14.5 已经内置有界的流式解压。`DeflateBuffer.feed_data` 每次按 `max_length` 限制输出，剩余数据留在解压器里，读取方消费后才继续；`feed_eof` 里还有防解压炸弹的断言（`aiohttp/http_parser.py:1231-1270`）。项目这边 `fetch.py:253` 使用 `auto_decompress=True`，读取上限（`:312`，`:466,476`）按解压后的字节计算。所以在锁定版本下，资源边界已经成立，不需要在阶段 1.5 返工。
- 但 `python/pyproject.toml:11` 允许 `aiohttp>=3.9`。不按锁文件安装的环境可能装到没有这项保护的旧版本。

修改：把 `pyproject.toml` 的下限提高到锁定版本，或者提高到经核实的、首个带有界解压的版本；再加一个回归测试：回环服务器返回 `Content-Encoding: gzip` 的压缩炸弹（例如把 1 GiB 的零压缩成约 1 MiB），断言 fetch 在 `max_bytes` 处终止，并且内存增长有上限（用 `tracemalloc` 测得增长小于 64 MiB）。放在阶段 4 做，不阻塞合并。

## 5. D1–D9：用户已决定，全部按审核方推荐执行

下表是本次复审后的最终推荐值，也就是用户的决定。和原报告第 7 节相比，只有 D8 增加了前置条件。

| # | 决定（最终） | 本次复审的补充 |
|---|---|---|
| D1 | 来源：marginalia + mwmbl（两个独立的通用网页 API 来源）+ wikipedia_en / wikipedia_zh（补充）。marginalia 由用户发邮件申请免费非商业 key；mwmbl 在 S0 阶段先用匿名额度，之后申请免费 key，**不开启按量付费** | 确认。实施顺序是 CL-20 → CL-07 → CL-06。用户需要做的事：给 marginalia 发邮件申请 key |
| D2 | S2 冻结在当前 partial 状态，S1 签收后再继续 | 确认 |
| D3 | 新批次只入库摘要；不再提交源码快照；已有的 48 MB 不改写历史，用 `.ignore` 和 `.gitattributes` 排除 | 确认。CL-09a 已经做完第 1 步；CL-21 的换行规范化是新增提交，不算改写历史 |
| D4 | S1 期间不做恢复；S2 的 BACKUP-00 做最小离线恢复 | 确认 |
| D5 | 终态后保留 90 天，过期的键重放返回 410 | 确认 |
| D6 | 抓取型适配器继续默认禁用；S1 不启用 ddg、bing 等 | 确认 |
| D7 | 维持现有的原始子串语义，S1 不改语义 | 确认 |
| D8 | 把开发线合并进 `main`：**直接合并 PR #12**（它的 base 已经是 `main`，包含全部 91 个提交）。用 merge commit，不 squash，不改写历史 | **新增前置条件**：先完成阶段 1.5 的 CL-21 和 CL-04r，并且该提交自己的 CI 三绿；CL-03r、CL-12r、CL-14r 建议同一批完成，最迟必须在阶段 3 开始前完成 |
| D9 | 合并后删除已并入 `main` 的 `work/*` 分支；`review/*` 保留只读；孤立分支逐个核对后处理 | 确认。具体做法：合并后用 `git branch -r --merged origin/main` 列出可删的分支，在交接文档里列清单后再删；PR #10 关闭时注明"两个测试已作为 `d1301d7`、`9927b13` 合入，其余是文档和 validation 记录，已被开发线覆盖"；`blueprint-next-ten`（多 1 个文档提交）和 `recovery-transfer`（只有一个标记提交）核对后删除 |

## 6. 下一步（给本地 agent 的任务单）

每个任务一个提交，提交信息以任务 ID 开头；先写复现测试，确认修改前失败，再修改；只认该提交自己的 CI。

**阶段 1.5（合并前）**

1. CL-21：换行规范化，加 CI 检查（必须先做，后面的提交才能看出真实 diff）
2. CL-04r：列表参数接受可解码的数组字符串；修正必填参数的报错文案；补扫描用例
3. CL-03r：改正 `src/sessions.cpp:293-295,301-304` 两处注释；修复 `tools/mutation_check.sh` 的日志覆盖和基线退出码；（可选）给测试改名
4. CL-12r：供体键改为全序，补反例测试，更新 `docs/API.md`
5. CL-14r：用 `log_current_internal` 取回异常对象，补端到端测试

门槛：CTest 不少于 42 个用例；pytest 通过数不少于 1,709（Linux）或 1,714（Windows）；`tools/mutation_check.sh` 的 M1–M3 全部 KILLED；R1、R2 的结果不回退。

**阶段 2（D8、D9 已同意）**

1. 在 PR #12 的描述里附上阶段 1.5 的结果，把它转为 ready，用 merge commit 合并进 `main`。
2. 关闭 #5–#9、#11（注明"已通过 #12 合入"）和 #10（注明处理情况，见第 5 节 D9）。
3. 列出待删分支清单，然后删除。
4. 建立唯一入口 `docs/CURRENT.md`（CL-05），把原报告第 6 节的流程规则写进蓝图。

**阶段 3（D1、D2、D6 已同意）**

CL-20（显式 TLS）→ CL-07（诚实的 User-Agent）→ CL-06（`mwmbl_api` 适配器和离线夹具测试；marginalia、mwmbl、wikipedia 三份审核记录）→ 给用户一条可直接复制的 S0 命令 → `tools/accept_s1.py`。

**阶段 4、5**

按原报告第 8 节执行，另外加入 CL-22。

## 7. 本次复审用到的探针（供复核）

`cx_fuse_tie.cpp`（CL-12 反例）：从 `tests/unit.cpp:185-221` 复制 `reviewed_sources`，构造两个来源 `srcx`、`srcy`，各返回 `{"https://same.example/a", "title from x|y", "snippet from x|y", 1}`，按两种顺序调用 `fuse(...,10,60,true)`，比较两次的 `canonical()`。构建：`g++ -std=c++20 -I include cx_fuse_tie.cpp <build>/libbzs_core.a -lsqlite3 -lcrypto -lxml2 -ljson-c -lpthread`。

`mcp_list_string.py`（CL-04 兼容性）：与 R2 相同，把 `_call` 替换成记录函数，依次调用 `read_urls(urls='["https://a.example/"]')`、`map_site(seeds='["https://a.example/docs/"]')`、`read_urls(urls=[...])`；分别用 `PYTHONPATH` 指向 `a2e5e4f` 和 PR #12 的 `python/` 目录运行，对比结果。

手工确认是哪条测试杀死了变异：对每个变异，用 `tools/mutation_check.sh` 里同样的 `sed` 改写 `src/sessions.cpp`，只构建并运行杀死它的那个套件（M1 对应 `bzs_tests`，M2 和 M3 对应 `bzs_session_tests`），查看 `FAIL <测试名>`，最后 `git checkout -- src/sessions.cpp`。

换行符检查：`git show <commit>:<file> | grep -c $'\r$'`，以及 `git diff --stat --ignore-cr-at-eol a2e5e4f 6d550cd -- tests/sessions.cpp`。
