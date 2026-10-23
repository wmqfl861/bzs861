#pragma once
#include "bzs/json.hpp"
#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include <chrono>
#include <filesystem>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>
namespace bzs {
  // Worker RPC port. searchd implements it over loopback HTTP; tests use a fake.
  struct WorkerPort {
    virtual ~WorkerPort()=default;
    virtual Json call(const std::string& method, const Json& params, int timeout_ms)=0;
  };
  // Key used to merge the same page returned by several sources (not used for fetching).
  std::string dedup_key(std::string_view url);
  // Policy pre-check for user-supplied URLs; the worker re-checks resolved addresses.
  void check_public_url(std::string_view url);
  // IRI -> URI for reads: percent-encodes non-ASCII bytes and spaces after the host; a non-ASCII
  // host must already be punycode (the MCP adapter converts it), otherwise E_URL with a hint.
  std::string normalize_iri(std::string_view url);
  // Ordinary HTTP wire identity for stored publication provenance, without
  // broadening the separate automatic map/crawl URL admission rules.
  std::string publication_url_identity(std::string_view url);
  struct SourceSpec {
    std::string id, provider, family, capability, kind, status;
    std::string budget_key;
    Json contract;
    bool enabled=false, by_default=false;
    std::int64_t weight=100, min_interval_ms=0, daily_cap=0;
    std::int64_t cooldown_blocked_ms=30*60*1000, cooldown_rate_ms=10*60*1000, cooldown_error_ms=5*60*1000;
    void require_review(std::int64_t now) const;
    bool reviewed(std::int64_t now) const;
  };
  class SourceRegistry {
    std::vector<SourceSpec> specs_;
    std::string manifest_digest_;
    bool configured_=false;
    public: SourceRegistry()=default;
    explicit SourceRegistry(const Json& manifest);
    static SourceRegistry load(const std::filesystem::path& file);
    const std::vector<SourceSpec>& all() const {
      return specs_;
    }
    const SourceSpec* find(const std::string& id) const;
    const std::string& manifest_digest() const { return manifest_digest_; }
    bool configured() const { return configured_; }
    // Stable reviewed-source publication identity; excludes ranking-only settings.
    Json publication_manifest() const;
  };
  struct SourceHit {
    std::string url, title, snippet;
    std::int64_t rank=0;
  };
  struct SourceHits {
    std::string source_id;
    std::vector<SourceHit> hits;
  };
  // Reciprocal-rank fusion over index families: one family counts once, however many adapters returned it.
  Json fuse(const std::vector<SourceHits>& results, const SourceRegistry& registry, std::size_t limit, std::int64_t k=60, bool retain_publication_urls=false);
  class WebService {
    Store& store_;
    WorkerPort& worker_;
    SourceRegistry registry_;
    SiteRegistry sites_;
    std::int64_t site_publication_generation_=0;
    // Short-lived cache of identical searches (ADR-0003): spares scraped engines repeated agent queries.
    struct CachedSearch {
      std::int64_t stored_at=0, ttl_ms=0, source_generation=0;
      std::string body;
    };
    std::mutex cache_mutex_;
    std::map<std::string, CachedSearch> cache_;
    // Last verified configuration within this core process. A temporarily
    // unavailable worker cannot change the representation selected by callers.
    std::string last_read_profile_;
    std::optional<Json> cache_get(const std::string& key);
    void cache_put(const std::string& key, const Json& value, bool partial,std::int64_t source_generation);
    struct ReadArtifact {
      SourceDocument document;
      ParsedDocument parsed;
      Json meta;
    };
    Json decode_publication(const Json& policy,const std::string& url,const std::string& final_url,const Json& redirects);
    std::optional<Json> decode_observation(const std::string& url,const Json& response);
    Json decode_redirect_observations(const std::string& url,const Json& response);
    ReadArtifact decode_read(const std::string& url, const Json& response,bool allow_empty=false);
    ReadArtifact decode_not_modified(const std::string& url,const Json& response,const std::optional<RevalidationBase>& base);
    Json window(const std::string& owner, const Json& snapshot, std::int64_t start, std::int64_t max_bytes, bool from_cache, std::int64_t fetched_at);
    public: WebService(Store& store, WorkerPort& worker, SourceRegistry registry,SiteRegistry sites={});
    Json search(const std::string& owner, const Json& args, bool retain_publication_urls=false,
                std::optional<std::chrono::steady_clock::time_point> internal_deadline=std::nullopt);
    Json submit_search(const std::string& owner,const Json& args);
    std::optional<SearchLease> lease_search();
    void execute_search(const SearchLease& lease);
    Json read(const std::string& owner, const Json& args);
    Json submit_reads(const std::string& owner, const Json& args);
    // The worker only returns an artifact. Store validates the durable lease and
    // publishes the artifact, receipt and reconciled usage in one transaction.
    void execute_read(const ReadLease& lease);
    void cancel_read_attempt(const std::string& attempt_id) noexcept;
    Json submit_map(const std::string& owner,const Json& args);
    Json submit_crawl(const std::string& owner,const Json& args);
    Json sites();
    std::optional<MapLease> lease_map();
    void execute_map(const MapLease& lease);
    Json sources();
    std::size_t enabled_sources() const;
  };
}
