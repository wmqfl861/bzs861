#include "bzs/service.hpp"
#include "bzs/search_modes.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <chrono>
#include <functional>
#include <iostream>
#include <thread>
#include <tuple>
#include <vector>
using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> value;return value; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add reg_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); }while(false)
template<class F> void rejects(F fn,const std::string& code) { ++assertions;try { fn(); }catch(const Error& e) { if(e.code==code)return;throw std::runtime_error("expected "+code+", got "+e.code); }throw std::runtime_error("expected "+code); }
struct Temp { std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-search-modes-"+random_id());Temp() { std::filesystem::create_directories(path); }~Temp() { std::error_code e;std::filesystem::remove_all(path,e); } };
Json manifest(std::int64_t count=10,std::int64_t quota=10000) {
  auto value=Json::object(),sources=Json::array();value.set("version",std::int64_t{1});
  for(std::int64_t i=0;i<count;++i) {
    auto source=Json::parse(R"({"id":"source","provider":"synthetic","index_family":"family","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":10000,"params":{"adapter":"offline_fixture","base_url":"https://search.example/search"},"contract":{"id":"source","provider_id":"synthetic","index_family":"family","logical_service_id":"service","capability":"web","endpoint":"https://search.example/search","adapter_id":"offline_fixture","enabled":true,"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://search.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-mode-fixture"},"limits":{"shared_budget_key":"service","max_concurrency":1,"min_interval_ms":0,"request_quota":10000,"quota_window_seconds":86400}}})");
    const auto id="source"+std::to_string(i);source.set("id",id);source.set("index_family",id);source.set("daily_cap",quota);
    source.at("contract").set("id",id);source.at("contract").set("index_family",id);source.at("contract").set("logical_service_id",id);source.at("contract").at("limits").set("shared_budget_key",id);source.at("contract").at("limits").set("request_quota",quota);sources.push(source);
  }
  value.set("sources",sources);return value;
}
Json input(const std::string& mode="",bool persistent=false) { auto value=Json::object();value.set("query","mode fixture");if(!mode.empty())value.set("mode",mode);if(persistent)value.set("idempotency_key","mode-search");return value; }
struct Worker:WorkerPort {
  std::vector<Json> requests;std::vector<int> timeouts;std::function<void()> during;
  Json call(const std::string& method,const Json& args,int timeout)override {
    CHECK(method=="search.multi");requests.push_back(Json::parse(args.dump()));timeouts.push_back(timeout);if(during)during();
    auto response=Json::object(),results=Json::object();const auto ids=args.at("sources");
    for(std::size_t source=0;source<ids.size();++source) {
      auto entry=Json::object(),hits=Json::array();
      for(std::int64_t i=0;i<30;++i) { auto hit=Json::object();hit.set("url","https://results.example/"+ids.at(source).str()+"/"+std::to_string(i));hit.set("title","mode marker");hit.set("snippet","mode snippet");hit.set("rank",i+1);hits.push(hit); }
      entry.set("status","ok");entry.set("hits",hits);results.set(ids.at(source).str(),entry);
    }
    response.set("results",results);return response;
  }
};
void drain(WebService& web) { for(int i=0;i<21;++i) { auto lease=web.lease_search();if(!lease)return;web.execute_search(*lease); }throw std::runtime_error("source lease bound exceeded"); }
TEST(mode_catalog_is_offline_and_describes_actual_limits) {
  Temp t;Store store(t.path/"db");FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service service(store,fixtures,t.path/"backups");auto request=Json::object();request.set("operation","search.modes");request.set("params",Json::object());const auto modes=service.dispatch({"alice",false},request);
  CHECK(modes.at("modes").size()==3);CHECK(modes.at("scope").str()=="search_only");CHECK(!modes.at("quality_guarantee").flag());
  for(std::size_t i=0;i<3;++i) { const auto budget=modes.at("modes").at(i).at("budget");CHECK(budget.at("max_reads").num()==0);CHECK(budget.at("max_model_calls").num()==0);CHECK(budget.at("max_browser_calls").num()==0); }
  request.at("params").set("mode","fast");rejects([&]{service.dispatch({"alice",false},request);},"E_SCHEMA");
}
TEST(omitted_mode_preserves_legacy_sync_and_session_defaults) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(3)));
  auto direct=web.search("alice",input());CHECK(direct.at("effective_mode").str()=="legacy");CHECK(direct.at("result_count").num()==10);CHECK(worker.requests.at(0).at("sources").size()==3);CHECK(worker.requests.at(0).at("limit").num()==10);
  const auto durable=web.submit_search("alice",input("",true));CHECK(durable.at("effective_mode").str()=="legacy");CHECK(durable.at("effective_budget").at("limit").num()==20);CHECK(durable.at("effective_budget").at("deadline_ms").num()==120000);
}
TEST(all_modes_bound_actual_source_fanout_candidates_and_rpc) {
  for(const auto& [name,count,per,timeout]:std::vector<std::tuple<std::string,std::int64_t,std::int64_t,std::int64_t>>{{"fast",2,5,4000},{"balanced",4,10,8000},{"deep",8,20,15000}}) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto result=web.search("alice",input(name));
    CHECK(worker.requests.size()==1);CHECK(worker.requests.at(0).at("sources").size()==static_cast<std::size_t>(count));CHECK(worker.requests.at(0).at("limit").num()==per);CHECK(worker.requests.at(0).at("timeout_ms").num()<=timeout);
    CHECK(result.at("effective_mode").str()==name);CHECK(result.at("effective_budget").at("max_sources").num()==count);CHECK(result.at("omitted_sources").size()==static_cast<std::size_t>(10-count));CHECK(result.at("omitted_sources").at(0).str()=="source"+std::to_string(count));CHECK(result.at("usage").at("external_requests").num()==count);
  }
}
TEST(explicit_source_selection_cannot_silently_exceed_preset) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));auto request=input("fast");request.set("sources",Json::parse(R"(["source0","source1","source2"])"));
  rejects([&]{web.search("alice",request);},"E_SCHEMA");request.set("idempotency_key","too-many");rejects([&]{web.submit_search("alice",request);},"E_SCHEMA");CHECK(worker.requests.empty());
}
TEST(duplicate_source_ids_do_not_consume_multiple_attempt_slots) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));auto request=input("fast");request.set("sources",Json::parse(R"(["source1","source1","source0"])"));const auto result=web.search("alice",request);CHECK(worker.requests.at(0).at("sources").size()==2);CHECK(result.at("omitted_sources").size()==0);
}
TEST(overrides_can_only_tighten_every_preset_dimension) {
  for(const auto& [key,value]:std::vector<std::pair<std::string,std::int64_t>>{{"max_sources",3},{"limit",11},{"per_source",6},{"timeout_ms",4001},{"deadline_ms",20001}}) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));auto request=input("fast");request.set(key,value);rejects([&]{web.search("alice",request);},"E_SCHEMA");request.set("idempotency_key","bad");rejects([&]{web.submit_search("alice",request);},"E_SCHEMA");CHECK(worker.requests.empty());
  }
}
TEST(narrowed_override_is_the_executed_budget) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));auto request=input("deep");request.set("max_sources",std::int64_t{1});request.set("limit",std::int64_t{2});request.set("per_source",std::int64_t{3});request.set("timeout_ms",std::int64_t{1200});request.set("deadline_ms",std::int64_t{2000});const auto result=web.search("alice",request);
  CHECK(worker.requests.at(0).at("sources").size()==1);CHECK(worker.requests.at(0).at("limit").num()==3);CHECK(worker.requests.at(0).at("timeout_ms").num()<=1200);CHECK(worker.timeouts.at(0)<=2000);CHECK(result.at("result_count").num()==2);CHECK(result.at("effective_budget").at("deadline_ms").num()==2000);
}
TEST(mode_never_bypasses_source_review_or_shared_quota) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(2,1)));auto request=input("fast");web.search("alice",request);request.set("refresh",true);const auto limited=web.search("alice",request);CHECK(worker.requests.size()==1);CHECK(limited.at("usage").at("external_requests").num()==0);CHECK(limited.at("partial").flag());
  auto invalid=manifest(1);invalid.at("sources").at(0).at("contract").at("review").set("status","candidate");WebService denied(store,worker,SourceRegistry(invalid));rejects([&]{denied.search("alice",input("deep"));},"E_SOURCE_REVIEW");CHECK(worker.requests.size()==1);
}
TEST(cache_is_bound_to_mode_and_effective_overrides) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));web.search("alice",input("fast"));CHECK(web.search("alice",input("fast")).at("from_cache").flag());CHECK(!web.search("alice",input("balanced")).at("from_cache").flag());CHECK(worker.requests.size()==2);
  auto narrowed=input("fast");narrowed.set("max_sources",std::int64_t{1});CHECK(!web.search("alice",narrowed).at("from_cache").flag());CHECK(worker.requests.size()==3);
}
TEST(persistent_mode_and_omissions_survive_restart_without_reexecution) {
  Temp t;Worker worker;std::string id,version,budget;
  { Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest()));const auto submitted=web.submit_search("alice",input("balanced",true));id=submitted.at("session_id").str();budget=submitted.at("effective_budget").canonical();CHECK(submitted.at("omitted_sources").size()==6);drain(web);const auto state=store.session_get("alice",id);version=state.at("result_set_version").str();CHECK(state.at("dispatched_source_attempts").num()==4);CHECK(worker.requests.size()==4); }
  { Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest()));const auto state=store.session_get("alice",id);CHECK(state.at("effective_budget").canonical()==budget);CHECK(state.at("omitted_sources").size()==6);const auto page=store.session_page("alice",id,version,"",20);CHECK(page.at("effective_mode").str()=="balanced");CHECK(page.at("effective_budget").canonical()==budget);CHECK(web.submit_search("alice",input("balanced",true)).at("reused").flag());CHECK(worker.requests.size()==4);rejects([&]{web.submit_search("alice",input("fast",true));},"E_IDEMPOTENCY_CONFLICT"); }
}
TEST(durable_dispatch_passes_only_remaining_deadline_to_worker) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));web.submit_search("alice",input("fast",true));now+=18500;const auto lease=web.lease_search();CHECK(lease);web.execute_search(*lease);CHECK(worker.requests.size()==1);CHECK(worker.requests.at(0).at("timeout_ms").num()<=1500);CHECK(worker.timeouts.at(0)<=1500);
}
void subsecond_session_dispatch(const std::string& mode) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  auto request=input(mode,true);request.set("deadline_ms",std::int64_t{1000});request.set("timeout_ms",std::int64_t{1000});
  const auto submitted=web.submit_search("alice",request),budget=submitted.at("effective_budget");const auto deadline=submitted.at("deadline_ms").num();
  now+=500;const auto lease=web.lease_search();CHECK(lease);CHECK(lease->deadline_ms-now==500);web.execute_search(*lease);
  CHECK(worker.requests.size()==1);CHECK(worker.requests.at(0).at("timeout_ms").num()>0);CHECK(worker.requests.at(0).at("timeout_ms").num()<=500);
  CHECK(worker.timeouts.at(0)>0);CHECK(worker.timeouts.at(0)<=500);
  const auto state=store.session_get("alice",submitted.at("session_id").str());CHECK(state.at("state").str()=="completed");CHECK(!state.at("result_set_version").str().empty());
  CHECK(state.at("deadline_ms").num()==deadline);CHECK(state.at("effective_budget").canonical()==budget.canonical());CHECK(state.at("unknown_source_attempts").num()==0);
}
TEST(legacy_durable_search_dispatches_with_500ms_remaining_without_extending_deadline) { subsecond_session_dispatch(""); }
TEST(explicit_mode_durable_search_dispatches_with_500ms_remaining_without_extending_deadline) { subsecond_session_dispatch("fast"); }
TEST(public_subsecond_timeout_and_deadline_remain_invalid) {
  for(const std::string mode:{"","fast"})for(const auto* field:{"timeout_ms","deadline_ms"}) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));auto request=input(mode);request.set(field,std::int64_t{500});
    rejects([&]{web.search("alice",request);},"E_SCHEMA");request.set("idempotency_key","public-subsecond");rejects([&]{web.submit_search("alice",request);},"E_SCHEMA");CHECK(worker.requests.empty());
  }
}
TEST(expired_durable_deadline_never_dispatches_or_publishes) {
  for(const std::string mode:{"","fast"}) {
    Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
    auto request=input(mode,true);request.set("deadline_ms",std::int64_t{1000});const auto submitted=web.submit_search("alice",request);const auto lease=web.lease_search();CHECK(lease);
    now+=1000;web.execute_search(*lease);CHECK(worker.requests.empty());const auto state=store.session_get("alice",submitted.at("session_id").str());
    CHECK(state.at("state").str()=="failed");CHECK(state.at("error_code").str()=="E_DEADLINE");CHECK(state.at("result_set_version").str().empty());CHECK(!web.lease_search());
  }
}
TEST(late_response_is_not_published_or_cached_after_monotonic_deadline) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));auto request=input("fast");request.set("deadline_ms",std::int64_t{1000});worker.during=[] { std::this_thread::sleep_for(std::chrono::milliseconds(1100)); };
  rejects([&]{web.search("alice",request);},"E_DEADLINE");CHECK(worker.requests.size()==1);worker.during={};CHECK(!web.search("alice",request).at("from_cache").flag());CHECK(worker.requests.size()==2);
}
TEST(mode_json_types_are_strict_and_unknown_modes_are_rejected) {
  for(const auto& change:std::vector<std::string>{R"({"mode":null})",R"({"mode":true})",R"({"mode":"FAST"})",R"({"mode":"legacy"})",R"({"max_sources":true})",R"({"limit":"2"})",R"({"deadline_ms":1.5})"}) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));auto request=Json::parse(change);request.set("query","mode fixture");rejects([&]{web.search("alice",request);},"E_SCHEMA");CHECK(worker.requests.empty());
  }
}
}
int main(int argc,char** argv) {
  std::size_t failed=0,executed=0;for(const auto& [name,test]:tests()) { if(argc>1&&name!=argv[1])continue;++executed;try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; } }
  std::cout<<executed<<" search mode tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed||!executed?1:0;
}
