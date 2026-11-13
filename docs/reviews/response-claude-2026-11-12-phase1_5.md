# 本地 agent 交接：阶段 1.5 + 1.6 执行记录（claude-2026-11-12 审核线）

- 日期：2026-11-13（本地 agent 会话）
- 范围：`6d550cd..002c35c`（阶段 1.5 六个提交 + 阶段 1.6 两个提交），PR #12
- 阶段 1.5 复审：`docs/reviews/claude-2026-11-13-phase1_5-rereview.md`（本次同提交原样入库；来源只读分支 `review/claude-2026-11-13-phase1_5`）
- 用户授权：D1–D17 全部按审核方推荐执行（D17 为长期规则）

## 1. 提交清单

| 提交 | 任务 | 说明 |
|---|---|---|
| `2d50aeb` | CL-21 | 换行规范化 + CI CRLF 检查（上一轮已交付，本轮复审 pass，引出 CL-23） |
| `fe71564` | CL-04r | list[str] 参数接受 JSON 数组字符串；必填参数报错不再提示省略；深层嵌套 RecursionError 转 E_SCHEMA |
| `b66f367` | CL-03r | 注释改正 + mutation 脚本重写（见 §3 红 CI 说明） |
| `5a89460` | CL-12r | fuse 供体全序 `(rank,url,source_id,title,snippet)`（见 §4 补充说明正文） |
| `4d19e0c` | CL-14r | 后台通道 `log_current_internal` 保留异常类型/消息（见 §4 补充说明正文） |
| `c7be1d7` | CL-03r 修复轮 | 变异击杀判定放宽为"exit 1 + 任意 `FAIL <测试名>:` 行"，修正对未捕获异常形态击杀的误判 |
| `002c35c` | CL-23 | 7 个归档证据恢复 `6d550cd` 原始字节；`validation/** -text`；CI 检查扩展到 i/mixed 并豁免 attr/-text |
| 本提交 | CL-24 | 阶段 1.5/1.6 交接文档 + 第二次复审文件原样入库 |

## 2. 验证数字（阶段 1.5+1.6 门槛）

- 本地（Windows，`002c35c` 内容）：pytest **1723 通过 / 0 失败**（门槛 ≥1723 ✓）；CTest 用例数 42（≥42 ✓），全量两次 41/42，唯一失败均为 `launcher_lifecycle` 的不同子测试（进程/文件竞态抖动家族，单测独立 3/3 通过；详见 §5 观察）
- Linux CI（复审方独立验证）：CTest 42/42；pytest 1718 通过 + 5 Windows 专用跳过；变异检查完整运行 **M1–M3 全部 KILLED**（M1→`contributor_overflow_tightens_capture_ceiling`，M2→`binding_history_overflow_tightens_capture_ceiling`，M3→两个 legacy 标记测试）；与 `c7be1d7` linux job 一致
- R1（`site_partial` 8 行 found=yes partial=false）、R2（6 行 REFUSED）、CL-04r 兼容探针：复审方与本地双确认不回退
- CL-23 验收（本地逐项）：7 文件 `i/crlf w/crlf attr/-text`；对 `6d550cd` diff 为空；对 `c7be1d7` 忽略 CR 后仅 2 个配置文件；`git add --renormalize .` 幂等；新 CI 检查输出为空
- 逐提交 CI：`2d50aeb`/`fe71564`/`c7be1d7` 三绿；`b66f367`/`5a89460`/`4d19e0c` 仅 linux 红（见 §3）；`002c35c` 与本提交的 CI 见 PR #12

## 3. CL-24 第 1 条：三个中间提交 Linux 红的说明

`b66f367`、`5a89460`、`4d19e0c` 的 linux job 失败在同一步（变异检查）：CTest 42/42 通过、脚本自测通过后，M1 的真实击杀（`FAIL contributor_overflow_tightens_capture_ceiling: Missing field: captured_mode`，exit 1，119/120）被旧判定误报为 `harness failure (exit 1, not an assertion kill)`。**这三个提交上 CTest 都是绿的**；run：`43268736001`（b66f367）、`43268779067`（5a89460）、`43268821763`/`43269181028`（4d19e0c push/pull_request 两轮）。`c7be1d7` 修正判定（异常形态的 `FAIL <名>:` 行也算击杀；崩溃/超时/exit≠1/无 FAIL 行仍按工具错误），其 CI 三绿且变异门禁全过。工件日志（run 43268736001 的 mutation-logs）证明 M1 当时已被杀死。教训（按复审要求将写进蓝图）：新增或修改 CI 门禁的提交，推送前先在真实代码树上完整跑一次该门禁——`b66f367` 只用替身程序测过判定逻辑。

