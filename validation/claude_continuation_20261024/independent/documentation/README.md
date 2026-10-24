# 交接文档独立审核

**结论：通过；没有发现需要修改草稿的实质问题。** 本结论绑定 `/tmp/bzs-continuation-handoff-20261024/handoff-docs.patch`，SHA256 为 `7bcee4e61ff2769b454bfabea4d0480ed8b759b9e40db75f8854ff94840dc625`。审核者为 `search_lock` 子代理，与文档作者 dispatcher 分离。

这是八份交接文档的准确性、历史保持和证据引用审核，不是重新签收产品或本轮完整 CI。没有修改根工作树、作者补丁或作者 before/after 文件；补丁仅在本目录的 `replay/` 内独立重放。[重放与字节检查](artifact-checks.json)记录具体命令、输入哈希和比较结果。

## 实现陈述

| 范围 | 独立核对结论 |
| --- | --- |
| P2-5 | 与 `1c69836` 的搜索实现及并发合同一致：精确匹配、非 FTS 评分和排序释放 Store 锁；SQL、逐篇复制及最终投影仍持锁。最终复核删除、owner、epoch、快照、发布历史和容量，失效候选标记 partial，不宣称无锁或通用时延保证。 |
| P2-6 | 与 `a9bac842` 的固定通道实现及合同一致：fixture/read_many/discovery/search 四类，map/crawl 合用 discovery，每通道一次一个租约；保留类别内轮转、原预算和撤权栅栏。没有把所有同步请求与后台请求合称一个全局并发上限。 |
| P2-7 | 对照已整合 P2-7、带 P2-6 句柄接线的 `apps/searchd/main.cpp`：IO 与业务执行分离；默认 `min(threads,32)`、显式 1–32；执行中与待开始合计不超过槽数，满额在 Service 前 503。64 连接、2 MiB 正文、8 KiB header、读写各 5 秒、health 不占业务槽、断连不取消、停止清未开始项并 join 已执行项等描述均与代码吻合。 |
| P2-9 | 与 `a30231a` 及最终 SOURCE_BACKOFF 一致：新限流 24h、parse-error 72h；合法绝对期限由同事务证明保护，未知旧 MAX 不获证明；无证明历史值按超过当前/事件时间各 365 天且事件不在未来的保守启发式处理。文档明确存量歧义和首版 `743f79d` 的回拨失败，没有声称所有旧等待已清理。 |

核看了 P2-7 正式 integration 回归和独立 8 槽饱和、断连、在途停止记录；没有把 Debug 独立探针冒称 sanitizer 运行。P2-5 与 P2-9 的原始失败、最终独立报告也与草稿的叙述一致。精确源码、合同和证据哈希见 [links-and-evidence.json](links-and-evidence.json)。本次没有为文档变更重新执行生产测试。

## 历史与保留决定

- 七个旧文档的 before 文件逐字节等于 Git `59081e14fdda765844031e13f874e588334f1a36`；独立应用补丁后八个结果逐字节等于作者 after，范围没有越出八个声明文件。
- 原 **83 项 features** 整个 JSON 数组 **122,468 字节**完全不变，SHA256 为 `f709f11425939fbfb63d526a31757013ad38e12e00122d83db672b32526f889d`。保持 37 partial / 46 not_started / 0 accepted，所有整包签收仍为 false。
- 11 个既有历史对象（含 JSON 并发后续）及其他未改顶层值保持原始 JSON 字节；11 个更新的当前元数据值全部准确保存到新增上一批历史对象。两个历史报告列表仅追加上一批 README，原条目与顺序保持。
- HANDOFF、DEVELOPMENT_STATUS、NEXT_AGENT 的原正文（标题以外）保持原字节。README 原三段介绍、原测试概述，以及 GITHUB_HANDOFF 原两段与 17 行旧表格均保留。README 原介绍块末尾少一个分隔空行，使最初的“整块含尾部空行”检查不匹配；逐段原文字节完全相同，详情明确记录于 [prose-history-checks.json](prose-history-checks.json)，不构成历史内容变更。
- 六项保留决定均继续保留。来源配置与站点配置未改且匹配基线：17 条注册来源、0 启用、0 站点。原计划仍为 83 包 / 161 验收映射 / 10 切片；未将结构有效等同于产品验收。版本、schema13、索引标记和未完成 S0/S1/S2 状态没有被重新签收。

## CI 和链接

草稿只将 `40067206826` 的成功归属于历史基线，没有提前宣称 continuation 的本地整合或未来远端 CI 通过。已核对 root 提供的本轮只读 API 结果：run 的 head 是 `59081e14fdda765844031e13f874e588334f1a36`、tree 为 `5d555fabc43180aa905e0581a301a3bafbeca1d5`，push/completed/success；Windows job `120792848850` 与 Linux job `120792849038` 都属于此 run 且 completed/success。原 run/jobs JSON 哈希和路径在链接证据报告中，未把这次文件读取写成审核者另发远端请求。

独立解析 **138 条本地 Markdown 链接**，没有意外缺失；三个产品合同绑定各自固定提交。唯一尚待 root 集成完成的目标是 `validation/claude_continuation_20261024/README.md`，这已在作者 manifest 中明确列为依赖。应用草稿没有阻断；发布交接前须随实际整合结果提供该记录，继续保留原始失败并只写真实执行与实际 CI。

## 产物

机器结论为 [review-result.json](review-result.json)。三个独立检查脚本、原始比较结果、八个重放文件和证据绑定均在本目录；[artifacts-final.json](artifacts-final.json)逐文件记录哈希。本报告不建议修改作者补丁，后续若补丁或被引用合同改变，应对变化部分重新核对。
