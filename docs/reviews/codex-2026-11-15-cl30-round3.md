# bzs861 CL-30 第 3 轮复审（PR #26，2026-11-15）

审核方：Codex，受用户委托的独立审核方。日期按 Asia/Taipei。本轮仅复审 PR #26 的补修 head，不重审 F6/F7、来源合同或其他批次。

**裁决：第 2 轮 RF1、RF2、RF3 均关闭；PR #26 的 eba350745fbdad9e1d2a9d901fe8f4cf2ec72d3c 内容可合入。本轮没有新增内容合入阻塞。两项回归测试的证明力仍有不足，列为 RF3-1、RF3-2 非阻塞发现；本次以实际 Windows 运行及独立失败注入补足验收证据。CI/D28 由执行方核对，本裁决不证明已合入，也不构成立即激活来源或 S1 签收。**

## 复审范围与事实口径

前轮依据：[第 2 轮复审](codex-2026-11-15-post-s0-review.md)的 RF1–RF3、D-R2-3、D-R2-5/6/7，以及给执行方的指令。开始时共享工作树位于 C:\bzs861-audit 的 work/post-s0-docs-20261115；本审核过程没有执行分支切换、更新或提交。收尾时观察到共享工作树已变为 work/s0-results-20261114，reflog 记录 02:06:58 的切换及 02:07:35 的合并提交；这些操作不是本审核过程执行的，未回滚或纳入本轮内容审查。待审分支经再次读取仍指向 eba3507，测试来自此前固定 Git 对象的临时副本，结论不受该工作树变化影响。

| 对象 | 本轮读取的 Git 对象 | 用途 |
|---|---|---|
| 待审新 head | eba350745fbdad9e1d2a9d901fe8f4cf2ec72d3c | work/cl30-20261114，用户指定的 PR #26 |
| 上轮已审旧 head | 9e7ee6bb562facd4ad8a48a0b17d0f19e5c37eec | 增量基线；RF1 旧行为反证 |
| CL-30 共同基线 | 18973986b5ac47559841024e42f8880a628c6990 | 完整范围核对 |
| 审阅中核对的文档工作树 HEAD | fc3efea1210bb372a75aaa1a4dd451bbdd1adea4 | 未作为待测实现 |
| 收尾时观察到的共享工作树 HEAD | 4640d9cd95757107383e71e9a1b67a6a2d4ba611 | work/s0-results-20261114；本轮未审该提交 |

增量 9e7ee6b..eba3507 只有 tools/create_auth.py、python/tests/test_windows_secrets.py 和 docs/CURRENT.md，共 149 行新增、42 行删除；CURRENT 仅新增一行 CL-30 补修及“待第 3 轮复审，通过前不合入”的状态。完整 1897398..eba3507 另外包括原有 python/bzs/winsecurity.py 的 SID 白名单修复，共四个文件。winsecurity.py 在本次增量中没有再次改动。

本文“仓库事实”指上述 Git 对象直接可见的代码、测试或文档；“本轮验证”指本机实际执行结果；“审核判断”指由这些事实作出的验收决定。文中源码行号均对应 eba3507，不能用当前文档分支的同名文件代替。

本轮未联网查询 PR、CI、来源或条款，未安装或下载依赖。本审核过程唯一新增的仓库文件是本文。开始时已有未跟踪目录 docs/audit/20261031-2487934/，未改动、未作为本轮证据。所有测试副本、脚本、注入文件、XML 和临时结果均置于仓库外的系统临时目录；没有修改仓库代码、配置、测试或既有证据。

收尾已验证临时目录的绝对路径在系统 Temp 下、没有本轮 Python 进程仍运行。清理操作两次被工具的自动审批策略拒绝，返回仅为 blocked by policy，没有给出更具体原因；未继续尝试其他删除方式。故临时目录 C:\Users\Administrator\AppData\Local\Temp\bzs861-cl30-round3-rb5qx2sw 仍保留，不能将其记为已清理；此限制不影响报告或验证结果。

## 执行的验证命令与结果

