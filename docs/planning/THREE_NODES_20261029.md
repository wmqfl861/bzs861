# 2026-10-29 第三轮三节点：条件翻译、瞬时 no-store、SBOM

输入：v2 分支 `work/blueprint-next-ten-v2-20261029` 经 codex（gpt-6-astra/max）
四轮审计修复后的 `8be2fea2d4674d7ca77766a68809cdb12c99a129`（checks 与
Windows Python safety CI 双绿）。工作分支 `work/blueprint-v2-dev-20261029`。
本轮按"计划 → 独立审核计划 → 并行开发 → 独立审核成果 → 全量验证 → 推送"
执行；三个节点均依赖已满足、不涉 D1–D7、不启用任何来源或站点。
独立审核结论（2026-10-29 第二次，对照 d3d6d75 逐锚点核实）：GO（有条件），
10 项修订折入：①测试引用改 python/tests/test_search_modes_adapters.py:162；
②SRC-03 明确 strict 并非既有运行时概念——当前 filetype/intitle/inbody
静默透传，本节点将其改为显式错误/来源剔除是 FT-011 要求的**有意行为变更**，
须在 API.md 声明；③filetype 仿真核验只依据 URL 扩展名证据（搜索命中无
已取 content_type），无扩展名 URL 计为未核验并剔除，不得静默保留；
④strict 来源剔除须在 sources 报告中保留该来源的 filter 状态，用量/报告
不漂移；⑤批读 delivered-transient 标记须经 job_results 投影可见
（succeeded+空 snapshot_id 默认不可分辨于保留期删除，需一条守卫分支）；
⑥明确 web.cpp:1230（重定向前缀 no-store 直读）须在直读路径**先于**该
require 分支到瞬时交付，而 crawl/map 保持严格 decode_read；⑦瞬时交付需
内存窗口化路径（window() 是 store 背书的：snapshot/document/revision/
store_.range+发布投影），DirectReadFlight 须为订阅者携带瞬时段位——
这是本节点最大单体工作项；⑧used_pages 计数含 succeeded，瞬时段会虚增
"pages"——在 API.md 记录该语义；⑨e2e test_304_no_store(146-159) 列入
不翻转清单；⑩锁定包实测 60 个（非 58；62 是含注释行数），GOV 验收标准
改 60+67。原第一轮审核修订继续有效（本地复核不削弱、
零留存措辞、批读项终态表示、GOV 计数/路径、304 单独决策、RPC 反静默、
durable 路径语义、必改测试清单）。

## 节点 1：SRC-03 来源条件翻译与硬过滤（FT-011 / B-04）

**缺口（调研实证）**：合同词汇已交付（source-manifest.schema.json 的
`native_filters`、search-request.schema.json 的 `hard_filters`/`scope.languages`、
contracts.cpp:404/414 校验），但运行时 0%：`WebService::search` 只接受
query/sources/.../language（web.cpp:485-487），原始查询串逐字转发 worker
（web.cpp:586）；adapter 只翻译语言/市场（engines.py DDG kl、Bing mkt）；
`site:` 有本地复核（web.cpp:675-705，QRY-04 lite）但
filetype/intitle/inbody/日期不核验，`catch(Error{})` 就是"静默丢弃"洞
（web.cpp:703-705）。parse_query 已解析 site/filetype/intitle/inbody
（query.hosts/query.types/字段节点）——本节点是分类/执行/报告工作，
不做语法改造。

**交付边界**：
1. C++ `web.cpp`：把 site:-only 块升级为条件计划。**审核修订（门槛）：
   本地可计算条件（site→host、filetype→URL 可知 content_type）无论
   native 与否一律保留本地事后复核——升级严格加法，不削弱 QRY-04 lite
   现有保证**。按来源 manifest `native_filters` 分类
   native/emulated/unsupported；emulated=本地核验；strict 语义下
   unsupported 条件显式失败或剔除来源，绝不静默；`filters` 报告扩展
   每条件执行状态（native 只是执行位置≠结果满足，B-04）。
2. `include/bzs/web.hpp:38` SourceSpec + `web.cpp:247-313` SourceRegistry：
   消费既有 `native_filters` 字段（数据结构无 schema 变化；当前
   config/sources.json 零填充，故全部真实来源先按 emulated/unsupported
   处理——不违反 D1，native 路径仅由离线 fixture manifest 演示）。
