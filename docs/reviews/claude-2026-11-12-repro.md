# bzs861 第三轮审核：复现程序、命令与原始输出

配套主报告：[claude-2026-11-12.md](claude-2026-11-12.md)。下面所有程序都离线运行，不访问任何外部站点。输出是 2026-11-12 在 2 vCPU / 7 GB Linux 容器（ext4）上的原始结果；计时只用于比较量级，不是性能基准。

## 环境

- GCC 13.3.0、Clang 18.1.3、CMake 3.28.3、Ninja 1.11.1
- SQLite 3.45.1、libxml2 2.9.14、json-c 0.17、Boost 1.83、OpenSSL 3.0.13
- Python 3.13.18；依赖按 `tools/requirements-s1-lock.txt` 安装，`pip check` 无冲突
- 被测提交：`a2e5e4f`（`work/review-2487934-a`）。对照提交：`d89c38b`（R-03 之前）、`2487934`（R-01 之前）

## 构建与全量测试（与 CI 的 linux job 一致）

```bash
python3 -m venv $VENV && $VENV/bin/pip install -r tools/requirements-s1-lock.txt
$VENV/bin/pip install --no-deps --no-build-isolation -e "python[test]" && $VENV/bin/pip check
cmake -S . -B $BUILD -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBZS_PYTHON=$VENV/bin/python -DBZS_REQUIRE_PYTHON_TESTS=ON
cmake --build $BUILD -j 2          # real 1m24s，全树 1 条告警：tests/cache_freshness.cpp:333 -Wmisleading-indentation
(cd $BUILD && ctest --output-on-failure --output-junit ctest.xml)
# 100% tests passed, 0 tests failed out of 42 ; Total Test time (real) = 345.11 sec
(cd python && $VENV/bin/python -m pytest -q -p no:cacheprovider tests -rs)
# 1703 passed, 5 skipped in 29.01s
# 跳过的 5 个：test_launcher_boundaries.py:140/177（Windows termination-handle race）、
#               test_process_tree.py:321/341/361（real Windows process tree）
```

## R1（CL-02）`site:` 查询截断时 `partial` 失真

`site_partial.cpp`：

```cpp
// Repro (R-03 follow-up): a site: query whose target lies beyond the enlarged SQL
// window must either return the target or report partial=true. Offline; links the
// Debug core library. Usage: site_partial <tmpdir> <others> <max_candidates>
#include "bzs/store.hpp"
#include "bzs/query.hpp"
#include <cstdio>
#include <filesystem>
#include <string>

using namespace bzs;

int main(int argc, char** argv) {
  if (argc != 4) { std::fprintf(stderr, "usage: %s DIR OTHERS MAX_CANDIDATES\n", argv[0]); return 2; }
  const std::filesystem::path dir = argv[1];
  const int others = std::stoi(argv[2]);
  const std::int64_t max_candidates = std::stoll(argv[3]);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  Store store(dir / "db");
  // Target first (oldest), long body with a single occurrence -> lowest BM25.
  std::string long_body = "alpha";
  for (int i = 0; i < 400; ++i) long_body += " filler" + std::to_string(i);
  store.ingest("alice", {"https://target.example/doc", long_body, "text/plain", "target page"});
  // Many newer, short, term-dense documents on another host -> rank above the target.
  for (int i = 0; i < others; ++i)
    store.ingest("alice", {"https://other.example/p" + std::to_string(i), "alpha alpha alpha alpha", "text/plain",
                           "alpha " + std::to_string(i)});
  for (const char* text : {"alpha site:target.example", "\"alpha\" site:target.example"}) {
    const auto query = parse_query(Json::string(text));
    const auto r = store.search_once("alice", query, max_candidates, 10);
    const auto hits = r.at("hits");
    bool found = false;
    for (std::size_t i = 0; i < hits.size(); ++i)
      if (hits.at(i).at("url").str() == "https://target.example/doc") found = true;
    const bool partial = r.has("partial") && r.at("partial").flag();
    const auto retrieval = r.has("retrieval") ? r.at("retrieval").dump() : std::string("{}");
    std::printf("others=%d max_candidates=%lld query=%-30s found=%s partial=%s  %s\n", others,
                static_cast<long long>(max_candidates), text, found ? "yes" : "NO", partial ? "true" : "false",
                (!found && !partial) ? "<-- VIOLATION: silent truncation" : "ok");
    std::printf("    retrieval=%s\n", retrieval.substr(0, 300).c_str());
  }
  return 0;
}
```

构建与运行（Linux）：

