# URI 删除身份与 MCP 来源说明接续

本批以 `ef45490c6a0c384d8ad89452e2ba7e4df2cf2340`（tree `5a9977f4ec6d5aa6efa276ec1197e58b2777ff06`）为基线。
基线 [CI 40096268557](https://github.com/wmqfl861/bzs861/actions/runs/40096268557) 的 Linux/Windows 已核对成功，只证明旧输入。
审计分支 `review/claude-2026-10-24` / `30f058557e3f7ebb873e9780759323b49cfe9636` 保持只读，没有合并。

## 本批范围

- 新增 schema14 的 `documents.deletion_key` 与 `(owner,deletion_key,id)` 索引，在事务内回填全部旧行，包括删除墓碑。
- 仅统一 unreserved percent 字节、主机/默认端口及有限 URI 语法；保留 reserved 编码、查询结构、owner、scheme 与非默认端口区别。
- 直接和跳转 HTTP 缓存、条件基底及在途提交复核同一删除身份；旧精确直达无 fence 表示以保守序号0检查。
- MCP 只修正部署来源说明，保留工具名称、schema、路由和来源政策。

合同见 [URI 删除身份](../../docs/URI_DELETION_IDENTITY.md)。本批不合并文档、快照、证据、发布键或永久幂等身份，不提供备份恢复流程。六项保留决定、17 来源/0 启用/0 站点、83 包/161 验收/10 切片与37 partial/46 not_started/0 accepted保持。

## 原失败与兼容修正

新增第一组正式回归对真实 ef45490 库运行：原51组缓存测试通过，新增8组有7组失败；包括反向编码漏拦截和 reserved 编码误合并。原源、命令和日志单独保存。

首个候选补齐删除检查后误拒原有无 fence 直达缓存，`author/cache-first.log` 保留该真实逻辑失败；修正为只有精确直达缺 fence 才以删除序号0检查，任何等价删除历史仍拒绝。原兼容测试没有改写。

随后新增的无 fence validator 夹具遗漏一直必需的 `site_publication_generation`，`author/cache-corrected.log` 保留3个夹具失败。修正只通过已有公开 API 填入合法站点代际，缺 fence 条件和全部断言不变。

## 验证记录约定

作者聚焦、独立原探针、真实旧库迁移和最终完整门禁分别绑定实际源码与二进制，不累计成产品验收数量。`run.py` 逐命令保存输入哈希、完整输出字节、退出码和前后二进制；同名记录不能覆盖。旧 validation 均保留原字节。

最终结果、独立审核与发布状态在本目录完成记录中归档。远端准确 SHA 和双平台 CI 必须在非强制发布后实际查询，不能由基线或本地测试推断。

## 最终本地结果

机器可读结果为 [final-report.json](final-report.json)。完整 CTest 原始输出使用 [ctest-detail.log](final/ctest-detail.log)，与运行目录的原始 `LastTest.log` 字节相同；JUnit 的 `system-out` 有 CTest 默认截断，不能用它推算完整断言数量。

| 门禁 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整 Debug CTest | 27/27 通过，0 失败、0 跳过、0 禁用 | [命令与输入绑定](final/ctest-command.json)、[JUnit](final/ctest.xml) |
| CTest 中的原生测试 | 15 套，507 组、16,106 条断言 | [完整原始输出](final/ctest-detail.log) |
| CTest 中的 Python | 1,281 passed，18.82 秒 | 同一原始输出中的 `python_worker` |
| 缓存专项组成 | 63 组、1,323 条断言，含原51组及新增12组 | 同一原始输出中的 `cache_freshness` |
| ASan/UBSan | 15 个目标全部通过，保留泄漏检测；同样507组、16,106条断言 | [逐目标记录](asan/)、[总报告](final-report.json) |
| 原设计合同夹具 | 34/34 | [合同结果](final/contracts.log) |
| 原计划与守卫 | 83包、161映射、10切片结构有效；守卫13/13；产品验收执行为0 | [计划核验](final/plan.log)、[守卫](final/plan-guard.log) |
| 依赖一致性 | `pip check` 通过 | [依赖检查](final/pip-check.log) |

Python、原生及缓存数字是27套 CTest 的组成，不能相加为更多套件。Sanitizer 与独立探针重放的是相同或重叠场景，也不增加产品验收数量。

最终 sanitizer 归档校验发现，首次 `bzs_contract_tests` 虽记录退出0，其日志在最终核对时为0字节，与原记录的 SHA 不符。原因未确定；[原记录](asan/bzs_contract_tests-command.json)、空日志及[完整性问题](asan/contract-log-integrity-issue.json)均保留，该记录不计作完整成功证据。以相同二进制、夹具、输入与泄漏检测设置执行的[独立重跑记录](asan/bzs_contract_tests-replay-command.json)通过55组、269断言，完整日志 SHA 已核对。最终15个目标由其余14份完整原日志与这份重跑记录构成，不重复计数，也不重建原日志。

## 独立复核与真实升级

[独立结果](independent/review-result.json)与[完整 CTest 后复核](independent/post-ctest-review.json)由未修改生产实现和正式回归的审核者生成，范围结论为通过，未发现阻塞项。

- 从真实基线 Core 创建 schema13 数据，再由新 Core 升至14；47张普通/FTS影子表的全部旧列摘要保持，两个虚拟接口由其影子数据覆盖。17条历史文档（含2条墓碑）的派生键正确。
- 二次打开、跨句柄新增及删除/重导入、旧二进制以 `E_SCHEMA_VERSION` 拒绝、隔离备份仍以 `E_RESTORE_QUARANTINED` 拒绝、迁移故障事务回滚及修复后升级分别验证。查询计划使用有序索引，没有临时排序。
- 原始六场景探针源码保持：真实旧库为209条实际检查、2个失败；最终为250条检查、0失败。失败会提前停止场景，不能把检查数增长解释为新增场景。
- 旧直达无 fence 的7场景为119条检查、0失败；534个 URI 向量全部通过，覆盖256种字节转义及大小写、幂等与 reserved 不等价对照。相同场景也通过独立 ASan/UBSan，不能重复累计。
- MCP 说明专项124项通过，普通22项/管理员23项工具的名称、schema、annotations保持；只改三项工具描述与服务器说明，工具发现过程没有 Core 或来源请求。

独立首轮 sanitizer 的受限环境 LSan/ptrace 退出1与之后相同四个冻结二进制的原生成功分别归档，泄漏检测没有关闭。MCP 受限运行停滞后中断的原记录同样保留；正式124项成绩来自后续原生运行。生成的本地测试 ELF 曾缺少执行位，只修复生成文件的 mode，前后内容哈希保持；该准备过程不算测试通过。

## 输入、历史与范围保留

[最终运行后输入](final/post-run-inputs.json)在完整 CTest、15个 sanitizer 目标及合同日志补跑全部结束后生成。224项输入、36项构建产物均有哈希。相对 CTest，只有[已记录的四份 Markdown 修正](final/documentation-after-ctest.json)发生在 CTest 结束后、sanitizer 开始前；生产、测试、构建和配置字节保持，最终 sanitizer 输入与运行后快照完全相同。

`run.py` 的逐命令快照不直接包含旧 validation 目录。CTest 使用的历史 `probe_http_policy.py` 通过[运行前独立冻结的2,793项清单](old-validation-before.json)、真实基线 Git blobs、[补充绑定复核](independent/supplementary-input-binding-review.json)和最终运行后逐字节核对绑定；其两个导入的测试脚本已在常规快照内。这是另外采集的补充证明，不冒称由 runner 当时直接捕获。

2,793项历史 validation 与原 feature JSON 字节均保持。原规划、黄金集、配置、CI、构建预设和锁文件没有修改。新证据的[首批复制清单](evidence-copy-manifest.json)与[补充清单](supplementary-evidence-copy-manifest.json)分别保留；不提交临时数据库、二进制或静态库。

升级前必须停止所有旧 Core。历史快照/证据的按ID访问继续遵循原权限，本次不把所有别名合并删除；完整 B-24、备份恢复连续性、真实来源/人工质量验收及更广长期场景仍按原计划推进。当前仍为37 partial、46 not_started、0 accepted。

## 远端提交与 CI

本地报告只声明已经完成的本地执行。发布使用准确父 SHA 非强制更新 `wmqfl861/bzs861`，再核对远端实际 commit/tree 及该 SHA 的 Linux/Windows。实际发布回执在取得远端结果后另存；此前的基线 CI 不作为本批成功证明。
