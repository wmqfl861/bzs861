# bzs861 S0 审核裁决（2026-11-14）

审核方：Codex，受用户委托的独立审核方。审核日期：2026-11-14（Asia/Taipei）。审核分支：`work/s0-results-20261114`；实读 HEAD：`3936cfbdca18d382c0727214f1171013bc7f463d`（简称 `3936cfb`）。审核对象为用户机器上的 S0 run `e61529fac4944363beb02f012ad7a02e`，运行时间为 `2026-11-14T10:48:12.222Z` 至 `2026-11-14T10:48:32.736Z`。

**裁决：mwmbl、wikipedia_en、wikipedia_zh 的本轮可达性和成功/空结果解析通过；marginalia 为符合 D15 风险预期的限流阻断，尚未通过成功响应解析验证。启用裁决为 marginalia/mwmbl hold，wikipedia_en/wikipedia_zh enable（限链接输出，完成前置条件后激活）。CL-30 放行；S1 用户签收仍受阻。7 个 API 草案全部继续作为候选，不能填入生产启用签名；具体修正及顺序见下文。**

本轮只读现有文件、在内存中解压和解析本地 gzip，没有访问网络、安装依赖、运行真实探测或签收程序、修改配置或夹具，也没有启用来源。唯一新建文件为本文。开始时存在未跟踪目录 `docs/audit/20261031-2487934/`；未将其作为本轮证据，也未改动。本文不包含凭据值。

审核材料：

- `validation/s0_20261114/results/probe_report.json`、`probe_report.md`，以及该目录全部 **10** 份 `.body.gz`。任务描述中的“9 份”应更正为“9 份 HTTP 200 响应 + 1 份 HTTP 429 响应”。
- `docs/S0_PROBE.md`、`docs/S0_COMMAND.md`、`docs/CURRENT.md`；`docs/sources/mwmbl.md`、`marginalia.md`、`wikipedia.md`、`draft-seven-api-candidates-20261114.md`。
- `python/bzs/worker/sources/apis.py`、`base.py`、`review.py`、`__init__.py`；`python/bzs/worker/fetch.py` 的响应状态构造；`python/bzs/probe.py`、`probe_format.py`、`probe_state.py`、`replay.py` 的身份、计数和回放路径。
- `python/tests/fixtures/sources/mwmbl/` 全部夹具及 README；`python/tests/test_sources.py`、`test_sources_mwmbl.py`、`test_mwmbl_v2_schema.py` 的相关用例。目录中没有独立的 Wikipedia/Marginalia 响应夹具文件，这两类样本在测试代码内。
- `config/sources.json`、`tests/golden/queries.json`、`tests/golden/manifest.json`；`docs/adr/0003-free-source-policy.md`、`docs/planning/schemas/source-manifest.schema.json`、蓝图 S1 九条完成标准。
- `tools/accept_s1.py` 及其相关测试；`python/bzs/winsecurity.py`、`tools/create_auth.py`；`src/source_publication.cpp`、`src/sessions.cpp`、`src/web.cpp`、`python/bzs/mcp_server.py` 和 `tests/source_publication.cpp` 的相关输出限制。
- 上轮复审 `docs/reviews/claude-2026-11-14-phase2to5-review.md`、修复交接 `response-claude-2026-11-14-pre-s0.md`，以及前轮 D1、D15 记录。本文决定从 D-S0-1 编号，不覆盖原 D 编号。

证据口径：**事实**指本地文件、解压字节或本轮离线执行直接证明的内容；**既有记录**指仓库中此前审核者对上游条款的记录；**推断/待核**明确标出。没有联网重核 2026-11-14 的上游条款。草案自述“事实来自官方文档”不等于本轮独立核验，尤其不能据此确认会变化的配额和鉴权政策。

## 发现（Findings）

### F1【记录】原始字节和解析器身份一致，10 次内存回放全部匹配

**事实与证据：** JSON 报告 `status=completed`、`schema_version=2`，计划 12 项；实际预留并完成 10 项，另 2 项在预留/发请求前跳过。10 行 `response.external_requests` 均为 1、`redirects=[]`、`truncated=false`，没有 uncertain 项。不能把 marginalia 的“计划 3 项”写成已经请求 3 次。

逐一解压全部文件，解压后长度及 SHA-256 均与对应 `rows[].response.body_bytes/body_sha256` 相符。下表中的文件均位于 `validation/s0_20261114/results/`，完整文件名为“尝试 ID + 连字符 + 报告中的完整 body_sha256 + `.body.gz`”；哈希前缀仅为方便定位。

| 尝试 | 来源 / query_id | HTTP | 解压字节数 | 原始结果条数 | 报告 hits | SHA-256 前缀 |
|---|---|---:|---:|---:|---:|---|
| a000001 | marginalia / aca01 | 429 | 18 | 不适用，限流文本 | 0 | `28c081b5dcd0` |
| a000004 | mwmbl / aca01 | 200 | 16560 | 40 | 10 | `9e4904e74a6c` |
| a000005 | mwmbl / code01 | 200 | 9288 | 21 | 10 | `df2869b4c597` |
| a000006 | mwmbl / sym01 | 200 | 1475 | 3 | 3 | `ccf6056141ae` |
| a000007 | wikipedia_en / aca01 | 200 | 4451 | 10 | 10 | `0b0c16e4d00a` |
| a000008 | wikipedia_en / gen01 | 200 | 3590 | 10 | 10 | `ca63aa21e4e2` |
| a000009 | wikipedia_en / gzh01 | 200 | 450 | 1 | 1 | `3d1190503200` |
| a000010 | wikipedia_zh / aca01 | 200 | 5155 | 10 | 10 | `9eed5f749ee9` |
| a000011 | wikipedia_zh / gen01 | 200 | 178 | 0 | 0 | `231af22ee114` |
| a000012 | wikipedia_zh / gzh01 | 200 | 6758 | 10 | 10 | `d99187c79324` |