```bash
g++ -std=c++20 -O1 -I include site_partial.cpp $BUILD/libbzs_core.a -lsqlite3 -lcrypto -lxml2 -ljson-c -lpthread -o site_partial
for args in "8 1" "30 1" "30 2"; do ./site_partial /tmp/sp-$RANDOM $args; done
```

Windows：在一个临时构建目录里加 `add_executable(site_partial_repro <路径>/site_partial.cpp)` 和 `target_link_libraries(site_partial_repro PRIVATE bzs_core)`，不要提交。更省事的做法是直接把它写成 CL-02 的回归测试：先确认它在当前代码上失败，再修。

当前核心（`a2e5e4f`）的输出：

```
others=8 max_candidates=1 query=alpha site:target.example      found=yes partial=false  ok
    retrieval="fts5_bm25_verified"
others=8 max_candidates=1 query="alpha" site:target.example    found=yes partial=false  ok
    retrieval="fts5_substring_verified"
others=30 max_candidates=1 query=alpha site:target.example      found=NO partial=false  <-- VIOLATION: silent truncation
    retrieval="fts5_bm25_verified"
others=30 max_candidates=1 query="alpha" site:target.example    found=NO partial=false  <-- VIOLATION: silent truncation
    retrieval="fts5_substring_verified"
others=30 max_candidates=2 query=alpha site:target.example      found=NO partial=false  <-- VIOLATION: silent truncation
    retrieval="fts5_bm25_verified"
others=30 max_candidates=2 query="alpha" site:target.example    found=NO partial=false  <-- VIOLATION: silent truncation
    retrieval="fts5_substring_verified"
```

R-03 之前的核心（`d89c38b`，只构建 `bzs_core` 目标）的输出：

```
others=30 max_candidates=1 query=alpha site:target.example      found=NO partial=true  ok
    retrieval="fts5_bm25_verified"
others=30 max_candidates=1 query="alpha" site:target.example    found=NO partial=true  ok
    retrieval="fts5_substring_verified"
others=30 max_candidates=2 query=alpha site:target.example      found=NO partial=true  ok
    retrieval="fts5_bm25_verified"
others=30 max_candidates=2 query="alpha" site:target.example    found=NO partial=true  ok
    retrieval="fts5_substring_verified"
```

修复后的期望：上面每一行都是 `found=yes`（host 条件下推到 SQL 后，目标不会再被其他 host 挤出窗口）。

## R2（CL-04）MCP 参数被 SDK 静默改写

`mcp_null_scope.py`：直接驱动真实 MCP SDK（`mcp==2.3.0`）的 `call_tool`，只把向核心发请求的 `_call` 换成记录函数，不需要启动核心。

```python
"""Repro: does the MCP layer forward an explicit scope_path or silently drop it?

Patches bzs.mcp_server._call so no core is needed, then drives the real MCP SDK
call_tool path exactly as a client would (arguments as JSON values).
"""
import asyncio
import json

import bzs.mcp_server as m

captured = []


async def fake_call(op, params):
    captured.append((op, dict(params)))
    return {"job_id": "x", "state": "queued"}


m._call = fake_call


async def main():
    server = m.build_server()
    cases = [
        ("crawl_site", {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k1",
                        "scope_path": "null"}),
        ("map_site", {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k2",
                      "scope_path": "null"}),
        ("map_site", {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k3",
                      "max_urls": "null"}),
        ("read_urls", {"urls": ["https://a.example/"], "idempotency_key": "k4", "max_requests": "null"}),
        ("read_urls", {"urls": ["https://a.example/"], "idempotency_key": "k5", "max_bytes": True}),
        ("map_site", {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k6",
                      "unexpected_field": 1}),
    ]
    for name, args in cases:
        before = len(captured)
        try:
            await server.call_tool(name, args)
            sent = captured[before][1] if len(captured) > before else None
            print(f"{name:<10} args={json.dumps(args, ensure_ascii=False)}\n           -> FORWARDED to core: {json.dumps(sent)}")
        except Exception as exc:  # ToolError from the guard or SDK validation
            print(f"{name:<10} args={json.dumps(args, ensure_ascii=False)}\n           -> REFUSED: {type(exc).__name__}: {str(exc)[:120]}")


asyncio.run(main())
```

运行：在仓库以外的目录执行 `<venv>/bin/python mcp_null_scope.py`（Windows：`.venv-win\Scripts\python.exe mcp_null_scope.py`）。

输出（`a2e5e4f`）：

