#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <limits>
#include <set>
#include <tuple>

namespace bzs {
namespace {
  // This module keeps the batch ledger beside the existing SQLite job/lease
  // tables. Every reservation and publication uses one writer transaction.
  void checked(int rc) {
    if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
    auto primary=rc&0xff;
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
    Sql& operator=(const Sql&)=delete;
    void bind(int i,const std::string& value) {
      checked(sqlite3_bind_text(statement_,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT));
    }
    void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(statement_,i,value)); }
    void null(int i) { checked(sqlite3_bind_null(statement_,i)); }
    bool step() { auto rc=sqlite3_step(statement_);checked(rc);return rc==SQLITE_ROW; }
    std::int64_t num(int i)const { return sqlite3_column_int64(statement_,i); }
    std::string text(int i)const {
      auto* value=sqlite3_column_text(statement_,i);
      return value?std::string(reinterpret_cast<const char*>(value),static_cast<std::size_t>(sqlite3_column_bytes(statement_,i))):"";
    }
  };
  class Transaction {
    sqlite3* db_;
    bool committed_=false;
  public:
    explicit Transaction(sqlite3* db):db_(db) { checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr)); }
    ~Transaction() { if(!committed_)sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr); }
    void commit() { checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));committed_=true; }
  };
  constexpr std::int64_t per_item_requests=18;
  constexpr std::int64_t per_item_bytes=8*1024*1024;
  // Admissions and dispatched attempts share one durable queue clock. New
  // arrivals join the tail, and equal wall clocks never fall back to random
  // job IDs for jobs admitted by this scheduler version.
  std::int64_t next_queue_sequence(sqlite3* db) {
    Sql current(db,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='read_dispatch_sequence'");
    require(current.step()&&current.num(0)>=0&&current.num(0)<std::numeric_limits<std::int64_t>::max(),
            "E_STORAGE","Read scheduling sequence is missing or exhausted",500);
    const auto sequence=current.num(0)+1;
    Sql update(db,"UPDATE settings SET value=? WHERE key='read_dispatch_sequence'");update.bind(1,sequence);update.step();
    return sequence;
  }
  Json receipt(const Json& snapshot) {
    Json out=Json::object();
    for(const auto* key:{"document_id","snapshot_id","extraction_id","revision","indexed"})out.set(key,snapshot.at(key));
    return out;
  }
}

