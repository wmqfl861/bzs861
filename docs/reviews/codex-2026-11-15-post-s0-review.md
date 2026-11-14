# bzs861 S0 裁决落地批次复审（第 2 轮，2026-11-15）

审核方：Codex，受用户委托的独立审核方。日期按 Asia/Taipei。本轮在 C:\bzs861-audit 的 work/post-s0-docs-20261115 上只读审阅；唯一新增的仓库文件为本文。

**裁决：F2 验收通过；F6/F7 修复内容通过；CL-30 尚未通过，PR #26 当前 head 不可合入。PR #27 的代码和测试内容可合，仍须履行同 PR 更新 CURRENT 及 D28 的集成条件；PR #28 须更正状态和验证声明后再合。mwmbl 获得本文 D-R2-5 的受限合同签名，但当前不放行实际激活：先关闭 CL-30 的 RF1–RF3。S1 最短路径可以采用 mwmbl + wikipedia_en/zh，用户复探及正式 accept_s1 的启动条件目前尚未齐备。**

## 复审范围、版本与验证方法

先读执行方交接 docs/reviews/response-codex-2026-11-15-post-s0.md，再读取上一轮裁决、三个 PR 的完整本地 diff、相关实现和回归、来源合同/schema、发布限制及签收入口。上一轮裁决和 F2 证据在 s0-results 分支，通过 git show / git archive 读取，没有切换当前分支。

| 对象 | 本轮实读 head / 提交 | 审查范围 |
|---|---|---|
| 本地 main，三个 PR 的共同基线 | 18973986b5ac47559841024e42f8880a628c6990 | merge-base 核对、配置和既有约束 |
| PR #26，work/cl30-20261114 | 9e7ee6bb562facd4ad8a48a0b17d0f19e5c37eec | winsecurity.py、create_auth.py、Windows 回归，共 3 文件 |
| PR #27，work/source-fixes-f6f7-20261114 | 8ec375165be62ed751518ad1f6cbf598c0da6f93 | apis.py 和 3 个测试文件，共 4 文件 |
| PR #28，work/post-s0-docs-20261115 | 0adccea6b39f09b6787cc3eb3412af872c4fdbde | CURRENT、mwmbl 来源记录、交接，共 3 文件 |
| 裁决归档 / F2 交付，work/s0-results-20261114 | 1310cc0 / 817ecef00426322c3f5e4e8320915ca5b866cd6b | 289 行裁决、10 个 gzip、旧报告、标准 replay 产物 |

**事实口径：**“仓库事实”指本地 Git 对象和文件直接证明的内容；“离线验证”指本轮实际执行结果；“执行方记录”指交接及条款核验记录中的声明；“待核”指本轮不能独立确认的内容。条款页面没有随本轮联网重取，本文对其判断基于入库引文。三个 head 与本地 origin 跟踪引用一致；没有 fetch，也没有独立查询远端 PR、分支或 CI 的现时状态。PR 编号映射采用用户提供的信息。

没有访问来源或条款网络端点、安装依赖、启用来源、执行真实 probe/accept_s1、修改代码/配置/夹具/既有证据或 Git 状态。按用户允许的本地解压与测试范围，在系统临时目录解出待测 Git 对象；测试生成的文件与 replay 新输出均位于临时目录，结束后清理。Python 使用 -B，pytest 禁用 cacheprovider；测试进程禁止外网/DNS，异步测试仅保留 Windows 事件循环所需的本机 socket。未产生外部网络尝试。开始时已有未跟踪目录 docs/audit/20261031-2487934/，未改动，也未将其作为证据。

本轮实际执行的验证命令及参数如下；Python 包装脚本经标准输入运行，未新增测试脚本到仓库：

