# 移植准备调研：SearXNG 引擎清单盘点（只读，草案）

状态：**草案，未激活任何来源**。本文件是转私有 + 移植 SearXNG 引擎的前置调研记录，
不是启用许可。所有候选在激活前仍需各自走完 CL-06 同款流程：条款审核记录 →
用户 S0 探测 → `approved_by` 签署 → 才能把 `enabled` 改为 `true`。

事实来源：searxng/searxng master `a7128d5`（2026-11-14 浅克隆至 C:\tools\searxng-src，
仅读取 git 对象库，未 checkout、未访问任何引擎端点）。克隆因仓库含 Windows 非法文件名
（`searxng.conf:socket` 等）无法 checkout，改用 `git show` 直读；对调研无影响。

## 1. 总量与分类

settings.yml 共 **353 条引擎配置**，引用 **228 个独立模块**（searx/engines/ 下 254 个
.py 文件，部分为公共基类）。按源码标记分类（是否使用 xpath/lxml/etree 抓取，
是否走 JSON 解析）：

| 类别 | 模块数 | 说明 |
|---|---|---|
| JSON API | 119 | `resp.json()`/`json.loads` 解析，**符合 D6**，可移植 |
| HTML 抓取 | 45 | xpath/etree/fromstring 解析 HTML，**D6 禁用，排除** |
| 其他 | 64 | 复核后其中约 20+ 实为 JSON（pypi、wikidata、yacy、sourcehut、lib_rs、pdbe 等，仅请求构造方式不同），其余为 RSS/特殊协议 |

结构性结论：SearXNG 的主力通用引擎（google、bing、ddg web、baidu、sogou、360、mojeek）
全部在抓取类里，与我们 D6 禁用名单一致——**移植不改变我们的抓取禁令边界**。可移植
的是它的 JSON API 引擎层，约 140+ 个模块。

## 2. 重叠验证：我们现有候选在 SearXNG 中的对应模块

以下 9 个来源在我们 config/sources.json 中已有（或已规划），SearXNG 同名引擎模块
可作为适配器**参考实现**，用于交叉核对接线细节（分页参数、字段名、错误路径）：

| 我们的来源 | SearXNG 模块 | SearXNG 默认 | 备注 |
|---|---|---|---|
| arxiv | `engines/arxiv.py` | 启用 | Atom 解析，与我们 API 路线可互证 |
| crossref | `engines/crossref.py` | 禁用 | 同一 api.crossref.org/works |
| openalex | `engines/openalex.py` | 禁用 | 同一 api.openalex.org/works |
| github | `engines/github.py` | 启用 | 无 key 可用（限流更低），与我们"需 token"口径需在 S0 复核 |
| hackernews | `engines/hackernews.py` | 禁用 | Algolia 端点，同我们适配器 |
| stackoverflow | `engines/stackexchange.py` | 启用 | api.stackexchange 2.3，站点参数化（可扩 superuser/serverfault 等） |
| wikipedia_* | `engines/mediawiki.py` | 启用 | **一个模块参数化所有 MediaWiki 站**：wiktionary、wikinews、wikicommons、gentoo wiki 等即插即用 |
| marginalia | `engines/marginalia.py` | 禁用 | 走 key 必需的 search.marginalia.nu，与 D15 一致 |
| mwmbl | `engines/mwmbl.py` | 禁用 | 走旧 v1 端点，**比我们的 CL-27 v2 适配器旧**——我们领先，无需移植 |

## 3. 新增可移植候选（按索引族分组）

均为 JSON API、无强制 key（除非注明）。"SearXNG 默认"指其发行版是否默认启用，
与质量/负载有关，不代表条款结论——**条款均未审，全部待走 CL-06 流程**。

### 3a. 独立网页索引族（与 marginalia/mwmbl 同族，general）

| 候选 | 端点 | SearXNG 默认 | 备注 |
|---|---|---|---|
| wiby | `https://wiby.me/json/?q={query}&p={pageno}` | 禁用 | 极简索引，轻量 |
| rawweb | `https://api.rawweb.org/api/search?keyword={query}&page={pageno}&lang=*` | 禁用 | 裸网索引 |
| avalw | `https://avalw.org/api/search?q={query}&page={pageno}&limit=10&tbm=all` | 禁用 | |
| cl0q | `https://cl0q.com/search?q={query}&limit=20&offset={pageno}` | 禁用 | |
| crowdview | `https://crowdview-next-js.onrender.com/api/search-v3?query={query}` | 禁用 | 论坛聚合 |
| encyclosearch | `https://encyclosearch.org/encyclosphere/search?q={query}&page={pageno}` | 禁用 | 百科聚合 |
| yacy | 公共实例（yacy.searchlab.eu 等）JSON API | 禁用 | P2P 索引，实例质量不一 |

