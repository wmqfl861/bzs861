# 统一搜索与网页采集平台
# 开发蓝图与实施计划 v1.1

**日期：2026-10-16｜关联规格：functional_spec_v0.3.md｜状态：供开发执行的设计，不是已完成实现。**

## 0. 本次交付与决策摘要

本蓝图覆盖83个工作包、9个里程碑和161条验收条目。完整产品范围包含前次评审的全部新增功能；通过分期管理风险，不将多媒体、私人资料、订阅或精确搜索降为“仅预留”。

采用：**C++20模块化核心 + 独立浏览器/解析/媒体工作进程 + 单机起步的版本化存储/全文索引 + 用户配置模型 + 免费来源直连适配器**。不安装或调用SearXNG、Scrapling、Firecrawl、Exa后端。基础组件复用与业务独立实现不是矛盾。

团队/硬件/正式项目名未知。本蓝图中的组件是首选技术方案而非用户已经逐个批准的依赖；有兼容或许可问题时用ADR记录替换，不改变产品约束。人日为规划估算，不是日历交付承诺，也不是对自动编程速度的承诺。

**审核状态：本轮为作者自审与可执行文档校验；未调用独立子 Agent，不构成独立批准。完整决定见 `review/review_report.md`。**

**直接开工入口：先执行M0，将QueryAST、SearchSession、Snapshot、Evidence、Job和SourceManifest锁成合同，再并行推进采集和多源搜索。**

## 1. 产品构成与部署形态

### 1.1 一个产品、多个受控进程，而非多个搜索产品拼装

```text
浏览器界面 / CLI / 外部Agent
             │
      HTTP API / MCP适配层
             │
           searchd
   认证与权限 │ 合同校验 │ 能力目录
   查询编译   │ 来源调度 │ 搜索会话
   任务控制   │ 存储索引 │ 证据与模型编排
             │
      有界任务队列/事件/预算
     ┌───────┼────────┬────────┐
  HTTP采集  browser-worker  parser-worker  media-worker
     │       Chromium/CDP    安全解析库     解码/OCR/ASR
     └───────┼────────┴────────┘
             │
     原始产物 → 正文版本 → 索引 → 证据
             │
       自有全文/向量/资产检索
```

`searchd`负责所有业务准入，不给工作进程全量数据库与模型密钥。`searchctl`只是客户端。`search-worker`按能力启动不同进程，回传限大小的产物引用和执行状态。将来增加节点仍使用同一任务合同，不另造一套爬虫。

首个部署无需Redis、RabbitMQ、Kubernetes、外部向量数据库。单机数据库由核心服务管理，远程worker不直接挂载数据库文件。只有实测瓶颈触发替换协调存储或索引分片，而非根据“功能很多”提前拆微服务。

### 1.2 三种运行配置
| 配置 | 目标 | 组成与边界 |
|---|---|---|
| 单机开发 | 功能调试与固定样例 | 核心、嵌入存储、少量worker；外网默认关闭的fixture模式 |
| 单机实际使用 | 个人/小团队搜索与采集 | 本机持久存储、受控浏览器池、用户模型、明确外网策略；需要测试后确定并发 |
| 多worker部署 | 更高采集量和故障隔离 | 核心协调、远程隔离worker、认证RPC、全局配额；不共享SQLite WAL文件 |

网页服务器与远程模型都可以在网络上，数据仍由策略控制。`network_mode=offline`关闭外部搜索、网页和远程模型；`cache_mode=only`只禁止该采集动作联网，二者不是同义词。

## 2. 技术基线与选型理由

| 层 | 首选方案 | 决策与风险 |
|---|---|---|
| 语言/构建 | C++20、CMake、固定依赖清单 | 为Windows/Linux建立同一核心；本项目Result<T>而非误用C++23专属类型；锁定实际构建版本 |
| API网关 | Drogon，业务逻辑不依赖其ORM | 官方支持Linux/Windows与C++；只作为HTTP/事件传输基础，不把框架QPS当产品QPS。[S05] |
| HTTP客户端 | libcurl multi | 使用其异步多请求接口；本项目负责URL准入、节流、内容校验和重试。[S03] |
| 动态浏览器 | Chromium + 自研CDP驱动 | 协议可控制浏览器目标；浏览器版本和协议能力需固定并测试；这是基础组件，不是Firecrawl后端。[S04] |
| HTML/CSS与XML | Lexbor；XPath/XML使用独立受限适配 | Lexbor提供HTML/选择器基础；正文识别、质量判断、统一定位由本项目实现。[S06] XPath与HTML树差异必须有位置映射测试 |
| Unicode/中文 | ICU + OpenCC + 领域词典 | ICU提供词边界；OpenCC处理简繁；不覆盖原始正文，不承诺机械转换解决全部中文语义。[S07][S08] |
| 元数据/队列 | 单机SQLite WAL，短事务单写入口 | 适用于本机起点；WAL不放网络文件系统，单写者限制要实测。[S02] |
| 全文检索 | SQLite FTS5 + 自研tokenizer和AST编译 | 有BM25、字段/布尔/短语与扩展接口；字符精确语义和中文偏移需自己补，不把FTS默认行为当完整需求。[S01] |
| 向量检索 | hnswlib C++，可重建派生索引 | 支持近邻和C++过滤样例；事务、水位、ACL、分片和删除一致性是本项目工作。[S09][S10] |
| PDF/Office | PDFium候选；安全ZIP/XML与自研结构抽取 | 提取文字/页信息不等于自动正确解析版面；Office不执行宏。基础库最终许可/构建分别固定。[S21] |
| 代码结构 | Tree-sitter可选基础解析 | 有增量语法树能力，不提供所有语言编译级语义；语言grammar分别审核。[S18] |
| 媒体 | 经审查的FFmpeg构建 + 本地ASR或用户模型 | 编解码构建选项影响许可；不启用未经批准的nonfree构建，不能假定整个媒体栈一个许可。[S20] |
| 语音转写 | whisper.cpp可选基础推理或用户ASR端点 | C/C++推理组件可复用；模型权重许可、资源和质量需另核验。[S19] |
| 模型 | 自研协议适配与profile路由 | 用户端点是唯一来源；先文本/embedding/vision/ASR能力合同，再接具体协议 |
| 前端 | TypeScript静态Web应用 | 仅作操作台；本地打包资源、无第三方遥测和字体依赖；C++ API是唯一业务入口 |

版本冻结文件记录：组件名、上游地址、commit/tag、校验和、构建选项、许可证及第三方依赖。许可审查先于分发；不先宣称最终产品必定可以某种闭源/开源许可发布。

## 3. 代码组织与模块职责

