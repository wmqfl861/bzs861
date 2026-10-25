#pragma once
#include "bzs/json.hpp"
#include <string>
namespace bzs {
  // Deliberately narrow HTTP validator contract, shared by read and robots.
  // Only Accept-Encoding Vary is supported, bound by the exact request profile.
  Json validate_http_cache(const Json& record,const std::string& url);
  Json http_conditional(const Json& cache,const std::string& binding);
  Json merge_http_cache(const Json& base,const Json& update);
  // Independent of validators: a page without ETag can still forbid caching.
  // Worker transports bounded raw fields; the core decides reuse and storage.
  Json validate_http_freshness(const Json& record,const std::string& url);
  Json merge_http_freshness(const Json& base,const Json& update);
  Json http_freshness(const Json& record,std::int64_t now,std::int64_t max_age_s);
  // Optional v1 proof for a complete, bounded HTTP-only redirect chain.
  Json validate_http_redirect_chain(const Json& record,const std::string& url,
                                    const std::string& final_url,const Json& redirects);
  Json validate_http_redirect_receipt(const Json& response,const std::string& url,bool not_modified=false);
  bool http_redirect_chain_cacheable(const Json& record);
  Json http_redirect_conditional(const Json& record,const Json& conditional);
  Json http_read_freshness(const Json& meta,const std::string& url,std::int64_t now,std::int64_t max_age_s);
  bool http_redirect_storage_allowed(const Json& response,const std::string& url,std::int64_t now);
  bool http_response_restricts_cache(const Json& response,std::int64_t now,const std::string& url="");
}
