# 下一阶段可执行计划 — 2026-10-31 @ 86bc23e 审核后

编排原则（沿用已验证有效的流程）：**计划 → 独立子代理审计划 → 并行开发（每模块独立提交）→ 独立子代理/codex 审成果至 PASS → 全量门禁 → 推送 → 准确 SHA CI 核对**。每个工作包走 复现→最小修复→回归→复验；禁止删断言/跳过必跑/放宽安全制造通过。

依赖图：N1-DIF ∥ N2-* ∥ N3-TLS →（D1）→ N4-* → N5 → N6。N0 为每轮前置。

---

## N0 基线核验（每轮开发前，~0.5 天）

- fetch 远端、核对 PR 链与有效开发线头（当前 `86bc23e`=PR#11）；确认 CI 状态；隔离 worktree；venv+锁定依赖；快门禁（合同/规划/守卫/pytest）。
- 映射：维护性步骤（无 DEV ID）。通过条件：基线 SHA、双 CI 结论、快门禁退出码记录在案。

## N1 查询语义收尾（审核确认无缺陷残留，仅补差分防线，~1 天）

### N1-DIF 随机语料差分测试（A01 规划要求）
- 映射：QRY-01/QRY-02（FT-001 系）；依赖：无。
- 文件：`tests/unit.cpp`（新增一个 TEST，或独立 `tests/query_differential.cpp`+CMake 注册）。
- 内容：确定性伪随机语料（固定种子，~2000 文档，混合 CJK/ASCII/符号/相邻语种），对每查询断言 暴力 matches() 全集 == search_impl 结果全集（预算≥语料）。复用既有 `store_candidate_predicate_agreement` 的断言骨架，参数化生成器。
- 回滚：纯新增测试，revert 单提交即可。提交拆分：1 提交。
- 失败路径：若差分发现真实缺口 → 冻结反例 → 最小修复（tokenizer/query/store 任一层）→ 回归 → 复验，不得用预算掩盖。
- 通过条件：Debug/Release 下新测试通过；CI 42 下限不降。

## N2 最小运行保障与质量基线（~3-4 天，三个独立包）

### N2-BAK 备份移出请求持锁路径（R1，P2）
- 映射：BACKUP-00/OPS-00；依赖：无。
- 文件：`src/service.cpp`（backup.create 分派到单槽维护执行器或分页步进）、`src/store.cpp`（`Store::backup` 增加分页 `sqlite3_backup_step(N)`+期限回调选项；互斥粒度评估：若 SQLite 版本支持，改读事务快照）、`tests/unit.cpp`（并发用例：备份中 search.once/health 期限不被突破）。
- 合同：无新业务 schema；备份输出形状不变。
- 回滚：行为回退=恢复同步路径（单提交 revert）。提交拆分：1-2 提交（实现+并发测试）。
- 失败路径：分页中断→清理暂存（已有 Cleanup 机制）；失败响应保持现有错误码。
- 通过条件：并发负载下短请求 p99 不超基线+容差；全部现有备份测试不回退。

### N2-RANK 排名身份与冻结基线工具链（R4，P2）
- 映射：QA-02/FT-042/C-14；依赖：无。
- 文件：`src/store.cpp`（BM25 权重提为常量并生成 `ranking_identity` 串：名称+权重+代码 SHA）、`include/bzs/store.hpp`（搜索响应元数据加 `ranking` 字段）、`docs/SEARCH_MODES.md` 或 `docs/API.md`（分层排序语义文档：词层 BM25 / gram+扫描层传统分+created_at）、`python/bzs/evaluation.py`（消费 C++ 侧身份，评测冻结时绑定）；顺手修 `tests/cache_freshness.cpp:333` 缩进。
- 回滚：元数据字段新增，旧客户端忽略；revert 即回退。提交拆分：2 提交（身份导出+文档/评测绑定）。
- 失败路径：人工标签不可得 → 评测运行完成但 `release_gate=blocked(缺人工标签)`，如实记录（验收条件允许）。
- 通过条件：搜索响应含 ranking 版本串；一次冻结评测运行产出报告（blocked 原因=缺标签可接受）；现有多层检索测试全绿。

### N2-SBOM 重绑（R9，P3，<0.5 天）
- 文件：重跑 `tools/sbom_report.py` 提交新 `validation/sbom_*/`。通过条件：`binding.git_commit`=发布提交。1 提交。

## N3 受控 HTTP 出站就绪（~2 天，D1 前可做）

