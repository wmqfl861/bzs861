#pragma once
#include "bzs/json.hpp"
namespace bzs {
  Json validate_publication(const Json& record);
  Json meet_publication(const Json& policy,const Json& ceiling);
  bool publication_covers(const Json& policy,const Json& observation);
  bool restrictive_publication(const Json& record);
}