报告文件本身 SHA-256：`90aa1aa26a8f4307bd7a68d1e659cdf29e34276afda032bbc9b9ddbaf2d25fab`。当前配置、查询、黄金 manifest 的原始字节哈希，以及 options 的规范化 JSON 哈希，均与 `identity` 相符。6 个解析相关源码文件逐个相符；汇总 `code_sha256=74d1a41ce03d444d1dce68508284e709cf920c1a1de0a0ccff213d696ea9c1f1`。

使用现有 `.venv` 的 Python 3.12.10，依赖身份完整匹配报告：aiohttp 3.14.5、cssselect 1.6.0、idna 3.20、lxml 6.1.3、trafilatura 2.3.1、yarl 1.25.1。开启禁止网络及文件写入的 Python audit hook，并用 `-B` 禁止生成字节码；按 F2 所述路径映射在内存重建 `FetchResult`，调用真实适配器的 `check()`、`parse()`，按 `MAX_HITS=10` 截断。**10/10 状态和完整结果字典相同，64 条保存命中的 URL/title/snippet/rank 全部相同，网络请求为 0。**

验证范围是本批字节与当前代码的可复现性，不是独立证明网络录制的来源真实性，也不是排序质量评测。未运行会产生临时文件的全量 pytest、CTest 或 `accept_s1.py`。

### F2【阻塞：标准回放交付】响应文件被放平，报告中的 raw/ 路径全部失效

**事实与证据：** 每行 `rows[].raw` 和 `response.raw_path` 仍为 `raw/a0000xx-<sha256>.body.gz`，`probe_report.md:21` 也声明文件在 `raw/`。实际 Git 跟踪的 10 份文件均直接位于 `results/`，没有该子目录。调用真实 `python/bzs/probe_state.py:200` 的 `read_body()` 读取第一份样本，得到指向 `results/raw/a000001-…body.gz` 的 `FileNotFoundError`。

F1 的解压哈希证明字节没有缺失，本轮人工按 basename 定位后能够审查；**原样交给标准 `bzs replay` 仍会失败**，不能声称标准回放命令已经通过。执行方应在后续证据修正提交中将这 10 个原文件恢复到报告规定的 `results/raw/`，保留 gzip 文件原始字节及旧 JSON/Markdown 字节，再在原解析器身份下完成标准离线回放。不要改写旧报告路径/哈希掩盖搬运问题，也不需要为这个目录问题重做网络探测。

### F3【记录；夹具完善为建议】mwmbl v2 的真实成功形状通过，7.7 是截断后平均数

**事实与证据：** a000004/5/6 解压后均为对象，字段为 `query`、`number_of_results`、`results`、`images`、`monthly_usage`、`monthly_limit`。全部 64 个原始结果的 `url/title/content/engine` 为字符串，`score` 为浮点数，`title_highlights/content_highlights` 为字符串数组。`number_of_results` 分别为 40、21、3，与原数组长度一致。

`apis.py:243` 的 `Mwmbl.parse` 读取对象中的 `results`，与 CL-27 重写及夹具的主要形状一致。a000004 有两项 `content=""`，能正确生成空摘要；三份响应的 `monthly_usage` 和 `monthly_limit` 都是 JSON null，解析器不依赖这两个字段，因此没有空值错误。没有观察到真实 `content=null`，其兼容性由 `test_mwmbl_content_may_be_omitted_or_null` 和实现支持，不能写成真实上游样本已经覆盖。

`base.py:149` 和 `probe.py:298` 明确将每次保存结果限为 10；所以 mwmbl 的平均数为 `(10+10+3)/3=7.666…`，显示 7.7。原始 40/21/3 的数量并未被解析器丢失。a000004 的 `engine` 分布为 wikipedia 4、mwmbl 36，另两份均为 mwmbl；这支持既有“混入 Wikipedia 结果”的记录，不能把这些重叠当作独立内容贡献。

**夹具差异：** `search_ok.json:3` 写 `number_of_results=3`，实际 `results` 含 4 项，其中一项是故意构造的非 HTTP URL；真实样本中该计数是原数组长度。现有夹具还没有 highlights 字段，usage/limit 使用整数，README 仍称未捕获真实响应。这些不影响已验证的解析分支，但后续应将“严格上游形状样本”和“故意非标准 URL 的健壮性样本”说明清楚，采用必要的脱敏真实样本，覆盖元数据 null、空 content 和混合 engine。不得把合成夹具写成已实测响应。

### F4【记录】Wikipedia 中英文形状一致；中文 empty 是上游合法空集

**事实与证据：** 两种语言的非空响应均为 `query.search` 对象数组；条目有整数 `ns/pageid`，字符串 `title/snippet`，所有非空结果的这四个字段均无 null。摘要中的 HTML 命中标记由 `clean_text` 清除；语言参数控制输出域名，标题通过 URL 编码构造链接，`pageid` 不参与解析。与 `test_sources.py:354` 的内联样本一致。该测试及 `:489` 的空数组样本较真实响应精简，但关键结构一致。

a000011 对中文站查询英文短语 `how tall is mount everest`，178 字节原始 JSON 为：

```json
{"batchcomplete":"","query":{"searchinfo":{"totalhits":0,"suggestion":"how talk is mount everett","suggestionsnippet":"how <em>talk</em> is mount <em>everett</em>"},"search":[]}}
```

`Wikipedia.parse` 在 `apis.py:78` 看到合法的空 `search` 数组，返回空列表；没有被丢弃的上游命中，没有应用层 error，也没有截断。**裁定为该查询在该语言站点的合法无结果，属于来源/查询表现，不是解析缺陷。**“与语言或上游查询解释有关”只能作为解释性推断；不声称已证明中文维基普遍不支持英文查询，也不擅自执行 suggestion 或扩大查询。