```
crawl_site args={"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k1", "scope_path": "null"}
           -> REFUSED: ToolError: E_SCHEMA: scope_path must be an absolute URL path
map_site   args={"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k2", "scope_path": "null"}
           -> FORWARDED to core: {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k2"}
map_site   args={"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k3", "max_urls": "null"}
           -> FORWARDED to core: {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k3"}
read_urls  args={"urls": ["https://a.example/"], "idempotency_key": "k4", "max_requests": "null"}
           -> FORWARDED to core: {"urls": ["https://a.example/"], "idempotency_key": "k4", "timeout_ms": 600000, "per_page_timeout_ms": 20000, "max_attempts": 1}
read_urls  args={"urls": ["https://a.example/"], "idempotency_key": "k5", "max_bytes": true}
           -> FORWARDED to core: {"urls": ["https://a.example/"], "idempotency_key": "k5", "timeout_ms": 600000, "per_page_timeout_ms": 20000, "max_attempts": 1, "max_bytes": 1}
map_site   args={"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k6", "unexpected_field": 1}
           -> FORWARDED to core: {"site_id": "docs", "seeds": ["https://a.example/docs/"], "idempotency_key": "k6"}
```

修复后的期望：除第一行外，其余 5 行全部是 `REFUSED`。

## R3（CL-03）发布"收紧上限"路径的变异检查

`mutate.sh`：在一个临时 worktree 里，把 `src/sessions.cpp` 的三条收紧语句分别禁用，重建并运行 8 个原生套件，报告哪个套件杀死了变异体。

```bash
#!/usr/bin/env bash
# Usage: REPO=/path/to/bzs861 WORK=/tmp/bzs-mut mutate.sh <commit> <label>
set -u
COMMIT=$1; LABEL=$2
WT=$WORK/wt-$LABEL; B=$WORK/build-$LABEL
git -C "$REPO" worktree remove -f "$WT" >/dev/null 2>&1
git -C "$REPO" worktree add -f "$WT" "$COMMIT" >/dev/null 2>&1 || { echo "worktree failed"; exit 1; }
cmake -S "$WT" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBZS_BUILD_TESTS=ON -DBZS_PYTHON=/usr/bin/false >/dev/null 2>&1
SUITES="bzs_session_tests bzs_site_publication_tests bzs_source_publication_tests bzs_tests bzs_batch_tests bzs_direct_shared_tests bzs_revocation_tests bzs_crawl_tests"
F=$WT/src/sessions.cpp
cp "$F" "$F.orig"
declare -A MUT
# M1: too many contributing URLs -> captured_mode=2 (restrict) is skipped
MUT[M1_contributor_overflow]='s/if(urls.size()>24||contributing_urls.size()>600) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M2: incomplete stored binding scan -> captured_mode=2 is skipped
MUT[M2_incomplete_records]='s/if(incomplete) { bindings.set("captured_mode",std::int64_t{2});return bindings; }/if(false) { }/'
# M3: legacy crawl marker no longer propagated into the captured bindings
MUT[M3_legacy_marker]='s/if(stored.has("legacy_crawl_unverified")\&\&stored.at("legacy_crawl_unverified").flag())bindings.set("legacy_crawl_unverified",true);//'
for name in M1_contributor_overflow M2_incomplete_records M3_legacy_marker; do
  cp "$F.orig" "$F"
  sed -i "${MUT[$name]}" "$F"
  if cmp -s "$F" "$F.orig"; then echo "[$LABEL] $name: PATTERN NOT APPLIED (skipped)"; continue; fi
  if ! cmake --build "$B" -j 2 --target $SUITES >"$B/build-$name.log" 2>&1; then echo "[$LABEL] $name: BUILD FAILED"; continue; fi
  killed=""
  for t in $SUITES; do
    (cd "$B" && timeout 300 "./$t" >"$B/run-$name-$t.log" 2>&1) || killed="$killed $t"
  done
  if [ -n "$killed" ]; then echo "[$LABEL] $name: KILLED by:$killed"; else echo "[$LABEL] $name: SURVIVED (no suite failed)"; fi
done
cp "$F.orig" "$F"
```

输出：

```
[head] M1_contributor_overflow: SURVIVED (no suite failed)      # a2e5e4f
[head] M2_incomplete_records: SURVIVED (no suite failed)
[head] M3_legacy_marker: SURVIVED (no suite failed)
[preR01] M1_contributor_overflow: SURVIVED (no suite failed)    # 2487934
[preR01] M2_incomplete_records: SURVIVED (no suite failed)
[preR01] M3_legacy_marker: SURVIVED (no suite failed)
```

确认变异确实生效：每个变异的构建日志里都有 `sessions.cpp.o` 的重新编译；`run-M1_contributor_overflow-bzs_session_tests.log` 末尾是 `23 tests, 319 assertions, 0 failures`。

Windows 等价做法：不用 bash，手工改一处 → `cmake --build build-win --config Debug --target bzs_session_tests ...` → 运行这 8 个套件 → `git checkout -- src/sessions.cpp`，三个变异各做一次。修复后，每个变异都应该至少让一个套件失败，或者对应代码已被删除，并附上可达性说明（见主报告 CL-03）。

