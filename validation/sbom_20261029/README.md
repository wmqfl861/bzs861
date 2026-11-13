# GOV-01 SBOM 与许可核对记录（2026-10-29）

工具：`tools/sbom_report.py`（仅标准库、离线、只读；绑定 `git rev-parse HEAD`）。
计划：`docs/planning/THREE_NODES_20261029.md` 节点 3，含审核修订 #10（实测计数）。

- 提交：`146138773eb3ad114a5c2b8a8c479b31e24aa98c`（分支 `work/blueprint-v2-dev-20261029`）
- 报告：`sbom-report.json`（本目录；行按 kind+name 排序，`sort_keys` JSON）

## 实测计数

| 项 | 实测 | 说明 |
|---|---|---|
| 锁定 Python 包 | 60 | lock 62 行 = 60 固定 + 2 注释（修订 #10；脚本运行时断言） |
| vcpkg SPDX 文档 | 67 | 55 个 boost-* 端口 + json-c/libiconv/libxml2/openssl/sqlite3/zlib + 5 个 vcpkg-* 构建工具端口 |
| 已装端口（installed/vcpkg/status 去重） | 67 | 与 SPDX 集合一致；本机 0 个端口走无-SPDX 回退 |
| 报告总行 | 127 | 每行 name/kind/version/license/license_evidence/replacement_strategy |

share/ 下另有 `doc`、`man`、`iconv`、`unofficial-sqlite3`、`boost`、`boost-1.92.0`、`boost_*` 目录无 SPDX——它们是端口附带的 usage/doc 产物而非端口，不产生行。无-SPDX 端口回退（端口 copyright 文件文本，否则 NOASSERTION）在本机无实例，已用合成 fixture 验证三分支（SPDX 正常 / copyright 回退 / NOASSERTION）后运行本报告。

## 绑定与不变性

- vcpkg baseline：`6719fd4d4cd3b2d4575f6164da4a02f757b3b067`（`.github/workflows/ci.yml` 固定 ref；本地 `C:/tools/vcpkg-bzs` git 对象库中该对象类型实测为 `commit`）。
- lock sha256：`0b7863d00c53c72b43a29a51c9887199601f24bf3e85041302f8b9d19129bcfb`；`git show HEAD:tools/requirements-s1-lock.txt` 字节哈希相同 → **锁定文件相对 HEAD 未变，无新增依赖**。
- 本地绑定哈希（lock 文件 + 60 份 dist-info METADATA 聚合 + 67 份 vcpkg.spdx.json 聚合）见报告 `binding`。**这不是 wheel 哈希**：`audit-dependencies.yml` 仅有 wheels 一 job，在 CI 下载 cp313 wheel 并对其字节做 sha256；本报告离线运行，只能对 lock 与 METADATA/SPDX 文本哈希，作为替代绑定并如此标注（报告 `binding.local_binding_note`）。
- CI runner 系统包仍以既有 `validation/s1_review/environment.json` 为准（记录未复审）。

## 确定性证明

同一命令运行两次（第二次写 `sbom-report.run2.json`，对比后删除）：

- 原始字节仅 `generated_at` 一处不同（12:25:39Z / 12:26:14Z）；
- 将 `"generated_at": "<ts>"` 归一后两次输出字节相同，sha256 均为
  `ceb77da32405a65d25bea0865cbb749dd1a00918e964bfc95842c0f032cdc6ab`。

## 异常（报告中显式枚举，共 13）

- 无许可证据（NOASSERTION）：`libiconv`、`vcpkg-make`（SPDX `licenseConcluded=LicenseRef-vcpkg-null`，端口目录亦无 copyright 文件）。
- 非 SPDX 自由文本：`python-dateutil`（"Dual License"；classifier 示 BSD/Apache）、`aiosignal`（"Apache 2.0"）、`jusText`（"The BSD 2-Clause License"）。
- 复合许可：`aiohttp`（Apache-2.0 **AND** MIT）、`regex`（Apache-2.0 AND CNRI-Python）、`cryptography`（Apache-2.0 OR BSD-3-Clause）、`packaging`（Apache-2.0 OR BSD-2-Clause）、`tld`。
- 强 copyleft 选项：`tld`（MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later）；弱 copyleft：`certifi`（MPL-2.0）、`tld`。
- SQLite 实测 SPDX id 为 `blessing`，手工表原记 "Public Domain"（意译）→ diff 判 mismatch；aiohttp、pytest/pytest-asyncio 的“按发行物核对”占位由实测细化（AND / 分包一致）。手工表未覆盖 113 行（组件级表格；其余 boost 端口、zlib、libiconv、vcpkg-* 工具端口与传递 Python 固定项）。
- `replacement_strategy` 全部为 null：仓库内无逐包替换策略证据（docs/ 于 HEAD 检索无映射；“后续候选”是新增候选非替换），不虚构。

## 边界

不是全产品签收（C-16 需独立审核者）；83/161/10 与 37 partial / 46 not_started / 0 accepted 计数不变，GOV-01 保持 partial；项目许可证仍由仓库所有者决定。本目录（报告 + 本 README）≤50KB。