英文平均 `(10+10+1)/3=7.0`；中文 `avg_hits_when_ok=(10+10)/2=10.0`，**该字段排除 empty**，若按全部三次平均则为 6.7。中文 `ok_rate=0.67`、`answered_rate=1.0` 与 `probe.py:373` 的定义一致。Markdown 的 “avg hits” 标题建议注明“仅 ok”，避免误读。报告中的 totalhits 是上游总匹配数，不能与本页数组长度或保存上限混为一谈。

空值边界：`title` 必须为非空字符串，缺失/null/错误类型应报 `parse_error`；可选 `snippet` 缺失/null 会变为空字符串。后者是实现支持，并非本轮非空真实样本已经覆盖。

### F5【记录】marginalia 的实际 429 和服务停止分类正确，但尚未验证成功 schema

**事实与证据：** a000001 的原始体恰为 18 字节文本 `QPM Limit Exceeded`；HTTP 429，记录头没有 `retry-after`。`Adapter.check`（`base.py:161`）在解析 JSON 前将 429 判为 `rate_limited`。报告 `retry_after_ms=0` 表示未收到可用的 Retry-After，不表示可以立即重试；`probe.py:326` 至 `:334` 采用本地默认 600000 ms 冷却，`budgets.marginalia` 为 `used=1`、`stopped=true`、`stop_reason=rate_limited`，冷却截止 10:58:14.212Z。

计划 p000002/p000003 为 `skipped`、`reason=service_stopped:rate_limited`，没有对应 HTTP 请求或响应体。正确口径为 **1 次真实限流响应 + 2 项因同一服务停止而未请求**。这符合 D15 对共享 key 可被限流、不能据此签收的预期；既有条款记录举的是 503，本次实际收到的是 429，应同时记录两种情况，不能将本次改写为 503。

本轮没有带 Retry-After 的真实样本，不能宣称真实网络已验证该头的解析；本轮离线合成 429 + `Retry-After: 3600` 得到 `rate_limited/3600000 ms`，与代码一致。共享 key 的使用来自用户任务背景和 S0 命令约定，原报告有意不保存请求凭据头，不能从原始 429 独立证明具体身份；本审核补充该非敏感运行说明。

没有成功 JSON，故 `Marginalia.parse` 所假定的 `results[].url/title/description` 仍只有 `test_sources.py:386` 等合成样本支撑，不能签署“真实成功响应解析通过”。`url` 必须为字符串；可选 title/description 的缺失/null 被归一为空串，其他错误类型被拒绝，这些也没有得到本轮真实成功样本验证。

### F6【阻塞：mwmbl 正式启用、CL-27 完成声明】401 在实际 search 路径被提前归为 http_error

**事实与证据：** `fetch.py:533` 将 HTTP 401 形成 `FetchResult(status="http_error")`；`Adapter.search` 在 `base.py:144` 先执行 `self.check(res)`，而基类 `check` 在 `:171` 抛出 `http_error`。CL-27 的 `auth_failed` 分支却位于之后才能执行的 `Mwmbl.parse`（`apis.py:244`）。

本轮以纯内存假 fetcher 返回 HTTP 401，调用真实 `Mwmbl.search`，实际得到 **`http_error, 401`**；直接调用同一适配器 `parse` 才得到 **`auth_failed, 401`**。`test_mwmbl_v2_schema.py:72` 仅测直接 parse，遗漏该顺序问题。

这是假响应验证出的控制流缺陷，本批 3 份真实 mwmbl 响应全为 200，没有真实 401。现有失败处理仍停止该源预算，未发现凭据绕过或不停重试；但“401 已独立归类”的交付声明不成立。应将鉴权状态识别放到实际检查路径，并增加经 `search`、真实 fetch 状态构造和服务停止路径的回归。不得为了通过测试把真实 401 的 fetch 状态伪装成 ok。

### F7【阻塞：marginalia 启用】503 分支会缩短上游较长的 Retry-After

**事实与证据：** `Marginalia.check`（`apis.py:205`）对所有 HTTP 503 固定抛出 600000 ms，不读取响应头。本轮合成 `503 + Retry-After: 3600` 的实际结果为 `rate_limited/600000 ms`；同样的头用于 429 则为 3600000 ms。至少在项目既定最大退避范围内，503 应采用本地冷却与上游 Retry-After 的较大者，而不是固定十分钟。

本轮原始体是 F5 的 429，没有触发该缺陷，不撤销本次停止行为的正确结论。另一个边界是“任何 503 都必然是共享 key 限流”并不由本轮样本证明；尤其使用个人 key 后，错误说明不得无条件断言共享身份。继续采取保守停源策略可以，但需区分证据与诊断推测。

### F8【阻塞：未满足条件的启用；部分可按本裁决消除】成功探测没有自动补全条款合同

**事实与证据：** 配置 17 个来源全部 `enabled=false`。四个已测来源的 `contract.review` 仍为 candidate，`approved_by/checked_at/expires_at` 为空，payment/access/cache/redistribution 均未核实；mwmbl 和两个 Wikipedia 的合同 `terms_url` 也为空。`review.py:99` 和 `src/web.cpp:219` 会正确拒绝这类启用。原始体可证明结果形状，不能证明缓存及再分发许可。

**既有记录：** `docs/sources/mwmbl.md:70` 明确写缓存/再分发条款未找到；不能因服务免费、代码开源或返回了搜索摘要便认定允许缓存。`marginalia.md:38` 记录 CC-BY-NC-SA 4.0；`wikipedia.md:46` 记录 CC-BY-SA 4.0。许可限制需进入具体使用范围，不能仅将字段改成 approved。

