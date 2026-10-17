# 基础依赖与许可记录

本批使用系统安装的基础库，没有复制四个参考产品的源码或安装其后端。这里不是全依赖/SBOM 发布验收；最终版本冻结、跨平台解析依赖与许可证复核仍待执行。

| 组件 | 本次使用 | 上游资料 |
|---|---|---|
| SQLite | 元数据、事务、FTS5、一致性备份 | https://www.sqlite.org/fts5.html ; https://www.sqlite.org/backup.html ; https://www.sqlite.org/copyright.html |
| Boost.Asio / Beast | C++ 回环 HTTP 的异步读写与超时 | https://www.boost.org/doc/libs/latest/libs/beast/doc/html/beast/using_io/timeouts.html ; https://www.boost.org/LICENSE_1_0.txt |
| json-c | JSON 值、引用计数和解码；严格词法与 Unicode 检查另行实现 | https://json-c.github.io/json-c/ ; https://github.com/json-c/json-c/blob/master/COPYING |
| libxml2 | 受限的 HTML 树解析，不加载外部内容 | https://gitlab.gnome.org/GNOME/libxml2 |
| OpenSSL Crypto | CSPRNG、SHA-256、定长凭据比较 | https://www.openssl.org/ |
| Python / jsonschema（仅测试工具） | 原计划合同样例与 HTTP/CLI 测试 | https://python-jsonschema.readthedocs.io/ |

本轮系统观察版本见 `validation/takeover/environment.json`；原0.0.1版本记录见 `validation/environment.json`，这些不是对任意机器的保证。没有隐式 FetchContent、没有下载浏览器或模型。使用系统包可离线构建的前提是依赖已经安装。

最终代码许可证由仓库所有者决定；依赖自己的许可继续适用。本轮CI已将actions/checkout固定到 `11d5960a326750d5838078e36cf38b85af677262`，Windows vcpkg固定到 `6719fd4d4cd3b2d4575f6164da4a02f757b3b067`，不依赖runner上漂移的vcpkg checkout。引用已通过上游Git对象核验；这不代表所有传递依赖或SBOM完成发布审查。Linux仍使用系统软件包，本次版本另行记录。

固定引用依据（核对日期2026-10-17）：[checkout v4 ref](https://api.github.com/repos/actions/checkout/git/ref/tags/v4)、[vcpkg commit](https://github.com/microsoft/vcpkg/commit/6719fd4d4cd3b2d4575f6164da4a02f757b3b067)、[SQLite fts5 feature](https://github.com/microsoft/vcpkg/blob/6719fd4d4cd3b2d4575f6164da4a02f757b3b067/ports/sqlite3/vcpkg.json)。