### N3-TLS 显式 TLS 策略（R2）
- 映射：SEC-01/READ-01；依赖：无。
- 文件：`python/bzs/worker/fetch.py`（`Fetcher.start` 构造 `ssl.SSLContext`：最低 TLS1.2、系统 CA、显式传 `connector=`；配置位暴露 min-version/CA 路径，默认安全）、`python/tests/test_fetch.py`（单测：策略生效、拒绝弱版本仿真——用回环 TLS 夹具或上下文断言）。
- 边界：**不关闭验证迁就测试**；测试用自签夹具时通过显式 test-only CA 注入，不设全局开关。
- 回滚：revert 单提交。通过条件：策略单测过；现有 netguard/fetch 全套不回退。

### N3-EGRESS 受控出站验证（R3；执行前提=D1 已批至少一个入口）
- 内容：对获批入口做一次真实探测（复用 `bzs probe --only <id> --include-candidates` 通道），归档请求/响应/断言到 `validation/egress_<date>/`（<50KB 摘要+原始外置）；结论标注"首次受控出站验证"。若 D1 未批 → 本包受阻并保持"线上未验证"标注。
- 通过条件：探测报告含实际连接地址、TLS 版本、robots 遵循证据；无私网/DNS 异常。

## N4 首批免费来源（~5-8 天；整体受阻于 D1，前置项可先行）

### N4-SIGN manifest 审批加固（R5，P3；D1 前置，~1 天）
- 文件：`src/contracts.cpp`/`src/web.cpp`（load 时验证 sources.json 附带的操作员 attestation 记录：`config/sources.attest.json` 含来源清单哈希+日期+操作员标识；缺失/不符→拒绝 load 全部启用项）、`python/bzs/worker/sources/review.py`（同样验证）、测试双侧。
- 回滚：attestation 文件缺失时行为=拒绝启用（保守方向）；revert 恢复纯字段校验。1-2 提交。
- 通过条件：无 attestation 的自写 approved 被拒；带 attestation 正常启用；默认全禁用状态不受影响。

### N4-SRC 首批来源适配与审核记录（R6；受阻于 D1+S0 探测）
- 前置：D1 批准入围清单（建议 ddg+wikipedia；见 AUDIT_DECISIONS）→ S0 探测（既有 `bzs probe` 通道）→ 逐入口审核记录（入口/能力/条款依据=免费+自动化许可+缓存/再发布条款、审核日期、实测数据）→ attestation → 启用。
- 文件：`config/sources.json`（enabled 翻转+review 填实）、`python/bzs/worker/sources/engines.py`或`apis.py`（适配器核对：现有 ddg/bing/wikipedia 适配器已注册，需按实测修参）、`validation/source_review_<id>_<date>/`（审核记录+探测证据摘要）。
- 共享上游限流/去重/退避：已实现（shared_budget_key、per-host 信号量+间隔、冷却），仅需配置核对。
- 提交拆分：每来源 1 提交（审核记录+配置+适配修正）。
- 失败路径：探测失败/条款不满足→该入口不启用、记录原因；不得以"凑数"启用不合格来源（无合格通用网页来源→阶段目标该项受阻并如实报告）。
- 通过条件：≥2 来源（≥1 通用网页）真实搜索返回去重结果；probe 报告归档；默认配置在无 attestation 下仍全禁用。

## N5 搜索→证据闭环集成验收（~2 天；依赖 N4）

- 内容：在真实来源上执行验收矩阵 7-11 项（单源 429/超时部分失败、登录/挑战页分类、显式读取→快照/证据→本地再检索→删除/撤权影响、重启/幂等/取消）。
- 文件：`tests/e2e_web.py` 或新 `tests/e2e_real_source.py`（真实来源用例以显式标记运行，离线 CI 不跑；记录在 validation）、验收脚本归档。
- 通过条件：矩阵 7-11 每项有命令/退出码/日志；离线全量门禁不回退（42+42+pytest）。

## N6 最小接口与验收收尾（~1-2 天）

- MCP 工具清单核对只含已实现能力（现状已满足，复核）；HTTP/CLI 复用。
- N6-MODEL（可并行，无 D 依赖）：模型 profile 假模型合同——把 model-config 从"仅校验"推进到"最小运行时占位"（假模型适配器按 profile 返回确定性输出，合同 validated+applied 但标注 synthetic）**或**维持现状仅更新文档（二选一，等用户倾向；默认维持现状+文档）。
- 阶段验收汇总：VALIDATION_REPORT 更新、83/161/10 口径核对、Windows 未跑项列表、合并建议（PR 栈顺序）。

---

## 风险与未决

- D1 是唯一硬阻塞（影响 N3-EGRESS/N4/N5 与阶段主目标）；建议批次：ddg+wikipedia。
- 人工相关性标签不可得 → 排名基线 blocked（如实）；不阻塞其余包。
- Windows 安装包/跨平台门禁不在本阶段（S9）。
- 每包推送前 re-fetch；PR 保持 draft 至准确 SHA 双 CI 绿。