**可复用的实现事实：** 项目已经实现 `links_only`，不是单纯填写标签。`src/source_publication.cpp:151` 对外将 title 改为 URL、snippet 清空；`src/web.cpp:489`、`:931` 将其用于缓存及直接搜索；`src/sessions.cpp:340` 同样限制会话结果。`tests/source_publication.cpp:50`、`:62` 有相应既有回归，本轮只读这些 C++ 测试，未运行会写临时数据库的测试程序。

因此 Wikipedia 可依据已入库许可记录，在**个人本地缓存、对外仅链接**的受限范围内签署。本文不批准公开复制其摘要/正文；今后开放内容输出时必须另行落实来源、作者归属/原文回链、许可及修改说明等适用要求。mwmbl 的链接输出即使受限，也不能替代其仍缺失的缓存许可审核。

### F9【阻塞：S1 签收】启用后的清单哈希必然不同，旧 S0 报告不能直接通过现有签收检查

**事实与证据：** 原报告 `identity.manifest_sha256=ea53a36f8c3297986bd0dc360cfcd77fb7b92a808ef13dffdcac7c308ff63c4e` 与当前全禁用配置完全匹配。`tools/accept_s1.py:143` 至 `:164` 要求提供的 S0 报告与签收时**整份清单字节哈希相同**，并要求每个启用且获批的来源有非空 ok 记录。

本轮在内存调用原函数：原清单 + mwmbl/en/zh 的记录返回空问题列表；仅在内存将这三个来源及合同的 enabled 改为 true 后，返回 `identity.manifest_sha256 mismatch`。实际填写审核字段同样会改变哈希。这个问题独立于 F2 的目录位置，也独立于解析是否成功。

无需放宽签收脚本或新增证明机制：在最终启用清单冻结后，由用户针对最终启用集合做一次有界的实测复核，保存到**新目录**，取得与最终清单相同哈希且各来源至少一个非空 ok 的报告，再交给签收脚本。保留本轮原报告和裁决；不得把旧报告的 manifest hash 改成新值。探测和 searchd 的预算分开，需把复核及签收消耗计入实际来源预算。

此外，`accept_s1.py:395` 要求至少两个获批来源，`:400` 要求至少一个运行配置 `capability=web` 的通用网页源。仅启用 en/zh 不满足后者；不能利用合同将 encyclopedia 规范化为 web 的内部映射来冒充通用网页来源。

### F10【阻塞：7 候选直接获批；条款修正见各决定】草案混淆了端点、配额和内容许可

**事实与证据：** `draft-seven-api-candidates-20261114.md` 所称四个已有适配器、三个 planned，与 `sources/__init__.py:17` 的注册表一致；三个 planned 没有实际适配器。草案 arXiv 使用 HTTP，实际配置和 `Arxiv.default_base` 是 HTTPS；Stack Overflow 草案写 `/search/advanced`，实际 `StackExchange.default_base` 和配置为 `/search/excerpts`。`GitHubRepos.request` 在无 token 时仍发送公共仓库搜索请求，与草案“必须用 token”的断言不符。

草案把 Stack Exchange 的 key/无 key 配额混为一谈，将 GitHub 日上限与小时/搜索分钟桶混为一谈；OpenAlex 的积分、请求、匿名资格未分开；HN 的运营服务及回链要求需核实；GDELT 自承没有正式条款；Crossref/arXiv 的“所有元数据自由复用”不能未经范围确认就覆盖第三方摘要。这些条款页没有随本批附入可核对的官方原文或配额响应，具体时点的数值及条件须标待核。

F1 的 10 份原始体**没有任何一个来自这 7 个候选**。所以即使条款某项确认，也没有一个候选完成本轮 S0，更不能把“已有适配器”写成“已准备激活”。各源的条款裁决和实现优先级见 D-S0-7 至 D-S0-13。

### F11【阻塞：S1 前 CL-30；记录：状态文档】CL-30 两项仍存在，CURRENT 未反映本轮 S0

**事实与证据：** `winsecurity.py:24` 只列三个宽权限 SID，`:312` 只报这些 SID 的可读 ACE；其他非允许主体仍可漏报。`tools/create_auth.py:12` 先写秘密内容，`:16` 才调用 Windows ACL 保护，写入顺序未改变。两者与上轮 CL-30 描述一致，需要按 D24 在签收前完成。

`docs/CURRENT.md:3`、`:17` 仍写等待用户运行 S0，`:23` 写无待决事项，与本轮报告及本裁决已经不一致。后续改变状态的 PR 应按既有规则更新该入口，准确区分 S0 已审、哪些来源获批、哪些仍 hold、CL-30 状态及 S1 仍未签收。本轮不修改它。

## 决定（Decisions）

### D-S0-1：本轮 S0 审核完成，接受有限样本结论，不签署完整 S1

确认 F1、F3、F4、F5 的字节、状态和计数结论。mwmbl、wikipedia_en、wikipedia_zh 的本轮成功/空结果解析通过；marginalia 为 blocked，成功解析尚未验证。F2 必须修复证据交付布局。S0 不产生排序质量、相关性、长期成功率或所有免费配额可用的结论。

| 来源 | 启用裁决 | S0 实测结论 | 前置条件 / 对应决定 |
|---|---|---|---|
| marginalia | **hold** | 1 次限流，2 项未请求；无成功响应 | 个人 key 下成功 S0、F7 修复、许可义务及签名，见 D-S0-2 |
| mwmbl | **hold** | 3 次成功，v2 解析通过 | 缓存许可依据、F6 修复、条款签署，见 D-S0-3 |
| wikipedia_en | **enable，限 links_only** | 3 次成功 | CL-30、F2、合同按下述范围落库，见 D-S0-4 |
| wikipedia_zh | **enable，限 links_only** | 2 次成功、1 次合法空集 | 与 en 同条件；不因合法空集强制重测，见 D-S0-5 |

