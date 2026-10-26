# 复现程序与原始输出（配合 claude-2026-10-26.md）

本文件只收录复现程序的源码、构建命令、语料来源和原始输出，供核对 [claude-2026-10-26.md](claude-2026-10-26.md) 里的数字。程序都是一次性的审核工具，不属于产品代码，也不要求合入测试套件；第 4 节各条的"验收"才是要写进正式测试的内容。

## 环境与构建

- 2 vCPU / 7 GB 的 Linux 容器；GCC 13.3；SQLite 3.45.1；Python 3.13；json-c 与 libxml2 从源码装在 `/opt/bzsdeps/prefix`。
- 核心库：分别在 `6410d27` 和 `44253a7` 的源码树里执行
  ```sh
  cmake --preset release -DCMAKE_PREFIX_PATH=/opt/bzsdeps/prefix
  cmake --build build-release --target bzs_core searchd
  ```
- C++ 复现程序（`new_*` 链接 `6410d27` 的库，`old_*` 链接 `44253a7` 的库，源码相同）：
  ```sh
  g++ -std=c++20 -O2 -I<repo>/include -I/opt/bzsdeps/prefix/include X.cpp <repo>/build-release/libbzs_core.a \
      -L/opt/bzsdeps/prefix/lib -Wl,-rpath,/opt/bzsdeps/prefix/lib -lsqlite3 -lcrypto -ljson-c -lxml2 -pthread -o new_X
  ```
- Python 复现程序：`PYTHONPATH=<repo>/python python -I X.py`。
- 全部离线：只用内存 SQLite 或回环端口；URL 都是 `example.com` 占位，不发出任何网络请求。
- 计时是同一台机器上的单次测量，只用于比较量级。计时类程序都在机器空闲时运行（启动前 load average < 1.2）。

## 语料

- 英文 `english_pool.txt`：`cat /usr/share/vim/vim91/doc/version*.txt <GOROOT>/src/testdata/Isaac.Newton-Opticks.txt`，5,352,455 字节，SHA-256 `9a153862ce17ea9e569ecc0ddf7fa5bbbf0954e83b8d666e7e7ba47c23e825b5`。任何自然语言英文文本都能复现 R1，关键是每篇 ≥30 KB、篇数 ≥4,000。
- 中文 `chinese_pool.txt`：`6410d27` 中以下 10 个文件按顺序拼接，196,527 字节，SHA-256 `10723b1b10602a7882faa23c2d0b4b814e88d1bbc35dd1e67eecaeda7e251935`：
  `docs/API.md docs/DEVELOPMENT_STATUS.md HANDOFF_START_HERE.md docs/planning/development_blueprint_v1.2.md docs/handoff/NEXT_AGENT.md docs/HTTP_REDIRECT_CACHE.md docs/DIRECT_SHARED_READS.md docs/SHARED_READS.md docs/SITE_CRAWLING.md docs/SITE_PUBLICATION.md`

## R1 long_doc_recall（P1-A）