1. **只读 Git 核对。**执行 git status --short、git branch --show-current、git rev-parse --verify、git log，并读取前轮报告及相关 Git blob。主要 diff 命令如下；两个 diff --check 均通过、无输出。

   ~~~text
   git diff --no-ext-diff --unified=60 9e7ee6b..eba3507 -- tools/create_auth.py python/tests/test_windows_secrets.py docs/CURRENT.md
   git diff --no-ext-diff --name-status 9e7ee6b..eba3507
   git diff --no-ext-diff --name-status 1897398..eba3507
   git diff --check 9e7ee6b..eba3507
   git diff --check 1897398..eba3507
   git show eba3507:tools/create_auth.py
   git show eba3507:python/bzs/winsecurity.py
   git show eba3507:python/tests/test_windows_secrets.py
   ~~~

2. **隔离待测版本。**使用现有 Python 3.12.10、Windows 11 10.0.22624，调用 git archive --format=tar eba3507，路径参数为该树除 validation 外的顶层项，在临时目录解出待测副本；全量 Python 套件不需要历史 validation。使用 venv.EnvBuilder(with_pip=False) 创建临时解释器环境，只引用现有环境的依赖目录，不加载其 bzs 可编辑安装。没有执行 pip。本机原 .venv 中的 bzs 指向当前工作树，故直接用它跑未隔离的测试不足以证明测试了新 head。

   用临时解释器确认：不设置 PYTHONPATH 时 importlib.util.find_spec("bzs") 返回 None；测试主进程显式加载临时副本的 python/bzs。测试后逐字节复核 create_auth.py、winsecurity.py、test_windows_secrets.py、worker/sources/apis.py 和 sources.json 与 eba3507 相同。

3. **Windows 专项与全量 Python 测试。**实际入口及 pytest.main 参数如下，<临时根> 是本轮专用系统临时目录。

   ~~~text
   <临时根>/env/Scripts/python.exe -B <临时根>/run_suite.py windows
   <临时根>/env/Scripts/python.exe -B <临时根>/run_suite.py full

   # 前者在 <临时根>/head/python 内调用：
   pytest -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp <临时根>/pytest-windows --junitxml <临时根>/windows.xml tests/test_windows_secrets.py

   # 后者在相同源码副本内调用：
   pytest -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp <临时根>/pytest-full --junitxml <临时根>/full.xml tests
   ~~~

   两次设置 PYTHONDONTWRITEBYTECODE=1、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1，BZS_HOME、TEMP/TMP、pytest 临时输出均指向本轮临时目录。结果分别为 **22 passed，4.84s** 和 **1883 passed，135.98s**；JUnit 均为零失败、零错误、零跳过。通过 AST 对照确认原 18 个 Windows 测试函数全部保留且函数内容不变，仅增加本轮四个测试。

   临时解释器为 Python socket/DNS 安装外部访问拦截，记录的外部访问尝试为 0。全量测试保留本机回环连接，用于事件循环和本地测试服务；没有访问外部网络、真实来源或远端 CI。

4. **独立失败注入。**执行下列本轮临时脚本；另以同一解释器 -B - 从标准输入执行精确测试观察、真实 icacls 回退注入、源码字节及 AST/XML 核对。未在仓库增加测试文件。

   ~~~text
   <临时根>/env/Scripts/python.exe -B <临时根>/rf1_injections.py
   <临时根>/env/Scripts/python.exe -B <临时根>/rf2_rf3_injections.py
   <临时根>/env/Scripts/python.exe -B -
   ~~~

   注入均使用临时文件和非秘密哨兵；真实 CLI 成功路径生成的临时令牌未打印。下表列出已完成验证的结果。RF1 辅助脚本调试时曾因测试 shim 缺少 os 常量、以及 Windows 删除须关闭标记删除的辅助句柄而未到达目标步骤；修正的仅是临时注入器。下表中的“替换完成”以最终运行中显式记录的 unlink/link 和文件身份断言为准，不把前两次未完成注入算作通过证据。

