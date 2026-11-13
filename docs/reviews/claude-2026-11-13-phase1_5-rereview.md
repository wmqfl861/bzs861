# 第二次复审：PR #12 阶段 1.5（claude-2026-11-12 审核）

- 复审日期：2026-11-13
- 复审对象：PR #12（草稿），head `c7be1d7`，base `main` @ `1d8e80e`；本轮新增 6 个提交 `6d550cd..c7be1d7`
- 依据：上一份复审 `docs/reviews/claude-2026-11-12-phase1-rereview.md`（已由 `2d50aeb` 原样入库）第 3、4、6 节的修改要求和验收；6 个提交的说明；各提交自己的 CI；PR #12 的描述。阶段 1.5 还没有交接文档，旧的 `docs/reviews/response-claude-2026-11-12-phase1.md` 未改动
- 方式：只读复审。没有合并 PR #12，没有修改任何已有分支。所有验证都在临时 worktree（检出 `c7be1d7`）里完成
- 存放位置：本文件单独推送在新分支 `review/claude-2026-11-13-phase1_5` 上（基于 `c7be1d7`，只多这一个文件），路径为 `docs/reviews/claude-2026-11-13-phase1_5-rereview.md`。该分支只读，不要合并它
- **用户决定**：用户于 2026-11-13 07:38 答复"所有需要我决策的，都按你推荐的做，以后默认都是这样"。所以第 5 节的推荐就是用户的决定；以后各轮也这样处理（长期规则见 D17）

## 1. 结论

阶段 1.5 的 5 项返工全部通过，每一项我都独立复现过。

新发现一个 P2（CL-23）：CL-21 的换行规范化顺带改写了 `validation/` 下 7 个归档证据文件的字节，其中 5 个文件的 SHA-256 被归档清单引用，现在对不上了。原因出在我上一轮给 CL-21 的做法：我让执行 `git add --renormalize .`，但没有把 `validation/` 排除在外。这是我的疏漏，本地 agent 是照做的。

修复只需要一个小提交，做法我已在临时 worktree 里验证过。CL-23 修好、补上交接文档，这两个提交各自的 CI 三绿后，就合并 PR #12（D12）。

| 任务 | 提交 | 结论 | 一句话理由 |
|---|---|---|---|
| CL-21 | `2d50aeb` | **pass，但引出 CL-23** | 索引里的 CRLF 文件从 11 个降到 0；忽略换行后只有 3 个文件有改动；CI 检查生效。但归档证据也被规范化了 |
| CL-04r | `fe71564` | **pass** | 数组字符串解码后以列表转发，非法字符串全部拒绝；必填参数不再提示"omit it"；R2 仍然 6 行全部拒绝；新测试在旧代码上有 5 个失败 |
| CL-03r | `b66f367`、`c7be1d7` | **pass** | 两处注释与实现一致，引用的测试名存在；脚本每个套件只跑一次，基线失败即退出，有超时；完整运行时 M1–M3 全部被杀死，并打印出杀死它的测试名 |
| CL-12r | `5a89460` | **pass** | 我上一轮的反例现在两种顺序输出逐字节相同，并已写进排列测试；`docs/API.md` 已同步 |
| CL-14r | `4d19e0c` | **pass** | 后台通道改用 `log_current_internal`；新测试让真实的读取通道失败，断言 where、type、message 和计数；`docs/SECURITY.md` 写入了约束 |
| 复审记录入库 | `2d50aeb` | **pass** | 与只读分支上的文件逐字节相同（blob `df6073e`） |

另有一项 P3 流程问题（CL-24）：三个中间提交的 Linux CI 是红的，两个提交没有说明正文，阶段 1.5 还缺交接文档。代码不用返工，按第 6 节补记录即可。

## 2. 独立验证（Linux 容器，2 vCPU；与前两轮相同的锁版依赖）