## R4（CL-10）测试耗时：Debug/Release 与 `synchronous`

```bash
# Debug，as shipped（src/store.cpp:247 synchronous=FULL）
for t in bzs_session_tests bzs_direct_shared_tests bzs_tests; do /usr/bin/time -f "$t %es" ./$t >/dev/null; done
# 仅实验：把 src/store.cpp:247 改成 synchronous=OFF 后重建，同样计时（实验后还原，不要提交）
# Release：cmake -DCMAKE_BUILD_TYPE=Release，只构建这几个目标并计时
```

| 套件 | Debug + FULL | Debug + OFF | Release + FULL |
|---|---:|---:|---:|
| bzs_session_tests | 22.23 s | 21.67 s | 10.05 s |
| bzs_direct_shared_tests | 20.17 s | 19.45 s | 8.27 s |
| bzs_tests | 9.42 s | 7.32 s | 7.74 s |
| bzs_long_document_recall_tests | 43.74 s（CTest） | – | 12.91 s |

全部 `rc=0`。核心库里 `assert(` 出现次数为 0（`grep -rn '\bassert(' src include apps`），所以 Release 和 RelWithDebInfo 不会跳过任何不变量检查。

Windows 上请做同样的 2×2×2 测量（Debug 对 RelWithDebInfo、FULL 对 OFF、测试临时目录是否加入 Defender 排除），把结果附到 issue #4。

## R5（CL-01）孤立分支里丢失的两个测试

```bash
git cherry -v a2e5e4f origin/work/blueprint-ten-modules-20261029
# - 438d3d4 fix(probe): bound total checkpoint JSON nodes                      （已有等价补丁）
# - 754013c test(probe): reject excessive checkpoint JSON node counts           （已有等价补丁）
# + 40e2afd test(map): verify concurrent same-origin admission fully releases   （不在开发线上）
# + 352b006 test(crawl): verify concurrent same-origin pages release admission  （不在开发线上）
# + 45761f1 docs(plan): record ten-node reliability and acceptance batch
# + 13591de test(validation): record ten-node blueprint batch
git grep -c test_map_parallel_same_origin_requests_release_all_host_admission_slots a2e5e4f -- python/tests   # 无输出
git grep -c test_crawl_parallel_same_origin_pages_release_all_admission_slots a2e5e4f -- python/tests        # 无输出

# 在 a2e5e4f 的临时分支上：
git cherry-pick 40e2afd 352b006            # 无冲突
cd python && python -m pytest -q -p no:cacheprovider \
  "tests/test_map_worker.py::test_map_parallel_same_origin_requests_release_all_host_admission_slots" \
  "tests/test_crawl_worker.py::test_crawl_parallel_same_origin_pages_release_all_admission_slots"
# 2 passed in 1.22s
```

## 静态分析（结论：没有确认的缺陷）

```bash
cppcheck --enable=warning,performance,portability --std=c++20 --inline-suppr --quiet -I include \
  --suppress=missingIncludeSystem --suppress=unknownMacro src apps
run-clang-tidy-18 -p $BUILD -j 2 -quiet -checks='-*,bugprone-*,concurrency-*,performance-*,-bugprone-easily-swappable-parameters,-bugprone-narrowing-conversions,-performance-unnecessary-value-param,-bugprone-implicit-widening-of-multiplication-result,-performance-avoid-endl,-performance-enum-size,-bugprone-exception-escape' "$PWD/src/.*\.cpp"
ruff check --select E9,F,B --ignore B008 python/bzs
bandit -q -r python/bzs -ll
```

- cppcheck：6 条。`src/highlight.cpp:101 invalidContainer` 是误报：`push_back` 时 `ranked` 为空，没有指针会失效。`src/json.cpp:160 containerOutOfBounds` 是误报：前一行的 `require()` 越界时会抛异常。其余 4 条是传值改传引用的建议。
- clang-tidy：56 条。25 条 `unchecked-optional-access` 都有 `require(x.has_value(),...)` 守护（例如 `src/web.cpp:1634`、`src/service.cpp:57`），属于误报。19 条 `empty-catch` 不是正确性缺陷，但没有任何日志，见主报告 CL-14。其余是 branch-clone 和字符串拼接之类的风格问题。
- ruff：11 条。5 条 B023 都在 `python/bzs/evaluation_ablation.py:212-216`，闭包在同一轮循环内立即调用，不存在延迟绑定；另有 3 条 B007 和 3 条 B905，属风格问题。
- bandit：中等及以上级别 0 条。