### 3b. 学术/科研族（science）

| 候选 | 端点族 | SearXNG 默认 | 备注 |
|---|---|---|---|
| europepmc | ebi.ac.uk/europepmc/webservices/rest | 启用 | 生物医学文献，无需 key |
| semantic_scholar | api.semanticscholar.org/graph/v1 | 启用 | key 可选（无 key 低限流） |
| openaire publications | `api.openaire.eu/search/publications?format=json` | 启用 | 欧盟开放获取 |
| openaire datasets | `api.openaire.eu/search/datasets?format=json` | 启用 | 同上 |

### 3c. IT/包管理族（it, packages）

| 候选 | 端点 | SearXNG 默认 | 备注 |
|---|---|---|---|
| mdn | `developer.mozilla.org/api/v1/search?q={query}` | 启用 | 前端文档 |
| mankier | `mankier.com/api/v2/mans/?q={query}` | 启用 | man 手册 |
| pypi | pypi.org/search JSON | 启用 | Python 包 |
| pkg_go_dev | pkg.go.dev | 禁用 | Go 包 |
| lib_rs | lib.rs | 禁用 | Rust crate |
| docker_hub | hub.docker.com API | 启用 | 镜像 |
| packagist | `packagist.org/search.json?q={query}` | 禁用 | PHP 包 |
| sourcehut | sr.ht API | 禁用 | 代码托管（无 key 路径需复核） |

### 3d. 新闻族（news）

| 候选 | 端点 | SearXNG 默认 | 备注 |
|---|---|---|---|
| reuters | reuters 内部 JSON API | 启用 | 条款风险高（内部接口），需重点审核 |
| sina | `search.sina.com.cn/api/search?q={query}&tp=mix` | 禁用 | **中文新闻**，补 wikipedia_zh 之外的 zh 来源 |
| wikinews | MediaWiki API（复用我们的适配器） | 启用 | 见 §2 mediawiki |

### 3e. 参考族（reference）

| 候选 | 端点 | SearXNG 默认 | 备注 |
|---|---|---|---|
| wikidata | wbsearchentities API | 启用 | 实体检索 |
| wiktionary / wikicommons | MediaWiki API | 启用 | 复用现有 wikipedia 适配器改前缀即得 |

## 4. 移植机制笔记

- SearXNG 引擎接口 = `request(params, …)` 构造请求 + `response(resp)` 返回
  `Result` 列表；映射到我们 `apis.py` 的 `search()/parse()` 与 `SourceError`
  状态机基本同构（他们无 per-source 预算/冷却栅栏——这部分用我们已有的，不移植）。
- `json_engine` 声明式条目（§3a 全部 + mdn/mankier/openaire/packagist）几乎
  1:1 映射到我们 sources.json 的 `params` + 适配器字段抽取，移植成本最低。
- 评分：SearXNG 各引擎带 `weight` 与启发式打分；我们已有自己的 rank 融合与
  dedup_key 跨源去重，**只移植请求/解析层，融合层保持我们自己的**。
- 代理/UA：SearXNG 有出口代理与浏览器 UA 模拟支持——按 D6/D7 一律**不移植**，
  我们统一 API_USER_AGENT。

## 5. 下一步（等 S0 审核结论后，顺序执行）

1. `gh repo edit wmqfl861/bzs861 --visibility private`（工具已验证可执行）；
   随后 agent 从私有仓库继续，AGPL 移植代码在私有副本内自用无分发义务。
2. 移植分批：第一批建议 §2 重叠验证里有把握的（mediawiki 族扩展 + stackexchange
   参数化）+ §3 各族 1-2 个，每批一个 PR、每引擎一份审核记录草案。
3. 每个新引擎在启用前：条款审核记录 → 用户 S0 探测（`bzs probe --include-candidates
   --only <id> --per-source 3`）→ 签署后 `enabled:true`。
4. reuters/yacy 条款或稳定性风险高的候选放最后或放弃。
