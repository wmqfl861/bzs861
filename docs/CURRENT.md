# bzs861 当前入口（唯一）

更新：2026-11-15（S0 已裁决、第 2 轮复审完成；CL-30 RF1-3 已补修待第 3 轮复审；mwmbl 已获受限合同签名，激活未放行）

## 已核验的集成基线

- `main` 头：`1897398`（Merge PR #25：S0 前修复批次；此前依次为 #23 CL-18 `358ae38`、#22 CL-11 `cd35820`、#21 进程安全 `748c82a`、#20 CL-10 `c4d219f`、#19 misc5 `3dc8722`、#18 CL-17 `5389c0c`、#17 CL-08 `663fd91`、#16 CL-22 `7b3bd2c`）。
- 全部以精确已验证 head merge-commit 合入。首轮失败记录见 [阶段 4/5 交接](reviews/response-claude-2026-11-12-phase4_5.md)第 1 表与 [记录更正](reviews/response-claude-2026-11-14-pre-s0.md)（三次 windows-python 漏记补录）。D28 全尝试核对自该批起持续执行——这不是一次性任务：每个改变状态的 PR 合并前与合并后 main 均须核对。
- 原 CL-01–CL-26 修复项中列为阶段 1–5 者均已合入 main；CL-30 于本批次实施（见下，未验收完）。

## S0 裁决与第 2 轮复审结论

- **第 1 轮（S0 裁决，[codex-2026-11-14-s0-review.md](reviews/codex-2026-11-14-s0-review.md)）**：mwmbl/wikipedia_en/wikipedia_zh 解析实测通过（10 份原始体回放 10/10 一致），marginalia 为 D15 预期限流；启用裁决 en/zh 获 links_only 许可、marginalia/mwmbl hold；CL-30 放行；S1 签收仍受阻。
- **第 2 轮（落地复审，[codex-2026-11-15-post-s0-review.md](reviews/codex-2026-11-15-post-s0-review.md)）**：F2 验收通过（PR #29 集成）；F6/F7 验收通过（PR #27）；CL-30 不予验收（RF1-3，PR #26 已补修、新 head `eba3507` 待第 3 轮复审）；**mwmbl 在 D-R2-5 获受限合同签名**（个人非商业本地缓存 + links_only，匿名 30/天、1000ms 间隔、并发 1，`expires_at=2026-12-13T00:00:00+08:00`，`approved_by="Codex:codex-2026-11-15-post-s0-review:D-R2-5"`）。**签名在 CL-30 闭环前不构成激活许可**；激活批次按 D-R2-7 有条件许可（mwmbl + wikipedia_en/zh 三源、同步 default=true）。
- 执行记录见 [落地批次交接](reviews/response-codex-2026-11-15-post-s0.md)。

## 正在进行的批次

- **PR #26（CL-30，work/cl30-20261114，head `eba3507`）**：doctor 白名单化 + create_auth 先 ACL 后写入；RF1（同 fd 绑定写入，路径替换不落秘密）、RF2（核验一律 SID 级 audit_path，账户名 icacls 回退已删）、RF3（`protected=False` 判失败）已补修并各配 RED→GREEN 回归。**待第 3 轮复审，通过前不合入。**
- **PR #27（F6/F7，work/source-fixes-f6f7-20261114，head `d7ba5d5`）**：mwmbl 401 经 check 路径归类 auth_failed（含 probe 停源回归）、marginalia 503 尊重较长 Retry-After。RF6 验收通过；验证口径更正：4 个新测试中 3 个旧代码反证失败，probe 停源测试为既有行为的保护性回归。CI/D28 满足即可独立合入。
- **PR #29（S0 证据集成，work/s0-results-20261114，head `69bc4e9`）**：S0 run e61529fa 结果 + 第 1 轮裁决归档 + 7 候选草案 + F2 归位与标准回放。内容已于第 2 轮验收（D-R2-2）。
- **PR #28（本批文档）**：状态入口与来源记录更正（本文件）。合入顺序：#27 → #29 → #26（复审通过后）→ #28。
- marginalia 维持禁用（D-S0-2 前置未动：个人 key、真实成功 S0、独立签名）；走 mwmbl 的 S1 路径不要求先申请 marginalia key。

## 下一步与用户待办

1. 第 3 轮复审 PR #26 新 head → 通过后按上述顺序合并四个 PR。
2. 激活批次（D-R2-7）：mwmbl + wikipedia_en/zh 一次同步合同、enabled、**default=true** 与用途说明；跑准入与 links_only 回归；冻结最终清单。
3. **用户复探**（D-R2-8）：新目录、只测三个启用源、先每源 1 次：`bzs probe --sources config/sources.json --only mwmbl wikipedia_en wikipedia_zh --per-source 1 --delay 4 --out <新目录>`；每源须真实非空 ok，manifest 哈希与最终清单一致。
4. **用户完整运行 `tools/accept_s1.py`**（九条 PASS + exit 0；准备对应最终 HEAD 的 --ci-run-url；不用 --skip-live）。

## 待决事项

无新增。持续有效项：D25（诊断插桩撤除条件）、D26（S1 前测量/重构冻结）、D27（Python 3.13 CI 时机）均为**持续/条件性规则，非已完成事项**；D28 全尝试核对为持续义务。转私有仓库 + SearXNG 引擎移植（用户已同意方向）建议排在 S1 签收后，盘点见 work/searxng-port-prep-20261114。

## 长期规则

- **D17（决策规则）**：审核方逐项推荐+理由，推荐值即用户决定，除非用户另有指示；只有用户本人能做的事列为用户待办。不可撤回的决定（删数据、改写历史、启用来源、对外发送）暂停并交用户。
- **D20（CI 重跑规则）**：失败 job 重跑前把首轮 job 链接和失败测试名写进交接文档；核对全部尝试（`filter=all`）。新失败先查因；不以重试或排除掩盖问题（D21）。
- **集成规则（CL-01/PROC-1）**：main 是唯一集成分支；每批一个 PR、CI 绿后 1 个工作日内合并、叠加深度 ≤2；**每个改变状态的 PR 必须在同一个 PR 里更新本文件**。
- **审核流转**：审核方推新 `review/*` 只读分支或本地只读审阅；本地 agent 将裁决文件原样入库 `docs/reviews/`。
- **证据与边界**：`validation/` 按字节保存；每批摘要 ≤50KB、原始日志走 CI artifacts；不提交源码快照/密钥/私人数据；不扩展 B-24、不新增证明链、不启用抓取型来源、不伪装浏览器、不删放宽断言换绿、不改写历史。

历史入口原件见 `docs/history/`。