```cpp
// Repro (6410d27): the substring-gram index fixes short documents, but long
// natural-language pages contain most common trigrams, so a phrase made of
// common words still matches far more than max_candidates documents and the
// oldest true match falls outside the newest-2000 window.
// Usage: long_doc_recall POOL.txt [docs=2100] [bytes=30000]
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
using namespace bzs;

static std::string slice(const std::string& pool, std::size_t at, std::size_t n) {
  while (at < pool.size() && !utf8_boundary(pool, at)) ++at;
  auto end = std::min(pool.size(), at + n);
  while (end > at && !utf8_boundary(pool, end)) --end;
  return pool.substr(at, end - at);
}

static long long count_substring_candidates(Store& store, const std::string& expr) {
  sqlite3_stmt* st = nullptr;
  sqlite3_prepare_v2(store.raw_handle(), "SELECT count(*) FROM substring_fts WHERE substring_fts MATCH ?", -1, &st, nullptr);
  sqlite3_bind_text(st, 1, expr.c_str(), -1, SQLITE_TRANSIENT);
  long long n = sqlite3_step(st) == SQLITE_ROW ? sqlite3_column_int64(st, 0) : -1;
  sqlite3_finalize(st);
  return n;
}

int main(int argc, char** argv) {
  if (argc < 2) { std::fprintf(stderr, "usage: %s POOL [docs] [bytes]\n", argv[0]); return 2; }
  std::ifstream in(argv[1], std::ios::binary);
  std::stringstream ss; ss << in.rdbuf();
  const std::string pool = ss.str();
  const int docs = argc > 2 ? std::stoi(argv[2]) : 2100;
  const std::size_t bytes = argc > 3 ? std::stoul(argv[3]) : 30000;
  std::mt19937_64 rng(861);
  std::uniform_int_distribution<std::size_t> pick(0, pool.size() - bytes - 1);
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  // Oldest document: the only one containing the phrase.
  store.ingest("alice", {"https://example.com/target",
                         slice(pool, pick(rng), bytes) + "\nThese notes compare machine learning toolkits.\n",
                         "text/plain", "Target"});
  for (int i = 0; i < docs; ++i) {
    now += 1000;
    auto text = slice(pool, pick(rng), bytes);
    if (text.find("achine learning") != std::string::npos) { --i; continue; }
    store.ingest("alice", {"https://example.com/doc/" + std::to_string(i), text, "text/plain", "Doc"});
  }
  now += 1000;
  for (const char* q : {"machine learning", "\"machine learning\""}) {
    const auto query = parse_query(Json::string(q));
    const auto sub = substring_candidates(query);
    const auto t0 = std::chrono::steady_clock::now();
    auto once = store.search_once("alice", query, 2000, 100);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    bool found = false;
    const auto hits = once.at("hits");
    for (std::size_t i = 0; i < hits.size(); ++i)
      if (hits.at(i).at("url").str() == "https://example.com/target") found = true;
    std::printf("%-20s retrieval=%-24s gram-filter matches=%lld  target found=%s  partial=%s  %lld ms\n", q,
                once.at("retrieval").str().c_str(), sub ? count_substring_candidates(store, *sub) : -1LL,
                found ? "yes" : "NO", once.at("partial").flag() ? "true" : "false", static_cast<long long>(ms));
  }
  return 0;
}
```

命令：`./new_long_doc_recall english_pool.txt 4000 30000`

```text
machine learning     retrieval=fts5_bm25_verified       gram-filter matches=2236  target found=yes  partial=false  44 ms
"machine learning"   retrieval=fts5_substring_verified  gram-filter matches=2505  target found=NO  partial=true  249 ms
```

第一行的"gram-filter matches"只是对照：不带引号时走 FTS 路径，gram 表达式只作为附加交集条件。

## R2 backpressure（P1-B）

