# 本地 agent 交接：claude-2026-11-12 审核的阶段 0 + 阶段 1 执行记录

- 日期：2026-11-12（本地 agent 会话）
- 执行者：本地开发 agent（Windows 工作树 `C:\bzs861-audit`）
- 交付分支：`work/review-2487934-a`，代码头 `0e23363`（本文件随后追加为文档提交）
- 审核 PR：#12（draft，不合并，等用户 D8/D9）：https://github.com/wmqfl861/bzs861/pull/12
- 状态：阶段 0、阶段 1 完成并自测通过；**阶段 2/3 阻塞在用户对 D1–D9 的答复**（见第 8 节）

## 1. 快速定位（审核 agent 从这里开始）

| 材料 | 位置 |
|---|---|
| 你的审核报告（权威任务单） | 分支 `review/claude-2026-11-12`：`docs/reviews/claude-2026-11-12.md`、`claude-2026-11-12-repro.md`、`claude-2026-11-12-tasks.json`（只读，未合并、未修改） |
| 本轮全部代码变更 | PR #12 / 分支 `work/review-2487934-a`，基线 `a2e5e4f` → 代码头 `0e23363`，8 个提交（见第 4 节表） |
| 变异检查工具（新入库） | `tools/mutation_check.sh` |
| 复现命令 | 本文件第 10 节 |

## 2. 背景与本轮范围

按报告第 8 节任务单执行阶段 0（不改代码：基线确认、决定表上交、复现 R1/R2/R3）与阶段 1（不需要用户决定的 7 项：CL-02、CL-03、CL-04、CL-12、CL-14、CL-09 第 1 步、CL-01 前置 cherry-pick）。阶段 2（合并 main、关 PR、`docs/CURRENT.md`）等 D8/D9；阶段 3（CL-20/07/06、S0、`accept_s1.py`）等 D1/D2/D6。未做任何报告"明确不做"清单里的事：无 B-24 扩展、无证明链机制、无 schema 升级、无历史改写、无抓取型来源启用、无浏览器 UA 伪装、无删除断言换绿灯。

## 3. 阶段 0：基线与复现（P0-1/P0-2/P0-3）

- **基线（P0-1）**：工作树与 `origin/work/review-2487934-a` 同步于 `a2e5e4f`，即你报告的被审提交。该提交推送前 CI 三个 job（linux / windows / windows-python）全部 success（记录在 PR #12 描述）。工作树干净（仅上轮导出残留 `docs/audit/20261031-2487934/` 未跟踪、未提交）。
- **决定表（P0-2）**：报告第 7 节 D1–D9 已原样提交用户，截至本文写作**用户尚未答复**。
- **复现（P0-3）**：
  - **R2（CL-04）**：`mcp_null_scope.py`（与 repro 相同的探针）在 `a2e5e4f` 输出与你逐行一致：仅 `crawl_site` 拒绝，其余 5 行静默转发（`"null"` 丢参、`true`→`1`、未知字段丢弃）。
  - **R3（CL-03）**：Windows/Git Bash 版变异循环（8 套件 × 基线 + M1/M2/M3）：基线（未修改）8 套件全过；`[M1_contributor_overflow] SURVIVED`、`[M2_incomplete_records] SURVIVED`、`[M3_legacy_marker] SURVIVED`——与你 repro 输出一致。
  - **R1（CL-02）**：按你 repro 的建议直接写成回归测试再修：新测试 `site_queries_push_host_predicate_into_candidate_windows` 在未修复核心上首跑红（`found` 断言失败，即"未找到且 partial=false"），构成 R1 复现。

## 4. 阶段 1：逐任务记录（一任务一提交，CL 前缀）

