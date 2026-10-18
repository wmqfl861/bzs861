# 下一批任务：0.1.3 S2 discovery development candidate

继续 [wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。用户已授权按更新后的 v1.2 蓝图修复、继续开发并适时 push。本批输入是已交付 0.1.2 的 `434a9dfa8ce26b86bf765c9d863c8607c3dabae0`；后续远端 HEAD 必须重新读取，不能把输入 SHA 当最终发布提交。

## 先读取

1. 真实远端 main、本地分支/工作区、[本批交接](S2_DISCOVERY_20261018.md) 和 [当前验证报告](../../validation/s2_discovery/test_report.json)。
2. [README](../../README.md)、[开发进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)。
3. [SITE_DISCOVERY](../SITE_DISCOVERY.md)、[API](../API.md)、[CONFIGURATION](../CONFIGURATION.md)、[SECURITY](../SECURITY.md)。
4. [蓝图 v1.2](../planning/development_blueprint_v1.2.md)、[切片/验收映射](../planning/slices_v1.2.json)、保留的原规格/backlog，以及 [0.1.1 Claude 接收审查](CLAUDE_S1_REVIEW_20261018.md)。

## 当前边界

83 包、161 验收全部保留，32 项部分实现、51 项未开始、0 项完整签收，S0/S1/S2 均未签署完成。ROBOTS-01 / MAP-01 本批新增部分实现；完整 CRAWL-01/03、QA-02、SESSION-02 仍未开始。旧随包 `implemented` 标签不是整包验收，测试数量也不是完成百分比。

新的 `map.submit` 使用独立、默认空的 `sites.json`。审核、启用、精确清单哈希、origin/path、robots 和实际连接层出站策略共同约束请求；两端不能用不同文件。BzsBot/robots 策略和缓存版本固定；没有条件 304 更新，失效的允许记录不能被继续使用。

核心持久控制队列、预算、请求租约、同源配额/冷却和删除屏障。每跳有唯一 attempt 和一次请求预留，未知响应保留全部预留用量；不会自动重发已完成或未知跳。Sitemap index 与 robots 声明可以扩张发现文档，HTML/feed/urlset 仅产出候选，`body_read=false` 不提供本次正文快照、证据或索引。历史候选必须维持原 document_id 与删除代际。

`read_many.submit` 继续处理最多 20 个显式 URL 的正文读取，失败重试需显式授权且不重置预算。不要把 map 失败转换为 read_many/web.read 自动绕过 robots；不要把 job 轮询当 SESSION-02 流式断线重取。

17 个搜索来源仍默认禁用，0 个真实站点预先批准。40 条黄金查询的原文字/ID不变，20 development / 15 regression / 5 holdout 已冻结；不能观察质量后静默改题或改 split。0.1.2 云端只有两次 DNS 失败而无 HTTP 样本，本批不重复此探测；真实来源 S0 与人工相关性均未完成。

## 接续顺序

1. 核对最终提交的源码清单和 Linux/Windows Actions，先处理任何真实失败；保留 Windows 原生 map 门槛及四套 native sanitizers，不把 skipped/非阻塞失败当通过。
2. 保留 schema 5 对 schema 1、两种 schema 2、schema 3/4 的迁移、隔离拒绝、原快照/证据/索引/任务和删除语义。不要手工清空队列或审计记录来规避保留容量；通用 GC 尚未实现。
3. 按 [S0_PROBE](../S0_PROBE.md) 用冻结输入在目标部署环境完成可用性实测，独立完成来源免费/自动访问/缓存再发布与有效期审核。至少两个可用来源包含真实通用网页源后，再逐项判断全部 S1 标准。
4. S2 的下一实现候选为 CRAWL-01/CRAWL-03：在现有 robots 与持久单跳通道上增加正文 frontier、页预算、边关系、公平队列、循环/trap 控制和断点恢复；同时实现公开索引/摘要声明、刷新与撤索引，不能以 nofollow 或旧读取 noindex 的局部行为替代全包。
5. QA-02 独立建立真实人工 qrels、评分 runner、预冻结阈值与留出评测；区分漏标、来源独有贡献、解析错误和网络失败。解析回放一致性不代表相关性成绩。
6. SESSION-02 的事件/结果去重、断线重取、取消/过期/背压，以及独立撤销日志和隔离恢复继续按原等效语义推进；无撤销连续性不允许恢复服务。

## 长期约束

业务与权限只维护一套 C++ 核心，MCP/HTTP/CLI 映射同一操作；Worker 不写数据库。生产 proxy、remote DNS、allow_cidrs 放宽拒绝，假站点回环仅在受控 fixture 中使用。不接付费搜索/抓取回退，不绕过封禁或验证码。用户配置的本地/远程模型属于后续功能，不能授权网页访问模型内网。

原 v1.1、规格、Schema 和历史验证保留原状；更新当前规划须保留全部 ID、验收文字、依赖及机制替换的等效要求。私人输入、凭据、运行数据库和原始 bundle 不公开。当前报告不能预写自身未来提交 SHA 或尚未执行的 CI 成功；发布后核验精确 commit/tree 与相应工作流。