这里 enable 是对执行方后续激活的审核许可，所有前置条件满足后才能落地；本轮配置和运行状态仍全部禁用。hold 不等于认定来源永久失效，也不授权填写生产 `approved_by`。

### D-S0-2：marginalia 暂缓，按个人 key 和真实成功样本补齐

保留禁用及 candidate，不填启用签名。必要条件依次为：

1. 用户取得自己的免费非商业 key；只以环境变量等既有安全方式配置，任何审核证据均不记录值。
2. 修复 F7：遵守有效 Retry-After，保留停源/共享预算行为，不把一般 503 的身份原因写死。
3. 用户在允许的预算和间隔内重新做该候选的 S0，至少提供真实非空成功响应，核对 `url/title/description`、空值和完整解析结果；仍受限则继续 hold，不循环重试。
4. 依已记录的 CC-BY-NC-SA 4.0 核定缓存范围、非商业使用、署名/回链和适用的相同方式共享要求，再明确 cache/redistribution 口径，由审核方对具体合同签署。

当前 `daily_cap=500` 是本地设定，不能从共享 key 的 429 推导个人 key 有相同承诺额度；个人 key 获准条件优先。若最终选择 mwmbl 加 Wikipedia 满足 S1，marginalia 可继续禁用；D15 的个人 key 要求针对启用 marginalia 的签收，不应成为整个项目必须等待该源的额外门槛。

### D-S0-3：mwmbl 暂缓；成功 schema 已过，许可和 401 仍须补齐

本轮不授权填入 mwmbl 的 `approved_by`。启用前必须关闭 F6，并为实际 v2 API 的缓存使用取得可入库的明确依据；再分发范围也须明确。优先考虑个人本地缓存与 `links_only` 的窄范围，但不能把“未找到限制”改写成“允许缓存”。免费 API 接入条款、数据使用许可和上游软件源码许可证需要分别判断。

**D23 不变：** 当前匿名 `daily_cap=30`、`contract.limits.request_quota=30`、窗口 86400 秒、`min_interval_ms=1000`、并发 1、共享预算键 mwmbl。30×31=930 是单一预算执行范围内的保守月度估算；`quota_scope=this_probe_run` 表明本轮 3 次探测、其他探测目录和 searchd 并未自动合并扣账，实际使用必须统筹这些消耗，不能声称已有全局月账本。

用户未来取得免费 key 并复核该免费层后，方可按 D23 调到 60/天；本轮不升级。保持无按量付费回退，不主动打开付费消费。本次真实元数据的 monthly_limit/usage 为 null，不能据此证明无月度配额，也不能据此取消上述上限。

### D-S0-4：wikipedia_en 批准受限启用，签署对象为该来源的完整运行合同

依据既有 `docs/sources/wikipedia.md` 的免费匿名 API、合规 UA 和 CC-BY-SA 4.0 记录，加上本轮 en 的三次成功样本，批准 **个人本地搜索缓存 + 对外仅链接**。利用现有输出限制，保持 `title=URL`、`snippet=""`；不批准通过把 policy 填为 allowed_with_limits 而输出无许可说明的正文/摘要。

本审核签名只适用于 `id=wikipedia_en`、`wikipedia_api`、`https://en.wikipedia.org/w/api.php`、`lang=en`，以及本文规定的配额和用途。执行方在 CL-30 完成、F2 修复、相关既有回归通过后，才能在同一次配置变更中同步填写运行来源和 `contract.enabled`，并落实：

- `contract.review.status=approved`、`payment_scheme=no_payment`、`automated_access=approved`。
- `cache_policy=allowed_with_limits`：缓存仅供个人本地搜索，保留来源和文章回链；本批准不涵盖向外提供原始摘要库或正文库。
- `redistribution_policy=links_only`，使用既有源级限制，不创建新输出模式。
- `approved_by="Codex:codex-2026-11-14-s0-review:D-S0-4"`。签署对象为上述**来源合同及限定用途**，不是对所有 Wikipedia 接口、抓取行为或任意未来配置的签名。
- `terms_url=https://en.wikipedia.org/wiki/Wikipedia:Copyrights`，来源记录中同时保留 UA 政策、API 礼仪和本文的引用。`checked_at=2026-11-14T15:37:35Z` 为本轮基于仓库证据的审核签署时刻，不是声称重新联网查询条款的时刻；`expires_at=2026-12-13T00:00:00+08:00`，保守采用原记录到期日的本地零点。启用时已到期则须重审，禁止自行顺延。

配额保持 `daily_cap=request_quota=2000`、窗口 86400 秒、`min_interval_ms=500`、并发 1。en/zh 的 `shared_budget_key` 均为 `wikimedia`，**两个语言共享合计 2000/天，不是各获独立的 2000 次上游承诺**。本地礼貌上限不保证上游容量；429、应用层 backoff 和 Retry-After 仍优先。继续匿名和诚实 UA。运行 capability 保持 encyclopedia。

### D-S0-5：wikipedia_zh 同范围批准；合法 empty 不构成缺陷

批准对象为 `id=wikipedia_zh`、`wikipedia_api`、`https://zh.wikipedia.org/w/api.php`、`lang=zh`。沿用 D-S0-4 的缓存、links_only、条款依据、有效期、共享预算及激活前置条件；其独立签名为 `approved_by="Codex:codex-2026-11-14-s0-review:D-S0-5"`。

a000011 的 empty 已由原始 `totalhits=0/search=[]` 证明，不能要求改解析器制造命中，也不需要为“修复 empty”重复探测。同一许可范围内，未来正式输出内容须重新落实许可展示并审核，不得仅复制本签名扩大用途。en/zh 即使有两个来源 ID，也共享 Wikimedia 服务；本裁决不赋予它们两个独立通用网页索引的资格。