| 提交 | 任务 | 内容与关键决策 | 新增/重写测试 |
|---|---|---|---|
| `b1c5b7c` | CL-04（P2） | 删除手写 `_RAW_ARGUMENT_TYPES`，改为 `inspect.signature` + `typing.get_type_hints` 从注册工具函数自动推导（`bool/int/float/str/Literal[str]/list[str]`、Optional 解包；不支持的注解 fail-closed 拒绝调用）。`int` 拒 `bool`；`str | None` 拒 `"null"` 字符串；`list[str]` 逐项检查；未知字段全工具拒绝；`scope_path ^/` 规则覆盖 `map_site`。`list_tools()` 对全部工具去 null 类型、`additionalProperties=false`。修复后 R2 六行全部 REFUSED | `tests/test_mcp_argument_sweep.py`（25 工具 × 全部可选参数 × `"null"`/错类型/显式 null + 每工具未知字段 + schema 断言）；`test_mcp_direct_cancel.py` 增显式 null 拒绝用例 |
| `d1301d7`、`9927b13` | CL-01a | `40e2afd`、`352b006` 无冲突 cherry-pick 进开发线（保留原作者与说明），两个测试通过（2 passed） | `test_map_parallel_same_origin_requests_release_all_host_admission_slots`、`test_crawl_parallel_same_origin_pages_release_all_admission_slots` |
| `7a8a1a0` | CL-02（P1） | host 条件下推进**全部三条候选 SQL**（FTS/子串/有界扫描）与 priority 层：每 host 一组 `AND (d.host=? OR (length(d.host)>length(?) AND substr(d.host,-length(?)-1)='.'||?))`（用 `substr` 不用 `LIKE`，`_` 不当通配符；多 site 按 AND，与 `matches()` 一致）；绑定顺序随 SQL 文本（词项/owner/子串交集/hosts/LIMIT）；`LIMIT` 恢复 `max_candidates+1`；删除 R-03 的 `fetch_limit` 放大与循环内预过滤。修复后 R1 三条路径（词项/`"alpha"` priority/`"alpha filler0"` 子串）`max_candidates=1/2` 全部 found=yes | `site_queries_push_host_predicate_into_candidate_windows`：目标域自身匹配超预算→`partial=true`；恰好放得下→`partial=false`；`site:a site:b` 仍空；子域点边界命中、`notarget.example` 不命中；`site:+NOT` 语法拒绝锁定 |
| `b188eca` | CL-12（P3） | 供体按 `(rank, url)` **整体**替换 url/title/snippet（空字段保持空，不跨命中借用）；组间平手由 `first_seen`（到达序）改为去重键；来源列表由仅按 rank 改为 `(rank, source_id)`。契约写入 `docs/API.md` 搜索结果段 | `fuse_output_is_independent_of_source_order`：两种来源顺序 × 命中顺序正反共 4 种排列输出逐字节一致；断言等 rank 平手胜者、整体供体、`publication_urls` 保留全部 3 个实际贡献变体、`(rank, source_id)` 排序 |
| `3a82153` | CL-14（P3） | 新 `include/bzs/diagnostics.hpp` + `src/diagnostics.cpp`：`log_internal(where, exception)` 输出结构化 JSON（event/where/type/`what()` 截 200 字节，无 URL/查询/正文/令牌）+ 无锁计数器 `internal_error_count()`。接入 searchd 两处 `catch(const std::exception&)`、service 四条后台通道（document/read/map/search）的外层 `catch(...)` 与"失败状态写不进去"的内层 `catch(...)`；`stats` 注入 `internal_errors`。有意回退的 catch 不改行为，`store.cpp` 迁移处两处无注释的补了注释 | `internal_error_diagnostics_are_structured_and_counted`：捕获 clog，注入 `runtime_error` 与无异常两次调用，断言两条可解析事件（where/type/message、无异常时 `type=unknown`）、计数精确 +2、Service `stats` 暴露同一计数 |
| `0476be4` | CL-03（P2） | 详见第 5 节 | 5 个测试（2 重写 + 3 新增） |
| `0e23363` | CL-09a（P2） | `git mv validation/s2_takeover/cache_review/probe_http_policy.py tests/probe_http_policy.py`（`ROOT` 由 `parents[3]`→`parents[1]`），CMake `e2e_cache_policy_review` 指向新位置；新增根 `.ignore`（ripgrep 类工具跳过 `validation/`）与 `.gitattributes`（`validation/** linguist-generated=true`）。**不改写历史**。验收：`rg -l "class RequestExecutor"` 仅命中 `include/bzs/request_executor.hpp`；`ctest -R e2e_cache_policy_review` Passed（34.5s） | （无新测试，验收即检查） |

**执行顺序偏差**：因变异检查独占构建目录，先做了纯 Python 的 CL-04/CL-01a，再回 CL-02→CL-12→CL-14→CL-03→CL-09a。任务语义无依赖，不影响结果。

## 5. CL-03 详情（本轮最复杂项，重点复核）

**重写的两条恒真测试**（精确值 + 阳性对照，被查 URL 都落在被查页内）：

