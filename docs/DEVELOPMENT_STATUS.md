# 开发进度：0.1.10 接手后的五项增量

更新：2026-10-23。本轮从另一会话已发布的0.1.9 `3ef86c653a2e2085848d5cdb151d66392c6af49a`、schema12继续。真实远端main与原工作区一致，来源策略未提交测试草稿已保留并审查；基线[CI 39725326547](https://github.com/wmqfl861/bzs861/actions/runs/39725326547)的Linux/Windows成功只证明该旧输入。范围见[接手计划](handoff/S2_TAKEOVER_20261023.md)，本轮证据见[验证说明](../validation/s2_takeover/README.md)和[当前报告](../validation/s2_takeover/windows-watchdog-report.json)。

## 五项实现与实际边界

| 增量 | 实现 | 保留的边界 |
| --- | --- | --- |
| 搜索来源持久发布限制 | 显式清单激活来源代际；links_only、停用、删除、审核过期及身份变化约束旧缓存、冻结会话和在途响应；后来放宽不恢复旧摘要 | 原17来源仍默认禁用；持久规则不是独立撤销journal或真实来源批准 |
| 主体撤权 | 管理员principal.revoke持久撤销主体，全部令牌与旧数据访问失效，重复操作幂等；在途任务拒绝迟到发布并保留已知/未知计费 | 不提供重启用；MCP管理工具仅显式stdio启用；恢复连续性仍未证明 |
| HTTP缓存指令与新鲜度 | 缓存限制独立于ETag，包含Date/Age/驻留与解析耗时；cache_only拒绝陈旧，no-store禁止新持久快照，失败/304收紧失效旧复用资格 | 不实现通用Vary、瞬时no-store读取或陈旧失败回退；历史证据继续按权限读取 |
| 持久共享批读 | 同owner兼容read_many在派发时共享一个实际请求；逐订阅者取消，最后订阅者退出才取消Worker；付款任务只计一次，回执与归属可重启/压缩保留 | 只覆盖派发时已排队的兼容批读，最多32成员；直接读取/crawl/search和迟到订阅未合并，B-24未完整签收 |
| 搜索执行预设 | search.modes可查看fast/balanced/deep；核心限制来源数、候选/结果数、超时及期限；覆盖只可收紧，返回有效预算和省略来源 | scope=search_only；read/model/browser预算为0；模式名不保证质量或实测时延，跨重启单调期限尚未实现 |

详细接口见[来源发布](SOURCE_PUBLICATION.md)、[主体撤权](PRINCIPAL_REVOCATION.md)、[HTTP新鲜度](HTTP_FRESHNESS.md)、[共享读取](SHARED_READS.md)、[搜索模式](SEARCH_MODES.md)和[API](API.md)。C++继续独占业务、权限、预算和数据库，Worker只做受限网络与解析，MCP不复制规则。

## 独立审查与升级

独立缓存审查保留真实反例：203和空正文crawl遗漏no-store，失败304收紧后旧缓存仍可复用，重复Pragma丢失早先限制，截断证据被误拒；还核对Worker profile RPC期间过期、提取耗时重置响应年龄。原始失败和修复后证据分别保留，固定回归进入CTest/Python与CI，不能用移除断言、盲目重试或忽略失败替代修复。

schema13仅追加来源规则、主体撤权及共享请求结构。真实基线0.1.9静态库新建schema12数据库，再由当前库升级副本；39张旧表的旧列逐表比较行数及内容摘要，覆盖快照/证据、删除、来源配额、robots、local/web会话、各状态任务及压缩摘要。旧元数据不会虚构新鲜度；旧web会话需显式激活来源清单。旧、新backup继续隔离。真实升级、跨组件独立审核与作者自测的角色和输入分别记录。

## 规划与剩余验收

正式路线仍为 **S0–S9、83个工作包、161个原验收项**。MODE-01从未开始进入部分实现，汇总为 **37 partial /46 not_started /0 accepted**。原工作包、依赖、估算和验收语义保留，S0/S1/S2均未完整签收；测试数量不能折算产品完成度。

来源可用性、免费/自动访问/缓存再发布审核仍须在目标环境真实完成，至少两个可用来源中包含通用网页源。QA-02仍需真实人工qrels/裁决、来源消融、留出及硬条件/引用证据。没有新增来源或站点批准，也没有付费回退。

继续完成通用共享取消、独立撤销journal/恢复连续性、广泛长期爬虫与会话故障、跨owner公共集合、跨节点及完整依赖验收；动态页面、文档、模型研究、私人资料、订阅和Windows完整安装包按原切片推进。原黄金40条字节和20/15/5划分、原合同/锁、历史validation全部保持。

## 本轮验证与交付

作者聚焦测试、独立反例、真实旧库升级和最终整合门禁分开记录；最终实际数量、二进制与源码哈希以[当前报告](../validation/s2_takeover/windows-watchdog-report.json)为准。Linux完整CTest不允许把跳过当通过，ASan/UBSan开启泄漏检测，原合同和计划守卫保留。Windows继续执行原生与HTTP阻塞门槛，完整Worker打包仍属S9。

首次生产实现的历史 `final-v2` 冻结222个执行输入，12条最终命令运行前后均一致。Linux完整 **27/27套CTest通过、0跳过**，其中15套原生共413个用例/12,906条断言，Python为1,049项；另以 `detect_leaks=1` 完成 **15/15套ASan/UBSan**。原合同34组、计划守卫13组、依赖检查与历史保持检查通过。真实旧库建库310条断言、升级1184条断言通过；共享读取独立探针以最终库重链重放3例/99条断言通过。这些重复覆盖不累加为产品验收数量。

首轮整合的两处失败原样保留：合法1秒会话在调度时剩余不足1秒被误拒，现由内部绝对单调期限约束任意正剩余时间，原6项真实恢复E2E未修改并全部通过；另一个旧测试错误要求links_only来源发布摘要，现补齐明确允许/禁止摘要的独立fixture及双向缓存验证。独立模式审核中同样存在的错误假设已明确撤回，不能用旧报告覆盖更正后的结论。

本轮发布到wmqfl861/bzs861前核对远端父SHA并非强制更新；取回真实commit/tree后再核对该SHA的Linux/Windows。不可变发布前报告不预写未来提交或未来CI结论。

## 首次发布CI与测试清理修正

首次发布提交 `a06e7e91f6a115b345ff1072095b80e15d193ad4` 的 [CI 39945994760](https://github.com/wmqfl861/bzs861/actions/runs/39945994760) 已完成：Linux完整27/27套CTest、15套sanitizer及原合同/计划守卫通过；Windows构建成功，15套原生与http_cli通过，http_revocation的两条业务测试均在tearDown删除metadata.sqlite时报WinError32，合计16/17套通过。完整原日志保留于validation/s2_takeover/ci_followup。

根因是测试辅助函数把sqlite3连接当作自动关闭的上下文使用，实际退出只处理事务。该次HTTP测试修正显式关闭连接，并在成功与SQL异常两条路径保留实际连接引用，确定性验证函数退出时连接已关闭。该次修正保留生产代码、两条HTTP业务测试、清理错误检查、工作流及超时。新输入、聚焦验证与独立复核见[CI后续报告](../validation/s2_takeover/ci-followup-report.json)；原final-v2报告保留为原输入的记录，未改写成修正提交的全量成绩。该HTTP修正随后发布为199eb635，其实际Windows通过、Linux失败的结果见下节。

## 截止时间事件竞态修正

HTTP清理修正发布为 `199eb635ca90c657dff673fbf88bed44e7c4b40b`；Windows实际17/17通过，其中撤权HTTP四项、严格连接清理均通过，耗时340.84秒。其 [CI 39950116698](https://github.com/wmqfl861/bzs861/actions/runs/39950116698) 的Linux为26/27；原真实恢复test_06的状态及计费断言通过，但事件实际为queued/source_unknown/failed，预期为queued/failed。该日志没有记录底层超时与两种时钟的精确差值；代码分析与独立可控时钟反例另外证明，整体期限触发的失败可能在持久时钟到期前走普通来源失败分支。旧代码还有末来源E_DEADLINE误记completed的确定性反例。

本次只修两处生产逻辑：整体剩余期限限制RPC时，将类型化Worker超时归为E_DEADLINE；当前有效lease的E_DEADLINE在一笔事务中写入failed、epoch屏障、一次unknown计费和单个failed事件。更短的来源超时或提前不可达保留原source_unknown处理；旧回调、取消及撤权屏障继续生效，既有冻结版本保留。原6项真实恢复E2E和Windows连接释放测试不变，5项新原生回归进入既有CI目标。

本次改变了生产库，使用新的完整执行输入、全量门禁、真实旧库升级与独立探针重链；实际数量和哈希见[当前报告](../validation/s2_takeover/deadline-followup-report.json)。此前final-v2和HTTP清理后续报告仍只证明各自输入，不改写为本次结果。83包/161验收、37部分/46未开始/0签收及来源审批边界保持。

本次截止时间修正重新冻结229个执行输入并完整验证：27/27套CTest、0跳过，15套原生共418例/13,106条断言，Python1,049项；15/15套ASan/UBSan开启泄漏检测。完整CTest耗时171.30秒，sanitizer耗时160.40秒。原合同34组、计划守卫13组、依赖检查通过；真实schema12→13升级保留39张旧表，旧库建库310条断言和新库升级1184条断言通过；同一独立期限探针对最终库重链后6/6例、92条断言通过。这些是本次新生产输入的实际本地成绩，发布后仍核对新SHA自己的Linux/Windows。

## Windows测试进程上限与逐例进度

截止时间修正发布为 `fcb58e4679a5bbdff17319b7b4ebff7019733eab`，其 [CI 39954357148](https://github.com/wmqfl861/bzs861/actions/runs/39954357148) 的Linux27/27套、15套sanitizer、合同34和计划守卫13通过。Windows为16/17，唯一失败为crawl在45.02秒触及整个进程的45秒watchdog；旧输出缓冲使已完成用例数未知。另14套native明确完成364例/10,308条断言，search_modes22例/382条断言、HTTP20项与撤权HTTP4项均通过。该次Windows总耗时497.91秒，不能把未完成crawl计入通过。

45秒上限沿用自该套件35例时，当前54例，前两次实际Windows完成分别为43.00和40.36秒。本次core44.80/45秒和site_publication56.88/60秒也接近上限，因此只为Windows将core设为90秒、crawl及site_publication设为120秒。全部保留有限阻塞式超时；原要求没有将此进程watchdog定义为产品性能阈值，业务期限、SQLite持久化配置和正式性能验收要求保持。多个套件耗时增加是观察结果，没有同机对照可确定宿主原因，也不能从超时日志排除某个crawl用例停滞。

crawl仅增加chrono计时与main逐例立即刷出的RUN/PASS，原54个测试函数、fixture、断言、注册顺序与失败退出语义保持。新冻结230个执行输入，本地Debug为54/54例、2,798断言（5.64秒），ASan/UBSan为同54例/2,798断言（20.79秒），开启泄漏检测。生产源码及Debug/ASan生产库、searchd哈希保持；本轮没有把历史完整27套、15套sanitizer或真实升级冒充新执行。Linux生成后的27套属性保持，实际Windows90/120/120秒与新SHA的完整双平台结果仍须在发布后核对。当前证据见[watchdog报告](../validation/s2_takeover/windows-watchdog-report.json)，既有1,325份validation原字节保留，正式37/46/0进度不变。