| 注入/检查 | 本轮观测事实 |
|---|---|
| 旧 9e7ee6b：真实 ACL 验证后 os.unlink + os.link | 替换完成，函数成功返回，既有目标收到哨兵且原内容被覆盖；再次复现原 RF1 |
| 新 eba3507：同样使用原生 os.open、真实保护及核验 | 删除被 PermissionError / WinError 32 拒绝，尚未建立硬链接；函数失败，空输出被清理，既有目标字节不变 |
| 新 eba3507：允许删除共享，强制完成核验后替换 | 真实 ACL 已验证；替换前路径身份与 fd 相同，替换后路径指向既有文件且与 fd 不同；最终 os.fdopen 使用原 fd，函数成功返回，既有目标和替换路径均保持原内容 |
| 新增 RF1 测试原函数，观察其实际子进程结果 | 测试通过，但子进程 exit 1，stderr 为 os.unlink(path) 的 WinError 32，输出 token 不存在 |
| 同一个 RF1 测试，将子进程替换为无关的启动失败 | 测试仍通过，说明其断言不能证明到达过替换或写入步骤 |
| 保护步骤留下显式 INTERACTIVE 读 ACE | 真实 read_dacl 确认 protected=True、S-1-5-4 显式读授权存在；真实核验以该 SID 报错，未到达 fdopen，输出不存在或为空 |
| ctypes 保护报错后走原有 SID icacls 回退写入器 | 先种入显式 S-1-5-4 读 ACE，再使 ctypes 路径抛错；实际 _protect_icacls 保留该 ACE，实际 verify_windows 拒绝，空输出被清理 |
| 强制 bzs 包导入抛 ImportError，保留仓库模块 | _load_winsecurity 实际加载待审树 python/bzs/winsecurity.py；保护、核验及写入成功，复核为 (True, []) |
| bzs 包不可导入，仓库模块文件也不存在 | 保护阶段报 cannot load ... to protect；未到达 fdopen，输出不存在或为空 |
| 保护成功后使包不可导入，且不存在仓库模块 | 核验阶段报 cannot load ... to verify；未到达 fdopen，输出不存在或为空 |
| 包不可导入，仓库模块执行时也抛 ImportError | 异常向外传播，write_exclusive 清理空文件；未到达 fdopen，失败关闭 |
| audit_path 返回 (False, []) | SystemExit，包含 DACL is not protected；未到达 fdopen，输出不存在或为空 |
| write_exclusive 直接面对既有文件 | FileExistsError；未到达 fdopen，既有字节不变 |
| 真实 CLI 首次创建，然后再次使用相同输出路径 | 首次 exit 0，两文件 ACL 均通过；第二次 exit 2、refusing to overwrite，两文件字节均不变 |

**RF1 强制替换的具体边界：**只在内存中替换 create_auth 模块的 os.open 接口，使用 CreateFileW 的 CREATE_NEW 保留独占创建语义，打开时增加 FILE_SHARE_DELETE（共享位为 7）及 DELETE 访问权；os.fdopen、生产 write_exclusive、protect_windows、verify_windows 均执行真实实现。真实 ACL 核验后，另开 DELETE 辅助句柄，调用 SetFileInformationByHandle(FileDispositionInfoEx=21，DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE)，关闭该辅助句柄使目录项实际删除，再 os.link(既有目标, 原路径)。原写入 fd 全程保持打开，并用 fstat/stat 的设备和文件身份记录证明其仍指向原对象。

这项注入验证的是“替换确实完成后，生产写入仍绑定原 fd”的性质。它不是在声称生产 os.open 的默认共享方式允许同样删除，也不是声称发生过真实攻击或秘密泄露。原生 Windows 的拒绝替换和放宽共享后的同 fd 写入分别有直接观测结果。

**验证限度：**本轮运行的是该 head 的 Windows 本地 Python 测试及上述注入，没有运行 CTest、Linux 测试、远端 CI 或实际来源探测；没有核验各 PR 的最新合入状态。内容合入判断不替代执行方 D28 核对。

## 发现

### RF3-1【低 / 非阻塞】RF1 回归未证明完成过路径替换，其成功写入注释不符合本机实际路径

**仓库事实：**python/tests/test_windows_secrets.py:365 的测试在假 audit_path 内执行 unlink/link，但没有记录或断言这两步已完成；:390 的 assert len(result.stdout) >= 0 恒成立。:387 声称写入成功进入已删除的原文件对象，但断言允许子进程任意失败、输出文件不存在。

**本轮验证：**精确调用该测试函数并观察实际 subprocess.run，子进程在 os.unlink(path) 处得到 WinError 32、exit 1，测试仍通过。再将子进程替换为“无关的启动失败”，同一测试仍通过。因此，这个测试本身不足以证明“核验后替换成功，仍通过同一 fd 写入”。

