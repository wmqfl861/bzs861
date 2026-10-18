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
  Json receipt(const Json& snapshot) {
    Json out=Json::object();
    for(const auto* key:{"document_id","snapshot_id","extraction_id","revision","indexed"})out.set(key,snapshot.at(key));
    return out;
  }
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
  Sql count(db_,"SELECT COUNT(*) FROM jobs WHERE owner=?");count.bind(1,owner);count.step();
  require(count.num(0)<10000,"E_QUOTA","Job retention quota reached",429);
  const auto now=clock_();
  require(now<=std::numeric_limits<std::int64_t>::max()-options.timeout_ms,"E_SCHEMA","Clock exceeds job deadline range");
  auto id=random_id();
  Sql job(db_,"INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at,created_delete_sequence,kind)VALUES(?,?,'',?,?,'queued',?,?,?,?,?,'web.read_many')");
  job.bind(1,id);job.bind(2,owner);job.bind(3,key);job.bind(4,fingerprint);job.bind(5,auth);
  job.bind(6,options.max_attempts);job.bind(7,now+options.timeout_ms);job.bind(8,now);job.bind(9,delete_sequence_locked());job.step();
  Sql budget(db_,"INSERT INTO read_job_budgets(job_id,max_requests,max_bytes,per_page_timeout_ms)VALUES(?,?,?,?)");
  budget.bind(1,id);budget.bind(2,options.max_requests);budget.bind(3,options.max_bytes);budget.bind(4,options.per_page_timeout_ms);budget.step();
  for(std::size_t i=0;i<urls.size();++i) {
    Sql item(db_,"INSERT INTO read_job_items(job_id,position,url,deletion_generation)VALUES(?,?,?,?)");
    item.bind(1,id);item.bind(2,static_cast<std::int64_t>(i));item.bind(3,urls[i]);
    item.bind(4,deletion_generation_locked(owner,urls[i]));item.step();
  }
  auto out=read_job_locked(owner,id);out.set("reused",false);tx.commit();return out;
}

Json Store::read_job_locked(const std::string& owner,const std::string& id) {
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
  out.set("usage",usage);out.set("external_requests",totals.num(5));
  return out;
}

void Store::settle_read_job_locked(const std::string& id) {
  Sql current(db_,"SELECT state FROM jobs WHERE id=? AND kind='web.read_many'");current.bind(1,id);
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
  std::lock_guard lock(mutex_);
  return live_read_lease_locked(lease)&&deletion_generation_locked(lease.owner,lease.url)==lease.deletion_generation;
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

std::optional<ReadLease> Store::lease_next_read() {
  std::lock_guard lock(mutex_);Transaction tx(db_);settle_read_jobs_locked();
  for(;;) {
    Sql job(db_,"SELECT j.id,j.owner,j.epoch,j.deadline,j.auth_epoch,j.max_attempts,b.max_requests-b.charged_requests,b.max_bytes-b.charged_bytes,b.per_page_timeout_ms FROM jobs j JOIN read_job_budgets b ON b.job_id=j.id WHERE j.kind='web.read_many' AND j.state='queued' ORDER BY j.created_at,j.id LIMIT 1");
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
                    std::min(per_item_requests,job.num(6)),std::min(per_item_bytes,job.num(7)),timeout,job.num(3)};
    Sql reserve(db_,"UPDATE read_job_budgets SET charged_requests=charged_requests+?,charged_bytes=charged_bytes+? WHERE job_id=?");
    reserve.bind(1,lease.request_grant);reserve.bind(2,lease.byte_grant);reserve.bind(3,id);reserve.step();
    Sql claim(db_,"UPDATE read_job_items SET state='running',attempts=attempts+1,lease_epoch=?,attempt_id=?,request_grant=?,byte_grant=?,error_code=NULL WHERE job_id=? AND position=?");
    claim.bind(1,lease.epoch);claim.bind(2,lease.attempt_id);claim.bind(3,lease.request_grant);claim.bind(4,lease.byte_grant);claim.bind(5,id);claim.bind(6,lease.input_index);claim.step();
    Sql activate(db_,"UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,lease_until=?,error_code=NULL WHERE id=?");
    // Bound the duration before adding to the clock (also valid close to INT64_MAX).
    activate.bind(1,lease.epoch);activate.bind(2,now+std::min(job.num(3)-now,timeout+5000));activate.bind(3,id);activate.step();
    tx.commit();return lease;
  }
}

