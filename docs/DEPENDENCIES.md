# 依赖与许可记录（0.1.1 S1 review candidate）

本轮保留 C++ 核心基础依赖，整合 Python worker/MCP。运行依赖声明分别见 [CMakeLists](../CMakeLists.txt) 与 [pyproject.toml](../python/pyproject.toml)；实际安装版本、来源和验证以 [本轮报告](../validation/s1_review/test_report.json) 及关联环境记录为准。

不安装或调用 SearXNG、Scrapling、Firecrawl、Exa 成品后端。2026-10-29（GOV-01）起，锁定集合的离线 SBOM 已实测生成并绑定提交：60 个锁定 Python 包（dist-info METADATA 按 License-Expression→License→License:: classifier 回退取证并记录所用字段）与 67 个 vcpkg 端口（`vcpkg.spdx.json`；缺 SPDX 的端口回退端口 copyright 文件，否则 NOASSERTION）逐条记录版本、许可、证据与替换策略，见 [sbom_report.py](../tools/sbom_report.py) 与 [核对记录](../validation/sbom_20261029/README.md)。下表标签已与该实测证据对齐；NOASSERTION、非 SPDX 文本与复合/强 copyleft 项在报告中显式枚举。许可文本本身未重读或作法律复审；CI runner 系统包仍按既有 [environment.json](../validation/s1_review/environment.json) 记录（记录未复审）。

## 当前依赖范围

| 组件 | 用途 | 许可标签（2026-10-29 实测核对） |
|---|---|---|
| SQLite（含 FTS5） | 事务、快照、全文索引、自定义 tokenizer、备份 | blessing（SPDX id；原记 Public Domain 为意译） |
| Boost.Asio / Beast | 核心与 worker 通道的回环 HTTP | BSL-1.0（实测共 55 个 boost 端口：54×BSL-1.0 + boost-uninstall MIT） |
| json-c | JSON 值与解码，严格词法由项目约束 | MIT（实测一致） |
| libxml2 | 显式本地 HTML 导入 | MIT（实测一致） |
| zlib | libxml2/OpenSSL 压缩传递依赖 | Zlib（实测） |
| libiconv | libxml2 传递依赖 | NOASSERTION（vcpkg SPDX 为 LicenseRef-vcpkg-null，端口无 copyright 文件，显式待核） |
| OpenSSL libcrypto | 随机数、摘要和定长比较 | Apache-2.0（实测一致） |
| aiohttp | Python 抓取与 worker RPC | Apache-2.0 AND MIT（实测 License-Expression；原记 " / " 占位已定界） |
| lxml / cssselect | 页面和结果解析 | BSD-3-Clause（实测一致） |
| trafilatura | 正文提取与元数据 | Apache-2.0（实测一致） |
| MCP Python SDK | stdio、本机 HTTP 与协议适配 | MIT（实测 mcp 与 mcp-types 一致） |
| pytest / pytest-asyncio | Python 测试 | MIT / Apache-2.0（实测一致） |

本轮实际安装并测试 Python 3.12.14、aiohttp 3.14.5、lxml 6.1.3、trafilatura 2.3.1、MCP SDK 2.3.0，完整直接与传递依赖固定于 [requirements-s1-lock.txt](../tools/requirements-s1-lock.txt)，环境和 pip check 结果在 [environment.json](../validation/s1_review/environment.json)。构建 backend 固定为 setuptools 84.0.0；CI 使用同一锁定文件并禁止缺依赖时跳过必要套件。SBOM 实测核对（60 锁定包 + 67 vcpkg 端口）与双跑确定性证明见 [validation/sbom_20261029](../validation/sbom_20261029/README.md)。

## 固定引用与可复现边界

保留已交付 CI 的 Action 固定 SHA、Windows vcpkg 固定基线和命令失败检查。本轮最终引用以仓库工作流为准，新增 Action 也需按相同方式核验和固定。Python 依赖使用本轮锁定集合。Linux 系统包仍由 Ubuntu 仓库解析，实际版本已经记录；固定 Action/vcpkg 引用不等于完整系统镜像复审——锁定依赖的 SBOM 已于 2026-10-29 实测生成（见上），系统镜像与 CI 系统包仍属记录未复审。

0.0.2 依赖证据在 `validation/takeover/environment.json`，0.0.1 历史在 `validation/environment.json`，两者不会被改成本轮版本。CMake 不隐式下载浏览器或模型；离线构建的前提是依赖已经安装。

## 后续候选

Playwright、PDF/Office 解析组件、OpenCC、sqlite-vec 等属于后续切片的候选。采用时再核对具体版本、许可、平台包、执行隔离和等效验收；本轮未把计划依赖当成已经集成或验证。

GOV-01 / SPIKE-01 保留依赖和许可核验工作，RELEASE-01 承接最终 SBOM、锁定和打包验收。项目许可证由仓库所有者决定，目前未指定，依赖各自许可继续适用。
