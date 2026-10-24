// Repro: queries that cannot be expressed as FTS candidates (quoted phrases, symbols,
// non-ASCII non-CJK words, URL field) fall back to scanning only the newest
// max_candidates (<=2000) documents, so older matching documents are never found.
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <chrono>
#include <cstdio>
#include <string>
using namespace bzs;

static void run(Store& store, const char* q) {
  const auto t0 = std::chrono::steady_clock::now();
  auto s = store.search("alice", parse_query(Json::string(q)), 2000, 200);
  auto page = store.page("alice", s.at("session_id").str(), "", 20);
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
  std::printf("%-28s retrieval=%-20s hits=%zu partial=%s (%lld ms)\n", q, s.at("retrieval").str().c_str(),
              page.at("hits").size(), s.at("partial").flag() ? "true" : "false", static_cast<long long>(ms));
}

int main() {
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  // Oldest document holds the answer.
  store.ingest("alice", {"https://example.com/target",
                         "Notes on machine learning, C++ templates, the café in Москва, and node.js.",
                         "text/plain", "Target"});
  for (int i = 0; i < 2100; ++i) {
    now += 1000;
    store.ingest("alice", {"https://example.com/filler/" + std::to_string(i),
                           "filler document number " + std::to_string(i) + " about nothing in particular",
                           "text/plain", "Filler"});
  }
  now += 1000;
  run(store, "machine learning");    // two ASCII terms -> FTS
  run(store, "\"machine learning\"");  // quoted phrase -> bounded recent scan
  run(store, "c++");                 // literal
  run(store, "node.js");             // literal
  run(store, "café");                // non-ASCII, non-CJK
  run(store, "Москва");              // Cyrillic
  return 0;
}