bool Store::complete_read(const ReadLease& lease,const SourceDocument& input,ParsedDocument parsed,
                          const Json& meta,std::int64_t requests,std::int64_t bytes) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(!owns_read_lease_locked(lease)){tx.commit();return false;}
  if(!live_read_lease_locked(lease)) {
    reconcile_read_usage_locked(lease,requests,bytes);
    Sql reject(db_,"UPDATE read_job_items SET state='failed',error_code='E_LEASE_EXPIRED' WHERE job_id=? AND position=?");
    reject.bind(1,lease.job_id);reject.bind(2,lease.input_index);reject.step();settle_read_job_locked(lease.job_id);tx.commit();return false;
  }
  require(input.url==lease.url,"E_WORKER_PROTOCOL","Read result identity differs from its lease",502);
  auto snapshot=ingest_web_locked(lease.owner,input,std::move(parsed),meta,lease.deletion_generation);
  require(live_read_lease_locked(lease),"E_LEASE_EXPIRED","Lease/deadline expired before commit",409);
  reconcile_read_usage_locked(lease,requests,bytes);
  Sql done(db_,"UPDATE read_job_items SET state='succeeded',snapshot_id=?,receipt_json=?,error_code=NULL WHERE job_id=? AND position=?");
  done.bind(1,snapshot.at("snapshot_id").str());done.bind(2,receipt(snapshot).dump());done.bind(3,lease.job_id);done.bind(4,lease.input_index);done.step();
  settle_read_job_locked(lease.job_id);tx.commit();return true;
}

void Store::fail_read(const ReadLease& lease,const std::string& error,std::int64_t requests,std::int64_t bytes) {
  require_identifier(error);
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(owns_read_lease_locked(lease)) {
    reconcile_read_usage_locked(lease,requests,bytes);
    Sql failed(db_,"UPDATE read_job_items SET state='failed',error_code=? WHERE job_id=? AND position=?");
    failed.bind(1,error);failed.bind(2,lease.job_id);failed.bind(3,lease.input_index);failed.step();
    settle_read_job_locked(lease.job_id);
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
  auto kind=job_locked(owner,id).at("kind").str();
  if(kind=="web.map")return map_results_locked(owner,id,offset,limit);
  require(offset>=0&&offset<=20&&limit>=1&&limit<=20,"E_SCHEMA","Invalid job result window");
  auto job=read_job_locked(owner,id);
  Sql rows(db_,"SELECT position,url,state,attempts,external_requests,content_bytes,unknown_requests,unknown_bytes,usage_unknown,snapshot_id,receipt_json,error_code FROM read_job_items WHERE job_id=? AND position>=? ORDER BY position LIMIT ?");
  rows.bind(1,id);rows.bind(2,offset);rows.bind(3,limit);
  Json items=Json::array();auto next=offset;
  while(rows.step()) {
    Json item=Json::object();item.set("input_index",rows.num(0));item.set("url",rows.text(1));
    item.set("state",rows.text(2));item.set("attempts",rows.num(3));item.set("external_requests",rows.num(4));
    item.set("content_bytes",rows.num(5));item.set("unknown_requests_charged",rows.num(6));item.set("unknown_bytes_charged",rows.num(7));
    item.set("usage_unknown",rows.num(8)!=0);item.set("error_code",rows.text(11).empty()?Json():Json::string(rows.text(11)));
    if(rows.text(2)=="succeeded") {
      Sql visible(db_,"SELECT 1 FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE s.id=? AND d.owner=? AND d.deleted=0");
      visible.bind(1,rows.text(9));visible.bind(2,owner);
      const bool available=!rows.text(9).empty()&&visible.step();
      item.set("available",available);
      if(available&&!rows.text(10).empty())item.set("receipt",Json::parse(rows.text(10)));
    }
    items.push(item);next=rows.num(0)+1;
  }
  Json out=Json::object();out.set("job_id",id);out.set("state",job.at("state"));out.set("partial",job.at("partial"));
  out.set("items",items);out.set("total_items",job.at("total_items"));out.set("next_offset",next);
  out.set("has_more",next<job.at("total_items").num());return out;
}

std::string Store::active_read_attempt(const std::string& owner,const std::string& id) {
  std::lock_guard lock(mutex_);
  Sql item(db_,"SELECT i.attempt_id FROM read_job_items i JOIN jobs j ON j.id=i.job_id WHERE j.id=? AND j.owner=? AND i.state='running'");
  item.bind(1,id);item.bind(2,owner);return item.step()?item.text(0):"";
}
}
