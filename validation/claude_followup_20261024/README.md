# Claude 审计复核与修复回执 — 2026-10-24

本批以 `44253a7`（0.1.10）重建并重跑原复现：P1-1、P1-2、P1-3、P2-1 至 P2-5 **八项均仍成立**。随后按 P1-2 → P1-1 → P1-3 → P2-1 → P2-2 → P2-3 → P2-4 的顺序修复；P2-5 原查询补丁经独立评审后接纳为局部优化。

`review/claude-2026-10-24` 的 `30f0585` 及提取的 11 份原审计材料只读，没有合并或改写。现有版本仍为 0.1.10，没有新 tag/release，也没有新的完整工作包签收。

## 逐项结果、正式回归与提交

下表为本地实际门禁。每项都完成完整 CTest、pytest 和 15 个原生 ASan/UBSan 目标；pytest 数字是参数化用例数，不能与独立探针断言数相加。完整测试文件、精确用例名和哈希见 [正式回归索引](final-issue-index.json)，本地与 GitHub 等树提交对照见 [commit-map.json](commit-map.json)。

| 项目 | 最终状态与范围 | 主要回归 | CTest / pytest / ASan | GitHub 提交 |
| --- | --- | --- | --- | --- |
| P1-2 | **已修**。MCP search_local 改用 search.once；不创建持久冻结会话、游标或收据，冻结搜索原配额保持。 | 单次查询容量、70 次真实 MCP stdio、删除/发布/撤销投影 | [27/27 · 1052 · 15](p1-2/result.json) | [`28cee59`](https://github.com/wmqfl861/bzs861/commit/28cee594fcb4315ea4a929bdf3e67d305cb8f807) |
| P1-1 | **已修**。永久身份与可回收明细容量分离；真实终态保留期压缩后可继续准入，旧键、指纹、归属和计费保持。 | 真实终态压缩、永久键重放、活动/暂停上限、撤销 owner 回收 | [27/27 · 1052 · 15](p1-1-v2/result.json) | [`34c998b`](https://github.com/wmqfl861/bzs861/commit/34c998bc4b83f51049c3393bdf95ed2bead1dc83) |
| P1-3 | **已修**。增加保持原匹配语义的子串候选索引；owner/AND 条件先交集再限额，溢出仍进入原文复核。 | 2101 文档旧目标、AND/owner 交集、overflow、真实旧库迁移 | [27/27 · 1052 · 15](p1-3/result.json) | [`03697c1`](https://github.com/wmqfl861/bzs861/commit/03697c189949e7a5de2e866e1e2388ea2049d5aa) |
| P2-1 | **已修**。允许有界、严格 ASCII 的 Accept-Encoding Vary，绑定准确请求 profile；其他选择及损坏形式仍拒绝复用。 | Vary 边界、profile/owner 隔离、304 继承、robots 请求头 | [27/27 · 1130 · 15](p2-1/result.json) | [`a36ea9c`](https://github.com/wmqfl861/bzs861/commit/a36ea9cb33a844d4a161246d7da102bb2e14cc78) |
| P2-2 | **已修**。支持有完整证明的有界 HTTP 跳转缓存和原路线刷新，保留逐跳计费、owner、CAS、删除及发布约束。 | 原 URL 命中、重启/逐跳刷新/计费、撤销/CAS、publication、8/16 KiB 边界 | [27/27 · 1173 · 15](p2-2-v4/result.json) | [`07dd85c`](https://github.com/wmqfl861/bzs861/commit/07dd85c17fa5d39fb16c7a62a7278eeb038f6e7b) |
| P2-3 | **已修**。隔离明确无关的损坏 robots UA 标识，保留分组与有效成员；自身、通配和未知损坏仍严格拒绝。 | 无关损坏 UA、混合组、空规则边界、自身/未知 fail-closed | [27/27 · 1217 · 15](p2-3/result.json) | [`db0f0d0`](https://github.com/wmqfl861/bzs861/commit/db0f0d016d74b2f3f0f998a9544a1e6c95c8d2f8) |
| P2-4 | **已修**。优先按可用声明字符集替换损坏字节，避免整页换编码；BOM、header/meta 顺序及原始 robots/XML 严格性保持。 | 损坏 UTF-8/ASCII、header/meta/BOM、Unicode 链接、原始 XML/robots | [27/27 · 1242 · 15](p2-4/result.json) | [`6aa27d2`](https://github.com/wmqfl861/bzs861/commit/6aa27d2d5c52f5dbd99c6d25e7832a3c66f08c3f) |
| P2-5 | **仍成立（已局部优化）**。接纳原补丁：一次 matches() 内每个访问字段最多 ASCII 转换一次；评分重复转换、逐叶扫描和 Store 总锁仍在。 | 字段/文档隔离、正反顺序 16 断言、ASCII-only 与符号边界 | [27/27 · 1242 · 15](p2-5/result.json) | [`51c10ab`](https://github.com/wmqfl861/bzs861/commit/51c10ab5241b9ec3cb412b710a69a70add6cac93) |

## 基线复现的必要限定

原命令、输出、退出码及构建输入见 [baseline](baseline/) 和 [baseline-findings.json](baseline-findings.json)。初次缺少 CMake 的环境失败与之后成功构建分开保存。

- P1-1 原 jobs 程序只创建未完成 queued 任务；这些任务本来就应占容量。修复后的正式回归使用真实终态、完整保留期限、压缩与再次准入，永久身份不删除。
- P1-2 保留冻结搜索 API 的原配额；MCP 本地单次搜索改用无持久会话的 `search.once`。保护性发布策略刷新仍可能写 SQLite，不能称为整个路径绝对只读。
- P1-3 原 2101 文档复现确实漏掉早期短语/符号/非 CJK 目标。新索引只生成必要候选，仍由原匹配器复核，候选和结果上限、诚实 partial 及 NOT-only 有界扫描保留。每字段 65536 上限指**不同 gram**，不是正文 codepoint 数。
- P2-2 补充真实 Store/WebService 与离线 Worker 的 25 断言基线；新支持范围是完整、有界、可证明且有正缓存期限的 HTTP 链。无证明或软跳转保持保守完整读取。

## 最终本地验收与独立复审

最终实际输入为 **220 个文件**，manifest SHA256：`d887d9e93e1b7dc5aa542fd4dc452dd1fe6c44094933276e86f876bae1f14ffd`。输入清单绑定包含七份已独立审阅交接文档的真实文件字节；最终回执只增加 validation 元数据。

[最终门禁](p2-5/result.json)：CTest **27/27、零跳过**；pytest **1242**；ASan/UBSan **15 个原生程序按原顺序**，开启泄漏检测；三项原规划守卫通过。每个命令记录输入、日志、退出状态及执行期间二进制哈希。没有放宽原 CTest watchdog 或删减 CI 目标。

[最终独立产品复审与执行绑定](independent/cross-item/final-delta/final-review.json) 未发现受审修正范围内未解决的阻断项。文档作者与其独立评审分开，见 [文档独立审核](independent/documentation-v3-review.json)；验证启动器由 root 单独审阅，见 [launcher 审核](launcher/root-review.json)。各项独立探针、真实旧库迁移及作者预检保留在 [independent](independent/) 和 [author](author/)。

独立跳转旧探针仍是 **4 例通过 / 2 例既存 reverse-percent URI 身份假设失败，209 checks**，原始失败与前后对照保留，不能称六例全过。文档已列出该 URI 等价性限制。

## Release 性能观察与 P2-5 边界

同一最终 Release 库、2000 × 200 KiB、每组单次运行，见 [记录与源/二进制哈希](p2-5/benchmark-results.json)。原复现源码未改；第二组仅把两个查询改成 NOT 形式，保留数据、owner、候选限额、返回限额和并发探测方式。

| 查询 | search | 并发 stats 最长等待 | 检索路径 |
| --- | ---: | ---: | --- |
| 原单短语 | 1 ms | 0 ms | fts5_substring_verified |
| 原四短语 OR | 1 ms | 0 ms | fts5_substring_verified |
| 单 NOT 短语 | 1704 ms | 1704 ms | bounded_scan_recent |
| 四 NOT 短语 AND | 2103 ms | 2103 ms | bounded_scan_recent |

原基线两组正向查询为 1450/2961 ms，stats 同时等待。现在正向样本主要受 P1-3 候选排除影响，不能把差异全归给小写转换补丁。NOT 观察证实总锁阻塞仍在；没有声称通用加速比例、SLA 或吞吐验收。局部保证仅为一次 `matches()` 中每个访问字段最多转换一次，`score_match()` 仍独立转换，逐叶扫描与 Store mutex 保持。

## 保留的失败与修正过程

- `p1-1`、`p1-1-v2` 和 `p2-1` 的生成 ELF 执行位丢失及启动失败保留；只修文件 mode，实际字节不变，随后以相同输入重跑完整套件。
- `p2-2` 初版在 canonical slash 编码大小不一致后中断。`p2-2-v2` 同时包含真实原 Worker aggregate-publication 合同回归与执行位失败；真实回归随后修复，原测试没有移除。
- `p2-2-v3` 的环境禁止 socket，HTTP fixture 失败且原 Python watchdog 到时；没有把这些结果计为通过。获准本地回环执行的 `p2-2-v4` 才是完整通过阶段。
- 验证启动器仅为哈希绑定的已知原生程序修复执行位；仅加生成 CTest 启动前缀，原 argv、属性、超时和实际退出码保持。产品 CMake 与 GitHub CI 未改。

## 尚未处理的事项

| 项目 | 状态 | 剩余工作 / 限定 |
| --- | --- | --- |
| P2-5 | 仍成立（已局部优化） | Store 总锁及有界扫描仍会阻塞并发 Store 操作。 本批原始及 NOT 变体 Release 复现；见 p2-5/benchmark-results.json。 |
| P2-6 | 仍成立 | 后台 dispatcher 串行执行同步工作。 结构复核；未新增并发压测，未纳入指定修复顺序。 |
| P2-7 | 仍成立 | 同步 Worker RPC 可占满 HTTP IO 线程；health 没有保留执行线程。 结构复核；不把单个慢请求等同于全池饱和，未纳入本批。 |
| P2-8 | 仍成立 | 真实隔离 SQLite 备份已存在，缺受支持的恢复及撤销对账流程。 按用户要求保持备份恢复现状，不绕过 quarantine。 |
| P2-9 | 仍成立 | 超长 Retry-After 可形成持久超长冷却；没有实施业务时长上限。 未纳入本批指定修复范围；这不是 manifest 禁用，也不是新增审批要求。 |

P2-6/7 的结论是结构复核，不冒称做过新的负载实验。早期 tail 报告的 S2 与 P2-9 表述更正见 [补充说明](independent/audit-tail-corrections.json)。P3 与蓝图架构重构不在本批修复范围。

## 保持的决定与升级方式

来源选择、审核及启用保持现状：**17 个来源，0 启用，0 注册站点**。S2 的范围、状态及是否冻结的决定、validation 继续入库、备份恢复、永久幂等身份、抓取适配器六项均保持；原 37 partial / 46 not_started / 0 accepted 和 83 包 / 161 验收项未变。

**升级须先停止旧 Core。** 业务 schema 仍为 13，派生索引 marker 为 `bzs-ascii-cjk/2+codepoint-grams/1`；支持事务式升级并保留业务数据。旧二进制拒绝重新打开新格式，仍在运行的旧进程与新进程混用不受支持。实际旧库升级、旧二进制拒绝及数据保留证据见 [P1-3 独立验证](independent/p1-3/)。

## GitHub 验收入口

GitHub 提交与本地已测提交的 tree SHA 逐项完全相同；不同 commit ID 来自连接器创建提交的作者/时间元数据，见 [提交映射](commit-map.json)。发布采用 main 原头 `44253a7` 的 expected-head 检查与非 force 更新，审计分支保持只读。

远端验收记录位于最终发布 main 提交的 Checks 和 [checks 工作流](https://github.com/wmqfl861/bzs861/actions/workflows/ci.yml)，交付回复提供准确 SHA 与 run 链接。只有该最终 SHA 的 Linux、Windows 两项成功才作为本批远端结果，旧 run 不替代。Linux 工作流包含完整 CTest、15 原生 ASan/UBSan 和原规划守卫；Windows 保留既有 17 个 native/HTTP 目标，完整 Worker 打包仍属于原 S9 范围。此文件中的本地成绩不替代远端 CI。

结构化总记录见 [final-report.json](final-report.json)。