```python
"""Repro (6410d27): searchd admits at most request_threads business calls and has
no wait queue; the MCP adapter does not retry 503. An agent that issues more
parallel tool calls than request threads gets hard failures even for cheap calls.
Usage: python backpressure.py SEARCHD POOL.txt [threads=8] [parallel=24]
Loopback only; no external network.
"""
import concurrent.futures as cf
import hashlib
import json
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

searchd, pool_path = sys.argv[1], sys.argv[2]
threads = sys.argv[3] if len(sys.argv) > 3 else "8"
parallel = int(sys.argv[4]) if len(sys.argv) > 4 else 24
pool = Path(pool_path).read_text(encoding="utf-8")
root = Path(tempfile.mkdtemp(prefix="bzs-bp-"))
token = secrets.token_hex(32)
(root / "auth.json").write_text(json.dumps({"tokens": [
    {"sha256": hashlib.sha256(token.encode()).hexdigest(), "subject": "alice", "admin": True}]}))
(root / "auth.json").chmod(0o600)
fixtures = Path(searchd).resolve().parents[1] / "tests" / "fixtures"  # <repo>/build-release/searchd
proc = subprocess.Popen([searchd, "--auth", str(root / "auth.json"), "--state", str(root / "state"),
                         "--fixtures", str(fixtures), "--port", "0", "--threads", threads, "--no-worker"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
ready = queue.Queue()
threading.Thread(target=lambda: ready.put(proc.stdout.readline()), daemon=True).start()
info = json.loads(ready.get(timeout=15))
port = info["port"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(op, params):
    body = json.dumps({"operation": op, "params": params}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v0/call", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as r:
        with r:
            return r.code, json.load(r)


try:
    print(f"searchd threads={info.get('threads')} request_threads={info.get('request_threads')}")
    for i in range(400):
        at = (i * 104729) % (len(pool) - 60000)
        status, _ = call("document.ingest", {"url": f"https://example.com/p/{i}", "content": pool[at:at + 60000],
                                             "content_type": "text/plain", "title": f"Doc {i}"})
        assert status == 200, status
    for label, op, params in [
        ("slow: search.once NOT-scan", "search.once", {"query": "-zzzzqqq", "limit": 10}),
        ("cheap: stats", "stats", {}),
    ]:
        start = time.monotonic()
        with cf.ThreadPoolExecutor(parallel) as ex:
            results = list(ex.map(lambda _: call(op, params), range(parallel)))
        codes = Counter(s if s == 200 else f"{s} {b['errors'][0]['code']}" for s, b in results)
        print(f"{label:28} parallel={parallel}: {dict(codes)}  ({time.monotonic() - start:.2f} s)")
    # Mixed: as many slow calls as request threads are in flight, then cheap calls arrive.
    slots = int(info.get("request_threads"))
    with cf.ThreadPoolExecutor(slots + 16) as ex:
        slow = [ex.submit(call, "search.once", {"query": "-zzzzqqq", "limit": 10}) for _ in range(slots)]
        time.sleep(0.05)
        cheap = [ex.submit(call, "stats", {}) for _ in range(16)]
        slow_codes = Counter(f.result()[0] for f in slow)
        cheap_codes = Counter(s if s == 200 else f"{s} {b['errors'][0]['code']}" for s, b in (f.result() for f in cheap))
    print(f"mixed: {slots} slow in flight + 16 stats: slow={dict(slow_codes)} stats={dict(cheap_codes)}")
finally:
    proc.terminate()
    proc.wait(timeout=10)
```

命令：`python -I backpressure.py <repo>/build-release/searchd english_pool.txt 8 24`，再把 8 换成 2 跑一次。

```text
=== backpressure threads=8
searchd threads=8 request_threads=8
slow: search.once NOT-scan   parallel=24: {200: 8, '503 E_REQUEST_BACKPRESSURE': 16}  (0.87 s)
cheap: stats                 parallel=24: {200: 24}  (0.02 s)
mixed: 8 slow in flight + 16 stats: slow={200: 8} stats={'503 E_REQUEST_BACKPRESSURE': 16}
=== backpressure threads=2
searchd threads=2 request_threads=2
slow: search.once NOT-scan   parallel=24: {200: 2, '503 E_REQUEST_BACKPRESSURE': 22}  (0.22 s)
cheap: stats                 parallel=24: {200: 23, '503 E_REQUEST_BACKPRESSURE': 1}  (0.02 s)
mixed: 2 slow in flight + 16 stats: slow={200: 2} stats={'503 E_REQUEST_BACKPRESSURE': 16}
```

## R3 identity_growth（P2-A）