1. Git 只读核对：git status --short、git for-each-ref、git merge-base、git log、git diff --no-ext-diff 1897398...<各 PR 分支>、三个 diff 的 git diff --check；后者均无输出。F2 使用 git diff-tree -r -M --name-status 1310cc0 817ecef，并逐个 git show 读取搬移前后压缩字节。
2. 字节校验：以 .venv\Scripts\python.exe -B - 执行内存 gzip.decompress、长度及 hashlib.sha256 核对，并比较两份旧报告和归档裁决的 Git blob。10/10 压缩文件及解压数据通过，详见 RF5。
3. 标准 replay：git archive --format=tar 817ecef validation/s0_20261114/results 解到临时目录，调用真实 bzs.cli.main，参数等价于下列标准命令；没有使用 --allow-parser-change，也没有替换读体或写报告函数：

   ~~~text
   bzs replay --report <临时解压目录>/validation/s0_20261114/results/probe_report.json --out <新的临时回放目录>
   ~~~

   结果：exit 0；status=passed；identity_match=true；10 replayed / 10 matched / 0 mismatches / 0 skipped；network_requests=0，网络拦截计数也为 0。
4. PR #26：git archive 9e7ee6b 的 python/tools/config/tests/docs 后，在临时副本的 python 目录，用现有 Python 3.12.10 和 pytest.main 执行：

   ~~~text
   pytest -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp <临时目录>/pytest-temp tests/test_windows_secrets.py
   ~~~

   结果：18 passed，4.40s。另在内存加载该 head 的真实 create_auth / winsecurity，使用临时文件、真实 Windows ACL 和非秘密哨兵内容完成 RF1–RF3 的失败注入；三个缺口均复现，未打印任何凭据值。
5. PR #27：同样从 8ec3751 解出待测副本，执行：

   ~~~text
   pytest -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp <临时目录>/pytest-temp tests/test_mwmbl_v2_schema.py tests/test_sources.py tests/test_sources_mwmbl.py tests/test_probe.py::test_auth_failed_stops_the_source_budget_and_survives_resume
   ~~~

   结果：129 passed，3.07s；外部网络尝试为 0。为核验“全部新增测试先 RED”的声明，另用内存 import loader 将 apis.py 换成 1897398 的版本，仅运行本 PR 新增的 4 个测试函数：结果为预期的 3 failed、1 passed，见 RF4。这是旧代码反证，不是待合代码的回归失败。
6. 合同和签收条件：在内存副本中填写 D-R2-5 和 D-S0-4/5 的合同，调用现有 validate_spec、require_review，以及 Draft202012Validator/FormatChecker；三个来源全部通过。再调用 accept_s1.manifest_analysis 和 probe_report_issues，分别确认只有 mwmbl 属于通用 web，以及旧 S0 报告不能绑定新清单。没有将该内存清单写回配置。

**验证限度：**未重跑执行方所称 1879/1878 项全量 pytest；未运行 CTest、Linux/Windows CI、真实网络探测或完整签收。上述两项全量通过数仍是执行方记录。没有把离线构造的 401/503 写成真实上游样本，也不以测试数替代对安全失败路径的检查。

## 发现（Findings）

### RF1【高 / P1；阻塞 PR #26 与激活】独占文件在验证前被关闭，写入时按路径重开，可覆盖另一文件

**仓库事实：**PR #26 的 tools/create_auth.py:24 用 O_EXCL 创建空文件，:25 随即关闭 fd；ACL 保护和核验后，:33 使用 open(path, 'w') 再次按名字打开。第二次打开没有独占创建语义，且不绑定刚才被验证的文件对象。如果输出目录允许另一主体删除/替换目录项，检查过的对象与最终被写的对象可以不同；路径消失时还会重新创建文件。

**离线验证：**在临时目录调用真实 write_exclusive；仅包装 verify_windows，在真实 ACL 验证成功之后、最终写入之前模拟并发替换：删除新建空文件，令同一路径硬链接到另一份已有非秘密文件。函数成功返回，已有文件被覆盖。输出为 preexisting_file_overwritten=true、same_file=true。该验证证明时序缺口；没有声称用户目录实际发生过攻击或秘密泄露。

**影响与修复要求：**“拒绝覆盖行为保留”仅对第一次打开成立。应让独占创建、ACL 设置/确认及最终写入绑定同一个文件对象，保留原句柄至写入结束，并避免仅靠路径重查留下等价竞态。补验证后替换/删除路径的确定性回归，保证不覆盖替换目标、不向未经确认的对象写入。属于原 CL-30 第二项的完整落实，不是新增权限模型或一般重构。

