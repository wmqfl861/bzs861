#pragma once
#include "bzs/json.hpp"
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

namespace bzs {
  // Map execution identities preserve query order and scheme/host/port. This
  // deliberately conservative path grammar is shared with the worker before
  // either URL parser may remove dot segments or decode path separators.
  std::string map_url(std::string_view input);
  std::string map_origin(std::string_view input);
  std::string map_path(std::string_view input);
  std::string map_scope_path(std::string_view input);
  bool path_within(std::string_view path,std::string_view prefix);

  struct SiteSpec {
    std::string id,origin;
    std::vector<std::string> path_prefixes;
    Json review;
    bool enabled=false;
    std::int64_t min_interval_ms=0,request_quota=0,quota_window_seconds=86400;
    void require_review(std::int64_t now) const;
    bool reviewed(std::int64_t now) const;
    bool allows(std::string_view url,std::string_view scope_path={}) const;
    void require_scope(std::string_view url,std::string_view scope_path={}) const;
  };
  // Trusted deployment input, independent from search-source manifests. No API
  // call can create or enable a site. Duplicate origins cannot multiply quota.
  class SiteRegistry {
    std::vector<SiteSpec> specs_;
    std::string digest_;
  public:
    SiteRegistry();
    explicit SiteRegistry(const Json& manifest);
    static SiteRegistry load(const std::filesystem::path& file);
    const SiteSpec* find(const std::string& id) const;
    const std::vector<SiteSpec>& all() const { return specs_; }
    const std::string& manifest_digest() const { return digest_; }
    Json describe(std::int64_t now) const;
  };
}