| 项目 | 结果 |
|---|---|
| Debug 构建 | 通过，仍只有 1 条已知告警 `tests/cache_freshness.cpp:333` |
| CTest | 42/42 通过，用时 337 秒 |
| pytest | 1,718 通过、5 跳过（Windows 专用用例）。CI 的 windows-python 为 1,723 通过，两边一致，比上一轮多 9 个用例 |
| R1（CL-02） | `site_partial` 8 行（N=8/30 × `max_candidates`=1/2 × 词项/短语）全部 `found=yes partial=false` |
| R2（CL-04） | `mcp_null_scope.py` 6 行全部 `REFUSED` |
| CL-04r 兼容性 | `mcp_list_string.py`：`read_urls(urls='["https://a.example/"]')` 和 `map_site(seeds='["https://a.example/docs/"]')` 在 `6d550cd` 被拒绝，在 `c7be1d7` 以列表转发；`urls="https://a.example/"`（不是 JSON）仍被拒绝，报错为 `urls must be a JSON array of strings`，不再提示省略 |
| CL-04r 测试判别力 | 新的 `test_mcp_argument_sweep.py` 配 `6d550cd` 的代码：5 失败、8 通过；配新代码：13 个全部通过 |
| CL-12r | `cx_fuse_tie`：`6d550cd` 两种来源顺序给出不同的标题和摘要；`c7be1d7` 两种顺序逐字节相同 |
| CL-12r、CL-14r 测试判别力 | 把修复临时还原后，对应的新测试失败；恢复后通过（见第 3 节） |
| 套件用例数 | `bzs_tests` 122 个、2,439 条断言（上一轮 120 个、2,398 条）；`bzs_background_pool_tests` 7 项检查（上一轮 5 项） |
| 变异检查 | `tools/mutation_check.sh` 完整运行（5 分 15 秒）：基线通过；M1 被 `bzs_tests` 的 `contributor_overflow_tightens_capture_ceiling` 杀死（表现为异常 `Missing field: captured_mode`）；M2 被 `bzs_session_tests` 的 `binding_history_overflow_tightens_capture_ceiling` 杀死；M3 被 `bzs_session_tests` 的 `legacy_crawl_marker_on_stored_snapshot_blocks_exactly_that_url` 和 `legacy_crawl_marker_on_variant_document_tightens_capture` 杀死。跑完后源码和构建都已恢复，`git status` 干净。与 CI（`c7be1d7` 的 linux job）结果一致 |
| 推送记录 | 6 次推送全部是快进，没有强推；`main` 和所有 `review/*` 分支未被改动 |

各提交自己的 CI（每个提交都有 push 和 pull_request 两轮，结果相同）：

| 提交 | linux | windows | windows-python |
|---|---|---|---|
| `2d50aeb` CL-21 | 成功 | 成功 | 成功 |
| `fe71564` CL-04r | 成功 | 成功 | 成功 |
| `b66f367` CL-03r | **失败**（变异门禁误判，见 CL-24） | 成功 | 成功 |
| `5a89460` CL-12r | **失败**（同上） | 成功 | 成功 |
| `4d19e0c` CL-14r | **失败**（同上） | 成功 | 成功 |
| `c7be1d7` CL-03r | 成功 | 成功 | 成功 |

## 3. 逐项复核

### CL-21（pass，引出 CL-23）

- `git ls-files --eol`：`i/crlf` 为 0（之前是 11）；`i/-text` 有 6 个，都是二进制文件，或内容被 git 识别为二进制的日志。
- `git diff --ignore-cr-at-eol --stat 6d550cd 2d50aeb` 只列出 `.gitattributes`、`ci.yml` 和复审记录 3 个文件，其余 11 个文件只改了换行符。
- CI 新步骤放在 linux job 最前面（`.github/workflows/ci.yml:14-19`），出现 `i/crlf` 就失败。
- 问题：这 11 个文件里有 7 个在 `validation/` 下，是归档证据，见 CL-23。

### CL-04r（pass）

- 实现（`python/bzs/mcp_server.py:134-148`）：`list[str]` 参数收到字符串时先 `json.loads`，结果是元素全为字符串的列表才接受，并且转发的是解码后的列表；`ValueError` 和 `RecursionError` 都转成 `E_SCHEMA`。
- 提示文案改为按签名默认值决定（`python/bzs/mcp_server.py:119`）：必填参数不再出现"omit it"，只有带默认值的参数才有。字符串 `"null"` 的守卫仍按"类型可空"判断，两者分开了。
- 测试覆盖：遍历 `list_tools()` 中全部 5 个数组参数，每个都测数组字符串、空数组字符串和 11 种非法字符串，每次拒绝都断言没有转发给核心；31 个必填参数和 57 个带默认值参数的提示文案；嵌套深度 2000 和 10000 的输入。
- 另外三个测试文件里，原来"数组字符串应被拒绝"的用例改成了 `"[1]"` 这类仍然非法的值，并新增了"解码后转发"的用例。这是上一轮要求的改动，不算放宽断言。

