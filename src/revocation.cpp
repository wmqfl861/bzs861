#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <limits>
#include <vector>

namespace bzs {
namespace {
void execute(sqlite3* db,const char* command) {
  require(sqlite3_exec(db,command,nullptr,nullptr,nullptr)==SQLITE_OK,
          "E_STORAGE","Principal revocation storage failure",500);
}
struct Statement {
  sqlite3_stmt* value=nullptr;
  Statement(sqlite3* db,const char* command) {
    require(sqlite3_prepare_v2(db,command,-1,&value,nullptr)==SQLITE_OK,
            "E_STORAGE","Principal revocation query failure",500);
  }
  ~Statement() { sqlite3_finalize(value); }
  void bind(int i,const std::string& v) {
    require(sqlite3_bind_text(value,i,v.data(),static_cast<int>(v.size()),SQLITE_TRANSIENT)==SQLITE_OK,
            "E_STORAGE","Principal revocation binding failure",500);
  }
  void bind(int i,std::int64_t v) {
    require(sqlite3_bind_int64(value,i,v)==SQLITE_OK,"E_STORAGE","Principal revocation binding failure",500);
  }
  bool step() {
    const auto rc=sqlite3_step(value);
    require(rc==SQLITE_ROW||rc==SQLITE_DONE,"E_STORAGE","Principal revocation update failure",500);
    return rc==SQLITE_ROW;
  }
  std::int64_t num(int i) const { return sqlite3_column_int64(value,i); }
  std::string text(int i) const {
    const auto* p=reinterpret_cast<const char*>(sqlite3_column_text(value,i));
    return p?std::string(p,static_cast<std::size_t>(sqlite3_column_bytes(value,i))):std::string{};
  }
};
struct Transaction {
  sqlite3* db;bool committed=false;
  explicit Transaction(sqlite3* value):db(value) { execute(db,"BEGIN IMMEDIATE"); }
  ~Transaction() { if(!committed)sqlite3_exec(db,"ROLLBACK",nullptr,nullptr,nullptr); }
  void commit() { execute(db,"COMMIT");committed=true; }
};
Json receipt(const std::string& subject,const std::string& id,std::int64_t at,std::int64_t epoch,bool repeated) {
  auto out=Json::object();out.set("subject",subject);out.set("revoked",true);
  out.set("already_revoked",repeated);out.set("revocation_id",id);
  out.set("revoked_at_ms",at);out.set("auth_epoch",epoch);
  out.set("scope","all_credentials_and_operations_for_subject");
  return out;
}
}

void Store::migrate_revocations_locked() {
  execute(db_,R"SQL(
CREATE TABLE IF NOT EXISTS principal_revocations(
  owner TEXT PRIMARY KEY REFERENCES subjects(owner),revocation_id TEXT NOT NULL UNIQUE,
  revoked_at INTEGER NOT NULL,revoked_by TEXT NOT NULL,auth_epoch INTEGER NOT NULL);
)SQL");
}
void Store::require_principal_active_locked(const std::string& owner) {
  require_identifier(owner);
  Statement s(db_,"SELECT 1 FROM principal_revocations WHERE owner=?");s.bind(1,owner);
  require(!s.step(),"E_PRINCIPAL_REVOKED","This principal has been revoked",403);
}
void Store::require_principal_active(const std::string& owner) {
  std::lock_guard lock(mutex_);require_principal_active_locked(owner);
}
Json Store::revoke_principal(const std::string& actor,const std::string& owner) {
  require_identifier(actor);require_identifier(owner);
  require(actor!=owner,"E_FORBIDDEN","An administrator cannot revoke its own principal",403);
  std::lock_guard lock(mutex_);Transaction tx(db_);require_principal_active_locked(actor);
  Statement old(db_,"SELECT revocation_id,revoked_at,auth_epoch FROM principal_revocations WHERE owner=?");old.bind(1,owner);
  if(old.step()) { auto out=receipt(owner,old.text(0),old.num(1),old.num(2),true);tx.commit();return out; }
  Statement count(db_,"SELECT COUNT(*) FROM principal_revocations");count.step();
  require(count.num(0)<10000,"E_QUOTA","Durable principal revocation limit reached",507);
  const auto previous=epoch_locked(owner);
  require(previous<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Principal epoch exhausted",500);
  Statement generations(db_,"SELECT COALESCE(MAX(epoch),0) FROM jobs WHERE owner=? AND state IN ('queued','paused','running','pausing','cancelling')");generations.bind(1,owner);generations.step();
  require(generations.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Job epoch exhausted",500);
  Statement stream_generations(db_,"SELECT COALESCE(MAX(epoch),0) FROM search_streams WHERE owner=? AND state IN ('queued','running')");stream_generations.bind(1,owner);stream_generations.step();
  require(stream_generations.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Search epoch exhausted",500);
  const auto epoch=previous+1,at=clock_();const auto id=random_id();
  Statement add(db_,"INSERT INTO principal_revocations VALUES(?,?,?,?,?)");
  add.bind(1,owner);add.bind(2,id);add.bind(3,at);add.bind(4,actor);add.bind(5,epoch);add.step();
  Statement advance(db_,"UPDATE subjects SET epoch=? WHERE owner=?");advance.bind(1,epoch);advance.bind(2,owner);advance.step();

  // Undispatched work is cancelled immediately. Already running reads retain
  // their durable grants and lease deadlines so a known completion can settle
  // usage; an unknown outcome is conservatively settled by existing recovery.
  // No request is retried, budget reset, receipt removed, or data resurrected.
  Statement read_pending(db_,"UPDATE read_job_items SET state='cancelled',error_code='E_PRINCIPAL_REVOKED' WHERE state='pending' AND job_id IN (SELECT id FROM jobs WHERE owner=?)");read_pending.bind(1,owner);read_pending.step();
  Statement map_pending(db_,"UPDATE map_resources SET state='cancelled',error_code='E_PRINCIPAL_REVOKED' WHERE state='pending' AND job_id IN (SELECT id FROM jobs WHERE owner=?)");map_pending.bind(1,owner);map_pending.step();
  Statement map_stop(db_,"UPDATE map_jobs SET partial=1,stop_reason='E_PRINCIPAL_REVOKED' WHERE job_id IN (SELECT id FROM jobs WHERE owner=? AND state IN ('queued','paused','running','pausing','cancelling'))");map_stop.bind(1,owner);map_stop.step();
  Statement jobs(db_,R"SQL(UPDATE jobs SET
    state=CASE WHEN kind!='fixture.ingest' AND
      (EXISTS(SELECT 1 FROM read_job_items WHERE job_id=jobs.id AND state='running') OR
       EXISTS(SELECT 1 FROM map_hops WHERE job_id=jobs.id AND state='running')) THEN 'cancelling' ELSE 'cancelled' END,
    epoch=epoch+1,error_code='E_PRINCIPAL_REVOKED',
    lease_until=CASE WHEN kind!='fixture.ingest' AND
      (EXISTS(SELECT 1 FROM read_job_items WHERE job_id=jobs.id AND state='running') OR
       EXISTS(SELECT 1 FROM map_hops WHERE job_id=jobs.id AND state='running')) THEN lease_until ELSE 0 END
    WHERE owner=? AND state IN ('queued','paused','running','pausing','cancelling'))SQL");jobs.bind(1,owner);jobs.step();

  Statement streams(db_,"SELECT id,state FROM search_streams WHERE owner=? AND state IN ('queued','running')");streams.bind(1,owner);
  std::vector<std::pair<std::string,bool>> pending;
  while(streams.step())pending.emplace_back(streams.text(0),streams.text(1)=="running");
  for(const auto& [stream,running]:pending) {
    Statement sources(db_,"UPDATE search_stream_sources SET state=CASE WHEN state='running' THEN 'unknown' ELSE 'cancelled' END WHERE session_id=? AND state IN ('pending','running')");sources.bind(1,stream);sources.step();
    Statement cancel(db_,"UPDATE search_streams SET state='cancelled',epoch=epoch+1,lease_until=0,unknown_requests=unknown_requests+?,error_code='E_PRINCIPAL_REVOKED' WHERE id=?");cancel.bind(1,static_cast<std::int64_t>(running?1:0));cancel.bind(2,stream);cancel.step();
    stream_event_locked(stream,"principal_revoked");stream_receipt_locked(stream);
  }
  tx.commit();return receipt(owner,id,at,epoch,false);
}
}