3. Worker：`search_multi` 携带条件对象 → run_one → adapter 可选参数
   （python/bzs/worker/sources/base.py:130 默认 no-op，未改 adapter 显式
   unsupported）；DDG/Bing 增加真实 native 翻译（仅其 HTML 端点真实支持
   的 site 类参数）；返回每来源条件执行报告。
   **审核修订（RPC 反静默）**：核心侧把"worker 未返回某条件执行报告"
   一律按"未执行"处理（与 web.cpp:611-618 现有缺席纪律一致），防旧
   worker 静默吞掉新条件对象。
4. **审核修订（durable 路径语义）**：`web.search.start` 走同一 search()
   （refresh=true），strict 失败经 fail_search 进入来源级 unknown/partial
   （与一次性路径的显式 E_* 不同，为既有 durable 语义，不新增机制）；
   `filters` 报告随 payload_json 持久化但 session_page/stream 投影今日
   不透出——本轮不扩投影，仅在 API.md 明示该边界。
5. MCP：`web_search` 描述更新；不新增结构化参数（条件走查询语法，
   MCP 白名单不变；条件在 query 内已天然并入 cache key，无需额外
   key_parts 改动）。
6. fakeweb 引擎补参数回显与条件违反夹具。
7. **必改测试（审核列出）**：test_search_modes_adapters.py:164-165 的
   run_many 精确调用断言；unit.cpp:1339-1343/2048-2049 现有形状；
   新增六类测试——(a) manifest 声明 native 但引擎忽略条件→命中被剔除
   且报告为 native-executed-not-satisfied（反 native-as-verified）；
   (b) stub worker 吞条件对象→按未执行处理；(c) 无 native_filters 的
   strict 未知条件→显式错误且不进满足列表；(d) durable 路径 filters
   行为；(e) native 声明下 site: 仍本地复核；(f) 缓存命中重投影保留
   filters 报告。

**明确不做**：不启用来源/不改 config/sources.json review 数据（D1）；不
原样转发未知操作符；不做 QRY-04 预算重召回或短语语义（D7）；日期条件在
web 命中无 published_at 时只能 native 或 unsupported-strict，不本地伪造
仿真；不动 B-24 与发布投影（过滤在 project_source_search 之前、不改变
publication_suppressed 形状）。
**验收条件**：不支持条件不静默丢弃；native 状态不冒充 verified；strict
未知项不进满足列表（FT-011/B-04 原文）；上述 (a)-(f) 全部落地。

## 节点 2：CACHE-01 瞬时 no-store 正文读取（FT-032 边界内）

**缺口（调研实证）**：no-store 响应当前在 decode_read（web.cpp:1317）与
304 路径（web.cpp:1374）整体抛 `E_CACHE_POLICY`——正文已抓取、请求/字节
已计费（e2e_cache_freshness.py:164-173 实证）却被丢弃；文档自认"瞬时正文
读取尚未实现"（HTTP_FRESHNESS.md:15、~26）。worker 已完整传输所需元数据
（server.py:765-766）。

**交付边界**：
1. `web.read`（直接读取，含 read_id 共享成员）与 `read_many` 批读项：当
   响应 no-store 且请求方语义允许时，交付一次性正文——不建快照、无
   snapshot_id/document_id、不带 http_freshness/http_cache 复用元数据；
   结果显式标记 `transient/no_store`。
   **审核修订（门槛措辞）**：不变量是**零留存**（不供未来请求复用），
   而非零成员交付——同一 flight 已准入成员按既有共享读取模型交付同一
   物理正文、付款方一次计费与 same_physical_receipt 语义保持（B-24
   边界内既有行为，不得削弱）。
2. **审核修订（批读项终态表示，门槛）**：采用 `state='succeeded'` +
   `snapshot_id NULL` + `receipt_json` 携带 `delivered-transient` 标记
   ——避免 batch.cpp:354/362/396/403/597/625 的新状态字符串级联改动；
   schema15 不变；retry_failed 不重跑（终态 succeeded 天然排除）。
3. **审核修订（304 路径单独决策）**：`decode_not_modified` 基于
   base->document（已持久化快照），"无快照瞬时"在 304 上不成立——
   **web.cpp:1374 的 304+no-store 拒绝保持不变**，本轮只开 200 全量
   响应的瞬时交付。
4. 计费不变（请求/字节照记）；既有快照失效（web.cpp:1027-1035）、发布
   栅栏/观察（1037-1068）保持先于交付执行；window/start_byte/max_bytes
   语义对瞬时正文照常；content_trust 标记照常。
