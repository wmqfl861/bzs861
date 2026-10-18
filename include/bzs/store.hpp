#pragma once
#include "bzs/html.hpp"
#include "bzs/query.hpp"
#include <filesystem>
#include <functional>
#include <mutex>
#include <optional>
struct sqlite3;
namespace bzs {
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
  class Store {
    sqlite3* db_=nullptr;
    std::filesystem::path path_;
    std::function<std::int64_t()> clock_;
    StoreOptions options_;
    mutable std::mutex mutex_;
    void migrate();
    std::int64_t epoch_locked(const std::string& owner);
    std::int64_t delete_sequence_locked();
    std::int64_t deletion_generation_locked(const std::string& owner,const std::string& url);
    Json snapshot_locked(const std::string& owner, const std::string& snapshot);
    Json ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed, const std::string& meta_json, const std::string& source_kind);
    Json job_locked(const std::string& owner, const std::string& id);
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
