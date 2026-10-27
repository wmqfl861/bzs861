#pragma once
#include "bzs/json.hpp"
#include <filesystem>
namespace bzs {
  // Local operator tools only: no Store construction, migration, publication
  // sweep, network, restore import or quarantine clearance.
  Json inspect_storage(const std::filesystem::path& file);
  Json convert_storage_incremental(const std::filesystem::path& file, int timeout_ms=120000);
}