```text
search-platform/
  apps/searchd/                 # 主服务
  apps/searchctl/               # CLI
  workers/browser/             # CDP与浏览器进程
  workers/parser/              # HTML/文档解析隔离
  workers/media/               # OCR/ASR/媒体解码
  libs/contracts/              # DTO、Schema、错误码与能力
  libs/core/                   # Clock、Budget、Cancel、Result、ID
  libs/auth_policy/            # ACL、出站与内容发布策略
  libs/query/                  # Lexer、AST、规范化、保护字面
  libs/source_registry/        # 免费审核、能力与健康
  libs/source_adapters/        # 独立实现来源协议
  libs/search/                 # 计划、融合、过滤、会话与分面
  libs/fetch/                  # HTTP获取、缓存和响应状态
  libs/crawl/                  # frontier、Sitemap、scope、礼貌抓取
  libs/extraction/             # 正文、结构块、偏移与元数据
  libs/storage/                # Document/Snapshot、blob、事务与outbox
  libs/index/                  # Lexical/Vector/Asset后端合同
  libs/evidence/               # 引文定位与支持性状态
  libs/model/                  # 用户profile、协议与请求预算
  libs/research/               # 研究步骤、字段核验、资料集
  libs/workspace/              # 历史、收藏、偏好、订阅与导出
  libs/platform/               # Linux/Windows安全和进程差异
  api/                         # OpenAPI设计及MCP/CLI合同
  schemas/                     # JSON Schema与合法/非法例子
  migrations/                  # 单向升级与回滚策略
  adapters/manifests/           # 来源清单，不含密钥
  web/                         # 界面、阅读器和媒体时间轴
  tests/unit/ integration/ e2e/ security/ replay/ benchmarks/
  docs/adr/                    # 架构决策记录
```

不允许方向倒置：query/core不能依赖具体搜索源、UI或浏览器；source adapter不能直接写数据库；模型编排不能创建未授权网络连接；索引不能自行抓网页；UI不能跳过权限去读取本地blob。

### 3.1 最小业务端口
以下是接口设计签名，不是已经实现、编译或可链接的SDK。`Task/Result/Context`均由本项目提供。

```cpp
struct Context { RequestId request; AuthScope auth; BudgetRef budget;
                 Deadline deadline; CancellationToken cancel; };

struct ISourceAdapter {
  virtual ~ISourceAdapter() = default;
  virtual Task<Result<SourceBatch>>
    search(const CompiledSourceQuery&, const Context&) = 0;
};
struct IPageFetcher {
  virtual ~IPageFetcher() = default;
  virtual Task<Result<FetchArtifact>>
    fetch(const FetchRequest&, const Context&) = 0;
};
struct IDocumentParser {
  virtual ~IDocumentParser() = default;
  virtual Task<Result<Extraction>>
    parse(const ArtifactRef&, const ParseOptions&, const Context&) = 0;
};
struct IIndexBackend {
  virtual ~IIndexBackend() = default;
  virtual Task<Result<IndexReceipt>>
    apply(const DocumentMutation&, const Context&) = 0;
  virtual Task<Result<CandidateBatch>>
    query(const IndexQuery&, const Context&) = 0;
};
struct IModelAdapter {
  virtual ~IModelAdapter() = default;
  virtual Task<Result<ModelOutput>>
    invoke(const RoleRequest&, const Context&) = 0;
};
```

进程间不传C++对象指针或ABI相关结构。采用明确版本、长度限制、request_id、lease_epoch、deadline_remaining与artifact token的RPC消息。可信基础库可静态/动态链接；用户扩展默认独立进程，原生动态插件不能当成天然沙箱。

## 4. 搜索主流程及算法边界

### 4.1 查询编译和严格筛选
```
输入 → 词法/AST → 保护字面 → 语言与意图 → 硬条件/软偏好
     → 按来源能力编译 → 来源查询/自有检索
     → 候选去重 → 按需读取 → 三值条件核验
     → 合格候选补足 → 排序/分面 → 冻结ResultSet
```

语法基础：NOT > AND > OR；空白隐式AND；`"..."`表示短语或literal模式；操作符识别只在规定位置触发。`site:`按主机与明确子域规则处理，不做任意字符串包含；`filetype:`按真实类型核验，不只看扩展名。日期ISO格式并绑定时间字段。代码标点和URL不是任意操作符。

将查询AST序列化、哈希并记录parser_version。模型只能产出候选QueryPlan，由程序检查protected literals、约束等价性和查询上限；不要求模型证明任意逻辑公式等价，限制为可检验的受控变换。

native过滤不能直接标true；来源可能忽略条件。硬筛选在本地索引应先限制授权/字段范围再做top-k；外部来源支持原生参数则使用，否则候选读取核验。过滤导致不足可在预算内加深上游页数；超预算返回scope和unknown，而非全网无结果。

### 4.2 基础融合、精确召回与多样性
初始融合用按来源排名的可解释方案，例如：
`RRF(d) = Σ_g w_g / (k + best_rank(d,g))`。
g是检索贡献桶，不是独立证据计数。已知同族按`family_id + capability`分桶；未知按`provider_id + capability + logical_service_id`分桶，绝不把所有unknown放入一桶。别名由注册表审核，同服务多adapter只取约定贡献。未知桶贡献可设单独权重上限，但不宣称来源独立。k和权重是配置，不是固定质量保证。

词项召回与向量召回分别保留原排名，融合后可用用户重排模型。精确标识符和字段匹配有独立召回通道，避免被语义近似淹没。文档与段落两级索引：先找文档，再选相关块，不能让同一长文占满top-k。

多样性只在满足硬条件的候选间处理。单域折叠/主题覆盖是软目标，可对导航型、site限定或特定官网查询关闭。文本近重复、版本关系、主题相似分别记录；合并可回滚。

质量过滤优先识别错误页、空壳、明显无关、模板污染，保留原因。来源可信度与内容真实性不是同一量，未经核验的评分不包装成“可信百分比”。

### 4.3 快速/均衡/深入
配置给出来源数、查询数、读取页数、浏览器并发、模型调用、总deadline。快速默认只检索不读全文；均衡读少量候选；深入按缺口补搜。数值必须在目标机器上测量后设定，不写虚假P95承诺。

首次结果TTFR与最终结果耗时分开；UI显示计划、已完成来源和截止状态。存在“有结果但条件待核验”的阶段，不能在后台核验未完成前标全部满足。

### 4.4 会话一致性
ResultSet是有序的物化文档ID/结果版本集合，不长期持有SQLite读事务作为网页分页会话，避免WAL增长。上游cursor只保存在服务端；用户cursor是不可篡改、绑定租户/会话/版本/有效期的引用。

流式预览使用result_added/result_updated/source_finished等事件；freeze产生稳定rank_version。后续扩展产生新版本，旧版本直到TTL内可读但每次重新检查ACL；撤权/删除导致E_SESSION_INVALIDATED。数字页码只能针对已物化的范围，不能伪造“跳到第1000页”的能力。

分面统计标明统计集合、截断和近似状态。连续筛选要声明filter_current_results或rerun_search，两者不能悄悄互换。

## 5. 来源并集接入计划

### 5.1 不是搬运代码，而是建立可维护的适配器目录
清点参考工具公开的底层来源名称与能力；加上新闻、科研、馆藏等额外公共接口候选。读取文档和协议后独立实现，不复制来源适配代码而忽略许可。能访问不等于允许产品自动访问；服务没有收费不等于无限自动化。

