# 本地 agent 交接：阶段 4/5 集成执行记录（claude-2026-11-12 审核线收尾）

- 日期：2026-11-14（本地 agent 会话）
- 范围：`c4d219f..358ae38`（PR #16–#23，八个 merge commit）
- 依据：复审第 6 节阶段 4/5 任务单 + D14/D17/D20/D21 + 四次 Codex 裁定（裁定 2/4 已入库 `docs/reviews/codex-rulings-20261113-winstop.md`；裁定 A/B 与格式门禁裁定见本文件附录链接）
- 决策通道：用户指示"需要决策调用 codex（gpt-6-astra/max），按其建议做"——本阶段全部新决策均经该通道裁定后执行

## 1. PR 总表（全部以精确已验证 head merge-commit 合入，不 squash、不改写历史）

| PR | merge | 内容 | 首轮失败（D20 全记录） |
|---|---|---|---|
| #16 | `7b3bd2c` | CL-22：aiohttp≥3.13.3 下限 + gzip 炸弹回归（1GiB→1.04MB，tracemalloc<64MiB；非空洞性：无界模拟 142MiB vs 锁定 1.45MiB） | 无 |
| #17 | `663fd91` | CL-08：Windows 秘密文件保护 DACL（ctypes SetNamedSecurityInfoW 断继承）、`bzs doctor`（GetNamedSecurityInfoW 检出宽 ACL）、双令牌（owner 非管理员默认给 MCP + admin 给 CLI；旧单令牌字节保留兼容） | 无 |
| #18 | `5389c0c` | CL-17：`Store::backup` 分页（每批 64 页，仅 step 持锁；`backup_deadline_ms` 默认 300s→E_BACKUP_TIMEOUT 503 关句柄再删临时零发布；并发 409 E_BACKUP_IN_PROGRESS；保留配额/原子发布/私有权限/隔离/E_BACKUP_EXISTS） | 无 |
| #19 | `3dc8722` | misc5：CL-14 可选 UTF-8 边界截断（红=json-c 拒绝含无效 UTF-8 的整行日志）；CL-19 unit.cpp:1124/1128 非抛出 CHECK（变异红：点名契约行号）；CL-19 cache_freshness.cpp:333 缩进修正（GCC CI 为最终门禁） | 无 |
| #20 | `c4d219f` | CL-10：Windows 重型套件 RelWithDebInfo 门禁（Debug 23 + RWDI 8 = 31 并集不变）；watchdog 恢复 Linux 值（sessions 420→60、direct_shared 420→90、core 90→45）；issue #4 附 2×2 测量（fsync 无关、RWDI 3.5–8.3×、Defender 本机 N/A）后关闭 | 无（push/PR 双轮首次通过） |
| #21 | `748c82a` | CL-25 收尾：写时身份锚定死亡检查（None-或-不同=已退出；同身份到期=失败；查询异常永不计退出）+ 所有权安全清理（spawn 后立即注册 ExitStack；终止授权仅用原始身份+持续持有的句柄；存活要求身份相等；真实树 4/4 零跳过） | 无 |
| #22 | `cd35820` | CL-11 全项：TSan/Release CI job；哈希锁（60 项逐字节不变 + Windows 闭包补 pywin32/colorama/tzdata 三项 marker 哈希，全新 venv 完整安装实证）；ruff E9,F,B 门禁（11 项修复）；`bzs_add_native_test` CMake helper（-118 行，ctest -N 双配置逐字节不变）；改动行格式门禁（不可变事件端点/零写 formatter/仅改行检查） | linux×2：run 43480876964（ruff pin 4/18 哈希缺 manylinux）→ run 43482335786（pip 续行语义：缩进行不附加）→ 单行 18 哈希修复 |
| #23 | `358ae38` | CL-18：URL 身份格属性测试（12000 对，三项蕴含 1540/1752/2107 + 严格见证 212/50）+ 文档；Codex 裁定准入定义域修正（4096/4100 精确 E_URL 边界回归） | linux×1：run 43499686222（**格式门禁首次真实拦截**——新 lattice 文件 1327 处缩进违规→clang-format 18 修正后三绿） |

## 2. 复核与裁定链

每个准备提交均经独立子代理复核（SPEC PASS/QUALITY APPROVED）后才 cherry-pick 集成；集成树另行验证（格式门禁 34/34、reporting 2/2、mutation harness 21/21、冒烟 61 过）。四次 Codex 裁定（gpt-6-astra/max，CLI header 核验）：

1. 计划审核（v2 修订：F1–F5 更正+5 答案+4 风险控制）
2. 停止树签名 2/3 裁定（已入库）
3. 哈希闭包+进程安全裁定（裁定 A：仅补三 pin 全哈希全新 venv 实证；裁定 B：先修既有 finally 授权再跑真实树）
4. 格式门禁裁定（17 点：事件端点/零写接口/major 18/XML 完整性等）

## 3. 格式门禁的端到端验证

PR #22 合入后，PR #23 首轮即被新门禁拦截（真实 PR 流程上的首次拦截），修复后三绿（首次放行）——门禁非空转。

## 4. 分支清理

远端：`main` + 6 个 `review/*`（只读保留）+ 5 个 `archive/` 标签。本地：全部 `work/phase4-*`、`prep-*` 分支与准备工作树已删。未改写任何历史。

## 5. 未完成项（如实，不冒充完成）

- CL-13（元数据池测量）、CL-15（json 锁测量）、CL-19 其余（FTS 提前终止、per-owner 证据上限）、CL-16（拆长函数）：原审核为"先测量再决定/可选"，不在本阶段已集成清单
- **用户侧 S1 闭环**：运行 `docs/S0_COMMAND.md` 探测（marginalia 可按 D15 先用共享 key）→ 申请自己的 marginalia key → 按审核结论启用来源 → 在用户机器上完整运行 `tools/accept_s1.py`。**S1 未签收**；来源保持禁用。
