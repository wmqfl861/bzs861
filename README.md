# 不周山搜索工具 · bzs861

**状态：0.1.1 S1 review candidate。** 本轮整合 Claude S1 增量与已交付的 0.0.2 主分支，面向 Linux、本机使用和 AI Agent。S0 真实来源实测与 S1 全部标准尚未完成，当前不是正式联网搜索版本。

目标：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。输入来源、修复问题与验证边界见 [S1 接收审查](docs/handoff/CLAUDE_S1_REVIEW_20261018.md)；实际结果见 [本轮报告](validation/s1_review/test_report.json)，远端 CI 以对应提交的 [Actions](https://github.com/wmqfl861/bzs861/actions) 为准。

项目自己实现搜索、读取、索引、权限与证据业务，使用基础库处理网络、协议和解析；不安装或调用 SearXNG、Scrapling、Firecrawl、Exa 成品后端。非模型外部服务仅接入经过审核的免费入口，不设付费回退。

## 当前代码范围

| 能力 | 当前边界 |
|---|---|
| 本地导入、搜索与证据 | 保留显式 HTML/TXT 导入、不可变快照、冻结分页、UTF-8 字节定位、原文查找、用户隔离和权限复核 |
| 原生配置合同 | C++ 校验 `model-config`、`source-manifest`、`search-request`；HTTP/CLI 共用逻辑，返回 `applied=false`，不自动注册配置或调用模型 |
| 联网搜索候选实现 | 多源并发、每源状态、URL 去重、索引族融合、冷却和缓存；默认没有已审核真实来源，未核验适配器不能进入正常搜索 |
| 网页读取候选实现 | worker 抓取与 Markdown 提取、快照、`cache_first / refresh / cache_only`；直接出站仅允许通过连接层检查的公网目标 |
| 检索改进候选实现 | 中文索引、BM25 与带字节区间的原文片段；与既有精确字面和查询语义一起回归 |
| Agent 接口 | MCP 映射同一核心，优先 stdio；本机带令牌 HTTP 是远程接口准备，不代表可公网部署 |
| 任务与备份 | 保留 fixture 任务和隔离备份；安全恢复未完成，备份不能直接启动服务 |

尚未交付动态 JS 页面、PDF/Office、递归爬虫、模型规划/向量检索/研究、远程多客户端发布和 Windows 安装包。**83 个工作包、161 项验收 ID** 全部保留，见 [蓝图 v1.2](docs/planning/development_blueprint_v1.2.md) 和 [开发进度](docs/DEVELOPMENT_STATUS.md)。

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

当前启动器按仓库的可编辑安装布局寻找配置与二进制。`bzs init` 创建本地令牌和来源配置；默认未审核来源保持禁用。既有用户配置不会因为源码清单改变而自动取得审核资格。

可以先调用离线能力和纯配置校验：

```bash
bzs call capabilities '{}'
bzs call contract.validate '{"kind":"model-config","config":{"policy":{"allow_reference_backends":false,"external_nonmodel_services":"reviewed_free_only","allow_provider_hosted_search":false,"allow_unconfigured_fallback":false},"default_text_profile":null,"profiles":{},"roles":{}}}'
```

正常联网搜索需完成来源审核、实测并显式启用。读取命令只接受符合出站策略的公开目标：

```bash
bzs search "断点续爬 实现"
bzs read https://zh.wikipedia.org/wiki/网络爬虫
bzs stop
```

没有合格来源时明确报告来源不可用，不自动使用未核验回退。S0 步骤见 [S0_PROBE](docs/S0_PROBE.md)；候选探测是显式联网动作，不自动批准或注册来源。

## 接入 Agent

优先用 `bzs mcp-config` 生成适合本机的 stdio 配置。配置形状如下，路径需替换：

```json
{"mcpServers":{"bzs861":{"command":"/path/to/bzs861/.venv/bin/python","args":["-m","bzs.cli","mcp"]}}}
```

MCP 工具为 `web_search`、`read_url`、`read_range`、`find_in_page`、`search_local`、`cite_evidence`、`list_sources`。运行能力与来源准入由核心决定；网页正文和搜索引擎摘要是外部数据，不赋予 Agent 新权限。

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

本轮 Linux 离线 CTest **5/5 通过**：核心 **81 项 / 707 断言**、原生合同 **55 项 / 269 断言**、HTTP/CLI **20 项**、假网站端到端 **12 项**、Python worker/MCP **122 项**；另有 **34 项**保留的 Python 设计合同参考校验。核心与原生合同均通过 ASan/UBSan，开启泄漏检查。源码哈希、环境、命令与原日志见 [本轮报告](validation/s1_review/test_report.json)。这些套件存在交叉覆盖，不是 161 项完整产品验收；真实来源尚未探测。GitHub Linux/Windows 结果应检查本提交对应的 Actions。

`validation/test_report.json` 与对应旧日志为 0.0.1 历史；`validation/takeover/` 为 0.0.2 证据；`validation/s1_test_report.json` 是 Claude 随包的 0.1.0 声明。它们不能代替本轮整合提交的验证。

## 已有数据与后续范围

两条开发分支曾各自使用 `user_version=2` 表示不同结构，不能仅凭版本号判断兼容。迁移必须识别来源、保留快照/证据/删除与隔离状态，并用旧数据库回归。旧 HTML 不会因更新解析器自动清洗；备份依旧保持隔离。最终迁移版本与实测见 [开发进度](docs/DEVELOPMENT_STATUS.md)。

本候选版离线修复和回归已完成；下一步补来源审核和 S0 证据，再按 S2–S9 推进。单用户先行不删除恢复、撤权、会话重取和多客户端阶段的等效验收。

## 文档与许可

[API](docs/API.md) · [原生配置合同](docs/CONFIGURATION.md) · [安全](docs/SECURITY.md) · [依赖](docs/DEPENDENCIES.md) · [Windows](docs/WINDOWS.md) · [开发进度](docs/DEVELOPMENT_STATUS.md) · [接手入口](HANDOFF_START_HERE.md)

项目许可证由仓库所有者决定，目前未指定；依赖各自的许可继续适用。完整依赖与许可证发布审查尚未完成。
