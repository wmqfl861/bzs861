# 下一位开发者的接续入口

更新：2026-10-22。当前范围为 **0.1.9 S2 五个接续增量**。本轮历史输入是已发布0.1.8 `2c536e9834c4897b0c70e3bd6651b765cc67f45e`，schema11；当前schema12。开始时先读取真实远端main、工作区、准确源码树和该SHA的Linux/Windows CI，不能把历史输入当当前HEAD。

## 阅读顺序

1. [本轮接续计划](S2_NEXT_FIVE_20261021.md)、[验证说明](../../validation/s2_next_five/README.md)、[最终报告](../../validation/s2_next_five/json-race-report.json)及独立复核记录。
2. [条件重验证](../HTTP_REVALIDATION.md)、[会话故障](../SEARCH_SESSIONS.md)、[来源贡献与外部消融](../RELEVANCE_EVALUATION.md)。
3. [任务保留](../JOB_RETENTION.md)、[调度](../TASK_SCHEDULING.md)、[页面发布](../SITE_PUBLICATION.md)、[正文爬取](../SITE_CRAWLING.md)、[API](../API.md)。
4. [开发进度](../DEVELOPMENT_STATUS.md)、[机器状态](../implementation_status.json)、[蓝图](../planning/development_blueprint_v1.2.md)、[83包/161验收映射](../planning/slices_v1.2.json)，再查原规格/backlog和历史交接。

## 已有实现与不可放宽的约束

本轮五个增量是页面304、robots重验证、真实会话故障、外部来源消融前置和有限容量/公平性循环，都属于已有partial包。83包/10切片/161验收保留，36 partial /47 not_started /0 accepted不变。

304绑定同owner、精确URI/profile、当前快照与完整元数据身份，沿用原完整正文发布序号/站点代际。派发与提交时合并当前publication，包括失败或在途观察到的nofollow；crawl按实际保存快照的follow决定旧链接扩张。新完整200可建立新表示，旧快照、证据和冻结会话不因此恢复。并发替换、删除、错validator、跳转后304或无绑定响应不能额外补抓绕过原授权。

robots只检查最新决策再判断新鲜度；过期rules只能作为重验证表示，不能直接放行内容。回收若仍存在较旧记录，最新拒绝必须保留；旧引用都退役后可在有界循环中一起回收。schema11旧行迁到12默认无validators、0新增元数据用量，完整200前不猜测条件身份。真实旧库升级证据与隔离backup拒绝已分别保留。

jobs.compact只压缩至少七天后的稳定终态明细，key/指纹/计费永久保留。活动/暂停/未知预留未结算不压缩；robots准入和回执pin都生效。每owner10000/部署50000永久任务身份仍有硬上限，不能删键或重置配额、水位、调度序号绕过。持久轮转保证合格租约机会，不保证固定秒数网络完成。

来源会话提供冻结版本、版本游标与元数据事件。真实故障用例覆盖Core/Worker kill、响应/ACK丢失、取消晚到和真实期限；未知执行不自动重发。在途有限RPC可结束，但取消后不得发布。4MiB背压、事件/会话到期和有限循环是对应测试范围，不称任意断电或长时验收。

QA-02的外部消融输入必须绑定实际base/ablation排序、来源/逻辑服务/索引族、参数/环境与完整查询运行摘要。nDCG保持整个冻结候选池IDCG；缺源/失败/缺失不当作empty，不在Python重建生产RRF。没有真实人工标签/裁决和独立质量证据，完整release_gate保持blocked，不能AI填等级、虚构人审或看holdout再调参。

## 下一步

先核对最终提交的实际CI与冻结输入，修真实失败。新revalidation/capacity_cycles原生目标须继续在Linux ASan/UBSan和Windows阻塞门槛；e2e_session_recovery/e2e_revalidation继续在Linux完整CTest。依赖跳过不能算通过，作者候选不替代独立复核或最终整合结果。

按原计划继续CRAWL-01/03广泛网站和长期容量/公平性、剩余HTTP缓存/共享取消B-24、公共集合等效验收。SESSION-02继续真实长时、完整故障矩阵及S5场景。QA-02接真实标签与目标环境，先冻结方法/阈值再跑可比基线，取得真实来源消融、留出、硬条件和引用证据。S0真实来源审核/实测、独立撤销连续性及隔离恢复仍是实际门槛；后续动态页、文档、模型、私人资料、订阅和Windows安装包按S0–S9推进。

## 长期边界

40条黄金查询原文/ID和20 development、15 regression、5 holdout保持；当前工具无留出运行开关。17来源默认禁用、站点为空，无新增批准或付费回退。合成fixture审核不是生产来源批准，0.1.2云端DNS失败不冒充本轮或用户机器结果。

C++独占业务、数据库、权限、预算与调度；Worker负责受限网络/解析并独立准入，MCP只映射严格参数。业务代码不直接使用POSIX/Win32；真实SIGKILL仅是Linux故障测试。页面水位、任务摘要、会话事件均不替代独立撤销journal，没有连续性证明仍隔离备份。

历史validation目录、v1.1规格/backlog/schema/锁不改写；不删减工作包或验收、不公开凭据和运行数据库。发布仅到wmqfl861/bzs861，以准确父SHA非强制更新；取回实际commit/tree对齐本地，再查该SHA的Linux/Windows，不能预写未来自身SHA。

## 0.1.9发布后的并发修正

先读最新json-race-report、json-race-execution-inputs和json-race-source-manifest；final-report与ci-followup-report分别属于首次发布和Windows测试修正，不能替代当前输入。c14b3c3的Windows12/12通过，但Linux真实ACK恢复发现共享json-c竞争。当前统一同步native读取、序列化返回缓冲复制及全部引用生命周期，两个并发回归进入core/sessions；共享树修改和同一C++句柄重赋值需调用者同步。

当前完整CTest19/19、Python984、11套sanitizer通过，core82案/714断言、sessions22案/285断言。真实恢复文件保持原字节，不能通过重试、修改断言或移除真实期限隐藏并发问题。独立同源3案及真实新建旧库升级已绑定最终库；默认沙箱网络限制的失败和允许回环后的完整成功分别留档。
