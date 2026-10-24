// Repro: `Vary: Accept-Encoding` (sent by most gzip-enabled servers) makes every
// stored page permanently "not fresh", so read_url(cache_first) always refetches.
#include "bzs/http_cache.hpp"
#include "bzs/util.hpp"
#include <cstdio>
using namespace bzs;

static Json record(const char* vary) {
  auto r = Json::object();
  r.set("version", "bzs-http-freshness-1");
  r.set("url", "https://example.com/article");
  r.set("request_profile", std::string(64, 'a'));
  r.set("cache_control", "max-age=3600");
  r.set("vary", vary);
  r.set("date", "");
  r.set("age", "");
  r.set("expires", "");
  r.set("pragma", "");
  r.set("received_at_ms", std::int64_t{1800000000000});
  r.set("response_delay_ms", std::int64_t{0});
  r.set("invalid", false);
  return r;
}

int main() {
  for (const char* vary : {"", "Accept-Encoding"}) {
    auto out = http_freshness(record(vary), 1800000000000 + 60000, 7 * 86400);  // one minute later
    std::printf("Vary=%-16s fresh=%s reason=%s\n", *vary ? vary : "(none)", out.at("fresh").flag() ? "true" : "false",
                out.at("reason").str().c_str());
  }
  return 0;
}
