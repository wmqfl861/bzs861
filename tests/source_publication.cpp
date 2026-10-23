#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <functional>
#include <sqlite3.h>
#include <iostream>
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
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-source-publication-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code e;std::filesystem::remove_all(path,e); }
};
Json manifest() {
  return Json::parse(R"({"version":1,"sources":[{"id":"source0","provider":"synthetic","index_family":"family0","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":10000,"params":{"adapter":"offline_fixture","base_url":"https://search.example/search"},"contract":{"id":"source0","provider_id":"synthetic","index_family":"family0","logical_service_id":"service0","capability":"web","endpoint":"https://search.example/search","adapter_id":"offline_fixture","enabled":true,"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://search.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-source-policy-fixture"},"limits":{"shared_budget_key":"service0","max_concurrency":1,"min_interval_ms":0,"request_quota":10000,"quota_window_seconds":86400}}}]})");
}
Json input(const std::string& key="request") { auto value=Json::object();value.set("query","marker");value.set("idempotency_key",key);return value; }
Json disabled() { auto value=manifest();auto source=value.at("sources").at(0);source.set("enabled",false);source.at("contract").set("enabled",false);return value; }
Json links_only() { auto value=manifest();value.at("sources").at(0).at("contract").at("review").set("redistribution_policy","links_only");return value; }
struct Worker:WorkerPort {
  std::size_t requests=0;std::function<void()> during;
  Json call(const std::string& method,const Json& args,int)override {
    CHECK(method=="search.multi");++requests;if(during)during();
    auto response=Json::object(),results=Json::object();const auto ids=args.at("sources");
    for(std::size_t source=0;source<ids.size();++source) {
      auto entry=Json::object(),hits=Json::array();
      for(std::int64_t i=0;i<3;++i) { auto hit=Json::object();hit.set("url","https://results.example/"+std::to_string(i));hit.set("title","private marker title "+std::to_string(i));hit.set("snippet","private marker snippet "+std::to_string(i));hit.set("rank",i+1);hits.push(hit); }
      entry.set("status","ok");entry.set("hits",hits);results.set(ids.at(source).str(),entry);
    }
    response.set("results",results);return response;
  }
};
std::pair<std::string,std::string> complete(Store& store,WebService& web,const std::string& key="request") {
  const auto id=web.submit_search("alice",input(key)).at("session_id").str();const auto lease=web.lease_search();CHECK(lease);web.execute_search(*lease);auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="completed");return {id,state.at("result_set_version").str()};
}
TEST(disabling_source_withdraws_existing_frozen_page) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  const auto [id,version]=complete(store,original);CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==3);
  WebService restricted(store,worker,SourceRegistry(disabled()));
  rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");CHECK(worker.requests==1);
}
TEST(links_only_source_suppresses_existing_frozen_snippets) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  const auto [id,version]=complete(store,original);WebService restricted(store,worker,SourceRegistry(links_only()));
  const auto page=store.session_page("alice",id,version,"",20);CHECK(page.at("hits").size()==3);
  for(std::size_t i=0;i<page.at("hits").size();++i) { const auto hit=page.at("hits").at(i);CHECK(hit.at("snippet").str().empty());CHECK(hit.at("title").str()==hit.at("url").str()); }
  CHECK(worker.requests==1);
}
Json query() { auto value=Json::object();value.set("query","marker");return value; }
void suppressed(const Json& hits) {
  CHECK(hits.size()==3);
  for(std::size_t i=0;i<hits.size();++i) { CHECK(hits.at(i).at("snippet").str().empty());CHECK(hits.at(i).at("title").str()==hits.at(i).at("url").str()); }
}
TEST(direct_search_enforces_current_links_only_review) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(links_only()));
  suppressed(web.search("alice",query()).at("results"));suppressed(web.search("alice",query()).at("results"));CHECK(worker.requests==1);
}
TEST(cache_replay_cannot_restore_snippets_after_temporary_restriction) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  CHECK(!original.search("alice",query()).at("results").at(0).at("snippet").str().empty());
  store.activate_source_publication(SourceRegistry(links_only()));
  rejects([&]{original.search("alice",query());},"E_SOURCE_POLICY_STALE");CHECK(worker.requests==1);
  store.activate_source_publication(SourceRegistry(manifest()));
  const auto replay=original.search("alice",query());CHECK(replay.at("from_cache").flag());suppressed(replay.at("results"));CHECK(worker.requests==1);
  auto fresh=query();fresh.set("refresh",true);CHECK(!original.search("alice",fresh).at("results").at(0).at("snippet").str().empty());CHECK(worker.requests==2);
}
TEST(withdrawn_cached_response_is_replaced_only_by_new_authorized_attempt) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  original.search("alice",query());store.activate_source_publication(SourceRegistry(disabled()));store.activate_source_publication(SourceRegistry(manifest()));
  CHECK(!original.search("alice",query()).at("from_cache").flag());CHECK(worker.requests==2);
}
TEST(frozen_links_only_survives_reopen_and_later_relaxation) {
  Temp t;Worker worker;std::string id,version;
  { Store store(t.path/"db");WebService original(store,worker,SourceRegistry(manifest()));std::tie(id,version)=complete(store,original); }
  { Store store(t.path/"db");store.activate_source_publication(SourceRegistry(links_only())); }
  { Store store(t.path/"db");suppressed(store.session_page("alice",id,version,"",20).at("hits"));store.activate_source_publication(SourceRegistry(manifest())); }
  { Store store(t.path/"db");suppressed(store.session_page("alice",id,version,"",20).at("hits"));WebService fresh(store,worker,SourceRegistry(manifest()));const auto result=complete(store,fresh,"fresh");CHECK(!store.session_page("alice",result.first,result.second,"",20).at("hits").at(0).at("snippet").str().empty()); }
}
TEST(explicit_empty_registry_retires_source_but_omission_preserves_it) {
  Temp t;Worker worker;Store store(t.path/"db");WebService original(store,worker,SourceRegistry(manifest()));const auto [id,version]=complete(store,original);
  WebService reads_only(store,worker,SourceRegistry());CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==3);
  WebService retired(store,worker,SourceRegistry(Json::parse(R"({"version":1,"sources":[]})")));
  rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");
  store.activate_source_publication(SourceRegistry(manifest()));rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");
}
TEST(disabled_source_remains_withdrawn_after_reopen_and_reenable) {
  Temp t;Worker worker;std::string id,version;
  { Store store(t.path/"db");WebService original(store,worker,SourceRegistry(manifest()));std::tie(id,version)=complete(store,original);store.activate_source_publication(SourceRegistry(disabled())); }
  { Store store(t.path/"db");rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");store.activate_source_publication(SourceRegistry(manifest())); }
  { Store store(t.path/"db");rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED"); }
}
TEST(expiry_persists_even_when_page_rejection_and_clock_rollback_follow) {
  Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Worker worker;auto reviewed=manifest();reviewed.at("sources").at(0).at("contract").at("review").set("expires_at","1970-01-01T00:17:00Z");WebService original(store,worker,SourceRegistry(reviewed));const auto [id,version]=complete(store,original);
  now=1020001;rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");
  now=1000000;rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");rejects([&]{original.search("alice",query());},"E_SOURCE_REVIEW");
  store.activate_source_publication(SourceRegistry(manifest()));rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");CHECK(worker.requests==1);
}
TEST(renewal_after_unobserved_expiry_does_not_revive_old_results) {
  Temp t;std::int64_t now=1000000;Worker worker;std::string id,version;
  { Store store(t.path/"db",[&]{return now;});auto reviewed=manifest();reviewed.at("sources").at(0).at("contract").at("review").set("expires_at","1970-01-01T00:17:00Z");WebService web(store,worker,SourceRegistry(reviewed));std::tie(id,version)=complete(store,web); }
  now=1020001;
  { Store store(t.path/"db",[&]{return now;});store.activate_source_publication(SourceRegistry(manifest()));now=1000000;rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED"); }
}
TEST(stale_store_cannot_replace_more_recent_source_manifest) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));Store stale(t.path/"db");const auto [id,version]=complete(store,original);
  store.activate_source_publication(SourceRegistry(links_only()));
  rejects([&]{stale.activate_source_publication(SourceRegistry(manifest()));},"E_SOURCE_POLICY_STALE");suppressed(stale.session_page("alice",id,version,"",20).at("hits"));
}
TEST(inflight_direct_response_keeps_intervening_snippet_ceiling) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  worker.during=[&]{store.activate_source_publication(SourceRegistry(links_only()));store.activate_source_publication(SourceRegistry(manifest()));};
  suppressed(original.search("alice",query()).at("results"));CHECK(worker.requests==1);
}
TEST(inflight_direct_response_cannot_cross_withdrawal_and_reenable) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  worker.during=[&]{store.activate_source_publication(SourceRegistry(disabled()));store.activate_source_publication(SourceRegistry(manifest()));};
  rejects([&]{original.search("alice",query());},"E_SOURCE_POLICY_CHANGED");CHECK(worker.requests==1);
  worker.during={};CHECK(!original.search("alice",query()).at("from_cache").flag());CHECK(worker.requests==2);
}
TEST(inflight_frozen_response_keeps_intervening_snippet_ceiling) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));
  worker.during=[&]{store.activate_source_publication(SourceRegistry(links_only()));store.activate_source_publication(SourceRegistry(manifest()));};
  const auto [id,version]=complete(store,original);suppressed(store.session_page("alice",id,version,"",20).at("hits"));
}
TEST(queued_session_cannot_dispatch_after_withdrawal_and_reenable) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));const auto id=original.submit_search("alice",input()).at("session_id").str();
  store.activate_source_publication(SourceRegistry(disabled()));store.activate_source_publication(SourceRegistry(manifest()));
  CHECK(!original.lease_search());CHECK(store.session_get("alice",id).at("state").str()=="failed");CHECK(worker.requests==0);
}
TEST(source_endpoint_change_with_same_id_withdraws_only_old_results) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));const auto [id,version]=complete(store,original);
  auto replacement=manifest();replacement.at("sources").at(0).at("params").set("base_url","https://replacement.example/search");replacement.at("sources").at(0).at("contract").set("endpoint","https://replacement.example/search");WebService current(store,worker,SourceRegistry(replacement));
  rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");const auto fresh=complete(store,current,"fresh");CHECK(!store.session_page("alice",fresh.first,fresh.second,"",20).at("hits").at(0).at("snippet").str().empty());
}
TEST(restriction_preserves_frozen_rank_and_cursor_identity) {
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(manifest()));const auto [id,version]=complete(store,original);
  const auto first=store.session_page("alice",id,version,"",1);const auto cursor=first.at("next_cursor").str();const auto second=store.session_page("alice",id,version,cursor,1);
  store.activate_source_publication(SourceRegistry(links_only()));const auto restricted=store.session_page("alice",id,version,cursor,1);
  CHECK(restricted.at("result_set_version").str()==version);CHECK(restricted.at("hits").at(0).at("url").str()==second.at("hits").at(0).at("url").str());CHECK(restricted.at("hits").at(0).at("position").num()==second.at("hits").at(0).at("position").num());CHECK(restricted.at("hits").at(0).at("snippet").str().empty());CHECK(restricted.at("next_cursor").str()==second.at("next_cursor").str());
}

