# Windows 11 原生接手报告 — 2026-10-28

输入：PR #7 头分支 `work/project-audit-followup-20261027` @
`8a7d552bb3199dce1333e22cde3d4f666d3e21df`（已核对该提交的
[CI 40617784255](https://github.com/wmqfl861/bzs861/actions/runs/40617784255)
Linux/Windows 均为 success；该成绩只证明该输入）。接手时 PR #7 仍为
open/draft 未合并；main 为 `1d8e80e5`，不包含 PR #5/#6/#7。因此本批从
8a7d552 创建工作分支 `work/windows11-audit-20261028-1100`，未接 main、
未 cherry-pick 旧成果。本批只做既有代码的 Windows 11 审核、修复与验证
收尾；未新增业务功能、未启用来源、未改 schema15、未动 D1–D7。

## 环境（全新安装，短路径 C:\bzs861）

- VS Build Tools 2022 17.14（MSVC 14.44，/W4 /permissive- /utf-8）
- Python 3.12.10 x64 + 仓库专用 .venv；requirements-s1-lock.txt 全装、
  pip check 通过、pytest/aiohttp/lxml/trafilatura/mcp 可导入
- 专用 vcpkg @ `6719fd4d4cd3b2d4575f6164da4a02f757b3b067`（与 CI 同版）：
  sqlite3[fts5] 3.53.4、libxml2、json-c、openssl 3.6.5、boost-beast
- CMake VS 生成器，build-win11 Debug+Release；DLL PATH 仅会话内指向
  vcpkg debug/bin 与 bin，不混用配置
- 系统代码页 ACP=65001（UTF-8）。见"环境限制"。

## 审核范围与修复（每项独立提交：反例 → 最小修复 → 回归测试 → 复验）

四个并行只读审核代理 + 人工复核覆盖：C++ 存储/维护（maintenance/store/
backup/searchctl）、启动器与进程生命周期（cli/process_control）、Worker
RPC/MCP/netguard、并发任务与搜索（service/web/batch/map/crawl/sessions/
retention/tokenizer/query）、searchd 入口与 CI。已核对与未覆盖区域见
validation/windows11_takeover_20261028/audit-coverage.json。

| 提交 | 缺陷与修复 |
|---|---|
| `6fcf790` fix(launcher) | 部分启动回滚在首个 cleanup_child 抛异常时跳过其余 DETACHED 子进程（真孤儿）；stop() 第一个组件失败即放弃第二个；ensure_running 回滚在控制锁被占时完全跳过清理。新增 cleanup_children（逐项隔离+合并报告+锁尽力获取）、stop() 双组件都尝试、stop_owned 对"身份探测后进程已退出"的窗口忘记记录返回 False。旧实现 7 个新测试失败，修复后 launcher/storage-CLI 22/22。 |
| `8478df2` fix(storage) | Windows 上 sqlite3_open* 把窄路径按 ANSI 代码页转换，而 path::string() 在 MSVC 返回 ANSI：中文目录下的库在 maintenance/Store/backup 打不开或产生乱码文件。新增 bzs::utf8_path；备份暂存名固定 ASCII；already_incremental 结果显式报告 fts_integrity_checked=true——SQLite ≥3.44 的 PRAGMA integrity_check 也会检查虚拟表，原"损坏 FTS"测试无法区分新增检查是否真正执行（删掉该分支测试仍绿），现在任何版本删分支都会失败；filesystem::space 改 error_code 重载。新增第 7 组测试 unicode_space_paths（中文+空格目录下 inspect/convert/重开/检索/backup）：7 组/146 断言（原 6/133）。 |
| `98f7b31` fix(pool) | fixture 泳道领取租约后收到停止时既不 complete 也不 fail，作业悬挂至 10s 租约超时并烧掉尝试次数；现与 read/map/search 泳道一致 fail E_SHUTDOWN。background_pool 5 项（原 4）。 |
| `0e761a9` fix(tests) | PYTEST_CURRENT_TEST 存整个参数化 id，Windows 单环境变量上限 32767 字符：6 个用例 teardown ValueError（2 万元素信封、1600 节点 sitemap）。显式短 id，字面量字节不变。1505/1505/0（原首轮 1 failed+6 errors）。 |
| `4422e2e` fix(launcher) | **venv python.exe 是启动器残根**：Popen 到的是残根、真正解释器是其子进程；cleanup/stop 只杀残根，真实 worker 存活占端口、回滚遗留解释器孤儿（8a7d552 上 launcher_lifecycle 稳定复现）。以出生身份验证的根为锚、单次快照收集父链后代、从深到浅终止；快照外进程绝不发信号。新测试驱动"残根→解释器→睡眠者"三层。launcher_lifecycle 12/12（原 11+1 失败）。 |
| `cc704c3` fix(web+tests) | 本机对无监听端口的回环连接是**静默丢弃**（实测 2s 超时而非拒绝），worker 死后 matching_profile 的 fetch.profile（本地无副作用探测）E_WORKER_TIMEOUT 致命 → 缓存读 504，违背"worker 挂了本地功能存活"。该探测现把 TIMEOUT 与 UNAVAILABLE 同等回退（真实抓取超时语义不变）。e2e 框架只杀残根导致 fakeweb.log/metadata 句柄滞留（WinError 32）：Stack.kill 树杀、rows() 显式关闭 sqlite 连接、test_10 杀 worker 前先补缓存去除墙钟耦合。e2e_web 15/15（原 1 败 1 错）。 |
| `6f80450` test(win) | 实测 Debug 单独运行：direct_shared 260s（34 测试/10259 断言全过）超 240s 护栏、sessions 273s（23/298 全过）超 180s。套件内无 pacing sleep，成本为 synchronous=FULL 每提交 fsync + Debug CPU。Windows 护栏提至 420s（1.5x 余量），断言全保留。 |
| `b93d6fe` fix(searchd) | Windows 上 SO_REUSEADDR 允许第二个 searchd **静默双绑定**忙端口并抢占连接（实测两个 searchd.exe 同绑 48610 且都报 ready）。Windows 改 SO_EXCLUSIVEADDRUSE：忙端口启动响亮失败（实测 rc=2 + E_ 错误），正常 stop→start 不受影响；POSIX 保持 reuse_address。 |
| `898126d` fix(storage) | 首次推送 af90aae 的 Windows CI（CP1252 代码页）在新增 unicode_space_paths 组失败："No mapping for the Unicode character exists…"——`Store::backup` 的 backup_id 与 searchctl ingest 的 title 仍用 `path::string()` 经 ANSI 代码页转换，CP1252 无法表示中文名即抛错（ACP 936/65001 上不显现）。改 utf8_path，JSON 报告在所有代码页携带真实 UTF-8。storage_maintenance 双配置 7 组/146 断言本地复验通过；CP1252 行为由远端 Windows CI 验证。 |

记录未修（非缺陷、业务决策或后续项）：
- 五套件历史并行超时根因（fsync+Debug CPU 叠加）已定位并实测；改
  synchronous 策略或 Debug /O2 属产品耐久性/性能决策，未动。
- Worker RPC P3 项（内部错误无 stderr 日志、业务 E_PARAMS 状态码 200 与
  信封 400 并存、mcp_auth 类型校验、_host_locks 无界增长等）：合同既定
  或防御纵深，见 audit-coverage.json。
- web.cpp 搜索缓存锁跨 Store 事务的串行化、礼貌等待不可停止、
  registry identity 重复计算：性能观察项。
- ACP=65001 环境限制：中文路径旧缺陷（8478df2 所修）在本机不可复现
  （string() 恰为 UTF-8）；在标准中文 Windows（ACP=936）上旧代码必然
  产生 GBK 字节。修复对所有代码页正确；新 unicode 测试用宽字面量构造
  真实中文名，任意机器上验证正确行为。

## Windows 11 验证结果（本机实测）

| 项目 | 命令/配置 | 结果 |
|---|---|---|
| Python 全量 | .venv python -m pytest -q -p no:cacheprovider tests（python/） | **1505 通过 / 0 失败 / 0 错误**（3 轮：首轮 1 个未识别瞬时失败+6 个环境变量错误，修复后两轮全绿） |
| 合同 | validate_contracts.py | 34/34 |
| 规划结构 | plan_v1_2.py check | 83 包/161 验收/10 切片有效 |
| plan guard | tools/test_plan_guard.py | 13 通过/0 失败 |
| CTest Debug | ctest -C Debug -j 1 --output-junit（42 项） | 首轮 37/42（5 失败均定位修复）→ **终轮 42/42**，verify_ctest_junit 42/0/0 与原始日志一致 |
| CTest Release | ctest -C Release -j 1 --output-junit（42 项） | **42/42**，JUnit 校验一致（终轮随 searchd 修复复跑） |
| 真实使用 | 全新中文+空格 BZS_HOME、48610/48611 | **38/38**：init→start→status→重复 start 同 PID→stop 无孤儿→再 start→重启后索引保留；中文/英文/符号检索；document.read；UTF-8 边界证据创建+回读（quote="C++ 搜索工具开发指南…"）；忙端口第二 searchd 拒绝（rc=2）；真实 stdio MCP initialize/tools/list/search_local/read_range；storage inspect/convert/already_incremental+fts 标记/占用拒绝（E_STORAGE_BUSY） |

首轮 Debug 5 个失败的处理（原始日志已存 external 目录，未覆盖）：
launcher_lifecycle（venv 残根，4422e2e）、e2e_web 15 中 2 个
（残根+丢弃型连接+墙钟，cc704c3）、e2e_revocation（句柄滞留，cc704c3）、
sessions/direct_shared 超时（实测慢而有限，6f80450）。

## 推送与远端 CI

- PR：https://github.com/wmqfl861/bzs861/pull/8（draft；
  base=`work/project-audit-followup-20261027`，即 PR #7 头分支）
- 首次推送 `af90aae`：Linux success / Windows failure（storage_maintenance，
  CP1252 backup_id 窄转换）→ 修复 `898126d`。
- 最终提交 `898126ded189c03d750956f790f75140483a9af7`：
  [run 40656300540](https://github.com/wmqfl861/bzs861/actions/runs/40656300540)
  与 [run 40656293917](https://github.com/wmqfl861/bzs861/actions/runs/40656293917)
  均 Linux+Windows **success**（含 Linux ASan/UBSan 全套与规划检查）。
- 本地全量 CTest 双配置 42/42 在 b93d6fe 状态执行；898126d 仅改两行报告
  字符串构造，storage_maintenance 双配置复验通过，全量矩阵由上述最终
  SHA 的双平台 CI 覆盖。

## 边界重申

schema15、来源 0 启用、B-24 范围、永久带键身份保留、隔离备份屏障、
83 包/161 验收/10 切片与 37/46/0 签收状态均未改变。D1–D7 仍待用户决定。
本批不合并 PR #5/#6/#7，不推送 main；新 PR 以 PR #7 头分支为 base。
