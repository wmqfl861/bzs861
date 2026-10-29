# bzs861 全仓库风险审计与修复记录 — 2026-10-29

## 交付身份与结论

输入为 `work/blueprint-v2-dev-20261029` 的 `83d45cd825d9dfc3ee09268eb65fc2f971b8ed69`，输入 tree `f36268e54af75a3e4d05f44d681908a83a62ef8e`。通过仓库现有 audit-source 工作流取得完整 Git bundle，校验归档、COMMIT、TREE、tracked tree 和 git fsck；不是从旧 `4c1fe86` 开始，也未覆盖本地 Agent 分支。

修复代码提交：`73478c51fa729280fcb51c06f5081313333e077c`；代码 tree：`04dddf6a1c4bbeb8a00dafd362dccaaa67fdaf37`。前一修复提交 `a31d16a347c1b1e372ad736f84a5a9bd226877ad` 与后续词法自审修正均保留。本记录所在提交只追加文档/摘要，实际执行输入以此代码 tree 和外置逐文件 SHA-256 清单为准。

**本轮六类缺陷已修复，Linux 最终回归及 23 个原生 ASan/UBSan 程序通过。这是实施助手的源码自审、反例和回归验证，不是另一名外部审核员签收，也不等于整个产品或 83 个工作包验收完成。修复代码目前保存在交付 Git 包中，尚未推送 GitHub；远端审计分支仍指向输入基线。**

## 发现与修复

| 编号 | 影响 | 修复与证据 |
|---|---|---|
| AUD-01 | 同一 no-store 批读仍有活跃成员时，取消/暂停的付款方或订阅方也可能得到成功状态和瞬时收据。 | 复用逐成员结算准入，停止成员不发布收据，原付款方只结算一次。基线四个取消/暂停反例失败；现有、迟到加入与逐成员矩阵通过。 |
| AUD-02 | 瞬时批读漏查已删除的重定向最终页/中间页，可能发布不应存在的成功收据。 | 同一事务内用已捕获的授权、删除和发布栅栏调用既有瞬时投影；失败不能留下成功收据。最终页删除反例基线失败；增加单人/共享、删除后重导入、过期、授权代际变化及收据写入回滚覆盖。正文和快照不持久化。 |
| AUD-03 | Core RPC 跟随跳转，导致错误成功、凭据转发或变更请求重放风险，健康检查也可误报就绪。 | urllib/aiohttp 都禁用重定向，在解析 JSON 前拒绝所有 3xx。两个真实回环端口的 11 个反例基线失败：同步 301/302/303 实测携带 Bearer 改发 GET；异步 307/308 在另一个端口重发 POST/body，但该跨 origin 场景没有携带 Bearer，不能笼统称所有异步跳转都泄露令牌。同步 307/308 则误把跳转 JSON 当成功。修复后目标端零请求，原背压重试合同保留。 |
| AUD-04 | 产物跨平台名称漏拒 Windows 保留字符、控制字符、上标设备名和大小写冲突。 | 在创建父目录、锁和 staging 前拒绝这些输入；使用保守 casefold 冲突规则。基线 25 个反例失败；修复后通过。不是对抗恶意同权限文件系统修改的完整沙箱。 |
| AUD-05 | SPDX 解析通过正则提取吞掉非法标点，把空值/坏文本误判有效，并将 WITH 例外和第二项许可证混淆。 | 有界、完整 token 匹配，许可证/例外使用不同集合；标识大小写只在 ASCII 输入上归一化，拒绝 Unicode 形似字符和非 ASCII 分隔。原 11 个反例失败，最终 27 个解析测试通过。仅支持明确的 curated flat subset；括号、LicenseRef、未收录项和其他未支持语法保留为未分类原始证据，不冒充完整 SPDX 解析器或法律许可结论。 |
| AUD-06 | 本机基准工具拒绝真实 Core 的 `success` 响应，模拟测试却使用了 `ok`，掩盖了不可用问题。 | 按原生 API 接受 `success/partial`，拒绝 `ok/empty/failed` 等非合同状态。新增真实 Worker/Core 进程端到端用例，修复前失败、修复后通过；仅调用本机 search，不调用 web.search。 |

另补 `test_test_definition_uniqueness.py`，检查 Python 测试的同一模块/类作用域下重复 test 定义，避免后定义静默覆盖前定义后出现虚假的全绿。

## 实际测试结果