- `incomplete_contributor_binding_keeps_captured_ceiling_with_legacy_crawl_marker`：显式 `limit=24` 使 24 条 worker 命中全部冻结；页大小 100。断言恰好 23 条可见（已存 `url(0)` 被旧爬取标记封锁）、`url(0)` 不在且 `url(1)` 在（阳性对照）、再封锁 `url(23)` 后恰好 22。
- `fused_snippet_keeps_all_actual_url_contributors_before_and_after_source_fusion`：改用 R-01 下仍会融合的变体（主机名大小写、`:80` 默认端口）。断言恰 1 条融合命中、胜者 URL/snippet、封锁共享发布身份后恰 0 条（两种策略 flavor 均如此）。

**三条变异的杀死方式**（均已在变异检查中验证，见第 6 节）：

- **M1**（贡献者溢出→`captured_mode=2`）：选择报告方案 (a) 保留分支。公开面上不可达（`max_sources(20) × max_per_source(30) × publication_urls(30) = 600`，R-01 下组内变体共享同一身份），推理已写进 `src/sessions.cpp` 分支处注释。测试 `contributor_overflow_tightens_capture_ceiling`（unit.cpp）经既有 friend 钩子 `StoreSearchTestAccess` 直调 `search_bindings_locked`：601 条原始 URL（同身份）→ `captured_mode=2`；600 条不触发；25 个不同身份 → 触发。
- **M2**（已存绑定扫描不完整→`captured_mode=2`）：**判别性构造有讲究**。最初按"长身份链"构造在变异检查中存活——因为分页时重算把冻结的 urls 当 contributing 传回，`urls.size()>24`（M1 子句）会把同一场景再挡一次（两层冗余）。改为 **records>24 路径**：25 个主机名大小写变体（`UNIQUE(owner,url)` 使每个变体是独立 documents 行，`url_key` 折叠大小写后同一）→ 扫描计 25 条记录 → `incomplete`。测试 `binding_history_overflow_tightens_capture_ceiling`（阳性对照 `control.example` 始终可见）。
- **M3**（旧爬取标记传播进捕获绑定）：**可达性发现**——标记只有经 `activate_site_publication` 折叠进 `legacy_frozen_withdrawn` 才被强制执行；旧测试从未激活，标记从未生效（这是恒真的第三层原因，也是 M3 变异在你我两轮检查中都存活的直接原因之一）。另一个发现：`erase` 会把 `current_snapshot` 置 NULL，已删除历史进不了扫描。**只有 M3 能挡住的场景**：标记在**同 url_key 的存活变体文档**的快照上（命中行自身文档干净）——web 会话分页的 mode 只来自冻结捕获绑定与现算绑定（`store.cpp`/`sessions.cpp:414`），两者都只经传播路径携带标记。测试 `legacy_crawl_marker_on_variant_document_tightens_capture`。

**工具**：`tools/mutation_check.sh`（清理后的变异检查：基线必须全过，然后 M1/M2/M3 各一轮重建+8 套件，逐套件日志；源码模式失配时显式报告）。规则已按报告第 6 节意图执行：改动这些路径的提交附变异结果（本提交与后续）。

## 6. 全量验证与 CI（证据）

| 项 | 结果 |
|---|---|
| 变异检查（阶段1完成后） | 基线 8 套件全过；`M1 KILLED by: bzs_tests`；`M3 KILLED by: bzs_session_tests`（`tools/mutation_check.sh` 完整跑）；M2 首轮 SURVIVED（长链构造无判别性，见第 5 节）→ 重写为 records 构造后**手动应用同一 sed 变异**重建验证 `bzs_session_tests` 失败（`check failed: !polluted`）→ 恢复源码。未完整重跑脚本（诚实记录；M1/M3 的杀测试在重写后未变） |
| 本地 CTest（Windows Debug，全量） | **42/42 passed**，总耗时 1925s（门槛 ≥42 ✓） |
| 本地 pytest（仓库锁版 venv，Python 3.12.10 / aiohttp 3.14.5 / mcp 2.3.0） | **1714 passed，0 failed**（108s；门槛 ≥1703 ✓；Windows 上 5 个 Windows 专属用例实跑不跳过） |
| 套件用例数（不减少） | `bzs_tests` 116→**120**；`bzs_session_tests` 23→**25**（319 断言→310 是因恒真断言被替换为更少的精确断言+对照，用例数增加） |
| 远端 CI（`0e23363`） | linux ✓ / windows ✓ / windows-python ✓ **三绿**（PR #12） |
| rg 验收 | `rg -l "class RequestExecutor"` 仅 `include/bzs/request_executor.hpp` |

## 7. 值得审核注意的实现细节与偏差清单

