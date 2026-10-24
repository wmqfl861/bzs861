// Repro: lifetime ("permanent identity") caps permanently disable search and jobs.
// Build: g++ -std=c++20 -I include permanent_caps.cpp build/libbzs_core.a -lsqlite3 -lcrypto -ljson-c -lxml2 -pthread
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <cstdio>
#include <string>
using namespace bzs;

int main() {
  std::int64_t now = 1800000000000;  // fixed, injectable clock
  Store store(":memory:", [&] { return now; });
  store.ingest("alice", {"https://example.com/a", "marker text for the local index", "text/plain", "Marker"});
  const auto query = parse_query(Json::string("marker"));

  // 1) Local search: every call writes a permanent search_stream_receipts row.
  //    Expired sessions are reclaimed, receipts never are.
  int searches = 0;
  try {
    for (;; ++searches) {
      if (searches % 60 == 0) now += 3600001;  // let every earlier session expire (64 active cap)
      store.search("alice", query, 2000, 200);
    }
  } catch (const Error& e) {
    std::printf("search #%d failed: %s (%s, HTTP %d)\n", searches + 1, e.code.c_str(), e.what(), e.http_status);
  }
  now += 30LL * 24 * 3600 * 1000;  // a month later: still refused
  try { store.search("alice", query, 2000, 200); std::puts("after 30 days: search OK"); }
  catch (const Error& e) { std::printf("after 30 days: search still fails: %s\n", e.code.c_str()); }

  // 2) Jobs: every job row (any kind) counts toward a lifetime per-owner cap.
  int jobs = 0;
  try {
    for (;; ++jobs) store.job_create("bob", "fixture-a", "key-" + std::to_string(jobs), 1, 1000);
  } catch (const Error& e) {
    std::printf("job #%d failed: %s (%s, HTTP %d)\n", jobs + 1, e.code.c_str(), e.what(), e.http_status);
  }
  now += 30LL * 24 * 3600 * 1000;
  auto compact = store.compact_jobs("bob", 64);
  std::printf("compact_jobs after 30 days: compacted=%lld retained_job_keys=%lld\n",
              static_cast<long long>(compact.at("compacted_jobs").num()),
              static_cast<long long>(compact.at("retained_job_keys").num()));
  try { store.job_create("bob", "fixture-a", "fresh-key", 1, 1000); std::puts("new job OK"); }
  catch (const Error& e) { std::printf("new job still fails: %s\n", e.code.c_str()); }
  return 0;
}
