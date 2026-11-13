# 验证记录 — 2026-10-31 审核 @ 86bc23e

## 环境

| 项 | 值 |
|---|---|
| 机器 | Windows 11 x64（10.0.22624）；MSVC 14.44（VS Build Tools 17.14）；CMake 3.31.6 |
| 冻结输入 | `C:\bzs861-audit`（git worktree，分支 work/local-integrate-audit-86bc23e），HEAD=`86bc23e1ba91042070d69ba2c48704cd9b2c5653`，`git status` 干净 |
| Python | 3.12.10（仓库 .venv；requirements-s1-lock.txt 60 pins 全装，`pip check` 通过，pytest/aiohttp/lxml/trafilatura/mcp 导入 OK） |
| C++ 依赖 | 专用 vcpkg @ `6719fd4d`（与 ci.yml:88 同基线）；sqlite3[fts5] 3.53.4 / libxml2 / json-c / openssl / boost-beast（x64-windows） |
| 远端 | origin=github.com/wmqfl861/bzs861（无凭据泄漏；fetch 正常） |

## 本轮实际执行的命令与结果（主审）

| # | 命令（工作目录） | 退出码 | 结果 | 日志 |
|---|---|---|---|---|
| 1 | `.venv/Scripts/python docs/planning/tools/validate_contracts.py` | 0 | 34/34 | /c/tools/audit-20261031/contracts.log |
| 2 | `.venv/Scripts/python docs/planning/tools/plan_v1_2.py check` | 0 | 83/161/10 有效 | /c/tools/audit-20261031/plan.log |
| 3 | `.venv/Scripts/python tools/test_plan_guard.py` | 0 | 13/13 | /c/tools/audit-20261031/guard.log |
| 4 | `(python/) ../.venv/Scripts/python -m pytest -q -p no:cacheprovider tests` | 0 | **1708 通过 / 0 失败 / 0 跳过**（本机即 Windows，历史 5 项专用跳过真实执行） | /c/tools/audit-20261031/pytest.log |
| 5 | CMake 配置+构建（Debug+Release，MSVC /W4） | 0 | 0 编译错误 | /c/tools/aud-config.log、aud-build-*.log |
| 6 | `ctest -C Debug -j 1 --output-junit`（42 项） | 0 | **42/42** | /c/tools/aud-ctest-debug.log；JUnit build/ctest-debug.xml |
| 7 | `ctest -C Release -j 1 --output-junit`（42 项） | 0 | **42/42** | /c/tools/aud-ctest-release.log；JUnit build/ctest-release.xml |
| 8 | `verify_ctest_junit.py` 双份 | 0 | 42/0/0 ×2，与原始日志一致 | 见 #6/#7 |
| 9 | Release 真机冒烟（init→start→ingest→中文检索→stop，全新中文+空格 BZS_HOME、48920/48921 端口） | 0 | OK，无孤儿进程 | 会话记录 |

说明：#5-#9 为本会话早前在**同一冻结 worktree、同一 SHA** 上的真实执行（导入复测阶段），日志完整保留，计入本轮验证记录。

## 子代理执行记录（真实独立 Agent，各自动冻结输入）

| 子代理 | 自核 SHA | 执行内容 | 结果 |
|---|---|---|---|
| 查询/索引/HTML | 86bc23e（git rev-parse 核对） | 只读源码精读 + 运行既有二进制：bzs_tests.exe（116/116）、bzs_gram_index_tests.exe（5 PASS/135 断言）、bzs_long_document_recall_tests.exe（PASS，含 partial 标志实测） | 全绿；二进制无法与 SHA 密码学绑定，源码证据为主 |
| 服务/安全/数据 | 86bc23e（同上） | 纯静态精读（未运行二进制），git 状态核对 | 发现 F-01/F-02/F-03/F-07/F-09 |
| 构建/CI/规划 | 86bc23e（同上） | 工作流/CMake/依赖/规划文档精读 + GitHub API 实查 | 86bc23e 精确 SHA 5/5 CI 全绿（run 41169433563/41169374589/41169433573/41169374596/41169374591）；PR#11 open/draft/clean |

## 未执行项（如实）

| 项 | 原因 | 处置 |
|---|---|---|
| MSan/TSan | 未配置（仓库声明未配置且未宣称） | 维持；不新增声明 |
| 真实出站/联网验证 | D1 未决、0 来源启用 | 所有联网能力标"回放通过、线上未验证" |
| 长时 soak / 断电矩阵 / 真实数据库破坏测试 | 超本轮范围且禁止用真实数据做破坏对象 | 列入计划边界 |
| Linux 本机 ASan/UBSan 重跑 | 本机 Windows；该 SHA 的 Linux CI 已含 23 程序全绿 | 引用 CI run 41169433563 |
| audit-dependencies 工作流 | paths 过滤未触发（三提交未触锁文件/工作流） | 正常，非缺陷 |

## 敏感信息检查

- 本报告及全部日志不含 token/cookie/模型密钥/私有正文；GitHub 凭据即时取用即时删除；remote URL 无凭据形式输出。
- 审核输出目录 `docs/audit/20261031-86bc23e/` 为未跟踪文件，未 commit/push。