| 检查 | 最终结果 | 范围 |
|---|---|---|
| Release 完整 CTest | **42/42 通过，0 失败、0 套件跳过** | Core、批读、Map/Crawl、发布/撤权、会话、HTTP/CLI、Worker/MCP、恢复端到端等原矩阵；最后执行见 final-v3。 |
| Python 完整测试 | **1703 通过、0 失败、5 跳过** | 1708 个收集项；5 项为 Windows 专用用例。CTest 也包含这些测试，不能重复相加。 |
| 原生 ASan/UBSan | **23/23 程序 exit 0** | 全核心插桩、Debug、detect_leaks=1、halt_on_error=1；没有声称 HTTP 端到端程序或 Python 都经过 sanitizer 插桩。 |
| 批读专项 | **55 组、1793 断言、0 失败** | 属于上述矩阵，不另加成产品验收数量。 |
| 原设计合同 / plan guard | **34 / 13 通过** | 原 83 包/161 验收映射/10 切片结构有效，不等于产品签收。 |
| 静态输入与保护 | **363 个 Python 文件可解析；4542 个受保护路径未修改** | 含 4534 份旧 validation；原来源配置、工作流与验收定义保持。 |

最终代码的清单和原始命令/日志/JUnit 均位于外置证据包。输入基线原全量成绩为 CTest42/42、Python1634通过/5跳过，只用于前后对照。所有最终 native 源码、头文件和 CMake 输入与 ASan 执行输入一致；最终词法跟进仅改变两个 Python 文件，完整 final-v3 已重新执行。

## 保留的失败与自审修正

基线新增反例共 53 个失败：批读5、Core跳转11、产物/SPDX36、真实基准1，原始日志未覆盖。原 pytest 从仓库根目录执行时有9个 `tests.*` 导入收集错误，未执行用例；改为仓库 CI 使用的 `python/` 工作目录重新运行，未通过改产品代码掩盖该调用错误。首次基准调用的相对解释器路径错误、提前链接旧原生库的无效复验也保留，并明确不当作候选成绩。

编辑时曾误重复追加四个已有 artifact 测试定义；发布前 AST 复核发现后恢复原字节，只保留新测试并补唯一性检查。SPDX 跟进自审又发现 Unicode casefold 可以把长 s 规范化成 ASCII 标识，修复并补两个反例。对应原观察和最终重测保留，不能将早期混合测试树或中间候选成绩当最终通过。

## 审查范围和仍未证明的事项

全仓库代码清单为 88 个生产/接口/工具源文件、22698 行；全量语法/编译与回归覆盖了仓库既有矩阵。深入人工阅读集中在上述六类缺陷及相邻的 Store 事务、逐成员结算、删除/撤权/发布屏障、RPC、离线证据和测试合同。**清单、静态扫描与测试全绿不等于逐行证明全部历史实现无缺陷。**

没有本地 Windows 实测或这些修复提交自身的远端 Windows CI；没有独立的普通 Debug 全量 CTest（ASan 是 Debug 构建）、MSan/TSan、真实来源批准/联网质量验收、生产数据库/断电/可信备份恢复、长时间 soak 或 Windows 完整60包/67份 SPDX的 SBOM重生成。仍有原 `tests/cache_freshness.cpp:333` 的误导缩进告警；未以放宽警告/测试门槛隐藏它。

业务 schema15、来源默认禁用、D1–D7 待决、holdout、永久带键身份、B-24范围、备份隔离和原验收定义均未改变。未合并 main，未碰本地 Agent 的 stash、工作区或分支。

## 复验与发布

依赖按锁定清单安装。本轮 Linux 离线使用现有 audit-dependencies 产物，核对归档/内部哈希与锁文件后安装；不能把它当新的依赖版本审核。

```sh
cmake --preset release -DBZS_PYTHON="$PWD/.venv/bin/python" -DBZS_REQUIRE_PYTHON_TESTS=ON
cmake --build --preset release -j 2
ctest --test-dir build-release --output-on-failure
(cd python && ../.venv/bin/python -m pytest -q -p no:cacheprovider tests)
.venv/bin/python docs/planning/tools/validate_contracts.py
.venv/bin/python docs/planning/tools/plan_v1_2.py check
.venv/bin/python tools/test_plan_guard.py
```

Sanitizer配置/程序清单沿用 `.github/workflows/ci.yml` 与 CMakePresets 的23个原生目标，实测命令逐项列在证据包 `final-asan-results.json`。外置早期复现源码含原容器绝对路径；异机重放需映射路径，正式回归已纳入仓库。

交付包包含完整 Git bundle、完整源码 ZIP、格式化提交补丁及校验信息。在新目录/独立 worktree 导入，核对最终 SHA/tree 后测试；只允许非强制推送审计分支。发布后还须检查那个准确提交自己的 Linux/Windows CI，不能引用83d45cd旧CI代替，更不能提前合并main。

参考规范（仅用于输入语法/命名规则，不构成第三方许可审批）：
- Microsoft 文件命名规则：https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
- SPDX 2.3.1 Annex D：https://spdx.github.io/spdx-spec/v2.3.1/SPDX-license-expressions/
