// Repro: the 65th local search inside one hour is refused (search_local MCP tool path).
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <cstdio>
using namespace bzs;

int main() {
  std::int64_t now = 1800000000000;
  Store store(":memory:", [&] { return now; });
  store.ingest("alice", {"https://example.com/a", "marker text", "text/plain", "Marker"});
  const auto query = parse_query(Json::string("marker"));
  int n = 0;
  try {
    for (;; ++n) { now += 1000; store.search("alice", query, 2000, 200); }  // one search per second
  } catch (const Error& e) {
    std::printf("search #%d after %d s refused: %s (%s, HTTP %d)\n", n + 1, n + 1, e.code.c_str(), e.what(), e.http_status);
  }
  return 0;
}