### RF2【高 / P1；阻塞 PR #26 与激活】icacls 回退无法证明白名单 ACL，显式额外读授权仍会收到内容

**仓库事实：**PR #26 的 tools/create_auth.py:54 使用 /inheritance:r /grant:r；这会移除继承 ACE、更新指定主体的授权，但不会删除其他主体的显式授权。verify_windows 在 :79 起仅检查 icacls 返回码及输出是否包含 (I)，没有枚举或拒绝白名单之外的可读显式 ACE，也没有实现其 :65 注释声称的“both grants present”检查。

**离线验证：**仅模拟 bzs 包不可导入以进入真实回退；在“空文件已建立、保护尚未执行”时，用真实 icacls 添加 INTERACTIVE（S-1-5-4）的显式读 ACE。随后实际运行回退保护、回退验证和写入。结果成功写入非秘密哨兵；真实 winsecurity.audit_path 独立复核得到 protected=true，且确认该额外主体仍可读。

**影响与修复要求：**没有继承标记不等于只有允许主体可读。回退必须提供与主路径等效的 SID/权限核验；不能可靠核验时在写入前失败关闭。可复用可用的 SID/DACL 读取能力，不要求扩展权限模型。补显式额外授权、核验失败及正常回退的回归，避免把本地化名称匹配当成完整 SID 白名单。

### RF3【中 / P2；阻塞 CL-30 验收】主路径把 protected=False 当作核验成功

**仓库事实：**PR #26 的 tools/create_auth.py:74–78 实际条件是“protected is None or problems”。当 audit_path 返回 (False, []) 时不会拒绝；交接第 2 项和函数注释却要求“protected 且零问题”。False 表示继承保护未建立，不能与 True 合并处理。

**离线验证：**注入保护调用返回、audit_path 返回 (False, [])，调用实际 write_exclusive，函数成功返回且临时文件收到非秘密哨兵内容。现有回归只覆盖保护抛错与核验返回 None，没有覆盖 False。

**修复要求：**成功条件须明确为 protected is True 且 problems 为空；False、None、读取错误均在写入前失败。补 False 的回归。18 项现有测试通过不足以关闭 RF1–RF3。

### RF4【中 / P2；当前文档交付不完整】CURRENT、交接和实际集成状态仍有差异

**仓库事实与离线验证：**

- docs/CURRENT.md:7 仍把 main 头写为 358ae38；本地 main 和三个分支的共同基线实际是 1897398。:31 的“D1–D28 执行完毕”也把 D25/D26/D27 的持续或 S1 后事项、D28 的持续核对义务混为已经完成；应准确写明持续有效和待办。
- 交接 docs/reviews/response-codex-2026-11-15-post-s0.md 的 CL-30 第 2 项及 CURRENT:19 对确认 ACL、拒绝覆盖的完整性描述，被 RF1–RF3 反证。修正前不能登记为 CL-30 已验收。
- 交接指令 4 第 3 项写“新测试全部经 RED（旧代码失败）→GREEN”。把 PR #27 的四个新增测试用于旧 apis.py 后，两个 Mwmbl 401 测试和 Marginalia 503 测试失败；test_auth_failed_stops_the_source_budget_and_survives_resume 仍通过。后者注入既成 SourceError、使用离线适配器，验证的是原有 probe 停源/恢复能力。它是有效的保护性回归，但不能列为旧实现失败的证据。
- CURRENT:38 要求每个改变状态的 PR 同 PR 更新 CURRENT；#26/#27 的 diff 均没有该文件。目前全部状态集中到 #28，尚未满足该规则。s0-results 的裁决及 F2 提交也尚未进入 main，1310cc0 不是 #28 的祖先；#28 对上一轮裁决的链接在其自身树中没有对应文件。
- docs/sources/mwmbl.md:25 附近的“30/天保证月额度”须保留 D-S0-3 的限定：只在所有相关消耗合并计算时才成立，不能替代共享 IP、其他 probe 目录和 searchd 的实际预算核算。:89–90 关于 URL/数据集再分发的概括也应收窄为本文签署的普通搜索链接输出，不表示批量 URL 数据库提取没有任何限制。

