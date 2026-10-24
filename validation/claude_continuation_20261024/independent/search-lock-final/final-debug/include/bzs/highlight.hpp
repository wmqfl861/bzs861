#pragma once
#include "bzs/json.hpp"
#include "bzs/query.hpp"
#include <string_view>
namespace bzs {
  // Query-relevant passages from immutable text (EVID-01 basic path, no model).
  // Returns {"snippet": "...", "highlights": [{"start_byte","end_byte","text","terms"}], "match_count"}.
  // Every highlight is an exact byte slice of `text`, so it can be turned into evidence.
  Json highlight(std::string_view text, const QueryNode& query, std::size_t max_passages=3, std::size_t max_bytes=240);
}
