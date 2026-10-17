# 不周山搜索工具 · bzs861

**状态：0.0.2 离线接手批次；联网搜索产品仍在开发中。**

目标仓库：[wmqfl861/bzs861](https://github.com/wmqfl861/bzs861)。本轮保留交接来源与原始历史记录，完成交接复核、搜索与解析缺陷修复，并增加原生 C++ 配置合同。改动和实测范围见 [接手记录](docs/handoff/TAKEOVER_20261017.md)。

按开发蓝图 v1.1 的启动顺序，独立实现 C++20 核心与第一条可测试的数据链：

```text
固定来源发现 → 读取固定网页／显式导入本地内容 → 提取正文
→ 不可变快照 → 本地全文搜索 → 稳定分页 → 原文定位
```

不安装、不调用 SearXNG、Scrapling、Firecrawl 或 Exa 后端。当前没有外部搜索、网页抓取或模型出站连接；用户配置的模型仍在后续产品范围内。

## 已实现的切片

- C++20 `searchd` 服务与真正通过 HTTP 调用它的 `searchctl` 客户端。
- 严格 JSON、重复键/UTF-8/复杂度检查，查询 AST、AND/OR/NOT、短语和精确符号。
- C++ 校验与规范化模型配置、来源清单和正式搜索请求；支持空模型、角色继承、可信部署上限及审核时效检查。
- 按令牌确定身份，读取、搜索、证据、任务和分页按用户隔离。
- 固定来源搜索与读取；显式导入 UTF-8 HTML/TXT，去除脚本/导航并保留代码文字。
- SQLite 持久快照与 FTS5 索引；新增、更新和删除同步；旧版本证据不被新内容覆盖。
- 中文及符号查询的有界字面检索备用路径，明确返回部分结果和扫描范围。
- 冻结搜索会话、跨页去重、权限变化后旧会话失效。
- 字节区间原文证据，拒绝切断 UTF-8 字符；不把位置正确当成事实真实。
- 单页 fixture 任务的幂等提交、暂停/继续/取消、租约 epoch、重启恢复和尝试预算。
- 一致性数据库备份；备份强制隔离，**恢复到可服务状态尚未实现**。

本批未实现：真实网页搜索源、URL 获取、浏览器、递归爬虫、语义检索、模型调用、MCP、多媒体和界面。运行 `capabilities` 可以查看真实能力目录；未实现操作返回明确错误。

## 构建（Linux）

需要 C++20 编译器、CMake >= 3.25、Ninja、SQLite >= 3.38 且启用 FTS5、libxml2、json-c、OpenSSL Crypto、Boost.Asio/Beast 头文件、Python >= 3.10。

Debian/Ubuntu 依赖示例（安装时需要包仓库网络；程序测试本身不需要外网）：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build libsqlite3-dev \
  libxml2-dev libjson-c-dev libssl-dev libboost-dev python3
cmake --preset dev
cmake --build --preset dev -j 2
ctest --preset dev
```

当前构建与验证结果见 [本轮报告](validation/takeover/test_report.json)，GitHub CI 以对应提交的 [Actions](https://github.com/wmqfl861/bzs861/actions) 结果为准。`validation/environment.json`、`validation/test_report.json` 保留原 0.0.1 历史记录。

## 启动与使用

生成自己的本地凭据；脚本拒绝覆盖已有凭据，不打印令牌正文：

```bash
python3 tools/create_auth.py --admin
export SEARCH_API_TOKEN="$(cat .local/auth.token)"
./build/searchd --auth .local/auth.json --state .local/state --fixtures tests/fixtures --port 8610
```

在另一个终端：

```bash
export SEARCH_API_TOKEN="$(cat .local/auth.token)"
./build/searchctl health
./build/searchctl capabilities
./build/searchctl fixtures
./build/searchctl fixture-search 'C++'
./build/searchctl fixture-ingest developer
./build/searchctl search 'std::expected'
./build/searchctl search '搜索'
```

使用上一条返回的标识：

```bash
./build/searchctl read SNAPSHOT_ID
./build/searchctl find SNAPSHOT_ID '断点恢复'
./build/searchctl page SESSION_ID CURSOR
./build/searchctl delete DOCUMENT_ID
```

显式导入你有权处理的本地 HTML/TXT：

```bash
./build/searchctl ingest ./page.html https://your-site.example/docs/page text/html
```

**这里的 URL 只是内容出处元数据，不会联网读取该地址。** CLI 读取指定本地文件后传给本地服务；HTTP API 不能借此读取服务器任意文件。

任意已公布操作也可通过 JSON 调用：

```json
{"operation":"job.create","params":{"fixture_id":"developer","idempotency_key":"demo-job-1"}}
```

保存为文件后执行 `searchctl call request.json`。所有操作、错误与范围见 [API 合同](docs/API.md)。

验证配置文件并查看规范化结果：

```bash
./build/searchctl validate model-config docs/planning/schemas/model-config.no-model.example.json
```

同一命令也支持 `source-manifest` 和 `search-request`。它通过本地 HTTP 服务执行 C++ 校验，返回 `applied=false`，不保存配置、不启用来源或调用模型。正式 v1 搜索请求通过校验，不代表当前 v0 搜索执行器支持其中所有字段。详见 [配置合同](docs/CONFIGURATION.md)。

## 测试

```bash
ctest --test-dir build --output-on-failure
cmake --preset asan
cmake --build --preset asan-core -j 2
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./build-asan/bzs_tests
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./build-asan/bzs_contract_tests docs/planning/validation/contract_cases.json
```

CTest 的 `contracts` 项直接运行 C++ 校验器，复用原 34 组设计 fixture 并增加边界回归。`docs/planning/tools/validate_contracts.py` 保留原 Python 设计基线，需要 `jsonschema`，可另行运行；它的结果与原生 C++ 测试分别记录：

```bash
python3 -m pip install -r tools/requirements-test.txt
python3 docs/planning/tools/validate_contracts.py
```

## 安全与限制

仅监听 `127.0.0.1`；拒绝浏览器 Origin；必须使用高熵 Bearer 令牌。不要暴露端口到公网，不要把原始 HTML 作为可信页面执行，不要把凭据或本地数据提交到 Git。

普通网络读取尚未实现，当前用“不提供该能力”拒绝外部访问；**这不表示完整 DNS/重定向/浏览器隔离已经开发完成**。HTML 解析目前仍在核心进程内，只有大小和遍历限制，没有独立解析沙箱。因此只用于可信的自建样例或经授权、可控的输入。

搜索采用早期基线排序；没有完成 BM25/中文分词/语义重排的完整质量验收。短中文、符号、NOT 等查询可能使用最多 2000 个候选的有界扫描，不能当作全量召回。

详见 [安全边界](docs/SECURITY.md)、[架构决策](docs/adr/0001-offline-bootstrap.md) 与 [开发进度](docs/DEVELOPMENT_STATUS.md)。

## 0.0.1 数据升级

第一次用本版打开可服务的 schema 1 数据库时，会在事务中升级到 schema 2，重建与 ASCII 词边界一致的 FTS 候选副本。原始内容、快照、证据和已冻结分页保持不变；隔离备份仍不能启动。旧版程序无法直接打开 schema 2。

HTML 解析版本为 `bootstrap-html/2`。旧 HTML 快照不会被自动改写；重新导入相同 HTML 才会生成采用新正文选择和 robots 解析规则的新快照，旧证据继续指向旧版本。

## 与计划及目标仓库的关系

`docs/planning/` 保留 v1.1 / v0.3 计划基线；`docs/implementation_status.json` 单独记录实现切片，不覆盖原计划，也不把整个工作包标为完成。

本轮已核验 `wmqfl861/bzs861` 的远程基线，接手时仓库为空。当前交付见 [接手记录](docs/handoff/TAKEOVER_20261017.md)；`validation/delivery_status.json` 只描述上轮旧目标仓库的失败尝试。私有聊天和外层个人交接包未进入仓库。

最终项目许可证待仓库所有者决定；本批没有替所有者选择开源许可证。基础依赖的许可证与来源见 `docs/DEPENDENCIES.md`。