每项SourceManifest至少记录：
`provider_id, index_family, capability, endpoint_id, adapter_id, terms_url, checked_at,
free_status, auth_type, automated_access_status, cache_policy, redistribution_policy,
rate_policy, native_filters, page_policy, supported_languages, enabled`。

可用状态分为`candidate/reviewed/enabled/cooldown/disabled`的不同维度；审核状态与运行健康不能合并成一个绿灯。示例配置默认candidate+disabled，绝不伪造真实审核。

### 5.2 每个来源的完成流程
候选登记 → 底层关系去重 → 免费与自动访问审核 → 请求/响应fixture →
独立适配实现 → 分页/语言/过滤合同测试 → 小流量真实验证 →
限流/缓存参数 → 启用 → 定期复核 → 失效停用或修复。

首批3–5源验证不同类别；其中web Alpha必须至少1个通过免费/自动访问审核并完成固定查询验证的通用web源，否则web能力不可宣告通过（不影响领域源和fixture开发）；下一批5–10源验证增量价值。所有清点出的不重复候选均应有处置结果；其余适配器按审计清单继续追加，不用“首批数量”冒充已经实现全部来源。当前总人日估算只覆盖这两批预算，长尾新增源另按复杂度估算。

单源工作量规划参考：规范公开API约2–4人日，HTML解析与复杂分页约4–8人日，依赖动态页面的入口约6–12人日；均含基础fixture而不包括不可控上游变动。以上为项目估算，不是测得平均值。源码适配器再多，不能抵消来源不可获准或不稳定的问题。

## 6. 获取、爬虫与解析流水线

### 6.1 统一获取器
URL准入→DNS与地址检查→预算预留→HTTP获取→内容状态分类。
`render=never/auto/always`明确执行路径。auto只因可解释的动态内容信号升级；解析差不一定要开浏览器。升级仍消耗统一预算。

浏览器动作只允许受控的wait/expand/scroll/next-page，数量/导航/字节/总时长有限。系统内部提取脚本固定版本，不允许调用方提交任意脚本。跨域iframe、SPA、服务工作者和CDN都要受到同一出站边界；CDP请求拦截只负责可观测性和内容调度，不能作为唯一网络沙箱。

Cookie/Authorization按批准的站点和会话作用域保存，重定向不得默认跨站转发。遇到验证码、访问限制或登录失效，报告受限或要求授权，不通过轮换身份规避限制。

### 6.2 frontier、预算和公平性
frontier条目包括URL_identity、scope_version、parent、depth、priority、state、lease与attempts。
唯一性以任务/规范身份/读取profile定义；共享抓取只在身份、权限与表示兼容时进行。

max_pages统计不同批准目标（含失败）；重试增加请求计数不增加不同页；重定向/子资源增加请求/字节。每次派发前原子预留，终结后对账；无实际统计时标未知。跨任务同源配额在source/host调度器统一执行。

优先序列：用户交互读取 > 有截止时间搜索 > 普通批量 > 后台刷新，带老化避免长期饿死。具体比例按实际负载调整。页面/媒体/模型分别有队列和并发，避免一个视频占满全部读取槽。

### 6.3 正文/结构、格式与多媒体
article模式抑制导航和推荐；document模式保留层级、代码、列表和表格；full_clean提供清理后完整内容。站点规则优先但需版本回归，自适应匹配不可靠时返回uncertain。

DOC-01实现PDF/TXT/MD/CSV及开放Office容器格式；文件加载在沙箱中，禁用宏/外部实体/主动内容，限制ZIP解压和嵌套。PDF复杂阅读顺序、跨页表格、合并单元格必须做专门样例，不能只返回散乱文本便宣称高质量。

OCR/视觉仅在文字层不足时使用。每个识别块有页/坐标/识别配置/质量状态。模型描述是生成内容，不进入原生quote字段。

媒体资产与落地页独立。字幕、ASR和视觉关键帧各自产物与时间轴；播放定位使用ms半开区间。只下载有权限取得的媒体，不实现DRM绕过。采样画面只支撑被观察时间段，不推断所有未观察内容。

反向图片路径：文件hash/感知hash → 图像向量 → 可选跨模态文本 → 候选视觉核验；自有图库功能必须可运行。外部以图搜图若无已审核免费入口，返回local-only，而不是偷偷上传到外部或声称全网覆盖。

## 7. 存储、索引和一致性设计

### 7.1 数据实体与约束
| 实体 | 关键字段 | 约束/索引 |
|---|---|---|
| tenants / principals / acl_rules | 身份、角色、集合授权、acl_epoch | 每次发布读取检查；不由客户端自行声明权限 |
| sources / endpoints / adapters | provider、index_family、capability、policy、review | entry唯一，条款变更有版本 |
| query_plans | 原查询、AST、语言、保护字面、派生查询、版本 | 不可变版本，不保存私有思维链 |
| search_sessions / result_sets | owner、query_hash、rank_version、expires、acl_epoch | frozen集合的顺序不可原地改写 |
| search_hits / source_runs | URL、title、snippet、source_rank、来源状态 | discovery与publisher分开，native不等于verified |
| documents / url_aliases | 稳定文档ID、原URL与别名、身份依据 | 别名合并有证据，可撤回 |
| artifacts / snapshots | blob_ref、fetch信息、内容hash、时间、保留期 | blob按租户命名空间，不跨私域暴露hash |
| extractions / blocks | parser_version、canonical_text、字段与偏移 | 文本不可变，span在UTF-8边界 |
| evidence / claims | 原文片段、版本、页/行/时间；支持关系 | 引文存在与支持性分别检查 |
| jobs / items / leases / budget_ledger | config、scope、state、fencing、原子额度 | 幂等键+请求hash；旧租约不可提交 |
| outbox / index_receipts | mutation_version、水位、lexical/vector状态 | 幂等消费者；失败可重放 |
| media_assets / segments | 图像/音频/视频ID、文件、时间、权利 | 原图/缩略图/页面分开；转写不是原字幕 |
| entities / fields | 实体ID、版本、字段、证据、status | 同名不自动合并；unknown可表达 |
| model_profiles / model_runs | 端点引用、角色、能力、prompt版本、usage | profile版本固定；密钥只存secret_ref |
| workspaces / bookmarks / preferences | owner、选择结果、标签与偏好版本 | 收藏不自动授权长期正文保存 |
| subscriptions / subscription_runs | query_version、时区、周期、seen/watermark | 执行失败不推进成功水位 |
| export_jobs / notifications | owner、内容清单、收件目标引用、TTL | 发布前重新鉴权，外发目标获准 |
| tombstones / audit / backup_manifests | 删除事件、保留记录、备份水位 | 从旧备份恢复时重放删除/撤权 |

### 7.2 提交路径
下载产物写入同文件系统临时文件 → 校验大小/内容hash →
原子重命名为blob → 数据库事务写Snapshot/Extraction/FTS mutation/outbox →
提交可见水位 → 发出index_updated事件。

数据库与文件系统没有天然跨资源事务：未引用blob可被GC；已引用但丢失blob必须明确损坏并触发恢复，不能返回空文档当成功。正式实现要测试电源/磁盘满、fsync、目录同步与Windows替换语义，不能只依赖rename的名字。

