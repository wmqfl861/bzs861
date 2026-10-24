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
  for (const char* q : {"\"alpha beta\"", "\"alpha beta\" OR \"gamma delta\" OR \"epsilon zeta\" OR \"eta theta\""}) {
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
