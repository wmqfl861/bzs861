#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <vector>
using namespace bzs;
namespace {
  using Test=std::pair<std::string,std::function<void()>>;
  std::vector<Test>& tests() { static std::vector<Test> values;return values; }
  struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
  std::size_t assertions=0;
#define TEST(n) void n();Add reg_##n(#n,n);void n()
#define CHECK(c) do{++assertions;if(!(c))throw std::runtime_error(std::string("check failed: ")+#c+" line "+std::to_string(__LINE__));}while(false)
  template<class F> void rejects(F fn,const std::string& code) {
    ++assertions;
    try { fn(); } catch(const Error& error) {
      if(error.code==code)return;
      throw std::runtime_error("expected "+code+", got "+error.code);
    }
    throw std::runtime_error("expected "+code);
  }
  struct Temp {
    std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-batch-"+random_id());
    Temp() { std::filesystem::create_directories(path); }
    ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
  };
  struct Fake : WorkerPort {
    std::vector<std::string> reads,cancellations;
    std::function<Json(const Json&)> response;
    static Json ok(const Json& params) {
      Json r=Json::object();
      r.set("status","ok");r.set("html","<main>batchneedle readable content <a href='https://not-input.example/discovered'>link</a></main>");
      r.set("text","batchneedle readable content");r.set("title","Fixture batch");
      r.set("content_type","text/html");r.set("extractor","batch-fixture/1");r.set("index_allowed",true);
      r.set("final_url",params.at("url"));r.set("redirects",Json::array());
      r.set("links",Json::parse(R"(["https://not-input.example/discovered"])"));
      r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));return r;
    }
    Json call(const std::string& method,const Json& params,int timeout) override {
      CHECK(timeout>0);
      if(method=="fetch.cancel") { cancellations.push_back(params.at("attempt_id").str());return Json::object(); }
      CHECK(method=="fetch.extract");CHECK(params.at("max_requests").num()>=1);
      CHECK(params.at("max_content_bytes").num()>=1);CHECK(params.at("attempt_id").str().size()==48);
      reads.push_back(params.at("url").str());
      return response?response(params):ok(params);
    }
  };
  Json args(const std::vector<std::string>& urls,const std::string& key="batch-key") {
    auto p=Json::object(),list=Json::array();for(const auto& url:urls)list.push(Json::string(url));
    p.set("urls",list);p.set("idempotency_key",key);return p;
  }
  Json req(const std::string& op,const Json& params) {
    auto r=Json::object();r.set("operation",op);r.set("params",params);return r;
  }
  std::vector<std::string> urls(std::size_t count) {
    std::vector<std::string> out;for(std::size_t i=0;i<count;++i)out.push_back("https://batch.example/page/"+std::to_string(i));return out;
  }
  void drain(Store& store,WebService& web) {
    std::size_t count=0;while(auto lease=store.lease_next_read()) { CHECK(++count<=100);web.execute_read(*lease); }
  }
  void sql(sqlite3* db,const char* query) { CHECK(sqlite3_exec(db,query,nullptr,nullptr,nullptr)==SQLITE_OK); }
  std::int64_t scalar(sqlite3* db,const char* query) {
    sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query,-1,&raw,nullptr)==SQLITE_OK);
    std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> stmt(raw,sqlite3_finalize);
    CHECK(sqlite3_step(stmt.get())==SQLITE_ROW);return sqlite3_column_int64(stmt.get(),0);
  }
  Json item(Store& store,const std::string& id,std::size_t position=0) { return store.job_results("alice",id).at("items").at(position); }

  TEST(ten_explicit_inputs_only_and_ordered_receipts) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto input=urls(10);auto p=args(input);p.set("max_requests",std::int64_t{10});p.set("max_bytes",std::int64_t{10000});
    auto id=web.submit_reads("alice",p).at("job_id").str();
    CHECK(!store.lease_next()); // Fixture dispatch never claims a web task.
    drain(store,web);
    CHECK(worker.reads==input);CHECK(store.stats("alice").at("documents").num()==10);
    auto status=store.job_get("alice",id);
    CHECK(status.at("state").str()=="succeeded");CHECK(status.at("succeeded_items").num()==10);
    CHECK(status.at("budget").at("charged_requests").num()==10);
    CHECK(status.at("budget").at("charged_bytes").num()==1000);
    CHECK(status.at("usage").at("external_requests").num()==10);CHECK(!status.at("partial").flag());
    auto first=store.job_results("alice",id,0,3);CHECK(first.at("has_more").flag());CHECK(first.at("next_offset").num()==3);
    auto tail=store.job_results("alice",id,3,20);CHECK(!tail.at("has_more").flag());CHECK(tail.at("items").size()==7);
    for(std::size_t i=0;i<input.size();++i) {
      auto receipt=item(store,id,i);CHECK(receipt.at("input_index").num()==static_cast<std::int64_t>(i));
      CHECK(receipt.at("url").str()==input[i]);CHECK(receipt.at("available").flag());
      auto snapshot=store.snapshot("alice",receipt.at("receipt").at("snapshot_id").str());
      CHECK(snapshot.at("url").str()==input[i]);CHECK(snapshot.at("links").size()==1);
    }
    CHECK(web.submit_reads("alice",p).at("reused").flag());drain(store,web);CHECK(worker.reads.size()==10);
  }
  TEST(input_budget_validation_and_owner_scope) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto p=args(urls(1));auto id=web.submit_reads("alice",p).at("job_id").str();
    rejects([&]{store.job_get("bob",id);},"E_NOT_FOUND");
    rejects([&]{store.job_results("bob",id);},"E_NOT_FOUND");
    rejects([&]{store.job_control("bob",id,"cancel");},"E_NOT_FOUND");
    CHECK(web.submit_reads("bob",p).at("job_id").str()!=id);
    auto changed=args(urls(2));rejects([&]{web.submit_reads("alice",changed);},"E_IDEMPOTENCY_CONFLICT");
    for(const char* key:{"max_requests","max_bytes"})for(const auto& value:{Json::integer(0),Json(),Json::boolean(true)}) {
      auto bad=args(urls(1),std::string("bad-")+key);bad.set(key,value);
      rejects([&]{web.submit_reads("alice",bad);},"E_SCHEMA");
    }
    rejects([&]{web.submit_reads("alice",args({}));},"E_SCHEMA");
    rejects([&]{web.submit_reads("alice",args(urls(21)));},"E_SCHEMA");
    rejects([&]{web.submit_reads("alice",args({urls(1)[0],urls(1)[0]}));},"E_SCHEMA");
    rejects([&]{web.submit_reads("alice",args({"http://127.0.0.1/private"}));},"E_URL_BLOCKED");
    CHECK(worker.reads.empty());
  }
  TEST(execution_identity_does_not_use_search_deduplication) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    std::vector<std::string> input={"http://example.com/path","https://example.com/path", "https://www.example.com/path/",
      "https://example.com/path?utm_source=one","https://example.com/path?utm_source=two"};
    CHECK(dedup_key(input[0])==dedup_key(input[4]));
    web.submit_reads("alice",args(input));drain(store,web);CHECK(worker.reads==input);
    CHECK(store.stats("alice").at("documents").num()==5);
  }
  TEST(errors_are_isolated_and_default_attempts_do_not_retry) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    worker.response=[](const Json& p) { auto r=Fake::ok(p);if(p.at("url").str().ends_with("/1"))r.set("status","http_error");return r; };
    auto id=web.submit_reads("alice",args(urls(3))).at("job_id").str();drain(store,web);
    auto status=store.job_get("alice",id);CHECK(status.at("state").str()=="failed");CHECK(status.at("partial").flag());
    CHECK(status.at("succeeded_items").num()==2);CHECK(item(store,id,1).at("error_code").str()=="E_FETCH_HTTP");
    CHECK(status.at("usage").at("external_requests").num()==3);
    rejects([&]{store.job_control("alice",id,"retry_failed");},"E_ATTEMPT_BUDGET");
    drain(store,web);CHECK(worker.reads==urls(3));
  }
  TEST(completed_items_survive_pause_restart_resume_without_refetch) {
    Temp t;Fake worker;std::string id,first_snapshot;
    {
      Store store(t.path/"db");WebService web(store,worker,{});
      id=web.submit_reads("alice",args(urls(3))).at("job_id").str();
      auto lease=store.lease_next_read();CHECK(lease);web.execute_read(*lease);
      first_snapshot=item(store,id).at("receipt").at("snapshot_id").str();
      CHECK(store.job_control("alice",id,"pause").at("state").str()=="paused");
    }
    {
      Store store(t.path/"db");WebService web(store,worker,{});CHECK(!store.lease_next_read());
      CHECK(item(store,id).at("receipt").at("snapshot_id").str()==first_snapshot);
      store.job_control("alice",id,"resume");drain(store,web);
      CHECK(store.job_get("alice",id).at("state").str()=="succeeded");CHECK(worker.reads==urls(3));
    }
  }
  TEST(pause_or_cancel_during_fetch_fences_late_publication) {
    for(const std::string action:{"pause","cancel"}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups",&web);
      auto id=web.submit_reads("alice",args(urls(2))).at("job_id").str();
      worker.response=[&](const Json& p) {
        auto control=Json::object();control.set("job_id",id);control.set("action",action);
        service.dispatch({"alice",false},req("job.control",control));return Fake::ok(p);
      };
      auto lease=store.lease_next_read();CHECK(lease);web.execute_read(*lease);
      CHECK(store.stats("alice").at("snapshots").num()==0);CHECK(worker.cancellations.size()==1);
      CHECK(item(store,id).at("state").str()=="failed");
      CHECK(store.job_get("alice",id).at("state").str()==(action=="pause"?"paused":"cancelled"));
      if(action=="pause") { worker.response={};store.job_control("alice",id,"resume");drain(store,web);CHECK(worker.reads==urls(2)); }
      else { drain(store,web);CHECK(worker.reads.size()==1);CHECK(item(store,id,1).at("state").str()=="cancelled"); }
    }
  }
  TEST(deletion_and_reimport_cannot_be_overwritten_by_old_batch) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto old=store.ingest("alice",{urls(1)[0],"old","text/plain","old"});
    auto id=web.submit_reads("alice",args(urls(2))).at("job_id").str();
    worker.response=[&](const Json& p) {
      if(p.at("url").str()==urls(1)[0]) {
        store.erase("alice",old.at("document_id").str());
        store.ingest("alice",{urls(1)[0],"explicitly restored","text/plain","restored"});
      }
      return Fake::ok(p);
    };
    drain(store,web);CHECK(worker.reads==urls(2));
    CHECK(item(store,id).at("error_code").str()=="E_TARGET_DELETED");
    CHECK(item(store,id,1).at("state").str()=="succeeded");
    auto current=store.cached("alice",urls(1)[0]);CHECK(current);
    CHECK(store.snapshot("alice",current->at("snapshot_id").str()).at("canonical_text").str()=="explicitly restored");
    worker.response={};auto fresh=web.submit_reads("alice",args(urls(1),"after-delete")).at("job_id").str();
    drain(store,web);CHECK(store.job_get("alice",fresh).at("state").str()=="succeeded");
  }
  TEST(deletion_before_dispatch_stops_only_its_target) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto old=store.ingest("alice",{urls(1)[0],"old","text/plain","old"});
    auto id=web.submit_reads("alice",args(urls(2))).at("job_id").str();
    store.erase("alice",old.at("document_id").str());drain(store,web);
    CHECK(worker.reads==std::vector<std::string>{urls(2)[1]});
    CHECK(item(store,id).at("attempts").num()==0);CHECK(item(store,id).at("error_code").str()=="E_TARGET_DELETED");
  }
  TEST(unknown_execution_keeps_grants_across_recovery) {
    Temp t;std::int64_t now=1000000;std::string id;ReadLease abandoned;Fake worker;
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});
      auto p=args(urls(2));p.set("max_attempts",std::int64_t{2});
      id=web.submit_reads("alice",p).at("job_id").str();abandoned=*store.lease_next_read();
      CHECK(store.job_get("alice",id).at("budget").at("charged_requests").num()==18);
    }
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});
      CHECK(!store.lease_next_read());now+=26000;drain(store,web);
      CHECK(worker.reads==std::vector<std::string>{urls(2)[1]});
      auto status=store.job_get("alice",id);CHECK(status.at("usage").at("unknown_requests_charged").num()==18);
      CHECK(status.at("usage").at("unknown_bytes_charged").num()==8*1024*1024);
      CHECK(status.at("budget").at("charged_requests").num()==19);
      CHECK(item(store,id).at("error_code").str()=="E_EXECUTION_UNKNOWN");
      store.job_control("alice",id,"retry_failed");drain(store,web);
      CHECK(worker.reads.size()==2&&worker.reads[1]==urls(1)[0]);
      status=store.job_get("alice",id);CHECK(status.at("state").str()=="succeeded");
      CHECK(status.at("budget").at("charged_requests").num()==20);
      CHECK(status.at("usage").at("unknown_requests_charged").num()==18);
      CHECK(item(store,id).at("attempts").num()==2);CHECK(item(store,id,1).at("attempts").num()==1);
      // Publication with an abandoned lease is inert.
      SourceDocument doc{abandoned.url,"late","text/plain","late"};
      CHECK(!store.complete_read(abandoned,doc,parse_document(doc.raw,doc.content_type,doc.title),Json::object(),1,4));
    }
  }
  TEST(request_and_byte_budgets_stop_unstarted_items) {
    for(bool bytes:{false,true}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      auto p=args(urls(2));p.set(bytes?"max_bytes":"max_requests",std::int64_t{bytes?100:1});
      auto id=web.submit_reads("alice",p).at("job_id").str();drain(store,web);
      CHECK(worker.reads.size()==1);CHECK(item(store,id,1).at("attempts").num()==0);
      CHECK(item(store,id,1).at("error_code").str()==(bytes?"E_BYTE_BUDGET":"E_REQUEST_BUDGET"));
      auto status=store.job_get("alice",id);CHECK(status.at("budget").at(bytes?"charged_bytes":"charged_requests").num()==(bytes?100:1));
    }
  }
  TEST(lease_expiry_and_permission_changes_refuse_late_results) {
    for(bool permissions:{false,true}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      auto id=web.submit_reads("alice",args(urls(1))).at("job_id").str();
      worker.response=[&](const Json& p) {
        if(permissions)sql(store.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");
        else now+=26000;
        return Fake::ok(p);
      };
      drain(store,web);CHECK(store.stats("alice").at("snapshots").num()==0);
      CHECK(item(store,id).at("state").str()=="failed");
      CHECK(store.job_get("alice",id).at("budget").at("charged_requests").num()==1);
    }
  }
  TEST(receipt_failure_rolls_back_snapshot_and_index) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto id=web.submit_reads("alice",args(urls(1))).at("job_id").str();
    sql(store.raw_handle(),"CREATE TRIGGER receipt_failure BEFORE UPDATE OF state ON read_job_items WHEN NEW.state='succeeded' BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END;");
    drain(store,web);CHECK(store.stats("alice").at("snapshots").num()==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM content_fts")==0);
    CHECK(item(store,id).at("error_code").str()=="E_STORAGE");CHECK(!item(store,id).has("receipt"));
    CHECK(store.job_get("alice",id).at("budget").at("charged_requests").num()==1);
  }
  TEST(malformed_usage_consumes_unknown_grant_and_never_publishes) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    worker.response=[](const Json& p) { auto r=Fake::ok(p);auto usage=r.at("usage");usage.set("external_requests",p.at("max_requests").num()+1);r.set("usage",usage);return r; };
    auto id=web.submit_reads("alice",args(urls(1))).at("job_id").str();drain(store,web);
    CHECK(store.stats("alice").at("snapshots").num()==0);CHECK(item(store,id).at("error_code").str()=="E_WORKER_PROTOCOL");
    CHECK(store.job_get("alice",id).at("budget").at("charged_requests").num()==18);CHECK(item(store,id).at("usage_unknown").flag());
  }
  TEST(noindex_and_deleted_receipts_never_resurrect_content) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    worker.response=[](const Json& p) { auto r=Fake::ok(p);r.set("index_allowed",false);return r; };
    auto id=web.submit_reads("alice",args(urls(1))).at("job_id").str();drain(store,web);
    CHECK(!item(store,id).at("receipt").at("indexed").flag());CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM content_fts")==0);
    store.erase("alice",item(store,id).at("receipt").at("document_id").str());
    CHECK(!item(store,id).at("available").flag());CHECK(!item(store,id).has("receipt"));
    CHECK(web.submit_reads("alice",args(urls(1))).at("reused").flag());drain(store,web);CHECK(worker.reads.size()==1);
    CHECK(store.stats("alice").at("snapshots").num()==0);
  }
  TEST(api_dispatch_and_fixture_jobs_coexist) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups",&web);
    auto id=service.dispatch({"alice",false},req("read_many.submit",args(urls(1)))).at("job_id").str();
    auto fixture=store.job_create("alice","developer","fixture");auto lease=store.lease_next();CHECK(lease);
    CHECK(lease->id==fixture.at("job_id").str());const auto& doc=fixtures.get(lease->fixture_id);
    CHECK(store.complete(*lease,doc,parse_document(doc.raw,doc.content_type,doc.title)));
    drain(store,web);auto params=Json::object();params.set("job_id",id);
    auto results=service.dispatch({"alice",false},req("job.results",params));CHECK(results.at("items").size()==1);
    CHECK(store.job_get("alice",fixture.at("job_id").str()).at("kind").str()=="fixture.ingest");
  }
  TEST(deadline_near_int64_max_does_not_overflow_lease) {
    Temp t;std::int64_t now=std::numeric_limits<std::int64_t>::max()-1000;
    Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
    auto p=args(urls(1));p.set("timeout_ms",std::int64_t{1000});p.set("per_page_timeout_ms",std::int64_t{1000});
    auto id=web.submit_reads("alice",p).at("job_id").str();auto lease=store.lease_next_read();CHECK(lease);
    CHECK(store.job_get("alice",id).at("lease_until_ms").num()==std::numeric_limits<std::int64_t>::max());
    now=std::numeric_limits<std::int64_t>::max();web.execute_read(*lease);CHECK(worker.reads.empty());
    CHECK(store.stats("alice").at("snapshots").num()==0);
  }
  TEST(schema_three_additive_migration_keeps_snapshots_evidence_jobs_and_index) {
    Temp t;Json historic,current,evidence,job,page;std::string session;
    std::int64_t index_rowid=0;
    {
      Store store(t.path/"db");
      historic=store.ingest("alice",{urls(1)[0],"historical wording","text/plain","before"});
      evidence=store.evidence("alice",historic.at("snapshot_id").str(),0,10);
      current=store.ingest("alice",{urls(1)[0],"current batchneedle","text/plain","after"});
      auto q=parse_query(Json::string("batchneedle"));session=store.search("alice",q).at("session_id").str();
      page=store.page("alice",session,"",10);job=store.job_create("alice","developer","preserved-job");
      historic=store.snapshot("alice",historic.at("snapshot_id").str());
      current=store.snapshot("alice",current.at("snapshot_id").str());
      job=store.job_get("alice",job.at("job_id").str());
      // A non-default rowid proves that schema 3's derived index is not rebuilt.
      sql(store.raw_handle(),"UPDATE content_fts SET rowid=731;");
      index_rowid=scalar(store.raw_handle(),"SELECT rowid FROM content_fts");
      sql(store.raw_handle(),"DROP TABLE map_hops; DROP TABLE map_candidates; DROP TABLE map_resources; DROP TABLE map_jobs; DROP TABLE robots_cache; DROP TABLE site_request_state; DROP TABLE read_job_items; DROP TABLE read_job_budgets; ALTER TABLE jobs DROP COLUMN kind; PRAGMA user_version=3;");
    }
    {
      Store store(t.path/"db");
      CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==5);
      CHECK(scalar(store.raw_handle(),"SELECT rowid FROM content_fts")==index_rowid);
      CHECK(store.snapshot("alice",historic.at("snapshot_id").str()).canonical()==historic.canonical());
      CHECK(store.snapshot("alice",current.at("snapshot_id").str()).canonical()==current.canonical());
      CHECK(store.evidence_get("alice",evidence.at("evidence_id").str()).canonical()==evidence.canonical());
      CHECK(store.page("alice",session,"",10).canonical()==page.canonical());
      CHECK(store.job_get("alice",job.at("job_id").str()).canonical()==job.canonical());
      Fake worker;WebService web(store,worker,{});web.submit_reads("alice",args(urls(1)));drain(store,web);
      CHECK(worker.reads.size()==1);
    }
  }
  TEST(schema_three_quarantine_is_rejected_before_new_tables) {
    Temp t;
    {
      Store store(t.path/"db");
      sql(store.raw_handle(),"DROP TABLE map_hops; DROP TABLE map_candidates; DROP TABLE map_resources; DROP TABLE map_jobs; DROP TABLE robots_cache; DROP TABLE site_request_state; DROP TABLE read_job_items; DROP TABLE read_job_budgets; ALTER TABLE jobs DROP COLUMN kind; PRAGMA user_version=3; UPDATE settings SET value='1' WHERE key='restore_quarantine';");
    }
    rejects([&]{Store blocked(t.path/"db");},"E_RESTORE_QUARANTINED");
    sqlite3* raw=nullptr;CHECK(sqlite3_open((t.path/"db").string().c_str(),&raw)==SQLITE_OK);
    std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);
    CHECK(scalar(raw,"PRAGMA user_version")==3);
    CHECK(scalar(raw,"SELECT COUNT(*) FROM sqlite_master WHERE name='read_job_items'")==0);
    CHECK(scalar(raw,"SELECT COUNT(*) FROM pragma_table_info('jobs') WHERE name='kind'")==0);
  }
}
int main() {
  std::size_t failures=0;
  for(const auto& [name,test]:tests()) {
    try { test();std::cout<<"PASS "<<name<<'\n'; }
    catch(const std::exception& error) { ++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  }
  std::cout<<tests().size()<<" batch tests, "<<assertions<<" assertions, "<<failures<<" failures\n";
  return failures?1:0;
}