单机全文元数据和FTS更新尽可能在同库事务，应用层保证触发器/外部内容表一致性。向量写入通过outbox异步，mutation id幂等；搜索报告lexical_watermark/vector_watermark。跨库扩展后采用版本事件和授权发布屏障，不声称所有派生索引瞬时强一致。

### 7.3 全文、向量与权限
中文tokenizer有规范词形、原始字节位置和变体映射；数字/代码另建精确字段。默认FTS短语是token序列，不等于字节完全相同；literal查询需要原文核验。[S01]

向量按模型ID/模型版本/维度/距离/归一化/分块版本分空间。模型变更重建并切换，不混合。过滤要在授权候选准入时执行；严格/小候选可暴力计算，ANN路径需要增大候选或返回不足。任何后过滤都可能减少召回，不能把删掉不合格结果的top-k冒充完整filtered top-k。

租户词典、补全、聚合、缓存和评分统计默认不跨租户。高度敏感集合使用独立索引命名空间，避免通过词频/计数透露无权资料。ACL结果裁剪要在排序输出与分面之前；权限中途改变使旧会话失效。

### 7.4 删除与保留
逻辑撤销立即提高acl_epoch并写tombstone，阻断所有发布入口；后台清理FTS/向量/blob/缓存/导出。保留审计不保留被删正文。运行中模型已收到内容不能撤回，应记录已出站事实并取消后续调用；未来调用不得复用被撤材料。

索引可重建，证据不可用同一URL最新页面替代。访问到过期快照返回E_EVIDENCE_EXPIRED。允许资料库长期保存必须有自己的使用/授权策略，不把缓存开关当授权。

## 8. 模型与证据研究架构

### 8.1 角色与能力
planner/extractor/verifier/writer可使用同一文本profile，也可各自设置模型/思考等级/超时。embedder/reranker/vision/asr按实际协议能力验证。授权任务覆盖仅能引用服务端配置，不接受任意base_url或密钥。profile撤销后旧任务也不能继续使用。

适配器负责供应商格式、流式事件、结构输出、取消/超时与usage；上层统一调用。仅OpenAI兼容不等于思考参数、工具调用、向量接口与视觉都兼容。[S16][S17] 工具调用由本系统执行，供应商托管网页/浏览器工具保持禁用。

### 8.2 请求组织与缓存
稳定system模板/工具列表/Schema顺序放前，问题与证据置于变化区；prompt version、model profile、input snapshot和权限构成应用缓存键。网页内容为不可信data，工具权限在执行层控制。提供方实际报告的prefix cache命中与本系统结果缓存分开统计，不承诺跨提供方命中。

每次请求预留调用数与估算token预算，响应后按可观测usage对账；隐藏推理量不可见时只能报告估算/unknown。失败修复和重排重试也消耗预算。用户授权模型费用不等于取消资源治理。

### 8.3 证据图与研究循环
问题 → 子问题 → query/source_run → snapshot/extraction → evidence → claim/field → report。
程序检查quote切片、字段类型、id归属和授权；核验模型判断支持/反驳/未知/推断，但不当绝对真值。

research步骤拥有自己的checkpoint；补搜必须对应未解决问题或冲突。保存已尝试查询、已读版本、每轮有效新证据数；重复无增益退出。研究可以只产出证据集合，没有writer时不伪造报告。故障下已完成证据仍保留。

对资料集，每个条件独立评估，实体消歧可回滚；新来源引起字段冲突时保留两个版本和依据，不能以“最新抓取”自动覆盖事实。

## 9. 接口与事件合同

### 9.1 外部HTTP业务API
下表是计划接口，未实现前不得对客户端宣告。所有写任务支持Idempotency-Key+request hash；同键不同参数报冲突。API版本是/v1，文档规格版本与MCP版本另行管理。

| 方法/路径 | 作用 | 关键输入/输出 |
|---|---|---|
| POST /v1/search | 创建搜索会话 | query/AST、scope、hard_filters、mode、budget → session_id、preview、coverage |
| GET /v1/search/{id}/results | 读取冻结版本结果 | rank_version/cursor → items、next_cursor、constraints、facets_scope |
| POST /v1/search/{id}/refine | 筛选当前或重搜 | explicit strategy/filters → 新ResultSet或新Session |
| GET /v1/search/{id}/events | 订阅预览与状态 | Last-Event-ID → SSE |
| POST /v1/suggest | 本地建议 | prefix/privacy_scope → suggestions |
| POST /v1/read | 单页/文件读取 | url/artifact_ref、render、freshness → snapshot/extraction或job |
| POST /v1/batch-read | 明确列表读取 | urls/input_refs、options → job |
| POST /v1/map | 候选发现 | seeds/scope/discovery → candidates或job |
| POST /v1/crawl | 有界爬取 | seeds/scope/budget → job |
| POST /v1/imports | 授权本地/连接器导入 | connector_ref、approved_path_scope → job |
| GET /v1/documents/{id}/versions | 内容版本 | 授权过滤后的快照清单 |
| GET /v1/extractions/{id}/blocks | 分段读取 | cursor/limit → blocks |
| POST /v1/find | 页内查找 | extraction_id/text/context → matches |
| POST /v1/highlights | 原文证据 | query/extraction_ids/budget → Evidence[] |
| POST /v1/similar | 文本/网页相似 | seed/local/external/both → related hits |
| POST /v1/images/search | 图片关键词/跨模态检索 | text/asset_ref/scope → image hits |
| POST /v1/images/similar | 反向/近重复图片 | asset_ref、privacy_policy → duplicate/related groups |
| POST /v1/media/search | 音视频检索 | text/media filters → assets/segments |
| POST /v1/media/analyze | 转写/关键帧 | asset_ref/capabilities/budget → job |
| POST /v1/extract | 字段提取 | inputs/schema/model_ref → fields/job |
| POST /v1/answer | 单轮证据回答 | question/scope/budget → answer/claims/evidence |
| POST /v1/research | 多轮研究 | objective/constraints/budget → job |
| POST /v1/datasets | 资料集建设 | entity_spec/conditions/schema → job/collection |
| GET /v1/jobs/{id} | 查看任务 | state/progress/stop_reason |
| POST /v1/jobs/{id}/control | 暂停/恢复/取消 | action；终态重跑创建新id |
| GET /v1/jobs/{id}/results | 逐项结果 | cursor → items/errors |
| POST /v1/collections/{id}/refresh | 刷新已有集合 | freshness/budget → job |
| POST /v1/workspaces | 保存研究工作区 | sessions/bookmarks/tags → workspace |
| PUT /v1/preferences | 显式偏好 | versioned preferences |
| POST /v1/subscriptions | 查询/集合订阅 | query_ref、timezone、schedule、condition |
| POST /v1/exports | 导出 | items/format/policy → job |
| GET /v1/capabilities | 能力目录 | 已实现+当前配置可用/不可用+原因 |
| GET /v1/sources | 来源状态 | 审核、健康、能力、限制 |
| POST /v1/admin/model-profiles/validate | 管理员配置检查 | profile_ref、显式test_policy → 能力报告 |
| POST /v1/admin/indexes/rebuild | 重建与迁移 | index_scope/version → job |