**处理：**#28 当前 head 不宜原样作为“已验收”的最终状态入口合入。补记本轮结论、准确版本、测试分类和集成依赖；保留历史 S0 结论及原始证据不动。代码 PR 补各自的最小 CURRENT 状态更新，并按更新后的 head 执行 D28。这里没有授权绕过同 PR 更新规则。

### RF5【记录 / 通过】F2 是真实的字节不变归位，标准 replay 已独立复现

**仓库事实：**817ecef 相对 1310cc0 只有 10 个 R100 搬移和两份新 replay 报告。逐文件比较 git blob 的压缩字节完全相同；所有 raw_path 均能按原报告目录结构读取。

**离线验证：**10 个解压体的长度和 SHA-256 全部匹配 rows[].response；各尝试解压长度依次为 a000001=18、a000004=16560、a000005=9288、a000006=1475、a000007=4451、a000008=3590、a000009=450、a000010=5155、a000011=178、a000012=6758 字节。旧 JSON/Markdown 与最初入库 aea983e 的字节相同。

| 文件 / 身份 | SHA-256 或结论 |
|---|---|
| probe_report.json | 90aa1aa26a8f4307bd7a68d1e659cdf29e34276afda032bbc9b9ddbaf2d25fab |
| probe_report.md | 5164007f3d27b928d26571f7f94459017e86f1eff7c80648a75aa093d900a9ce |
| 归档裁决，289 行 | c6464e93119010cdf3dd7100170f9136b7d8acaf22de6a4e0a9655f45a966178；1310cc0 至 817ecef 不变 |
| replay 解析源码汇总 | 74d1a41ce03d444d1dce68508284e709cf920c1a1de0a0ccff213d696ea9c1f1 |
| 本轮新执行的标准 replay | identity_match=true，10/10 matched，零 mismatch/skip/network |

解析器的 6 个文件及 Python 3.12.10、aiohttp 3.14.5、cssselect 1.6.0、idna 3.20、lxml 6.1.3、trafilatura 2.3.1、yarl 1.25.1 与原记录匹配。归档正文含上一轮 F1–F11、D-S0-1–16 和指令 1–7；本轮可以确认归档后的字节稳定，但没有额外的归档前独立原件来证明首次复制过程。接受当前归档对象作为后续引用基准。F2 内容验收通过不等于该分支已经合入 main。

### RF6【记录 / 通过】F6/F7 的修复绑定正确，覆盖相关真实控制流

**仓库事实：**PR #27 的 apis.py:239 在 Mwmbl.check 中先识别 401，之后才调用基类 check；不再等待真实 search 路径无法到达的 parse 分支。原 parse 分支保留，保护直接解析调用者。测试的 FetchResult 使用真实 fetch 对 4xx 使用的 status=http_error。

Marginalia.check（:205）对 503 采用 max(600000, retry_after_ms(headers))，由现有 SourceError 钳制到 24h；错误说明不再断言共享 key。3600 秒头得到 3600000 ms，60 秒和无头得到 600000 ms。既有退避边界测试随本轮选定套件通过。

**离线验证：**129 项相关测试通过，包含真实 Mwmbl.search 控制流、check+parse、probe 的 auth_failed 停源及 completed resume；三个直接缺陷回归在旧代码上失败。这里“真实控制流”指实际函数加内存假 fetcher，不是联网取得 401/503。本轮没有发现该 PR 的内容合入阻塞。

### RF7【记录 / 边界遵守】范围没有扩张，来源配置保持全禁用

**仓库事实：**#26 仅涉及原 CL-30 两项及测试；#27 仅涉及 F6/F7 及相应回归，未混入 #26；#28 仅改文档。main、三个 PR head 和 817ecef 的 config/sources.json 原始哈希均为 ea53a36f8c3297986bd0dc360cfcd77fb7b92a808ef13dffdcac7c308ff63c4e；17 个来源的运行 enabled 和 contract.enabled 全为 false。

diff 没有修改 accept_s1.py、7 候选适配器注册、B-24、测量/重构实现、CI 范围或其他配置。D26 在本批交付的可见范围内被遵守。Git diff 只能证明交付内容；执行方过去是否另做未记录的网络活动不是本轮离线可独立证明的事实。

### RF8【记录 / 有限依据】mwmbl 新增的是肯定性数据许可依据，可支持窄范围合同

