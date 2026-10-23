#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <functional>
#include <iostream>
#include <memory>
#include <vector>
using namespace bzs;
namespace {
std::size_t assertions=0;
#define CHECK(c) do { ++assertions;if(!(c))throw std::runtime_error(std::string("failed: ")+#c+" line "+std::to_string(__LINE__)); }while(false)
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-shared-independent-"+random_id());
  Temp(){std::filesystem::create_directories(path);}
  ~Temp(){std::error_code e;std::filesystem::remove_all(path,e);}
};
const std::string target="https://independent.example/page";
std::string fixture_directory;
Json publication(const std::string& text,bool snippets=true) {
  auto value=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");
  value.set("snippet_allowed",snippets);value.set("snippet_text",snippets?text:"");value.set("snippet_title",snippets?"independent title":"");
  if(!snippets)value.set("directives",Json::parse(R"(["nosnippet"])"));
  return value;
}
Json ingest(Store& store,const std::string& text) {
  ParsedDocument parsed;parsed.title="independent title";parsed.text=text;parsed.content_type="text/plain";parsed.parser_version="independent/1";
  parsed.publication=publication(text);auto meta=Json::object();meta.set("final_url",target);meta.set("redirects",Json::array());
  return store.ingest_web("alice",{target,text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",target));
}
Json args(const std::string& key,std::int64_t attempts=1) {
  auto value=Json::object();auto urls=Json::array();urls.push(Json::string(target));value.set("urls",urls);value.set("idempotency_key",key);value.set("max_attempts",attempts);return value;
}
struct Worker:WorkerPort {
  std::size_t reads=0;std::vector<std::string> cancellations;std::function<void()> during;
  Json call(const std::string& method,const Json& params,int timeout)override {
    CHECK(timeout>0);
    if(method=="fetch.cancel") { cancellations.push_back(params.at("attempt_id").str());return Json::object(); }
    CHECK(method=="fetch.extract");++reads;if(during)during();
    auto response=Json::parse(R"({"status":"ok","http_status":200,"html":"staleneedle private body","text":"staleneedle private body","title":"independent title","content_type":"text/plain","extractor":"independent/1","index_allowed":true,"links":[],"redirects":[],"usage":{"external_requests":1,"content_bytes":22}})");
    response.set("final_url",params.at("url"));response.set("publication",publication("staleneedle private body"));return response;
  }
};
Json item(Store& store,const std::string& id) { return store.job_results("alice",id).at("items").at(std::size_t{0}); }
std::int64_t scalar(sqlite3* db,const char* query) {
  sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query,-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);
  CHECK(sqlite3_step(raw)==SQLITE_ROW);return sqlite3_column_int64(raw,0);
}
void control(Service& service,const std::string& id,const std::string& action) {
  auto params=Json::object();params.set("job_id",id);params.set("action",action);
  auto request=Json::object();request.set("operation","job.control");request.set("params",params);service.dispatch({"alice",false},request);
}
void three_members_keep_independent_cancellation_and_publication_fences() {
  Temp temp;Store store(temp.path/"db");Worker worker;WebService web(store,worker,{});
  FixtureCatalog fixtures(fixture_directory);Service service(store,fixtures,temp.path/"backups",&web);
  ingest(store,"initialneedle body");
  const auto payer=web.submit_reads("alice",args("three-payer")).at("job_id").str();
  const auto paused=web.submit_reads("alice",args("three-paused")).at("job_id").str();
  const auto live=web.submit_reads("alice",args("three-live")).at("job_id").str();
  const auto lease=store.lease_next_read();CHECK(lease);CHECK(lease->job_id==payer);
  CHECK(item(store,payer).at("shared_read").at("members").num()==3);
  std::string new_head;
  worker.during=[&] {
    control(service,payer,"cancel");control(service,paused,"pause");CHECK(worker.cancellations.empty());CHECK(store.read_lease_live(*lease));
    const auto fence=store.publication_fence("alice",target);
    store.observe_web_publication("alice",target,target,publication("",false),fence);
    // A newly permitted replacement becomes current while this old shared
    // request is in flight. Its older response must retain its snippet barrier.
    new_head=ingest(store,"freshneedle permitted body").at("snapshot_id").str();
  };
  web.execute_read(*lease);CHECK(worker.reads==1);CHECK(worker.cancellations.empty());
  CHECK(store.job_get("alice",payer).at("state").str()=="cancelled");CHECK(store.job_get("alice",paused).at("state").str()=="paused");
  CHECK(!item(store,payer).has("receipt"));CHECK(!item(store,paused).has("receipt"));CHECK(item(store,live).at("state").str()=="succeeded");
  const auto late=item(store,live).at("receipt").at("snapshot_id").str();CHECK(late!=new_head);
  CHECK(store.cached("alice",target)->at("snapshot_id").str()==new_head);
  CHECK(store.snapshot("alice",late).at("canonical_text").str()=="staleneedle private body");
  CHECK(!store.snapshot("alice",late).at("publication").at("snippet_allowed").flag());
  CHECK(store.job_get("alice",payer).at("usage").at("external_requests").num()==1);
  CHECK(store.job_get("alice",paused).at("usage").at("external_requests").num()==0);
  CHECK(store.job_get("alice",live).at("usage").at("external_requests").num()==0);
  CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1 AND external_requests=1")==1);
  CHECK(!store.complete_read(*lease,{target,"second late response","text/plain","late"},parse_document("second late response","text/plain"),Json::object(),1,20));
  CHECK(store.job_get("alice",payer).at("usage").at("external_requests").num()==1);
  std::cout<<"PASS three_members_cancel_pause_and_publication_release_keep_old_result_sealed\n";
}
void unknown_attempt_can_rejoin_with_new_payer_without_overwriting_history() {
  Temp temp;std::int64_t now=1000000;Worker worker;std::string original,subscriber,newcomer;ReadLease old;
  {
    Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{});
    original=web.submit_reads("alice",args("history-payer",2)).at("job_id").str();
    subscriber=web.submit_reads("alice",args("history-subscriber",2)).at("job_id").str();
    old=*store.lease_next_read();CHECK(old.job_id==original);CHECK(item(store,subscriber).at("shared_read").at("role").str()=="subscriber");
  }
  now+=26000;
  {
    Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{});
    CHECK(!store.lease_next_read());CHECK(worker.reads==0);
    CHECK(store.job_get("alice",original).at("usage").at("unknown_requests_charged").num()==18);
    CHECK(store.job_get("alice",subscriber).at("usage").at("unknown_requests_charged").num()==0);
    store.job_control("alice",subscriber,"retry_failed");
    newcomer=web.submit_reads("alice",args("history-newcomer",2)).at("job_id").str();
    auto retry=store.lease_next_read();CHECK(retry);CHECK(retry->job_id==subscriber);CHECK(retry->attempt_id!=old.attempt_id);
    CHECK(item(store,subscriber).at("shared_read").at("role").str()=="payer");CHECK(item(store,newcomer).at("shared_read").at("role").str()=="subscriber");
    CHECK(!store.complete_read(old,{target,"old body","text/plain","old"},parse_document("old body","text/plain"),Json::object(),1,8));
    store.fail_read(old,"E_FETCH_HTTP",1,8);
    CHECK(store.job_get("alice",subscriber).at("shared_reads").at("unsettled_attempts").num()==1);
    web.execute_read(*retry);CHECK(worker.reads==1);CHECK(store.stats("alice").at("snapshots").num()==1);
    CHECK(item(store,subscriber).at("state").str()=="succeeded");CHECK(item(store,newcomer).at("state").str()=="succeeded");
    CHECK(store.job_get("alice",original).at("budget").at("charged_requests").num()==18);
    CHECK(store.job_get("alice",subscriber).at("budget").at("charged_requests").num()==1);
    CHECK(store.job_get("alice",newcomer).at("budget").at("charged_requests").num()==0);
    CHECK(store.job_get("alice",subscriber).at("shared_reads").at("attempts").num()==2);
    CHECK(store.job_get("alice",subscriber).at("shared_reads").at("unknown_attempts").num()==1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1")==2);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE external_requests=-1")==1);
    CHECK(scalar(store.raw_handle(),"SELECT SUM(external_requests) FROM read_shared_attempts WHERE external_requests>=0")==1);
    now+=8LL*24*60*60*1000;
    std::int64_t compacted=0;for(int i=0;i<4;++i)compacted+=store.compact_jobs("alice",1).at("compacted_jobs").num();CHECK(compacted==3);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts")==0);
    CHECK(store.job_get("alice",original).at("usage").at("unknown_requests_charged").num()==18);
    CHECK(store.job_get("alice",subscriber).at("shared_reads").at("attempts").num()==2);
    CHECK(store.job_get("alice",subscriber).at("shared_reads").at("unknown_attempts").num()==1);
    CHECK(store.job_get("alice",newcomer).at("usage").at("external_requests").num()==0);
    CHECK(!store.complete_read(old,{target,"obsolete body","text/plain","old"},parse_document("obsolete body","text/plain"),Json::object(),1,13));
  }
  std::cout<<"PASS unknown_restart_new_group_new_payer_late_settlement_and_compaction\n";
}
void second_receipt_failure_rolls_back_the_first_receipt_and_its_accounting() {
  Temp temp;Store store(temp.path/"db");Worker worker;WebService web(store,worker,{});std::vector<std::string> ids;
  for(const auto* key:{"receipt-one","receipt-two","receipt-three"})ids.push_back(web.submit_reads("alice",args(key)).at("job_id").str());
  CHECK(sqlite3_exec(store.raw_handle(),R"SQL(
CREATE TRIGGER fail_second_shared_receipt BEFORE UPDATE OF state ON read_job_items
WHEN NEW.state='succeeded' AND EXISTS(SELECT 1 FROM read_job_items prior
  WHERE prior.attempt_id=NEW.attempt_id AND prior.state='succeeded')
BEGIN SELECT RAISE(ABORT,'second receipt failure'); END;
)SQL",nullptr,nullptr,nullptr)==SQLITE_OK);
  const auto lease=store.lease_next_read();CHECK(lease);web.execute_read(*lease);CHECK(worker.reads==1);
  CHECK(store.stats("alice").at("snapshots").num()==0);
  for(const auto& id:ids) {
    CHECK(item(store,id).at("state").str()=="failed");CHECK(item(store,id).at("error_code").str()=="E_STORAGE");CHECK(!item(store,id).has("receipt"));
    const auto state=store.job_get("alice",id);CHECK(state.at("usage").at("reserved_requests").num()==0);
    CHECK(state.at("usage").at("external_requests").num()==(id==lease->job_id?1:0));
    CHECK(state.at("budget").at("charged_requests").num()==(id==lease->job_id?1:0));
  }
  CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts WHERE settled=1 AND external_requests=1")==1);
  CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_job_items WHERE snapshot_id IS NOT NULL OR receipt_json IS NOT NULL")==0);
  CHECK(!store.lease_next_read());
  std::cout<<"PASS second_receipt_failure_rolls_back_prior_receipt_snapshot_and_charge\n";
}
}
int main(int argc,char** argv) {
  if(argc!=2) { std::cerr<<"Usage: independent_shared_read_probe FIXTURE_DIRECTORY\n";return 2; }
  fixture_directory=argv[1];
  std::size_t failures=0;
  for(const auto& test:std::vector<std::pair<const char*,std::function<void()>>>{{"three-member publication",three_members_keep_independent_cancellation_and_publication_fences},{"retry history",unknown_attempt_can_rejoin_with_new_payer_without_overwriting_history},{"second receipt rollback",second_receipt_failure_rolls_back_the_first_receipt_and_its_accounting}}) {
    try{test.second();}catch(const std::exception& e){++failures;std::cerr<<"FAIL "<<test.first<<": "<<e.what()<<'\n';}
  }
  std::cout<<"3 independent shared-read probes, "<<assertions<<" assertions, "<<failures<<" failures\n";return failures?1:0;
}
