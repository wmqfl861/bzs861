# bzs861 当前入口（唯一）

更新：2026-11-13（阶段 2，PR #12 合并后）

## 当前集成提交

- `main` 头：`c6ccfac`（Merge pull request #12：阶段 0/1/1.5/1.6，99 个提交合入；本文件所在 PR 为其直接后继）
- CI：[actions/runs 列表](https://github.com/wmqfl861/bzs861/actions?query=branch%3Amain)——合并前最后一个代码头 `b85eb56` 三绿（linux/windows/windows-python，均首次尝试通过）

## 正在进行的批次

- `docs(CURRENT)` 本批（分支 `work/current-docs-20261113`，PR #13）：CL-05 唯一入口 + PROC-1 蓝图流程规则

## 下一步

阶段 2.5：从 `main` 开分支 `work/windows-stop-20261113`，先 CL-26（Windows 锁文件写入竞争），再 CL-25（停止进程树排除父 PID 复用的无关进程），一个 PR 到 `main`；验收见 `docs/reviews/claude-2026-11-13-phase1_6-rereview.md` 第 6 节。之后按同一复审第 6 节进入阶段 3（CL-20 → CL-07 → CL-06 → S0 → `tools/accept_s1.py`）。

用户待办（不属于决策）：给 marginalia 发邮件申请免费非商业 key（现在即可发）；收到 S0 命令后在自己的机器上运行；在自己的机器上运行 `tools/accept_s1.py` 作为 S1 签收依据。

## 待决事项

无。D1–D21 已全部按审核方推荐执行（记录见 `docs/reviews/claude-2026-11-12-phase1-rereview.md`、`claude-2026-11-13-phase1_5-rereview.md`、`claude-2026-11-13-phase1_6-rereview.md` 第 5 节）。

## 长期规则

- **D17（决策规则）**：需要用户决定的事项，审核方逐项列出推荐和理由，推荐值即用户的决定，除非用户另有指示。只有用户本人能做的事（发邮件申请 key、注册账号、付费、在自己机器上运行 S0 和签收脚本）列为用户待办，不算决策。审核文件未覆盖的新决定：可撤回的，选最保守、最符合现有规则的做法先做并写进交接文档待下一轮确认；不可撤回的（删除数据、改写历史、启用来源、对外发送请求或消息），先停下来写清选项和推荐，交用户转审核方。
- **D20（CI 重跑规则）**：失败的 job 可以重跑，但重跑前要把首轮失败的 job 链接和失败的测试名写进交接文档。CL-25、CL-26 修好之前，这两类已知特征的失败（`launcher_lifecycle`/`test_process_tree` 的 `_ProcessDenied`/父链竞态、`test_mcp_auth` 并发 `PermissionError`）允许按此规则重跑；其他任何失败都要先查明原因再继续。
- **集成规则（CL-01/PROC-1）**：`main` 是唯一集成分支；合并后所有新工作从 `main` 开新分支，每批一个 PR，CI 绿后合并；PR 叠加深度不超过 2；CI 绿后 1 个工作日内合并；每批结束时 `main` 必须包含该批成果并在同一 PR 里更新本文件。
- **审核文档流转**：审核方把每份审核/复审推送到一个新的 `review/*` 只读分支；本地 agent 在下一批的第一个提交里把它原样（字节级）存入 `docs/reviews/`。`review/*` 分支永久保留只读，不合并、不修改。
- **证据与边界（不变）**：`validation/` 按字节保存（`-text`），任何批量格式化或换行处理都排除它；每批新增验证材料 ≤50KB，原始日志走 CI artifacts，不再提交源码快照；S1 签收前不开新的加固/工具批次；不扩展 B-24、不新增证明链机制、不启用抓取型来源、不伪装浏览器 User-Agent、不删除或放宽断言换取通过、不改写 git 历史。

## 历史入口

2026-11-13 之前的入口文档均已转为历史，见 `docs/history/`（HANDOFF_START_HERE、NEXT_AGENT 等）；各文档内的"当前入口"字样已改为"历史入口"并标注日期。