敏感管理、连接器、订阅和模型配置有独立授权；普通Agent工具不暴露任意profile写入。GET不能产生抓取、转写或研究副作用。

### 9.2 统一结果信封与统计
`request_id,status,job_id,result,warnings,errors,usage,provenance,capabilities_used`。
status为success/partial/failed/blocked；HTTP 202只表示任务受理。errors含code/stage/target/retryable，不泄露秘密。usage区分外部请求、不同页面、字节、浏览器时间、模型调用、已知/未知token；不要重复计请求合并后的网络成本。

主要新增错误：E_QUERY_SYNTAX、E_UNSUPPORTED_FILTER、E_CONSTRAINT_UNKNOWN、E_CURSOR_EXPIRED、
E_SESSION_INVALIDATED、E_INDEX_NOT_READY、E_EVIDENCE_EXPIRED、E_MODEL_CAPABILITY、
E_MEDIA_UNAVAILABLE、E_REPLAY_EXPIRED、E_IDEMPOTENCY_CONFLICT。继承原网络/解析/预算错误。

事件通用字段：event_id、session/job、version、type、timestamp、payload_ref。
事件类型：accepted、plan_ready、source_started/finished、result_added/updated、
result_set_frozen、page_completed、index_updated、evidence_added、partial、completed、failed、cancelled。

MCP只做协议适配与工具Schema，能力协商/生命周期符合冻结版本，不能将HTTP返回直接无差别映射。[S15] 高权限任务仍需服务端检查；Skills仅提供说明与工作流指引，不是独立授权系统。

## 10. 工作区、订阅与导出

历史、收藏、集合、缓存有不同生命周期；删除历史不意外删除用户另存资料，删除资料必须使引用失效。共享工作区不自动共享其中所有文档，展示前逐项检查。

搜索订阅（发现新页面/新满足条件对象）与集合刷新（已知页面变化）分两类。保存query/constraint版本、成功水位、已通知事件；来源失败不推进水位，不把失联当删除。使用IANA时区，默认随部署设置，示例可用Asia/Taipei；固定UTC与本地墙钟策略分别定义。错过多次执行有上限，不无限补跑。通知默认站内；外部Webhook由用户明确配置并经出口审核。

导出JSON/JSONL/CSV/Markdown以及科研引用格式，保留字段证据。CSV以安全文本编码或转义处理前导=,+,-,@等公式风险；不能修改真实数据却不注明。原文/媒体导出需要保存和再分发许可，默认优先导出链接与引用。导出下载引用有TTL和鉴权，不假定拿到ID即授权。

## 11. 安全、隐私与供应链

安全是M0开始的主线，不是M8补丁。攻击面包括URL/重定向/协议、浏览器子请求、HTML预览、PDF/ZIP/XML、媒体解码、查询语法、模型上下文、插件、Webhook、导出和备份。

Linux与Windows分别实现资源与网络隔离。Linux可采用namespace/cgroup/seccomp等经验证配置；Windows进程资源限制不能替代网络隔离，需要经过测试的出口代理配合系统级阻断策略。若隔离能力不可用，不把不可信互联网浏览器任务降级为无限权限运行；明确阻断并给出部署诊断。

SSRF通过域名解析到连接、重定向与子请求的多层检查；浏览器禁止绕过强制出口，限制不受控协议和直连路径。[S14] 页面内容不接触密钥；模型不会收到全局环境变量。内部资料导入通过专门权限，不允许从网页工具转向任意本地文件。

对快照HTML展示做清理或隔离，禁止作为可信脚本执行。查询日志默认脱敏、设置保留期，遥测可关闭。来源条款、模型数据出站和采集范围更严格的变更立即生效；旧任务版本不能覆盖新禁令。

依赖锁、SBOM、模型权重单独清单、测试素材授权、分发许可证文本与安全更新流程都纳入发布。自研功能不能从“参考”滑向未记录的源码复制。

## 12. 验收、质量与性能计划

### 12.1 门禁分层
A-01…A-22为原约束；B-01…B-40为既有专项；C-01…C-16为本轮复核回归；FT-001…FT-083为功能合同。总计161项计划检查，并非声称139个自动化测试已经写好。工作包完成需单元/合同/失败路径测试及对应基准，不能仅勾选“有接口”。

硬门禁：已发布quote字节一致性、精确受保护字面、权限/索引撤销、预算、免费非模型依赖边界、无隐藏回退。安全只能对覆盖用例宣告通过，不说不可能存在未测试漏洞。

语义质量按人工标签与留出集评测；支持率、回答覆盖率和unknown率一起报告，避免全部答未知刷指标。rank evaluation使用代表性查询与文档相关性判断的原则参考[S13]，不依赖Elasticsearch执行。

### 12.2 样本与测量
建议逐步扩展到不少于300份受控页面/文档、200条检索/约束问题、至少各一组图片近重复/相似、字幕/转写/关键帧和私有权限样例。数量是规划起点，不是质量证明；训练/调参、回归与留出集严格区分。真实网络来源另建每日/按需观测样本，不与确定性fixture混算。

指标：nDCG@10、MRR、Precision@k、受控全集Recall@k、硬条件违规数、原文提取块精确率/召回、引用完整性、字段准确率与unknown、语义支持率、来源独有有效贡献、结果多样性、TTFR、阶段P50/P95、浏览器比、内存峰值、索引滞后、可恢复率与缓存分层命中。

开放互联网没有已知完整相关集合，因此不承诺全网召回率。性能目标在M0/M2实测机器后冻结；不同模型/冷热缓存/授权来源/页面类型分组。不得引用Drogon、hnswlib等上游性能替代本产品实测。

### 12.3 建议质量门槛如何使用
原v0.1的90%正文块质量、95%关键结论“支持或限定/未知”只是待校准目标，不能直接当成绩。实现中应分别报告真正supported率与abstention，避免用限定语掩盖低回答质量。字节完整性可程序检查100%，事实真实性不能承诺100%。

模糊测试query/URL/HTML/XML/文档与媒体边界；故障注入worker kill、租约过期、磁盘满、索引损坏、网络超时、模型坏JSON、事件重放失效。多worker必须测试fencing而非只看队列长度。


## 13. 里程碑、依赖与资源计划

### 13.1 里程碑总表
人日包含工作包实施、单元/合同测试和必要说明；集成、安全演练与发布另有专门包。数字是由工作包逐项估算汇总，不是实测工时。