### CL-03r（pass）

- 注释（`src/sessions.cpp:293-296,302-305`）现在写的是"同一 `url_key` 下存活的变体文档"；引用的 `legacy_crawl_marker_on_variant_document_tightens_capture` 存在（`tests/sessions.cpp:360`）。旧测试已改名为 `legacy_crawl_marker_on_stored_snapshot_blocks_exactly_that_url`，内容没变。
- `tools/mutation_check.sh` 逐行核对过：每个套件的可执行文件在基线构建后只解析一次、只运行一次，失败日志不会再被覆盖；基线失败直接 `exit 1`；每个套件都有超时；原文件备份在原子创建的目录里（`tools/mutation_check.sh:41`），任何退出路径（包括 HUP、INT、TERM）都会恢复源码并重建受影响的目标；有变异存活或改写模式没有命中，门禁都会失败。
- `c7be1d7` 放宽了"被杀死"的判定（`tools/mutation_check.sh:119-122`）：退出码为 1 且至少有一行 `FAIL <测试名>: ...` 就算杀死，不再要求出现 `check failed:`。理由成立：M1 确实是被测试杀死的，只是表现为测试里的 `at()` 抛出异常（`Missing field: captured_mode`），而原生测试程序对断言失败和未捕获异常都打印 `FAIL` 行。崩溃、超时、退出码不为 1、或者没有 `FAIL` 行，仍然按工具错误处理，这是从严的一侧。
- `tools/test_mutation_check.py` 用小的替身程序对脚本做黑盒测试（21 个用例）；CI 先跑它，再跑真实的变异检查。
- 可选改进：`tests/unit.cpp:1124` 可以写成 `CHECK(overflow.has("captured_mode")&&overflow.at("captured_mode").num()==2)`（第 1128 行同理）。这样 M1 被杀死时表现为断言失败，同一测试里后面的检查也能继续执行。不阻塞。

### CL-12r（pass）

- 供体比较改为 `std::tie(rank, url, source_id, title, snippet)` 的全序（`src/web.cpp:340-341`）。我核对了 `fuse()` 的其余部分：分数按 `std::map` 中的索引族顺序累加，来源列表按（rank, source_id）排序，组与组之间按去重键定序，都与到达顺序无关。
- `fuse_output_is_independent_of_source_order`（`tests/unit.cpp:1440`）加入了我上一轮的反例（`tied.example/d`），对来源顺序和两个来源内部的命中顺序都做了排列（共 8 种）。另有两个同一来源内平手的测试，对 3 条命中的全部 6 种排列逐字节比较。
- 判别力：把比较临时还原为 `(rank, url)` 后，`bzs_tests` 中 `fuse_output_is_independent_of_source_order`、`fuse_same_source_ties_use_title_before_snippet`、`fuse_same_source_and_title_ties_use_snippet` 三个测试都失败；恢复后通过。
- 说明：按这个全序，完全平手时空标题会胜过非空标题（空字符串最小）。这是我上一轮指定的规则直接带来的结果，只在同一 URL、同一 rank 时才会出现。决定不改，见 D16。

### CL-14r（pass）

- `log_current_internal`（`src/diagnostics.cpp:32-40`）按上一轮给的写法实现，声明为 `noexcept`；头文件注明只能在 catch 块里调用。`src/service.cpp` 的 9 处调用都在 `catch(...)` 块内。
- 新测试 `read_lane_internal_diagnostics`（`tests/background_pool.cpp:65`）利用 Store 原有的可注入时钟，让读取通道在写入失败状态时抛出异常，分别测标准异常和非标准异常（`throw 14`）。断言 where 为 `service.lane_read`、type 不是 unknown、message 正确、计数恰好加 1，并且未知用量不退还、不产生回执。
- 判别力：把读取通道的调用（`src/service.cpp:448`）临时还原为 `log_internal("service.lane_read",nullptr)` 后，`bzs_background_pool_tests` 在标准异常那一轮失败于 `event.at("type").str()!="unknown"`；恢复后通过。
- `docs/SECURITY.md:16` 新增了"异常与内部诊断"一节，约束今后的异常消息。
- 上一轮的可选项"按 UTF-8 字符边界截断"没有做。现有异常消息都是 ASCII，影响很小，放到阶段 4。