### D-S0-6：7 个 API 草案只裁定为候选条款修订，不签署启用

逐源结论如下；“修正”表示保留方向并改正记录，“驳回当前口径”针对草案中的确定性结论，不代表永久否决该 API。**7 个均没有本轮真实 S0；4 个已有代码的候选也必须经过 S0，不能只做条款确认就激活。** 本轮不填写这 7 个来源的 `approved_by`，不更改 planned 适配器标记。

数值证据分两类：来自本地配置的现值可以确认；以下提到的上游通常配额是用于指出草案问题的参考，须由执行方在后续获准的条款核验中保存当时官方依据，不能冒充本轮在线确认。缓存/再分发若涉及摘要或用户内容，不能用“只存片段”自动推定没有版权义务。

### D-S0-7：arxiv——修正后保留候选；后续接入顺序 1

- **端点事实修正：** 使用当前配置及实现的 `https://export.arxiv.org/api/query`，草案中的 HTTP 与实际请求身份不一致，不为匹配草案而降级。
- **配额：** 保留 3 秒间隔、并发 1 的保守规则；“所有机器合计”不是单进程 `min_interval_ms=3000` 就能证明满足。草案 300/天可作为本地建议，当前实际为 1000/天；二者都不是上游承诺的日额度。
- **key：** 按既有 API 模型无需 key；本轮没有该源真实响应，启用前仍按当前官方政策确认。
- **缓存/再分发：** 不接受由“元数据 CC0”直接推导“所有返回字段都无限制、署名不强制”。具体元数据授权范围、摘要及 API 使用条款是否有额外条件须保存依据。实现会读取 Atom summary，并非只保存题名和链接；应保留 arXiv 来源标识和摘要页回链，正文/PDF 不纳入本批范围。未明确的摘要输出先限制为链接，不预签内容再分发。
- **进入工作：** 已有 Arxiv 适配器；先修正条款及样本验证，不重写一个新适配器。S1 后优先处理，原因是接口已有、匿名、限速口径简单且补充 papers 能力。

### D-S0-8：stackoverflow——修正后保留候选；后续接入顺序 4

- **端点事实修正：** 当前为 Stack Exchange 2.3 `/search/excerpts`，不是草案 `/search/advanced`；前者提供 `item_type/question_id/answer_id/excerpt`，与 `apis.py:121` 的解析一致。审核及 S0 必须绑定真实请求端点。
- **配额：** 驳回“无 key 默认 10000/天”的笼统写法。通常公开口径为无应用 key 300/天、带已注册应用 key 10000/天；匿名/IP 共享和认证额度要分开，应用 key 也不意味着所有请求无条件拥有独立桶。上述现行值仍须官方原文及 `quota_max/quota_remaining` 核实。当前本地 250/天只能视为保守预算，不能保证共享 IP 剩余额度；同时遵守动态 backoff 和短时限制。
- **key：** 应用 key 可选，用户 OAuth access token 与应用 key 是不同概念，不把“不带 token”当成“不带 key”。现有实现没有自动申请或发送应用 key 的逻辑。
- **缓存/再分发：** 同请求至少一分钟避免重复请求的规则应验证实际请求缓存；响应 backoff 目前有处理，还应处理额度耗尽。用户贡献存在不同历史版本的 CC BY-SA，不能一概写为 4.0。若输出 excerpt，需要按适用许可保留作者/贡献归属、原问题或答案链接、许可和修改信息；现有 Hit 不携带这些完整信息，先采用 links_only 或补齐后再审。
- **进入工作：** 先校准端点、配额及署名范围，再做用户 S0；已有代码不等于完成准入。

### D-S0-9：github——修正“必须 token”，保留公共仓库搜索候选；后续接入顺序 3

- **key/可用性：** 公共 repository search 有匿名使用路径，现有 `GitHubRepos.request` 也明确只在 token 存在时加认证头；不得因草案要求而强迫用户先创建 PAT。这里是公共仓库搜索，不是私有仓库或需要认证的代码搜索。匿名在用户当前网络是否实际可用仍须 S0。
- **配额：** 分开记录通用 REST 匿名约 60/小时、认证约 5000/小时与搜索资源匿名约 10/分钟、认证约 30/分钟等通常口径；按实际 `x-ratelimit-resource/remaining/reset` 判定所属桶，不将这些数字当作每个请求必然同时扣减的同一桶。短时/二级限制另行遵守。`daily_cap=500` 和 6 秒间隔不能证明遵守所有共享及短窗口约束；首轮匿名可自设更保守日预算，并以实际响应为准。
- **退避：** 当前 `apis.py:180` 对 `403 + remaining=0` 固定等待一分钟，不能据此宣称完整支持较长 Retry-After 或 reset 时间；进入该源工作时补足对应行为。不要将所有 403 都视为 token 必需。
- **缓存/再分发：** 驳回“仓库元数据按各自仓库许可，因此搜索结果本身无额外限制”的推导。API 使用条款、仓库内容许可、账号/个人信息字段各有范围；仓库代码许可证并非所有 API 字段的统一授权，公开可读也不等于可任意再发布。首轮限公共仓库链接及经核实可缓存的检索元数据，不下载代码或 README。

### D-S0-10：hackernews——修正署名/回链及服务身份；后续接入顺序 2

