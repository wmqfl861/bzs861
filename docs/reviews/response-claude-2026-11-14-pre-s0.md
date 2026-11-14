# 记录更正：阶段 3/4/5 的三次 windows-python 首轮失败未按 D20 记录

对应第四次复审 CL-29（`docs/reviews/claude-2026-11-14-phase2to5-review.md` 第 4 节）。以下三次失败在此前的交接文档（`response-claude-2026-11-12-phase4_5.md` 写 "#16/#19 首轮失败：无"）与 `docs/CURRENT.md`（写"首轮失败按 D20 全记录"）中均未记录，本文件补记并更正：

| 提交 | job（事件） | run | 失败测试 | 原因 |
|---|---|---|---|---|
| `f157e7f`（PR #16 head） | windows-python（pull_request） | [43414366832](https://github.com/wmqfl861/bzs861/actions/runs/43414366832) | `test_probe.py::test_resume_continues_only_unreserved_work_with_the_same_quota` | probe 按距上次开始的时钟计步，scripted 离线适配器无网络 I/O；首查后的三次 fsync 检查点写超过 200ms 间隔时，第二条查询之前无任何 await，测试取消落在第二条之后（详见复审第 4 节 CL-29） |
| `3dc8722`（合并 PR #19 后的 main） | windows-python（push） | [43431373105](https://github.com/wmqfl861/bzs861/actions/runs/43431373105) | 同上 | 同上 |
| `410a146`（PR #22 中间提交） | windows-python（pull_request） | [43482335582](https://github.com/wmqfl861/bzs861/actions/runs/43482335582) | 同上 | 同上 |

**流程事实（照实陈述）**：PR #16 在其 pull_request 轮 windows-python 为红且未重跑的情况下合并——违反"CI 绿后合并"规则；PR #19 合并后 main 的 push CI 红过一次且当天未记录。根因是核对时只看了 PR 页的最后状态而未用 `filter=all` 查全部尝试。三次失败的产品行为均无问题（真实适配器有网络 I/O，必然让出事件循环）；测试已在本批（`81b2600`）按复审验证过的冻结时钟法修复。

**规则落地（D28）**：今后写交接文档或合并 PR 之前，对本批每个提交及合并后 main 的 merge commit 运行

```
gh api "repos/wmqfl861/bzs861/commits/<sha>/check-runs?per_page=100&filter=all" \
  --jq '.check_runs[] | select(.conclusion != "success") | "\(.name) \(.conclusion) \(.details_url)"'
```

逐条记录非成功尝试；有红色检查且未记录并重跑转绿的 PR 不得合并；合并后 main 变红当天记录并开修复任务。