```cpp
// Repro (6410d27): P1-1 no longer refuses work after 10,000 uses, but every
// frozen search and every job keeps a permanent row (jobs keep an up-to-8 KiB
// archive summary). Nothing ever deletes them, so the database grows without
// bound and outside the owner byte quota.
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <sqlite3.h>
#include <cstdio>
#include <string>
using namespace bzs;

static long long scalar(Store& store, const char* sql) {
  sqlite3_stmt* st = nullptr;
  sqlite3_prepare_v2(store.raw_handle(), sql, -1, &st, nullptr);
  long long n = sqlite3_step(st) == SQLITE_ROW ? sqlite3_column_int64(st, 0) : -1;
  sqlite3_finalize(st);
  return n;
}

int main() {
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  store.ingest("alice", {"https://example.com/a", "marker text", "text/plain", "Marker"});
  const auto query = parse_query(Json::string("marker"));
  int searches = 0;
  try {
    for (; searches < 12000; ++searches) {
      if (searches % 60 == 0) now += 3600001;  // stay under the 64 active-session cap
      store.search("alice", query, 2000, 200);
    }
    std::printf("12,000 frozen searches: OK (no lifetime 429)\n");
  } catch (const Error& e) {
    std::printf("frozen search #%d failed: %s\n", searches + 1, e.code.c_str());
  }
  int jobs = 0;
  try {
    for (; jobs < 12000; ++jobs) {
      store.job_create("bob", "fixture-a", "key-" + std::to_string(jobs), 1, 100);
      if (jobs % 500 == 499) {  // let the batch expire, settle it, then wait out retention
        now += 1000;
        (void)store.lease_next();               // marks deadline-expired jobs failed
        now += 8LL * 24 * 3600 * 1000;           // past the 7-day retention window
      }
    }
    std::printf("12,000 jobs: OK (no lifetime 429)\n");
  } catch (const Error& e) {
    std::printf("job #%d failed: %s (%s)\n", jobs + 1, e.code.c_str(), e.what());
  }
  std::printf("rows kept forever: search_stream_receipts=%lld, jobs=%lld (archived=%lld), archive_json bytes=%lld\n",
              scalar(store, "SELECT COUNT(*) FROM search_stream_receipts"),
              scalar(store, "SELECT COUNT(*) FROM jobs"),
              scalar(store, "SELECT COUNT(*) FROM jobs WHERE archived_at<>0"),
              scalar(store, "SELECT COALESCE(SUM(length(archive_json)),0) FROM jobs"));
  std::printf("owner byte quota sees: %s\n", store.stats("bob").at("content_bytes").dump().c_str());
  return 0;
}
```

命令：`./new_identity_growth`

```text
12,000 frozen searches: OK (no lifetime 429)
12,000 jobs: OK (no lifetime 429)
rows kept forever: search_stream_receipts=12000, jobs=12000 (archived=11500), archive_json bytes=4991000
owner byte quota sees: 0
```

## R4 gram_storage 与 gram_count（P2-B）

```cpp
// Measure (44253a7 vs 6410d27): on-disk size and ingest time per document after
// the codepoint-gram substring index was added. Same source, linked against
// either core library. Usage: gram_storage POOL DB_DIR [docs=300] [bytes=50000]
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>
using namespace bzs;

int main(int argc, char** argv) {
  if (argc < 3) return 2;
  std::ifstream in(argv[1], std::ios::binary);
  std::stringstream ss; ss << in.rdbuf();
  const std::string pool = ss.str();
  const std::filesystem::path dir = argv[2];
  const int docs = argc > 3 ? std::stoi(argv[3]) : 300;
  const std::size_t bytes = argc > 4 ? std::stoul(argv[4]) : 50000;
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  std::mt19937_64 rng(7);
  std::uniform_int_distribution<std::size_t> pick(0, pool.size() > bytes ? pool.size() - bytes - 1 : 0);
  std::int64_t now = 1800000000000, content = 0;
  const auto t0 = std::chrono::steady_clock::now();
  {
    Store store(dir / "metadata.sqlite", [&] { return now; });
    for (int i = 0; i < docs; ++i) {
      now += 1000;
      auto at = pick(rng);
      while (at < pool.size() && !utf8_boundary(pool, at)) ++at;
      auto end = std::min(pool.size(), at + bytes);
      while (end > at && !utf8_boundary(pool, end)) --end;
      auto text = pool.substr(at, end - at);
      content += static_cast<std::int64_t>(text.size());
      store.ingest("alice", {"https://example.com/doc/" + std::to_string(i), text, "text/plain", "Doc"});
    }
    sqlite3_exec(store.raw_handle(), "PRAGMA wal_checkpoint(TRUNCATE);", nullptr, nullptr, nullptr);
  }
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
  std::uintmax_t disk = 0;
  for (const auto& e : std::filesystem::directory_iterator(dir)) if (e.is_regular_file()) disk += e.file_size();
  std::printf("docs=%d text=%.1f MiB disk=%.1f MiB (%.2fx of text; raw copy is stored too) ingest=%lld ms (%.1f ms/doc)\n",
              docs, content / 1048576.0, disk / 1048576.0, static_cast<double>(disk) / static_cast<double>(content),
              static_cast<long long>(ms), static_cast<double>(ms) / docs);
  return 0;
}
```