**执行方记录：**docs/sources/mwmbl.md:74–84 留存了 2026-11-15 官方条款核验引文，包括“Data submitted via the Crawling API is made available under the CC BY-NC-SA 4.0 license, consistent with the rest of the Mwmbl dataset”，并说明第三方权利限制；developer 页记载免费额度、速率及每月 1 日重置，未找到专门的搜索 API 缓存条款。

**审核判断：**“consistent with the rest of the Mwmbl dataset”提供全库许可的一致性表述。与已通过的 v2 响应样本、免费 API 接入记录结合，可作为普通查询结果在个人非商业场景复制到本地缓存的项目审核依据；无需把“没有专门缓存句”本身当作许可。现有 links_only 实现把对外 title 改为 URL、snippet 清空，见 src/source_publication.cpp:151 附近及既有缓存/会话调用路径；本批未改变这些限制。

**待核边界：**本轮未独立联网确认引文现时内容、条款完整上下文或各第三方字段的权利归属；每月重置的具体时区也未见记录。数据许可、API 接入条件、AGPL 软件许可分别判断。该依据足以签署 D-R2-5 的受限使用，不支持商业服务、公开原始缓存/摘要库、正文/图片再分发或批量导出整个 URL 数据库。

## 决定（Decisions）

### D-R2-1：指令 1 部分验收；归档接受，状态入口须更正并完成集成

接受 1310cc0 的归档对象及其后保持不变的事实。CURRENT 已补齐“S0 已审、Wikipedia 受限许可、S1 未签收”的主要内容，这部分通过；RF4 尚未关闭，故“归档+CURRENT 完整落地”不能登记为全部完成。修正后应准确区分“实施许可”“代码验收”“条件性来源签名”“已合入”“已激活”。旧 S0 裁决原文不追改，本轮新增决定引用本文。

### D-R2-2：指令 2 完整内容验收通过；保留原身份基线 replay

接受 817ecef 的归位与标准 replay，关闭上一轮 F2 的交付缺陷。执行方将该证据分支按既定集成流程纳入 main，保留归档、压缩字节和旧 JSON/Markdown 不变。

F6/F7 会改变解析器身份；后续若用修复后代码再次回放，必须明确登记为变更后比较，必要时显式使用 --allow-parser-change，并另存结果。不得改原 parser hash、把原身份匹配结果冒充新代码身份匹配，或覆盖本次基线 replay。

### D-R2-3：指令 3 不予验收；仅放行原范围内的补修

doctor 的 SID 白名单化及无法取当前用户 SID 时失败关闭通过；create_auth 的完整安全顺序尚未满足。#26 当前 head 不可合入，不可据其放行激活或 S1。

执行方在同一 CL-30 范围内关闭 RF1–RF3，补“同一文件对象”“回退额外显式读授权”“protected=False”回归，保留成功路径、拒绝覆盖和原失败分支。回退无法可靠确认时允许保守失败，不允许仅凭 icacls 无继承标记写入。修复后的新 head、diff、相关 Windows 测试和失败注入结果交回复审；不把 F6/F7 或一般安全重构并入此 PR。

### D-R2-4：指令 4 的 F6/F7 内容验收通过；marginalia 仍 hold

接受 #27 的修复内容，F6/F7 的实现缺口在该 head 关闭；生产生效仍以合入为条件。补证记录足以支持下一决定的 mwmbl 签名，故其条款缺口可在限定范围内关闭。

marginalia 仍须 D-S0-2 的个人免费 key、真实非空成功 S0、成功 schema 复核及独立合同签名；F7 修好不等于该来源获批。可以继续禁用 marginalia，走 mwmbl 的 S1 路径，不要求先申请 marginalia key。

### D-R2-5：签署 mwmbl 的个人非商业本地缓存 + links_only 合同

**可签署。**签名只覆盖 id/provider/index_family/logical_service_id=mwmbl、mwmbl_api、https://api.mwmbl.org/api/v2/search/ 的普通搜索使用，以及下列完整值和限制。它是本轮基于仓库条款记录的审核决定，不是新的联网条款核验，也不是当前立即启用操作。

在所有激活前置条件满足后，contract 填为：

