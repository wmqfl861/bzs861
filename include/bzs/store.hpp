#pragma once
#include "bzs/html.hpp"
#include "bzs/query.hpp"
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <vector>
struct sqlite3;
struct sqlite3_context;
struct sqlite3_value;
namespace bzs {
  class SiteRegistry;
  struct Lease {
    std::string id, owner, fixture_id;
    std::int64_t epoch=0;
  };
  struct SourceDocument {
    std::string url, raw, content_type, title;
  };
  struct StoreOptions {
    std::int64_t max_snapshots_per_owner=200000;
    std::int64_t max_bytes_per_owner=8LL*1024*1024*1024;
    // Older revisions beyond this count are pruned unless evidence or a live session pins them.
    std::int64_t max_revisions_per_document=5;
  };
  struct ReadManyOptions {
    std::int64_t timeout_ms=600000, per_page_timeout_ms=20000;
    std::int64_t max_requests=0, max_bytes=0, max_attempts=1;
  };
  struct ReadLease {
    std::string job_id, owner, url, attempt_id;
    std::int64_t epoch=0, input_index=0, deletion_generation=0;
    std::int64_t request_grant=0, byte_grant=0, timeout_ms=0, deadline_ms=0;
  };
  struct MapLease {
    std::string job_id,owner,site_id,manifest_sha256,origin,url,logical_url,kind,attempt_id;
    std::int64_t epoch=0,position=0,resource_position=0,depth=0,redirect_hop=0,deletion_generation=0;
    std::int64_t request_grant=1,byte_grant=0,timeout_ms=0,deadline_ms=0;
    Json robots_record;
  };
  class Store {
    sqlite3* db_=nullptr;
    std::filesystem::path path_;
    std::function<std::int64_t()> clock_;
    StoreOptions options_;
    mutable std::mutex mutex_;
    struct SitePublicationRule {
      std::string origin,path;
      int mode=0;
      std::int64_t snippet_generation=0,index_generation=0;
    };
    std::vector<SitePublicationRule> site_publication_rules_;
    std::shared_ptr<SiteRegistry> site_publication_registry_;
    std::int64_t site_publication_cached_generation_=-1,site_publication_bound_generation_=0;
    std::int64_t site_publication_manifest_generation_=0;
    bool site_publication_legacy_withdrawn_=false;
    void migrate();
    static void site_publication_sql(sqlite3_context*,int,sqlite3_value**);
    void refresh_site_publication_locked(bool sweep=true);
    void sweep_site_publication_locked();
    Json site_bindings_locked(const std::string& url,const Json& meta);
    int site_publication_mode_locked(const std::string& source_kind,const std::string& bindings,
                                    std::int64_t generation,const std::string& url,const std::string& meta,
                                    const std::string& frozen_representation="");
    void observe_policy_locked(const std::string& owner,const std::string& url,const std::string& final_url,const Json& policy);
    void publish_policy_locked(const std::string& owner,const std::string& url,const std::string& snapshot_id,const Json& policy);
    Json alias_fences_locked(const std::string& owner,const std::string& logical,const std::string& target,std::int64_t created_sequence);
    bool alias_fences_live_locked(const Json& fences);
    std::int64_t epoch_locked(const std::string& owner);
    std::int64_t delete_sequence_locked();
    std::int64_t deletion_generation_locked(const std::string& owner,const std::string& url);
    Json snapshot_locked(const std::string& owner, const std::string& snapshot);
    Json ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed, const std::string& meta_json, const std::string& source_kind);
    Json ingest_web_locked(const std::string& owner, const SourceDocument& input, ParsedDocument parsed,
                           const Json& meta, std::int64_t expected_deletion_generation,bool allow_empty=false);
    Json job_locked(const std::string& owner, const std::string& id);
    Json read_job_locked(const std::string& owner, const std::string& id);
    Json control_read_locked(const std::string& owner, const std::string& id, const std::string& action);
    void settle_read_jobs_locked();
    void settle_read_job_locked(const std::string& id);
    bool owns_read_lease_locked(const ReadLease& lease);
    bool live_read_lease_locked(const ReadLease& lease);
    void reconcile_read_usage_locked(const ReadLease& lease, std::int64_t requests, std::int64_t bytes);
    Json frontier_submit(const std::string& owner,const Json& args,const SiteRegistry& sites,bool crawl);
    Json crawl_results_locked(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit);
    bool crawl_edge_locked(const std::string& id,const std::string& owner,const Json& candidate,bool followed,const std::string& reason);
    Json map_job_locked(const std::string& owner,const std::string& id);
    Json map_results_locked(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit);
    Json control_map_locked(const std::string& owner,const std::string& id,const std::string& action);
    void settle_map_jobs_locked();
    void settle_map_job_locked(const std::string& id);
    bool owns_map_lease_locked(const MapLease& lease);
    bool live_map_lease_locked(const MapLease& lease);
    void reconcile_map_usage_locked(const MapLease& lease,std::int64_t requests,std::int64_t bytes);
    void map_partial_locked(const std::string& id,const std::string& reason);
    bool map_candidate_locked(const std::string& id,const std::string& owner,const Json& candidate);
    bool map_resource_locked(const std::string& id,const std::string& owner,const std::string& url,
                             const std::string& kind,std::int64_t depth,const std::string& parent="",const std::string& source="seed");
    bool live_lease_locked(const Lease& lease);
    void settle_interruptions_locked();
    void usage_add_locked(const std::string& owner, std::int64_t bytes, std::int64_t snapshots);
    void prune_locked(const std::string& document_id, const std::string& keep_snapshot, std::int64_t newest_revision, const std::string& owner);
    public: explicit Store(const std::filesystem::path& path, std::function<std::int64_t()> clock= {
    }, StoreOptions options= {
    });
    ~Store();
    Store(const Store&)=delete;
    Store& operator=(const Store&)=delete;
    Json ingest(const std::string& owner, const SourceDocument& input);
    // Explicit trusted configuration activation. An empty manifest retires
    // previously registered scopes; omitting activation keeps durable policy.
    // A Store opened before another activation cannot overwrite its manifest.
    std::int64_t activate_site_publication(const SiteRegistry& sites);
    std::int64_t site_publication_generation() const;
    Json publication_fence(const std::string& owner,const std::string& url);
    void observe_web_publication(const std::string& owner,const std::string& url,const std::string& final_url,const Json& policy,const Json& fence);
    // Capture before a worker read. Every deletion advances this persistent
    // value, including when an explicit later import revives the document.
    std::int64_t deletion_generation(const std::string& owner,const std::string& url);
    // Commit extracted web content only if no deletion intervened. The fence
    // comparison and snapshot/index writes share one transaction.
    Json ingest_web(const std::string& owner, const SourceDocument& input, ParsedDocument parsed, const Json& meta,
                    std::int64_t expected_deletion_generation);
    // Current snapshot summary for (owner,url), if any; used for read caching.
    std::optional<Json> cached(const std::string& owner, const std::string& url);
    Json snapshot(const std::string& owner, const std::string& snapshot_id);
    // A UTF-8-safe window of the canonical text (read_range).
    Json range(const std::string& owner, const std::string& snapshot_id, std::int64_t start_byte, std::int64_t max_bytes);
    Json erase(const std::string& owner, const std::string& document_id);
    Json search(const std::string& owner, const Query& query, std::int64_t max_candidates=2000, std::int64_t limit=200);
    Json page(const std::string& owner, const std::string& session_id, const std::string& cursor, std::int64_t page_size=20);
    Json find(const std::string& owner, const std::string& snapshot_id, const std::string& needle, std::int64_t limit=16);
    Json evidence(const std::string& owner, const std::string& snapshot_id, std::int64_t start, std::int64_t end);
    Json evidence_get(const std::string& owner, const std::string& evidence_id);
    Json stats(const std::string& owner);
    Json job_create(const std::string& owner, const std::string& fixture_id, const std::string& key, std::int64_t max_attempts=3, std::int64_t timeout_ms=60000);
    Json job_get(const std::string& owner, const std::string& job_id);
    Json job_control(const std::string& owner, const std::string& job_id, const std::string& action);
    Json read_many_submit(const std::string& owner, const std::vector<std::string>& urls,
                          const std::string& key, ReadManyOptions options);
    Json job_results(const std::string& owner, const std::string& job_id,
                     std::int64_t offset=0, std::int64_t limit=20);
    std::string active_read_attempt(const std::string& owner, const std::string& job_id);
    std::optional<ReadLease> lease_next_read();
    bool read_lease_live(const ReadLease& lease);
    bool complete_read(const ReadLease& lease, const SourceDocument& input, ParsedDocument parsed,
                       const Json& meta, std::int64_t external_requests, std::int64_t content_bytes);
    // Negative usage means an unknown outcome. Its entire durable grant stays charged.
    void fail_read(const ReadLease& lease, const std::string& error_code,
                   std::int64_t external_requests=-1, std::int64_t content_bytes=-1,
                   const Json* publication=nullptr,const std::string& final_url="",const Json* redirect_observations=nullptr);
    Json map_submit(const std::string& owner,const Json& args,const SiteRegistry& sites);
    Json crawl_submit(const std::string& owner,const Json& args,const SiteRegistry& sites);
    Json crawl_edges(const std::string& owner,const std::string& id,std::int64_t offset=0,std::int64_t limit=20);
    std::optional<MapLease> lease_next_map(const SiteRegistry& sites);
    bool map_lease_live(const MapLease& lease);
    bool complete_map(const MapLease& lease,const Json& response,const SiteRegistry& sites,
                      std::int64_t external_requests,std::int64_t content_bytes,
                      const SourceDocument* page=nullptr,const ParsedDocument* parsed=nullptr,const Json* meta=nullptr,const Json* observation=nullptr);
    void fail_map(const MapLease& lease,const std::string& error_code,
                  std::int64_t external_requests=-1,std::int64_t content_bytes=-1,const Json* observation=nullptr);
    std::string active_map_attempt(const std::string& owner,const std::string& job_id);
    std::optional<Lease> lease_next(std::int64_t lease_ms=10000);
    bool complete(const Lease& lease, const SourceDocument& input, const ParsedDocument& parsed);
    void fail(const Lease& lease, const std::string& error_code);
    void settle_interruptions();
    Json backup(const std::filesystem::path& trusted_destination);
    // Source scheduling state (cooldown, minimum interval, daily cap). Returns "" when admitted.
    std::string source_admit(const std::string& source_id, std::int64_t min_interval_ms, std::int64_t daily_cap);
    void source_report(const std::string& source_id, const std::string& status, const std::string& error, std::int64_t cooldown_ms);
    Json source_states();
    std::int64_t now() const {
      return clock_();
    }
    // Test hook: raw handle for tokenizer inspection.
    sqlite3* raw_handle() const {
      return db_;
    }
  };
}
