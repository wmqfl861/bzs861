# bzs861 当前入口（唯一）

更新：2026-11-15（S0 已由 Codex 裁决；CL-30/F6/F7/F2 落地批次待复审合入）

## 已核验的集成基线

- `main` 头：`358ae38`（Merge PR #23：CL-18 URL 身份格；此前依次为 #22 CL-11 全项 `cd35820`、#21 进程安全 `748c82a`、#20 CL-10 `c4d219f`、#19 misc5 `3dc8722`、#18 CL-17 `5389c0c`、#17 CL-08 `663fd91`、#16 CL-22 `7b3bd2c`）。
- 全部以精确已验证 head merge-commit 合入。首轮失败记录：PR #22 linux×2、PR #23 linux×1 见 [阶段 4/5 交接](reviews/response-claude-2026-11-12-phase4_5.md)第 1 表；另有三次 windows-python 失败（PR #16/#19/#22 期间）当时漏记，已由 [记录更正](reviews/response-claude-2026-11-14-pre-s0.md) 补录（根因为 test_probe 时序假设，测试已修复）。自该批起按 D28 在合并前用 `filter=all` 核对全部尝试。
- 原 2026-11-12 审核发现清单（CL-01–CL-22 + 复审新发现 CL-23–CL-26）中，列为阶段 1–5 的全部修复项均已合入 main。

## S0 裁决结论（2026-11-14，[Codex 裁决](reviews/codex-2026-11-14-s0-review.md)，11 发现 + D-S0-1..16）

- **来源启用裁决**：marginalia hold（需个人 key 成功 S0 + F7 修复）；mwmbl hold（需 F6 修复 + 缓存许可依据，均已在本批交付，待复审签署）；wikipedia_en / wikipedia_zh **enable（限 links_only）**，前置 CL-30 + F2 + 合同落库（D-S0-4/5 已给专属签名与配额：en/zh 共享 wikimedia 预算 2,000/天）。
- **实测核验**：10 份原始响应体（9×200 + 1×429）在内存按报告解析器身份回放 10/10 一致；mwmbl 平均 7.7 为 40/21/3 截断后的平均数；zh 的 1 次 empty 为上游合法空集（totalhits=0）。
- **闸门**：CL-30 放行；S1 签收仍受阻（需最终清单下用户小规模复探，F9 哈希绑定）；7 个 API 草案全部维持候选，接入顺序 arxiv→hackernews→github→stackoverflow→crossref→openalex→gdelt，全部排在 S1 后；D26 测量/重构冻结继续。

## 正在进行的批次（S0 裁决落地，待 Codex 复审后合入）

- **CL-30**（D-S0-14，分支 work/cl30-20261114）：doctor 白名单化（只允许当前用户/SYSTEM/BUILTIN\Administrators 读秘密，其余任意 SID 可读即报错）；create_auth 改为独占建空文件→保护→确认 ACL→才写秘密，失败不留 token 字节。
- **F6/F7**（D-S0-3/2 前置，分支 work/source-fixes-f6f7-20261114）：mwmbl 401 经真实 search 路径归类 auth_failed（check 路径，三层回归）；marginalia 503 冷却取 max(10 分钟, Retry-After) 且不再写死共享 key 诊断。
- **F2**（分支 work/s0-results-20261114 提交 817ecef）：10 份响应体归位 results/raw/（字节未变），标准离线回放 passed 10/10。
- **mwmbl 缓存条款补证**（本批 docs）：mwmbl.org/terms 明示数据集 CC BY-NC-SA 4.0；个人本地缓存 + links_only 口径待复审签署。

## 下一步与用户待办

- Codex 复审本批（CL-30/F6/F7/缓存依据）→ 通过后按精确 SHA 合入 → 若 mwmbl 获签署则激活 mwmbl + wikipedia_en/zh（links_only）→ 冻结最终清单 → **用户小规模复探（新目录，每源至少 1 次真实非空 ok）** → 用户完整运行 `tools/accept_s1.py`。
- 用户可发：marginalia 免费 key 申请邮件（可选，仅当要启用 marginalia）。

## 待决事项

无。D1–D28 执行完毕；S0 裁决与后续技术决策均经用户指定的 Codex（gpt-6-astra/max）裁定。转私有仓库 + SearXNG 引擎移植的时序（用户已同意方向）建议放在 S1 签收之后执行，盘点见 work/searxng-port-prep-20261114 分支。

## 长期规则

- **D17（决策规则）**：审核方逐项推荐+理由，推荐值即用户决定，除非用户另有指示；只有用户本人能做的事（申请 key、注册、付费、在自己机器上运行 S0/签收脚本）列为用户待办。未覆盖的新决定：可撤回的选保守做法留档；不可撤回的（删数据、改写历史、启用来源、对外发送）暂停并交用户。
- **D20（CI 重跑规则）**：失败 job 重跑前把首轮 job 链接和失败测试名写进交接文档；核对全部尝试（`filter=all`）。CL-25/CL-26 已交付，旧的重跑豁免失效；新失败先查因。
- **D21**：不以测试重试或 Defender 排除掩盖代码问题。
- **集成规则（CL-01/PROC-1）**：main 是唯一集成分支；每批一个 PR、CI 绿后 1 个工作日内合并、叠加深度 ≤2；**每个改变状态的 PR 必须在同一个 PR 里更新本文件**。
- **审核流转**：审核方推新 `review/*` 只读分支；本地 agent 下一批第一个提交原样入库 `docs/reviews/`。
- **证据与边界**：`validation/` 按字节保存；每批摘要 ≤50KB、原始日志走 CI artifacts；不提交源码快照/密钥/私人数据；不扩展 B-24、不新增证明链、不启用抓取型来源、不伪装浏览器、不删放宽断言换绿、不改写历史。

历史入口原件见 `docs/history/`。
