# bzs861 审核复现文件（基线 `44253a7`，2026-10-24）

这些程序只调用核心库或 worker 模块，使用内存数据库和可注入时钟，不访问任何外部网站。

| 文件 | 对应问题 | 预期输出（基线 44253a7） |
| --- | --- | --- |
| `permanent_caps.cpp` | P1-1 | 第 10001 次搜索 `E_SESSION_BACKPRESSURE`，30 天后仍拒；第 10001 个任务 `E_QUOTA`，`compact_jobs` 回收 0 |
| `hourly_session_cap.cpp` | P1-2 | 第 65 次本地搜索 `E_QUOTA`（429） |
| `phrase_recall.cpp` | P1-3 | 普通词命中 1；短语、`c++`、`node.js`、`café`、`Москва` 命中 0、partial=true |
| `scan_blocking.cpp` | P2-5 | 参数 `[文档数] [每篇字节数]`，默认 2000 204800；打印查询耗时和另一用户 `stats()` 的最长等待 |
| `vary_cache.cpp` | P2-1 | `Vary: Accept-Encoding` 时 fresh=false、reason=unsupported_vary |
| `default_sources.cpp` | S1、P3-4 | 参数为 `config/sources.json`；0 个来源启用，`web.search` 返回 `E_SCHEMA` |
| `robots_tokens.py` | P2-3 | `360Spider`、`Sogou web spider`、`Googlebot-Image/1.0` 任一组 → deny_all |
| `charset_fallback.py` | P2-4 | 英文页夹 0x92 → 按 gb18030 解成 `It抯` |
| `query-lowercase-once.patch` | P2-5 局部修复 | `git apply` 后 CTest 27/27；4 短语 OR 约快 32% |

## 构建与运行

C++ 程序在仓库根目录、`cmake --preset dev && cmake --build --preset dev` 之后编译（json-c、libxml2 装在非系统前缀时，补上 `-L<前缀>/lib -Wl,-rpath,<前缀>/lib`）：

```sh
D=docs/reviews/claude-2026-10-24
g++ -std=c++20 -O1 -I include $D/permanent_caps.cpp build/libbzs_core.a \
    -lsqlite3 -lcrypto -ljson-c -lxml2 -pthread -o /tmp/permanent_caps && /tmp/permanent_caps
```

其余 `.cpp` 同理替换文件名；`default_sources` 运行时传 `config/sources.json`。性能数字用 Release 库更有意义：`cmake --preset release && cmake --build build-release --target bzs_core`，再把 `build/libbzs_core.a` 换成 `build-release/libbzs_core.a`、`-O1` 换成 `-O2`。

Python 片段需要 worker 依赖：

```sh
PYTHONPATH=python python docs/reviews/claude-2026-10-24/robots_tokens.py
PYTHONPATH=python python docs/reviews/claude-2026-10-24/charset_fallback.py
```

补丁：`git apply docs/reviews/claude-2026-10-24/query-lowercase-once.patch`（基线 `44253a7`，只改 `src/query.cpp`，不改语义）。
