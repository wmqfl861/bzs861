#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <vector>
#include <utility>

namespace bzs {
namespace {
constexpr std::int64_t retention_ms=7LL*24*60*60*1000;
constexpr std::int64_t owner_details=10000,global_details=50000,max_summary_bytes=8192;
constexpr const char* version="bzs-job-retention-1";
void checked(int rc) {
  if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
  const auto primary=rc&0xff;
  if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
  if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy; retry later",503);
  throw Error("E_STORAGE","Storage operation failed (SQLite "+std::to_string(primary)+")",500);
}
class Sql {
  sqlite3_stmt* statement_=nullptr;
public:
  Sql(sqlite3* db,const char* query) { checked(sqlite3_prepare_v2(db,query,-1,&statement_,nullptr)); }
  ~Sql() { sqlite3_finalize(statement_); }
  Sql(const Sql&)=delete;
  void bind(int i,const std::string& value) { checked(sqlite3_bind_text(statement_,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
  void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(statement_,i,value)); }
  bool step() { const auto rc=sqlite3_step(statement_);checked(rc);return rc==SQLITE_ROW; }
  std::int64_t num(int i)const { return sqlite3_column_int64(statement_,i); }
  std::string text(int i)const { const auto* value=sqlite3_column_text(statement_,i);return value?std::string(reinterpret_cast<const char*>(value),static_cast<std::size_t>(sqlite3_column_bytes(statement_,i))):""; }
};
class Tx {
  sqlite3* db_;bool committed_=false;
public:
  explicit Tx(sqlite3* db):db_(db) { checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr)); }
  ~Tx() { if(!committed_)sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr); }
  void commit() { checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));committed_=true; }
};
// The deadline, rather than a mutable state-change time, also protects the
// original retry window. Paused jobs and unsettled grants are never eligible.
constexpr const char* eligible=R"SQL(
 AND j.kind IN ('fixture.ingest','web.read_many','web.map','web.crawl') AND j.state IN ('succeeded','failed','cancelled')
 AND j.created_at<=? AND j.deadline<=? AND j.lease_until<=?
 AND (j.kind='fixture.ingest' OR (j.kind='web.read_many' AND EXISTS(SELECT 1 FROM read_job_budgets b WHERE b.job_id=j.id)
   AND NOT EXISTS(SELECT 1 FROM read_job_items i WHERE i.job_id=j.id AND (i.state IN ('pending','running') OR i.request_grant<>0 OR i.byte_grant<>0)))
  OR (j.kind IN ('web.map','web.crawl') AND EXISTS(SELECT 1 FROM map_jobs m WHERE m.job_id=j.id)
   AND NOT EXISTS(SELECT 1 FROM map_resources r WHERE r.job_id=j.id AND r.state IN ('pending','running'))
   AND NOT EXISTS(SELECT 1 FROM map_hops h WHERE h.job_id=j.id AND (h.state='running' OR h.request_grant<>0 OR h.byte_grant<>0))))
 ORDER BY MAX(j.created_at,j.deadline,j.lease_until),j.id LIMIT ?
 )SQL";
}

// Compute from retained rows, including old databases; no business schema or
// duplicated counter is introduced. The owner/key index bounds the owner scan.
std::int64_t Store::archive_bytes_locked(const std::string& owner) {
  Sql bytes(db_,"SELECT COALESCE(SUM(length(CAST(archive_json AS BLOB))),0) FROM jobs WHERE owner=? AND archived_at<>0");
  bytes.bind(1,owner);bytes.step();require(bytes.num(0)>=0,"E_STORAGE","Invalid archive accounting",500);return bytes.num(0);
}
bool Store::payload_quota_fits_locked(const std::string& owner,std::int64_t extra) {
  if(extra<=0)return true; // Deletion or restrictive metadata may reduce usage.
  Sql bytes(db_,"SELECT bytes FROM usage WHERE owner=?");bytes.bind(1,owner);
  const auto content=bytes.step()?bytes.num(0):0,archive=archive_bytes_locked(owner),limit=options_.max_bytes_per_owner;
  require(content>=0,"E_STORAGE","Invalid snapshot accounting",500);
  return extra<=limit&&archive<=limit-extra&&content<=limit-extra-archive;
}

std::optional<Json> Store::compacted_job_locked(const std::string& owner,const std::string& id) {
  Sql row(db_,"SELECT archive_json FROM jobs WHERE id=? AND owner=? AND archived_at<>0");row.bind(1,id);row.bind(2,owner);
  if(!row.step())return std::nullopt;
  const auto encoded=row.text(0);
  require(!encoded.empty()&&encoded.size()<=static_cast<std::size_t>(max_summary_bytes),"E_STORAGE","Invalid retained job summary",500);
  auto summary=Json::parse(encoded);
  require(summary.is_object()&&summary.at("job_id").str()==id&&summary.at("retention").at("version").str()==version&&
          !summary.at("retention").at("details_available").flag(),"E_STORAGE","Invalid retained job summary",500);
  return summary;
}

