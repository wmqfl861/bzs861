#include "bzs/error.hpp"
#include "bzs/http_cache.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <functional>
#include <iostream>
#include <map>
#include <vector>
using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests(){static std::vector<Test> value;return value;}
struct Add{Add(const char* name,std::function<void()> run){tests().emplace_back(name,std::move(run));}};
std::size_t assertions=0;
#define TEST(name) void name();Add register_##name(#name,name);void name()
#define CHECK(value) do{++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__));}while(false)
template<class F> void rejects(F run,const std::string& code){++assertions;try{run();}catch(const Error& e){if(e.code==code)return;throw std::runtime_error("expected "+code+", got "+e.code);}throw std::runtime_error("expected "+code);}
struct Temp{std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-revalidation-"+random_id());Temp(){std::filesystem::create_directories(path);}~Temp(){std::error_code e;std::filesystem::remove_all(path,e);}};
constexpr const char* origin="https://validate.example";
std::string url(const std::string& path="/docs/page"){return std::string(origin)+path;}
Json cache(const std::string& target){auto r=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"version-1\"","last_modified":"Tue, 14 Nov 2023 22:13:20 GMT","cache_control":"max-age=3600","vary":"","date":"","age":"","expires":""})");r.set("url",target);r.set("request_profile",sha256("offline-profile"));return r;}
Json manifest(){return Json::parse(R"({"version":1,"sites":[{"id":"validator-site","enabled":true,"origin":"https://validate.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://validate.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-validator-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");}
Json read_args(const std::string& target=url()){auto r=Json::object();r.set("url",target);r.set("freshness","refresh");return r;}
Json crawl_args(const std::string& key,const std::string& target=url(),std::int64_t depth=3){auto r=Json::object(),seeds=Json::array();seeds.push(Json::string(target));r.set("site_id","validator-site");r.set("idempotency_key",key);r.set("seeds",seeds);r.set("max_depth",depth);return r;}
Json restriction(const std::string& kind){auto r=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");if(kind=="noindex"){r.set("index_allowed",false);r.set("snippet_allowed",false);}if(kind=="nosnippet")r.set("snippet_allowed",false);if(kind=="nofollow")r.set("follow_allowed",false);auto d=Json::array();d.push(Json::string(kind));r.set("directives",d);return r;}
std::int64_t scalar(sqlite3* db,const char* sql){sqlite3_stmt* s=nullptr;CHECK(sqlite3_prepare_v2(db,sql,-1,&s,nullptr)==SQLITE_OK);CHECK(sqlite3_step(s)==SQLITE_ROW);const auto n=sqlite3_column_int64(s,0);sqlite3_finalize(s);return n;}
struct Worker:WorkerPort{
  std::int64_t now=1700000000000;std::vector<Json> calls;std::map<std::string,std::vector<std::string>> links;
  std::function<Json(const Json&,Json)> alter;
  Json page(const Json& p)const{
    auto r=Json::parse(R"({"status":"ok","html":"validationneedle original body","text":"validationneedle original body","title":"validator fixture","content_type":"text/html","extractor":"validator-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"validationneedle original body","snippet_title":"validator fixture","directives":[],"diagnostics":[]}})");
    auto values=Json::array();if(auto found=links.find(p.at("url").str());found!=links.end())for(const auto& item:found->second)values.push(Json::string(item));
    r.set("links",values);r.set("final_url",p.at("url"));r.set("http_cache",cache(p.at("url").str()));r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));return r;
  }
  Json response(const Json& p)const{
    const bool map=p.has("kind"),robots=map&&p.at("kind").str()=="robots",conditional=p.has("conditional");
    auto r=conditional?Json::object():page(p);
    if(conditional){r.set("status","not_modified");r.set("http_status",std::int64_t{304});r.set("final_url",p.at("url"));r.set("binding_id",p.at("conditional").at("binding_id"));r.set("http_cache",cache(p.at("url").str()));r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":0})"));}
    if(!map)return r;
    if(!conditional){r=Json::object();r.set("status","ok");r.set("http_status",std::int64_t{200});r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));}
    r.set("url",p.at("url"));r.set("final_url",p.at("url"));
    if(robots){
      auto record=conditional?Json::parse(p.at("robots_revalidation_base").at("record").dump()):Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
      record.set("origin",std::string(origin));record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);r.set("robots",record);r.set("http_cache",cache(p.at("url").str()));
    }else{
      auto decision=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","allowed":true,"reason":"robots_no_matching_rule"})");decision.set("origin",std::string(origin));decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));r.set("robots_decision",decision);
      if(!conditional){auto body=page(p),nested=Json::object();for(const auto& key:body.keys())if(key!="http_cache")nested.set(key,body.at(key));r.set("page",nested);r.set("http_cache",body.at("http_cache"));r.set("publication",body.at("publication"));auto d=Json::object(),candidates=Json::array();
        for(std::size_t i=0;i<body.at("links").size();++i){auto c=Json::object();c.set("url",body.at("links").at(i));c.set("parent",p.at("url"));c.set("source","html");c.set("document",Json());c.set("depth",p.at("depth").num()+1);c.set("path_depth",std::int64_t{2});candidates.push(c);}
        d.set("document_url",p.at("url"));d.set("document_type","html");d.set("parse_status","ok");d.set("candidates",candidates);d.set("truncated",false);d.set("diagnostics",Json::object());r.set("discovery",d);}
    }return r;
  }
  Json call(const std::string& method,const Json& p,int)override{if(method=="fetch.cancel")return Json::object();CHECK(method=="fetch.extract"||method=="map.fetch");calls.push_back(Json::parse(p.dump()));auto r=response(p);return alter?alter(p,r):r;}
};
void drain(WebService& web){int n=0;while(auto lease=web.lease_map()){CHECK(++n<=40);web.execute_map(*lease);}}
std::size_t hits(Store& store){auto session=store.search("alice",parse_query(Json::string("validationneedle")));return store.page("alice",session.at("session_id").str(),"",20).at("hits").size();}

