# bzs861 全仓审核报告 — 2026-10-31（AUDIT_PLAN_ONLY）

## 审核基线与方法

| 项 | 值 |
|---|---|
| 审核输入（冻结） | `86bc23e1ba91042070d69ba2c48704cd9b2c5653`（worktree `C:\bzs861-audit`，工作区干净，`git rev-parse` 三方一致核对） |
| 输入在开发链中的位置 | main(`1d8e80e5`) → #5(`d292235`) → #6(`b126344`) → #7(`8a7d552`) → #8(`0c47a5f`) → #9(`4082b86`) → #10 链(`13591de`/v2) → **83d45cd** → 本审核输入 `86bc23e`（PR #11 头） |
| 远端默认分支 | `main` = `1d8e80e5`（不包含上述任何开发成果；所有 PR 均 draft/open） |
| 本轮模式 | AUDIT_PLAN_ONLY：只读审核 + 隔离目录运行既有构建/测试；未修改任何业务代码，未 commit/push |
| 审核执行者 | 主审（GLM-5.3 会话）+ 3 个并行只读子代理（真实独立 Agent 工具执行，各自 `git rev-parse` 冻结同一 SHA 并记录身份）：①查询/索引/HTML ②服务/安全/数据 ③构建/CI/规划 |
| 历史线索来源 | 2026-10-29 交接包 AUDIT_REPORT/FOLLOWUP_REPORT + 用户提示词 A01–A08；全部在当前代码中重新定位验证，不照抄旧行号 |

无 AGENTS.md（根目录与全树均无）；工程说明以 README.md、docs/SECURITY.md、docs/planning/CURRENT_EXECUTION_20261028.md 为准。

## 能力五分类总览（详见 VALIDATION_REPORT.md 与各发现）

| 分类 | 代表项 |
|---|---|
| 已实现且本次验证 | 本地导入/搜索/证据（CTest core 116/116 等）、查询语法（term/phrase/literal/CJK/字段/布尔）、候选-谓词一致性（暴力等价测试通过）、引号操作符分类、HTML 子树提取、批读/共享/取消、来源发布与撤权、任务回收、HTTP 执行池、配置合同（source-manifest 双侧强制）、SBOM 工具链 |
| 已实现但未验证（联网/长期） | Worker netguard 全栈（DNS 钉扎/私网拒绝/逐跳重验证/预算/robots）——仅回环夹具覆盖；探测/回放工具端到端审核列为未完成；crawl/map 长时 soak |
| 部分实现 | 联网搜索（17 来源注册/0 启用，D1 未决）、爬虫/站点发现（离线全绿、完整验收未做）、评测（模板冻结已做、人工标签阻塞）、隔离备份（隔离正确、恢复未实现）、排名（BM25+优先层+有界兜底，质量无基线） |
| 未实现 | 模型运行时（models 在 not_implemented）、向量检索、动态 JS/PDF/Office、完整研究工作流、远程多客户端发布、Windows 安装包 |
| 受阻 | S1 来源签收（D1）、S2 冻结（D2）、恢复对账（D4）、身份保留期（D5）、抓取适配器（D6）、短语语义（D7） |

---

## 发现清单

格式：编号｜优先级｜类型｜证据。类型 ∈ {已证实缺陷, 静态推导风险, 已声明未实现功能, 验证不足, 已修复确认}。"未运行"= 本轮未执行复现，仅有静态证据。

### F-01｜P2｜已证实缺陷（未运行复现，静态路径完整）
**backup.create 在请求线程上同步持 Store 互斥做全库复制**
- 当前 commit：86bc23e；`src/service.cpp:368-377`（dispatch 内联调用）→ `src/store.cpp:1963-1964`（`std::lock_guard lock(mutex_)`）→ `store.cpp:1989`（`sqlite3_backup_step(b,-1)` 全量）。
- 触发条件：管理员令牌调用 `backup.create`，库较大时其他需要 Store 互斥的请求线程排队。
- 最小复现（未运行）：并发 `search.once` + `backup.create`，观察前者时延被拉长；代码路径无争议。
- 期望与实际：期望重活不在业务请求线程长持锁；实际事件循环已解耦（F-08/A04 主体已修），唯备份残留。
- 影响：备份期间队头阻塞其他请求（健康检查不占业务槽、控制槽独立，不受影响）。
- 修复建议：迁入单槽维护执行器，或分页 `sqlite3_backup_step(N)` + 期限检查。
- 验收条件：备份进行中 `/healthz`、`web.read.cancel`、`search.once` 不超既有期限；并发负载 p99 不受影响。
- 置信度：高。

