#include "bzs/service.hpp"
#include "bzs/sites.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <functional>
#include <iostream>
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
    ++assertions;try { fn(); } catch(const Error& error) { if(error.code==code)return;throw std::runtime_error("expected "+code+", got "+error.code); }
    throw std::runtime_error("expected "+code);
  }
  struct Temp {
    std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-map-"+random_id());
    Temp() { std::filesystem::create_directories(path); }
    ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
  };
  constexpr const char* base="https://map.example";
  Json manifest() { return Json::parse(R"({"version":1,"sites":[{"id":"map-site","enabled":true,"origin":"https://map.example","path_prefixes":["/map/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://map.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})"); }
  Json args(const std::vector<std::string>& input={std::string(base)+"/map/index"},const std::string& key="map-key") {
    auto p=Json::object(),seeds=Json::array();for(const auto& url:input)seeds.push(Json::string(url));
    p.set("site_id","map-site");p.set("seeds",seeds);p.set("idempotency_key",key);return p;
  }
  Json candidate(const Json& p,const std::string& url,const std::string& source="html",bool document=false) {
    auto c=Json::object();c.set("url",url);c.set("parent",p.at("url"));c.set("source",source);c.set("document",document?Json::string("sitemap"):Json());
    c.set("depth",p.at("depth").num()+1);c.set("path_depth",std::int64_t{2});return c;
  }
  struct Fake : WorkerPort {
    std::int64_t now=1700000000000;
    std::vector<Json> calls;
    std::vector<std::string> cancelled;
    std::function<Json(const Json&)> response;
    Json ok(const Json& p)const {
      auto r=Json::object();r.set("status","ok");r.set("url",p.at("url"));r.set("final_url",p.at("url"));r.set("http_status",std::int64_t{200});
      r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
      if(p.at("kind").str()=="robots") {
        auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","body_sha256":"","state":"rules","rules":[],"sitemaps":[],"diagnostics":[],"origin":"https://map.example","fetched_at_ms":0,"expires_at_ms":0})");
        record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);r.set("robots",record);
      } else {
        auto discovery=Json::object();discovery.set("document_url",p.at("url"));discovery.set("document_type","html");discovery.set("parse_status","ok");discovery.set("candidates",Json::array());discovery.set("truncated",false);discovery.set("diagnostics",Json::object());r.set("discovery",discovery);
        auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",base);decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","robots_no_matching_rule");r.set("robots_decision",decision);
      }
      return r;
    }
    Json call(const std::string& method,const Json& p,int timeout) override {
      CHECK(timeout>0);if(method=="fetch.cancel") { cancelled.push_back(p.at("attempt_id").str());return Json::object(); }
      CHECK(method=="map.fetch");CHECK(p.at("max_requests").num()==1);CHECK(p.at("attempt_id").str().size()==48);CHECK(p.at("deadline_ms").num()>now);
      calls.push_back(p);return response?response(p):ok(p);
    }
  };
  void drain(WebService& web) { std::size_t turns=0;while(auto lease=web.lease_map()) { CHECK(++turns<=100);web.execute_map(*lease); } }
  void sql(sqlite3* db,const char* query) { CHECK(sqlite3_exec(db,query,nullptr,nullptr,nullptr)==SQLITE_OK); }
  std::int64_t scalar(sqlite3* db,const char* query) {
    sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query,-1,&raw,nullptr)==SQLITE_OK);
    std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> stmt(raw,sqlite3_finalize);CHECK(sqlite3_step(stmt.get())==SQLITE_ROW);return sqlite3_column_int64(stmt.get(),0);
  }
  Json request(const std::string& op,const Json& p) { auto r=Json::object();r.set("operation",op);r.set("params",p);return r; }
  void drop_map(sqlite3* db) { sql(db,"DROP TABLE map_hops; DROP TABLE map_candidates; DROP TABLE map_resources; DROP TABLE map_jobs; DROP TABLE robots_cache; DROP TABLE site_request_state; PRAGMA user_version=4;"); }

  TEST(empty_sites_default_and_strict_independent_review) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService web(s,f,{});
    CHECK(web.sites().at("sites").size()==0);CHECK(web.sites().at("manifest_sha256").str()==sha256("{\"version\":1,\"sites\":[]}"));
    rejects([&]{web.submit_map("alice",args());},"E_SITE_REVIEW");CHECK(f.calls.empty());
    auto m=manifest();m.at("sites").at(0).set("enabled",false);WebService disabled(s,f,{},SiteRegistry(m));rejects([&]{disabled.submit_map("alice",args());},"E_SITE_REVIEW");
    m=manifest();m.at("sites").at(0).at("review").set("cache_policy","disallow");WebService denied(s,f,{},SiteRegistry(m));rejects([&]{denied.submit_map("alice",args());},"E_SITE_REVIEW");
    m=manifest();m.at("sites").at(0).at("review").set("expires_at","2000-01-01T00:00:00Z");WebService expired(s,f,{},SiteRegistry(m));rejects([&]{expired.submit_map("alice",args());},"E_SITE_REVIEW");
  }
  TEST(registry_origin_limits_and_exact_uri_identity) {
    auto m=manifest();auto duplicate=Json::parse(m.at("sites").at(0).dump());duplicate.set("id","same-origin");duplicate.set("origin","https://MAP.example:443");m.at("sites").push(duplicate);rejects([&]{SiteRegistry bad(m);},"E_CONFIG");
    m=manifest();m.at("sites").at(0).at("limits").set("max_concurrency",std::int64_t{2});rejects([&]{SiteRegistry bad(m);},"E_CONFIG");
    CHECK(map_url("https://MAP.example:443/map/%61?x=%62&x=%2b#fragment")=="https://map.example/map/%61?x=%62&x=%2b");
    CHECK(map_url("https://map.example/map/index?")=="https://map.example/map/index");
    auto sites=SiteRegistry(manifest());CHECK(sites.find("map-site")->allows("https://map.example/map"));CHECK(!sites.find("map-site")->allows("https://map.example/map2/a"));
    for(const auto& tail:{"/map/../outside","/map/%2e%2e/outside","/map%2f../outside","/map/%252e%252e/outside","/map/a\\b"})
      rejects([&]{map_url(std::string(base)+tail);},"E_SCOPE");
    CHECK(sites.describe(1700000000000).at("sites").at(0).at("limits").at("request_quota").num()==1000);
  }
  TEST(input_validation_idempotency_and_owner_only_jobs) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto p=args();auto id=w.submit_map("alice",p).at("job_id").str();CHECK(w.submit_map("alice",p).at("reused").flag());
    CHECK(w.submit_map("bob",p).at("job_id").str()!=id);rejects([&]{s.job_get("bob",id);},"E_NOT_FOUND");rejects([&]{s.job_results("bob",id);},"E_NOT_FOUND");rejects([&]{s.job_control("bob",id,"cancel");},"E_NOT_FOUND");
    p.set("max_urls",std::int64_t{500});rejects([&]{w.submit_map("alice",p);},"E_IDEMPOTENCY_CONFLICT");
    for(const char* key:{"max_urls","max_requests","max_bytes","timeout_ms","per_request_timeout_ms"}) { auto bad=args();bad.set(key,std::int64_t{0});rejects([&]{w.submit_map("alice",bad);},"E_SCHEMA"); }
    rejects([&]{w.submit_map("alice",args({}));},"E_SCHEMA");
    p=args();p.set("scope_path","/");rejects([&]{w.submit_map("alice",p);},"E_SCOPE");
    p=args({std::string(base)+"/map/narrow/index"},"narrow");p.set("scope_path","/map/narrow/");CHECK(w.submit_map("alice",p).at("scope_path").str()=="/map/narrow");
    CHECK(f.calls.empty());
  }
  TEST(robots_precedes_discovery_and_html_is_candidates_only) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()!="robots") { auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/child"));r.at("discovery").set("candidates",c); }return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();CHECK(!s.lease_next());CHECK(!s.lease_next_read());drain(w);
    CHECK(f.calls.size()==2);CHECK(f.calls[0].at("kind").str()=="robots");CHECK(f.calls[1].at("url").str()==std::string(base)+"/map/index");
    CHECK(s.stats("alice").at("snapshots").num()==0);CHECK(s.job_get("alice",id).at("state").str()=="succeeded");
    auto result=s.job_results("alice",id);CHECK(result.at("items").size()==1);CHECK(!result.at("items").at(0).at("body_read").flag());CHECK(result.at("next_offset").is_null());
    CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM map_hops WHERE response_json LIKE '%robots_decision%'")==1);
  }
  TEST(optional_bad_candidates_and_sitemaps_do_not_poison_valid_results) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")r.at("robots").set("sitemaps",Json::parse(R"(["http://127.0.0.1/private","not a URL","https://other.example/map/x"] )"));
      else { auto c=Json::array();for(const auto& url:{std::string(base)+"/map/safe",std::string(base)+"/map/%2e%2e/outside",std::string("http://169.254.169.254/latest"),std::string(base)+"/outside"})c.push(candidate(p,url));r.at("discovery").set("candidates",c); }return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(s.job_get("alice",id).at("state").str()=="succeeded");CHECK(s.job_results("alice",id).at("items").size()==1);CHECK(s.job_get("alice",id).at("filtered_urls").num()==6);CHECK(f.calls.size()==2);
  }
  TEST(sitemap_index_children_only_are_queued_once) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
      auto c=Json::array();auto url=p.at("url").str();
      if(url.ends_with("index")) { r.at("discovery").set("document_type","sitemap_index");c.push(candidate(p,std::string(base)+"/map/child.xml","sitemap_index",true)); }
      else { r.at("discovery").set("document_type","sitemap_urlset");c.push(candidate(p,std::string(base)+"/map/page","sitemap")); }
      r.at("discovery").set("candidates",c);return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==3);CHECK(f.calls[2].at("kind").str()=="sitemap");CHECK(s.job_results("alice",id).at("items").size()==2);CHECK(s.stats("alice").at("snapshots").num()==0);
  }
  TEST(url_depth_and_fixed_query_variant_budgets_report_partial) {
    for(const std::string mode:{"urls","depth","variants"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;auto c=Json::array();for(int i=0;i<6;++i)c.push(candidate(p,std::string(base)+"/map/result?x="+std::to_string(i)));r.at("discovery").set("candidates",c);return r; };
      auto p=args();if(mode=="urls")p.set("max_urls",std::int64_t{2});if(mode=="depth")p.set("max_depth",std::int64_t{0});auto id=w.submit_map("alice",p).at("job_id").str();drain(w);
      CHECK(s.job_get("alice",id).at("partial").flag());CHECK(s.job_get("alice",id).at("state").str()=="succeeded");auto n=s.job_results("alice",id).at("items").size();CHECK(n==(mode=="urls"?1U:mode=="depth"?0U:4U));CHECK(f.calls.size()==2);
    }
  }
  TEST(history_is_owner_scoped_and_deletion_never_revives_candidates) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto old=s.ingest("alice",{std::string(base)+"/map/own","owner history","text/plain","own"});s.ingest("bob",{std::string(base)+"/map/other","other history","text/plain","other"});
    auto id=w.submit_map("alice",args()).at("job_id").str();auto items=s.job_results("alice",id).at("items");CHECK(items.size()==1);CHECK(items.at(0).at("source").str()=="history");
    s.erase("alice",old.at("document_id").str());s.ingest("alice",{std::string(base)+"/map/own","new explicit revision","text/plain","new"});CHECK(s.job_results("alice",id).at("items").size()==0);drain(w);CHECK(s.job_results("alice",id).at("items").size()==0);
  }
  TEST(history_alias_tombstone_remains_bound_to_original_document) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto snapshot=s.ingest("alice",{"https://MAP.example:443/map/history#old","old","text/plain","old"});
    auto id=w.submit_map("alice",args()).at("job_id").str();auto items=s.job_results("alice",id).at("items");
    CHECK(items.size()==1);CHECK(items.at(0).at("url").str()=="https://map.example/map/history");
    s.erase("alice",snapshot.at("document_id").str());s.ingest("alice",{"https://MAP.example:443/map/history#old","restored","text/plain","new"});
    CHECK(s.job_results("alice",id).at("items").size()==0);CHECK(f.calls.empty());
  }
  TEST(each_redirect_hop_gets_new_persistent_grant_and_same_origin_scope) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",std::string(base)+"/map/final"); }return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==3);CHECK(f.calls[2].at("redirect_hop").num()==1);CHECK(f.calls[1].at("attempt_id").str()!=f.calls[2].at("attempt_id").str());CHECK(s.job_get("alice",id).at("usage").at("external_requests").num()==3);
    f.response=[&](const Json& p) { auto r=f.ok(p);r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url","https://other.example/map/out");return r; };
    id=w.submit_map("alice",args({std::string(base)+"/map/cross"},"cross")).at("job_id").str();drain(w);CHECK(f.calls.size()==4);CHECK(s.job_get("alice",id).at("state").str()=="failed");
  }
  TEST(late_results_after_logical_or_redirect_target_deletion_are_fenced) {
    for(bool redirect:{false,true}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      const auto victim=std::string(base)+(redirect?"/map/target":"/map/index");auto snap=s.ingest("alice",{victim,"old","text/plain","old"});
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
        if(redirect&&p.at("url").str().ends_with("index")) { r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",victim); }
        else { s.erase("alice",snap.at("document_id").str());s.ingest("alice",{victim,"explicit restore","text/plain","restore"});auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/late"));r.at("discovery").set("candidates",c); }return r; };
      auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(s.job_get("alice",id).at("state").str()=="failed");CHECK(s.job_results("alice",id).at("items").size()==0);CHECK(s.stats("alice").at("snapshots").num()==1);
    }
  }
  TEST(pause_cancel_fences_dispatch_and_late_publication) {
    for(const std::string action:{"pause","cancel"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service api(s,fixtures,t.path/"backups",&w);
      auto id=w.submit_map("alice",args({std::string(base)+"/map/index",std::string(base)+"/map/second"})).at("job_id").str();
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()!="robots") { auto ctrl=Json::object();ctrl.set("job_id",id);ctrl.set("action",action);api.dispatch({"alice",false},request("job.control",ctrl));auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/late"));r.at("discovery").set("candidates",c); }return r; };
      drain(w);CHECK(s.job_get("alice",id).at("state").str()==(action=="pause"?"paused":"cancelled"));CHECK(f.calls.size()==2);CHECK(f.cancelled.size()==1);CHECK(s.job_results("alice",id).at("items").size()==0);
      if(action=="pause") { f.response={};s.job_control("alice",id,"resume");drain(w);CHECK(f.calls.size()==3);CHECK(f.calls.back().at("url").str().ends_with("second")); }
    }
  }
  TEST(completed_resources_and_robots_cache_survive_restart_without_refetch) {
    Temp t;Fake f;std::string id;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_map("alice",args({std::string(base)+"/map/index",std::string(base)+"/map/second"})).at("job_id").str();
      auto robots=w.lease_map();CHECK(robots);w.execute_map(*robots);auto first=w.lease_map();CHECK(first);w.execute_map(*first);s.job_control("alice",id,"pause"); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));CHECK(!w.lease_map());s.job_control("alice",id,"resume");drain(w);CHECK(f.calls.size()==3);CHECK(s.job_get("alice",id).at("state").str()=="succeeded"); }
  }
  TEST(unknown_dispatched_step_remains_charged_and_is_not_retried) {
    Temp t;Fake f;std::string id;std::int64_t grant=0;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_map("alice",args()).at("job_id").str();auto lease=w.lease_map();CHECK(lease);grant=lease->byte_grant; }
    f.now+=30000;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));drain(w);CHECK(f.calls.empty());auto j=s.job_get("alice",id);CHECK(j.at("state").str()=="failed");CHECK(j.at("usage").at("unknown_requests_charged").num()==1);CHECK(j.at("budget").at("charged_bytes").num()==grant);rejects([&]{s.job_control("alice",id,"retry_failed");},"E_JOB_STATE"); }
  }
  TEST(robots_and_content_share_request_and_byte_totals) {
    for(bool request_budget:{false,true}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto p=args();
      if(request_budget)p.set("max_requests",std::int64_t{1});else p.set("max_bytes",std::int64_t{100});auto id=w.submit_map("alice",p).at("job_id").str();drain(w);
      CHECK(f.calls.size()==1);auto j=s.job_get("alice",id);CHECK(j.at("state").str()=="failed");CHECK(j.at("usage").at("external_requests").num()==1);CHECK(j.at("budget").at("charged_bytes").num()==100);
    }
  }
  TEST(site_quota_is_shared_across_owners_and_persisted) {
    Temp t;Fake f;auto m=manifest();m.at("sites").at(0).at("limits").set("request_quota",std::int64_t{2});
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(m));auto first=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(s.job_get("alice",first).at("state").str()=="succeeded"); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(m));auto second=w.submit_map("bob",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==2);CHECK(s.job_get("bob",second).at("stop_reason").str()=="E_SITE_QUOTA"); }
  }
  TEST(site_pacing_cooldown_and_review_expiry_survive_warm_cache) {
    Temp t;Fake f;auto m=manifest();m.at("sites").at(0).at("limits").set("min_interval_ms",std::int64_t{5000});Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(m));
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==1);CHECK(s.job_get("alice",id).at("state").str()=="queued");f.now+=5000;drain(w);CHECK(f.calls.size()==2);
    auto next=w.submit_map("alice",args({std::string(base)+"/map/next"},"next")).at("job_id").str();auto changed=manifest();WebService replaced(s,f,{},SiteRegistry(changed));drain(replaced);CHECK(s.job_get("alice",next).at("state").str()=="failed");CHECK(f.calls.size()==2);
  }
  TEST(protocol_binding_and_unknown_usage_do_not_publish_candidates) {
    for(const std::string mode:{"decision","identity","usage"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()!="robots") {
          auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/leak"));r.at("discovery").set("candidates",c);
          if(mode=="decision")r.at("robots_decision").set("target_url",std::string(base)+"/map/another");
          if(mode=="identity")r.set("final_url",std::string(base)+"/map/another");
          if(mode=="usage")r.at("usage").set("external_requests",std::int64_t{2});
        }return r; };
      auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(s.job_get("alice",id).at("state").str()=="failed");CHECK(s.job_results("alice",id).at("items").size()==0);CHECK(s.job_get("alice",id).at("usage").at("unknown_requests_charged").num()==(mode=="usage"?1:0));
    }
  }
  TEST(candidate_commit_and_hop_receipt_are_one_transaction) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto id=w.submit_map("alice",args()).at("job_id").str();auto metadata=w.lease_map();CHECK(metadata);w.execute_map(*metadata);
    sql(s.raw_handle(),"CREATE TRIGGER fail_map_receipt BEFORE UPDATE OF response_json ON map_hops WHEN NEW.response_json LIKE '%document_type%' BEGIN SELECT RAISE(ABORT,'fixture failure'); END;");
    f.response=[&](const Json& p) { auto r=f.ok(p);auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/atomic"));r.at("discovery").set("candidates",c);return r; };
    drain(w);CHECK(s.job_results("alice",id).at("items").size()==0);CHECK(s.job_get("alice",id).at("state").str()=="failed");CHECK(s.job_get("alice",id).at("usage").at("external_requests").num()==2);
  }
  TEST(negative_robots_cache_stops_content_even_during_cooldown) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);CHECK(p.at("kind").str()=="robots");r.set("status","error");r.set("error_code","E_RATE_LIMITED");r.set("http_status",std::int64_t{429});r.set("retry_after_ms",std::int64_t{600000});r.at("robots").set("state","deny_all");return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==1);CHECK(s.job_get("alice",id).at("state").str()=="failed");CHECK(s.job_get("alice",id).at("stop_reason").str()=="E_RATE_LIMITED");
  }
  TEST(predispatch_error_preserves_null_status_and_zero_request_accounting) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=Json::object();r.set("status","error");r.set("url",p.at("url"));r.set("final_url",p.at("url"));r.set("http_status",Json());r.set("error_code","E_SITE_MANIFEST_MISMATCH");r.set("usage",Json::parse(R"({"external_requests":0,"content_bytes":0})"));return r; };
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);auto status=s.job_get("alice",id);CHECK(status.at("state").str()=="failed");CHECK(status.at("stop_reason").str()=="E_SITE_MANIFEST_MISMATCH");CHECK(status.at("budget").at("charged_requests").num()==0);CHECK(scalar(s.raw_handle(),"SELECT requests FROM site_request_state")==0);
  }
  TEST(global_robots_retention_limit_stops_before_network_dispatch) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    sql(s.raw_handle(),"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<512) INSERT INTO robots_cache(id,owner,origin,manifest_sha256,record_json,fetched_at,expires_at) SELECT 'old-'||x,'unrelated','https://other.example','old','{}',0,1 FROM n;");
    auto id=w.submit_map("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.empty());CHECK(s.job_get("alice",id).at("state").str()=="failed");CHECK(s.job_get("alice",id).at("stop_reason").str()=="E_QUOTA");
  }
  TEST(schema_four_migration_preserves_old_read_jobs_snapshots_evidence_and_index) {
    Temp t;Json snap,evidence,job;
    { Store s(t.path/"db");snap=s.ingest("alice",{std::string(base)+"/map/old","migration marker text","text/plain","old"});evidence=s.evidence("alice",snap.at("snapshot_id").str(),0,9);snap=s.snapshot("alice",snap.at("snapshot_id").str());
      ReadManyOptions options;job=s.read_many_submit("alice",{std::string(base)+"/map/read"},"legacy-read",options);sql(s.raw_handle(),"UPDATE content_fts SET rowid=913;");drop_map(s.raw_handle()); }
    { Store s(t.path/"db");CHECK(scalar(s.raw_handle(),"PRAGMA user_version")==8);CHECK(s.snapshot("alice",snap.at("snapshot_id").str()).canonical()==snap.canonical());CHECK(s.evidence_get("alice",evidence.at("evidence_id").str()).canonical()==evidence.canonical());auto current=s.job_get("alice",job.at("job_id").str());job.set("reused",Json());CHECK(current.at("kind").str()=="web.read_many");CHECK(current.at("state").str()=="queued");CHECK(scalar(s.raw_handle(),"SELECT rowid FROM content_fts")==913);CHECK(s.lease_next_read()); }
  }
  TEST(schema_four_quarantine_refuses_map_ddl) {
    Temp t;{ Store s(t.path/"db");drop_map(s.raw_handle());sql(s.raw_handle(),"UPDATE settings SET value='1' WHERE key='restore_quarantine';"); }
    rejects([&]{Store forbidden(t.path/"db");},"E_RESTORE_QUARANTINED");sqlite3* raw=nullptr;CHECK(sqlite3_open((t.path/"db").string().c_str(),&raw)==SQLITE_OK);std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);
    CHECK(scalar(raw,"PRAGMA user_version")==4);CHECK(scalar(raw,"SELECT COUNT(*) FROM sqlite_master WHERE name='map_jobs'")==0);
  }
}
int main() {
  std::size_t failures=0;for(const auto& [name,test]:tests()) { try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; } }
  std::cout<<tests().size()<<" map tests, "<<assertions<<" assertions, "<<failures<<" failures\n";return failures?1:0;
}
