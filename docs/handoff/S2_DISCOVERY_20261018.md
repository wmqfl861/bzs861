# 0.1.3 S2 robots 与有界发现交接

本批基于已交付的 0.1.2 `434a9dfa8ce26b86bf765c9d863c8607c3dabae0`，按合并后的 v1.2 蓝图继续开发。交付目标仍为 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)，用户已授权适时推送。本页描述本批功能与后续边界；实际命令、源码绑定和测试见 [报告](../../validation/s2_discovery/test_report.json)。

## 已实现链路

部署方在独立 `sites.json` 配置已审核 origin/path，核心与 Worker 通过精确文件 SHA-256 绑定身份。随包站点为空，搜索来源的审核不批准任意目标站点。启动器新安装创建空文件、旧安装缺字段仍为空；额外 Worker 参数不能覆盖共享站点路径。

`map.sites` 返回当前登记；`map.submit` 接收 1–20 个显式发现入口及有界参数，创建 `web.map` 持久任务。核心在每跳 HTTP 前持久预留预算，先获取或选择有效 robots 记录。Worker 使用固定 BzsBot 标识，在真正请求前复核审核、目标、期限和规则；HTTP 跳转只返回核心，不能在 Worker 内自动扩张请求。

Sitemap index / robots Sitemap 声明可扩张后续发现文档。Sitemap urlset、RSS 2/1、Atom、HTML/XHTML 和 owner 历史只产生候选。候选有 parent/source/depth/path_depth，标记 `body_read=false`；不生成本次正文快照、读取收据、证据或索引。XML 禁止 DTD/实体与外部资源展开，发现输入、树结构及条目数有界。

schema 5 保存队列、候选、robots 版本/摘要、单跳回执、请求/字节预留、同源请求窗口与冷却。已知用量退还未用预留；未知结果保守收费且不自动重发。任务暂停/取消、授权代际、总期限和删除代际参与事务提交屏障。历史候选保留原文档 ID，重定向目标也按当前目标代际复核。

同一核心接口可通过 HTTP、原生 CLI、MCP `list_sites` / `map_site` 调用，并复用 `get_job` / `job_results` / `control_job`。map 不接受批量读取专用的 `retry_failed`。

## 复核与执行

实现分工覆盖 C++ 持久核心、Python Worker/robots、纯发现解析器，以及 root 的集成/MCP/启动器。跨作者审查及修复见 [independent_review.json](../../validation/s2_discovery/independent_review.json)，不是外部人工或全产品独立签收。

本批重点修复了 URI 编码身份不一致、坏候选污染整份结果、当前重定向目标删除遗漏、历史别名删除失联、robots 允许缓存过期判断、跨进程回执类型不一致，以及额外参数覆盖站点文件。原生事务/恢复回归、真实回环请求计数和真实 CLI/MCP 进程验证相互补充，证据不互相替代。

测试报告保留完整 8 套 CTest、四套原生 sanitizer、依赖与规划检查及独立启动器生命周期的命令、退出码和源码身份。Windows CI 扩充原生 map 门槛；没有用 Linux 本地测试宣称 Windows 安装包已验收。报告自身不预写未来 CI 结果，发布后必须核对最终提交。

## 当前限制与下一步

ROBOTS-01 / MAP-01 为部分实现，32 包 partial / 51 包 not_started / 0 包 accepted。全部 83 包与 161 项验收、依赖及原文保留，S0/S1/S2 尚未签署完成。

- robots 当前是本项目固定策略，最大允许缓存 24 小时；无条件请求/304 更新。失效、禁止复用或无效的显式缓存元数据采用短期拒绝；没有完整搜索引擎扩展兼容声明。
- 站点发现没有自动展开 `.gz` Sitemap 归档，不自动递归读取普通页面；当前 per-path 查询变体和 Sitemap 循环控制不等于完整 calendar trap / 公平 frontier。
- 全局保留上限明确，尚无自动清除审计记录的 GC。未经撤销连续性验证的历史备份不能恢复为服务。
- 内部退休 attempt 重放可能被核心统一记录为协议错误；历史用量仍保留且不发布候选，当前核心没有自动重放该路径。这是诊断细分限制。
- CRAWL-01 正文 frontier 与 CRAWL-03 的公开索引、摘要声明/刷新/移除尚待实现；QA-02 人工标签、评分、阈值与留出评测仍未开始。
- SESSION-02 断线重取/去重/背压、独立撤销日志、安全恢复及后续模型/文档/媒体/Windows 交付均按原等效语义保留。

本批没有真实互联网产品来源请求或站点发现请求。0.1.2 的两次云端 DNS 失败记录原样保留，17 个默认搜索来源仍禁用。目标部署环境 S0 与实际审核继续独立完成。具体接续顺序见 [NEXT_AGENT](NEXT_AGENT.md)。
