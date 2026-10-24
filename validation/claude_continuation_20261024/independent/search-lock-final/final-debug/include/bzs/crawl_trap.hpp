#pragma once
#include "bzs/json.hpp"
#include <string>
#include <string_view>
struct sqlite3;

namespace bzs {
  // Classification keys never replace the exact URL used by the frontier,
  // scope checks, robots proof, HTTP request, or saved document.
  inline constexpr const char* crawl_trap_version="bzs-crawl-trap-1";
  struct CrawlTrapDecision {
    std::string reason;
    Json detail;
    explicit operator bool() const { return !reason.empty(); }
  };

  // Mutating helpers run inside the caller's existing Store transaction and
  // admission/lease fences. A legacy live job is bootstrapped once, from its
  // bounded durable frontier and successfully retained body artifacts.
  void crawl_trap_ensure(sqlite3* db,const std::string& job);
  CrawlTrapDecision crawl_trap_decide(sqlite3* db,const std::string& job,const std::string& url,
                                     const std::string& parent,bool explicit_seed=false,bool dispatch=false);
  void crawl_trap_admit(sqlite3* db,const std::string& job,const std::string& url,
                        const std::string& parent,bool explicit_seed=false);
  Json crawl_trap_observe_body(sqlite3* db,const std::string& job,const std::string& url,std::string_view text);
  void crawl_trap_record_edge(sqlite3* db,const std::string& job,const Json& candidate,const CrawlTrapDecision& decision);
  void crawl_trap_skip_edges(sqlite3* db,const std::string& job,const std::string& logical_url,const CrawlTrapDecision& decision);
  Json crawl_trap_summary(sqlite3* db,const std::string& job);
}