1. **CL-04 语义收紧一处既有测试**：`test_mcp_direct_cancel.py` 原 `[None]` 参数化用例记录的行为是"显式 `read_id: null` 被静默当省略转发"——这正是 CL-04 要消除的类别，且与 `crawl_site` 既有守卫行为（`max_pages: None` → E_SCHEMA）矛盾。已改为 `test_read_url_refuses_explicit_null_handle`（拒绝），提交说明写明理由。**这不是删断言换绿灯**，是把与统一规则相反的旧期望改齐。
2. **CL-02 有界扫描路径无法与 `site:` 组合**：查询语法本就要求 `site:` 处于顶层正向合取（`NOT` 组合被拒），测试改为锁定该语法边界（`rejects(...,"E_QUERY_SYNTAX")`）。host 片段本身仍插入有界扫描 SQL（同一代码路径）。
3. **CL-12 的 `(rank,url)` 平手规则**：等 rank 时**字典序小写前**——ASCII 大写 < 小写，故 `HTTP://SHARED…` 胜 `http://shared…`。测试明确断言了这一点；如你认为应改用其他平手规则（如小写优先），是规格讨论不是缺陷。
4. **CL-14**：`typeid().name()` 在 MSVC 输出含空格的类名（如 `class std::runtime_error`），Linux libstdc++ 输出 mangled 名。日志字段按平台不同，属可接受差异；测试用 `find("runtime_error")` 跨平台断言。
5. **CL-09a**：`.gitattributes` 只加 `validation/** linguist-generated=true`，不动已有属性；`.ignore` 是新文件（ripgrep 等遵守，git 不受影响）。
6. **M2 的完整脚本未重跑**（见第 6 节）——如果你要求端到端证据，可对你自己的复检环境跑一次 `tools/mutation_check.sh`（约 1 小时）。
7. **上轮遗留 A4/R-04（aiohttp 压缩解码资源边界）**：本轮报告任务单未包含，未处理。如你判定仍需处理，请列入返工清单。

## 8. D1–D9 状态（等待用户）

报告第 7 节决定表已原样提交用户，**尚未收到任何一项答复**。阶段 2（合并 main、关闭 #5–#9/#11、`docs/CURRENT.md`、蓝图流程规则）阻塞于 D8/D9；阶段 3（CL-20/07/06、S0 探测、`tools/accept_s1.py`）阻塞于 D1/D2/D6；D3 只影响 CL-09 第 2 步的取向（推荐不改写历史，本轮已按此执行第 1 步）。审核 agent 可对推荐值给出确认或修改意见，但**决定权在用户**。

## 9. 审核 agent 需要做什么（验收清单）

1. 对照你报告第 4 节各条目"验收"与 `claude-2026-11-12-tasks.json` 的 acceptance，逐项复核 PR #12 的 8 个提交 diff。
2. 重点复核第 5/7 节列出的细节：CL-02 的 SQL 下推与 partial 语义恢复、CL-03 三条变异的杀死证据与 M2 判别性说明（含两层冗余的发现）、CL-04 全工具类型推导与显式 null 收紧、CL-12 顺序无关性的排列测试、CL-14 日志内容不含敏感材料。
3. 抽查验证证据（第 10 节命令）：R1/R2 复现程序修复后输出、`tools/mutation_check.sh`、套件/断言计数、`rg` 验收。
4. 输出：每个任务 `pass / rework`（附证据或反例）；新发现问题按 P1/P2/P3 列出；对 D1–D9 推荐值给出你的意见（交用户拍板）。
5. 边界（不变）：只读复审；不合并 PR #12；不修改 `review/*` 分支；不改写历史；不推送；结论交给用户转发本地 agent。

## 10. 复现命令（Windows 工作树；Linux 等价见你 repro 文件）

```bash
# 全量原生 + Python（与 CI 的 windows job 同口径）
cmake --build build --config Debug -j 8        # VS 生成器，build/ 已配置
cd build && ctest -C Debug --output-on-failure # 42/42
cd ../python && .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests   # 1714 passed

# R2 修复后行为（repo 外运行，venv python）
python mcp_null_scope.py   # 六行全部 REFUSED

# 变异检查（约 1 小时；REPO/BUILD 按你的环境）
tools/mutation_check.sh <repo> <build-dir> [cmake]
# 期望：baseline 全过；M1 KILLED by bzs_tests；M2/M3 KILLED by bzs_session_tests

# CL-09a 验收
rg -l "class RequestExecutor"        # 仅 include/bzs/request_executor.hpp
```
