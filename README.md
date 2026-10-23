# 不周山搜索工具 · bzs861

**状态：0.1.10 接手后的五项增量。** 从已发布0.1.9继续，增加搜索来源持久发布限制、管理员主体撤权、独立HTTP缓存新鲜度、可分别取消的共享批读，以及fast/balanced/deep搜索执行预算。范围见[接手计划](docs/handoff/S2_TAKEOVER_20261023.md)，实际验证见[当前报告](validation/s2_takeover/deadline-followup-report.json)。S0、S1、S2的完整验收仍未完成。

生产实现原 `final-v2` 本地验证为27/27套CTest、15/15套ASan/UBSan及Python 1,049项，真实schema12→13升级保留39张旧表内容。随后修正Windows测试连接清理，并根据第二次CI暴露的截止时间事件竞态，修正整体期限超时的分类与原子终态结算；原一秒E2E保持不变。旧报告和失败日志保留，最新输入与全量验证见[截止时间后续报告](validation/s2_takeover/deadline-followup-report.json)，公开CI按最新精确SHA对应的Actions核对。

目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。从[当前开发入口](HANDOFF_START_HERE.md)开始；[验证说明](validation/s2_takeover/README.md)区分作者、独立反例、真实升级和最终整合输入，远端CI须核对最终提交自己的[Actions](https://github.com/wmqfl861/bzs861/actions)。[0.1.9报告](validation/s2_next_five/json-race-report.json)及其条件重验证/JSON并发修复证据按原字节保留。


项目自己实现搜索、读取、索引、权限与证据业务，使用基础库处理网络、协议和解析；不安装或调用 SearXNG、Scrapling、Firecrawl、Exa 成品后端。非模型外部服务仅接入经过审核的免费入口，不设付费回退。

## 当前代码范围

| 能力 | 当前边界 |
|---|---|
| 本地导入、搜索与证据 | 保留显式 HTML/TXT 导入、不可变快照、冻结分页、UTF-8 字节定位、原文查找、用户隔离和权限复核 |
| 原生配置合同 | C++ 校验 `model-config`、`source-manifest`、`search-request`；HTTP/CLI 共用逻辑，返回 `applied=false`，不自动注册配置或调用模型 |
| 联网搜索候选实现 | 多源并发、每源状态、URL 去重、索引族融合、冷却和缓存；默认没有已审核真实来源，未核验适配器不能进入正常搜索 |
| 网页读取候选实现 | 缓存指令独立于validator；完整表示按年龄/profile复用或条件刷新，no-store无新持久快照，cache_only陈旧则拒绝；直接出站仍受连接层策略限制 |
| 批量网页读取 | 1–20个显式URL、逐项结果和持久预算；同owner兼容任务在派发时共享一次读取，逐订阅取消和一次计费，收据原子提交 |
| 站点发现 | 独立审核登记、版本化 robots 缓存、逐跳准入和预算；Sitemap / RSS / Atom / HTML / owner 历史产生持久候选，未自动读取或索引候选正文 |
| 有界正文爬取 | 持久正文前沿、页预算、链接边与同源轮转；版本化日期、参数排列/组合、重复分页防护，明确记录停止原因 |
| 页面发布规则 | 页面声明与站点发布上限共同生效；显式站点配置在离线重启后也约束已存结果，旧会话不因后来放宽而恢复 |
| 来源发布与主体撤权 | 来源收紧、停用、移除、过期与主体撤权约束旧缓存/会话/在途响应；放宽来源不恢复旧摘要，撤权需要管理员 |
| 搜索执行模式 | 可查看fast/balanced/deep来源数、候选数和期限上限；覆盖只可收紧，报告省略来源；只搜索，不自动读取或调用模型 |
| 来源探测与回放 | 唯一运行、预留与检查点、显式恢复、不覆盖旧样本；原始响应按 SHA-256 绑定，可离线比较完整解析结果 |
| 黄金查询集 | 原有 40 条内容保持不变，补充冻结清单与开发/回归/保留集元数据；按来源能力跨类别选样，默认排除保留集 |
| 检索改进候选实现 | 中文索引、BM25 与带字节区间的原文片段；与既有精确字面和查询语义一起回归 |
| Agent 接口 | MCP 映射同一核心，优先 stdio；本机带令牌 HTTP 是远程接口准备，不代表可公网部署 |
| 任务历史回收 | 至少七天后压缩稳定终态明细，永久保留 key/指纹/计费摘要；活动与未结算任务受保护，无引用过期 robots 可回收 |
| 持久调度与会话 | 批读与map/crawl持久轮转；联网搜索冻结版本、游标和元数据事件，支持重连、去重、取消、过期与背压 |
| 固定候选评测 | 未标注人工模板、输入/方法/阈值/环境冻结、来源贡献与外部消融配对；固定全池IDCG，缺失/失败不当空结果，完整质量门禁仍阻塞 |
| 隔离备份 | 安全恢复未完成；独立撤销连续性未证明前，备份不能直接启动服务 |

尚未交付动态 JS 页面、PDF/Office、完整爬虫工作包、模型规划/向量检索/研究、远程多客户端发布和 Windows 安装包。**83 个工作包、161 项验收 ID** 全部保留；37 包部分实现、46 包未开始、0 包完整签收，见 [蓝图 v1.2](docs/planning/development_blueprint_v1.2.md) 和 [开发进度](docs/DEVELOPMENT_STATUS.md)。

## 架构

Agent 经 `bzs-mcp` 调用回环 HTTP 上的 C++ `searchd`。核心负责授权、合同、预算、来源准入、调度、存储、检索与证据，通过独立令牌的回环 HTTP 调用 Python `bzs-worker`。worker 执行网络和解析，并再次检查来源准入与目标地址。

MCP 不复制业务规则，worker 不写数据库。进程分离不等于操作系统沙箱；限制见 [安全说明](docs/SECURITY.md)。

## 安装与启动（Linux）

需要 C++20 编译器、CMake ≥ 3.25、Ninja、SQLite ≥ 3.38（含 FTS5）、libxml2、json-c、OpenSSL、Boost 头文件及 Python。支持范围和实际依赖版本以 [依赖说明](docs/DEPENDENCIES.md)、`python/pyproject.toml` 和本轮报告为准。

```bash
sudo apt-get install -y build-essential cmake ninja-build libsqlite3-dev libxml2-dev libjson-c-dev \
  libssl-dev libboost-dev python3-venv

cmake --preset release
cmake --build --preset release -j 2
python3 -m venv .venv
. .venv/bin/activate
pip install -r tools/requirements-s1-lock.txt
pip install --no-deps --no-build-isolation -e "python[test]"
bzs init
bzs start
bzs status
```

当前启动器按仓库的可编辑安装布局寻找配置与二进制。`bzs init` 创建本地令牌、来源配置和空站点发现登记；默认未审核来源保持禁用。既有用户配置不会因为源码清单改变而自动取得审核资格。

可以先调用离线能力和纯配置校验：

```bash
bzs call capabilities '{}'
bzs call contract.validate '{"kind":"model-config","config":{"policy":{"allow_reference_backends":false,"external_nonmodel_services":"reviewed_free_only","allow_provider_hosted_search":false,"allow_unconfigured_fallback":false},"default_text_profile":null,"profiles":{},"roles":{}}}'
```

正常联网搜索需完成来源审核、实测并显式启用。读取命令只接受符合出站策略的公开目标：

```bash
bzs search-modes
bzs search "断点续爬 实现" --mode balanced
bzs read https://zh.wikipedia.org/wiki/网络爬虫
```

没有合格来源时明确报告来源不可用，不自动使用未核验回退。S0 步骤见 [S0_PROBE](docs/S0_PROBE.md)；候选探测是显式联网动作，不自动批准或注册来源。

### 显式批量读取

```bash
bzs call read_many.submit '{"urls":["https://example.com/a","https://example.com/b"],"idempotency_key":"batch_1","max_requests":12,"max_bytes":16777216}'
bzs call job.get '{"job_id":"替换为返回的任务ID"}'
bzs call job.results '{"job_id":"替换为返回的任务ID","offset":0,"limit":20}'
```

任务只读取列出的 URL。完成项提供不可变快照收据；单项失败不会自动重试。默认每个 URL 只有一次授权尝试，崩溃后的未知执行保留预算消耗。`job.control` 提供 `pause / resume / cancel / retry_failed`；恢复不重发完成项，也不重置期限和预算。详细参数、用量与取消边界见 [API](docs/API.md#持久批量网页读取)。

### 有界站点发现

先用 `bzs call map.sites '{}'` 或 MCP `list_sites` 查看部署中已审核的站点。默认 `sites.json` 为空，搜索来源审核不批准任意结果网站。`map.submit` 只接受已审核范围内的显式发现入口，使用 `job.get/results/control` 跟踪；Sitemap index 可继续发现文档，HTML/Feed 链接只保存为候选。

每次 HTTP 跳转和 robots 获取都计入持久预算。任务不生成候选的正文快照，也不承诺完整全站覆盖。配置、URI 范围、缓存策略、停止原因与上限见 [站点发现](docs/SITE_DISCOVERY.md)。

### 有界正文爬取

`crawl.submit` 从已审核范围内的明确入口递归读取静态页面，保存快照和链接边。省略 `max_pages` 时使用 `min(100,max_urls)`；页面、请求、字节、URL 数和深度分别有界。查看正文结果使用 `job.results`，查看链接来源与扩张决定使用 `crawl.edges`。示例中的站点需要由部署方实际登记和审核：

```bash
bzs call crawl.submit '{"site_id":"operator-reviewed-site","seeds":["https://reviewed.example/docs/"],"idempotency_key":"docs_crawl_1","max_pages":30,"max_urls":100,"max_depth":3,"max_requests":80,"max_bytes":16777216}'
bzs call crawl.edges '{"job_id":"替换为返回的任务ID","offset":0,"limit":20}'
```

页面当前的 noindex / 摘要限制在每页提交时执行，旧搜索会话也必须遵守。正文快照与允许公开的摘要分别保存；站点 `links_only` 限制不会被普通读取刷新扩大。具体页预算、恢复、声明处理及限制见 [正文爬取](docs/SITE_CRAWLING.md)。

## 任务回收、持久会话与评测

```bash
bzs jobs compact --limit 16
bzs call web.search.start '{"query":"example","sources":["operator-approved-source"],"idempotency_key":"search_001"}'
bzs call session.events '{"session_id":"替换为返回的会话ID","after_event_id":0,"limit":20}'
```

任务回收只释放符合固定七天资格的终态明细；旧键和累计计费保留，明细过期返回410。见 [任务保留](docs/JOB_RETENTION.md) 和 [公平调度](docs/TASK_SCHEDULING.md)。联网会话仍需要真实获准来源，分页绑定不可变结果版本；事件只传状态和版本身份，内容每次分页重新鉴权。见 [持久会话](docs/SEARCH_SESSIONS.md)。

`bzs evaluation prepare/freeze/run` 用于离线固定候选评测；可选外部消融manifest绑定实际base/ablation排名及来源身份，评测层不重新实现生产融合。具体文件合同见 [相关性评测](docs/RELEVANCE_EVALUATION.md)。模板不会自动填写人类等级，原黄金查询与20/15/5划分保持；没有真实人工标签与完整质量证据时，不能将合成计算结果当作产品验收。

## 接入 Agent

优先用 `bzs mcp-config` 生成适合本机的 stdio 配置。配置形状如下，路径需替换：

```json
{"mcpServers":{"bzs861":{"command":"/path/to/bzs861/.venv/bin/python","args":["-m","bzs.cli","mcp"]}}}
```

MCP 工具为 `web_search`、`read_url`、`read_range`、`find_in_page`、`search_local`、`cite_evidence`、`list_sources`、`read_urls`、`get_job`、`job_results`、`control_job`、`list_sites`、`map_site`，`crawl_site`、`crawl_edges`、`compact_job_history`，以及 `start_web_search`、`get_search_session`、`search_session_events`、`search_session_page`、`cancel_search_session`。批量读取和 crawl 按快照收据续读正文；map 返回发现候选和来源，三者共用持久任务接口。运行能力与准入由核心决定；网页正文和搜索摘要是外部数据，不赋予 Agent 新权限。

本机 HTTP MCP 需要每客户端令牌：

```bash
bzs mcp-token add laptop
bzs mcp --transport streamable-http
```

它只监听回环地址，完整 TLS、按客户端限流和审计验收仍属于 S5。无鉴权 HTTP 启动与生产代理放宽选项不属于本候选版允许配置。

## 网络与来源配置

本候选版拒绝生产使用 `proxy`、`proxy_remote_dns` 和非空 `allow_cidrs`，防止重新解析或网段放行绕过连接层检查。假网站回环映射仅用于受控 fixture，不是用户内网访问能力。网络不可达如实报错，不能靠放宽策略掩盖。

适配器元数据、审核状态和启用状态分别保存。审核需覆盖免费条件、自动化访问、条款和复核日期；可用率不能证明这些条件。调用方不能选中未获准来源，不能从 worker RPC 绕过核心准入。详见 [API](docs/API.md) 与 [安全说明](docs/SECURITY.md)。

## 验证

```bash
cmake --preset dev -DBZS_PYTHON="$PWD/.venv/bin/python" -DBZS_REQUIRE_PYTHON_TESTS=ON
cmake --build --preset dev -j 2
ctest --preset dev
```

本轮测试分为原生核心/批量/发现/爬取/发布/合同/回收/公平调度/会话、HTTP/CLI、受控站点端到端、Python worker/MCP 和原生 ASan/UBSan；源码哈希、实际结果、环境、命令与原日志见 [本轮报告](validation/s2_next_five/json-race-report.json)。这些套件存在交叉覆盖，不能按总数替代 161 项产品验收。0.1.2 的有限云端来源探测保留在 [历史云端观察](validation/s0_s2_progress/cloud_probe_summary.json)，不代表本次新增实测、用户网络、人工相关性评分或来源批准。GitHub Linux/Windows 结果应检查本提交对应的 Actions。

`validation/test_report.json` 与对应旧日志为 0.0.1 历史；`validation/takeover/` 为 0.0.2 证据；`validation/s1_test_report.json` 是 Claude 随包的 0.1.0 声明；`validation/s1_review/` 是 0.1.1 整合证据；`validation/s0_s2_progress/` 是 0.1.2 证据；`validation/s2_discovery/` 是 0.1.3 证据。历史记录保留原输入与结果，不能代替当前提交的验证。

## 已有数据与后续范围

两条开发分支曾各自使用 `user_version=2` 表示不同结构，不能仅凭版本号判断兼容。迁移必须识别来源、保留快照/证据/删除与隔离状态，并用旧数据库回归。旧 HTML 不会因更新解析器自动清洗；备份依旧保持隔离。最终迁移版本与实测见 [开发进度](docs/DEVELOPMENT_STATUS.md)。

下一步继续补用户环境的来源审核与 S0 证据、冻结质量基准的人工标签和阈值，完善爬虫工作包的陷阱与长期恢复验收，推进独立撤销日志、隔离恢复及会话真实长期运行与故障验收。单用户先行继续保留撤权、恢复和多客户端阶段的等效验收。

## 文档与许可

[API](docs/API.md) · [原生配置合同](docs/CONFIGURATION.md) · [安全](docs/SECURITY.md) · [依赖](docs/DEPENDENCIES.md) · [Windows](docs/WINDOWS.md) · [开发进度](docs/DEVELOPMENT_STATUS.md) · [接手入口](HANDOFF_START_HERE.md)

项目许可证由仓库所有者决定，目前未指定；依赖各自的许可继续适用。完整依赖与许可证发布审查尚未完成。