### F-02｜P2｜验证不足（非漏洞声明）
**出站安全栈已实现但零联网验证；TLS 策略隐式依赖 aiohttp 默认**
- `python/bzs/worker/netguard.py:31-140`（仅全局 IP、IPv6 嵌套递归拒绝、DNS 连接时钉扎 `GuardedResolver`）、`fetch.py:249-257`（resolver 接入连接器、trust_env=False）、`fetch.py:239-242`（代理硬拒）、`fetch.py:331-347,487-507`（逐跳重验证/跨源剥凭据）、`fetch.py:44-70,229,312,363,443-474`（预算/8MiB/连接期限）。全部代码级实现，**仅回环夹具测试**。
- TLS：`fetch.py/server.py` 无显式 ssl 上下文/最低版本/CA 策略（grep 仅有注释）；aiohttp 默认（系统 CA、验证开）成立但无测试证据。
- 触发条件：D1 批准任一来源启用后，首次真实出站。
- 期望与实际：期望显式 TLS 策略 + 至少一次受控真实出站验证与证据归档；实际缺两者。**不得表述为"已存在 SSRF 漏洞"**——离线原型、0 来源启用。
- 验收条件：Fetcher.start 显式 TLS 策略（最低版本/CA）；一次获批免费入口的受控出站验证存 `validation/`；在此之前所有联网能力保持"回放通过、线上未验证"标注。
- 置信度：实现状态高；aiohttp 默认行为中（未运行验证）。

### F-03｜P3｜静态推导风险
**来源/站点 manifest 的 review 字段是程序性信任**
- `src/web.cpp:288-295`（运行时↔合同身份绑定）与 `src/contracts.cpp:416-418,542-564`（approved 语义）均只校验字段一致性；能写 `config/sources.json` 者可自填 `approved_by`。默认 16/17 来源 enabled=false、S0 探测不回写审核（`python/bzs/worker/sources/__init__.py:84-88`）。
- 影响：部署卫生问题（文件写权限=审批权），非代码缺陷。
- 修复建议：manifest 附操作员签名/attestation 记录并在 `SourceRegistry::load` 验证；或文档化 config/ 权限要求。
- 验收条件：无签名/签名不符的 manifest 在 load 时以独立错误拒绝。
- 置信度：高。

### F-04｜P2｜验证不足
**排名质量无标注基线；C++ 排名器无版本化身份**
- `src/store.cpp:1290-1294`（BM25 4.0/1.0 硬编码）、`store.cpp:1300-1309`（gram/扫描层按 created_at DESC）、`store.cpp:1387`（传统命中分 stable_sort）；partial 标志诚实（`store.cpp:1353,1372,1412,1420,1426`，实测 4001 文档语料 `partial=1` 且目标仍被优先层找到）。
- 排名身份合同仅存在于评测工具（`docs/RELEVANCE_EVALUATION.md:60-67`），C++ 侧无版本串；QA-02 人工 qrels 未产出，`release_gate` 恒 blocked。
- 影响：候选预算截断是披露契约（docs/API.md:255），非缺陷；但 BM25+传统分混合的有效性无任何测量。
- 修复建议：提取版本化排名身份（权重/代码哈希）随搜索元数据输出；用黄金 40 查询集跑一次冻结评测打通基线工具链（人工标签受阻时先落 unlabeled 机制）。
- 验收条件：搜索响应携带 ranking version；一次冻结评测（标签可得时不 blocked，不可得时明确 blocked 原因=缺标签）。
- 置信度：高。

### F-05｜P3｜已证实（低危）
**SBOM 报告绑定 SHA 落后**
- `validation/sbom_20261029/README.md:6`：绑定 `1461387`（三节点计划提交），非当前 `86bc23e`；三审计提交未触锁文件（`git diff --name-only 83d45cd..86bc23e` 17 文件不含 requirements-s1-lock.txt），故锁哈希仍有效，但 HEAD 绑定过期。
- 修复建议：在下一代码提交上重跑 `tools/sbom_report.py` 刷新绑定（<5 分钟）。
- 验收条件：报告 `binding.git_commit` = 发布提交。
- 置信度：高。

### F-06｜P3｜记录性观察（不采纳为缺陷）
- Windows CI regex 有意选 31/42（排除 11 项 e2e/http_admission/python_worker），由独立 `windows-python.yml`（全量 pytest、真实进程树零跳过）补位——分工合理，非缺口；本轮本机已补全 42/42×2 配置。
- 无 `/WX`/`-Werror`（CMakeLists.txt 全树）；MSan/TSan 未配置且未宣称（CURRENT_EXECUTION:40 如实声明）。
- `tests/cache_freshness.cpp:333` 误导缩进告警仍在（历史已知，未放宽门槛隐藏）。
- vcpkg 基线仅 ci.yml:88 一处锁定（本地与 CI 一致，风险低）。