命令：`./old_gram_storage chinese_pool.txt /tmp/g_old 300 50000`、`./new_gram_storage chinese_pool.txt /tmp/g_new 300 50000`，英文同理。

```text
=== old_gram_storage chinese 300 x 50000
docs=300 text=14.3 MiB disk=62.8 MiB (4.39x of text; raw copy is stored too) ingest=3927 ms (13.1 ms/doc)
=== new_gram_storage chinese 300 x 50000
docs=300 text=14.3 MiB disk=165.2 MiB (11.55x of text; raw copy is stored too) ingest=12231 ms (40.8 ms/doc)
=== old_gram_storage english 300 x 50000
docs=300 text=14.3 MiB disk=50.0 MiB (3.50x of text; raw copy is stored too) ingest=1999 ms (6.7 ms/doc)
=== new_gram_storage english 300 x 50000
docs=300 text=14.3 MiB disk=76.7 MiB (5.36x of text; raw copy is stored too) ingest=4352 ms (14.5 ms/doc)
```

不同 gram 数量（按 `indexed_grams` 的规则近似：只折叠 ASCII 大小写，一元 + 二元 + 三元合计，超过 65,536 即溢出）：

```python
"""Count distinct codepoint uni/bi/tri-grams per body (approximation of
indexed_grams: ASCII case folded, limit 65,536 distinct grams per field)."""
import sys

def distinct(text):
    pts = [c.lower() if c.isascii() else c for c in text]
    s1, s2, s3 = set(pts), set(zip(pts, pts[1:])), set(zip(pts, pts[1:], pts[2:]))
    return len(s1) + len(s2) + len(s3)

for path, sizes in [("english_pool.txt", [30_000, 100_000, 300_000, 1_000_000]), ("chinese_pool.txt", [30_000, 50_000, 100_000, 196_000])]:
    data = open(path, encoding="utf-8").read()
    for n in sizes:
        chunk = data[:n] if path.startswith("english") else data.encode()[:n].decode("utf-8", "ignore")
        d = distinct(chunk)
        print(f"{path:18} {len(chunk.encode())/1024:7.0f} KiB  distinct grams={d:7d}  {'OVERFLOW' if d > 65536 else 'indexed'}")
```

```text
english_pool.txt        29 KiB  distinct grams=   6274  indexed
english_pool.txt        98 KiB  distinct grams=  10864  indexed
english_pool.txt       293 KiB  distinct grams=  16851  indexed
english_pool.txt       977 KiB  distinct grams=  25260  indexed
chinese_pool.txt        29 KiB  distinct grams=  14079  indexed
chinese_pool.txt        49 KiB  distinct grams=  21747  indexed
chinese_pool.txt        98 KiB  distinct grams=  36024  indexed
chinese_pool.txt       191 KiB  distinct grams=  58952  indexed
```