TEST(direct_304_keeps_body_revision_and_evidence){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("alice",read_args());auto id=first.at("snapshot_id").str();auto proof=s.evidence("alice",id,0,16);w.now+=1000;auto second=web.read("alice",read_args());CHECK(w.calls.size()==2);CHECK(w.calls.back().has("conditional"));CHECK(second.at("snapshot_id").str()==id);CHECK(second.at("revision").num()==first.at("revision").num());CHECK(second.at("fetch").at("http_status").num()==304);CHECK(second.at("fetch").at("revalidated").flag());CHECK(second.at("fetched_at_ms").num()==w.now);CHECK(s.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()==proof.at("quote").str());}
TEST(batch_304_charges_one_request_and_no_body_bytes){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("alice",read_args());auto id=s.read_many_submit("alice",{url()},"batch-304",{}).at("job_id").str();auto lease=s.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);auto job=s.job_get("alice",id);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("usage").at("external_requests").num()==1);CHECK(job.at("usage").at("content_bytes").num()==0);CHECK(job.at("usage").at("reserved_bytes").num()==0);CHECK(s.job_results("alice",id).at("items").at(std::size_t{0}).at("receipt").at("snapshot_id").str()==first.at("snapshot_id").str());}
TEST(validators_survive_restart_and_are_owner_scoped){Temp t;Worker w;std::string sid;{Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});sid=web.read("alice",read_args()).at("snapshot_id").str();}{Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});CHECK(web.read("alice",read_args()).at("snapshot_id").str()==sid);CHECK(w.calls.back().has("conditional"));web.read("bob",read_args());CHECK(!w.calls.back().has("conditional"));CHECK(scalar(s.raw_handle(),"PRAGMA user_version")==12);}}
TEST(not_modified_does_not_overwrite_a_concurrent_full_replacement){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("alice",read_args());w.alter=[&](const Json&,Json r){s.ingest("alice",{url(),"new authorized import","text/plain","new"});return r;};rejects([&]{web.read("alice",read_args());},"E_REVALIDATION_STALE");CHECK(s.cached("alice",url())->at("canonical_text").str()=="new authorized import");}
TEST(deletion_and_reimport_fence_a_304){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("alice",read_args());w.alter=[&](const Json&,Json r){s.erase("alice",first.at("document_id").str());s.ingest("alice",{url(),"reimported body","text/plain","new"});return r;};rejects([&]{web.read("alice",read_args());},"E_TARGET_DELETED");CHECK(s.cached("alice",url())->at("canonical_text").str()=="reimported body");}
TEST(restrictions_observed_before_304_dispatch_still_seal_old_body){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("alice",read_args());CHECK(hits(s)==1);s.observe_web_publication("alice",url(),url(),restriction("noindex"),s.publication_fence("alice",url()));CHECK(hits(s)==0);auto refreshed=web.read("alice",read_args());CHECK(!refreshed.at("indexed").flag());CHECK(hits(s)==0);CHECK(s.snapshot("alice",first.at("snapshot_id").str()).at("canonical_text").str()=="validationneedle original body");w.alter=[&](const Json& p,Json){return w.page(p);};web.read("alice",read_args());CHECK(hits(s)==1);}
TEST(not_modified_header_can_tighten_but_never_clear_cached_meta_directives){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("alice",read_args());w.alter=[](const Json&,Json r){r.set("publication_observation",restriction("nosnippet"));return r;};auto limited=web.read("alice",read_args());CHECK(!s.snapshot("alice",limited.at("snapshot_id").str()).at("publication").at("snippet_allowed").flag());w.alter={};auto again=web.read("alice",read_args());CHECK(!s.snapshot("alice",again.at("snapshot_id").str()).at("publication").at("snippet_allowed").flag());}
TEST(unsolicited_or_unbound_304_is_rejected_without_touching_body){for(const auto& kind:{"binding","url","etag","profile","body","last_modified"}){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("alice",read_args());const auto before=s.cached("alice",url())->canonical();w.alter=[&](const Json&,Json r){if(std::string(kind)=="binding")r.set("binding_id",sha256("wrong"));else if(std::string(kind)=="url")r.set("final_url",url("/docs/other"));else if(std::string(kind)=="body")r.set("text","injected");else{auto h=r.at("http_cache");h.set(std::string(kind)=="profile"?"request_profile":kind,std::string(kind)=="etag"?"\"different\"":std::string(kind)=="profile"?sha256("other"):"Wed, 15 Nov 2023 22:13:20 GMT");}return r;};rejects([&]{web.read("alice",read_args());},"E_WORKER_PROTOCOL");CHECK(s.cached("alice",url())->canonical()==before);CHECK(first.at("snapshot_id").str()==s.cached("alice",url())->at("snapshot_id").str());}}
TEST(http_metadata_rejects_unsafe_and_ambiguous_validators){for(const auto& item:std::vector<std::pair<std::string,std::string>>{{"vary","Accept-Language"},{"cache_control","public, no-store"},{"cache_control","private=\"X-Test\""},{"cache_control","max-age=0"},{"cache_control","max-age=1,max-age=2"},{"etag","\"one\", \"two\""},{"etag","bad"},{"last_modified","Mon, 14 Nov 2023 22:13:20 GMT"},{"age","-1"}}){auto h=cache(url());h.set(item.first,item.second);rejects([&]{validate_http_cache(h,url());},"E_WORKER_PROTOCOL");}auto h=cache(url());h.set("etag","W/\"version-1\"");CHECK(merge_http_cache(cache(url()),h).at("etag").str()=="W/\"version-1\"");}
TEST(imported_or_redirected_snapshots_do_not_grant_conditions){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});s.ingest("alice",{url(),"import","text/plain","import"});web.read("alice",read_args());CHECK(!w.calls.back().has("conditional"));w.alter=[](const Json& p,Json){auto r=Worker{}.page(p);auto chain=Json::array();chain.push(Json::string(p.at("url").str()));r.set("redirects",chain);r.set("final_url",url("/docs/final"));r.set("http_cache",cache(url("/docs/final")));return r;};web.read("alice",read_args());web.read("alice",read_args());CHECK(!w.calls.back().has("conditional"));}
TEST(robots_revalidation_renews_exact_rules_and_retains_receipt_references){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));auto first=web.submit_crawl("alice",crawl_args("first")).at("job_id").str();drain(web);CHECK(s.job_get("alice",first).at("state").str()=="succeeded");w.now+=3600001;auto second=web.submit_crawl("alice",crawl_args("second")).at("job_id").str();drain(web);auto job=s.job_get("alice",second);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("usage").at("external_requests").num()==2);CHECK(job.at("usage").at("content_bytes").num()==0);CHECK(w.calls.at(2).at("kind").str()=="robots");CHECK(w.calls.at(2).has("conditional"));CHECK(w.calls.at(2).has("robots_revalidation_base"));CHECK(!w.calls.at(2).has("robots"));CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM map_hops h JOIN robots_cache c ON c.id=json_extract(h.admission_json,'$.robots_cache_id') WHERE h.kind='robots'")==1);}
TEST(robots_304_cannot_replace_rules_or_admit_content_on_failure){Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));web.submit_crawl("alice",crawl_args("base"));drain(web);w.now+=3600001;w.alter=[](const Json& p,Json r){if(p.at("kind").str()=="robots"){auto rule=Json::parse(R"({"pattern":"/","allow":false})"),rules=Json::array();rules.push(rule);r.at("robots").set("rules",rules);}return r;};auto id=web.submit_crawl("alice",crawl_args("bad-rules")).at("job_id").str();drain(web);CHECK(s.job_get("alice",id).at("partial").flag());CHECK(w.calls.size()==3);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==1);}
TEST(crawl_304_rebuilds_the_new_frontier_depth_from_complete_retained_links){Temp t;Worker w;w.links[url()]={url("/docs/child")};w.links[url("/docs/child")]={url("/docs/grandchild")};Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));web.submit_crawl("alice",crawl_args("shallow",url(),1));drain(web);CHECK(w.calls.size()==3);auto id=web.submit_crawl("alice",crawl_args("new-seed",url("/docs/child"),1)).at("job_id").str();drain(web);CHECK(s.job_get("alice",id).at("state").str()=="succeeded");CHECK(w.calls.at(3).has("conditional"));CHECK(w.calls.at(4).at("url").str()==url("/docs/grandchild"));CHECK(w.calls.at(4).at("depth").num()==1);}
TEST(crawl_304_nofollow_blocks_retained_links){Temp t;Worker w;w.links[url()]={url("/docs/child")};Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));web.submit_crawl("alice",crawl_args("base"));drain(web);const auto before=w.calls.size();w.alter=[](const Json& p,Json r){if(p.has("conditional")&&p.at("kind").str()=="page")r.set("publication_observation",restriction("nofollow"));return r;};web.submit_crawl("alice",crawl_args("nofollow"));drain(web);CHECK(w.calls.size()==before+1);CHECK(w.calls.back().has("conditional"));}

