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
  TEST(shared_reads_dispatch_once_and_keep_individual_durable_receipts) {
    Temp t;std::int64_t now=1000000;Fake worker;std::string first,second,attempt;
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});
      first=web.submit_reads("alice",args(urls(2),"shared-first")).at("job_id").str();
      second=web.submit_reads("alice",args(urls(2),"shared-second")).at("job_id").str();
      auto lease=store.lease_next_read();CHECK(lease);CHECK(lease->job_id==first);attempt=lease->attempt_id;
      CHECK(store.job_get("alice",second).at("state").str()=="running");CHECK(!store.lease_next_read());
      CHECK(store.job_get("alice",first).at("budget").at("charged_requests").num()==18);
      CHECK(store.job_get("alice",second).at("budget").at("charged_requests").num()==0);
      web.execute_read(*lease);CHECK(worker.reads.size()==1);CHECK(store.stats("alice").at("snapshots").num()==1);
      auto a=item(store,first),b=item(store,second);
      CHECK(a.at("receipt").at("snapshot_id").str()==b.at("receipt").at("snapshot_id").str());
      CHECK(a.at("shared_read").at("role").str()=="payer");CHECK(b.at("shared_read").at("role").str()=="subscriber");
      CHECK(b.at("shared_read").at("attempt_id").str()==attempt);CHECK(b.at("shared_read").at("payer_job_id").str()==first);
      CHECK(b.at("shared_read").at("associated_external_requests").num()==1);
    }
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});drain(store,web);
      CHECK(worker.reads==urls(2));CHECK(store.stats("alice").at("snapshots").num()==2);
      CHECK(store.job_get("alice",first).at("state").str()=="succeeded");CHECK(store.job_get("alice",second).at("state").str()=="succeeded");
      CHECK(store.job_get("alice",first).at("usage").at("external_requests").num()==2);
      CHECK(store.job_get("alice",second).at("usage").at("external_requests").num()==0);
      CHECK(store.job_get("alice",second).at("shared_reads").at("subscriber_attempts").num()==2);
      CHECK(store.job_get("alice",second).at("shared_reads").at("associated_external_requests").num()==2);
      CHECK(item(store,second).at("shared_read").at("attempt_id").str()==attempt);
    }
  }
  TEST(shared_reads_cancel_or_pause_one_subscriber_without_cancelling_others) {
    for(bool payer:{false,true})for(const std::string action:{"cancel","pause"}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups",&web);
      auto first=web.submit_reads("alice",args(urls(1),"payer")).at("job_id").str();
      auto second=web.submit_reads("alice",args(urls(1),"subscriber")).at("job_id").str();
      auto stopped=payer?first:second,active=payer?second:first;
      worker.response=[&](const Json& p) {
        auto control=Json::object();control.set("job_id",stopped);control.set("action",action);
        service.dispatch({"alice",false},req("job.control",control));CHECK(worker.cancellations.empty());return Fake::ok(p);
      };
      drain(store,web);CHECK(worker.reads.size()==1);CHECK(worker.cancellations.empty());
      CHECK(store.stats("alice").at("snapshots").num()==1);CHECK(item(store,active).at("state").str()=="succeeded");
      CHECK(item(store,stopped).at("state").str()=="failed");CHECK(!item(store,stopped).has("receipt"));
      CHECK(store.job_get("alice",stopped).at("state").str()==(action=="pause"?"paused":"cancelled"));
      CHECK(store.job_get("alice",first).at("budget").at("charged_requests").num()==1);
      CHECK(store.job_get("alice",second).at("budget").at("charged_requests").num()==0);
    }
  }
  TEST(shared_reads_last_subscriber_cancel_stops_worker_and_rejects_late_body) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups",&web);
    auto first=web.submit_reads("alice",args(urls(1),"cancel-payer")).at("job_id").str();
    auto second=web.submit_reads("alice",args(urls(1),"cancel-subscriber")).at("job_id").str();
    worker.response=[&](const Json& p) {
      for(const auto& id:{first,second}) {
        auto control=Json::object();control.set("job_id",id);control.set("action","cancel");
        service.dispatch({"alice",false},req("job.control",control));
      }
      CHECK(worker.cancellations.size()==1);CHECK(worker.cancellations[0]==p.at("attempt_id").str());return Fake::ok(p);
    };
    drain(store,web);CHECK(worker.reads.size()==1);CHECK(store.stats("alice").at("snapshots").num()==0);
    CHECK(store.job_get("alice",first).at("state").str()=="cancelled");CHECK(store.job_get("alice",second).at("state").str()=="cancelled");
    CHECK(store.job_get("alice",first).at("usage").at("external_requests").num()==1);
    CHECK(store.job_get("alice",second).at("usage").at("external_requests").num()==0);
  }
  TEST(shared_reads_cancelled_payer_before_worker_start_still_serves_live_member) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto first=web.submit_reads("alice",args(urls(1),"early-payer")).at("job_id").str();
    auto second=web.submit_reads("alice",args(urls(1),"early-member")).at("job_id").str();
    auto lease=store.lease_next_read();CHECK(lease);store.job_control("alice",first,"cancel");
    CHECK(store.read_lease_live(*lease));CHECK(store.active_read_attempt("alice",first).empty());
    web.execute_read(*lease);CHECK(worker.reads.size()==1);CHECK(item(store,second).at("state").str()=="succeeded");
    CHECK(store.job_get("alice",first).at("budget").at("charged_requests").num()==1);
  }
  TEST(shared_reads_owner_uri_deadline_timeout_and_budget_boundaries) {
    for(const std::string difference:{"owner","uri","deadline","timeout","requests","bytes","order"}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      web.submit_reads("alice",args(urls(1),"boundary-first"));auto input=urls(1);auto p=args(input,"boundary-second");
      auto owner=difference=="owner"?"bob":"alice";
      if(difference=="uri")p=args({urls(1)[0]+"?utm_source=two"},"boundary-second");
      if(difference=="deadline")p.set("timeout_ms",std::int64_t{1000});
      if(difference=="timeout")p.set("per_page_timeout_ms",std::int64_t{1000});
      if(difference=="requests")p.set("max_requests",std::int64_t{1});
      if(difference=="bytes")p.set("max_bytes",std::int64_t{100});
      if(difference=="order")p=args({urls(2)[1],urls(1)[0]},"boundary-second");
      auto second=web.submit_reads(owner,p).at("job_id").str();auto lease=store.lease_next_read();CHECK(lease);
      CHECK(store.job_get(owner,second).at("state").str()=="queued");web.execute_read(*lease);drain(store,web);
      CHECK(worker.reads.size()==(difference=="order"?3U:2U));
    }
  }
  TEST(shared_reads_recovery_marks_one_physical_unknown_and_never_auto_retries) {
    Temp t;std::int64_t now=1000000;Fake worker;std::string first,second;ReadLease old;
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});
      auto a=args(urls(1),"unknown-payer"),b=args(urls(1),"unknown-member");a.set("max_attempts",std::int64_t{2});b.set("max_attempts",std::int64_t{2});
      first=web.submit_reads("alice",a).at("job_id").str();second=web.submit_reads("alice",b).at("job_id").str();old=*store.lease_next_read();
      store.job_control("alice",first,"cancel");
    }
    now+=26000;
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});drain(store,web);CHECK(worker.reads.empty());
      CHECK(store.job_get("alice",first).at("state").str()=="cancelled");CHECK(store.job_get("alice",second).at("state").str()=="failed");
      CHECK(store.job_get("alice",first).at("usage").at("unknown_requests_charged").num()==18);
      CHECK(store.job_get("alice",second).at("usage").at("unknown_requests_charged").num()==0);
      CHECK(item(store,second).at("shared_read").at("usage_unknown").flag());
      CHECK(store.job_get("alice",second).at("shared_reads").at("unknown_attempts").num()==1);
      store.job_control("alice",second,"retry_failed");drain(store,web);CHECK(worker.reads.size()==1);
      CHECK(store.job_get("alice",second).at("budget").at("charged_requests").num()==1);
      CHECK(store.job_get("alice",second).at("shared_reads").at("unknown_attempts").num()==1);
      SourceDocument late{old.url,"late content","text/plain","late"};
      CHECK(!store.complete_read(old,late,parse_document(late.raw,late.content_type),Json::object(),1,12));
    }
  }
  TEST(shared_reads_deletion_and_revoked_epoch_fence_all_subscribers) {
    for(bool deletion:{false,true}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      auto old=store.ingest("alice",{urls(1)[0],"before","text/plain","before"});
      auto first=web.submit_reads("alice",args(urls(1),"fence-payer")).at("job_id").str();
      auto second=web.submit_reads("alice",args(urls(1),"fence-member")).at("job_id").str();
      worker.response=[&](const Json& p) {
        if(deletion)store.erase("alice",old.at("document_id").str());else sql(store.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");
        return Fake::ok(p);
      };
      drain(store,web);CHECK(worker.reads.size()==1);CHECK(item(store,first).at("state").str()=="failed");CHECK(item(store,second).at("state").str()=="failed");
      CHECK(!item(store,second).has("receipt"));CHECK(store.job_get("alice",first).at("budget").at("charged_requests").num()==1);
      CHECK(store.job_get("alice",second).at("budget").at("charged_requests").num()==0);
    }
  }
  TEST(shared_reads_receipt_failure_rolls_back_every_subscriber_and_snapshot) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    auto first=web.submit_reads("alice",args(urls(1),"atomic-payer")).at("job_id").str();
    auto second=web.submit_reads("alice",args(urls(1),"atomic-member")).at("job_id").str();
    sql(store.raw_handle(),"CREATE TRIGGER shared_receipt_failure BEFORE UPDATE OF state ON read_job_items WHEN NEW.state='succeeded' BEGIN SELECT RAISE(ABORT,'shared receipt unavailable'); END;");
    drain(store,web);CHECK(worker.reads.size()==1);CHECK(store.stats("alice").at("snapshots").num()==0);
    CHECK(item(store,first).at("error_code").str()=="E_STORAGE");CHECK(item(store,second).at("error_code").str()=="E_STORAGE");
    CHECK(store.job_get("alice",first).at("budget").at("charged_requests").num()==1);
    CHECK(store.job_get("alice",second).at("budget").at("charged_requests").num()==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1 AND external_requests=1")==1);
  }
  TEST(shared_reads_compaction_preserves_payer_attribution_until_last_member) {
    Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
    auto first=web.submit_reads("alice",args(urls(1),"compact-payer")).at("job_id").str();
    auto second=web.submit_reads("alice",args(urls(1),"compact-member")).at("job_id").str();drain(store,web);
    now+=8LL*24*60*60*1000;CHECK(store.compact_jobs("alice",1).at("compacted_jobs").num()==1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts")==1);CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_members")==1);
    auto a=store.job_get("alice",first),b=store.job_get("alice",second);CHECK(a.has("retention")!=b.has("retention"));
    const auto remaining=a.has("retention")?second:first;CHECK(item(store,remaining).at("shared_read").at("payer_job_id").str()==first);
    CHECK(store.compact_jobs("alice",1).at("compacted_jobs").num()==1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts")==0);CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_members")==0);
    CHECK(store.job_get("alice",first).at("usage").at("external_requests").num()==1);
    CHECK(store.job_get("alice",second).at("shared_reads").at("subscriber_attempts").num()==1);
  }
  TEST(shared_reads_bound_membership_and_do_not_attach_after_dispatch) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    std::vector<std::string> jobs;for(int i=0;i<34;++i)jobs.push_back(web.submit_reads("alice",args(urls(1),"bounded-"+std::to_string(i))).at("job_id").str());
    auto lease=store.lease_next_read();CHECK(lease);CHECK(item(store,jobs[0]).at("shared_read").at("members").num()==32);
    CHECK(store.job_get("alice",jobs[32]).at("state").str()=="queued");
    auto late=web.submit_reads("alice",args(urls(1),"late-member")).at("job_id").str();
    CHECK(store.job_get("alice",late).at("state").str()=="queued");web.execute_read(*lease);drain(store,web);
    CHECK(worker.reads.size()==2);CHECK(store.job_get("alice",late).at("state").str()=="succeeded");
  }
  TEST(shared_reads_two_store_connections_cannot_dispatch_the_same_group_twice) {
    Temp t;std::int64_t now=1000000;Store first(t.path/"db",[&]{return now;}),second(t.path/"db",[&]{return now;});
    Fake worker;WebService web(first,worker,{});
    auto payer=first.read_many_submit("alice",urls(1),"connection-payer",{}).at("job_id").str();
    auto member=second.read_many_submit("alice",urls(1),"connection-member",{}).at("job_id").str();
    auto lease=first.lease_next_read();CHECK(lease);CHECK(!second.lease_next_read());
    second.job_control("alice",payer,"cancel");CHECK(second.active_read_attempt("alice",payer).empty());
    web.execute_read(*lease);CHECK(worker.reads.size()==1);CHECK(item(second,member).at("state").str()=="succeeded");
    CHECK(second.job_get("alice",payer).at("state").str()=="cancelled");CHECK(!second.lease_next_read());
  }
  TEST(shared_reads_known_failure_and_pre_dispatch_cancel_do_not_create_free_retries) {
    for(bool before:{false,true}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      auto payer=web.submit_reads("alice",args(urls(1),"failure-payer")).at("job_id").str();
      auto member=web.submit_reads("alice",args(urls(1),"failure-member")).at("job_id").str();
      auto lease=store.lease_next_read();CHECK(lease);
      if(before) { store.job_control("alice",payer,"cancel");store.job_control("alice",member,"cancel"); }
      else worker.response=[](const Json& p) { auto r=Fake::ok(p);r.set("status","http_error");return r; };
      web.execute_read(*lease);CHECK(!store.lease_next_read());CHECK(worker.reads.size()==(before?0U:1U));
      CHECK(store.stats("alice").at("snapshots").num()==0);
      CHECK(store.job_get("alice",payer).at("budget").at("charged_requests").num()==(before?0:1));
      CHECK(store.job_get("alice",member).at("budget").at("charged_requests").num()==0);
      CHECK(store.job_get("alice",member).at("shared_reads").at("unsettled_attempts").num()==0);
      CHECK(item(store,payer).at("error_code").str()==(before?"E_LEASE_EXPIRED":"E_FETCH_HTTP"));
      CHECK(item(store,member).at("error_code").str()==(before?"E_LEASE_EXPIRED":"E_FETCH_HTTP"));
    }
  }
  void check_late_shared_receipt(Store& store,const std::string& payer,const std::string& subscriber,const std::string& attempt) {
    const auto paid=item(store,payer),joined=item(store,subscriber);
    CHECK(paid.at("state").str()=="succeeded");CHECK(joined.at("state").str()=="succeeded");
    CHECK(paid.at("receipt").at("snapshot_id").str()==joined.at("receipt").at("snapshot_id").str());
    CHECK(paid.at("shared_read").at("attempt_id").str()==attempt);CHECK(joined.at("shared_read").at("attempt_id").str()==attempt);
    CHECK(joined.at("shared_read").at("payer_job_id").str()==payer);CHECK(joined.at("shared_read").at("role").str()=="subscriber");
    CHECK(store.job_get("alice",payer).at("usage").at("external_requests").num()==1);
    CHECK(store.job_get("alice",payer).at("usage").at("content_bytes").num()==100);
    CHECK(store.job_get("alice",subscriber).at("usage").at("external_requests").num()==0);
    CHECK(store.job_get("alice",subscriber).at("usage").at("content_bytes").num()==0);
    CHECK(joined.at("shared_read").at("associated_external_requests").num()==1);
    CHECK(joined.at("shared_read").at("associated_content_bytes").num()==100);
    for(const auto& id:{payer,subscriber}) {
      const auto status=store.job_get("alice",id);
      CHECK(status.at("usage").at("reserved_requests").num()==0);CHECK(status.at("usage").at("reserved_bytes").num()==0);
      CHECK(status.at("shared_reads").at("unsettled_attempts").num()==0);
    }
  }
  TEST(late_shared_single_payer_accepts_a_subscriber_inside_the_worker_call) {
    Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-single-payer")).at("job_id").str();
    const auto lease=store.lease_next_read();CHECK(lease);const auto original=store.job_get("alice",payer);Json admitted,paid_during,joined_during;
    worker.response=[&](const Json& p) {
      now+=500;admitted=web.submit_reads("alice",args(urls(1),"late-single-member"));
      paid_during=store.job_get("alice",payer);joined_during=item(store,admitted.at("job_id").str());return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};
    CHECK(admitted.at("state").str()=="running");CHECK(admitted.at("lease_until_ms").num()==original.at("lease_until_ms").num());
    CHECK(admitted.at("budget").at("charged_requests").num()==0);CHECK(admitted.at("budget").at("charged_bytes").num()==0);
    CHECK(paid_during.at("budget").canonical()==original.at("budget").canonical());
    CHECK(paid_during.at("lease_until_ms").num()==original.at("lease_until_ms").num());
    CHECK(joined_during.at("shared_read").at("members").num()==2);CHECK(joined_during.at("attempts").num()==1);
    CHECK(worker.reads.size()==1);CHECK(!store.lease_next_read());CHECK(store.stats("alice").at("snapshots").num()==1);
    check_late_shared_receipt(store,payer,admitted.at("job_id").str(),lease->attempt_id);
  }
  TEST(late_shared_existing_group_accepts_members_without_another_physical_grant) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-group-payer")).at("job_id").str();
    const auto early=web.submit_reads("alice",args(urls(1),"late-group-early")).at("job_id").str();
    const auto lease=store.lease_next_read();CHECK(lease);Json admitted,during;
    worker.response=[&](const Json& p) {
      admitted=web.submit_reads("alice",args(urls(1),"late-group-late"));during=item(store,payer);return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};
    CHECK(admitted.at("state").str()=="running");CHECK(during.at("shared_read").at("members").num()==3);
    CHECK(worker.reads.size()==1);CHECK(!store.lease_next_read());
    check_late_shared_receipt(store,payer,early,lease->attempt_id);check_late_shared_receipt(store,payer,admitted.at("job_id").str(),lease->attempt_id);
  }
  TEST(late_shared_cancel_or_pause_does_not_stop_another_subscriber) {
    for(bool stop_payer:{false,true})for(const std::string action:{"cancel","pause"}) {
      Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
      FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups",&web);
      const auto payer=web.submit_reads("alice",args(urls(1),"late-control-payer")).at("job_id").str();
      const auto lease=store.lease_next_read();CHECK(lease);Json admitted;
      worker.response=[&](const Json& p) {
        admitted=web.submit_reads("alice",args(urls(1),"late-control-member"));
        auto control=Json::object();control.set("job_id",stop_payer?payer:admitted.at("job_id").str());control.set("action",action);
        service.dispatch({"alice",false},req("job.control",control));return Fake::ok(p);
      };
      web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");
      const auto member=admitted.at("job_id").str(),stopped=stop_payer?payer:member,active=stop_payer?member:payer;
      CHECK(worker.reads.size()==1);CHECK(worker.cancellations.empty());CHECK(item(store,active).at("state").str()=="succeeded");
      CHECK(!item(store,stopped).has("receipt"));CHECK(store.job_get("alice",stopped).at("state").str()==(action=="pause"?"paused":"cancelled"));
      CHECK(store.job_get("alice",payer).at("budget").at("charged_requests").num()==1);
      CHECK(store.job_get("alice",member).at("budget").at("charged_requests").num()==0);CHECK(store.stats("alice").at("snapshots").num()==1);
    }
  }
  TEST(late_shared_last_exit_closes_admission_before_the_cancel_rpc_window) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-window-payer")).at("job_id").str();
    const auto lease=store.lease_next_read();CHECK(lease);Json admitted,after_close;std::string still_live,stop_attempt;
    worker.response=[&](const Json& p) {
      admitted=web.submit_reads("alice",args(urls(1),"late-window-member"));
      store.job_control("alice",payer,"cancel");still_live=store.active_read_attempt("alice",payer);
      store.job_control("alice",admitted.at("job_id").str(),"cancel");stop_attempt=store.active_read_attempt("alice",admitted.at("job_id").str());
      // Exactly the real Service ordering: obtain the cancellation ID, then a
      // new submit can interleave before the cooperative Worker RPC is sent.
      after_close=web.submit_reads("alice",args(urls(1),"late-window-after-close"));
      web.cancel_read_attempt(stop_attempt);return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");
    CHECK(still_live.empty());CHECK(stop_attempt==lease->attempt_id);CHECK(after_close.at("state").str()=="queued");
    CHECK(worker.cancellations==std::vector<std::string>{lease->attempt_id});CHECK(store.stats("alice").at("snapshots").num()==0);
    for(const auto& id:{payer,admitted.at("job_id").str()}) {CHECK(store.job_get("alice",id).at("state").str()=="cancelled");CHECK(!item(store,id).has("receipt"));}
    CHECK(store.job_get("alice",payer).at("usage").at("external_requests").num()==1);
    CHECK(store.job_get("alice",admitted.at("job_id").str()).at("usage").at("external_requests").num()==0);
    CHECK(item(store,after_close.at("job_id").str()).at("attempts").num()==0);
    drain(store,web);CHECK(worker.reads.size()==2);CHECK(store.job_get("alice",after_close.at("job_id").str()).at("state").str()=="succeeded");
  }
  TEST(late_shared_membership_never_reopens_after_a_liveness_failure_and_clock_rollback) {
    for(bool cancel_query:{false,true}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      const auto payer=web.submit_reads("alice",args(urls(1),"late-closed-payer")).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);
      now+=26000;
      if(cancel_query)CHECK(store.active_read_attempt("alice",payer)==lease->attempt_id);else CHECK(!store.read_lease_live(*lease));
      // Closing admission must not manufacture a known or unknown settlement.
      CHECK(item(store,payer).at("state").str()=="running");CHECK(store.job_get("alice",payer).at("usage").at("reserved_requests").num()==18);
      now=1000500;Store second(t.path/"db",[&]{return now;});
      const auto late=second.read_many_submit("alice",urls(1),"late-after-rollback",{});
      CHECK(late.at("state").str()=="queued");CHECK(!item(second,late.at("job_id").str()).has("shared_read"));
      CHECK(store.job_get("alice",payer).at("usage").at("reserved_requests").num()==18);
      // This fixture never sent the old lease to Worker; release it with known
      // zero use, leaving the new task its own ordinary dispatch opportunity.
      store.fail_read(*lease,"E_SHUTDOWN",0,0);drain(store,web);CHECK(worker.reads.size()==1);
      CHECK(store.job_get("alice",late.at("job_id").str()).at("state").str()=="succeeded");
    }
  }
  TEST(late_shared_budget_owner_uri_and_first_pending_item_must_match) {
    for(const std::string difference:{"owner","uri","percent_uri","requests","bytes","timeout","deadline","order"}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      web.submit_reads("alice",args(urls(1),"late-boundary-payer"));const auto lease=store.lease_next_read();CHECK(lease);Json admitted,at_submit;
      const std::string owner=difference=="owner"?"bob":"alice";
      worker.response=[&](const Json& p) {
        now+=500;auto options=args(urls(1),"late-boundary-member");
        if(difference=="uri")options=args({urls(1)[0]+"?view=two"},"late-boundary-member");
        if(difference=="percent_uri")options=args({"https://batch.example/page/%30"},"late-boundary-member");
        if(difference=="requests")options.set("max_requests",std::int64_t{1});
        if(difference=="bytes")options.set("max_bytes",std::int64_t{100});
        if(difference=="timeout")options.set("per_page_timeout_ms",std::int64_t{1000});
        if(difference=="deadline")options.set("timeout_ms",std::int64_t{20000});
        if(difference=="order")options=args({urls(2)[1],urls(1)[0]},"late-boundary-member");
        admitted=web.submit_reads(owner,options);at_submit=store.job_results(owner,admitted.at("job_id").str()).at("items").at(std::size_t{0});return Fake::ok(p);
      };
      web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="queued");CHECK(!at_submit.has("shared_read"));CHECK(at_submit.at("attempts").num()==0);
      drain(store,web);CHECK(worker.reads.size()==(difference=="order"?3U:2U));CHECK(store.job_get(owner,admitted.at("job_id").str()).at("state").str()=="succeeded");
    }
  }
  TEST(late_shared_auth_deletion_snapshot_and_publication_changes_prevent_joining) {
    for(const std::string difference:{"auth","target_delete","unrelated_delete","snapshot","publication"}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      const auto target=store.ingest("alice",{urls(1)[0],"before dispatch","text/plain","before"});
      const auto other=store.ingest("alice",{"https://batch.example/unrelated","other","text/plain","other"});
      web.submit_reads("alice",args(urls(1),"late-history-payer"));const auto lease=store.lease_next_read();CHECK(lease);Json admitted,at_submit;
      worker.response=[&](const Json& p) {
        if(difference=="auth")sql(store.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");
        if(difference=="target_delete") {store.erase("alice",target.at("document_id").str());store.ingest("alice",{urls(1)[0],"new authorization","text/plain","new"});}
        if(difference=="unrelated_delete")store.erase("alice",other.at("document_id").str());
        if(difference=="snapshot")store.ingest("alice",{urls(1)[0],"new snapshot","text/plain","new"});
        if(difference=="publication") {
          const auto denied=Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex"],"diagnostics":[]})");
          store.observe_web_publication("alice",urls(1)[0],urls(1)[0],denied,store.publication_fence("alice",urls(1)[0]));
        }
        admitted=web.submit_reads("alice",args(urls(1),"late-history-member"));at_submit=item(store,admitted.at("job_id").str());return Fake::ok(p);
      };
      web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="queued");CHECK(!at_submit.has("shared_read"));CHECK(at_submit.at("attempts").num()==0);
      drain(store,web);CHECK(worker.reads.size()==2);CHECK(store.job_get("alice",admitted.at("job_id").str()).at("state").str()=="succeeded");
    }
  }
  TEST(late_shared_matches_the_effective_dispatch_timeout_without_extending_it) {
    for(bool match:{false,true}) {
      Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Fake worker;WebService web(store,worker,{});
      auto options=args(urls(1),"late-short-payer");options.set("timeout_ms",std::int64_t{2000});
      const auto payer=web.submit_reads("alice",options).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);CHECK(lease->timeout_ms==2000);
      const auto until=store.job_get("alice",payer).at("lease_until_ms").num();Json admitted,wire;
      worker.response=[&](const Json& p) {
        wire=Json::parse(p.dump());now+=500;auto late=args(urls(1),"late-short-member");
        if(match)late.set("per_page_timeout_ms",std::int64_t{2000});
        admitted=web.submit_reads("alice",late);return Fake::ok(p);
      };
      web.execute_read(*lease);worker.response={};CHECK(wire.at("timeout_ms").num()==2000);
      CHECK(admitted.at("state").str()==(match?"running":"queued"));
      if(match) {CHECK(admitted.at("lease_until_ms").num()==until);check_late_shared_receipt(store,payer,admitted.at("job_id").str(),lease->attempt_id);}
      drain(store,web);CHECK(worker.reads.size()==(match?1U:2U));
    }
  }
  TEST(late_shared_lifetime_membership_limit_and_idempotency_survive_cancellation) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-limit-payer")).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);
    std::vector<Json> joined;Json replay,overflow,group;
    worker.response=[&](const Json& p) {
      for(int i=0;i<31;++i)joined.push_back(web.submit_reads("alice",args(urls(1),"late-limit-"+std::to_string(i))));
      store.job_control("alice",joined.front().at("job_id").str(),"cancel");
      replay=web.submit_reads("alice",args(urls(1),"late-limit-0"));overflow=web.submit_reads("alice",args(urls(1),"late-limit-overflow"));
      group=item(store,payer);return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};CHECK(joined.size()==31);
    for(const auto& admitted:joined)CHECK(admitted.at("state").str()=="running");
    CHECK(group.at("shared_read").at("members").num()==32);CHECK(replay.at("reused").flag());CHECK(replay.at("job_id").str()==joined.front().at("job_id").str());
    CHECK(overflow.at("state").str()=="queued");CHECK(item(store,joined.front().at("job_id").str()).at("attempts").num()==1);
    CHECK(!item(store,joined.front().at("job_id").str()).has("receipt"));
    CHECK(store.job_get("alice",joined.front().at("job_id").str()).at("shared_reads").at("attempts").num()==1);
    for(std::size_t i=1;i<joined.size();++i)CHECK(item(store,joined[i].at("job_id").str()).at("shared_read").at("attempt_id").str()==lease->attempt_id);
    CHECK(worker.reads.size()==1);drain(store,web);CHECK(worker.reads.size()==2);
    CHECK(store.job_get("alice",overflow.at("job_id").str()).at("state").str()=="succeeded");CHECK(item(store,payer).at("shared_read").at("members").num()==32);
  }
  TEST(late_shared_admission_is_durable_across_store_connections) {
    Temp t;std::int64_t now=1000000;Store first(t.path/"db",[&]{return now;}),second(t.path/"db",[&]{return now;});Fake worker;WebService web(first,worker,{});
    const auto payer=first.read_many_submit("alice",urls(1),"late-connections-payer",{}).at("job_id").str();const auto lease=first.lease_next_read();CHECK(lease);Json admitted;std::string cancellation;
    worker.response=[&](const Json& p) {
      admitted=second.read_many_submit("alice",urls(1),"late-connections-member",{});
      second.job_control("alice",payer,"cancel");cancellation=second.active_read_attempt("alice",payer);return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");CHECK(cancellation.empty());CHECK(worker.reads.size()==1);
    const auto subscriber=admitted.at("job_id").str();CHECK(item(second,subscriber).at("state").str()=="succeeded");
    CHECK(item(second,subscriber).at("shared_read").at("attempt_id").str()==lease->attempt_id);CHECK(!second.lease_next_read());
    CHECK(second.job_get("alice",payer).at("state").str()=="cancelled");CHECK(second.job_get("alice",payer).at("usage").at("external_requests").num()==1);
    CHECK(second.job_get("alice",subscriber).at("usage").at("external_requests").num()==0);
  }
  TEST(late_shared_recovery_retains_one_unknown_charge_and_requires_explicit_retry) {
    Temp t;std::int64_t now=1000000;Fake worker;ReadLease abandoned;std::string payer,subscriber;
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});
      auto first=args(urls(1),"late-unknown-payer"),second=args(urls(1),"late-unknown-member");
      first.set("max_attempts",std::int64_t{2});second.set("max_attempts",std::int64_t{2});
      payer=web.submit_reads("alice",first).at("job_id").str();abandoned=*store.lease_next_read();
      const auto admitted=web.submit_reads("alice",second);CHECK(admitted.at("state").str()=="running");subscriber=admitted.at("job_id").str();
      store.job_control("alice",payer,"cancel");
    }
    {
      Store store(t.path/"db",[&]{return now;});WebService web(store,worker,{});CHECK(!store.lease_next_read());now+=26000;drain(store,web);
      CHECK(worker.reads.empty());CHECK(store.job_get("alice",payer).at("state").str()=="cancelled");CHECK(store.job_get("alice",subscriber).at("state").str()=="failed");
      CHECK(store.job_get("alice",payer).at("usage").at("unknown_requests_charged").num()==18);
      CHECK(store.job_get("alice",payer).at("usage").at("unknown_bytes_charged").num()==8*1024*1024);
      CHECK(store.job_get("alice",subscriber).at("usage").at("unknown_requests_charged").num()==0);CHECK(item(store,subscriber).at("shared_read").at("usage_unknown").flag());
      CHECK(!store.lease_next_read());store.job_control("alice",subscriber,"retry_failed");drain(store,web);CHECK(worker.reads.size()==1);
      CHECK(store.job_get("alice",subscriber).at("state").str()=="succeeded");CHECK(item(store,subscriber).at("attempts").num()==2);
      CHECK(store.job_get("alice",subscriber).at("budget").at("charged_requests").num()==1);
      CHECK(store.job_get("alice",subscriber).at("shared_reads").at("unknown_attempts").num()==1);
      SourceDocument stale{abandoned.url,"abandoned late body","text/plain","stale"};
      CHECK(!store.complete_read(abandoned,stale,parse_document(stale.raw,stale.content_type),Json::object(),1,100));
      CHECK(store.job_get("alice",payer).at("usage").at("unknown_requests_charged").num()==18);
    }
  }
  TEST(late_shared_known_failure_is_charged_once_without_an_automatic_retry) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-failure-payer")).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);Json admitted;
    worker.response=[&](const Json& p) {
      admitted=web.submit_reads("alice",args(urls(1),"late-failure-member"));auto response=Fake::ok(p);response.set("status","http_error");return response;
    };
    web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");const auto member=admitted.at("job_id").str();
    for(const auto& id:{payer,member}) {CHECK(item(store,id).at("error_code").str()=="E_FETCH_HTTP");CHECK(!item(store,id).has("receipt"));}
    CHECK(store.job_get("alice",payer).at("budget").at("charged_requests").num()==1);CHECK(store.job_get("alice",payer).at("budget").at("charged_bytes").num()==100);
    CHECK(store.job_get("alice",member).at("budget").at("charged_requests").num()==0);CHECK(store.job_get("alice",member).at("budget").at("charged_bytes").num()==0);
    CHECK(item(store,member).at("shared_read").at("associated_external_requests").num()==1);CHECK(!store.lease_next_read());CHECK(worker.reads.size()==1);CHECK(store.stats("alice").at("snapshots").num()==0);
  }
  TEST(late_shared_receipt_failure_rolls_back_all_members_and_preserves_one_charge) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    const auto payer=web.submit_reads("alice",args(urls(1),"late-atomic-payer")).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);Json admitted;
    worker.response=[&](const Json& p) {
      admitted=web.submit_reads("alice",args(urls(1),"late-atomic-member"));
      const auto trigger="CREATE TRIGGER late_receipt_failure BEFORE UPDATE OF state ON read_job_items WHEN NEW.state='succeeded' AND NEW.job_id='"+admitted.at("job_id").str()+"' BEGIN SELECT RAISE(ABORT,'late receipt unavailable'); END;";
      sql(store.raw_handle(),trigger.c_str());return Fake::ok(p);
    };
    web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");const auto member=admitted.at("job_id").str();
    CHECK(worker.reads.size()==1);CHECK(store.stats("alice").at("snapshots").num()==0);
    for(const auto& id:{payer,member}) {CHECK(item(store,id).at("error_code").str()=="E_STORAGE");CHECK(!item(store,id).has("receipt"));}
    CHECK(store.job_get("alice",payer).at("budget").at("charged_requests").num()==1);CHECK(store.job_get("alice",member).at("budget").at("charged_requests").num()==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1 AND external_requests=1 AND content_bytes=100")==1);
    CHECK(!store.lease_next_read());
  }
  TEST(late_shared_settled_attempts_do_not_accept_new_subscribers) {
    Temp t;Store store(t.path/"db");Fake worker;WebService web(store,worker,{});
    web.submit_reads("alice",args(urls(1),"late-settled-payer"));const auto lease=store.lease_next_read();CHECK(lease);Json admitted;
    worker.response=[&](const Json& p) {admitted=web.submit_reads("alice",args(urls(1),"late-settled-member"));return Fake::ok(p);};
    web.execute_read(*lease);worker.response={};CHECK(admitted.at("state").str()=="running");
    const auto next=web.submit_reads("alice",args(urls(1),"late-after-settlement"));CHECK(next.at("state").str()=="queued");
    CHECK(!item(store,next.at("job_id").str()).has("shared_read"));drain(store,web);CHECK(worker.reads.size()==2);
    CHECK(store.job_get("alice",next.at("job_id").str()).at("state").str()=="succeeded");
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
      CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==14);
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