"CJK 段不建 gram"能省多少（P2-B 修改方案第 1 点）：统计每篇 50 KB 中，窗口里不含 CJK 码点的 gram 占多少（CJK 范围与 `bzs::is_cjk` 相同）。

```python
"""Share of distinct grams whose window contains a CJK codepoint (same ranges as
bzs::is_cjk), for 50 KB chunks of a corpus."""
import sys

def is_cjk(c):
    o = ord(c)
    return (0x3040 <= o <= 0x30FF or 0x3100 <= o <= 0x312F or 0x31A0 <= o <= 0x31BF or 0x3400 <= o <= 0x4DBF
            or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F
            or 0xF900 <= o <= 0xFAFF or 0xFF66 <= o <= 0xFF9F or 0x20000 <= o <= 0x2FA1F or 0x30000 <= o <= 0x3134F)

def grams(pts):
    for w in (1, 2, 3):
        for i in range(len(pts) - w + 1):
            yield tuple(pts[i:i + w])

data = open(sys.argv[1], encoding="utf-8").read().encode()
for start in range(0, len(data) - 50_000, 50_000):
    text = data[start:start + 50_000].decode("utf-8", "ignore")
    pts = [c.lower() if c.isascii() else c for c in text]
    all_g = set(grams(pts))
    non_cjk = {g for g in all_g if not any(is_cjk(c) for c in g)}
    print(f"chunk@{start // 1024:4d}KiB distinct={len(all_g):6d} without-CJK-windows={len(non_cjk):6d} ({100 * len(non_cjk) / len(all_g):.0f}%)")
```

命令：`python -I gram_cjk_share.py chinese_pool.txt`

```text
chunk@   0KiB distinct= 21747 without-CJK-windows=  4224 (19%)
chunk@  48KiB distinct= 20468 without-CJK-windows=  4498 (22%)
chunk@  97KiB distinct= 22561 without-CJK-windows=  4457 (20%)
```

这份语料是中英混排的技术文档（大量代码标识符），纯中文文本的比例会更低。

## R5 robots_residual（P2-C）

```python
"""Repro (6410d27): unrelated agent groups that are common in real robots.txt
files still turn the whole site into deny_all for BzsBot."""
from bzs.worker.robots import parse_robots

base = "User-agent: *\nDisallow: /private/\n"
for agent in ["Googlebot/2.1", "AhrefsBot", "008", "Mediapartners-Google*", "Яндекс", "Baiduspider+", "*"]:
    body = f"User-agent: {agent}\nDisallow: /\n\n{base}".encode()
    r = parse_robots(body)
    print(f"{agent!r:26} -> state={r['state']:9} diagnostics={r['diagnostics']}")
```

命令：`PYTHONPATH=<repo>/python python robots_residual.py`

```text
'Googlebot/2.1'            -> state=rules     diagnostics=['ignored_unrelated_product_tokens']
'AhrefsBot'                -> state=rules     diagnostics=[]
'008'                      -> state=deny_all  diagnostics=['robots_invalid_product_token']
'Mediapartners-Google*'    -> state=deny_all  diagnostics=['robots_invalid_product_token']
'Яндекс'                   -> state=deny_all  diagnostics=['robots_invalid_product_token']
'Baiduspider+'             -> state=rules     diagnostics=['ignored_unrelated_product_tokens']
'*'                        -> state=rules     diagnostics=[]
```

## R6 common_word_cost（P3-A）