## 4. 新发现

### CL-23（P2）CL-21 的换行规范化改写了归档证据，5 个文件的引用摘要对不上了

**证据。** CL-21 规范化的 11 个文件中，有 7 个是 `validation/` 下的归档证据：

| 文件 | `6d550cd` 的 SHA-256（前 12 位） | 引用这个摘要的文件数 | `c7be1d7` 的 SHA-256（前 12 位） |
|---|---|---|---|
| `validation/direct_shared_20261024/published-baseline/independent-windows-job-121060890088.log` | `45150378f34c` | 3 | `95b65a833e9c` |
| `validation/s2_next_five/evidence/json-race-ci-c14/ctest-results-excerpt.log` | `612a95b6406d` | 13 | `156d1b336d97` |
| `validation/s2_next_five/evidence/json-race-ci-c14/job-119635050132.log` | `459c536f3099` | 13 | `d6f67301c101` |
| `validation/s2_next_five/evidence/json-race-ci-c14/sessions.log` | `36bdc2b110a1` | 13 | `a690770f0a51` |
| `validation/s2_next_five/evidence/windows-ci-initial/job-119625336688.log` | `80e539ab1817` | 13 | `91645b1e9d21` |
| `validation/sbom_20261029/sbom-report.json` | `0f47084d4b39` | 0 | `b45412478474` |
| `validation/windows11_takeover_20261028/second-round-20261028.json` | `43fa2d199106` | 0 | `af9bce52502d` |

新摘要在仓库里一处引用都没有。我用直接引用这些文件的 5 份清单（`published-baseline/manifest.json`、`json-race-ci-c14/archive-text-files.json`、`json-race-ci-c14/review.json`、`s2_next_five/ci-followup-evidence.json`、`s2_next_five/json-race-evidence.json`）逐条核对：在 `6d550cd` 上，139 条引用全部吻合；在 `c7be1d7` 上，只有 128 条吻合，不符的 11 条引用全部指向上表前 5 个文件。

这些证据自己也写明了不能规范化。`validation/s2_next_five/evidence/json-race-ci-c14/REVIEW.md:10` 写的是 "saved as UTF-8 bytes with original CRLF preserved. Copy it as bytes; do not normalize its line endings"；同目录 `archive-text-files.json:4` 的 `copy_mode` 写的是 "Byte-preserving copy; raw and excerpt logs retain CRLF"。

**原因。** 我上一轮给 CL-21 的做法是"运行 `git add --renormalize .`"，没有排除 `validation/`。本地 agent 照做了。

**影响。** 不影响任何代码和测试。但合并以后，`main` 上这些证据的字节会和它们自己的摘要清单不一致，按清单核验一定失败。原始字节还在历史里（`6d550cd` 及更早的提交），可以无损恢复。

**修改**（一个提交，例如 `chore(CL-23): restore archived evidence bytes; keep validation/ byte-preserved`，不改写历史）：

1. 在 `.gitattributes` 的 `validation/** linguist-generated=true` 之后加三行。`-text` 会取消 `validation/` 下文件的文本属性，git 以后不再对它们做任何换行转换：

   ```
   # CL-23: archived evidence is byte-preserved (cited SHA-256 digests cover
   # the original CRLF bytes); never convert line endings under validation/.
   validation/** -text
   ```

2. 改好 `.gitattributes` 之后，把上表 7 个文件恢复成 `6d550cd` 的字节：`git checkout 6d550cd -- <7 个文件的路径>`。这条命令直接从历史取出原始字节，与所用的 shell 无关。不要在 PowerShell 里用 `git show ... > 文件` 的写法，PowerShell 的重定向会改动编码和换行。
3. 把 `.github/workflows/ci.yml:16` 的检查改为只放过显式标为 `-text` 的文件，并顺带拦住混合换行：

   ```
   bad=$(git ls-files --eol | awk '($1=="i/crlf"||$1=="i/mixed") && $3!="attr/-text"')
   ```