**审核判断：**这是回归证据的缺口，不是本轮发现的新生产覆盖漏洞。源代码已消除按路径重开；本轮额外完成真实 ACL 验证、成功替换、文件身份及 fdopen 描述符一致性的断言，且既有目标未被覆盖，故不保留原 RF1 的合入阻塞。

**给执行方的改进要求：**在后续测试维护中明确区分原生 Windows 拒绝替换和成功替换后仍绑定原对象两种验证。前者须断言已到达注入点及预期共享冲突，后者须断言替换完成、原 fd 身份保持、写入完成及目标不变；移除恒真断言，更正注释。不把当前测试的绿色单独记作“成功替换后写入”证据。该测试改进不作为本次 eba3507 内容合入的新增前置条件。

### RF3-2【低 / 非阻塞】“无已安装 bzs”测试未强制该前提，且没有覆盖所有模块均不可加载

**仓库事实：**python/tests/test_windows_secrets.py:424 的测试仅从子进程环境移除 PYTHONPATH，没有禁用已安装包或阻止 bzs 导入，也未断言实际模块来源。.github/workflows/windows-python.yml 会先将 python[test] 可编辑安装；在这样的环境下，移除 PYTHONPATH 仍可导入 bzs，测试可能一直走包导入分支。该测试是成功路径，不覆盖“包和仓库模块同时不可加载”。

test_protection_that_leaves_extra_read_grants_fails_closed（:397）则以真实 Windows ACL 和真实 audit_path 检查额外 SID，能覆盖额外显式读授权导致核验拒绝；但它把 _load_winsecurity 固定为已有模块，所以也没有覆盖模块不可加载分支。

**本轮验证：**临时隔离解释器确认无 PYTHONPATH 时 bzs 不可导入，整套新测试仍通过。独立注入又显式阻止 bzs 导入并确认仓库模块的 __file__，验证了真正的直接加载成功路径；同时验证保护时无模块、核验时无模块、仓库模块执行抛错三种失败路径，均未到达 fdopen。额外显式授权在主保护路径和真实 icacls 回退写入器之后均被拒绝。

**审核判断：**RF2 的生产行为符合失败关闭要求，原阻塞关闭；但不能把题列两个回归称为已经覆盖“额外授权”和“所有模块均不可加载”这两个分支。

**给执行方的改进要求：**后续使“包不可导入”的前提可验证，并断言直接加载的是仓库模块；补双重不可加载的保护/核验失败回归，断言未写入及错误可见。现有额外授权测试可同时检查 icacls 返回码，明确注入成功。此项为测试证据维护，不要求为本次验收改写 ACL 实现或增加依赖，也不构成本次 head 的新增合入前置条件。

## 决定

### D-R3-1：RF1 验收通过并关闭

**仓库事实：**tools/create_auth.py:53 的独占 os.open 所得 fd 不再在保护前关闭；:62 通过 os.fdopen(fd, ...) 写入同一描述符，没有按路径 open(..., 'w')。保护/核验失败分支先关闭 fd，再清理空文件并传播错误。

**本轮验证：**旧 head 可覆盖替换目标；新 head 在原生 Windows 下拒绝删除，且在明确允许并完成替换的注入下仍保持原 fd 写入、替换目标字节不变。拒绝已有输出和正常创建均通过。

**审核判断：**题定“核验后删除并硬链接替换”的 RF1 性质已取得足够证据。RF3-1 限制的是新增回归单独可证明的内容，不撤销上述独立验收。

### D-R3-2：RF2 验收通过并关闭

**仓库事实：**create_auth.py 已删除账户名 icacls 保护及通过文本标记判断 ACL 的回退。protect_windows 和 verify_windows 均调用 _load_winsecurity；后者调用 SID 级 audit_path。无法取得模块时分别显式失败；加载执行异常也会在写入之前传播。

winsecurity 内部仍可用按 SID 的 icacls 作为保护写入器；这是保留的既有路径，其输出仍必须经过真实 SID/DACL 核验。不能将“移除了账户名回退”误写为“完全移除了 icacls”。

**本轮验证：**显式 INTERACTIVE 读授权被识别；真实内部回退留下相同授权也被识别。正常直接加载和各类完全不可加载场景均有实际注入结果。

**审核判断：**额外授权不会因没有继承标记而获准写入，无法核验时失败关闭。题列测试的覆盖局限按 RF3-2 记录，不再保留 RF2 的生产内容阻塞。