- **服务事实：** 现有实现调用 Algolia 的 HN Search，参数 `tags=story`；应与 Y Combinator 的 Hacker News/Firebase API 区分，不能用后者的条款替代该搜索服务条款。
- **配额与 key：** 草案记载无 key、10000 次/小时/IP；具体当前上限及“从 1000 放宽”的历史没有本地原文，本轮不确认该变更叙述。可采用 1 秒间隔、并发 1、本地 500/天的保守建议，遇上游限制停止；这些是项目预算，不是新发现的官方配额。
- **署名修正：** 不能仅写“注明 Hacker News 数据源”便认定已经满足 HN Search 服务条件。核对原文的服务回链/Algolia 署名要求；拟用范围同时保留 Hacker News 数据来源、HN Search（Algolia）服务来源及 `https://hn.algolia.com/` 回链。若原文规定特定品牌呈现，再按原文落实；本轮不声称已在线确认某个特定 Logo 或措辞为强制要求。
- **缓存/再分发：** 免费 API 不自动授予用户 story_text 或链接文章的版权。代码在有 story_text 时会生成文本摘要，不能把当前行为概括为只输出链接；在内容授权与署名落地前采用 links_only，且保留服务所需署名/回链。仅有结果 `source_id=hackernews` 是否满足完整条款尚未证明。
- **进入工作：** 无需新建适配器，先修正条款、确定输出范围后做 S0；作为 news 补充优先于从零实现 GDELT。

### D-S0-11：gdelt——驳回当前草案作为完整条款批准依据，保留占位；后续接入顺序 7

- 草案自己写明没有找到正式条款，社区经验 1 次/5 秒不能转成官方限速事实；免费开放数据集也不能自动授权重新发布新闻媒体内容。无 key、默认 75/最大 250 条等陈述仍须对所用 DOC API 版本留存官方依据。
- 5000 ms、并发 1、200/天只能是待实施的本地礼貌预算。当前配置还是 1000 ms、500/天，不得把建议写成已经落地。
- 缓存范围先限经核实的新闻链接及必要检索元数据；媒体正文、图片和摘要许可分别判断。保留 GDELT 来源说明，但“预计署名”不是完整条款证据。
- **占位符结论：保留 `gdelt_planned` 且禁用。** 该标记准确表达尚无可执行适配器；注册表没有 GDELT，不能仅改 adapter 名或套用通用 JSON 解析就启用。占位记录便于保留候选身份，但 GDELT 不是 S1 的必要实现项，既不能替代通用网页源，也无需为解除 S1 阻塞优先开发。
- 官方接入/数据使用条件明确后，才安排真实端点参数、异常/退避、离线夹具与用户 S0。

### D-S0-12：openalex——驳回当前积分与匿名鉴权结论，退回修订；后续接入顺序 6

- 草案的“2025-11 起免费 100000 积分/天、100 req/s、匿名 10 req/s、key 可选、mailto 已废弃”不能作为同一份已核实的现行规则。本地没有对应版本化官方原文，也没有该源响应；尤其**不能用一个匿名速率数字推导匿名已获完整搜索资格**。
- 新积分制必须同时记录：生效日期、免费层是否要求 key、每日免费预算及重置时点、works 的搜索/列表等操作各自成本、翻页/附加操作成本、免费额度用尽行为和速率上限。积分不是请求次数，`daily_cap=500` 不能直接证明积分预算合规；需按“各类请求数量 × 对应积分成本”的总和控制，且明确余额不足停止。
- 实现准备按**需要用户自己的免费 key**处理，直到当时官方证据明确证明所需匿名接口可用；这是本项目保守选择，不声称本轮已经验证上游一律强制 key。mailto 不充当 key 替代。若要匿名例外，先补条款和真实 S0 依据。
- 只有无需付款、长期固定免费层且耗尽后停止的方案才符合 ADR-0003；周期性免费积分本身不等于付费试用，也不能因此启用按量回退。不得使用过期积分表推导免费承诺。
- CC0 可作为核验 OpenAlex 自身元数据缓存/再分发范围的依据，但不能扩张为相连出版物/PDF/第三方全文均为 CC0。补齐条款及成本模型后再实现 `openalex_api`；当前保留 planned、禁用。

### D-S0-13：crossref——修正许可范围及动态限流，保留候选；后续接入顺序 5

- `/works` 公共元数据 API、无需普通 API key、可用联系邮箱进入礼貌池的方向可保留；mailto 是联络标识，不是认证凭据，也不等于服务质量保证。付费层不在本项目范围。
- 草案未存档“2025-11 更新”的具体规则，不能写成已核实。实现应识别实际响应中明确的 rate-limit limit/interval 和并发约束，遵守 429/Retry-After；公共池与礼貌池条件分开记。1 秒、并发 1、300/天为建议的本地预算，当前 500/天且无适配器；不能由此宣称已合规。
- 不接受笼统“全部返回元数据都是 CC0”覆盖出版商提供的摘要等版权字段。核实 Crossref 对其元数据的开放范围，保留 DOI/出版者回链；摘要、全文链接所指内容和 TDM/full-text 许可各自判断，不随 API 免费一起批准。对经核实的书目/DOI 元数据可规划缓存与再分发，摘要输出待明确。
- 新实现只做已审范围的检索、响应与退避；先提供离线样本，再由用户做 S0。当前保留 `crossref_planned` 且禁用。

### D-S0-14：CL-30 小批次现在放行，范围限定为原两项

按 D24 单独一个小 PR，先完成 CL-30，再进入来源激活和 S1。具体要求：

1. doctor 改为只允许当前用户、SYSTEM、BUILTIN\Administrators 读取秘密；检查其他任意 SID 的可读授权，不能只补几项黑名单。ACL 无法检查仍失败。补允许主体和 INTERACTIVE、Guests、应用包、任意其他用户等拒绝案例。
2. `create_auth` 按“独占创建空文件 → 建立并确认限制 ACL → 写入秘密”的顺序操作。ACL 失败时不得已写入秘密，保留拒绝覆盖行为；增加验证顺序及失败分支的回归。