TEST(independent_same_snapshot_metadata_refresh_is_fenced) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  auto first=web.read("alice",read_args());const auto sid=first.at("snapshot_id").str();
  bool nested=false;
  w.alter=[&](const Json&,Json response) {
    if(!nested) { nested=true;++w.now;auto renewed=web.read("alice",read_args());CHECK(renewed.at("snapshot_id").str()==sid); }
    return response;
  };
  rejects([&]{web.read("alice",read_args());},"E_REVALIDATION_STALE");
  CHECK(s.cached("alice",url())->at("snapshot_id").str()==sid);
  CHECK(s.cached("alice",url())->at("fetched_at_ms").num()==w.now);
}

TEST(independent_during_request_restriction_cannot_republish_old_body) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("alice",read_args());CHECK(hits(s)==1);
  w.alter=[&](const Json&,Json response) {
    s.observe_web_publication("alice",url(),url(),restriction("noindex"),s.publication_fence("alice",url()));
    return response;
  };
  auto renewed=web.read("alice",read_args());
  CHECK(!renewed.at("indexed").flag());CHECK(hits(s)==0);
}

TEST(independent_304_metadata_growth_rolls_back_at_owner_quota) {
  Temp t;Worker w;std::int64_t used=0;std::string before;
  {Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("alice",read_args());
   used=scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'");before=s.cached("alice",url())->canonical();}
  StoreOptions options;options.max_bytes_per_owner=used;
  Store s(t.path/"db",[&]{return w.now;},options);WebService web(s,w,{});++w.now;
  rejects([&]{web.read("alice",read_args());},"E_QUOTA");
  CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==used);
  CHECK(s.cached("alice",url())->canonical()==before);
}

