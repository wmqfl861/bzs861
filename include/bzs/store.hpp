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
  class Store {
    sqlite3* db_=nullptr;
    std::filesystem::path path_;
    std::function<std::int64_t()> clock_;
    mutable std::mutex mutex_;
    void migrate();
    std::int64_t epoch_locked(const std::string& owner);
    Json snapshot_locked(const std::string& owner, const std::string& snapshot);
    Json ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed);
    Json job_locked(const std::string& owner, const std::string& id);
    bool live_lease_locked(const Lease& lease);
    void settle_interruptions_locked();
    public: explicit Store(const std::filesystem::path& path, std::function<std::int64_t()> clock= {
    });
    ~Store();
    Store(const Store&)=delete;
    Store& operator=(const Store&)=delete;
    Json ingest(const std::string& owner, const SourceDocument& input);
    Json snapshot(const std::string& owner, const std::string& snapshot_id);
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
  };
}