本次审核发现的 401、503 问题是后续**来源启用前的针对性修复**，各自绑定 F6/F7，不能混入 CL-30 冒充原范围；没有批准扩展权限模型或开展一般重构。CL-30 的本轮结论是允许实施，不是已完成或已验收。

### D-S0-15：tools/accept_s1.py 用户签收不解除阻塞；满足以下条件后方可正式签收

阻塞包括：CL-30 尚未完成；当前无已启用的审核来源；mwmbl/marginalia 尚无生产启用批准；原始体目录布局错误；最终启用清单会触发 F9 的哈希变化。两个 Wikipedia 的受限启用许可不能代替“至少一个通用网页源”。

最短路径为：CL-30 及证据修正完成 → 关闭 mwmbl 的 F6 和缓存条款缺口并取得针对该源的审核签名 → 按签名落地至少 mwmbl 加一个 Wikipedia（可两个）→ 冻结最终清单 → 用户在该清单下为全部最终启用来源各取得至少一个真实非空 ok 的小规模复核报告 → 用户完整运行 `tools/accept_s1.py`。如果选择 marginalia 替代 mwmbl，则先满足 D-S0-2；没有要求两条通用源必须同时就绪。

最终清单下的复核可先从每源 1 次开始，使用既有 probe 和新的输出目录；出现限流立即停止，出现 empty 不伪造成功。原本 S0 审核通过是激活前依据，后续小规模复核用于满足现有签收脚本的最终清单绑定，不能倒过来先启用尚未获批的来源。所有请求均计入实际预算，不能因换目录获得“免费”额度。

只有九条标准全部 PASS、脚本 exit 0 且证据对应最终代码和配置，才签署 S1。不能改脚本忽略哈希、删除失败项或将百科能力改称通用网页；若 marginalia 仍禁用，脚本不应因未提供其个人 key 而阻塞。本文既不运行签收，也不预填其通过结果。

### D-S0-16：D22 的待审闸门结束，下一批仅放行 S1 必需收尾；D26 继续有效

允许后续执行方处理本文归档和 CURRENT 更新、F2 的证据布局修正、CL-30、拟启用来源必要的 F6/F7 修复及条款闭合、按有效签名激活、用户有界复核和 S1 签收。这些动作按各自前置条件进行，不表示所有来源或所有新工作全面放开。

7 候选的草案文字可以按本文修正，但**新增适配器和其接入实施排在 S1 签收后**，建议顺序明确为：**arxiv → hackernews → github → stackoverflow → crossref → openalex → gdelt**。前四项主要是现有实现的条款、限流和样本补齐，后三项才是新适配器。排序按当前实现成本、能力补充及待核风险安排，不是强制把前一源无法解决的条款风险带到后一源。

D26 保持：S1 签收前不开展 CL-13、CL-15、CL-16、CL-19 余项及其他测量/重构任务；签收后先按既定顺序做测量，FTS 提前终止需测量证明有必要，CL-16 不独立开批。D25 的诊断插桩撤除条件及 D27 的 Python 3.13 CI 时机不提前。继续禁止扩大 B-24、抓取型来源启用、浏览器伪装、改写历史和通过放宽断言换绿。

## 给执行方的指令（按顺序）

1. **先归档裁决和纠正状态入口。** 原样纳入本文；更新 CURRENT，写清本轮 S0 已审、marginalia/mwmbl hold、两个 Wikipedia 仅获 links_only 激活许可、CL-30 待做、S1 未签收。不要把“审核已结束”写成“来源全通过”。
2. **修复证据交付，不改证据内容。** 将本轮 10 个 gzip 文件按旧报告相对路径恢复到 `results/raw/`，逐个核对压缩文件未改、解压长度及哈希不变；原 JSON/Markdown 不改。在记录中的解析器代码和依赖身份下运行标准离线 replay，另存回放结果；如代码已改变，先用原身份完成基线回放，变更后比较必须明确标注，不能偷偷忽略身份。
3. **实施并验收 CL-30。** 一个小批次仅包括白名单检查和先 ACL 后写入两项及必要测试。按 D20/D28 记录本批全部 CI 尝试，全部所需检查通过后按既定集成流程合入；本审核没有查询或担保新的远端 CI。
4. **单独关闭拟启用通用源的缺口。** 优先修 mwmbl 的 F6，并补齐其缓存/再分发依据及具体审核签名；若选择 marginalia，则还需个人 key、F7 和真实成功 S0。条款证据需在后续获准核验时取得，本轮不代替访问。两个 Wikipedia 可在前述条件完成后按 D-S0-4/5 落库；不要给 hold 源复制 Wikipedia 签名。
5. **激活时一次同步合同和运行字段。** 保留核定端点、身份、限速、共享预算、有效期及 links_only 范围；两个 enabled 字段保持一致。mwmbl 若获批仍按匿名 30/天，Wikipedia 两语言共享 2000/天。确认最终集合至少含一个获批通用网页源，再进入正式 S1 路径。
6. **由用户完成最终清单绑定的有界复核，再完整签收。** 原报告保留；新报告写新目录，与最终清单字节哈希相同，覆盖所有启用源的真实非空 ok。核算探测与签收总消耗，不绕过停源；随后运行 `tools/accept_s1.py`，九条 PASS/exit 0 后才宣布 S1 签收完成。
7. **S1 完成前保持任务边界。** 不实现 7 个新接入批次，不启动测量或重构。S1 后按 D-S0-16 的候选顺序分别补当前条款、所需实现/夹具、用户 S0 和审核签名；任何一项条款仍不确定就保持该项 candidate/禁用，不用其他来源的成功替它过闸。

签署：**Codex，2026-11-14。** 来源生产启用签名仅授予 D-S0-4、D-S0-5 所列两个限定合同；其余来源无本轮启用签名。执行本文建议时应保留事实、离线合成验证和条款待核状态之间的区别。
