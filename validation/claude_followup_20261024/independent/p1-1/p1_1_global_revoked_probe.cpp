#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
using namespace bzs;
namespace {
int checks=0;
void check(bool yes,const char* message) { ++checks;if(!yes)throw std::runtime_error(message); }
void sql(sqlite3* db,const std::string& q) {char* detail=nullptr;const int rc=sqlite3_exec(db,q.c_str(),nullptr,nullptr,&detail);std::string error=detail?detail:"";sqlite3_free(detail);if(rc!=SQLITE_OK)throw std::runtime_error("probe SQL: "+error);}
std::string value(sqlite3* db,const std::string& q) {
 sqlite3_stmt* row=nullptr;check(sqlite3_prepare_v2(db,q.c_str(),-1,&row,nullptr)==SQLITE_OK,"prepare observation");check(sqlite3_step(row)==SQLITE_ROW,"read observation");
 const auto* p=sqlite3_column_text(row,0);std::string s=p?std::string(reinterpret_cast<const char*>(p),static_cast<std::size_t>(sqlite3_column_bytes(row,0))):"";sqlite3_finalize(row);return s;
}
long long number(sqlite3* db,const std::string& q) {return std::stoll(value(db,q));}
void rejects(const std::function<void()>& f,const char* expected) {try{f();}catch(const Error& e){check(e.code==expected,"unexpected error code");return;}throw std::runtime_error("expected refusal absent");}
std::string identity_digest(sqlite3* db) {
 sqlite3_stmt* rows=nullptr;
 const char* q="SELECT id,owner,fixture_id,idempotency_key,fingerprint,kind,state,auth_epoch,epoch,attempts,max_attempts,used_pages,deadline,created_at FROM jobs WHERE owner LIKE 'history-%' ORDER BY id";
 check(sqlite3_prepare_v2(db,q,-1,&rows,nullptr)==SQLITE_OK,"prepare immutable identities");std::string all;int rc;
 while((rc=sqlite3_step(rows))==SQLITE_ROW)for(int i=0;i<14;++i){const auto* p=sqlite3_column_text(rows,i);const auto n=sqlite3_column_bytes(rows,i);all+=std::to_string(n)+":";if(p)all.append(reinterpret_cast<const char*>(p),static_cast<std::size_t>(n));all+='\n';}
 sqlite3_finalize(rows);check(rc==SQLITE_DONE,"finish immutable identities");return sha256(all);
}
}
int main() {
 try {
  const std::int64_t started=1800000000000,week=7LL*24*3600*1000;std::int64_t now=started;Store store(":memory:",[&]{return now;});auto* db=store.raw_handle();std::string running;
  for(int owner_no=0;owner_no<5;++owner_no) {
   const auto owner="history-"+std::to_string(owner_no);
   const auto id=store.job_create(owner,"fixture","real-terminal",1,1000).at("job_id").str();const auto lease=store.lease_next();check(lease&&lease->id==id,"lease real fixture");
   const SourceDocument doc{"https://history.example/"+owner,"private marker "+owner,"text/plain","private fixture"};
   check(store.complete(*lease,doc,parse_document(doc.raw,doc.content_type)),"complete real fixture");
   int copies=9999;
   if(owner_no==0) {
    ReadManyOptions options;options.max_requests=1;options.max_bytes=4096;options.timeout_ms=60000;options.per_page_timeout_ms=1000;
    running=store.read_many_submit(owner,{"https://read.example/unsettled"},"unsettled",options).at("job_id").str();const auto read=store.lease_next_read();check(read&&read->job_id==running,"reserve real read grant");--copies;
   }
   // Cardinality fixture only: clone the actual completed public-API row with
   // unique identities, preserving its genuine terminal timestamps/counters.
   sql(db,"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<"+std::to_string(copies)+") INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,epoch,attempts,max_attempts,lease_until,deadline,created_at,used_pages,result_json,error_code,kind,created_delete_sequence) SELECT '"+owner+"-copy-'||x,owner,fixture_id,'copy-'||x,fingerprint,state,auth_epoch,epoch,attempts,max_attempts,lease_until,deadline,created_at,used_pages,result_json,error_code,kind,created_delete_sequence FROM jobs,n WHERE id='"+id+"'");
   check(number(db,"SELECT COUNT(*) FROM jobs WHERE owner='"+owner+"'")==10000,"per-owner history fixture cardinality");
   store.revoke_principal("admin",owner);
   rejects([&]{store.compact_jobs(owner);},"E_PRINCIPAL_REVOKED");
  }
  check(number(db,"SELECT COUNT(*) FROM jobs")==50000,"global history cardinality");
  check(number(db,"SELECT COUNT(*) FROM jobs WHERE archived_at<>0")==0,"premature history compaction");
  check(number(db,"SELECT COUNT(*) FROM principal_revocations")==5,"revocation identity count");
  const auto identity_before=identity_digest(db);
  const auto grant_before=value(db,"SELECT json_object('charged_requests',b.charged_requests,'charged_bytes',b.charged_bytes,'request_grant',i.request_grant,'byte_grant',i.byte_grant,'state',i.state,'unknown_requests',i.unknown_requests) FROM read_job_budgets b JOIN read_job_items i ON i.job_id=b.job_id WHERE b.job_id='"+running+"'");
  const auto snapshots_before=value(db,"SELECT json_group_array(json_object('id',id,'text',canonical_text,'raw',raw_content)) FROM snapshots ORDER BY id");
  now=started+week+999;rejects([&]{store.job_create("new-owner","fixture","before-window",1,1000);},"E_QUOTA");
  check(number(db,"SELECT COUNT(*) FROM jobs WHERE archived_at<>0")==0,"seven-day boundary bypassed");
  ++now;const auto admitted=store.job_create("new-owner","fixture","after-window",1,1000);
  check(!admitted.at("reused").flag(),"new owner was not admitted");
  const auto archived=number(db,"SELECT COUNT(*) FROM jobs WHERE archived_at<>0");check(archived>=1&&archived<=8,"global compaction exceeded bounded batch");
  check(number(db,"SELECT COUNT(*) FROM jobs")==50001,"permanent identity row was deleted");
  check(number(db,"SELECT COUNT(*) FROM jobs WHERE archived_at=0")<=50000,"active capacity exceeded");
  check(identity_digest(db)==identity_before,"historical identity or accounting fields changed");
  check(value(db,"SELECT json_object('charged_requests',b.charged_requests,'charged_bytes',b.charged_bytes,'request_grant',i.request_grant,'byte_grant',i.byte_grant,'state',i.state,'unknown_requests',i.unknown_requests) FROM read_job_budgets b JOIN read_job_items i ON i.job_id=b.job_id WHERE b.job_id='"+running+"'")==grant_before,"unsettled read grant changed during compaction");
  check(number(db,"SELECT archived_at FROM jobs WHERE id='"+running+"'")==0,"unsettled read was compacted");
  check(value(db,"SELECT json_group_array(json_object('id',id,'text',canonical_text,'raw',raw_content)) FROM snapshots ORDER BY id")==snapshots_before,"snapshot changed");
  check(number(db,"SELECT COUNT(*) FROM principal_revocations")==5,"revocation tombstone disappeared");
  const auto foreign=value(db,"SELECT id FROM jobs WHERE archived_at<>0 ORDER BY id LIMIT 1");rejects([&]{store.job_get("new-owner",foreign);},"E_NOT_FOUND");
  for(int i=0;i<5;++i){const auto owner="history-"+std::to_string(i);rejects([&]{store.job_create(owner,"fixture","real-terminal",1,1000);},"E_PRINCIPAL_REVOKED");rejects([&]{store.compact_jobs(owner);},"E_PRINCIPAL_REVOKED");}
  std::cout<<"{\"checks\":"<<checks<<",\"failures\":0,\"global_archived\":"<<archived<<",\"retained_old_identities\":50000,\"unsettled_grant_preserved\":true,\"external_requests\":0}\n";return 0;
 }catch(const Error& e){std::cerr<<"ERROR "<<e.code<<": "<<e.what()<<" (after "<<checks<<" checks)\n";return 2;}
 catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<" (after "<<checks<<" checks)\n";return 1;}
}
