# 下一位开发者的接续入口

更新：2026-10-18。当前增量为 **0.1.4 S2 crawl development candidate**，从已交付 0.1.3 `8a4e0f4cc630627ffa91c49598c66fec7fb4291d` 接续。该 SHA 是输入基线，不是最终发布提交；先检查真实远端 main、当前工作区及对应 CI。

## 阅读顺序

1. [本批交接](S2_CRAWL_20261018.md)、[当前验证说明](../../validation/s2_crawl/README.md) 和 [报告](../../validation/s2_crawl/test_report.json)。
2. [README](../../README.md)、[开发进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)。
3. [SITE_CRAWLING](../SITE_CRAWLING.md)、[SITE_DISCOVERY](../SITE_DISCOVERY.md)、[API](../API.md)、[CONFIGURATION](../CONFIGURATION.md)、[SECURITY](../SECURITY.md)。
4. [蓝图 v1.2](../planning/development_blueprint_v1.2.md)、[切片/验收映射](../planning/slices_v1.2.json)、原规格/backlog 与 [Claude S1 接收审查](CLAUDE_S1_REVIEW_20261018.md)。

## 当前边界

83 包、161 验收全部保留，34 项部分实现、49 项未开始、0 项完整签收，S0/S1/S2 均未签署完成。CRAWL-01 / CRAWL-03 本批新增部分实现；QA-02、SESSION-02 尚未开始。旧随包 `implemented` 标签和测试数量都不是整包验收。

`map.submit` 产出 `body_read=false` 的未读候选；`read_many.submit` 读取最多 20 个显式 URL；`crawl.submit` 在已审核站点范围内读取有界静态正文并保存边。正文抓取继续使用固定 BzsBot、有效 robots、精确站点清单身份与实际连接层策略。默认站点为空，17 个搜索来源仍禁用，合成 fixture 不批准生产站点。

核心持久控制 frontier、请求/字节/页预算、请求租约、同源配额/冷却、map/crawl 轮转和删除屏障。未知请求保留全部预留用量，不自动重发已完成或未知跳。暂停后重启已覆盖，不得扩大为断电或长时故障验收。边的 `followed` 表示准入前沿，不表示正文读取成功。

正文、公开摘要、当前策略与历史搜索表示分别保存。noindex 隐藏新的和旧分页中的相关结果；nosnippet/data-nosnippet 不改变 owner 显式读取的完整快照。有效限制性响应即使正文保存失败，也不能让旧索引继续暴露或被失败的宽松响应恢复。重定向链限制、历史最终 URL、站点 links_only 上限及元数据解析界限都有回归，后续入口必须接入。

40 条黄金查询原文字/ID 和 20 development / 15 regression / 5 holdout 已冻结。0.1.2 云端仅两次 DNS 失败、0 HTTP 样本；本批不重复真实探测，真实来源 S0 与人工相关性仍未完成。

## 接续顺序

1. 核对最终提交源码清单与 Linux/Windows Actions，先修实际失败。保留十套普通 CTest、五套原生 sanitizer 及 Windows 六套原生/HTTP 门槛；不得把跳过或非阻塞失败当通过。
2. 保留 schema 6 对 schema 1、两种 schema 2、schema 3/4/5 的迁移、隔离拒绝、旧证据/任务与删除语义。不要手工清空队列或策略表规避容量限制；通用 GC 和独立撤销日志尚未完成。
3. 继续 CRAWL-01 的专门 trap 策略、长期队列/容量/故障矩阵与 CRAWL-03 的完整刷新/发布验收；已有深度/查询变体保护不能替代原 FT-028/FT-029 全部要求。冻结搜索表示不得只依赖可变的当前重定向目标。
4. 按 [S0_PROBE](../S0_PROBE.md) 在目标部署环境取得真实可用性证据，并独立审核免费/自动访问/缓存再发布与有效期。至少两个可用来源包含真实通用网页源后，仍需逐项判断全部 S1 标准。
5. QA-02 建立真实人工 qrels、评分 runner、预冻结阈值和留出评测，区分漏标、来源贡献、解析错误与网络失败。解析回放一致性不等于相关性成绩。
6. SESSION-02 的去重/断线重取/取消/过期/背压，以及独立撤销日志与隔离恢复继续按等效语义推进；没有撤销连续性不允许恢复服务。保留后续文档、多媒体、用户模型、私人资料、订阅与完整 Windows 交付。

## 长期约束

业务与权限只维护一套 C++ 核心，HTTP/CLI/MCP 映射同一操作，Worker 不写数据库。生产代理、远程 DNS、allow_cidrs 放宽仍拒绝，回环映射仅用于显式测试。不得付费回退、绕过封禁或验证码；用户模型配置不授权网页访问内网。

原 v1.1、规格、Schema 与历史验证保留原状。规划保留全部 ID、验收文字、依赖和机制替换等效要求。私人输入、凭据、运行数据库和原 bundle 不公开。报告不预写未来自身 SHA 或未执行的 CI；发布后核验精确 commit/tree 与工作流。