TEST(independent_304_then_full_fetch_refunds_only_retired_cache_metadata) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("alice",read_args());const auto before=scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'");
  web.read("alice",read_args());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")>before);
  w.alter=[&](const Json& p,Json){return w.page(p);};
  auto restored=web.read("alice",read_args());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==before);
  s.erase("alice",restored.at("document_id").str());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==0);
}

TEST(independent_latest_robots_denial_survives_selective_job_compaction) {
  Temp t;Worker w;const auto start=w.now;
  Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));
  web.submit_crawl("alice",crawl_args("initial-allow"));drain(web);
  // A later crawl pins the old allowing robots record, but its longer retry
  // deadline prevents compaction when the following short denial job expires.
  w.now=start+3599000;auto pin=crawl_args("long-retry-pin");pin.set("timeout_ms",std::int64_t{3600000});
  web.submit_crawl("alice",pin);drain(web);
  w.now=start+3600001;
  w.alter=[&](const Json& p,Json response) {
    if(p.at("kind").str()!="robots")return response;
    auto denied=Json::object();denied.set("status","error");denied.set("http_status",std::int64_t{503});
    denied.set("url",p.at("url"));denied.set("final_url",p.at("url"));denied.set("error_code","E_ROBOTS_UNAVAILABLE");
    denied.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":0})"));
    auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"deny_all","rules":[],"sitemaps":[],"diagnostics":[]})");
    record.set("origin",std::string(origin));record.set("body_sha256",sha256("synthetic HTTP 503"));
    record.set("fetched_at_ms",w.now);record.set("expires_at_ms",w.now+1000);denied.set("robots",record);return denied;
  };
  auto deny=crawl_args("short-denial");deny.set("timeout_ms",std::int64_t{1000});
  const auto denial_job=web.submit_crawl("alice",deny).at("job_id").str();drain(web);
  CHECK(s.job_get("alice",denial_job).at("partial").flag());
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);
  constexpr std::int64_t retention=7LL*24*60*60*1000;
  w.now=start+3601002+retention;auto compacted=s.compact_jobs("alice",64);
  CHECK(compacted.at("compacted_jobs").num()==2);
  // No direct SQL writes or fabricated leases: all decisions and references
  // above were persisted through normal submit/execute/compact APIs.
  w.alter={};web.submit_crawl("alice",crawl_args("after-compaction"));auto lease=web.lease_map();
  CHECK(lease.has_value());CHECK(lease->kind=="robots");
  CHECK(lease->conditional.is_null());
}
}
int main(){std::size_t failed=0;for(const auto& [name,run]:tests()){try{run();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n';}}std::cout<<tests().size()<<" tests / "<<assertions<<" assertions / "<<failed<<" failures\n";return failed?1:0;}
