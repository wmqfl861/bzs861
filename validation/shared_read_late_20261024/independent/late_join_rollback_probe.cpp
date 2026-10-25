#include "probe_support.hpp"
#include <algorithm>
#include <map>
using namespace independent;
namespace {
void execute(sqlite3* db,const std::string& sql) {
  char* error=nullptr;const int result=sqlite3_exec(db,sql.c_str(),nullptr,nullptr,&error);
  const std::string detail=error?error:"";sqlite3_free(error);
  ++checks;if(result!=SQLITE_OK)throw std::runtime_error(detail);
}
std::string hex(const unsigned char* value,int size) {
  const char* digits="0123456789abcdef";std::string out;
  for(int i=0;i<size;++i) {out+=digits[value[i]>>4];out+=digits[value[i]&15];}return out;
}
std::map<std::string,std::vector<std::string>> capture(sqlite3* db) {
  std::map<std::string,std::vector<std::string>> out;
  for(const auto* table:{"jobs","read_job_budgets","read_job_items","read_shared_attempts","read_shared_members","settings"}) {
    sqlite3_stmt* raw=nullptr;const std::string query=std::string("SELECT * FROM ")+table;
    VERIFY(sqlite3_prepare_v2(db,query.c_str(),-1,&raw,nullptr)==SQLITE_OK);
    std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);
    auto& rows=out[table];int status;
    while((status=sqlite3_step(raw))==SQLITE_ROW) {
      std::string row;
      for(int i=0;i<sqlite3_column_count(raw);++i) {
        const auto type=sqlite3_column_type(raw,i);const auto* bytes=sqlite3_column_text(raw,i);
        row+=std::to_string(type)+":"+hex(bytes,sqlite3_column_bytes(raw,i))+";";
      }
      rows.push_back(std::move(row));
    }
    VERIFY(status==SQLITE_DONE);std::sort(rows.begin(),rows.end());
  }
  return out;
}
void check_branch(const std::filesystem::path& path,bool already_grouped) {
  Worker worker;Store payer_store(path,[&]{return worker.now;}),late_store(path,[&]{return worker.now;});
  WebService first(payer_store,worker,{}),second(late_store,worker,{});
  const std::string url="https://independent-shared.example/admission-rollback";
  const auto payer=first.submit_reads("alice",parameters(url,"rollback-payer")).at("job_id").str();
  std::string original;
  if(already_grouped)original=first.submit_reads("alice",parameters(url,"rollback-original")).at("job_id").str();
  const auto lease=payer_store.lease_next_read();VERIFY(lease);
  VERIFY(scalar(payer_store.raw_handle(),"PRAGMA user_version")==15);
  Json retry;bool caught=false;
  worker.response=[&](const Json& request) {
    execute(late_store.raw_handle(),R"SQL(CREATE TRIGGER independent_late_admission_abort
 BEFORE INSERT ON read_shared_members
 WHEN EXISTS(SELECT 1 FROM jobs WHERE id=NEW.job_id AND idempotency_key='rollback-late')
 BEGIN SELECT RAISE(ABORT,'independent late member admission fault'); END)SQL");
    const auto before=capture(late_store.raw_handle());
    const auto payer_before=payer_store.job_get("alice",payer).canonical();
    const auto sequence=scalar(late_store.raw_handle(),"SELECT CAST(value AS INTEGER) FROM settings WHERE key='read_dispatch_sequence'");
    try {second.submit_reads("alice",parameters(url,"rollback-late"));}
    catch(const Error& error) {VERIFY(error.code=="E_STORAGE");caught=true;}
    VERIFY(caught);VERIFY(capture(late_store.raw_handle())==before);
    VERIFY(payer_store.job_get("alice",payer).canonical()==payer_before);
    VERIFY(scalar(late_store.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE idempotency_key='rollback-late'")==0);
    VERIFY(scalar(late_store.raw_handle(),"SELECT CAST(value AS INTEGER) FROM settings WHERE key='read_dispatch_sequence'")==sequence);
    VERIFY(scalar(late_store.raw_handle(),"SELECT sharing_closed FROM read_job_items WHERE job_id=?",payer)==0);
    VERIFY(scalar(late_store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts")==std::int64_t(already_grouped));
    VERIFY(scalar(late_store.raw_handle(),"SELECT COUNT(*) FROM read_shared_members")==(already_grouped?2:0));
    VERIFY(payer_store.read_lease_live(*lease));
    execute(late_store.raw_handle(),"DROP TRIGGER independent_late_admission_abort");
    retry=second.submit_reads("alice",parameters(url,"rollback-late"));
    VERIFY(!retry.at("reused").flag());VERIFY(retry.at("state").str()=="running");
    VERIFY(item(late_store,retry.at("job_id").str()).at("shared_read").at("members").num()==(already_grouped?3:2));
    return Worker::ok(request);
  };
  first.execute_read(*lease);VERIFY(caught);VERIFY(worker.requests.size()==1);VERIFY(worker.cancellations.empty());
  const auto joined=retry.at("job_id").str();
  VERIFY(item(payer_store,payer).at("state").str()=="succeeded");
  VERIFY(item(late_store,joined).at("receipt").at("snapshot_id").str()==item(payer_store,payer).at("receipt").at("snapshot_id").str());
  if(already_grouped)VERIFY(item(payer_store,original).at("receipt").at("snapshot_id").str()==item(payer_store,payer).at("receipt").at("snapshot_id").str());
  VERIFY(payer_store.job_get("alice",payer).at("budget").at("charged_requests").num()==1);
  VERIFY(late_store.job_get("alice",joined).at("budget").at("charged_requests").num()==0);
  VERIFY(scalar(late_store.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE idempotency_key='rollback-late'")==1);
  VERIFY(scalar(late_store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1 AND external_requests=1 AND content_bytes=100")==1);
  VERIFY(!payer_store.lease_next_read());
}
}
int main(int argc,char** argv) {
  if(argc!=2)return 2;const std::filesystem::path root=argv[1];std::filesystem::create_directories(root);
  std::size_t failures=0;
  for(bool grouped:{false,true}) {
    const std::string name=grouped?"existing_group_member_insert_rolls_back":"singleton_group_promotion_rolls_back";
    try {check_branch(root/(name+".sqlite"),grouped);std::cout<<"PASS "<<name<<std::endl;}
    catch(const std::exception& error){++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<std::endl;}
  }
  std::cout<<"INDEPENDENT_ADMISSION_ROLLBACK_CASES 2 CHECKS "<<checks<<" FAILURES "<<failures<<std::endl;
  return static_cast<int>(failures);
}