void Store::job_capacity_locked(const std::string& owner) {
  const auto counts=[&] {
    Sql count(db_,"SELECT (SELECT COUNT(*) FROM jobs WHERE owner=? AND archived_at=0),(SELECT COUNT(*) FROM jobs WHERE archived_at=0)");
    count.bind(1,owner);count.step();return std::pair{count.num(0),count.num(1)};
  };
  auto [owned,total]=counts();
  if(total>=global_details) {
    // Capacity belongs to the deployment. Revoked or inactive owners must not
    // pin it forever: reclaim only fully settled, aged details in this same
    // admission transaction. Never return another owner's metadata to callers.
    compact_jobs_locked(owner,8,true);
    const auto after=counts();owned=after.first;total=after.second;
  }
  require(owned<owner_details&&total<global_details,"E_QUOTA","Unarchived job detail capacity reached; compact eligible terminal jobs",429);
}

std::int64_t Store::compact_robots_locked(const std::string& owner,std::int64_t limit) {
  // Both references are indexed. A successful robots fetch pins its receipt;
  // admitted page hops pin the exact record used to authorize that request.
  // Keep the newest decision while any older representation remains. Otherwise
  // collecting a newer denial could expose an old, pinned validator as current.
  // A second bounded pass collects the head after the unreferenced older rows
  // disappear; singleton histories do not become permanent capacity leaks.
  std::int64_t removed=0;
  while(removed<limit) {
  Sql remove(db_,R"SQL(DELETE FROM robots_cache WHERE id IN (
 SELECT c.id FROM robots_cache c WHERE c.owner=? AND c.expires_at<=?
 AND NOT EXISTS(SELECT 1 FROM map_hops h WHERE json_extract(h.admission_json,'$.robots_cache_id')=c.id)
 AND NOT EXISTS(SELECT 1 FROM map_hops h WHERE json_extract(h.response_json,'$.robots_cache_id')=c.id)
 AND (EXISTS(SELECT 1 FROM robots_cache n WHERE n.owner=c.owner AND n.origin=c.origin AND n.manifest_sha256=c.manifest_sha256
      AND (n.fetched_at>c.fetched_at OR (n.fetched_at=c.fetched_at AND n.rowid>c.rowid)))
   OR NOT EXISTS(SELECT 1 FROM robots_cache o WHERE o.owner=c.owner AND o.origin=c.origin AND o.manifest_sha256=c.manifest_sha256
      AND (o.fetched_at<c.fetched_at OR (o.fetched_at=c.fetched_at AND o.rowid<c.rowid))))
 ORDER BY c.fetched_at,c.rowid LIMIT ?))SQL");
  remove.bind(1,owner);remove.bind(2,clock_());remove.bind(3,limit-removed);remove.step();
  const auto count=sqlite3_changes(db_);removed+=count;if(count==0)break;
  }
  return removed;
}