| 里程碑 | 主题 | 工作包数 | 基础人日区间 | 阶段产物 | 退出门禁 |
|---|---|---:|---:|---|---|
| M0 | 工程与安全底座 | 12 | 61–104 | 内部构建 | 合同校验、Linux/Windows核心构建、隔离策略、持久任务恢复与版本快照样例通过；A类测试开始运行。 OPS-00/BACKUP-00/SPIKE-01/CONTRACT-02通过；独立审查状态不得用作者自审代替。 |
| M1 | 多源搜索与会话 | 14 | 70–122 | 搜索Alpha | 已审核3–5源可查；冻结分页无重复；部分结果/SSE/取消可用；所有非实测源仍禁用。 API-00和ROBOTS-01完成；web Alpha至少1个审核web来源，领域源数量不能替代。 |
| M2 | 网页采集与全文索引 | 12 | 73–126 | 采集Alpha | 搜索→读取/限定爬取→快照→全文检索闭环；基础新增/更新/删除/撤权生效；公开索引规则通过。 |
| M3 | 搜索正确性与体验 | 10 | 48–82 | 搜索Beta | 精确/中文/硬条件/分面/排序解释测试通过；相关性基准建立；strict不存在伪满足。 固定语料/环境/阈值/留出集已冻结，不能空值放行。 |
| M4 | 模型、证据与研究 | 10 | 59–99 | R0.1基本完整版本 | 用户文本模型驱动有界研究与引用；嵌入配置存在时语义检索可用；无模型基础模式仍通过。 |
| M5 | 垂直搜索与私人资料 | 7 | 45–80 | R0.2垂直版本 | 代码行号绑定版本、论文元数据/全文边界、私人资料权限及资料集核验通过；适配器扩充有审计。 |
| M6 | 图片与音视频 | 5 | 40–69 | R0.3多模态版本 | 以图搜图覆盖范围明确；转写/字幕/关键帧有时间位置；模型缺失与素材无权限时正确降级。 |
| M7 | 工作区、订阅与完整接口 | 8 | 44–76 | R1.0候选版本 | 收藏工作区、订阅、导出、通知、HTTP/CLI/MCP和完整UI全部可用且共享权限逻辑。 |
| M8 | 规模、加固与正式交付 | 5 | 31–53 | R1.0完整交付 | 全功能回归、跨平台包、备份恢复、多worker公平调度和安全门禁通过；性能结果绑定实测环境。 |
| 合计 | 全范围 | 83 | 471–811 | 完整产品 | 所有适用门禁通过；独立审核另行签署 |

R0.1（M0–M4）规划基础工量为311–533人日，不代表这些阶段必须完全串行。全范围另外设置30%风险缓冲，约613–1055人日。缓冲是可编辑规划假设，不能再给每个工作包重复加相同缓冲。

工量估算覆盖首批3–5个来源和下一批5–10个来源的预算、自有图库反向搜索、授权媒体分析、开放格式和通用私人资料导入；不覆盖无限数量网站/来源、全网索引建设、所有商业App连接器、专有模型训练、长期上游维护。已清点候选均需审核处置，但不能默认全部可启用。

不填写固定发布日期：团队人数、专长、每天有效投入和机器未知。采用公式估算容量：`可用人日/周=人数×每周工作日×有效投入系数`；`容量下界=总人日/可用人日每周`只是容量换算，不是工期，实际还受依赖关键路径、审核等待和返工影响。并行人数不能无限线性缩短工期。

### 13.2 实施依赖主线
```text
M0 合同/权限/存储/任务
 ├─ M1 免费来源 + 查询AST + 聚合 + 搜索会话
 └─ M2 获取/浏览器/解析 + 爬虫 + 全文索引/删改
           └─ M3 精确/中文/硬筛选/分面/质量评测
                    └─ M4 模型 + 向量 + 证据 + 有界研究
                             ├─ M5 代码/科研/私人资料/资料集
                             └─ M6 图片/音视频/视觉识别
                                       └─ M7 工作区/订阅/导出/完整API与UI
                                                 └─ M8 多worker/安全/备份/跨平台交付
```
图是验收分组示意，真实执行依赖见每个DEV工作包。完成前置合同后，M2读取、M4模型协议和UI静态框架可以提前并行；不能把M4研究当成免于M2证据/权限依赖的独立聊天机器人。

优先路径：CORE-01 → CONTRACT-01 → STORE/AUTH/JOB →
READ/INDEX 与 SOURCE/SEARCH 两条 → QRY-04/SESSION → MODEL/EVIDENCE →
RESEARCH → 统一API/UI → 全范围发布测试。实际关键路径在资源分配后用任务DAG计算，不能仅看阶段编号。

### 13.3 并行职责建议
| 工作流 | 主要职责 | 交接产物 |
|---|---|---|
| 核心/架构 | 合同、任务、权限、存储、取消与资源治理 | DTO/Schema、状态机、迁移、公共fixture |
| 搜索/检索 | 来源、AST、中文、排序、会话、分面与索引 | QueryPlan、SourceBatch、ResultSet、IndexReceipt |
| 采集/解析 | HTTP、CDP、正文、文档、frontier与版本 | FetchArtifact、Extraction、候选发现与错误 |
| 模型/证据 | profile、预算、Highlights、字段、研究 | ModelRun、Evidence、Claim、EntityRecord |
| 垂直/媒体/UI | 代码/科研/私人资料、图片/音视频、工作区 | 类型化资产和界面流程 |
| 测试/安全/运维 | 回放、权限、基准、跨平台、备份恢复 | 发布门禁、漏洞处置与测量报告 |

这六类职责不代表需要六个固定人员；小团队可以兼任，Agent可以承担明确工作包，但每个核心合同和安全变更需要独立review。不要按“每个功能一个常驻Agent/服务”实现。

### 13.4 工作包执行合同
每个DEV包至少包含：引用功能ID、前置合同版本、输入fixture、实现子步骤、产物、失败路径、单元/合同测试、对应B/FT检查、可观测字段、文档及独立review。一个包可拆成1–3日粒度的具体提交；大包先做spike，再实施，不把长分支一次合并。

完成定义：代码已实现且可调用；合同与负例测试通过；错误/取消/权限路径可检查；新增配置有默认和迁移；无敏感日志；已记录依赖许可；性能影响有样本；真实网络功能经过小流量获准验证或明确标受限；所有相关Schema和UI能力目录同步。

AI生成代码的速度不作为工量压缩系数；以实际“通过验收的工作包/迭代”校准。独立review、可靠性和来源审核时间不能因为代码生成快而删除。

## 14. 立即可执行的启动批次

这批在开发工程中执行，不需要先提供真实模型密钥。使用假源和固定响应，只在明确需要外网验证时启用来源。

