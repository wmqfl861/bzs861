#pragma once
#include "bzs/json.hpp"
#include <string>
namespace bzs {
  // Deliberately narrow HTTP validator contract, shared by read and robots.
  // Vary and responses requiring a broader cache implementation are excluded.
  Json validate_http_cache(const Json& record,const std::string& url);
  Json http_conditional(const Json& cache,const std::string& binding);
  Json merge_http_cache(const Json& base,const Json& update);
}