4. 不要为此新增证据校验工具或 CI 步骤。那属于"证明链"类机制，原报告列为明确不做。

**验收**（我已在临时 worktree 里按上面的做法提交过，没有推送，以下各项全部满足；另外确认了改后的检查仍能拦住 `validation/` 以外的 CRLF 和混合换行文件）：

- `git ls-files --eol -- <7 个文件>` 都显示 `i/crlf w/crlf attr/-text`；
- `git diff --stat 6d550cd HEAD -- <7 个文件>` 为空，也就是字节和原始文件完全相同（`6d550cd` 上 139 条引用全部吻合，所以恢复后同样吻合）；
- `git diff --ignore-cr-at-eol --stat c7be1d7 HEAD` 只列出 `.gitattributes` 和 `.github/workflows/ci.yml`；
- 再运行一次 `git add --renormalize .`，不产生任何改动；
- 改后的 CI 检查输出为空，并且该提交自己的 CI 三绿。

### CL-24（P3，流程）中间提交的 CI 变红；两个提交没有说明；缺阶段 1.5 交接文档

1. `b66f367`、`5a89460`、`4d19e0c` 三个提交的 Linux CI 都失败在同一步：CTest 42/42 通过、脚本自测通过之后，变异检查把 M1 的真实杀死误判成"工具错误"（`harness failure (exit 1, not an assertion kill)`）。`c7be1d7` 修正了判定。不改写历史；在 PR 描述和交接文档里写明这三个提交变红的原因和 run 链接（`43268736001`、`43268779067`、`43268821763`、`43269181028`），并注明这几个提交上 CTest 都是通过的。
2. 教训写进蓝图：新增或修改 CI 门禁的提交，推送前先在真实代码树上完整跑一次这个门禁。`b66f367` 只用替身程序测过判定逻辑，没在真实代码树上跑过，所以没发现 M1 是以异常的形式被杀死的。
3. `5a89460`（CL-12r）和 `4d19e0c`（CL-14r）的提交说明只有标题，而原报告第 8 节要求每个提交附上新增测试名、测试结果和简短说明。不改写历史，在交接文档里补上这两项各自的"修改前失败"记录和新增测试名。
4. 阶段 1.5 还没有交接文档，PR #12 的描述也还停留在阶段 1。合并前补齐（见第 6 节）。

### 其他观察（不需要处理）

- `fe71564` 之后，数字字符串（例如 `limit="5"`）按类型错误拒绝，而 `a2e5e4f` 会经 pydantic 的宽松模式把它转成 5。这是 CL-04 有意的收紧，报错清楚；SDK 的注释也只提到客户端会把数组当字符串发送。维持现状。
- CI 的 linux job 因为变异检查多了约 6 分钟，可以接受。

## 5. 决策（用户已授权：全部按审核方推荐执行）

D1–D9 维持上一份复审第 5 节的最终值，没有变化。本轮新增：