## 4. CL-24 第 3 条：`5a89460`（CL-12r）与 `4d19e0c`（CL-14r）的补充说明正文

**CL-12r（`5a89460`，文件：`src/web.cpp`、`tests/unit.cpp`、`docs/API.md`）**
- 修改前失败：同 URL、同 rank、双来源（title/snippet 不同）两种顺序 fuse 输出 canonical 不一致——3 个断言失败（119/122 通过，exit 8）。
- 新增测试：`fuse_output_is_independent_of_source_order`（扩展：加入复审反例 `tied.example/d`，双来源顺序 × 各自命中反转共 8 种排列）；`fuse_same_source_ties_use_title_before_snippet`；`fuse_same_source_and_title_ties_use_snippet`（各 3!=6 种全排列，逐字节比较）。
- 实现：`std::tie(rank,url,source_id,title,snippet)` 全序（`src/web.cpp:340`），整体供体替换不变；组间去重键定序、来源列表 (rank,source_id) 排序、publication_urls 保留均不变。
- 结果：bzs_tests 122/122（2439 断言）、source_publication 20/20、search_modes 22/22；完全平手时空标题胜非空标题为 D16 维持的行为。

**CL-14r（`4d19e0c`，文件：`diagnostics.hpp/.cpp`、`src/service.cpp`、`tests/background_pool.cpp`、`docs/SECURITY.md`）**
- 修改前失败：真实 read 通道故障（Store 时钟注入使 `fail_read` 内 `clock_()` 抛出，逃逸 `execute_read` 到达 `service.cpp:445` 的通道 catch）日志 type 恒为 `unknown`——`bzs_background_pool_tests` exit 8，失败点恰为 `event.at("type").str()!="unknown"`（where/计数/计费断言先行通过，证明事件经过真实通道 catch）。
- 新增测试：`read_lane_internal_diagnostics(bool)`（标准异常 `runtime_error` + 非标准 `throw 14` 两轮）：断言 where=`service.lane_read`、type 非 unknown/为 unknown、受控 canary 消息、计数恰 +1、未知用量 3 请求/4096 字节不退还、无回执。
- 实现：`log_current_internal(where) noexcept`（`throw;` 取回当前异常，std::exception→带类型/消息，否则 unknown）；`src/service.cpp` 全部 9 处通道/失败写入 catch 改用；失败结算与预留语义未动；`docs/SECURITY.md` 新增"异常与内部诊断"约束。
- 结果：background_pool+core 2/2、`--repeat until-fail:3` 稳定、map 24/462、batch 55/1793；UTF-8 截断（可选）留阶段 4。

## 5. 观察项（未列为本轮发现，如实记录）

Windows 进程/文件竞态抖动家族，两天内 3 个测试 7 次失败、全部重跑转绿，阶段 1.5/1.6 diff 未触碰该区域：`launcher_lifecycle`（CI ×3：4d19e0c、c7be1d7 两轮；本地全量 ×2，两个不同子测试）、`test_process_tree::test_stop_owned_kills_the_whole_real_tree`（CI ×2：2d50aeb、c7be1d7 首轮）、`test_mcp_auth::test_concurrent_adds_never_lose_tokens`（CI ×1：b66f367；本地 ×1）。建议并入阶段 4 与 CL-10 的 Windows 测量/Defender 假设一起处理（测试重试容忍或 runner 目录排除）。

## 6. 阶段 1.6 之后

按 D12：本文件与 CL-23 两个提交各自 CI 三绿后执行阶段 2（PR #12 转正合并、#5–#11 留言关闭、分支清理按 D13 清单打 archive/ 标签、CL-05 `docs/CURRENT.md` 写入 D17 与审核文档流转方式、蓝图写入流程规则与两条教训）。