TEST(rejected_expired_search_commits_withdrawal_before_clock_rollback) {
  for(const bool persistent:{false,true}) {
    Temp t;std::int64_t now=1000000;Store store(t.path/"db",[&]{return now;});Worker worker;auto reviewed=manifest();reviewed.at("sources").at(0).at("contract").at("review").set("expires_at","1970-01-01T00:17:00Z");WebService web(store,worker,SourceRegistry(reviewed));
    const auto [id,version]=complete(store,web);now=1020001;
    rejects([&]{if(persistent)web.submit_search("alice",input("expired"));else web.search("alice",query());},"E_SOURCE_REVIEW");
    now=1000000;rejects([&]{store.session_page("alice",id,version,"",20);},"E_SESSION_INVALIDATED");CHECK(worker.requests==1);
  }
}
TEST(one_restricted_contributor_suppresses_fused_content) {
  auto mixed=manifest();auto second=Json::parse(mixed.at("sources").at(0).dump());second.set("id","source1");second.set("index_family","family1");second.at("contract").set("id","source1");second.at("contract").set("index_family","family1");second.at("contract").set("logical_service_id","service1");second.at("contract").at("limits").set("shared_budget_key","service1");mixed.at("sources").push(second);
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(mixed));const auto id=web.submit_search("alice",input()).at("session_id").str();
  for(int i=0;i<2;++i) { const auto lease=web.lease_search();CHECK(lease);web.execute_search(*lease); }
  const auto version=store.session_get("alice",id).at("result_set_version").str();CHECK(store.session_page("alice",id,version,"",20).at("hits").at(0).at("sources").size()==2);
  mixed.at("sources").at(1).at("contract").at("review").set("redistribution_policy","links_only");store.activate_source_publication(SourceRegistry(mixed));suppressed(store.session_page("alice",id,version,"",20).at("hits"));
}
TEST(withdrawal_of_unrelated_source_preserves_frozen_allowed_results) {
  auto extra=manifest();auto disabled_source=disabled().at("sources").at(0);disabled_source.set("id","source1");disabled_source.at("contract").set("id","source1");extra.at("sources").push(disabled_source);
  Temp t;Store store(t.path/"db");Worker worker;WebService original(store,worker,SourceRegistry(extra));const auto [id,version]=complete(store,original);
  store.activate_source_publication(SourceRegistry(manifest()));CHECK(!store.session_page("alice",id,version,"",20).at("hits").at(0).at("snippet").str().empty());
}

}
int main(int argc,char** argv) {
  std::size_t failed=0,executed=0;
  for(const auto& [name,test]:tests()) {
    if(argc>1&&name!=argv[1])continue;
    ++executed;
    try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  }
  if(executed==0)return 1;
  std::cout<<executed<<" source publication tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0;
}