Json Store::compact_jobs_locked(const std::string& owner,std::int64_t limit,bool all_owners) {
  const auto now=clock_();std::vector<std::pair<std::string,std::string>> ids;
  // Subtraction is safe even near INT64_MAX. A clock before the Unix epoch or
  // within the first window cannot make a future deadline eligible.
  if(now>=retention_ms) {
    const auto cutoff=now-retention_ms;
    const auto query=std::string("SELECT j.id,j.owner FROM jobs j WHERE j.archived_at=0 ")+
      (all_owners?"":"AND j.owner=? ")+eligible;
    Sql rows(db_,query.c_str());int parameter=1;
    if(!all_owners)rows.bind(parameter++,owner);
    for(int i=0;i<3;++i)rows.bind(parameter++,cutoff);
    rows.bind(parameter,limit+1);
    while(rows.step())ids.emplace_back(rows.text(0),rows.text(1));
  }
  const bool more=ids.size()>static_cast<std::size_t>(limit);
  if(more)ids.resize(static_cast<std::size_t>(limit));
  std::int64_t details=0,compacted=0,blocked=0;
  for(const auto& [id,job_owner]:ids) {
    auto summary=job_details_locked(job_owner,id);
    if(summary.at("kind").str()=="fixture.ingest") {
      // The fixture result is expired detail. Keep its permanent identity and
      // terminal counters, never arbitrary legacy result_json or body copies.
      summary.set("result",Json());
    } else {
      const auto usage=summary.at("usage"),budget=summary.at("budget");
      require(usage.at("reserved_requests").num()==0&&usage.at("reserved_bytes").num()==0&&
              budget.at("charged_requests").num()==usage.at("external_requests").num()+usage.at("unknown_requests_charged").num()&&
              budget.at("charged_bytes").num()==usage.at("content_bytes").num()+usage.at("unknown_bytes_charged").num(),
              "E_STORAGE","Unsettled job accounting cannot be compacted",500);
    }
    auto retention=Json::object();retention.set("version",version);retention.set("details_available",false);
    retention.set("compacted_at_ms",now);retention.set("minimum_retention_ms",retention_ms);summary.set("retention",retention);
    const auto encoded=summary.canonical();require(encoded.size()<=static_cast<std::size_t>(max_summary_bytes),"E_QUOTA","Retained job summary exceeds its bound",429);
    // Do not erase details unless their immutable summary fits. This also
    // allows old over-quota owners to read/cancel/delete without losing keys.
    if(!payload_quota_fits_locked(job_owner,static_cast<std::int64_t>(encoded.size()))) { ++blocked;continue; }
    ++compacted;
    Sql archive(db_,"UPDATE jobs SET archive_json=?,archived_at=?,result_json=CASE WHEN kind='fixture.ingest' THEN '' ELSE result_json END WHERE id=? AND owner=? AND archived_at=0");
    archive.bind(1,encoded);archive.bind(2,now);archive.bind(3,id);archive.bind(4,job_owner);archive.step();
    // Preserve the job/key row, snapshots, evidence, deletion and publication
    // watermarks, origin quotas/cooldowns and dispatch sequences. All detail
    // deletion and its preceding immutable summary share the caller's writer
    // transaction; any failure rolls the entire compaction back.
    for(const auto* table:{"crawl_trap_edges","crawl_trap_urls","crawl_trap_policy","crawl_edges","map_hops","map_candidates","map_resources","map_jobs","read_shared_members","read_job_items","read_job_budgets"}) {
      const auto query="DELETE FROM "+std::string(table)+" WHERE job_id=?";Sql remove(db_,query.c_str());remove.bind(1,id);remove.step();details+=sqlite3_changes(db_);
    }
    // A compacted payer still exists as a permanent job/key summary. Keep its
    // physical attempt attribution while any other job retains membership.
    // At most 20 items * 5 attempts can lose their final member per job.
    Sql shared(db_,R"SQL(DELETE FROM read_shared_attempts WHERE attempt_id IN (
 SELECT g.attempt_id FROM read_shared_attempts g WHERE g.owner=? AND g.settled<>0
 AND NOT EXISTS(SELECT 1 FROM read_shared_members m WHERE m.attempt_id=g.attempt_id)
 ORDER BY g.attempt_id LIMIT 100))SQL");
    shared.bind(1,job_owner);shared.step();details+=sqlite3_changes(db_);
  }
  const auto robots=all_owners?0:compact_robots_locked(owner,std::min<std::int64_t>(64,limit*4));
  Sql count(db_,"SELECT COUNT(*),COALESCE(SUM(archived_at=0),0) FROM jobs WHERE owner=?");count.bind(1,owner);count.step();
  auto out=Json::object();out.set("version",version);out.set("minimum_retention_ms",retention_ms);out.set("limit",limit);
  out.set("compacted_jobs",compacted);out.set("archive_quota_blocked_jobs",blocked);out.set("deleted_detail_rows",details);out.set("deleted_robots_records",robots);
  out.set("has_more_jobs",more||blocked>0);out.set("retained_job_keys",count.num(0));out.set("unarchived_jobs",count.num(1));
  auto limits=Json::object();limits.set("max_jobs_per_owner",owner_details);limits.set("max_jobs_global",global_details);limits.set("job_capacity_scope","unarchived_details");
  limits.set("max_jobs_per_call",std::int64_t{64});limits.set("max_robots_per_call",std::int64_t{64});limits.set("max_summary_bytes",max_summary_bytes);out.set("limits",limits);
  out.set("external_requests",std::int64_t{0});out.set("model_calls",std::int64_t{0});return out;
}

Json Store::compact_jobs(const std::string& owner,std::int64_t limit) {
  require_identifier(owner);require(limit>=1&&limit<=64,"E_SCHEMA","Job compaction limit must be 1..64");
  std::lock_guard lock(mutex_);Json out;
  { Tx tx(db_);require_principal_active_locked(owner);out=compact_jobs_locked(owner,limit);tx.commit(); }
  // Compaction is already committed. Reclamation is best-effort and bounded;
  // a busy reader must not make callers believe the logical deletion failed.
  auto reclaim=Json::object();std::int64_t selected=0;
  { Sql mode(db_,"PRAGMA auto_vacuum");mode.step();selected=mode.num(0); }
  reclaim.set("auto_vacuum_mode",selected);reclaim.set("page_budget",std::int64_t{128});
  reclaim.set("status",selected==2?"completed":"requires_offline_conversion");
  if(selected==2) {
    try {
      // Drain all SQLITE_ROW results: SQLite can reclaim one page per step.
      Sql vacuum(db_,"PRAGMA incremental_vacuum(128)");while(vacuum.step()){}
    }catch(const Error&e){reclaim.set("status","deferred");reclaim.set("error_code",e.code);}
  }
  out.set("storage_reclamation",reclaim);return out;
}
}
