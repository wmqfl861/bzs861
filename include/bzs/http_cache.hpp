#pragma once
#include "bzs/json.hpp"
#include <string>
namespace bzs {
  // Deliberately narrow HTTP validator contract, shared by read and robots.
  // Vary and responses requiring a broader cache implementation are excluded.
  Json validate_http_cache(const Json& record,const std::string& url);
  Json http_conditional(const Json& cache,const std::string& binding);
  Json merge_http_cache(const Json& base,const Json& update);
  // Independent of validators: a page without ETag can still forbid caching.
  // Worker transports bounded raw fields; the core decides reuse and storage.
  Json validate_http_freshness(const Json& record,const std::string& url);
  Json merge_http_freshness(const Json& base,const Json& update);
  Json http_freshness(const Json& record,std::int64_t now,std::int64_t max_age_s);
  bool http_response_restricts_cache(const Json& response,std::int64_t now);
}
