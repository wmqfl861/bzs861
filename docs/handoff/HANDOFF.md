# bzs861 开发交接书（0.0.1 归档）

> 本文保留原打包时的事实。当前状态以 [0.0.2 接手记录](TAKEOVER_20261017.md) 和仓库源码为准；本轮用户已授权接手并推送 `wmqfl861/bzs861`。

整理日期：2026-10-17。目的：让仓库所有者手动上传，并让新的开发者或 Agent 从现有代码继续，而不是重写或只读聊天猜测进度。

## 1. 版本与事实优先级

| 对象 | 当前版本／状态 | 权威路径 |
|---|---|---|
| 源码 | 0.0.1 离线启动批次 | 仓库源文件、`README.md` |
| 架构蓝图 | v1.1 | `docs/planning/development_blueprint_v1.1.md` |
| 功能规格 | v0.3 | `docs/planning/functional_spec_v0.3.md` |
| 计划与工量 | 83 工作包、9 里程碑、161 产品验收项 | `docs/planning/development_plan_v1.1.xlsx` 与 backlog JSON |
| 实现进度 | 21 项部分实现、62 项未开始、0 项完整验收签署 | `docs/implementation_status.json` |
| 产品独立审核 | 未执行 | `docs/planning/review/independent_review_request.md` |
| 原测试记录 | Linux 核心与本地 HTTP 集成已记录通过 | `validation/test_report.json` 与相应日志 |
| 本次打包 | 不修改代码、不重跑编译、不冒称新测试通过 | 外层 `06_checks/packaging_report.json` |

当前用户明确澄清优先于早期建议；实现状态优先于“计划应有”。最新蓝图描述目标架构，源代码和 capabilities 描述已实现行为。聊天历史只用于解释决策来由，不能替代当前接口合同。

计划基础估算 471—811 人日；一次性 30% 风险缓冲后 613—1055 人日。它们是完整范围的规划估算，不是剩余工期或交付承诺；本次不重新估算，也不把部分实现等价换算成完成百分比。

## 2. 已实现的最小闭环

`固定来源搜索 → 固定网页／显式本地 HTML、TXT 导入 → 正文提取 → SQLite 快照 → 全文／有界字面检索 → 冻结分页 → 原文证据 → 更新和删除同步`。

核心入口是 `apps/searchd/main.cpp` 与 `apps/searchctl/main.cpp`。CLI 通过真实的 loopback HTTP 调用服务，不是直接修改数据库。业务主要在 `src/service.cpp`、`src/store.cpp`，查询在 `src/query.cpp`，解析在 `src/html.cpp`，严格 JSON 在 `src/json.cpp`。

已实现用户隔离、令牌摘要、不可变快照、UTF-8 字节证据、冻结分页、fixture 作业幂等、暂停／恢复／取消、租约 epoch 和旧租约拒绝。备份使用 SQLite 备份接口并带隔离标志，但安全恢复到可提供服务的状态尚未实现。

## 3. 不可误报为完成的部分

没有真实免费网页搜索源、HTTP 外网抓取、浏览器、递归爬虫、模型调用、语义检索、MCP、多媒体、完整界面或远程 worker。配置／Schema 测试通过，不代表对应的 C++ 执行功能完成。

当前 HTML 解析仍在核心进程中，只能处理可信、可控且有权处理的输入；大小限制不能替代独立进程沙箱。服务只监听回环地址，不应开放公网。

中文和符号查询可用有界字面备用路径，某些查询最多扫描 2000 个候选；不能宣称完整中文分词、全量召回或精细相关性排序完成。

Linux 记录的测试不能替代 Windows、GitHub CI、长期压力／断电测试、真实网络或全 161 项验收；CI 文件存在不代表已运行。

## 4. 实际依赖与蓝图差异

启动代码实际使用 C++20、SQLite/FTS5、Boost.Asio/Beast、json-c、libxml2、OpenSSL Crypto；测试使用 Python，设计合同另需 jsonschema。详见 `docs/DEPENDENCIES.md` 与 `validation/environment.json`。