| # | 决定 | 理由 |
|---|---|---|
| D10 | CL-23 在合并前修复（阶段 1.6），做法见第 4 节 | 问题是本 PR 引入的，修复只要一个小提交；合并前修好，`main` 上就不会出现与摘要不符的证据 |
| D11 | 三个中间红提交不改写历史，只在 PR 描述和交接文档里说明 | 变红只因为门禁的判定，每个提交上 CTest 都通过；改写已推送的历史代价更大 |
| D12 | 合并 PR #12 的条件：CL-23 提交和随后的交接文档提交，各自的 CI 三绿。D8 原有的前置条件（CL-21、CL-04r 完成且各自 CI 三绿）已经满足 | 合并时的 head 必须是绿的；这两个提交是合并前的最后两个提交 |
| D13 | 分支和 PR 的清理按第 6 节阶段 2 的清单逐个执行；不在 `main` 中的分支，删除前先打 `archive/` 标签 | D9 只定了原则，这里定到每个分支；打标签后，这些提交以后仍能找到 |
| D14 | CL-22：`python/pyproject.toml` 的下限从 `aiohttp>=3.9,<4` 改为 `aiohttp>=3.13.3,<4`。回归测试在锁定版本下运行，fetch 在 `max_bytes` 处截断、或者干净地返回解压错误，两种结局都接受，但内存增长必须小于 64 MiB | 3.13.3 是首个修复 CVE-2025-69223（GHSA-6mq8-rvhq-8wgg，解压炸弹）的版本。我比对过 3.13.2 和 3.13.3 的 wheel：3.13.3 的 `DeflateBuffer` 每次调用最多解压 32 MiB（默认值），超出即报错；锁定的 3.14.5 进一步改成了有界的流式解压。两者都有上界，但超限时的表现不同，所以测试按"有界且能终止"来断言 |
| D15 | S0 时如果用户自己的 marginalia key 还没到，可以先用官方共享 key `public` 做探测，并在报告里注明；S1 签收（`tools/accept_s1.py`）必须使用用户自己的 key。mwmbl 在 S0 使用匿名额度 | 不让 S0 卡在等邮件上；共享 key 经常被限流，不能作为签收依据 |
| D16 | CL-12 完全平手时的取舍不改，维持（rank, url, source_id, title, snippet）全序 | 只在同一 URL、同一 rank 时出现；改规则要再动一次合同和测试。以后真实结果里出现空标题问题再议 |
| D17 | 长期规则：今后凡是需要用户决定的事项，审核方照常逐项列出推荐和理由，推荐值即用户的决定，除非用户另有指示。只有用户本人能做的事（发邮件申请 key、注册账号、付费、在自己机器上运行 S0 和签收脚本）仍然列为用户待办，不算决策 | 用户 2026-11-13 的指示。写进 `docs/CURRENT.md`，以后接手的 agent 都能看到 |

## 6. 下一步（给本地 agent 的任务单）

规则不变：每个任务一个提交，提交信息以任务 ID 开头，提交说明写明新增测试名和验证结果；只认该提交自己的 CI。

**阶段 1.6（合并前，现在做）**

1. CL-23：按第 4 节修复。
2. `docs(handoff)`：新建 `docs/reviews/response-claude-2026-11-12-phase1_5.md`（不要改旧的交接文档），记录阶段 1.5 和 1.6：每个任务的提交、修改前失败的证据、新增测试名、CTest 和 pytest 数字、CI 链接，以及 CL-24 第 1、3 条要补的说明。同一个提交里，把本文件原样存为 `docs/reviews/claude-2026-11-13-phase1_5-rereview.md`（从只读分支 `review/claude-2026-11-13-phase1_5` 取）。
3. 更新 PR #12 的描述：补上阶段 1.5、1.6 的提交表和验证结果，以及三个中间红提交的说明。

门槛：CTest 不少于 42 个用例；pytest 通过数不少于 1,718（Linux）或 1,723（Windows）；CI 中变异检查 M1–M3 全部 KILLED；R1、R2 的结果不回退；CL-23 的验收全部满足。

**阶段 2（满足 D12 后做）**

1. 把 PR #12 转为 ready，用 merge commit 合并进 `main`（不 squash，不 rebase）。
2. 先在 PR 上留言并关闭，再删分支。PR 还开着时删除它的分支，GitHub 会自动关闭它或改它的 base，就来不及留下说明了。
   - #6–#9、#11：留言"已通过 #12 合入（merge commit `<sha>`）"后关闭。#5 的 head 合并后已在 `main` 里，GitHub 可能会自动标记为已合并，同样留言。
   - #10：留言"4 个代码和测试提交在 `main` 中已有等价补丁（`git cherry` 标为 `-`；其中两个测试提交就是 `d1301d7`、`9927b13`）；剩下 3 个是十节点批次的计划和 validation 记录（`45761f1`、`13591de`，以及 `work/blueprint-next-ten-20261029` 上的 `d80b8fc`），按 S1 冻结规则不再合入"，然后关闭。