~~~json
{
  "id": "mwmbl",
  "provider_id": "mwmbl",
  "index_family": "mwmbl",
  "capability": "web",
  "endpoint": "https://api.mwmbl.org/api/v2/search/",
  "adapter_id": "mwmbl_api",
  "enabled": true,
  "logical_service_id": "mwmbl",
  "review": {
    "status": "approved",
    "payment_scheme": "no_payment",
    "automated_access": "approved",
    "checked_at": "2026-11-15T00:57:34+08:00",
    "expires_at": "2026-12-13T00:00:00+08:00",
    "terms_url": "https://mwmbl.org/terms",
    "cache_policy": "allowed_with_limits",
    "redistribution_policy": "links_only",
    "approved_by": "Codex:codex-2026-11-15-post-s0-review:D-R2-5"
  },
  "limits": {
    "max_concurrency": 1,
    "min_interval_ms": 1000,
    "shared_budget_key": "mwmbl",
    "request_quota": 30,
    "quota_window_seconds": 86400
  }
}
~~~

checked_at 是本轮离线合同审核时刻；到期保守沿用现有来源记录的 2026-12-13 本地零点，不自动顺延。terms_url 使用数据条款页；docs/sources/mwmbl.md 同时保留 https://developer.mwmbl.org/ 的接入/配额记录、https://creativecommons.org/licenses/by-nc-sa/4.0/ 许可链接、上游引文和本文决定的引用。若启用时已过期或发现相反/新增限制，先重审。

运行项同步为 enabled=true、default=true、status=approved、kind=api、capability=web、daily_cap=30、min_interval_ms=1000；provider/index_family 保持 mwmbl，params.adapter=mwmbl_api，params.base_url 与合同 endpoint 相同。notes 明确以下受限用途；不要保留“unreviewed”说明，也不要将用途限制伪装成 schema 不支持的新字段。

**cache_policy 的具体范围：**仅供该用户个人、非商业、本地搜索的必要结果缓存及本地审核；保留 Mwmbl 来源、原链接、已有许可/权利说明。允许范围不含链接目标正文、图片的采集，不含向公众提供原始响应、摘要库、缓存数据库或批量数据集。发现明确第三方限制时按该限制处理，本签名不替第三方授予权利。

**redistribution_policy 的具体范围：**沿用源级 links_only，在直接搜索、缓存命中、会话及其他对外投影中保持 title=URL、snippet=""，保留来源识别。普通查询的链接输出许可不能扩张成商业服务、批量抽取/再发布或撤销第三方限制。将来要输出原始标题、摘要、正文或共享数据集，应重新审核实际许可范围及适用的署名、许可回链、修改说明、NC/SA 义务；不能只改一个 policy 值沿用本签名。

**配额口径：**本批仅匿名免费层，1 请求/秒、并发 1，30/天；上游记录为同 IP 1000/月、每月 1 日重置。30×31=930 只是合并计算全部相关用量时的保守估算；probe、不同输出目录、searchd、签收及其他自有客户端须统筹，NAT 下其他人的流量可能令上游更早限流。没有全局月账本，也不能把本地重启/新目录视为额度重置。重置时区不明时不自行假定台北零点可再用；以可确认的上游状态为准。monthly_usage/monthly_limit=null 不表示不限额。401 停止并处理鉴权问题，429/服务阻断按既有停源与退避处理，不换身份/IP 绕过。

本批不发送 mwmbl key、不提高到 60/天，也不开启付费或付费回退。将来使用免费 key、提高额度须按 D23 复核免费层并另作配置变更；不把当前匿名签名直接套用到付费方案。

**启用前置清单：**F2 和原裁决已入 main；RF1–RF3 修复后的 CL-30 已复审通过并合入；F6/F7 已合入；本文及更正后的来源记录/CURRENT 已入库；三个最终合同与运行字段通过既有双重准入和相关 links_only 回归；签名仍有效、用途仍在个人非商业范围、实际免费预算已核算。当前此清单尚未完成。

### D-R2-6：三个 PR 的内容可合性与集成约束