```cpp
// Measure (6410d27): an FTS-path query keeps its candidates in BM25 order, yet
// search_once copies and verifies every candidate (up to max_candidates) even
// when only `limit` hits are returned.
// Usage: common_word_cost POOL.txt [docs=2000] [bytes=30000]
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <chrono>
#include <cstdio>
#include <fstream>
#include <random>
#include <sstream>
using namespace bzs;

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  std::ifstream in(argv[1], std::ios::binary);
  std::stringstream ss; ss << in.rdbuf();
  const std::string pool = ss.str();
  const int docs = argc > 2 ? std::stoi(argv[2]) : 2000;
  const std::size_t bytes = argc > 3 ? std::stoul(argv[3]) : 30000;
  std::mt19937_64 rng(42);
  std::uniform_int_distribution<std::size_t> pick(0, pool.size() - bytes - 1);
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  for (int i = 0; i < docs; ++i) {
    now += 1000;
    auto at = pick(rng);
    while (at < pool.size() && !utf8_boundary(pool, at)) ++at;
    auto end = std::min(pool.size(), at + bytes);
    while (end > at && !utf8_boundary(pool, end)) --end;
    store.ingest("alice", {"https://example.com/doc/" + std::to_string(i), pool.substr(at, end - at), "text/plain", "Doc"});
  }
  for (const char* q : {"the", "light", "the light"}) {
    for (int limit : {10, 100}) {
      const auto t0 = std::chrono::steady_clock::now();
      auto r = store.search_once("alice", parse_query(Json::string(q)), 2000, limit);
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
      std::printf("query=%-10s limit=%-3d retrieval=%s scanned=%lld returned=%lld partial=%s %lld ms\n", q, limit,
                  r.at("retrieval").str().c_str(), static_cast<long long>(r.at("scanned_candidates").num()),
                  static_cast<long long>(r.at("returned_count").num()), r.at("partial").flag() ? "true" : "false",
                  static_cast<long long>(ms));
    }
  }
  return 0;
}
```

命令：`./new_common_word_cost english_pool.txt 2000 30000`

```text
query=the        limit=10  retrieval=fts5_bm25_verified scanned=2000 returned=10 partial=true 338 ms
query=the        limit=100 retrieval=fts5_bm25_verified scanned=2000 returned=100 partial=true 342 ms
query=light      limit=10  retrieval=fts5_bm25_verified scanned=245 returned=10 partial=true 62 ms
query=light      limit=100 retrieval=fts5_bm25_verified scanned=245 returned=100 partial=true 76 ms
query=the light  limit=10  retrieval=fts5_bm25_verified scanned=245 returned=10 partial=true 66 ms
query=the light  limit=100 retrieval=fts5_bm25_verified scanned=245 returned=100 partial=true 99 ms
```

## R7 scan_blocking_not（P2-5 核对）

上轮 scan_blocking 的 NOT 变体：同样的数据、owner、候选上限和并发探测方式，只把查询换成只含 NOT 的形式，强制走有界扫描。

```cpp
// Repro: a fallback-scan query re-lowercases every candidate body once per query leaf
// while holding the single Store mutex, so one search stalls every other Store call.
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
using namespace bzs;
using clk = std::chrono::steady_clock;
static long long ms_since(clk::time_point t) {
  return std::chrono::duration_cast<std::chrono::milliseconds>(clk::now() - t).count();
}

int main(int argc, char** argv) {
  const int docs = argc > 1 ? std::stoi(argv[1]) : 2000;
  const std::size_t bytes = argc > 2 ? std::stoul(argv[2]) : 200 * 1024;
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  std::string body;
  while (body.size() < bytes) body += "lorem ipsum dolor sit amet consectetur adipiscing elit ";
  for (int i = 0; i < docs; ++i)
    store.ingest("alice", {"https://example.com/" + std::to_string(i), body + std::to_string(i), "text/plain", "Doc"});
  std::printf("%d docs x %zu KiB\n", docs, bytes / 1024);
  for (const char* q : {"-\"alpha beta\"", "-\"alpha beta\" -\"gamma delta\" -\"epsilon zeta\" -\"eta theta\""}) {
    std::atomic<bool> done{false};
    long long worst = 0;
    std::thread probe([&] {
      while (!done) {
        const auto t = clk::now();
        (void)store.stats("bob");  // unrelated owner, trivial query
        worst = std::max(worst, ms_since(t));
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
      }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const auto t = clk::now();
    auto s = store.search("alice", parse_query(Json::string(q)), 2000, 200);
    const auto search_ms = ms_since(t);
    done = true;
    probe.join();
    std::printf("query %-75s search=%lld ms, worst concurrent stats()=%lld ms, retrieval=%s\n", q, search_ms, worst,
                s.at("retrieval").str().c_str());
    now += 3600001;
  }
  return 0;
}
```