| 顺序 | 启动任务 | 产物 | 完成检查 |
|---|---|---|---|
| 1 | GOV-01边界与ADR初始化 | FREE_POLICY/INDEPENDENCE/SBOM约定 | 没有四产品后端依赖；模型例外准确 |
| 2 | CORE-01构建骨架 | searchd/searchctl、Linux/Windows CI | 空服务、CLI和单元测试运行 |
| 3 | CONTRACT-01协议与Schema | 核心DTO、枚举、请求/错误例子 | 正例通过、非法输入拒绝 |
| 4 | QA-01模拟来源与测试站 | 无网fixture、循环/动态/错误页 | CI不需要搜索API和真实模型 |
| 5 | SEC-01出口策略最小实现 | URL/DNS/私网检查、进程限制 | 恶意重定向/解析输入被阻断 |
| 6 | STORE-01/AUTH-01最小版本 | 快照/正文/租户权限/撤销 | UTF-8定位、跨用户拒绝 |
| 7 | JOB-01任务与租约 | 幂等提交、暂停/取消、恢复 | worker kill后恢复无重复产物 |
| 8 | QRY-01与假来源search | AST、来源编译、部分结果 | 精确符号不损坏；超时独立 |
| 9 | READ-01→READ-03→INDEX-01切片 | 抓固定页→正文→全文检索 | 新增/更新/删除可观察 |
| 10 | SESSION-01与UI-01切片 | 冻结结果和基础页面 | 翻页稳定、错误透明 |
| 11 | SRC-01审核真实首批来源 | 已批准manifest和接口fixture | 不审核不启用 |
| 12 | MODEL-01/02假模型合同 | 验证配置/Schema/坏响应 | 无真实密钥也能测试所有控制路径 |

启动批次不是另一份缩减需求。只为尽早验证最难撤回的合同与安全边界，之后按83个工作包推进。

## 15. 风险台账与替代路径

| 风险 | 触发信号 | 处理与替代 | 对应工作包 |
|---|---|---|---|
| 免费上游条款/接口变化 | 审核过期、限流或解析回归 | 暂停来源、保留部分结果、修复独立适配；不切收费服务 | SRC-01/02/06 |
| 来源更多但质量下降 | 冗余上升、前十质量变差 | 来源消融、限贡献、多样性与策略回滚 | RANK-01/02、SRC-05 |
| 浏览器成本/出口隔离失败 | 内存高、进程残留、旁路请求 | HTTP优先、池限额、网络阻断；失败不降为无隔离运行 | READ-02、SEC-01 |
| 中文与精确符号失真 | 命中错误、quote错位 | 原文字段、映射表、领域词典版本回滚 | QRY-02/03 |
| SQLite写入或WAL增长 | 长锁、队列积压、WAL失控 | 短事务、单写队列、物化会话；实测后迁移后端 | INDEX-03、SCALE-01 |
| ANN受硬过滤漏召回 | 合格top-k不足 | 提前过滤、增加候选、小集精确、明确partial | INDEX-02 |
| 模型坏结构/幻觉/注入 | 无效引用、越权工具、预算扩张 | 本地验证、有限修复、独立核验与执行限制 | MODEL-02、EVID-01 |
| 非文本解析/媒体安全 | 畸形包、解压炸弹、解码崩溃 | 沙箱和资源上限、格式白名单、样本回归 | DOC-01/02、AV-02 |
| 公开可见但不可保存/再发布 | 来源政策或权利不明 | 默认只元数据/链接；权限与保留期审查 | CRAWL-03、EXPORT-01 |
| 多worker双执行/配额超限 | 租约争用、分区重试 | fencing、幂等产物、全局预算账本 | JOB-01、SCALE-01 |
| 删除后仍在旧会话/备份 | 旧cursor可读、恢复复活 | acl_epoch、tombstone、恢复重放 | AUTH-01、OPS-02 |
| 工量与来源数量扩大 | 新增大量长尾源/格式/平台 | 总清单保留，单独批次与单位估算；不吞进既有固定承诺 | GOV-01、SRC-06 |

## 16. 容量规划与观测的初始方法

先测一组目标机器，记录CPU/内存/磁盘/网络/模型端点与浏览器版本；再冻结性能指标。可以用以下公式计算资源，所有变量来自测量而非本文猜测：

- 活跃浏览器内存 ≈ 浏览器上下文数 × 样本P95内存 + 核心/索引/缓存余量；
- 向量原始存储 ≈ 分块数 × 维度 × 每元素字节，另加ANN结构、元数据与版本；
- 网页保留存储 ≈ 每期新/变更页面 × 平均产物大小 × 保留版本数，加复制与索引；
- 工作吞吐以不同页面/媒体/模型类别分别测量，不把并发数当QPS；
- 远程模型以允许并发、真实延迟和调用上限限制，不自动假设GPU在本机。

单机先保证正确性和可观测性；在负载阈值/错误率触发时扩大worker或迁移存储。配置的固定上限是安全预算，不是声称该机器一定承受得住。

## 17. 交付包内容与验证范围

`functional_spec_v0.3.md`：完整功能与验收；本文件：架构和开发计划；
`development_plan.xlsx`：可编辑工作包/阶段/验收追踪和估算公式；
`requirements_and_backlog.json`：机器可读清单；
`schemas/`：起始查询、来源和模型配置JSON Schema及示例；
`baseline/`：原v0.1历史文档。

本次文档校验以validation_report.json为准，包括ID/依赖/阶段/工量以及可执行配置合同；它不是独立子Agent审核。**没有编译业务代码、启动浏览器、访问候选搜索源、运行检索基准或验证用户模型。** 相关完成状态全部保留为未开始。

## 18. 官方技术依据

以下资料用于验证基础组件能力与设计边界，查阅于2026-10-16。引用不表示安装它们的托管平台，也不表示使用其收费产品。具体版本在M0锁定；不直接依赖不断变化的main/tip-of-tree。

| ID | 资料 | 地址 |
|---|---|---|
| S01 | SQLite FTS5：全文索引、BM25与自定义分词 | `https://www.sqlite.org/fts5.html` |
| S02 | SQLite WAL：并发与本机文件约束 | `https://www.sqlite.org/wal.html` |
| S03 | libcurl multi：多请求异步接口 | `https://curl.se/libcurl/c/libcurl-multi.html` |
| S04 | Chrome DevTools Protocol：浏览器控制基础 | `https://chromedevtools.github.io/devtools-protocol/` |
| S05 | Drogon：C++跨平台HTTP框架 | `https://github.com/drogonframework/drogon` |
| S06 | Lexbor：HTML解析与选择器基础 | `https://lexbor.com/` |
| S07 | ICU：Unicode与中文词边界 | `https://unicode-org.github.io/icu/userguide/boundaryanalysis/` |
| S08 | OpenCC：中文简繁转换 | `https://github.com/BYVoid/OpenCC` |
| S09 | hnswlib：C++近邻检索 | `https://github.com/nmslib/hnswlib` |
| S10 | hnswlib：C++过滤与持久化示例 | `https://github.com/nmslib/hnswlib/blob/master/examples/cpp/EXAMPLES.md` |
| S11 | 公开索引与摘要声明：Google说明（不视为普适法律） | `https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag` |
| S12 | RFC 9309：Robots Exclusion Protocol | `https://www.rfc-editor.org/rfc/rfc9309.html` |
| S13 | 检索排序评估方法 | `https://www.elastic.co/docs/reference/elasticsearch/rest-apis/search-rank-eval` |
| S14 | OWASP：SSRF风险与防护 | `https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html` |
| S15 | MCP规范：本次latest指向2026-07-28 | `https://modelcontextprotocol.io/specification/2026-07-28` |
| S16 | OpenAI：API接口概览 | `https://developers.openai.com/api/reference/overview/` |
| S17 | Claude：Messages API | `https://platform.claude.com/docs/en/api/messages` |
| S18 | Tree-sitter：增量语法分析 | `https://tree-sitter.github.io/tree-sitter/` |
| S19 | whisper.cpp：C/C++转写与许可 | `https://github.com/ggml-org/whisper.cpp` |
| S20 | FFmpeg：构建选项与许可证边界 | `https://ffmpeg.org/legal.html` |
| S21 | PDFium：使用与构建指引 | `https://pdfium.googlesource.com/pdfium/+show/HEAD/docs/getting-started.md` |
| S22 | JSON Schema：default是注解 | `https://json-schema.org/understanding-json-schema/reference/annotations` |
| S23 | SQLite Online Backup API | `https://www.sqlite.org/backup.html` |
| S24 | JSON Schema：format校验 | `https://json-schema.org/understanding-json-schema/reference/string` |