| PR | 当前 head 的内容结论 | 执行方合入条件 |
|---|---|---|
| #26 / 9e7ee6b | **不可合入** | 关闭 RF1–RF3、补同 PR CURRENT、提交新 head 复审；通过后再核对该 head 的 D28 |
| #27 / 8ec3751 | **代码与测试内容可合** | 无须为 RF1–RF3 改本 PR 代码；补本 PR 的最小 CURRENT 状态及准确验证说明，保持已审实现不变，核对更新后 head 的全部 CI 尝试后可独立合入，不必等 #26 才合来源错误修复 |
| #28 / 0adccea | **须更正文档后合入** | 关闭 RF4；加入/引用本文受限签名及 CL-30 未完成状态，明确 s0-results 的集成依赖，记录最终 head 和 D28；不得先宣告激活或 S1 就绪 |

对 #27/#28 的纯状态/事实订正，本裁决已经给出明确内容要求，执行方可据此完成；若改变已审生产实现或扩大签名用途，则新内容另审。#26 的安全修复须复审新 head，不能以“现有 18 项仍绿”替代。

另须将 work/s0-results-20261114 的已审交付纳入 main；它不包含在这三个 PR 的 diff 内，不能认为合入 #26–#28 后 F2 自然已集成。本轮没有获知其 PR 编号，不虚构编号。该分支的旧候选草案仍受 D-S0-6–13 约束，合入归档不使草案成为获批合同。

CI 完全由执行方按 D20/D28 核对：更新后的每个提交、所有非成功尝试、合并后 main；失败如实登记、处理后再合。这里裁定的是内容，不证明 CI 已绿。按既定流程使用精确通过的 head 和 merge commit，不改写历史；状态更新随各自 PR，集中交接不能替代。

### D-R2-7：激活批次有条件许可；本轮结束时仍不得激活

完成 D-R2-5 前置清单、尤其 CL-30 新 head 复审通过后，放行一个独立的来源激活批次，集合为 mwmbl、wikipedia_en、wikipedia_zh；其余来源继续禁用。该条件性许可不要求再就同一用途重审 mwmbl 条款，但未完成条件时不能先置 enabled=true。

Wikipedia 严格沿用 D-S0-4/5：各自专属 approved_by；cache_policy=allowed_with_limits、redistribution_policy=links_only；terms_url=https://en.wikipedia.org/wiki/Wikipedia:Copyrights；checked_at=2026-11-14T15:37:35Z；expires_at=2026-12-13T00:00:00+08:00。en/zh 保持各自端点和 lang，运行 capability=encyclopedia；daily_cap=request_quota=2000、窗口 86400 秒、min_interval_ms=500、并发 1、共享预算键 wikimedia，两语言合计 2000/天。不得复制 mwmbl 或另一语言的签名，也不得宣称两个独立通用 web 索引。

同一激活 PR 内同步三项运行 enabled、contract.enabled、批准字段和用途记录，并将三项运行 default=true。后者有实际必要：accept_s1.py:465 的真实 web.search 没有显式 sources，而 src/web.cpp:577 仅选默认来源；只改 enabled、仍让三个 default=false，会使该调用没有来源。三者 default 的变更也必须进入最终清单哈希。无需改变 accept_s1、选源逻辑或能力分类。

激活 PR 同步 CURRENT，跑既有配置合同及源级 links_only 的相关回归，确认默认搜索确实选择已获批集合；按 D28 合入后冻结用于复探和签收的代码、实际运行清单及其字节哈希。签名、默认值或清单格式再变化，都要重新检查证据绑定。

### D-R2-8：复探和 accept_s1 目前未就绪；按现有入口依次启动

**用户小规模复探的启动条件：**D-R2-7 完成，最终清单/代码固定，合同未过期，免费预算足够，mwmbl 按匿名运行。准备新的输出目录，只测最终三个已启用来源，先每源 1 次；不再执行旧 S0_COMMAND 中包括 marginalia 的四候选命令。

~~~text
bzs probe --sources config/sources.json --only mwmbl wikipedia_en wikipedia_zh --per-source 1 --delay 4 --out <新的复探目录>
~~~

