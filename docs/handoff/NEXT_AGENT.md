# 下一会话接手顺序

更新：2026-10-23。当前增量是0.1.10，唯一推送目标为[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。先读根目录HANDOFF_START_HERE.md、[当前进度](../DEVELOPMENT_STATUS.md)、[接手范围](S2_TAKEOVER_20261023.md)、[当前截止时间报告](../../validation/s2_takeover/deadline-followup-report.json)和[机器状态](../implementation_status.json)，再核对包含报告的实际提交及其CI。原[生产实现报告](../../validation/s2_takeover/final-report.json)和[HTTP清理报告](../../validation/s2_takeover/ci-followup-report.json)作为历史输入保留。

## 当前基线与接口

本轮接手基线为0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`，tree `d5219c5d58e8dfef48a97f08df5b71dbc9b538f1`，CI39725326547成功。当前schema13组合来源发布、主体撤权与共享批读。真实schema12建库升级的可重放探针、旧39表摘要和备份隔离证据在validation/s2_takeover/migration，不能用手造旧表或修改user_version替代。

五项合同分别见SOURCE_PUBLICATION、PRINCIPAL_REVOCATION、HTTP_FRESHNESS、SHARED_READS、SEARCH_MODES。主体撤权是持久且不可重启用的管理员操作；MCP默认不暴露，显式--admin-tools仅允许stdio。旧缓存、会话和在途提交需要重查来源/主体/页面/删除限制。来源后来放宽不恢复旧结果，主体撤权也不随来源重启用而恢复。

缓存的原始限制独立于validators。no-store无新持久快照；截断证据可以保留但不作为新鲜缓存；cache_only不访问来源，无证明则拒绝。profile RPC后再检期限，提取不能重置响应年龄。共享读取只覆盖同owner且派发时已排队的兼容read_many，付款者取消不取消其余订阅，实际/未知用量只计一次。未知执行不自动重发。

搜索模式只执行搜索，fast/balanced/deep返回实际配置上限与omitted_sources；所有读取/模型/浏览器预算为0。旧无mode请求的normalized JSON与幂等身份保留，不能为了扩功能悄悄改变已有key。

生产实现的原本地冻结输入是validation/s2_takeover/final-v2-execution-inputs.json（222个文件）；27/27套CTest、15/15套ASan/UBSan及Python 1,049项通过，无跳过。原1秒真实截止时间E2E保持字节一致；公开参数最小1000毫秒不代表内部剩余预算也必须1000毫秒，任何仍为正的余量由绝对steady-clock期限收紧。旧mode reviewer的相反假设已在search_modes_deadline_review明确撤回。首轮失败不得删掉或改写为通过。

首次发布a06e7e9的CI39945994760中Linux通过，Windows构建成功、16/17套通过；http_revocation两例均在清理metadata.sqlite时遇到WinError32。Python SQLite事务上下文退出不等于连接关闭，该次HTTP修正只将rows()改为显式关闭，并在同一CI测试脚本加入成功/异常两条连接释放回归。该次保留生产代码、两条撤权HTTP业务测试与严格清理；对应冻结为ci-followup-execution-inputs.json。原v2不改写或冒充新测试输入，该HTTP修正发布为199eb635，Windows实际17/17通过，包含四项撤权HTTP测试；该成绩不代替其后的生产修正。

HTTP修正提交199eb635的CI39950116698又在Linux原恢复test_06出现queued/source_unknown/failed事件竞态。当前整体期限限制的RPC超时归为E_DEADLINE，并由Store对有效lease原子终止且只计一次unknown；普通较短来源超时仍可在原预算内继续尾部。原E2E字节不变，新5项native和独立6场景验证末来源、取消/撤权/旧epoch、冻结版本及普通错误对照。以deadline-followup-execution-inputs.json和对应报告绑定的新生产库为准；旧final-v2及HTTP聚焦成绩不代替当前全量验证，前两次失败不得改写。

## 接续开发

先修当前精确提交上任何真实失败，再按原蓝图扩展剩余S2工作。完整B-24仍需直接读取、crawl/search、迟到订阅与更广取消矩阵；AUTH-01仍需独立撤销journal、恢复连续性及远程多客户端隔离；MODE-01后续读取/模型能力必须有真实实现与独立预算，不可仅改标签。

QA-02先取得真实人工标签/裁决与目标环境证据，冻结方法/阈值后才比较质量；不得AI填人审、看holdout调参或用合成成绩冒充真实消融。S0保留真实来源审核/实测门槛；广泛长时、物理断电、跨owner集合、跨节点与依赖完整性不由有限回归代替。

## 验证与发布约束

保留原83包/161验收/10切片、依赖、估算与验收含义；当前37部分/46未开始/0完整签收。17搜索来源默认禁用、站点为空，原黄金字节及20/15/5划分不变。业务逻辑不加入平台系统调用；Core/Worker/MCP职责不变，独立撤销连续性未证明则备份继续隔离。

新source_publication/search_modes/cache_freshness/revocation原生目标须留在Linux sanitizer与Windows门槛；完整Linux CTest包括真实缓存/撤权HTTP及原恢复套件。缺依赖、跳过或不完整日志不能记作通过。按源文件与二进制哈希记录作者、独立、升级和最终执行，保留原失败。

所有历史validation、v1.1规格/backlog/schema/锁不改写；不用新的报告冒充旧运行。以准确父SHA非强制推送，远端前进先审差异；获取实际tree对齐本地，再查该SHA的Linux/Windows。公开仓库不含私人聊天、凭据、运行数据库或未批准外部原始内容。