### D-R3-3：RF3 验收通过并关闭

**仓库事实：**create_auth.py:92 条件为 protected is not True or problems，唯一接受状态是 protected is True 且 problems 为空。

**本轮验证：**新增 test_create_auth_rejects_an_unprotected_dacl 明确注入 (False, [])，断言错误、not protected 文案及 token 不存在或为空；本轮独立注入还确认未到达 fdopen。原来的保护抛错、ACL 不可读取等测试保留且通过。

**审核判断：**False 不再被当成核验成功，RF3 关闭。

### D-R3-4：范围遵守，eba3507 内容可合；集成核对仍由执行方执行

**仓库事实与本轮验证：**增量仅为 RF1–RF3、四个回归和 CURRENT 状态行；无 F6/F7、一般重构、CI/签收机制改动。worker/sources/apis.py 与 1897398 字节相同。17 个来源的运行 enabled 和 contract.enabled 仍全部为 false；sources.json 与共同基线字节相同，SHA-256 为 ea53a36f8c3297986bd0dc360cfcd77fb7b92a808ef13dffdcac7c308ff63c4e。原 Windows 18 项全部保留，本轮 22 项和全量 1883 项均绿。

**审核判断：**接受 eba350745fbdad9e1d2a9d901fe8f4cf2ec72d3c 的代码、测试及本 PR 最小状态更新，允许按既定流程合入。此决定替代第 2 轮 D-R2-3、D-R2-6 对旧 9e7ee6b 的“RF1–RF3 未关、不可合入”结论；旧报告原文仍作为历史保留。

CURRENT 的最小补行已落实，但本轮不宣告第 2 轮 RF4 的其余文档/集成事项全部完成。执行方仍须核对本次实际合入 head 的全部 CI 尝试及合并后 main，按 D20/D28 如实登记；本轮没有代为执行或预签该核对。

### D-R3-5：仅关闭激活前置条件中的“CL-30 新 head 复审通过”一项

本轮通过安全内容复审，使第 2 轮 D-R2-5/7 规定的 CL-30 复审条件成立；CL-30 真正合入以及其他前置条件仍须分别完成。本文不能证明 F2/原裁决、F6/F7、相关报告和更正后的状态/来源记录已进入 main，也没有验证激活用最终合同、预算、有效期或清单。

待 D-R2-5 的前置条件及既定集成要求全部满足，执行方可依据已有 D-R2-7 许可进入独立的 mwmbl、wikipedia_en、wikipedia_zh 激活批次，不必仅为已关闭的 RF1–RF3 再请求同范围安全复审。其余来源继续遵守原决定，marginalia 不因本轮通过而获批。最终配置验证、用户有界复探和正式 accept_s1 仍按 D-R2-7/8 执行；本轮没有批准立即启用，也没有预签 S1。

## 给执行方的指令

1. 原样归档本文，按 D-R3-1/2/3 登记 RF1、RF2、RF3 关闭；将 eba3507 标为“第 3 轮内容验收通过、CI/合入由执行方核对”。保留前轮报告和历史证据原文，不追改旧 head 的结论。
2. 对实际待合 head 按 D20/D28 核对全部 CI 尝试，处理、登记失败后依既定流程合入 PR #26，并核对合并后 main。合并 CURRENT 时保留后续文档批次的正确状态，不用本分支旧上下文覆盖其更新；登记实际合入 SHA，不把本文当作 CI 绿或已经合入的证明。
3. 如实记录 RF3-1、RF3-2 的测试局限和本轮独立注入结论，在后续测试维护中补足断言与模块不可加载覆盖。这两项不阻塞本次已审 head 合入；若因此产生新 head，CI/D28 须对应新 head。若改动已审生产安全语义，则新内容另交复审。
4. 核对 D-R2-5/7 其余前置条件后，按原有条件许可推进独立激活批次，再按 D-R2-8 固定最终代码/配置、安排复探及完整签收。不要把“CL-30 复审通过”登记成“全部前置完成”“已经激活”或“S1 完成”。

签署：**Codex，2026-11-15（Asia/Taipei）。**本轮裁定对象仅为 PR #26 的 eba350745fbdad9e1d2a9d901fe8f4cf2ec72d3c。
