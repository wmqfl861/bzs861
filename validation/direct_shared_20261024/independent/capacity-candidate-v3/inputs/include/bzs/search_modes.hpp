#pragma once
#include "bzs/json.hpp"
#include <cstdint>
#include <string>

namespace bzs {
struct SearchMode {
  std::string name="legacy";
  bool explicit_mode=false,explicit_source_limit=false;
  std::int64_t max_sources=20,limit=10,per_source=10,timeout_ms=8000,deadline_ms=120000;
  Json budget(std::int64_t selected_sources)const;
};
SearchMode search_mode(const Json& request,bool persistent=false);
Json search_modes();
}
