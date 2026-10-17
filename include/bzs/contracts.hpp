#pragma once
#include "bzs/json.hpp"
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace bzs {
  // Trusted deployment ceilings. These are configuration limits, not measured
  // capacity or permission to run a model/source. Never populate them from an
  // API request. Explicit values exceeding a ceiling are rejected; generated
  // model defaults are bounded by the ceiling.
  struct ContractLimits {
    std::size_t max_request_bytes=65536;
    std::size_t max_ast_depth=16;
    std::size_t max_ast_nodes=512;
    std::int64_t model_timeout_ms=86400000;
    std::int64_t model_concurrency=1024;
    std::int64_t model_output_tokens=10000000;
    std::int64_t model_retry_limit=10;
    std::int64_t source_concurrency=100;
    std::int64_t search_source_requests=1000;
    std::int64_t search_reads=10000;
    std::int64_t search_browser_seconds=86400;
    std::int64_t search_model_calls=1000;
    std::int64_t search_timeout_ms=86400000;
    std::int64_t search_bytes=1073741824;
    std::int64_t search_tokens_estimated=10000000;
  };

  // Fixed v1.1 contracts: model-config, source-manifest, search-request.
  // Returns an independent normalized copy; does not mutate input, register a
  // configuration, resolve credentials, access files, or make network calls.
  // The clock and ceilings must come from the trusted server, never a caller.
  // E_SCHEMA: structure; E_CONTRACT: semantics; E_CONTRACT_LIMIT: resource limit.
  // Cross-configuration identities, review authenticity, DNS/egress, actual
  // model capabilities and authorization still require execution-time checks.
  Json validate_contract(std::string_view kind, const Json& input,
                         std::int64_t validation_now_ms,
                         const ContractLimits& limits = {});
}
