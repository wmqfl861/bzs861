# S0 裁决落地批次交接（复审输入，2026-11-15）

对应 [Codex S0 裁决](codex-2026-11-14-s0-review.md)"给执行方的指令"第 1–4 步。
本文件按指令顺序逐条陈述执行结果与证据位置，供复审。

## 指令 1：归档裁决和纠正状态入口

- 裁决文件已原样入库：work/s0-results-20261114 提交 `1310cc0`
  （docs/reviews/codex-2026-11-14-s0-review.md，289 行未改动）。
- `docs/CURRENT.md` 已更新（本批 docs 分支）：写明 S0 已审、四来源裁决
  （en/zh 获 links_only 激活许可、marginalia/mwmbl hold 及原因）、CL-30
  状态、S1 未签收、批次内容与用户待办。未写"来源全通过"。

## 指令 2：修复证据交付，不改证据内容（F2）

- work/s0-results-20261114 提交 `817ecef`：10 个 gzip 由 `results/` 归位
  `results/raw/`（git 识别为 100% rename，即压缩字节逐文件未变；归位后
  解压长度与 SHA-256 对 10/10 全部行再次核对通过）。
- 标准 `bzs replay` 在记录的解析器身份下运行（身份匹配 true）：
  **passed，10 replayed / 10 matched / 0 mismatches / 0 skipped，
  网络请求 0**。产物在 `validation/s0_20261114/replay/`。
- 旧 probe_report.json / probe_report.md 字节未动；未重做网络探测。

## 指令 3：实施并验收 CL-30（D-S0-14，独立小批次）

分支 work/cl30-20261114（基于 main=1897398）：

1. **doctor 白名单化**（python/bzs/winsecurity.py）：audit_path 改为
   允许名单判定——仅当前用户、SYSTEM、BUILTIN\Administrators 的
   access-allowed ACE 可含读权限；其余任意 SID（含 INTERACTIVE、Guests、
   应用包、未知账户）可读即报问题；deny ACE 与不含读位的 allow ACE 不
   视为泄漏；当前用户 SID 无法确定时整体判失败（fail closed）。
2. **create_auth 先 ACL 后写入**（tools/create_auth.py）：独占建空文件 →
   保护 → **确认**（主路径 winsecurity.audit_path 须 protected 且零问题；
   icacls 回退路径检查无继承 ACE）→ 才写秘密。任何失败移除仍为空的文件
   并报错；拒绝覆盖行为保留。
3. 回归（python/tests/test_windows_secrets.py）：白名单单元测试
   （INTERACTIVE/Guests/应用包/未知 SID 被标、deny 与无读位 allow 不标）、
   doctor 对 icacls 实加的 INTERACTIVE 读授权报警、用户 SID 查询失败
   fail closed、两个 create_auth 顺序失败分支（注入保护失败/确认失败，
   磁盘上无 token 字节）。
4. 本批 Python 全量 1879 通过（本机 .venv，Python 3.12.10）。

## 指令 4：关闭拟启用通用源缺口（F6/F7 + 缓存依据）

分支 work/source-fixes-f6f7-20261114（基于 main=1897398）：

1. **F6**：Mwmbl 增加 check() 路径的 401→auth_failed（fetch 对 ≥400 构造
   status="http_error"，原分类在 parse() 中不可达）。回归三层：真实
   search() 路径（按 fetch 构造方式的 4xx FetchResult）、check+parse
   helper、probe 服务停止路径（budget stop_reason=auth_failed、剩余尝试
   skipped 为 service_stopped:auth_failed、completed resume 不重试）。
   parse() 401 分支保留（直接 parse/回放调用者仍受保护）。
2. **F7**：Marginalia 503 冷却改为 max(600000ms, Retry-After)（由
   SourceError 构造器钳制到 24h 上限），错误消息不再写死"共享 key"诊断。
   回归：3600s 头→3,600,000ms；60s 头与无头→600,000ms。
3. 新测试全部经 RED（旧代码失败）→GREEN 验证；本批全量 1878 通过。
4. **mwmbl 缓存条款补证**（本批 docs 分支，docs/sources/mwmbl.md）：
   mwmbl.org/terms（2026-11-15 在线复核）明示数据集 **CC BY-NC-SA 4.0**
   （原文引用在记录内）；developer 页新增"配额每月 1 日重置"事实；
   无专门针对搜索 API 缓存的许可句（如实记录）。**提议口径：个人本地
   缓存（NC 范围内）+ 对外 links_only；请复审按 D-S0-3 裁定是否签署
   mwmbl 合同及具体 cache/redistribution 字段值。**

## 未做 / 边界（照裁决）

- 未启用任何来源；sources.json 全部 enabled=false 未动（含 wikipedia，
   等本批评复审通过 + CL-30/F2 合入后按 D-S0-4/5 一次同步落库）。
- 未实现 7 候选任何新适配器（排 S1 后）；未动 accept_s1.py；未做测量/
  重构（D26）；未扩展 B-24；未访问任何来源端点（仅复核 mwmbl 官方条款
  页面属条款核验，非来源探测）。
- CI：三分支已推送并开 PR（见下）；D28 全尝试核对在合并前执行并记录。