3. 删分支前，把下面的清单写进交接文档：
   - 已经在 `main` 中，可以直接删：`work/cancel-retention-20261026`、`work/project-audit-20261026`、`work/project-audit-recovery-20261026`、`work/project-audit-stage1-20261026`。
   - 合并 PR #12 后进入 `main`，删除：`work/blueprint-next-ten-v2-20261029`、`work/blueprint-v2-dev-20261029`、`work/project-audit-followup-20261027`、`work/project-audit-full-20261029`、`work/project-audit-next-three-20261027`、`work/project-audit-owner-review-20261028`、`work/project-audit-rebuilt-five-20261027`、`work/windows11-audit-20261028-1100`，以及 `work/review-2487934-a` 本身。删除前用 `git branch -r --merged origin/main` 再确认一次。
   - 不在 `main` 中的 5 个：先在分支头打附注标签 `archive/<分支名去掉 work/ 前缀>` 并推送标签，再删分支。它们是 `blueprint-ten-modules-20261029` 和 `blueprint-next-ten-20261029`（只剩上面说的计划和记录提交），以及 `next3-transport-20261026`、`recovery-transfer-20261027`、`stage1-transfer-20261026`（运送提交用的辅助分支，提交说明都写着 DO NOT MERGE；运送的目标提交 `fbffc79`、`1778339`、`d292235` 都已在开发线中）。
   - `review/*` 全部保留（现在 5 个）。
4. CL-05：建立唯一入口 `docs/CURRENT.md`，按原报告 CL-05 的要求写。另外写进两条：D17 的决策规则；审核文档的流转方式（审核方把每份审核推送到一个新的 `review/*` 分支，本地 agent 在下一批的第一个提交里原样存入 `docs/reviews/`）。
5. 把原报告第 6 节的流程规则写进蓝图，另加本轮的两条教训：新增或修改 CI 门禁的提交，推送前先在真实代码树上完整跑一次这个门禁；`validation/` 按字节保存，任何批量格式化或换行处理都要排除它。

**阶段 3（D1、D2、D6、D15 已定）**

CL-20（显式 TLS）→ CL-07（诚实的 User-Agent）→ CL-06（`mwmbl_api` 适配器和离线夹具测试；marginalia、mwmbl、wikipedia 三份来源审核记录）→ 给用户一条可直接复制、在用户机器上运行的 S0 命令 → `tools/accept_s1.py`。

用户待办：给 marginalia 发邮件申请免费的非商业 key（现在就可以发）；收到 S0 命令后在自己机器上运行。

**阶段 4、5**

按原报告（分支 `review/claude-2026-11-12` 的 `docs/reviews/claude-2026-11-12.md`）第 8 节执行，另外加入 CL-22（按 D14）和本文提到的两个可选项：CL-14 的 UTF-8 截断、`tests/unit.cpp:1124,1128` 的断言写法。

**明确不做（不变）**：扩展 B-24；新增"证明链"类机制；不必要的 schema 升级；改写 git 历史；启用抓取型来源；伪装成浏览器的 User-Agent；为了让测试通过而删除或放宽断言。

## 7. 本次复审用到的探针（供复核）

- 证据摘要核对：对第 4 节列出的 5 份清单，取出每条（路径, sha256），对 `git show <提交>:<路径>` 的字节计算 SHA-256 并比较。结果：`6d550cd` 139/139 吻合，`c7be1d7` 128/139 吻合。
- CL-23 修复验证：在 `c7be1d7` 的临时 worktree 里按第 4 节的做法提交（没有推送），逐项检查验收。
- `mcp_list_string.py`、`cx_fuse_tie.cpp`、`site_partial.cpp`、`mcp_null_scope.py`：做法同上一份复审第 7 节。
- CL-04r 测试判别力：在一个空目录里运行 `PYTHONPATH=<6d550cd 的 python/> python -m pytest <c7be1d7 的 python/tests/test_mcp_argument_sweep.py>`。不要在 `python/` 目录里运行：`python -m` 会把当前目录放在模块搜索路径最前面，导入的就成了新代码。
- CL-12r、CL-14r 测试判别力：在临时 worktree 里分别把 `src/web.cpp:340-341` 还原为 `(rank, url)` 比较、把 `src/service.cpp:448` 还原为 `log_internal("service.lane_read",nullptr)`，重新构建并运行 `bzs_tests`、`bzs_background_pool_tests`，确认失败后用 `git checkout -- src` 恢复。