每个最终启用来源均须取得真实、非夹具、非空 status=ok；报告的 identity.manifest_sha256 与实际签收清单原始字节相同。empty 如实保留，需要时仅在预算允许下做有界补充；鉴权失败、限流或阻断先处理，不靠换目录连续重试。保留本次历史 S0 及新报告各自字节、raw 布局和身份，不能改旧报告 hash。

**离线验证：**内存中三个完整合同均获现有 schema/Python 准入接受，manifest_analysis 的 approved 有三个、approved_web 只有 mwmbl；把旧 S0 报告交给新清单检查时，确实得到 identity.manifest_sha256 mismatch。合同已可表达不等于复探已通过。只有两个 Wikipedia 时仍缺通用 web，marginalia 继续禁用不要求个人 key。

**正式 accept_s1 的启动条件：**上述新报告齐备、服务和最终配置可复现启动、最终代码对应的完整 CI/CTest/pytest 与 ASan/UBSan 证据已按 D28 核对，并准备对应 HEAD 的 --ci-run-url。使用明确的 --sources 和 --probe-report，防止误选旧清单/旧报告；完整运行，不能使用 --skip-live 充当签收。示意参数：

~~~text
.venv\Scripts\python.exe tools/accept_s1.py --sources config/sources.json --probe-report <新的复探目录>/probe_report.json --out <新的签收目录> --ci-run-url <最终HEAD对应的已核对CI运行URL>
~~~

九条标准全部 PASS、exit 0、证据对应最终代码和配置后，才可登记 S1 签收。执行方还须核对脚本引用的 CI 证明，不能把给它一个 URL 本身当作独立核实。本文没有运行或预签这些结果。

### D-R2-9：D26 和其余冻结边界继续有效

本轮只批准原范围补修、状态订正、条件闭合后的激活、用户有界复探与签收。不启动 CL-13/15/16/19 余项、测量、一般重构、7 候选接入实现、B-24 扩张、抓取型来源启用或浏览器伪装。不修改签收哈希规则、不删断言换绿、不增加新证明链。S1 后才按 D-S0-16 的候选顺序及 D25–D27 条件进入后续工作。

## 给执行方的指令（顺序与条件）

1. **先归档本轮裁决并更正入口。**原样纳入本文；按 RF4 更正 CURRENT/交接，包括 main 基线、持续规则、CL-30 未完成、#27 测试的真实 RED 范围、mwmbl 受限签名和仍禁止当前激活。保留上一轮裁决和历史证据原文。
2. **集成已验收的归档/F2。**将 s0-results 已审内容按既定 PR 流程纳入 main，状态更新随该集成；保留 10 个 gzip、旧 JSON/Markdown 及原身份 replay 字节。不要重做网络探测来替代证据归位。
3. **处理各 PR。**#26 只补 RF1–RF3 和必要回归、CURRENT，提交新 head 复审，通过前不得合；#27 的已审代码可保留，补同 PR 状态和准确验证记录，D28 满足后可先独立合入；#28 完成 RF4 和本轮合同引用后按 D28 合入。各 PR 及合并后的 main 均核对全部尝试，交接记录实际 SHA，不沿用旧 head 的 CI 为新 head 背书。
4. **仅在 #26 新 head 复审通过、相关修复/归档均已入 main、合同仍有效时，进入激活 PR。**按 D-R2-5、D-S0-4/5 一次同步三个来源的完整合同、enabled/default 和用途说明，跑既有准入/发布限制回归，更新 CURRENT，按 D28 合入。marginalia 和其他候选保持禁用。
5. **激活合入后冻结最终代码与清单，再交用户复探。**使用 D-R2-8 的三个最终来源和新目录，先各 1 次，核算所有相关消耗；取得每源非空真实 ok 及相同 manifest hash。失败/限流按原机制停，不伪造、不改旧报告、不靠换目录绕过。
6. **新报告及最终 HEAD 的 CI 证据齐备后，用户完整运行 accept_s1。**只有九条 PASS、exit 0、身份绑定正确才登记 S1 完成；在此之前继续 D26 和候选实现冻结。

签署：**Codex，2026-11-15（Asia/Taipei）。**本轮新增的来源合同签名仅为 D-R2-5 所列 mwmbl 的限定合同；Wikipedia 沿用原签名。安全修复未闭合之前，任何一个上述签名都不构成当前激活许可。