5. crawl/map 页面**不做**瞬时交付（map_service.cpp:164 与
   store.cpp:975/979、web.cpp:1230/1359 的持久化拒绝全部保持）。
6. 文档同步：HTTP_FRESHNESS.md:15/~26、HTTP_REDIRECT_CACHE.md:36、
   DIRECT_SHARED_READS.md:105、SHARED_READS、API.md。
7. **必翻转/必不翻转测试清单（审核列出）**——翻转：cache_freshness.cpp:
   101/104/214/217、direct_shared.cpp:521-540（付款+订阅双成员改断言
   瞬时交付）、542-552（保留其失效一半断言）、e2e_cache_freshness.py:
   134-144/164-173；不得翻转：store.cpp:975/979、web.cpp:1230/1359。
   新增：瞬时读取后 stats.snapshots==0、无文档行、document.read 该 URL
   E_NOT_FOUND、响应含 transient/no_store 且无 snapshot_id；既有快照+
   no-store 刷新→交付瞬时正文且旧复用仍失效（cache_only→E_CACHE_STALE）；
   no-store 响应的限制性发布观察仍在交付前记录；批读结算一致且
   retry_failed 不重跑；crawl/map 仍 E_CACHE_POLICY（断言而非假设）。

**明确不做**：通用 Vary 选型；B-24 扩展（通用合并/迟到订阅机制变更）；
跨身份复用；stale-if-error；存储层 schema 变化。
**验收条件**：no-store 站点可读（离线 Origin 夹具）；交付无快照痕迹、
用量照记、成员交付与一次计费保持；FT-032 四条款无一违反。

## 节点 3：GOV-01 完整离线 SBOM 与许可证核对（FT-001 的 SBOM 半项）

**缺口（调研实证）**：docs/DEPENDENCIES.md:5 自认"尚不构成完整传递依赖
SBOM"；锁定 Python 包无许可记录；vcpkg 端口 SPDX 从未核对；
audit-dependencies 通道已备但从未执行记录。

**交付边界（含审核计数/路径修正）**：
1. `tools/sbom_report.py`：读 `tools/requirements-s1-lock.txt`
   （**实测 60 个锁定包**；62 为含 2 行注释的总行数）+ .venv dist-info 许可
   （License-Expression→License→classifier 回退）+ vcpkg SPDX
   （`C:/tools/vcpkg-bzs/installed/x64-windows/share/<port>/vcpkg.spdx.json`，
   **实测 67 份 SPDX 文档**；机器外路径作为参数并记录 vcpkg baseline
   提交 6719fd4d）+ 缺 SPDX 的端口以 copyright 文件/标注 NOASSERTION
   处理；产出逐包版本/来源/许可/替换策略表，绑定 HEAD SHA。
2. 差异/异常报告：与 DEPENDENCIES.md 现有手工标签对照（如 aiohttp
   双许可）；无许可字段/可疑兼容项显式列出。
3. 更新 docs/DEPENDENCIES.md 与 ≤50KB validation 摘要；CI runner 系统包
   按既有 environment.json 记录、标注"记录未复审"；本地等价审计仅覆盖
   wheels SHA256（audit-dependencies 工作流本身只有 wheels 步骤，如实
   说明）。
4. 可重复性：脚本双跑输出字节一致（除时钟）且绑定提交；覆盖断言用
   实测计数（58/67），锁定文件字节不变（不新增依赖）。

**明确不做**：全产品独立签收（C-16 需独立审核者，不自我宣称）；不新增
任何依赖；不选定项目自身许可证（owner 决定）；不复审 CI 系统镜像。
**验收条件**：60+67 条目全覆盖、每条有许可证据与来源字段、
NOASSERTION 显式枚举、脚本可重复且绑定提交、差异报告可操作。

## 编排与门禁

- 冲突：SRC-03 与 CACHE-01 同改 src/web.cpp（另共享 include/bzs/web.hpp、
  docs/API.md、可能 tests/unit.cpp）→ **GOV-01 与 CACHE-01 并行，SRC-03
  在 CACHE-01 合入工作区后启动**；实现代理各自带测试；每节点独立提交。
- 门禁：每节点定向测试 + 全量 pytest + CTest Debug/Release -j1 + 合同/
  规划/plan guard + 真实启停与 stdio MCP 冒烟 + 准确新 SHA 双平台 CI
  （含 #9 引入的 Windows Python 门禁与 Linux ASan/UBSan）。
- 计数不冒充：83/161/10 与 37/46/0 不变；SRC-03/CACHE-01/GOV-01 保持
  partial，不宣称完整签收。

