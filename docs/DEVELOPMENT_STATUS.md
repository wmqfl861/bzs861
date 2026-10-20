# 开发进度：0.1.5 S2 crawl hardening candidate

更新：2026-10-19。本批从已交付 0.1.4 `90ca7e93f9c96a43ff8d99b237638e4aa2aa2c63` 接续，继续 **CRAWL-01 / CRAWL-03**：存量站点发布上限、持久抓取陷阱与真实崩溃/回执丢失验证。输入基线不是本批发布 HEAD；当前源码、命令、环境和实际结果见 [报告](../validation/s2_hardening/test_report.json)。

## 状态与规划

v1.2 的 C++ 核心、Python Worker、stdio MCP 优先与单用户先行安排继续生效。**83 个工作包、161 项验收 ID 全部保留；34 项部分实现、49 项未开始、0 项完整签收。S0、S1、S2 均未签署完成。** CRAWL-01 / CRAWL-03 已在 0.1.4 进入部分实现，本批继续增强；测试数量不折算为工作包完成度。

[蓝图](planning/development_blueprint_v1.2.md)、[切片计划](planning/slices_v1.2.json)、[规划说明](planning/README.md)、[机器状态](implementation_status.json) 与交接同步到 0.1.5。计划只更新执行说明，原 ID、验收文字、依赖、估算及未签收属性保留；418–720 人日仍是待重估的历史数，不是剩余时间承诺。

## 本批实际范围

| 范围 | 本批行为 | 仍需验收 |
|---|---|---|
| CRAWL-03 | 站点规则在核心持久生效；离线启动、拒绝/过期/空登记立即约束已存搜索结果；历史水位、完整URL链和冻结表示防止旧摘要复活 | 全部页面扩展、跨owner公开集合、独立站点审核和撤销连续性 |
| CRAWL-01 | `bzs-crawl-trap-1` 日期族/序列、查询维度/排列/组合、重复分页正文；持久观测、边原因和已排队页面派发前复核 | 广泛网站误判评估、完整长期容量/GC与全部FT-028 |
| STORE-01 | schema7 保存scope水位、不可变绑定、冻结行版本和trap；搜索候选立即门控，FTS物理清理每事务最多128个文档 | 历史缺失链不能还原；通用GC、独立撤销日志与安全恢复 |
| JOB-01 / QA-01 | 真正SIGKILL覆盖Worker派发前、完整回包丢失、待派发目标删除，以及任务已提交但accepted回执丢失；核对实际HTTP、预留、快照/证据和幂等 | 每条SQLite指令、物理断电、长时故障矩阵、跨节点与SESSION-02 |
| API-00 / CORE-01 | HTTP/CLI/MCP继续共享核心；返回trap策略/原因及当前site_publication；Linux加入存量发布原生套件与恢复E2E，Windows增加同一原生发布门槛 | Windows完整Worker打包和远程多客户端仍按原切片推进 |

默认站点为空，17条搜索来源默认禁用。map仍产出未读候选，read_many只处理显式列表，crawl才递归读取有界正文；本批不改变这三种合同。参数及具体规则见 [SITE_CRAWLING](SITE_CRAWLING.md)、[SITE_PUBLICATION](SITE_PUBLICATION.md) 和 [API](API.md)。

## 缺陷基线与复核

保留的0.1.4 searchd二进制与已提交构建记录SHA-256一致。新增实际进程基线中，四种离线规则收紧均复现旧索引/摘要泄漏；四个崩溃/回执边界通过，证明这部分是新增故障验证，而非把已有机制重新宣称为新实现。首次夹具的状态预期错误及修正单独留档。

分开的助手上下文复核完整中间跳转、旧实例配置、祖先scope水位、候选窗口、导入与网页表示、旧库迁移、种子跳转豁免和超界历史状态。具体发现、修复和复核方法见 [独立上下文复核记录](../validation/s2_hardening/independent_review.json)。这不是外部人工或全产品独立签收。

## 验证与升级

最终门槛为12套普通CTest、6个原生ASan/UBSan/泄漏检查可执行文件、设计合同参考、规划防回退、固定依赖与实际Linux启动器生命周期。当前执行日志、准确计数和失败修复以 [validation/s2_hardening](../validation/s2_hardening/README.md) 为准，不把跳过、中间运行或交叉覆盖相加为161项验收。

Windows对应core/batch/map/crawl/site_publication/contracts及HTTP/CLI七套阻塞测试。发布后检查精确提交的[Actions](https://github.com/wmqfl861/bzs861/actions)，旧提交成功不能代替本批CI。

schema7保留旧迁移、隔离、删除、快照和证据语义。缺少可靠完整链的历史网页结果可保守撤发布，需要新查询或完整重读；正文没有因此删除。已交付到外部的内容不能收回，站点水位不是独立撤销日志，旧隔离备份不能据此恢复服务。

历史0.1.4及更早验证目录保持原样。0.1.2的有限云端观察仍只有两次DNS失败、0HTTP响应，本批没有新增真实来源/站点/付费/模型调用。S0目标环境与独立审核、QA-02人工qrels/评分/阈值/留出评测、SESSION-02重取与独立撤销恢复仍待完成。下一批按 [NEXT_AGENT](handoff/NEXT_AGENT.md) 接续，保留原模型、文档、多媒体、私人资料、订阅与Windows交付范围。