命令：`./old_scan_blocking_not 2000 204800`、`./new_scan_blocking_not 2000 204800`

```text
=== old_scan_blocking_not 2000 x 200KiB
2000 docs x 200 KiB
query -"alpha beta"                                                               search=2065 ms, worst concurrent stats()=2064 ms, retrieval=bounded_scan_recent
query -"alpha beta" -"gamma delta" -"epsilon zeta" -"eta theta"                   search=3091 ms, worst concurrent stats()=3091 ms, retrieval=bounded_scan_recent
=== new_scan_blocking_not 2000 x 200KiB
2000 docs x 200 KiB
query -"alpha beta"                                                               search=623 ms, worst concurrent stats()=198 ms, retrieval=bounded_scan_recent
query -"alpha beta" -"gamma delta" -"epsilon zeta" -"eta theta"                   search=1136 ms, worst concurrent stats()=155 ms, retrieval=bounded_scan_recent
```

## R8 上轮复现程序重跑（P1-2、P1-3、P2-1、S1 来源）

源码未改，在 `review/claude-2026-10-24` 分支的 `docs/reviews/claude-2026-10-24/`，本轮重新链接 `6410d27` 的核心库。上轮的 `permanent_caps` 依赖"一万次后失败"才会退出，P1-1 修好后它不再结束，本轮改用 R3。

```text
=== phrase_recall（P1-3：2,101 篇短文档，目标在最旧一篇）
machine learning             retrieval=fts5_bm25_verified   hits=1 partial=false (1 ms)
"machine learning"           retrieval=fts5_substring_verified hits=1 partial=false (1 ms)
c++                          retrieval=fts5_substring_verified hits=1 partial=false (1 ms)
node.js                      retrieval=fts5_substring_verified hits=1 partial=false (1 ms)
café                        retrieval=fts5_substring_verified hits=1 partial=false (1 ms)
Москва                 retrieval=fts5_substring_verified hits=1 partial=false (1 ms)
=== hourly_session_cap（冻结 search API；MCP 已改用 search.once）
search #65 after 65 s refused: E_QUOTA (Too many active search sessions; wait for expiry, HTTP 429)
=== vary_cache（P2-1）
Vary=(none)           fresh=true reason=fresh
Vary=Accept-Encoding  fresh=true reason=fresh
=== default_sources（S1 第 1 条）
sources=17 enabled=0
web.search category=web -> E_SCHEMA: No enabled sources for category: web
web.search category=all -> E_SCHEMA: No enabled sources for category: all
```

## R9 构建与测试摘要（`6410d27`）

```text
Debug    : 0 warnings; CTest 28/28 passed in 266.6 s; python_worker: 1332 passed
Release  : 0 warnings (bzs_core, searchd)
ASan     : 0 warnings; CTest 26/28 —— sessions Timeout 60.13 s, direct_shared Timeout 90.13 s（均在重型用例中途被杀，此前用例全部 PASS）
           空闲时去掉超时重跑：bzs_session_tests rc=0 80 s  "23 tests, 298 assertions, 0 failures"
                               bzs_direct_shared_tests rc=0 89 s  "34 direct shared tests, 10259 assertions, 0 failures"
           ASan/UBSan/LSan 报告：0
TSan     : 15 个原生套件 rc=0；http_cli、http_revocation、e2e_web、e2e_crawl、e2e_session_recovery、e2e_revalidation 6/6 passed；TSan 报告文件 0 个
GitHub   : 6410d27 checks —— linux success, windows success
```