void Store::migrate_shared_reads_locked() {
  // Membership and the physical payer survive process failure independently of
  // a job's current epoch. Historical attempts remain until their members' job
  // details can be compacted; retries never overwrite their accounting.
  checked(sqlite3_exec(db_,R"SQL(
 CREATE TABLE IF NOT EXISTS read_shared_attempts(
   attempt_id TEXT PRIMARY KEY,owner TEXT NOT NULL,payer_job_id TEXT NOT NULL REFERENCES jobs(id),
   payer_position INTEGER NOT NULL,payer_epoch INTEGER NOT NULL,lease_until INTEGER NOT NULL,
   request_grant INTEGER NOT NULL,byte_grant INTEGER NOT NULL,member_count INTEGER NOT NULL,
   settled INTEGER NOT NULL DEFAULT 0,external_requests INTEGER NOT NULL DEFAULT -1,
   content_bytes INTEGER NOT NULL DEFAULT -1,error_code TEXT NOT NULL DEFAULT '');
 CREATE TABLE IF NOT EXISTS read_shared_members(
   attempt_id TEXT NOT NULL REFERENCES read_shared_attempts(attempt_id) ON DELETE CASCADE,
   job_id TEXT NOT NULL REFERENCES jobs(id),position INTEGER NOT NULL,lease_epoch INTEGER NOT NULL,
   PRIMARY KEY(attempt_id,job_id,position));
 CREATE INDEX IF NOT EXISTS read_shared_member_job ON read_shared_members(job_id,attempt_id);
 CREATE INDEX IF NOT EXISTS read_shared_recovery ON read_shared_attempts(settled,lease_until);
 CREATE INDEX IF NOT EXISTS read_shared_item_attempt ON read_job_items(attempt_id,state);
 )SQL",nullptr,nullptr,nullptr));
}

void Store::attach_shared_reads_locked(const ReadLease& payer) {
  Sql original(db_,"SELECT j.auth_epoch,j.created_delete_sequence,j.lease_until,i.publication_snapshot_id FROM jobs j JOIN read_job_items i ON i.job_id=j.id WHERE j.id=? AND i.position=? AND i.attempt_id=?");
  original.bind(1,payer.job_id);original.bind(2,payer.input_index);original.bind(3,payer.attempt_id);
  require(original.step(),"E_STORAGE","Shared read payer is missing",500);
  const auto now=clock_(),auth=original.num(0),deleted=original.num(1),until=original.num(2);
  const auto snapshot=original.text(3);
  // Co-dispatch only the first pending item of another eligible batch. Exact
  // URL, permission/deletion history and the immutable representation captured
  // in this writer transaction are shared. Caller limits can only be tighter
  // on the physical payer, never expanded to accommodate a subscriber.
  Sql candidates(db_,R"SQL(SELECT j.id,j.epoch,j.deadline,i.position
 FROM jobs j JOIN read_job_budgets b ON b.job_id=j.id JOIN read_job_items i ON i.job_id=j.id
 WHERE j.kind='web.read_many' AND j.state='queued' AND j.owner=? AND j.auth_epoch=?
 AND j.created_delete_sequence=? AND j.deadline>=? AND j.id<>?
 AND i.state='pending' AND i.url=? AND i.deletion_generation=? AND i.attempts<j.max_attempts
 AND i.position=(SELECT MIN(p.position) FROM read_job_items p WHERE p.job_id=j.id AND p.state='pending')
 AND b.max_requests-b.charged_requests>=? AND b.max_bytes-b.charged_bytes>=?
 AND MIN(b.per_page_timeout_ms,j.deadline-?)=?
 ORDER BY b.last_dispatch_sequence,j.created_at,j.id LIMIT 31)SQL");
  candidates.bind(1,payer.owner);candidates.bind(2,auth);candidates.bind(3,deleted);
  candidates.bind(4,until);candidates.bind(5,payer.job_id);candidates.bind(6,payer.url);
  candidates.bind(7,payer.deletion_generation);candidates.bind(8,payer.request_grant);
  candidates.bind(9,payer.byte_grant);candidates.bind(10,now);candidates.bind(11,payer.timeout_ms);
  struct Candidate { std::string id;std::int64_t epoch,position; };
  std::vector<Candidate> members;
  while(candidates.step()) {
    require(candidates.num(1)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Lease generation exhausted",500);
    members.push_back({candidates.text(0),candidates.num(1)+1,candidates.num(3)});
  }
  if(members.empty())return;
  Sql group(db_,"INSERT INTO read_shared_attempts(attempt_id,owner,payer_job_id,payer_position,payer_epoch,lease_until,request_grant,byte_grant,member_count)VALUES(?,?,?,?,?,?,?,?,?)");
  group.bind(1,payer.attempt_id);group.bind(2,payer.owner);group.bind(3,payer.job_id);
  group.bind(4,payer.input_index);group.bind(5,payer.epoch);group.bind(6,until);
  group.bind(7,payer.request_grant);group.bind(8,payer.byte_grant);
  group.bind(9,static_cast<std::int64_t>(members.size()+1));group.step();
  const auto record=[&](const std::string& id,std::int64_t position,std::int64_t epoch) {
    Sql insert(db_,"INSERT INTO read_shared_members(attempt_id,job_id,position,lease_epoch)VALUES(?,?,?,?)");
    insert.bind(1,payer.attempt_id);insert.bind(2,id);insert.bind(3,position);insert.bind(4,epoch);insert.step();
  };
  record(payer.job_id,payer.input_index,payer.epoch);
  for(const auto& member:members) {
    Sql claim(db_,"UPDATE read_job_items SET state='running',attempts=attempts+1,lease_epoch=?,attempt_id=?,request_grant=0,byte_grant=0,error_code=NULL,publication_sequence=?,publication_snapshot_id=?,dispatch_timeout_ms=0,sharing_closed=1 WHERE job_id=? AND position=?");
    claim.bind(1,member.epoch);claim.bind(2,payer.attempt_id);claim.bind(3,payer.publication_sequence);
    claim.bind(4,snapshot);claim.bind(5,member.id);claim.bind(6,member.position);claim.step();
    Sql activate(db_,"UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,lease_until=?,error_code=NULL WHERE id=?");
    activate.bind(1,member.epoch);activate.bind(2,until);activate.bind(3,member.id);activate.step();
    Sql rotate(db_,"UPDATE read_job_budgets SET last_dispatch_sequence=? WHERE job_id=?");
    rotate.bind(1,next_queue_sequence(db_));rotate.bind(2,member.id);rotate.step();
    record(member.id,member.position,member.epoch);
  }
}

void Store::close_read_sharing_locked(const ReadLease& payer) {
  // Closing admission is separate from publication and accounting. A delayed
  // check for an old lease must never close a later explicit retry of this item.
  Sql close(db_,"UPDATE read_job_items SET sharing_closed=1 WHERE job_id=? AND position=? AND attempt_id=? AND lease_epoch=? AND state='running'");
  close.bind(1,payer.job_id);close.bind(2,payer.input_index);close.bind(3,payer.attempt_id);
  close.bind(4,payer.epoch);close.step();
}

void Store::attach_late_read_locked(const std::string& owner,const std::string& id) {
  // Only the first item of a newly admitted batch can join. Idempotent replay
  // returns before this function, and failed/paused work keeps its own scheduler.
  Sql incoming(db_,R"SQL(SELECT j.auth_epoch,j.created_delete_sequence,j.deadline,j.epoch,
 b.max_requests-b.charged_requests,b.max_bytes-b.charged_bytes,b.per_page_timeout_ms,
 i.url,i.deletion_generation
 FROM jobs j JOIN read_job_budgets b ON b.job_id=j.id JOIN read_job_items i ON i.job_id=j.id
 WHERE j.id=? AND j.owner=? AND j.kind='web.read_many' AND j.state='queued'
 AND i.position=0 AND i.state='pending' AND i.attempts<j.max_attempts)SQL");
  incoming.bind(1,id);incoming.bind(2,owner);
  if(!incoming.step())return;
  const auto now=clock_(),deadline=incoming.num(2);
  if(deadline<=now||incoming.num(0)!=epoch_locked(owner)||
     incoming.num(8)!=deletion_generation_locked(owner,incoming.text(7)))return;
  const auto timeout=std::min(incoming.num(6),deadline-now);
  Sql current(db_,"SELECT current_snapshot FROM documents WHERE owner=? AND url=?");
  current.bind(1,owner);current.bind(2,incoming.text(7));
  const auto snapshot=current.step()?current.text(0):"";
  const auto publication=publication_sequence_locked();
  // The partial index contains physical payers only: subscribers have zero
  // grants and cannot reopen a closed attempt through a different member.
  // Bounded exact-URI ranges do not scan permanent completed job history.
  Sql candidates(db_,R"SQL(SELECT j.id,j.owner,i.url,i.attempt_id,i.lease_epoch,i.position,
 i.deletion_generation,i.request_grant,i.byte_grant,i.dispatch_timeout_ms,j.deadline,
 i.publication_sequence,j.lease_until,COALESCE(g.member_count,1)
 FROM read_job_items i INDEXED BY read_shared_open_uri JOIN jobs j ON j.id=i.job_id
 LEFT JOIN read_shared_attempts g ON g.attempt_id=i.attempt_id
 WHERE i.url=? AND i.state='running' AND i.sharing_closed=0 AND i.request_grant>0
 AND j.owner=? AND j.kind='web.read_many' AND j.auth_epoch=? AND j.created_delete_sequence=?
 AND i.deletion_generation=? AND j.lease_until>? AND j.lease_until<=?
 AND i.dispatch_timeout_ms=? AND i.request_grant<=? AND i.byte_grant<=?
 AND i.publication_sequence=? AND i.publication_snapshot_id=? AND i.publication_snapshot_id<>'@legacy'
 AND (g.attempt_id IS NULL OR (g.settled=0 AND g.owner=j.owner AND g.payer_job_id=j.id
 AND g.payer_position=i.position AND g.payer_epoch=i.lease_epoch AND g.member_count<32))
 ORDER BY i.job_id,i.position LIMIT 32)SQL");
  candidates.bind(1,incoming.text(7));candidates.bind(2,owner);candidates.bind(3,incoming.num(0));
  candidates.bind(4,incoming.num(1));candidates.bind(5,incoming.num(8));candidates.bind(6,now);
  candidates.bind(7,deadline);candidates.bind(8,timeout);candidates.bind(9,incoming.num(4));
  candidates.bind(10,incoming.num(5));candidates.bind(11,publication);candidates.bind(12,snapshot);
  struct Candidate { ReadLease payer;std::int64_t until,count; };
  std::vector<Candidate> pending;
  while(candidates.step())pending.push_back({
    {candidates.text(0),candidates.text(1),candidates.text(2),candidates.text(3),
     candidates.num(4),candidates.num(5),candidates.num(6),candidates.num(7),candidates.num(8),
     candidates.num(9),candidates.num(10),candidates.num(11)},candidates.num(12),candidates.num(13)});
  for(const auto& candidate:pending) {
    const auto& payer=candidate.payer;
    const auto members=shared_read_members_locked(payer);
    const bool live=members.empty()?
      live_read_lease_locked(payer)&&deletion_generation_locked(owner,payer.url)==payer.deletion_generation:
      shared_read_live_locked(payer);
    if(!live) { close_read_sharing_locked(payer);continue; }
    require(incoming.num(3)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Lease generation exhausted",500);
    const auto epoch=incoming.num(3)+1;
    if(members.empty()) {
      Sql group(db_,"INSERT INTO read_shared_attempts(attempt_id,owner,payer_job_id,payer_position,payer_epoch,lease_until,request_grant,byte_grant,member_count)VALUES(?,?,?,?,?,?,?,?,2)");
      group.bind(1,payer.attempt_id);group.bind(2,owner);group.bind(3,payer.job_id);
      group.bind(4,payer.input_index);group.bind(5,payer.epoch);group.bind(6,candidate.until);
      group.bind(7,payer.request_grant);group.bind(8,payer.byte_grant);group.step();
      Sql original(db_,"INSERT INTO read_shared_members(attempt_id,job_id,position,lease_epoch)VALUES(?,?,?,?)");
      original.bind(1,payer.attempt_id);original.bind(2,payer.job_id);original.bind(3,payer.input_index);
      original.bind(4,payer.epoch);original.step();
    } else {
      require(candidate.count>=2&&candidate.count<32,"E_STORAGE","Invalid shared read member count",500);
      Sql extend(db_,"UPDATE read_shared_attempts SET member_count=member_count+1 WHERE attempt_id=? AND settled=0 AND member_count<32");
      extend.bind(1,payer.attempt_id);extend.step();
      require(sqlite3_changes(db_)==1,"E_LEASE_EXPIRED","Shared read is no longer accepting subscribers",409);
    }
    Sql claim(db_,"UPDATE read_job_items SET state='running',attempts=attempts+1,lease_epoch=?,attempt_id=?,request_grant=0,byte_grant=0,error_code=NULL,publication_sequence=?,publication_snapshot_id=?,dispatch_timeout_ms=0,sharing_closed=1 WHERE job_id=? AND position=0");
    claim.bind(1,epoch);claim.bind(2,payer.attempt_id);claim.bind(3,payer.publication_sequence);
    claim.bind(4,snapshot);claim.bind(5,id);claim.step();
    Sql activate(db_,"UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,lease_until=?,error_code=NULL WHERE id=?");
    activate.bind(1,epoch);activate.bind(2,candidate.until);activate.bind(3,id);activate.step();
    Sql rotate(db_,"UPDATE read_job_budgets SET last_dispatch_sequence=? WHERE job_id=?");
    rotate.bind(1,next_queue_sequence(db_));rotate.bind(2,id);rotate.step();
    Sql member(db_,"INSERT INTO read_shared_members(attempt_id,job_id,position,lease_epoch)VALUES(?,?,0,?)");
    member.bind(1,payer.attempt_id);member.bind(2,id);member.bind(3,epoch);member.step();
    return;
  }
}

std::vector<ReadLease> Store::shared_read_members_locked(const ReadLease& payer) {
  Sql group(db_,"SELECT 1 FROM read_shared_attempts WHERE attempt_id=? AND owner=? AND payer_job_id=? AND payer_position=? AND payer_epoch=? AND settled=0");
  group.bind(1,payer.attempt_id);group.bind(2,payer.owner);group.bind(3,payer.job_id);
  group.bind(4,payer.input_index);group.bind(5,payer.epoch);
  if(!group.step())return {};
  Sql rows(db_,R"SQL(SELECT j.id,j.owner,i.url,i.attempt_id,i.lease_epoch,i.position,i.deletion_generation,
 i.request_grant,i.byte_grant,b.per_page_timeout_ms,j.deadline,i.publication_sequence
 FROM read_shared_members m JOIN read_job_items i ON i.job_id=m.job_id AND i.position=m.position
 JOIN jobs j ON j.id=i.job_id JOIN read_job_budgets b ON b.job_id=j.id
 WHERE m.attempt_id=? AND i.attempt_id=m.attempt_id AND i.lease_epoch=m.lease_epoch AND i.state='running'
 ORDER BY j.id,i.position)SQL");
  rows.bind(1,payer.attempt_id);std::vector<ReadLease> members;
  while(rows.step())members.push_back({rows.text(0),rows.text(1),rows.text(2),rows.text(3),rows.num(4),rows.num(5),rows.num(6),rows.num(7),rows.num(8),rows.num(9),rows.num(10),rows.num(11)});
  require(members.size()>=2&&members.size()<=32,"E_STORAGE","Shared read membership is incomplete",500);
  return members;
}

bool Store::shared_read_live_locked(const ReadLease& payer) {
  for(const auto& member:shared_read_members_locked(payer))
    if(live_read_lease_locked(member)&&deletion_generation_locked(member.owner,member.url)==member.deletion_generation)return true;
  return false;
}

void Store::settle_shared_read_locked(const ReadLease& payer,const std::string& error,
                                    std::int64_t requests,std::int64_t bytes,const Json* snapshot) {
  auto members=shared_read_members_locked(payer);
  if(members.empty())return;
  std::vector<bool> publish;bool any=false;
  for(const auto& member:members) {
    const bool live=snapshot&&live_read_lease_locked(member)&&
      deletion_generation_locked(member.owner,member.url)==member.deletion_generation;
    publish.push_back(live);any=any||live;
  }
  require(!snapshot||any,"E_LEASE_EXPIRED","Shared read has no live subscriber at commit",409);
  // The original payer remains responsible even after its own subscription is
  // cancelled. No grant is moved, refunded twice or charged to every member.
  reconcile_read_usage_locked(payer,requests,bytes);
  for(std::size_t index=0;index<members.size();++index) {
    const auto& member=members[index];
    if(member.job_id!=payer.job_id)reconcile_read_usage_locked(member,0,0);
    Sql done(db_,"UPDATE read_job_items SET state=?,snapshot_id=?,receipt_json=?,error_code=? WHERE job_id=? AND position=?");
    done.bind(1,std::string(publish[index]?"succeeded":"failed"));
    // NULL avoids creating an invalid foreign key for a cancelled subscriber.
    if(publish[index]) {
      done.bind(2,snapshot->at("snapshot_id").str());done.bind(3,receipt(*snapshot).dump());
    } else {
      done.null(2);done.null(3);
    }
    done.bind(4,publish[index]?std::string{}:error.empty()?std::string("E_LEASE_EXPIRED"):error);
    done.bind(5,member.job_id);done.bind(6,member.input_index);done.step();
  }
  Sql settled(db_,"UPDATE read_shared_attempts SET settled=1,external_requests=?,content_bytes=?,error_code=? WHERE attempt_id=? AND settled=0");
  settled.bind(1,requests);settled.bind(2,bytes);settled.bind(3,error);settled.bind(4,payer.attempt_id);settled.step();
  for(const auto& member:members)settle_read_job_locked(member.job_id);
}

Json Store::read_many_submit(const std::string& owner,const std::vector<std::string>& input,
                             const std::string& key,ReadManyOptions options) {
  require_identifier(owner);
  require_identifier(key);
  require(!input.empty()&&input.size()<=20,"E_SCHEMA","Read batches require 1..20 explicit URLs");
  std::vector<std::string> urls;
  std::set<std::string> unique;
  Json parameters=Json::object(),list=Json::array();
  for(const auto& original:input) {
    auto url=normalize_iri(original);
    check_public_url(url);
    require(unique.insert(url).second,"E_SCHEMA","Duplicate URL in read batch");
    urls.push_back(url);
    list.push(Json::string(url));
  }
  if(options.max_requests==0)options.max_requests=per_item_requests*static_cast<std::int64_t>(urls.size());
  if(options.max_bytes==0)options.max_bytes=per_item_bytes*static_cast<std::int64_t>(urls.size());
  require(options.timeout_ms>=1000&&options.timeout_ms<=3600000&&
          options.per_page_timeout_ms>=1000&&options.per_page_timeout_ms<=55000&&
          options.max_requests>=1&&options.max_requests<=1000&&
          options.max_bytes>=1&&options.max_bytes<=256LL*1024*1024&&
          options.max_attempts>=1&&options.max_attempts<=5,"E_SCHEMA","Invalid read batch budget");
  parameters.set("kind","web.read_many");
  parameters.set("urls",list);
  parameters.set("timeout_ms",options.timeout_ms);
  parameters.set("per_page_timeout_ms",options.per_page_timeout_ms);
  parameters.set("max_requests",options.max_requests);
  parameters.set("max_bytes",options.max_bytes);
  parameters.set("max_attempts",options.max_attempts);
  auto fingerprint=sha256(parameters.canonical());
  std::lock_guard lock(mutex_);
  Transaction tx(db_);
  require_principal_active_locked(owner);
  auto auth=epoch_locked(owner);
  {
    Sql existing(db_,"SELECT id,fingerprint,kind FROM jobs WHERE owner=? AND idempotency_key=?");
    existing.bind(1,owner);existing.bind(2,key);
    if(existing.step()) {
      require(existing.text(1)==fingerprint&&existing.text(2)=="web.read_many","E_IDEMPOTENCY_CONFLICT",
              "Idempotency key reused with different parameters",409);
      auto out=read_job_locked(owner,existing.text(0));out.set("reused",true);tx.commit();return out;
    }
  }
  compact_jobs_locked(owner,8);
  job_capacity_locked(owner);
  const auto now=clock_();
  require(now<=std::numeric_limits<std::int64_t>::max()-options.timeout_ms,"E_SCHEMA","Clock exceeds job deadline range");
  auto id=random_id();
  Sql job(db_,"INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at,created_delete_sequence,kind)VALUES(?,?,'',?,?,'queued',?,?,?,?,?,'web.read_many')");
  job.bind(1,id);job.bind(2,owner);job.bind(3,key);job.bind(4,fingerprint);job.bind(5,auth);
  job.bind(6,options.max_attempts);job.bind(7,now+options.timeout_ms);job.bind(8,now);job.bind(9,delete_sequence_locked());job.step();
  Sql budget(db_,"INSERT INTO read_job_budgets(job_id,max_requests,max_bytes,per_page_timeout_ms,last_dispatch_sequence)VALUES(?,?,?,?,?)");
  budget.bind(1,id);budget.bind(2,options.max_requests);budget.bind(3,options.max_bytes);budget.bind(4,options.per_page_timeout_ms);
  budget.bind(5,next_queue_sequence(db_));budget.step();
  for(std::size_t i=0;i<urls.size();++i) {
    Sql item(db_,"INSERT INTO read_job_items(job_id,position,url,deletion_generation)VALUES(?,?,?,?)");
    item.bind(1,id);item.bind(2,static_cast<std::int64_t>(i));item.bind(3,urls[i]);
    item.bind(4,deletion_generation_locked(owner,urls[i]));item.step();
  }
  attach_late_read_locked(owner,id);
  auto out=read_job_locked(owner,id);out.set("reused",false);tx.commit();return out;
}

Json Store::read_job_locked(const std::string& owner,const std::string& id) {
  if(auto archived=compacted_job_locked(owner,id))return *archived;
  Sql job(db_,"SELECT j.state,j.epoch,j.attempts,j.max_attempts,j.lease_until,j.deadline,j.error_code,b.max_requests,b.max_bytes,b.charged_requests,b.charged_bytes,b.per_page_timeout_ms FROM jobs j JOIN read_job_budgets b ON b.job_id=j.id WHERE j.id=? AND j.owner=? AND j.kind='web.read_many'");
  job.bind(1,id);job.bind(2,owner);require(job.step(),"E_NOT_FOUND","Job not found",404);
  Sql totals(db_,"SELECT COUNT(*),COALESCE(SUM(state='succeeded'),0),COALESCE(SUM(state='failed'),0),COALESCE(SUM(state='cancelled'),0),COALESCE(SUM(state='running'),0),COALESCE(SUM(external_requests),0),COALESCE(SUM(content_bytes),0),COALESCE(SUM(unknown_requests),0),COALESCE(SUM(unknown_bytes),0),COALESCE(SUM(request_grant),0),COALESCE(SUM(byte_grant),0) FROM read_job_items WHERE job_id=?");
  totals.bind(1,id);totals.step();
  Json out=Json::object();
  out.set("job_id",id);out.set("kind","web.read_many");out.set("state",job.text(0));
  out.set("lease_epoch",job.num(1));out.set("dispatches",job.num(2));out.set("max_attempts_per_item",job.num(3));
  out.set("lease_until_ms",job.num(4));out.set("deadline_ms",job.num(5));
  out.set("total_items",totals.num(0));out.set("succeeded_items",totals.num(1));out.set("failed_items",totals.num(2));
  out.set("cancelled_items",totals.num(3));out.set("running_items",totals.num(4));
  out.set("pending_items",totals.num(0)-totals.num(1)-totals.num(2)-totals.num(3)-totals.num(4));
  out.set("used_pages",totals.num(1));out.set("partial",totals.num(2)>0||totals.num(3)>0);
  out.set("error_code",job.text(6).empty()?Json():Json::string(job.text(6)));
  Json budget=Json::object();
  budget.set("max_requests",job.num(7));budget.set("max_bytes",job.num(8));
  budget.set("charged_requests",job.num(9));budget.set("charged_bytes",job.num(10));
  budget.set("remaining_requests",job.num(7)-job.num(9));budget.set("remaining_bytes",job.num(8)-job.num(10));
  budget.set("per_page_timeout_ms",job.num(11));out.set("budget",budget);
  Json usage=Json::object();
  usage.set("external_requests",totals.num(5));usage.set("content_bytes",totals.num(6));
  usage.set("unknown_requests_charged",totals.num(7));usage.set("unknown_bytes_charged",totals.num(8));
  usage.set("reserved_requests",totals.num(9));usage.set("reserved_bytes",totals.num(10));
  usage.set("model_calls",std::int64_t{0});usage.set("accounting","known_usage_plus_conservative_grants");
  usage.set("scope","job_lifetime");
  Sql shared(db_,R"SQL(SELECT COUNT(*),COALESCE(SUM(g.payer_job_id<>m.job_id),0),
 COALESCE(SUM(g.settled=0),0),COALESCE(SUM(g.settled<>0 AND g.external_requests<0),0),
 COALESCE(SUM(CASE WHEN g.settled<>0 AND g.external_requests>=0 THEN g.external_requests ELSE 0 END),0),
 COALESCE(SUM(CASE WHEN g.settled<>0 AND g.content_bytes>=0 THEN g.content_bytes ELSE 0 END),0)
 FROM read_shared_members m JOIN read_shared_attempts g ON g.attempt_id=m.attempt_id WHERE m.job_id=?)SQL");
  shared.bind(1,id);shared.step();
  auto sharing=Json::object();sharing.set("version","bzs-shared-read-1");
  sharing.set("attempts",shared.num(0));sharing.set("subscriber_attempts",shared.num(1));
  sharing.set("unsettled_attempts",shared.num(2));sharing.set("unknown_attempts",shared.num(3));
  sharing.set("associated_external_requests",shared.num(4));sharing.set("associated_content_bytes",shared.num(5));
  sharing.set("accounting","physical_usage_charged_once_to_original_payer;associated_usage_not_additive");
  out.set("shared_reads",sharing);
  out.set("usage",usage);out.set("external_requests",totals.num(5));
  return out;
}

void Store::settle_read_job_locked(const std::string& id) {
  Sql current(db_,"SELECT state FROM jobs WHERE id=? AND kind='web.read_many' AND archived_at=0");current.bind(1,id);
  if(!current.step())return;
  auto state=current.text(0);
  Sql counts(db_,"SELECT COALESCE(SUM(state='pending'),0),COALESCE(SUM(state='running'),0),COALESCE(SUM(state='succeeded'),0),COALESCE(SUM(state IN ('failed','cancelled')),0) FROM read_job_items WHERE job_id=?");
  counts.bind(1,id);counts.step();
  if(counts.num(1)>0)return;
  std::string next,error;
  if(state=="cancelling"||state=="cancelled")next="cancelled";
  else if(state=="pausing"||state=="paused")next="paused";
  else if(counts.num(0)>0)next="queued";
  else { next=counts.num(3)==0?"succeeded":"failed";if(counts.num(3)>0)error=counts.num(2)>0?"E_BATCH_PARTIAL":"E_BATCH_FAILED"; }
  Sql update(db_,"UPDATE jobs SET state=?,lease_until=0,used_pages=?,error_code=? WHERE id=?");
  update.bind(1,next);update.bind(2,counts.num(2));update.bind(3,error);update.bind(4,id);update.step();
}

bool Store::owns_read_lease_locked(const ReadLease& lease) {
  Sql item(db_,"SELECT 1 FROM read_job_items i JOIN jobs j ON j.id=i.job_id WHERE i.job_id=? AND i.position=? AND i.state='running' AND i.lease_epoch=? AND i.attempt_id=? AND i.url=? AND j.owner=? AND j.kind='web.read_many'");
  item.bind(1,lease.job_id);item.bind(2,lease.input_index);item.bind(3,lease.epoch);item.bind(4,lease.attempt_id);
  item.bind(5,lease.url);item.bind(6,lease.owner);return item.step();
}
bool Store::live_read_lease_locked(const ReadLease& lease) {
  if(!owns_read_lease_locked(lease))return false;
  Sql job(db_,"SELECT state,epoch,lease_until,deadline,auth_epoch FROM jobs WHERE id=? AND owner=?");
  job.bind(1,lease.job_id);job.bind(2,lease.owner);
  if(!job.step()||job.text(0)!="running"||job.num(1)!=lease.epoch||job.num(2)<=clock_()||job.num(3)<=clock_())return false;
  return job.num(4)==epoch_locked(lease.owner);
}
bool Store::read_lease_live(const ReadLease& lease) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  const bool live=!shared_read_members_locked(lease).empty()?shared_read_live_locked(lease):
    live_read_lease_locked(lease)&&deletion_generation_locked(lease.owner,lease.url)==lease.deletion_generation;
  if(!live)close_read_sharing_locked(lease);
  tx.commit();return live;
}
void Store::reconcile_read_usage_locked(const ReadLease& lease,std::int64_t requests,std::int64_t bytes) {
  Sql item(db_,"SELECT request_grant,byte_grant FROM read_job_items WHERE job_id=? AND position=? AND attempt_id=? AND state='running'");
  item.bind(1,lease.job_id);item.bind(2,lease.input_index);item.bind(3,lease.attempt_id);
  require(item.step()&&item.num(0)==lease.request_grant&&item.num(1)==lease.byte_grant,"E_LEASE_EXPIRED","Read grant is no longer current",409);
  const bool known=requests>=0&&bytes>=0;
  require(known?requests<=lease.request_grant&&bytes<=lease.byte_grant&&(requests!=0||bytes==0):requests==-1&&bytes==-1,
          "E_WORKER_PROTOCOL","Worker usage exceeds its durable grant",502);
  if(known) {
    Sql refund(db_,"UPDATE read_job_budgets SET charged_requests=charged_requests-?,charged_bytes=charged_bytes-? WHERE job_id=?");
    refund.bind(1,lease.request_grant-requests);refund.bind(2,lease.byte_grant-bytes);refund.bind(3,lease.job_id);refund.step();
    Sql used(db_,"UPDATE read_job_items SET external_requests=external_requests+?,content_bytes=content_bytes+?,request_grant=0,byte_grant=0 WHERE job_id=? AND position=?");
    used.bind(1,requests);used.bind(2,bytes);used.bind(3,lease.job_id);used.bind(4,lease.input_index);used.step();
  } else {
    Sql unknown(db_,"UPDATE read_job_items SET usage_unknown=1,unknown_requests=unknown_requests+request_grant,unknown_bytes=unknown_bytes+byte_grant,request_grant=0,byte_grant=0 WHERE job_id=? AND position=?");
    unknown.bind(1,lease.job_id);unknown.bind(2,lease.input_index);unknown.step();
  }
}

void Store::settle_read_jobs_locked() {
  Sql groups(db_,R"SQL(SELECT j.id,j.owner,i.url,i.attempt_id,i.lease_epoch,i.position,i.deletion_generation,
 i.request_grant,i.byte_grant,b.per_page_timeout_ms,j.deadline,i.publication_sequence
 FROM read_shared_attempts g JOIN read_job_items i ON i.job_id=g.payer_job_id AND i.position=g.payer_position
 JOIN jobs j ON j.id=i.job_id JOIN read_job_budgets b ON b.job_id=j.id
 WHERE g.settled=0 AND g.lease_until<=? AND i.state='running' AND i.attempt_id=g.attempt_id)SQL");
  groups.bind(1,clock_());std::vector<ReadLease> abandoned_groups;
  while(groups.step())abandoned_groups.push_back({groups.text(0),groups.text(1),groups.text(2),groups.text(3),groups.num(4),groups.num(5),groups.num(6),groups.num(7),groups.num(8),groups.num(9),groups.num(10),groups.num(11)});
  for(const auto& group:abandoned_groups)settle_shared_read_locked(group,"E_EXECUTION_UNKNOWN",-1,-1);
  Sql expired(db_,"SELECT j.id,j.owner,i.url,i.attempt_id,i.lease_epoch,i.position,i.deletion_generation,i.request_grant,i.byte_grant,j.deadline FROM jobs j JOIN read_job_items i ON i.job_id=j.id WHERE j.kind='web.read_many' AND i.state='running' AND j.lease_until<=?");
  expired.bind(1,clock_());
  std::vector<ReadLease> abandoned;
  while(expired.step())abandoned.push_back({expired.text(0),expired.text(1),expired.text(2),expired.text(3),expired.num(4),expired.num(5),expired.num(6),expired.num(7),expired.num(8),0,expired.num(9)});
  for(const auto& lease:abandoned) {
    reconcile_read_usage_locked(lease,-1,-1);
    Sql item(db_,"UPDATE read_job_items SET state='failed',error_code='E_EXECUTION_UNKNOWN' WHERE job_id=? AND position=?");
    item.bind(1,lease.job_id);item.bind(2,lease.input_index);item.step();
    settle_read_job_locked(lease.job_id);
  }
}

std::optional<ReadLease> Store::lease_next_read(std::stop_token stopping) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(stopping.stop_requested())return std::nullopt;
  settle_read_jobs_locked();
  for(;;) {
    if(stopping.stop_requested())return std::nullopt;
    Sql job(db_,"SELECT j.id,j.owner,j.epoch,j.deadline,j.auth_epoch,j.max_attempts,b.max_requests-b.charged_requests,b.max_bytes-b.charged_bytes,b.per_page_timeout_ms FROM jobs j JOIN read_job_budgets b ON b.job_id=j.id WHERE j.kind='web.read_many' AND j.state='queued' ORDER BY b.last_dispatch_sequence,j.created_at,j.id LIMIT 1");
    if(!job.step()){tx.commit();return std::nullopt;}
    const auto id=job.text(0),owner=job.text(1);const auto now=clock_();
    std::string stop;
    if(job.num(4)!=epoch_locked(owner))stop="E_POLICY_CHANGED";
    else if(job.num(3)<=now)stop="E_DEADLINE";
    else if(job.num(6)<=0)stop="E_REQUEST_BUDGET";
    else if(job.num(7)<=0)stop="E_BYTE_BUDGET";
    if(!stop.empty()) {
      Sql items(db_,"UPDATE read_job_items SET state='failed',error_code=? WHERE job_id=? AND state='pending'");
      items.bind(1,stop);items.bind(2,id);items.step();settle_read_job_locked(id);continue;
    }
    Sql item(db_,"SELECT position,url,deletion_generation,attempts FROM read_job_items WHERE job_id=? AND state='pending' ORDER BY position LIMIT 1");
    item.bind(1,id);
    if(!item.step()){settle_read_job_locked(id);continue;}
    if(item.num(3)>=job.num(5)||deletion_generation_locked(owner,item.text(1))!=item.num(2)) {
      Sql denied(db_,"UPDATE read_job_items SET state='failed',error_code=? WHERE job_id=? AND position=?");
      denied.bind(1,std::string(item.num(3)>=job.num(5)?"E_ATTEMPT_BUDGET":"E_TARGET_DELETED"));
      denied.bind(2,id);denied.bind(3,item.num(0));denied.step();settle_read_job_locked(id);continue;
    }
    require(job.num(2)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Lease generation exhausted",500);
    const auto timeout=std::min(job.num(8),job.num(3)-now);
    ReadLease lease{id,owner,item.text(1),random_id(),job.num(2)+1,item.num(0),item.num(2),
                    std::min(per_item_requests,job.num(6)),std::min(per_item_bytes,job.num(7)),timeout,job.num(3),publication_sequence_locked()};
    if(stopping.stop_requested())return std::nullopt;
    Sql reserve(db_,"UPDATE read_job_budgets SET charged_requests=charged_requests+?,charged_bytes=charged_bytes+?,last_dispatch_sequence=? WHERE job_id=?");
    reserve.bind(1,lease.request_grant);reserve.bind(2,lease.byte_grant);reserve.bind(3,next_queue_sequence(db_));reserve.bind(4,id);reserve.step();
    Sql current(db_,"SELECT current_snapshot FROM documents WHERE owner=? AND url=?");current.bind(1,owner);current.bind(2,lease.url);const auto snapshot=current.step()?current.text(0):"";
    Sql claim(db_,"UPDATE read_job_items SET state='running',attempts=attempts+1,lease_epoch=?,attempt_id=?,request_grant=?,byte_grant=?,error_code=NULL,publication_sequence=?,publication_snapshot_id=?,dispatch_timeout_ms=?,sharing_closed=0 WHERE job_id=? AND position=?");
    claim.bind(1,lease.epoch);claim.bind(2,lease.attempt_id);claim.bind(3,lease.request_grant);claim.bind(4,lease.byte_grant);claim.bind(5,lease.publication_sequence);claim.bind(6,snapshot);claim.bind(7,lease.timeout_ms);claim.bind(8,id);claim.bind(9,lease.input_index);claim.step();
    Sql activate(db_,"UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,lease_until=?,error_code=NULL WHERE id=?");
    // Bound the duration before adding to the clock (also valid close to INT64_MAX).
    activate.bind(1,lease.epoch);activate.bind(2,now+std::min(job.num(3)-now,timeout+5000));activate.bind(3,id);activate.step();
    attach_shared_reads_locked(lease);
    tx.commit();return lease;
  }
}

bool Store::complete_read(const ReadLease& lease,const SourceDocument& input,ParsedDocument parsed,
                          const Json& meta,std::int64_t requests,std::int64_t bytes) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(!owns_read_lease_locked(lease)){tx.commit();return false;}
  const auto members=shared_read_members_locked(lease);
  if(!members.empty()) {
    const ReadLease* publisher=nullptr;
    for(const auto& member:members)if(live_read_lease_locked(member)&&
        deletion_generation_locked(member.owner,member.url)==member.deletion_generation) { publisher=&member;break; }
    if(!publisher) {
      settle_shared_read_locked(lease,"E_LEASE_EXPIRED",requests,bytes);tx.commit();return false;
    }
    require(input.url==lease.url,"E_WORKER_PROTOCOL","Read result identity differs from its lease",502);
    Sql captured(db_,"SELECT j.created_delete_sequence,j.auth_epoch,i.publication_sequence,i.publication_snapshot_id FROM jobs j JOIN read_job_items i ON i.job_id=j.id WHERE j.id=? AND i.position=?");
    captured.bind(1,publisher->job_id);captured.bind(2,publisher->input_index);captured.step();
    auto read_meta=Json::parse(meta.dump()),fence=Json::object();fence.set("epoch",captured.num(1));
    fence.set("sequence",captured.num(0));fence.set("generation",publisher->deletion_generation);
    if(captured.text(3)!="@legacy")fence.set("snapshot_id",captured.text(3));
    read_meta.set("publication_fence",fence);read_meta.set("page_publication_sequence",captured.num(2));
    auto snapshot=ingest_web_locked(publisher->owner,input,std::move(parsed),read_meta,publisher->deletion_generation);
    settle_shared_read_locked(lease,"",requests,bytes,&snapshot);tx.commit();return true;
  }
  if(!live_read_lease_locked(lease)) {
    reconcile_read_usage_locked(lease,requests,bytes);
    Sql reject(db_,"UPDATE read_job_items SET state='failed',error_code='E_LEASE_EXPIRED' WHERE job_id=? AND position=?");
    reject.bind(1,lease.job_id);reject.bind(2,lease.input_index);reject.step();settle_read_job_locked(lease.job_id);tx.commit();return false;
  }
  require(input.url==lease.url,"E_WORKER_PROTOCOL","Read result identity differs from its lease",502);
  Sql captured(db_,"SELECT j.created_delete_sequence,j.auth_epoch,i.publication_sequence,i.publication_snapshot_id FROM jobs j JOIN read_job_items i ON i.job_id=j.id WHERE j.id=? AND i.position=?");
  captured.bind(1,lease.job_id);captured.bind(2,lease.input_index);captured.step();
  auto read_meta=Json::parse(meta.dump()),fence=Json::object();fence.set("epoch",captured.num(1));fence.set("sequence",captured.num(0));fence.set("generation",lease.deletion_generation);
  if(captured.text(3)!="@legacy")fence.set("snapshot_id",captured.text(3));
  read_meta.set("publication_fence",fence);read_meta.set("page_publication_sequence",captured.num(2));
  auto snapshot=ingest_web_locked(lease.owner,input,std::move(parsed),read_meta,lease.deletion_generation);
  require(live_read_lease_locked(lease),"E_LEASE_EXPIRED","Lease/deadline expired before commit",409);
  reconcile_read_usage_locked(lease,requests,bytes);
  Sql done(db_,"UPDATE read_job_items SET state='succeeded',snapshot_id=?,receipt_json=?,error_code=NULL WHERE job_id=? AND position=?");
  done.bind(1,snapshot.at("snapshot_id").str());done.bind(2,receipt(snapshot).dump());done.bind(3,lease.job_id);done.bind(4,lease.input_index);done.step();
  settle_read_job_locked(lease.job_id);tx.commit();return true;
}

bool Store::complete_read_transient(const ReadLease& lease,std::int64_t requests,std::int64_t bytes,
                                    const Json* publication,const std::string& final_url,const Json* redirect_observations) {
  // A no-store body settles once without any snapshot row: state='succeeded',
  // NULL snapshot_id and a delivered-transient receipt distinguish it from a
  // retention-deleted receipt, so retry_failed never reruns it.
  auto marker=Json::object();marker.set("transient","no_store");
  const auto encoded=marker.dump();
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(!owns_read_lease_locked(lease)){tx.commit();return false;}
  const auto members=shared_read_members_locked(lease);
  if(!members.empty()) {
    bool any=false;
    for(const auto& member:members)any=any||(live_read_lease_locked(member)&&
      deletion_generation_locked(member.owner,member.url)==member.deletion_generation);
    require(any,"E_LEASE_EXPIRED","Shared read has no live subscriber at commit",409);
    require(deletion_generation_locked(lease.owner,lease.url)==lease.deletion_generation,"E_TARGET_DELETED",
            "Document was deleted after this read started",409);
    if((publication||redirect_observations)&&requests>0) {
      Sql created(db_,"SELECT created_delete_sequence FROM jobs WHERE id=?");created.bind(1,lease.job_id);created.step();
      const auto apply=[&](const std::string& target,const Json& policy) {
        bool live=true;try { (void)alias_fences_locked(lease.owner,lease.url,target,created.num(0)); } catch(const Error& e) { if(e.code!="E_TARGET_DELETED")throw;live=false; }
        if(live)observe_policy_locked(lease.owner,lease.url,target,policy);
      };
      if(publication)apply(final_url,*publication);
      if(redirect_observations)for(std::size_t i=0;i<redirect_observations->size();++i) {
        const auto observation=redirect_observations->at(i);apply(observation.at("url").str(),observation.at("publication"));
      }
    }
    reconcile_read_usage_locked(lease,requests,bytes);
    for(const auto& member:members) {
      if(member.job_id!=lease.job_id)reconcile_read_usage_locked(member,0,0);
      Sql done(db_,"UPDATE read_job_items SET state='succeeded',snapshot_id=NULL,receipt_json=?,error_code=NULL WHERE job_id=? AND position=?");
      done.bind(1,encoded);done.bind(2,member.job_id);done.bind(3,member.input_index);done.step();
    }
    Sql settled(db_,"UPDATE read_shared_attempts SET settled=1,external_requests=?,content_bytes=?,error_code='' WHERE attempt_id=? AND settled=0");
    settled.bind(1,requests);settled.bind(2,bytes);settled.bind(3,lease.attempt_id);settled.step();
    for(const auto& member:members)settle_read_job_locked(member.job_id);
    tx.commit();return true;
  }
  if(!live_read_lease_locked(lease)) {
    reconcile_read_usage_locked(lease,requests,bytes);
    Sql reject(db_,"UPDATE read_job_items SET state='failed',error_code='E_LEASE_EXPIRED' WHERE job_id=? AND position=?");
    reject.bind(1,lease.job_id);reject.bind(2,lease.input_index);reject.step();settle_read_job_locked(lease.job_id);tx.commit();return false;
  }
  require(deletion_generation_locked(lease.owner,lease.url)==lease.deletion_generation,"E_TARGET_DELETED",
          "Document was deleted after this read started",409);
  if((publication||redirect_observations)&&requests>0) {
    Sql created(db_,"SELECT created_delete_sequence FROM jobs WHERE id=?");created.bind(1,lease.job_id);created.step();
    const auto apply=[&](const std::string& target,const Json& policy) {
      bool live=true;try { (void)alias_fences_locked(lease.owner,lease.url,target,created.num(0)); } catch(const Error& e) { if(e.code!="E_TARGET_DELETED")throw;live=false; }
      if(live)observe_policy_locked(lease.owner,lease.url,target,policy);
    };
    if(publication)apply(final_url,*publication);
    if(redirect_observations)for(std::size_t i=0;i<redirect_observations->size();++i) {
      const auto observation=redirect_observations->at(i);apply(observation.at("url").str(),observation.at("publication"));
    }
  }
  reconcile_read_usage_locked(lease,requests,bytes);
  Sql done(db_,"UPDATE read_job_items SET state='succeeded',snapshot_id=NULL,receipt_json=?,error_code=NULL WHERE job_id=? AND position=?");
  done.bind(1,encoded);done.bind(2,lease.job_id);done.bind(3,lease.input_index);done.step();
  settle_read_job_locked(lease.job_id);tx.commit();return true;
}

void Store::fail_read(const ReadLease& lease,const std::string& error,std::int64_t requests,std::int64_t bytes,const Json* publication,const std::string& final_url,const Json* redirect_observations) {
  require_identifier(error);
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(owns_read_lease_locked(lease)) {
    const auto members=shared_read_members_locked(lease);
    const ReadLease* observer=nullptr;
    if(members.empty()) {
      if(live_read_lease_locked(lease)&&deletion_generation_locked(lease.owner,lease.url)==lease.deletion_generation)observer=&lease;
    } else for(const auto& member:members)if(live_read_lease_locked(member)&&
        deletion_generation_locked(member.owner,member.url)==member.deletion_generation) { observer=&member;break; }
    if((publication||redirect_observations)&&requests>0&&observer) {
      Sql created(db_,"SELECT created_delete_sequence FROM jobs WHERE id=?");created.bind(1,observer->job_id);created.step();
      const auto apply=[&](const std::string& target,const Json& policy) {
        bool live=true;try { (void)alias_fences_locked(lease.owner,lease.url,target,created.num(0)); } catch(const Error& e) { if(e.code!="E_TARGET_DELETED")throw;live=false; }
        if(live)observe_policy_locked(lease.owner,lease.url,target,policy);
      };
      if(publication)apply(final_url,*publication);
      if(redirect_observations)for(std::size_t i=0;i<redirect_observations->size();++i) {
        const auto observation=redirect_observations->at(i);apply(observation.at("url").str(),observation.at("publication"));
      }
    }
    if(!members.empty())settle_shared_read_locked(lease,error,requests,bytes);
    else {
      reconcile_read_usage_locked(lease,requests,bytes);
      Sql failed(db_,"UPDATE read_job_items SET state='failed',error_code=? WHERE job_id=? AND position=?");
      failed.bind(1,error);failed.bind(2,lease.job_id);failed.bind(3,lease.input_index);failed.step();
      settle_read_job_locked(lease.job_id);
    }
  }
  tx.commit();
}

Json Store::control_read_locked(const std::string& owner,const std::string& id,const std::string& action) {
  auto current=read_job_locked(owner,id);auto state=current.at("state").str();
  std::string next;
  if(action=="pause") {
    if(state=="queued")next="paused";
    else if(state=="running")next="pausing";
    else if(state=="paused"||state=="pausing")next=state;
  } else if(action=="resume"&&state=="paused")next="queued";
  else if(action=="cancel") {
    if(state=="queued"||state=="paused")next="cancelled";
    else if(state=="running"||state=="pausing")next="cancelling";
    else if(state=="cancelled"||state=="cancelling")next=state;
  } else if(action=="retry_failed"&&state=="failed") {
    require(current.at("deadline_ms").num()>clock_(),"E_DEADLINE","Job deadline has expired",409);
    Sql retryable(db_,"SELECT COUNT(*) FROM read_job_items i JOIN jobs j ON j.id=i.job_id WHERE i.job_id=? AND i.state='failed' AND i.attempts<j.max_attempts AND i.error_code NOT IN ('E_TARGET_DELETED','E_POLICY_CHANGED','E_REQUEST_BUDGET','E_BYTE_BUDGET','E_DEADLINE')");
    retryable.bind(1,id);retryable.step();require(retryable.num(0)>0,"E_ATTEMPT_BUDGET","No failed items have another authorized attempt",409);
    Sql reset(db_,"UPDATE read_job_items SET state='pending',error_code=NULL WHERE job_id=? AND state='failed' AND attempts<(SELECT max_attempts FROM jobs WHERE id=?) AND error_code NOT IN ('E_TARGET_DELETED','E_POLICY_CHANGED','E_REQUEST_BUDGET','E_BYTE_BUDGET','E_DEADLINE')");
    reset.bind(1,id);reset.bind(2,id);reset.step();next="queued";
  }
  require(!next.empty(),"E_JOB_STATE","Action is not valid for this job state",409);
  if(action=="cancel") {
    Sql cancel(db_,"UPDATE read_job_items SET state='cancelled',error_code='E_CANCELLED' WHERE job_id=? AND state='pending'");
    cancel.bind(1,id);cancel.step();
  }
  if(next!=state) {
    Sql update(db_,"UPDATE jobs SET state=?,epoch=epoch+1,error_code=NULL WHERE id=?");
    update.bind(1,next);update.bind(2,id);update.step();
  }
  settle_read_job_locked(id);
  return read_job_locked(owner,id);
}

Json Store::job_results(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit) {
  std::lock_guard lock(mutex_);
  Transaction transaction(db_);refresh_site_publication_locked();
  const auto retained=job_locked(owner,id);
  require(!retained.has("retention"),"E_JOB_RESULTS_EXPIRED","Job details have expired; use job.get for the permanent summary",410);
  auto kind=retained.at("kind").str();
  if(kind=="web.map") { auto result=map_results_locked(owner,id,offset,limit);transaction.commit();return result; }
  if(kind=="web.crawl") { auto result=crawl_results_locked(owner,id,offset,limit);transaction.commit();return result; }
  require(offset>=0&&offset<=20&&limit>=1&&limit<=20,"E_SCHEMA","Invalid job result window");
  auto job=read_job_locked(owner,id);
  Sql rows(db_,"SELECT position,url,state,attempts,external_requests,content_bytes,unknown_requests,unknown_bytes,usage_unknown,snapshot_id,receipt_json,error_code,attempt_id FROM read_job_items WHERE job_id=? AND position>=? ORDER BY position LIMIT ?");
  rows.bind(1,id);rows.bind(2,offset);rows.bind(3,limit);
  Json items=Json::array();auto next=offset;
  while(rows.step()) {
    Json item=Json::object();item.set("input_index",rows.num(0));item.set("url",rows.text(1));
    item.set("state",rows.text(2));item.set("attempts",rows.num(3));item.set("external_requests",rows.num(4));
    item.set("content_bytes",rows.num(5));item.set("unknown_requests_charged",rows.num(6));item.set("unknown_bytes_charged",rows.num(7));
    item.set("usage_unknown",rows.num(8)!=0);item.set("error_code",rows.text(11).empty()?Json():Json::string(rows.text(11)));
    Sql shared(db_,"SELECT payer_job_id,payer_position,member_count,settled,external_requests,content_bytes FROM read_shared_attempts WHERE attempt_id=? AND owner=?");
    shared.bind(1,rows.text(12));shared.bind(2,owner);
    if(shared.step()) {
      auto sharing=Json::object();sharing.set("attempt_id",rows.text(12));sharing.set("payer_job_id",shared.text(0));
      sharing.set("payer_input_index",shared.num(1));sharing.set("members",shared.num(2));sharing.set("settled",shared.num(3)!=0);
      sharing.set("role",shared.text(0)==id?"payer":"subscriber");
      sharing.set("associated_external_requests",shared.num(4)>=0?Json::integer(shared.num(4)):Json());
      sharing.set("associated_content_bytes",shared.num(5)>=0?Json::integer(shared.num(5)):Json());
      sharing.set("usage_unknown",shared.num(3)!=0&&shared.num(4)<0);item.set("shared_read",sharing);
    }
    if(rows.text(2)=="succeeded") {
      Sql visible(db_,"SELECT s.index_allowed,bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,'') FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE s.id=? AND d.owner=? AND d.deleted=0");
      visible.bind(1,rows.text(9));visible.bind(2,owner);
      const bool available=!rows.text(9).empty()&&visible.step();
      item.set("available",available);
      if(available&&!rows.text(10).empty()) {
        auto receipt=Json::parse(rows.text(10));const auto mode=visible.num(1);
        receipt.set("indexed",receipt.at("indexed").flag()&&visible.num(0)!=0&&mode<2);
        auto deployment=Json::object();deployment.set("version","bzs-site-publication-1");deployment.set("mode",mode==2?"withdrawn":mode==1?"links_only":"allowed");deployment.set("generation",site_publication_cached_generation_);
        receipt.set("site_publication",deployment);item.set("receipt",receipt);
      } else if(!available&&rows.text(9).empty()&&!rows.text(10).empty()) {
        // Succeeded with a NULL snapshot: a delivered-transient receipt is the
        // only observable marker and never names a snapshot identity. A
        // receipt whose snapshot was removed by retention keeps its non-empty
        // snapshot_id and stays hidden.
        auto receipt=Json::parse(rows.text(10));
        if(receipt.is_object()&&receipt.has("transient")&&receipt.at("transient").str()=="no_store")
          item.set("receipt",receipt);
      }
    }
    items.push(item);next=rows.num(0)+1;
  }
  Json out=Json::object();out.set("job_id",id);out.set("state",job.at("state"));out.set("partial",job.at("partial"));
  out.set("items",items);out.set("total_items",job.at("total_items"));out.set("next_offset",next);
  out.set("has_more",next<job.at("total_items").num());transaction.commit();return out;
}

std::string Store::active_read_attempt(const std::string& owner,const std::string& id) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  Sql item(db_,"SELECT i.attempt_id,i.position,i.lease_epoch FROM read_job_items i JOIN jobs j ON j.id=i.job_id WHERE j.id=? AND j.owner=? AND i.state='running'");
  item.bind(1,id);item.bind(2,owner);
  if(!item.step()){tx.commit();return "";}
  const auto attempt=item.text(0);
  ReadLease payer;payer.job_id=id;payer.input_index=item.num(1);payer.epoch=item.num(2);
  payer.owner=owner;payer.attempt_id=attempt;
  Sql group(db_,"SELECT payer_job_id,payer_position,payer_epoch FROM read_shared_attempts WHERE attempt_id=? AND owner=? AND settled=0");
  group.bind(1,attempt);group.bind(2,owner);
  if(group.step()) {
    payer.job_id=group.text(0);payer.input_index=group.num(1);payer.epoch=group.num(2);
    if(shared_read_live_locked(payer)){tx.commit();return "";}
  }
  // Persist the decision before the caller sends fetch.cancel outside this
  // transaction. Late admission cannot reopen it, even after a clock rollback.
  close_read_sharing_locked(payer);tx.commit();return attempt;
}
}