## 19. v1.1复核后的执行约束（覆盖旧版同主题宽泛表述）

### 19.1 前置能力与交付顺序
API-00在M1交付最小HTTP/CLI/MCP；M2再接read，M7的API-01仅补齐所有端口和兼容性。OPS-00/BACKUP-00/CONTRACT-02/SPIKE-01进入M0。ROBOTS-01进入M1，成为递归爬取准入的前置；CRAWL-03不再承担首次robots实现。新增工作包是原职责拆分与缺口补强，原77个ID全部保留。

JOB-01先取得STORE-01/AUTH-01/QA-01；模型可选的基础Highlights不再硬依赖MODEL-02，但模型增强路径必须另测。图无环只是最低条件，关键合同依赖与阶段门禁也需要验证。

### 19.2 配置三层验证
第一层：JSON结构与字段类型，明确启用日期/URI的format验证；第二层：规范化和语义校验，包括profile引用、字段/操作符、AST复杂度、截止时间、审核到期、role capability；第三层：执行时真实授权、出站策略、端点能力、凭据及预算。设计包的Python参考校验器仅覆盖前两层选定合同，不替代C++运行时和网络安全实现。

允许`default_text_profile=null, profiles={}, roles={}`，核心搜索/读取/爬虫可在不配置模型时运行。文本角色缺省继承默认配置；显式null禁用；专门嵌入/视觉/转写能力缺省禁用。覆盖字段包含reasoning、timeout、max_concurrency、max_output_tokens、retry_limit；部署上限不可被角色/任务覆盖突破。effective_config由程序生成并保存，不能把Schema的default注解当自动赋值。[S22]

`query`是唯一查询主体，可为字符串或AST；hard_filters始终作为附加AND条件。禁止不兼容的field/operator/value/match组合。先做请求体/AST深度节点限制，再进入递归Schema验证；错误日期、URI协议和引用在实际调用来源前拒绝。

### 19.3 robots准入与来源可行性
robots决策先于递归页面请求。请求自身经过出站/限流/预算管控；按RFC9309区分成功、重定向、不可用与不可达，缓存和解析大小按锁定策略执行。服务器/网络不可达且无适用规则缓存时不放行；429采用更保守的限流退避。[S12] 授权交互读取与自动爬虫使用明确模式，不能通过伪装为单页读取规避爬虫策略。

web Alpha必须有至少一个真实审核的通用web来源；论文/馆藏等领域源不能替代。实际源清单和观察窗口是G0输入，未验证者保持disabled。全量候选清点不等于免费性/可访问性已证明。

### 19.4 向量并发与版本可见性
同一可变HNSW实例以应用级读写锁保守保护：只读查询可并发；增删改、容量变化、保存、加载必须独占，并由单writer串行提交。上游API明确不保证add与query并行安全，因此不能从“支持多线程”推导出混合读写安全。[S09] 若未来改为immutable generation+delta，另做ADR及资源/恢复回归，不在首版默认引入两套路径。

离线重建写入新实例，校验模型空间/维度/水位/文件校验和后原子发布，旧实例等引用归零回收。每次查询绑定generation；跨代文档以当前可发布Extraction过滤，历史查询显式指定。保存需绑定ef等未必由库完整持久化的配置，k不足时返回实际数量或partial。索引可重建；不能把模型向量文件当权威内容库。

### 19.5 存储容量与恢复屏障
对临时抓取、原始blob、正文/快照、索引、事件、导出和备份分别配置预算；并发派发预留，下载中校验真实增长。达到高水位暂停低优先级采集，低水位才恢复。GC必须尊重活动任务和证据pin，不能为释放空间删除仍承诺可用的快照。磁盘满、GC和提交竞争有独立故障fixture。

数据库一致备份使用Online Backup API或经验证的停写流程，不直接复制正在WAL写入的主文件。[S23] blob清单按已提交引用冻结并验证。撤销journal不能仅存放在被回滚快照中；须独立保护其最新水位。恢复后默认隔离，只有校验撤销连续性、重放删除/授权、重建派生索引后才对外发布。缺失最新日志则保持隔离，不能把恢复成功冒充安全恢复。RPO/RTO在目标机器实测后冻结，不在本次虚构数值。对外部已送达的文件或模型请求不承诺撤回。

### 19.6 订阅提交与失败
以(subscription_id, query_version, source_id)保存成功checkpoint、扫描区间、覆盖不足和seen集合；使用明确重叠窗口，不能仅按发布时间高水位忽略晚收录/更正。单源失败只阻止其checkpoint前进，其他源可发布partial结果。扫描结果事务提交后写outbox；通知event_id稳定，重发不创建新逻辑事件。接收方无幂等支持时不承诺外部消息恰好一次。

### 19.7 质量门禁和审查独立性
G0记录支持平台/组件版本、目标机器、固定语料hash、测试命令和拟验收方法；M2测基础指标；M3前冻结阈值、留出集和负责人。缺环境、阈值或有效报告时，性能/语义门禁保持blocked；不允许测试后临时降低阈值。安全和确定性合同只对已覆盖样本声明通过，不夸大为绝对安全。

独立review须有实际执行器或独立人员、不同执行上下文、review_run_id、reviewer标识、输入哈希、原始结论和逐项裁决。单纯更换提示词/脚本角色不是独立Agent。`document_revision_applied`、`implementation_verified`、`independently_reviewed`是三个状态，本轮仅完成第一类与文档层检查，后两类未完成。可先开展离线fixture/骨架工作，不得虚报独立发布门禁通过。

### 19.8 工量与启动批次的变更
新包工量与原包减量在`review/effort_delta.json`逐项对照，所有估算仍未由实际开发校准。阶段号是验收分组，不是强制串行；不得只用总人日/人数承诺工期。启动顺序：GOV/CORE/CONTRACT → QA/STORE/AUTH/SEC → JOB → CONTRACT-02/OPS-00/BACKUP-00/SPIKE-01 → API-00/SRC/ROBOTS → SEARCH与READ/INDEX两路。实际可并行前沿见`review/dependency_analysis.json`。


本轮外部资料复核范围：S01、S02、S09、S12、S22、S23、S24。其他链接从v1.0继承，不表示本轮重新验证了每个基础组件或其版本。