### F-07｜已声明未实现功能（by design，D4 阻塞）
**恢复对账未实现 → 备份隔离保持正确**
- 三重隔离门：`store.cpp:275-277`（启动拒绝）、`maintenance.cpp:67-68`（离线转换拒绝）、`maintenance.cpp:194-201`（只能收紧不能清除）；无任何恢复路径（grep restore 仅拒绝与 `backup_restore_performed:false`）。
- 回收只删任务明细/robots 记录，永不删用户正文凑空间（`retention.cpp:158-163`，摘要配额检查 `:151-154`）。
- 缺失：删除/撤权日志 + 恢复对账（docs/JOB_RETENTION.md:99 自认）。在 D4 回复前隔离是唯一正确姿态；不签署"可可靠恢复"。

### F-08｜已修复确认（历史 A01/A02/A03/A04 主体，关闭线索）
- **A01 候选-谓词一致性：已修**。三层候选（FTS 词级超集 `query.cpp:288-325`、gram 必要条件超集 `query.cpp:353-399`+overflow 标记、无表达则全量有界扫描 `store.cpp:1306-1309`）+ 无条件精确复核；暴力等价测试 `store_candidate_predicate_agreement`（unit.cpp:545-571）等**本轮已运行通过**。旧例 phrase/"app"↔apple、term/hello↔中文hello 均被覆盖。
- **A02 引号操作符：已修**（`16f1f7f`+`83d45cd`）。词法器共享分类（`query.cpp:434-446`），全引号词为字面量、未引号前缀（含负号/括号/引号操作数）拒绝；`intitle:"C++ guide"` 合法字段短语保留；AST 对象路径无操作符解释。测试 116/116 **本轮已运行**。
- **A03 HTML 子树：已修**。`walk_node` 只访所选子树（`html.cpp:67-93`，不随 `p->next`），`<main>…</main><aside>广告</aside>` 专项回归 `html_selected_subtree`（unit.cpp:436-441）**本轮已运行通过**。
- **A04 事件循环阻塞：主体已修**。IO/业务分离（`main.cpp:193-211` 回投 strand；`main.cpp:259-267` 三池准入+503；`request_executor.hpp:11-76` 有界）；后台四通道（`service.cpp:399-472`）；Worker RPC 独立 io_context+期限（`main.cpp:94-143`）。残余见 F-01。

### F-09｜A06 配置合同执行链：部分采纳（拆分结论）
- **已执行**：source-manifest 在 C++（SourceRegistry 身份绑定+quota 等价 `web.cpp:247-319`、派发时 `require_review` `web.cpp:611,231-243`）与 Python（`review.py:99-114` 每次调用复查）双侧强制；manifest SHA-256 绑定 core↔worker；scope/budget 经模式预设与期限强制（`search_modes.cpp:9,24-31`、`web.cpp:557-561`）。
- **声明性未实现（非缺陷）**：model-config/search-request 仅 `contract.validate`（`applied:false`，`service.cpp:195-207`；models 列入 not_implemented `service.cpp:155-158`）——与 docs/SECURITY.md 声明一致。
- 残余程序性信任 → F-03。

### F-10｜A08 构建链：采纳为证据（全绿）
- 精确 SHA `86bc23e`：5/5 工作流 success（checks PR 41169433563 / checks push 41169374589 / WinPySafety 41169433573、41169374596 / audit-source 41169374591）；linux 10/10 步、windows 11/11 步、win-python 8/8 步全绿。
- audit-dependencies 未触发属正常（paths 过滤：本轮三提交未触锁文件/工作流）。
- 42 项 Linux 下限与 31 项 Windows 下限由 JUnit 校验脚本强制；23 个 ASan/UBSan 程序与泄漏检测在 Linux CI。

---

## 审核限制

- 三个子代理均只读；两个静态精读+定向运行既有二进制，一个含真实二进制执行（bzs_tests/gram_index/long_document_recall，绿色）；二进制无法与 SHA 密码学绑定，以源码证据为主、运行佐证。
- MSan/TSan、真实出网、长时 soak、断电矩阵：未执行（见 VALIDATION_REPORT）。
- 本轮未发现新的 P1 级已证实缺陷；上一轮六类修复（AUD-01..06）在位。
