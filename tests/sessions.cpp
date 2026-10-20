#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> v;return v; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add reg_##name(#name,name);void name()
#define CHECK(x) do { ++assertions;if(!(x))throw std::runtime_error(std::string("check failed: ")+#x+" at "+std::to_string(__LINE__)); }while(false)
template<class F> void rejects(F fn,const std::string& code) { ++assertions;try { fn(); }catch(const Error& e) { if(e.code==code)return;throw std::runtime_error("expected "+code+", got "+e.code); }throw std::runtime_error("expected "+code); }
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-sessions-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code e;std::filesystem::remove_all(path,e); }
};
Json manifest(std::int64_t count=2) {
  auto manifest=Json::object(),sources=Json::array();manifest.set("version",std::int64_t{1});
  for(std::int64_t i=0;i<count;++i) {
    const auto id="source"+std::to_string(i);auto source=Json::parse(R"({"provider":"synthetic","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":10000,"params":{"adapter":"offline_fixture","base_url":"https://search.example/search"}})");
    source.set("id",id);source.set("index_family",id);
    auto contract=Json::parse(R"({"provider_id":"synthetic","capability":"web","endpoint":"https://search.example/search","adapter_id":"offline_fixture","enabled":true,"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://search.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":10000,"quota_window_seconds":86400}})");
    contract.set("id",id);contract.set("index_family",id);contract.set("logical_service_id",id);contract.at("limits").set("shared_budget_key",id);source.set("contract",contract);sources.push(source);
  }
  manifest.set("sources",sources);return manifest;
}
Json input(const std::string& key="request") { auto in=Json::object();in.set("query","marker");in.set("idempotency_key",key);return in; }
struct Worker:WorkerPort {
  std::vector<std::string> requests;std::function<void()> during;
  Json call(const std::string& method,const Json& args,int)override {
    CHECK(method=="search.multi");CHECK(args.at("sources").size()==1);const auto source=args.at("sources").at(0).str();requests.push_back(source);if(during)during();
    auto results=Json::object(),body=Json::object(),entry=Json::object(),hits=Json::array();
    for(std::int64_t i=0;i<3;++i) { auto hit=Json::object();hit.set("url","https://results.example/"+std::to_string(i));hit.set("title","marker title "+std::to_string(i));hit.set("snippet","marker secret "+std::to_string(i));hit.set("rank",source=="source0"?i+1:3-i);hits.push(hit); }
    entry.set("status","ok");entry.set("hits",hits);results.set(source,entry);body.set("results",results);return body;
  }
};
void execute_one(WebService& web) { const auto lease=web.lease_search();CHECK(lease.has_value());web.execute_search(*lease); }
Json policy(bool index,bool snippet) { auto p=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");p.set("index_allowed",index);p.set("snippet_allowed",snippet);return p; }
Json site(const std::string& mode) {
  auto m=Json::parse(R"({"version":1,"sites":[{"id":"fixture-site","enabled":true,"origin":"https://results.example","path_prefixes":["/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://results.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");m.at("sites").at(0).at("review").set("redistribution_policy",mode);return m;
}
TEST(progress_creates_immutable_versions_and_bound_stable_cursors) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));
  const auto started=web.submit_search("alice",input());const auto id=started.at("session_id").str();CHECK(worker.requests.empty());CHECK(started.at("state").str()=="queued");
  execute_one(web);auto first=store.session_get("alice",id);const auto v1=first.at("result_set_version").str();CHECK(first.at("state").str()=="queued");
  auto p1=store.session_page("alice",id,v1,"",1);CHECK(p1.at("hits").at(0).at("url").str()=="https://results.example/0");const auto cursor=p1.at("next_cursor").str();
  execute_one(web);const auto last=store.session_get("alice",id);const auto v2=last.at("result_set_version").str();CHECK(v1!=v2);CHECK(last.at("state").str()=="completed");
  CHECK(store.session_page("alice",id,v1,"",1).canonical()==p1.canonical());
  const auto next=store.session_page("alice",id,v1,cursor,1);CHECK(next.at("hits").at(0).at("url").str()=="https://results.example/1");CHECK(store.session_page("alice",id,v1,cursor,1).canonical()==next.canonical());
  rejects([&]{store.session_page("alice",id,v2,cursor,1);},"E_CURSOR");rejects([&]{store.session_page("alice",id,"","",1);},"E_RESULT_VERSION");
  CHECK(store.session_page("alice",id,v2,"",100).at("hits").size()==3);CHECK(worker.requests.size()==2);CHECK(web.submit_search("alice",input()).at("reused").flag());CHECK(!web.lease_search());
}
TEST(events_reconnect_without_duplicates_or_content_replay_and_survive_restart) {
  Temp t;Worker worker;std::string id,version;Json events;
  { Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest()));id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);execute_one(web);events=store.session_events("alice",id,0,2);version=store.session_get("alice",id).at("result_set_version").str();CHECK(events.at("events").size()==2);CHECK(events.at("has_more").flag());CHECK(events.dump().find("marker")==std::string::npos); }
  Store reopened(t.path/"db");auto tail=reopened.session_events("alice",id,events.at("next_event_id").num(),2);CHECK(tail.at("events").size()==1);CHECK(tail.at("events").at(0).at("event_id").num()==3);CHECK(!tail.at("has_more").flag());
  CHECK(reopened.session_events("alice",id,3,2).at("events").size()==0);CHECK(reopened.session_page("alice",id,version,"",100).at("hits").size()==3);CHECK(worker.requests.size()==2);
}
TEST(cancel_mid_request_fences_late_response_and_preserves_completed_version) {
  Temp t;Store store(t.path/"db"),other(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);
  const auto version=store.session_get("alice",id).at("result_set_version").str();worker.during=[&] { other.session_cancel("alice",id); };execute_one(web);
  auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="cancelled");CHECK(state.at("result_set_version").str()==version);CHECK(state.at("unknown_source_attempts").num()==1);CHECK(state.at("dispatched_source_attempts").num()==2);CHECK(store.session_page("alice",id,version,"",100).at("hits").size()==3);CHECK(!web.lease_search());
  const auto last=state.at("last_event_id").num();CHECK(other.session_cancel("alice",id).at("last_event_id").num()==last);CHECK(worker.requests.size()==2);
}
TEST(cancel_pending_is_zero_request_and_idempotent) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();store.session_cancel("alice",id);CHECK(!web.lease_search());CHECK(worker.requests.empty());CHECK(web.submit_search("alice",input()).at("state").str()=="cancelled");
}
TEST(lost_lease_is_not_resent_and_unknown_accounting_survives_restart) {
  Temp t;Worker worker;std::int64_t now=1800000000000;std::string id;
  { Store store(t.path/"db",[&]{return now;});WebService web(store,worker,SourceRegistry(manifest()));id=web.submit_search("alice",input()).at("session_id").str();const auto lost=web.lease_search();CHECK(lost&&lost->source_id=="source0"); }
  now+=20000;Store store(t.path/"db",[&]{return now;});WebService web(store,worker,SourceRegistry(manifest()));const auto next=web.lease_search();CHECK(next&&next->source_id=="source1");web.execute_search(*next);
  auto state=store.session_get("alice",id);CHECK(state.at("unknown_source_attempts").num()==1);CHECK(state.at("dispatched_source_attempts").num()==2);CHECK(worker.requests==std::vector<std::string>{"source1"});CHECK(store.session_page("alice",id,state.at("result_set_version").str(),"",20).at("partial").flag());
}
TEST(owner_isolation_invalid_options_and_version_binding) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();
  for(const auto op:{"get","events","cancel","page"})rejects([&] { if(std::string(op)=="get")store.session_get("bob",id);else if(std::string(op)=="events")store.session_events("bob",id);else if(std::string(op)=="cancel")store.session_cancel("bob",id);else store.session_page("bob",id,"bad","",1); },"E_NOT_FOUND");
  auto bad=input();bad.set("query","different");rejects([&]{web.submit_search("alice",bad);},"E_IDEMPOTENCY_CONFLICT");bad=input("zero");bad.set("limit",std::int64_t{0});rejects([&]{web.submit_search("alice",bad);},"E_SCHEMA");
  rejects([&]{store.session_events("alice",id,2);},"E_EVENT_CURSOR");rejects([&]{store.session_events("alice",id,0,33);},"E_SCHEMA");CHECK(worker.requests.empty());
}
TEST(expired_events_have_explicit_recovery_and_expired_keys_never_reexecute) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto state=store.session_get("alice",id);now+=600001;
  rejects([&]{store.session_events("alice",id,0);},"E_EVENT_HISTORY_EXPIRED");CHECK(store.session_events("alice",id,state.at("last_event_id").num()).at("events").size()==0);CHECK(store.session_page("alice",id,state.at("result_set_version").str(),"",20).at("hits").size()==3);
  now+=3600000;rejects([&]{store.session_get("alice",id);},"E_SESSION_EXPIRED");rejects([&]{web.submit_search("alice",input());},"E_SESSION_EXPIRED");CHECK(worker.requests.size()==1);CHECK(!web.lease_search());
}
TEST(delete_and_page_restriction_rechecked_without_content_in_event_log) {
  Temp t;Store store(t.path/"db"),other(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));const auto document=store.ingest("alice",{"https://results.example/0","marker","text/plain","marker"});const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==3);
  other.erase("alice",document.at("document_id").str());const auto url=std::string("https://results.example/1");auto fence=other.publication_fence("alice",url);other.observe_web_publication("alice",url,url,policy(false,false),fence);
  auto page=store.session_page("alice",id,version,"",20);CHECK(page.at("hits").size()==1);CHECK(page.at("hits").at(0).at("url").str()=="https://results.example/2");CHECK(store.session_events("alice",id).dump().find("marker")==std::string::npos);
}
TEST(site_tightening_survives_reopen_and_later_relaxation) {
  Temp t;Worker worker;std::string id,version;
  { Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest(1)),SiteRegistry(site("allowed_with_limits")));id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);version=store.session_get("alice",id).at("result_set_version").str();CHECK(!store.session_page("alice",id,version,"",20).at("hits").at(0).at("snippet").str().empty()); }
  { Store store(t.path/"db");store.activate_site_publication(SiteRegistry(site("links_only")));auto page=store.session_page("alice",id,version,"",20);CHECK(page.at("hits").size()==3);CHECK(page.at("hits").at(0).at("snippet").str().empty());store.activate_site_publication(SiteRegistry(site("allowed_with_limits"))); }
  Store store(t.path/"db");CHECK(store.session_page("alice",id,version,"",20).at("hits").at(0).at("snippet").str().empty());CHECK(worker.requests.size()==1);
}
TEST(active_session_backpressure_and_expired_detail_reclamation) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  for(int i=0;i<64;++i)web.submit_search("alice",input("q"+std::to_string(i)));
  rejects([&]{web.submit_search("alice",input("overflow"));},"E_SESSION_BACKPRESSURE");CHECK(worker.requests.empty());now+=3600001;
  const auto next=web.submit_search("alice",input("after-expiry"));CHECK(next.at("state").str()=="queued");rejects([&]{web.submit_search("alice",input("q0"));},"E_SESSION_EXPIRED");
}
TEST(local_search_gets_durable_events_and_still_has_snapshot_paging) {
  Temp t;Store store(t.path/"db");auto doc=store.ingest("alice",{"https://local.example/x","marker","text/plain","marker"});const auto id=store.search("alice",parse_query(Json::string("marker"))).at("session_id").str();
  CHECK(store.session_get("alice",id).at("kind").str()=="local");auto events=store.session_events("alice",id);CHECK(events.at("events").size()==1);CHECK(events.at("events").at(0).at("result_set_version").str()==id);
  auto page=store.session_page("alice",id,id,"",20);CHECK(page.at("hits").at(0).at("snapshot_id").str()==doc.at("snapshot_id").str());CHECK(store.session_cancel("alice",id).at("state").str()=="completed");
}
TEST(expired_local_sessions_release_capacity_for_web_without_local_search) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  for(int i=0;i<64;++i)store.search("alice",parse_query(Json::string("marker")));
  rejects([&]{web.submit_search("alice",input());},"E_SESSION_BACKPRESSURE");now+=3600001;
  CHECK(web.submit_search("alice",input()).at("state").str()=="queued");CHECK(worker.requests.empty());
  sqlite3_stmt* row=nullptr;CHECK(sqlite3_prepare_v2(store.raw_handle(),"SELECT COUNT(*) FROM sessions",-1,&row,nullptr)==SQLITE_OK);CHECK(sqlite3_step(row)==SQLITE_ROW);CHECK(sqlite3_column_int64(row,0)==0);sqlite3_finalize(row);
}
TEST(fused_snippet_keeps_all_actual_url_contributors_before_and_after_source_fusion) {
  struct AliasWorker:WorkerPort {
    bool same_source=false;
    Json call(const std::string&,const Json& args,int)override {
      const auto source=args.at("sources").at(0).str();auto out=Json::object(),results=Json::object(),entry=Json::object(),hits=Json::array();
      for(int i=0;i<(same_source?2:1);++i) {
        const bool first=same_source?i==0:source=="source0";auto hit=Json::object();hit.set("url",first?"http://results.example/alias":"https://results.example/alias");hit.set("title",first?"protected title":"");hit.set("snippet",first?"protected canary":"");hit.set("rank",static_cast<std::int64_t>(i+1));hits.push(hit);
      }
      entry.set("status","ok");entry.set("hits",hits);results.set(source,entry);out.set("results",results);return out;
    }
  };
  for(const bool same_source:{false,true})for(const bool index:{false,true}) {
    Temp t;Store store(t.path/"db"),other(t.path/"db");AliasWorker worker;worker.same_source=same_source;WebService web(store,worker,SourceRegistry(manifest(same_source?1:2)));
    const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);if(!same_source)execute_one(web);
    const auto version=store.session_get("alice",id).at("result_set_version").str();auto hits=store.session_page("alice",id,version,"",20).at("hits");CHECK(hits.size()==1);CHECK(hits.at(0).at("url").str()=="https://results.example/alias");CHECK(hits.at(0).at("snippet").str()=="protected canary");CHECK(!hits.at(0).has("publication_urls"));
    const auto original=std::string("http://results.example/alias");other.observe_web_publication("alice",original,original,policy(index,false),other.publication_fence("alice",original));
    CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==0);
    other.observe_web_publication("alice",original,original,policy(true,true),other.publication_fence("alice",original));CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==0);
  }
}
TEST(source_policy_drift_stops_pending_work_and_does_not_refund_attempts) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);
  auto changed=manifest();changed.at("sources").at(0).set("weight",std::int64_t{101});WebService restarted(store,worker,SourceRegistry(changed));CHECK(!restarted.lease_search());
  auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="failed");CHECK(state.at("error_code").str()=="E_SOURCE_POLICY_CHANGED");CHECK(state.at("dispatched_source_attempts").num()==1);CHECK(worker.requests.size()==1);
}
TEST(source_duplicate_provenance_keeps_best_rank_through_second_fusion) {
  struct RankedWorker:WorkerPort {
    Json call(const std::string&,const Json&,int)override {
      return Json::parse(R"({"results":{"source0":{"status":"ok","hits":[{"url":"http://results.example/alias","title":"first","snippet":"first","rank":1},{"url":"https://results.example/other","title":"second","snippet":"second","rank":2},{"url":"https://results.example/alias","title":"","snippet":"","rank":30}]}}})");
    }
  } worker;
  Temp t;Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest(1)));auto query=Json::object();query.set("query","marker");query.set("refresh",true);
  const auto sync=web.search("alice",query).at("results");CHECK(sync.size()==2);CHECK(sync.at(0).at("url").str()=="https://results.example/alias");CHECK(!sync.at(0).has("publication_urls"));
  const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();const auto frozen=store.session_page("alice",id,version,"",20).at("hits");
  CHECK(frozen.size()==2);for(std::size_t i=0;i<sync.size();++i) { CHECK(sync.at(i).at("url").str()==frozen.at(i).at("url").str());CHECK(sync.at(i).at("score_micro").num()==frozen.at(i).at("score_micro").num()); }
}
TEST(permission_epoch_invalidates_replay_and_late_completion) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);auto lease=web.lease_search();CHECK(lease.has_value());
  // Synthetic authority change, not a user database edit or a deletion stand-in.
  CHECK(sqlite3_exec(store.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice'",nullptr,nullptr,nullptr)==SQLITE_OK);
  rejects([&]{store.session_events("alice",id);},"E_SESSION_INVALIDATED");CHECK(!store.search_lease_live(*lease));web.execute_search(*lease);CHECK(worker.requests.size()==1);rejects([&]{store.session_cancel("alice",id);},"E_SESSION_INVALIDATED");
}
TEST(continuous_new_sessions_cannot_overtake_an_existing_pending_source) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest()));
  const auto a=web.submit_search("alice",input("a")).at("session_id").str();const auto b=web.submit_search("alice",input("b")).at("session_id").str();
  auto lease=web.lease_search();CHECK(lease&&lease->id==a);web.execute_search(*lease);const auto c=web.submit_search("alice",input("c")).at("session_id").str();
  lease=web.lease_search();CHECK(lease&&lease->id==b);web.execute_search(*lease);web.submit_search("alice",input("d"));lease=web.lease_search();CHECK(lease&&lease->id==a&&lease->source_id=="source1");web.execute_search(*lease);
  lease=web.lease_search();CHECK(lease&&lease->id==c);CHECK(worker.requests.size()==3);
}
TEST(frozen_known_redirect_binding_survives_target_revival_and_route_change) {
  for(bool deletion:{false,true}) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
    const auto a=std::string("https://results.example/0"),b=std::string("https://target.example/b"),c=std::string("https://target.example/c");
    const auto target=store.ingest("alice",{b,"marker target","text/plain","marker"});
    const auto save=[&](const std::string& final) {
      ParsedDocument parsed;parsed.title="marker";parsed.text="marker body";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);parsed.publication.set("snippet_text",parsed.text);parsed.publication.set("snippet_title",parsed.title);
      auto meta=Json::object();meta.set("final_url",final);meta.set("redirects",Json::array());return store.ingest_web("alice",{a,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",a));
    };
    save(b);const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==3);
    if(deletion) { store.erase("alice",target.at("document_id").str());store.ingest("alice",{b,"new marker","text/plain","marker"}); }
    else store.observe_web_publication("alice",b,b,policy(false,false),store.publication_fence("alice",b));
    save(c);CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==2);
    const auto fresh=web.submit_search("alice",input("fresh")).at("session_id").str();execute_one(web);CHECK(store.session_page("alice",fresh,store.session_get("alice",fresh).at("result_set_version").str(),"",20).at("hits").size()==3);
  }
}
TEST(incomplete_contributor_binding_keeps_captured_ceiling_with_legacy_crawl_marker) {
  struct WideWorker:WorkerPort {
    static std::string url(int i) { return "http://results.example/alias?utm_id="+std::to_string(i); }
    Json call(const std::string&,const Json&,int)override {
      auto out=Json::object(),results=Json::object(),entry=Json::object(),hits=Json::array();
      for(int i=0;i<24;++i) { auto hit=Json::object();hit.set("url",url(i));hit.set("title","protected legacy");hit.set("snippet","protected legacy canary");hit.set("rank",static_cast<std::int64_t>(i+1));hits.push(hit); }
      entry.set("status","ok");entry.set("hits",hits);results.set("source0",entry);out.set("results",results);return out;
    }
  } worker;
  Temp t;Store store(t.path/"db");WebService web(store,worker,SourceRegistry(manifest(1)));const auto a=WideWorker::url(0);
  ParsedDocument parsed;parsed.title="marker";parsed.text="marker body";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);auto meta=Json::object();meta.set("final_url","https://target.example/extra");meta.set("redirects",Json::array());
  store.ingest_web("alice",{a,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",a));
  // Isolated migration-shape fixture: schema6 unknown crawl provenance.
  CHECK(sqlite3_exec(store.raw_handle(),R"(UPDATE snapshots SET site_bindings_json='{"version":"bzs-site-bindings-1","legacy_crawl_unverified":true,"urls":[]}';UPDATE site_publication_state SET legacy_crawl_unverified=1;)",nullptr,nullptr,nullptr)==SQLITE_OK);
  auto query=input();query.set("per_source",std::int64_t{30});const auto id=web.submit_search("alice",query).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==0);
  const auto restricted=WideWorker::url(23);store.observe_web_publication("alice",restricted,restricted,policy(false,false),store.publication_fence("alice",restricted));CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==0);
}
TEST(preexisting_page_or_deletion_ceiling_is_immutable_after_fresh_complete_read) {
  for(int mode=0;mode<3;++mode) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));const auto url=std::string("https://results.example/0");
    if(mode==2) { const auto doc=store.ingest("alice",{url,"marker body","text/plain","marker"});store.erase("alice",doc.at("document_id").str()); }
    else store.observe_web_publication("alice",url,url,policy(mode==1,false),store.publication_fence("alice",url));
    const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();
    if(mode==2)store.ingest("alice",{url,"fresh marker body","text/plain","marker"});
    else {
      ParsedDocument parsed;parsed.title="fresh marker";parsed.text="fresh marker body";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);parsed.publication.set("snippet_title",parsed.title);parsed.publication.set("snippet_text",parsed.text);auto meta=Json::object();meta.set("final_url",url);meta.set("redirects",Json::array());store.ingest_web("alice",{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",url));
    }
    Store reopened(t.path/"db");const auto previous=reopened.session_page("alice",id,version,"",20).at("hits");
    if(mode==1) { CHECK(previous.size()==3);for(std::size_t i=0;i<previous.size();++i)if(previous.at(i).at("url").str()==url) { CHECK(previous.at(i).at("snippet").str().empty());CHECK(previous.at(i).at("title").str()==url); } }
    else CHECK(previous.size()==2);
    const auto fresh=web.submit_search("alice",input("fresh")).at("session_id").str();execute_one(web);const auto hits=store.session_page("alice",fresh,store.session_get("alice",fresh).at("result_set_version").str(),"",20).at("hits");CHECK(hits.size()==3);CHECK(!hits.at(0).at("snippet").str().empty());
  }
}
}
int main() { std::size_t failed=0;for(const auto& [name,test]:tests())try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& e) { ++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n'; }std::cout<<tests().size()<<" tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0; }