蓝图中的 Drogon、Lexbor、ICU、OpenCC、Chromium/CDP、hnswlib、PDFium 和媒体方案属于目标候选，并非本批均已引入。`docs/adr/0001-offline-bootstrap.md` 说明了启动阶段的取舍。不要在不检查当前接口和测试的情况下搬动代码目录或一次性替换所有依赖。

最终代码许可证由仓库所有者决定，尚未新增统一 LICENSE。公开上传前应复核项目与依赖许可；不要把仓库公开可读自动解释为授予任意使用许可证。

## 5. 构建、运行和复测

在 Linux 且依赖已安装的环境中，从仓库根目录执行：

```bash
cmake --preset dev
cmake --build --preset dev -j 2
ctest --preset dev
```

运行示例完整保存在 `README.md`；不要把令牌写进源文件、聊天或 Git 远程 URL。

```bash
python3 tools/create_auth.py --admin
./build/searchd --auth .local/auth.json --state .local/state --fixtures tests/fixtures --port 8610
```

另开终端：

```bash
export SEARCH_API_TOKEN="$(cat .local/auth.token)"
./build/searchctl capabilities
./build/searchctl fixture-search 'C++'
./build/searchctl fixture-ingest developer
./build/searchctl search 'std::expected'
./build/searchctl search '搜索'
```

`searchctl ingest ./page.html https://your-site.example/docs/page text/html` 的 URL 只是出处元数据，不会访问该地址。认证脚本拒绝覆盖现有凭据；不要为重复演示而无检查删除旧状态。

原记录：核心 45/45、174 断言；HTTP/CLI 16/16；ASan/UBSan 只覆盖核心 45 项；Python 设计合同 34/34。它们保存在交付源码中；本次仅核对记录与哈希，没有重新运行这些产品测试。

## 6. 下一批开发建议与退出条件

| 顺序 | 工作 | 预期验证 |
|---|---|---|
| 0 | 阅读远程仓库、整合到正确基线、复测现有实现 | 不覆盖未知代码，记录新的远程分支／commit |
| 1 | 完成独立审查、Windows 构建与依赖锁核验 | 独立意见与作者裁决分开，未测项不放行 |
| 2 | 正式合同 C++ 化、模型空配置与角色继承 | 正例／负例与现有 Python 设计测试语义一致 |
| 3 | 独立撤销日志与隔离恢复流程 | 旧备份不会复活已删除内容和撤销权限 |
| 4 | 网页出站与解析进程隔离 | DNS、重定向、子请求、非 HTML 与恶意解析样例有测试 |
| 5 | HTTP 获取与 robots 准入 | 超时／字节／请求预算、失败状态和缓存语义可验证 |
| 6 | 第一个经审核的真实免费通用网页源 | 实际审核资料、原始 fixture、来源失败与去重测试 |
| 7 | 最小 MCP／UI 与后续模型模块 | 只暴露已实现能力，保持统一权限与业务逻辑 |

这些只是按现有依赖规划的接续批次，不缩减其他 83 项范围。新增功能需要更新实现状态和相应用例，不要直接把整张计划表全部标为完成。

## 7. Git 交接

原离线分支：`agent/bootstrap-m0-20261017`。

```text
7266ce42c64c0e5d273448d6fa49274ac02e5359  初次实现
b801b08db7b9d56108d8ea516923075007ab99f2  推送受阻说明
```

原推送失败且未读取远程基线。本次也是本地打包，没有访问、授权或修改 GitHub。网页上传会产生新的提交 ID，不会保留这两次历史；完整个人包的 `03_git_history/` 保存 bundle 和 patch。

新补充交接文档与工作簿不在旧 bundle 的两个提交内；恢复 bundle 后，要另行整合上传目录中的新增资料。不要用 `--force`、`--mirror` 或未经检查的 `--allow-unrelated-histories` 覆盖远程；先审查再合并。

## 8. 聊天和隐私

聊天正文只在完整个人包的 `04_chats_private/`，不在本可上传目录。记录覆盖当前可见对话，不等于账户所有会话的官方导出。其他 Exa 和 Agent-Reach 会话只有检索摘要，不补造原文。

打包程序未修改源码或测试记录，也未把运行数据库、实际令牌、Cookie、字体文件和构建目录放入上传目录。自动模式扫描不是完整保密审核，公开前仍应自行查看文件清单。
