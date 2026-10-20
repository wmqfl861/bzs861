#include "bzs/service.hpp"
#include "bzs/sites.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/crawl_trap.hpp"
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
  Json manifest() { return Json::parse(R"({"version":1,"sites":[{"id":"map-site","enabled":true,"origin":"https://map.example","path_prefixes":["/map/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://map.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})"); }
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
      if(p.at("kind").str()=="page") {
        auto publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker visible secret","snippet_title":"page title","directives":[],"diagnostics":[]})");
        auto page=Json::object();page.set("status","ok");page.set("html","<p>marker visible secret</p>");page.set("text","marker visible secret");page.set("content_type","text/html");page.set("title","page title");page.set("extractor","fixture-crawl/1");page.set("extract_status","ok");page.set("final_url",p.at("url"));page.set("http_status",std::int64_t{200});page.set("charset","utf-8");page.set("index_allowed",true);page.set("links",Json::array());page.set("metadata",Json::object());page.set("warnings",Json::array());page.set("redirects",Json::array());page.set("publication",publication);r.set("page",page);r.set("publication",publication);
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

  TEST(crawl_submission_is_distinct_and_bounded) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto p=args();p.set("max_urls",std::int64_t{2});const auto j=w.submit_crawl("alice",p);CHECK(j.at("kind").str()=="web.crawl");CHECK(j.at("max_pages").num()==2);CHECK(j.at("used_pages").num()==0);CHECK(!j.at("body_read").flag());
    CHECK(w.submit_crawl("alice",p).at("reused").flag());rejects([&]{w.submit_map("alice",p);},"E_IDEMPOTENCY_CONFLICT");
    p.set("max_pages",std::int64_t{3});rejects([&]{w.submit_crawl("alice",p);},"E_SCHEMA");
    p.set("max_pages",std::int64_t{0});rejects([&]{w.submit_crawl("alice",p);},"E_SCHEMA");
    p=args();p.set("approved",true);rejects([&]{w.submit_crawl("alice",p);},"E_SCHEMA");
    rejects([&]{s.crawl_edges("bob",j.at("job_id").str());},"E_NOT_FOUND");drain(w);
    auto results=s.job_results("alice",j.at("job_id").str());CHECK(results.at("items").size()==1);CHECK(results.at("items").at(0).at("body_read").flag());CHECK(s.stats("alice").at("snapshots").num()==1);
  }
  void publication(Json& response,bool index=true,bool snippets=true,bool follow=true,const std::string& text="marker visible secret",const std::string& title="page title",const std::vector<std::string>& directives={}) {
    auto policy=Json::parse(response.at("publication").dump());policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippets);policy.set("follow_allowed",follow);policy.set("snippet_text",index&&snippets?text:"");policy.set("snippet_title",index?title:"");
    auto values=Json::array();for(const auto& directive:directives)values.push(Json::string(directive));policy.set("directives",values);
    response.set("publication",policy);response.at("page").set("publication",policy);response.at("page").set("index_allowed",index);
  }
  Json freeze(Store& store,const std::string& query="marker",const std::string& owner="alice") { return store.search(owner,parse_query(Json::string(query))); }
  Json hits(Store& store,const Json& session,const std::string& owner="alice") { return store.page(owner,session.at("session_id").str(),"",100).at("hits"); }
  Json crawl_one(WebService& web,Store& store,const std::string& key="crawl",const std::string& url=std::string(base)+"/map/index") {
    auto p=args({url},key);p.set("max_depth",std::int64_t{0});const auto id=web.submit_crawl("alice",p).at("job_id").str();drain(web);auto result=store.job_results("alice",id).at("items");CHECK(result.size()==1);return result.at(0);
  }
  TEST(recursive_diamond_cycle_edges_are_durable_and_bodies_are_unique) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;auto links=Json::array();auto url=p.at("url").str();
      if(url.ends_with("index"))for(const auto* child:{"a","b"})links.push(candidate(p,std::string(base)+"/map/"+child));
      else if(url.ends_with("a")||url.ends_with("b"))links.push(candidate(p,std::string(base)+"/map/shared"));
      else { links.push(candidate(p,std::string(base)+"/map/index")); }
      r.at("discovery").set("candidates",links);return r; };
    auto p=args();p.set("max_depth",std::int64_t{4});const auto id=w.submit_crawl("alice",p).at("job_id").str();drain(w);
    CHECK(f.calls.size()==5);CHECK(s.job_get("alice",id).at("used_pages").num()==4);CHECK(s.stats("alice").at("snapshots").num()==4);
    auto edges=s.crawl_edges("alice",id,0,3);CHECK(edges.at("items").size()==3);CHECK(edges.at("has_more").flag());
    auto tail=s.crawl_edges("alice",id,edges.at("next_offset").num(),3);CHECK(tail.at("items").size()==2);CHECK(!tail.at("has_more").flag());
    for(const auto& page:{edges,tail})for(std::size_t i=0;i<page.at("items").size();++i) { CHECK(page.at("items").at(i).at("followed").flag());CHECK(page.at("items").at(i).at("reason").is_null()); }
    CHECK(hits(s,freeze(s)).size()==4);const auto result=s.job_results("alice",id,0,2);CHECK(result.at("has_more").flag());CHECK(s.job_results("alice",id,result.at("next_offset").num(),3).at("items").size()==2);
  }
  TEST(robots_sitemap_metadata_and_body_pages_share_one_bounded_frontier) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")r.at("robots").set("sitemaps",Json::parse(R"(["https://map.example/map/index.xml"] )"));
      else if(p.at("kind").str()=="sitemap") { r.at("discovery").set("document_type","sitemap_urlset");auto c=Json::array();c.push(candidate(p,std::string(base)+"/map/discovered","sitemap"));r.at("discovery").set("candidates",c); }
      return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==4);CHECK(s.job_get("alice",id).at("used_pages").num()==2);CHECK(s.stats("alice").at("snapshots").num()==2);
    CHECK(s.job_results("alice",id).at("items").size()==2);CHECK(s.crawl_edges("alice",id).at("items").at(0).at("source").str()=="sitemap");
  }
  TEST(page_budget_stops_before_another_body_dispatch_and_keeps_skipped_rows) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(const auto* name:{"a","b","c"})links.push(candidate(p,std::string(base)+"/map/"+name));r.at("discovery").set("candidates",links); }return r; };
    auto p=args();p.set("max_pages",std::int64_t{2});const auto id=w.submit_crawl("alice",p).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
    CHECK(job.at("state").str()=="succeeded");CHECK(job.at("partial").flag());CHECK(job.at("stop_reason").str()=="E_PAGE_BUDGET");CHECK(job.at("used_pages").num()==2);CHECK(job.at("skipped_pages").num()==2);CHECK(f.calls.size()==3);
    auto rows=s.job_results("alice",id).at("items");CHECK(rows.size()==4);CHECK(!rows.at(2).at("body_read").flag());CHECK(rows.at(2).at("snapshot_id").is_null());
  }
  TEST(depth_and_query_variants_bound_recursive_traps) {
    for(const std::string mode:{"depth","variants"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(int i=0;i<6;++i)links.push(candidate(p,std::string(base)+"/map/query?v="+std::to_string(i)));r.at("discovery").set("candidates",links); }return r; };
      auto p=args();if(mode=="depth")p.set("max_depth",std::int64_t{0});const auto id=w.submit_crawl("alice",p).at("job_id").str();drain(w);
      CHECK(s.job_get("alice",id).at("partial").flag());CHECK(f.calls.size()==(mode=="depth"?2U:6U));CHECK(s.crawl_edges("alice",id).at("items").size()==6);
      if(mode=="depth")CHECK(s.crawl_edges("alice",id).at("items").at(0).at("reason").str()=="E_MAP_DEPTH_LIMIT");
    }
  }
  TEST(noindex_refresh_retracts_frozen_rows_without_destroying_owner_evidence) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto original=crawl_one(w,s,"first");auto session=freeze(s);CHECK(hits(s,session).size()==1);auto ev=s.evidence("alice",original.at("snapshot_id").str(),0,6);
    f.response=[&](const Json& p) { auto r=f.ok(p);publication(r,false,false,true,"","",{"noindex"});return r; };
    auto restricted=crawl_one(w,s,"restricted");CHECK(restricted.at("snapshot_id").str()!=original.at("snapshot_id").str());CHECK(!restricted.at("publication").at("index_allowed").flag());CHECK(hits(s,session).size()==0);CHECK(hits(s,freeze(s)).size()==0);
    CHECK(s.evidence_get("alice",ev.at("evidence_id").str()).at("quote").str()=="marker");CHECK(s.snapshot("alice",original.at("snapshot_id").str()).at("canonical_text").str()=="marker visible secret");
    f.response={};crawl_one(w,s,"allow-again");CHECK(hits(s,freeze(s)).size()==1);CHECK(hits(s,session).size()==0);
  }
  TEST(nosnippet_and_data_nosnippet_restrict_old_cursors_and_do_not_forge_offsets) {
    for(const std::string mode:{"nosnippet","data-nosnippet"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto original=crawl_one(w,s,"first");auto frozen=freeze(s);CHECK(!hits(s,frozen).at(0).at("snippet").str().empty());
      f.response=[&](const Json& p) { auto r=f.ok(p);publication(r,true,mode!="nosnippet",true,"marker visible","safe title",{mode});return r; };
      auto fresh=crawl_one(w,s,"restricted");CHECK(fresh.at("snapshot_id").str()!=original.at("snapshot_id").str());
      for(const auto& result:{hits(s,frozen),hits(s,freeze(s))}) { CHECK(result.size()==1);CHECK(result.at(0).at("snippet").str().find("secret")==std::string::npos);CHECK(result.at(0).at("highlights").size()==0); }
      CHECK(s.snapshot("alice",fresh.at("snapshot_id").str()).at("canonical_text").str().find("secret")!=std::string::npos);
      f.response={};crawl_one(w,s,"allow-again");CHECK(hits(s,frozen).at(0).at("snippet").str().empty());
    }
  }
  TEST(empty_successful_page_keeps_the_restrictive_refresh) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto original=crawl_one(w,s,"first");auto frozen=freeze(s);
    f.response=[&](const Json& p) { auto r=f.ok(p);publication(r,false,false,true,"","",{"noindex"});auto page=r.at("page");page.set("status","empty_content");page.set("html","");page.set("text","");page.set("extract_status","empty");return r; };
    auto empty=crawl_one(w,s,"empty");CHECK(empty.at("state").str()=="succeeded");CHECK(empty.at("body_read").flag());CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);CHECK(s.snapshot("alice",empty.at("snapshot_id").str()).at("canonical_text").str().empty());CHECK(!s.snapshot("alice",original.at("snapshot_id").str()).at("canonical_text").str().empty());
  }
  TEST(quota_failure_retains_restriction_observation_without_claiming_a_new_page) {
    for(const std::string mode:{"noindex","nosnippet","data-nosnippet"}) {
      Temp t;Fake f;StoreOptions limits;limits.max_snapshots_per_owner=1;Store s(t.path/"db",[&]{return f.now;},limits);WebService w(s,f,{},SiteRegistry(manifest()));auto original=crawl_one(w,s,"first");auto frozen=freeze(s);
      f.response=[&](const Json& p) { auto r=f.ok(p);publication(r,mode!="noindex",mode=="data-nosnippet",true,"marker public","safe title",{mode});return r; };
      auto failed=crawl_one(w,s,"quota");CHECK(failed.at("state").str()=="failed");CHECK(failed.at("error_code").str()=="E_QUOTA");CHECK(!failed.at("body_read").flag());CHECK(failed.at("snapshot_id").is_null());CHECK(s.stats("alice").at("snapshots").num()==1);
      auto old=hits(s,frozen);auto current=hits(s,freeze(s));if(mode=="noindex") { CHECK(old.size()==0);CHECK(current.size()==0); } else { CHECK(old.at(0).at("snippet").str().empty());CHECK(current.at(0).at("snippet").str().empty()); }
      CHECK(s.snapshot("alice",original.at("snapshot_id").str()).at("canonical_text").str().find("secret")!=std::string::npos);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM map_hops WHERE response_json LIKE '%publication_observation%'")==1);
    }
  }
  TEST(failed_header_observation_is_monotonic_and_keeps_failed_usage) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto original=crawl_one(w,s,"first");auto frozen=freeze(s);
    bool noindex=true;
    f.response=[&](const Json& p) { auto r=f.ok(p);publication(r,!noindex,false,true,"","",{noindex?"noindex":"nosnippet"});r.set("publication_observation",r.at("publication"));r.set("status","error");r.set("error_code","E_DISCOVERY_PARSE");return r; };
    auto failed=crawl_one(w,s,"parse-error");CHECK(failed.at("state").str()=="failed");CHECK(failed.at("error_code").str()=="E_DISCOVERY_PARSE");CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);CHECK(s.stats("alice").at("snapshots").num()==1);
    noindex=false;crawl_one(w,s,"less-restrictive-failure");CHECK(hits(s,freeze(s)).size()==0);CHECK(scalar(s.raw_handle(),"SELECT index_allowed FROM document_publication LIMIT 1")==0);CHECK(scalar(s.raw_handle(),"SELECT SUM(external_requests) FROM map_hops")==4);
    CHECK(!s.snapshot("alice",original.at("snapshot_id").str()).at("canonical_text").str().empty());
  }
  TEST(body_redirect_uses_one_page_grant_and_propagates_policy_to_existing_redirect_reads) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto parsed=parse_document("marker former redirect","text/plain","old");auto meta=Json::object();meta.set("final_url",std::string(base)+"/map/target");auto old=s.ingest_web("alice",{std::string(base)+"/map/source","","text/plain","old"},parsed,meta,0);auto frozen=freeze(s);
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
      if(p.at("url").str().ends_with("index")) { r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",std::string(base)+"/map/target"); }
      else { publication(r,false,false,true,"","",{"noindex"}); }
      return r; };
    auto target=crawl_one(w,s,"redirect");CHECK(target.at("state").str()=="succeeded");CHECK(target.at("final_url").str()==std::string(base)+"/map/target");CHECK(f.calls.size()==3);CHECK(scalar(s.raw_handle(),"SELECT SUM(used_pages) FROM map_jobs")==1);CHECK(hits(s,freeze(s)).size()==0);CHECK(hits(s,frozen).size()==0);CHECK(s.snapshot("alice",old.at("snapshot_id").str()).at("canonical_text").str()=="marker former redirect");
  }
  TEST(existing_normalized_aliases_share_publication_and_deletion_fences) {
    for(bool erase:{false,true}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto alias=s.ingest("alice",{"https://MAP.example:443/map/index#old","marker alias","text/plain","alias"});auto frozen=freeze(s);
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
        if(erase) { s.erase("alice",alias.at("document_id").str());s.ingest("alice",{"https://MAP.example:443/map/index#old","explicit restore","text/plain","restored"}); }
        else { publication(r,false,false,true,"","",{"noindex"}); }
      return r; };
      auto result=crawl_one(w,s,"alias");CHECK(result.at("state").str()==(erase?"failed":"succeeded"));CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);
      if(erase)CHECK(result.at("snapshot_id").is_null());else CHECK(s.snapshot("alice",alias.at("snapshot_id").str()).at("canonical_text").str()=="marker alias");
    }
  }
  TEST(shared_dispatch_is_round_robin_across_map_crawl_jobs_and_restart) {
    Temp t;Fake f;std::string first,second,mapped;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));first=w.submit_crawl("alice",args({std::string(base)+"/map/a",std::string(base)+"/map/b",std::string(base)+"/map/c"},"first")).at("job_id").str();
      auto lease=w.lease_map();CHECK(lease);w.execute_map(*lease);lease=w.lease_map();CHECK(lease);w.execute_map(*lease);
      ++f.now;mapped=w.submit_map("alice",args({std::string(base)+"/map/metadata"},"mapped")).at("job_id").str();++f.now;second=w.submit_crawl("alice",args({std::string(base)+"/map/second"},"second")).at("job_id").str(); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));auto lease=w.lease_map();CHECK(lease);CHECK(lease->job_id==mapped);w.execute_map(*lease);lease=w.lease_map();CHECK(lease);CHECK(lease->job_id==second);w.execute_map(*lease);drain(w);CHECK(s.job_get("alice",first).at("state").str()=="succeeded");CHECK(f.calls.size()==6); }
  }
  TEST(pause_cancel_and_auth_epoch_fence_late_body_and_observation) {
    for(const std::string mode:{"pause","cancel","epoch"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));const auto old=s.ingest("alice",{std::string(base)+"/map/index","marker old","text/plain","old"});std::string id;
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;publication(r,false,false,true,"","",{"noindex"});r.set("publication_observation",r.at("publication"));
        if(mode=="epoch")sql(s.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");else s.job_control("alice",id,mode);return r; };
      id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);const auto result=s.job_results("alice",id).at("items").at(0);CHECK(!result.at("body_read").flag());CHECK(s.stats("alice").at("snapshots").num()==1);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM document_publication")==0);CHECK(s.snapshot("alice",old.at("snapshot_id").str()).at("canonical_text").str()=="marker old");
    }
  }
  TEST(restarted_unknown_body_keeps_request_byte_and_page_reservations_without_retry) {
    Temp t;Fake f;std::string id;std::int64_t bytes=0;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_crawl("alice",args()).at("job_id").str();auto robots=w.lease_map();CHECK(robots);w.execute_map(*robots);auto page=w.lease_map();CHECK(page);bytes=page->byte_grant;CHECK(s.job_get("alice",id).at("used_pages").num()==1); }
    f.now+=30000;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));drain(w);auto job=s.job_get("alice",id);CHECK(job.at("state").str()=="failed");CHECK(job.at("used_pages").num()==1);CHECK(job.at("usage").at("unknown_requests_charged").num()==1);CHECK(job.at("usage").at("unknown_bytes_charged").num()==bytes);CHECK(f.calls.size()==1);CHECK(s.stats("alice").at("snapshots").num()==0);rejects([&]{s.job_control("alice",id,"retry_failed");},"E_JOB_STATE"); }
  }
  TEST(known_zero_http_refusal_refunds_page_and_request_grant) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="page") { r.set("status","error");r.set("http_status",Json());r.set("error_code","E_ROBOTS_DISALLOWED");r.at("robots_decision").set("allowed",false);r.set("usage",Json::parse(R"({"external_requests":0,"content_bytes":0})")); }return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto job=s.job_get("alice",id);CHECK(job.at("used_pages").num()==0);CHECK(job.at("usage").at("external_requests").num()==1);CHECK(job.at("budget").at("charged_bytes").num()==100);CHECK(scalar(s.raw_handle(),"SELECT requests FROM site_request_state")==1);
  }
  TEST(body_policy_index_edges_and_hop_receipt_commit_atomically) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));const auto id=w.submit_crawl("alice",args()).at("job_id").str();auto robots=w.lease_map();CHECK(robots);w.execute_map(*robots);
    sql(s.raw_handle(),"CREATE TRIGGER fail_crawl_receipt BEFORE UPDATE OF response_json ON map_hops WHEN NEW.response_json LIKE '%snapshot_id%' BEGIN SELECT RAISE(ABORT,'fixture failure'); END;");
    f.response=[&](const Json& p) { auto r=f.ok(p);auto links=Json::array();links.push(candidate(p,std::string(base)+"/map/late"));r.at("discovery").set("candidates",links);return r; };
    drain(w);CHECK(s.stats("alice").at("snapshots").num()==0);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM document_publication")==0);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM content_fts")==0);CHECK(s.crawl_edges("alice",id).at("items").size()==0);CHECK(s.job_get("alice",id).at("usage").at("external_requests").num()==2);
  }
  TEST(edge_capacity_stops_enqueue_before_an_unrecorded_body) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));const auto id=w.submit_crawl("alice",args()).at("job_id").str();
    const auto fill="WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000) INSERT INTO crawl_edges(job_id,position,parent,url,source,depth,followed,parent_generation,target_generation) SELECT '"+id+"',x-1,'https://map.example/map/index','https://map.example/map/retained/'||x,'html',1,0,0,0 FROM n;";sql(s.raw_handle(),fill.c_str());
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="page") { auto links=Json::array();links.push(candidate(p,std::string(base)+"/map/new"));r.at("discovery").set("candidates",links); }return r; };
    drain(w);CHECK(f.calls.size()==2);CHECK(s.job_get("alice",id).at("stop_reason").str()=="E_CRAWL_EDGE_LIMIT");CHECK(s.job_get("alice",id).at("page_count").num()==1);CHECK(s.crawl_edges("alice",id).at("total").num()==10000);
  }
  void drop_schema_seven(sqlite3* db) {
    sql(db,R"SQL(DROP TABLE crawl_trap_edges; DROP TABLE crawl_trap_urls; DROP TABLE crawl_trap_policy;
 DROP TABLE site_publication_rules; DROP TABLE site_publication_state;
 ALTER TABLE snapshots DROP COLUMN site_bindings_json; ALTER TABLE snapshots DROP COLUMN site_publication_generation;
 ALTER TABLE session_rows DROP COLUMN site_binding_version; PRAGMA user_version=6;)SQL");
  }
  void drop_crawl(sqlite3* db) {
    drop_schema_seven(db);
    sql(db,R"SQL(DROP TABLE crawl_edges; DROP TABLE document_publication; DROP INDEX documents_url_key; DROP INDEX documents_representation_key; DROP INDEX map_resource_snapshot; DROP INDEX session_rows_representation;
 ALTER TABLE documents DROP COLUMN url_key; ALTER TABLE documents DROP COLUMN representation_key; ALTER TABLE snapshots DROP COLUMN publication_json; ALTER TABLE snapshots DROP COLUMN representation_publication_json;
 ALTER TABLE session_rows DROP COLUMN publication_generation; ALTER TABLE session_rows DROP COLUMN representation_key; ALTER TABLE map_jobs DROP COLUMN max_pages; ALTER TABLE map_jobs DROP COLUMN used_pages;
 ALTER TABLE map_jobs DROP COLUMN last_dispatch_sequence; ALTER TABLE map_resources DROP COLUMN parent; ALTER TABLE map_resources DROP COLUMN source;
 ALTER TABLE map_resources DROP COLUMN page_charged; ALTER TABLE map_resources DROP COLUMN publication_ceiling_json; ALTER TABLE map_resources DROP COLUMN snapshot_id; ALTER TABLE map_resources DROP COLUMN receipt_json;
 DELETE FROM settings WHERE key IN ('map_dispatch_sequence','publication_restriction_sequence'); PRAGMA user_version=5;)SQL");
  }
  TEST(schema_five_migration_preserves_map_read_sessions_snapshots_and_index_identity) {
    Temp t;Fake f;Json snapshot,evidence,session;std::string map_id,read_id;
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));snapshot=s.ingest("alice",{"https://MAP.example:443/map/old#fragment","marker preserved","text/plain","old"});evidence=s.evidence("alice",snapshot.at("snapshot_id").str(),0,6);session=freeze(s);map_id=w.submit_map("alice",args()).at("job_id").str();drain(w);read_id=s.read_many_submit("alice",{std::string(base)+"/map/read"},"read",{}).at("job_id").str();sql(s.raw_handle(),"UPDATE content_fts SET rowid=913;");drop_crawl(s.raw_handle()); }
    { Store s(t.path/"db",[&]{return f.now;});CHECK(scalar(s.raw_handle(),"PRAGMA user_version")==8);CHECK(scalar(s.raw_handle(),"SELECT rowid FROM content_fts")==913);CHECK(s.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker preserved");CHECK(s.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()=="marker");CHECK(hits(s,session).size()==1);CHECK(s.job_get("alice",map_id).at("state").str()=="succeeded");CHECK(s.job_get("alice",read_id).at("state").str()=="queued");CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM documents WHERE url_key='https://map.example/map/old'")==1); }
  }
  TEST(schema_five_quarantine_rejects_before_new_publication_ddl) {
    Temp t;{ Store s(t.path/"db");drop_crawl(s.raw_handle());sql(s.raw_handle(),"UPDATE settings SET value='1' WHERE key='restore_quarantine';"); }
    rejects([&]{Store forbidden(t.path/"db");},"E_RESTORE_QUARANTINED");sqlite3* raw=nullptr;CHECK(sqlite3_open((t.path/"db").string().c_str(),&raw)==SQLITE_OK);std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);CHECK(scalar(raw,"PRAGMA user_version")==5);CHECK(scalar(raw,"SELECT COUNT(*) FROM sqlite_master WHERE name='document_publication'")==0);
  }
  struct Reader:WorkerPort {
    Fake fixture;std::function<Json(const Json&)> response;std::vector<Json> reads;
    Json ok(const Json& params)const {
      auto p=Json::parse(params.dump());p.set("kind","page");p.set("depth",std::int64_t{0});p.set("robots",Json::parse(R"({"body_sha256":"fixture"})"));
      auto page=fixture.ok(p).at("page");page.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));return page;
    }
    Json call(const std::string& method,const Json& params,int timeout)override { CHECK(method=="fetch.extract");CHECK(timeout>0);reads.push_back(params);return response?response(params):ok(params); }
  };
  Json page_policy(Json page,bool index,bool snippets) {
    page.set("index_allowed",index);auto policy=page.at("publication");policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippets);
    if(!index||!snippets)policy.set("snippet_text","");
    if(!index)policy.set("snippet_title","");
    if(!index)policy.at("directives").push(Json::string("noindex"));else if(!snippets)policy.at("directives").push(Json::string("nosnippet"));
    return page;
  }
  Json refresh_args(const std::string& url) { auto p=Json::object();p.set("url",url);p.set("freshness","refresh");return p; }
  Json hit_url(const Json& rows,const std::string& url) { for(std::size_t i=0;i<rows.size();++i)if(rows.at(i).at("url").str()==url)return rows.at(i);return Json(); }
  TEST(crawl_late_body_cannot_undo_a_newer_direct_page_restriction_or_replace_fresh_current) {
    for(bool index:{false,true})for(bool restore_before:{false,true}) {
      Temp t;Fake f;Reader reader;Store first(t.path/"db",[&]{return f.now;}),second(t.path/"db",[&]{return f.now;});WebService crawl(first,f,{},SiteRegistry(manifest())),read(second,reader,{});
      const auto url=std::string(base)+"/map/a";read.read("alice",refresh_args(url));const auto previous=freeze(first);Json qualified;
      f.response=[&](const Json& p) {
        auto response=f.ok(p);if(p.at("kind").str()!="page")return response;
        reader.response=[&](const Json& q) { return page_policy(reader.ok(q),index,false); };read.read("alice",refresh_args(url));
        if(restore_before) { reader.response={};qualified=read.read("alice",refresh_args(url)); }
        return response;
      };
      const auto late=crawl_one(crawl,first,"late",url);CHECK(late.at("state").str()=="succeeded");CHECK(late.at("body_read").flag());
      const auto snapshot=first.snapshot("alice",late.at("snapshot_id").str());CHECK(!snapshot.at("publication").at("snippet_allowed").flag());CHECK(snapshot.at("canonical_text").str()=="marker visible secret");
      const auto current=hit_url(hits(first,freeze(first)),url);
      if(restore_before) { CHECK(!current.is_null());CHECK(!current.at("snippet").str().empty());CHECK(first.cached("alice",url)->at("snapshot_id").str()==qualified.at("snapshot_id").str()); }
      else if(!index)CHECK(current.is_null());else CHECK(current.at("snippet").str().empty());
      CHECK(f.calls.size()==2);const auto old=hit_url(hits(first,previous),url);if(!index)CHECK(old.is_null());else CHECK(old.at("snippet").str().empty());
      reader.response={};read.read("alice",refresh_args(url));CHECK(!hit_url(hits(first,freeze(first)),url).at("snippet").str().empty());
      const auto after=hit_url(hits(first,previous),url);if(!index)CHECK(after.is_null());else CHECK(after.at("snippet").str().empty());
    }
  }
  TEST(crawl_logical_refresh_sequence_survives_redirect_pause_and_restart) {
    for(bool index:{false,true}) {
      Temp t;Fake f;Reader reader;std::string id;Json previous;const std::string a=std::string(base)+"/map/a",x=std::string(base)+"/map/x",b=std::string(base)+"/map/b";
      f.response=[&](const Json& p) { auto response=f.ok(p);if(p.at("kind").str()=="page"&&p.at("url").str()!=b) { response.set("status","redirect");response.set("http_status",std::int64_t{302});response.set("redirect_url",p.at("url").str()==a?x:b); }return response; };
      {
        Store first(t.path/"db",[&]{return f.now;});WebService crawl(first,f,{},SiteRegistry(manifest())),read(first,reader,{});
        reader.response=[&](const Json& p) { auto page=reader.ok(p);page.set("final_url",b);auto redirects=Json::array();redirects.push(Json::string(x));redirects.push(Json::string(b));page.set("redirects",redirects);return page; };
        read.read("alice",refresh_args(a));previous=freeze(first);id=crawl.submit_crawl("alice",args({a},"retained-logical-sequence")).at("job_id").str();
        auto robots=crawl.lease_map();CHECK(robots&&robots->kind=="robots");crawl.execute_map(*robots);auto start=crawl.lease_map();CHECK(start&&start->url==a);crawl.execute_map(*start);first.job_control("alice",id,"pause");CHECK(first.job_get("alice",id).at("state").str()=="paused");
      }
      Store reopened(t.path/"db",[&]{return f.now;}),second(t.path/"db",[&]{return f.now;});WebService read(second,reader,{}),crawl(reopened,f,{},SiteRegistry(manifest()));
      reader.response=[&](const Json& p) { return page_policy(reader.ok(p),index,false); };read.read("alice",refresh_args(x));reader.response={};read.read("alice",refresh_args(x));
      reopened.job_control("alice",id,"resume");drain(crawl);CHECK(reopened.job_get("alice",id).at("state").str()=="succeeded");CHECK(f.calls.size()==4);CHECK(reopened.job_get("alice",id).at("usage").at("external_requests").num()==4);
      const auto rows=hits(reopened,freeze(reopened));CHECK(!hit_url(rows,b).is_null());CHECK(!hit_url(rows,b).at("snippet").str().empty());CHECK(!hit_url(rows,x).at("snippet").str().empty());
      const auto current=hit_url(rows,a),old=hit_url(hits(reopened,previous),a);if(!index) { CHECK(current.is_null());CHECK(old.is_null()); }else { CHECK(current.at("snippet").str().empty());CHECK(old.at("snippet").str().empty()); }
      read.read("alice",refresh_args(a));CHECK(!hit_url(hits(reopened,freeze(reopened)),a).is_null());const auto after=hit_url(hits(reopened,previous),a);if(!index)CHECK(after.is_null());else CHECK(after.at("snippet").str().empty());
    }
  }
  TEST(schema_seven_redirected_crawl_keeps_an_unknown_logical_capture_restricted_after_upgrade) {
    Temp t;Fake f;Reader reader;std::string id;Json previous;const std::string a=std::string(base)+"/map/a",b=std::string(base)+"/map/b";
    f.response=[&](const Json& p) { auto response=f.ok(p);if(p.at("kind").str()=="page"&&p.at("url").str()==a) { response.set("status","redirect");response.set("http_status",std::int64_t{302});response.set("redirect_url",b); }return response; };
    {
      Store store(t.path/"db",[&]{return f.now;});WebService crawl(store,f,{},SiteRegistry(manifest())),read(store,reader,{});
      reader.response=[&](const Json& p) { auto page=reader.ok(p);page.set("final_url",b);auto redirects=Json::array();redirects.push(Json::string(b));page.set("redirects",redirects);return page; };
      read.read("alice",refresh_args(a));previous=freeze(store);id=crawl.submit_crawl("alice",args({a},"schema-seven-inflight")).at("job_id").str();auto robots=crawl.lease_map();CHECK(robots);crawl.execute_map(*robots);auto start=crawl.lease_map();CHECK(start);crawl.execute_map(*start);
      reader.response=[&](const Json& p) { return page_policy(reader.ok(p),false,false); };read.read("alice",refresh_args(b));
      sql(store.raw_handle(),"ALTER TABLE document_publication DROP COLUMN index_generation; ALTER TABLE document_publication DROP COLUMN snippet_generation; ALTER TABLE session_rows DROP COLUMN snippet_suppressed; ALTER TABLE read_job_items DROP COLUMN publication_sequence; ALTER TABLE read_job_items DROP COLUMN publication_snapshot_id; ALTER TABLE map_resources DROP COLUMN publication_sequence; ALTER TABLE map_resources DROP COLUMN publication_snapshot_id; UPDATE snapshots SET meta_json=json_remove(meta_json,'$.page_publication_sequence','$.page_publication_history','$.page_publication_stale','$.publication_fence'); UPDATE map_hops SET admission_json=json_remove(admission_json,'$.publication_sequence','$.publication_snapshot_id'); PRAGMA user_version=7;");
    }
    Store reopened(t.path/"db",[&]{return f.now;});WebService crawl(reopened,f,{},SiteRegistry(manifest())),read(reopened,reader,{});CHECK(scalar(reopened.raw_handle(),"PRAGMA user_version")==8);drain(crawl);CHECK(reopened.job_get("alice",id).at("state").str()=="succeeded");CHECK(hits(reopened,freeze(reopened)).size()==0);
    reader.response={};read.read("alice",refresh_args(b));const auto current=hits(reopened,freeze(reopened));CHECK(hit_url(current,a).is_null());CHECK(!hit_url(current,b).is_null());CHECK(hits(reopened,previous).size()==0);CHECK(f.calls.size()==3);
    read.read("alice",refresh_args(a));CHECK(!hit_url(hits(reopened,freeze(reopened)),a).is_null());CHECK(hits(reopened,previous).size()==0);
  }
  TEST(links_only_publication_ceiling_survives_direct_and_batch_refresh) {
    for(bool batch:{false,true}) {
      Temp t;Fake f;auto config=manifest();config.at("sites").at(0).at("review").set("redistribution_policy","links_only");Store s(t.path/"db",[&]{return f.now;});WebService crawl(s,f,{},SiteRegistry(config));crawl_one(crawl,s,"first");CHECK(hits(s,freeze(s)).at(0).at("snippet").str().empty());
      Reader reader;WebService web(s,reader,{},SiteRegistry(config));
      if(batch) { const auto job=s.read_many_submit("alice",{std::string(base)+"/map/index"},"batch-refresh",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);CHECK(s.job_get("alice",job.at("job_id").str()).at("state").str()=="succeeded"); }
      else { auto p=Json::object();p.set("url",std::string(base)+"/map/index");p.set("freshness","refresh");CHECK(web.read("alice",p).at("content").at("text").str().find("secret")!=std::string::npos); }
      auto result=hits(s,freeze(s));CHECK(result.size()==1);CHECK(result.at(0).at("snippet").str().empty());CHECK(result.at(0).at("title").str()==std::string(base)+"/map/index");
    }
  }
  TEST(candidate_registry_without_optional_redistribution_does_not_break_explicit_read) {
    Temp t;Reader reader;auto config=manifest();auto site=config.at("sites").at(0);site.set("enabled",false);site.set("review",Json::parse(R"({"status":"candidate","payment_scheme":"unverified","automated_access":"unverified","checked_at":null,"terms_url":null})"));Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(config));
    auto p=Json::object();p.set("url",std::string(base)+"/map/index");const auto read=web.read("alice",p);CHECK(read.at("content").at("text").str().find("marker")!=std::string::npos);
    CHECK(hits(s,freeze(s)).size()==0);CHECK(s.snapshot("alice",read.at("snapshot_id").str()).at("canonical_text").str().find("marker")!=std::string::npos);rejects([&]{web.submit_crawl("alice",args());},"E_SITE_REVIEW");
    p.set("url",std::string(base)+"/unregistered/index");CHECK(web.read("alice",p).at("content").at("text").str().find("marker")!=std::string::npos);auto public_hits=hits(s,freeze(s));CHECK(public_hits.size()==1);CHECK(public_hits.at(0).at("url").str()==std::string(base)+"/unregistered/index");
  }
  TEST(empty_meta_only_explicit_and_batch_reads_retract_without_a_new_snapshot) {
    for(bool batch:{false,true}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));auto old=s.ingest("alice",{std::string(base)+"/map/index","marker old private","text/plain","old"});auto frozen=freeze(s);
      reader.response=[&](const Json& p) { auto page=reader.ok(p);page.set("status","empty_content");page.set("extract_status","empty");page.set("html","<meta name='BzsBot' content='noindex'>");page.set("text","");page.set("index_allowed",false);auto policy=page.at("publication");policy.set("index_allowed",false);policy.set("snippet_allowed",false);policy.set("snippet_text","");policy.set("snippet_title","");policy.set("directives",Json::parse(R"(["noindex"])"));return page; };
      if(batch) { const auto job=s.read_many_submit("alice",{std::string(base)+"/map/index"},"empty-batch",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);CHECK(s.job_get("alice",job.at("job_id").str()).at("state").str()=="failed");CHECK(s.job_get("alice",job.at("job_id").str()).at("usage").at("external_requests").num()==1); }
      else { auto p=Json::object();p.set("url",std::string(base)+"/map/index");p.set("freshness","refresh");rejects([&]{web.read("alice",p);},"E_PARSE_FAILED"); }
      CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);CHECK(s.stats("alice").at("snapshots").num()==1);CHECK(!s.snapshot("alice",old.at("snapshot_id").str()).at("canonical_text").str().empty());
    }
  }
  TEST(success_protocol_rejects_partial_error_http_and_hidden_redirects_with_known_usage) {
    for(const std::string mode:{"http500","partial206","redirects"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;if(mode=="redirects")r.at("page").at("redirects").push(Json::string(std::string(base)+"/map/hidden"));else { const std::int64_t status=mode=="http500"?500:206;r.set("http_status",status);r.at("page").set("http_status",status); }return r; };
      auto failed=crawl_one(w,s,"protocol");CHECK(failed.at("state").str()=="failed");CHECK(failed.at("error_code").str()=="E_WORKER_PROTOCOL");CHECK(!failed.at("body_read").flag());CHECK(scalar(s.raw_handle(),"SELECT SUM(external_requests) FROM map_hops")==2);CHECK(s.stats("alice").at("snapshots").num()==0);
    }
  }
  TEST(allowing_page_cannot_clear_its_own_noindex_observation) {
    for(bool malformed_discovery:{false,true}) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));crawl_one(w,s,"first");auto frozen=freeze(s);
    f.response=[&](const Json& p) { auto r=f.ok(p);auto observation=Json::parse(r.at("publication").dump());observation.set("index_allowed",false);observation.set("snippet_allowed",false);observation.set("snippet_text","");observation.set("snippet_title","");observation.set("directives",Json::parse(R"(["noindex"])"));r.set("publication_observation",observation);if(malformed_discovery)r.at("discovery").set("candidates",Json());return r; };
    auto failed=crawl_one(w,s,"conflict");CHECK(failed.at("state").str()=="failed");CHECK(failed.at("error_code").str()=="E_WORKER_PROTOCOL");CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);CHECK(s.stats("alice").at("snapshots").num()==1);CHECK(scalar(s.raw_handle(),"SELECT SUM(external_requests) FROM map_hops")==3);
    }
  }
  TEST(redirect_observation_survives_pause_restart_and_cannot_be_relaxed_by_target) {
    Temp t;Fake f;std::string id;Json frozen,old;
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) {
      r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",std::string(base)+"/map/target");publication(r,false,false,true,"","",{"noindex"});r.set("publication_observation",r.at("publication"));
    }return r; };
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));old=s.ingest("alice",{std::string(base)+"/map/index","marker old","text/plain","old"});frozen=freeze(s);id=w.submit_crawl("alice",args()).at("job_id").str();auto lease=w.lease_map();CHECK(lease);w.execute_map(*lease);lease=w.lease_map();CHECK(lease);w.execute_map(*lease);s.job_control("alice",id,"pause");CHECK(hits(s,frozen).size()==0); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));s.job_control("alice",id,"resume");drain(w);CHECK(s.job_get("alice",id).at("state").str()=="succeeded");CHECK(s.job_get("alice",id).at("used_pages").num()==1);CHECK(hits(s,frozen).size()==0);auto fresh=hits(s,freeze(s));CHECK(fresh.size()==1);CHECK(fresh.at(0).at("url").str()==std::string(base)+"/map/target");CHECK(scalar(s.raw_handle(),"SELECT index_allowed FROM document_publication WHERE url_key='https://map.example/map/index'")==0);CHECK(s.snapshot("alice",old.at("snapshot_id").str()).at("canonical_text").str()=="marker old"); }
  }
  Json representation(Store& s,const std::string& logical,const std::string& final_url,const std::string& text,bool index=true,bool snippets=true) {
    ParsedDocument parsed;parsed.text=text;parsed.title="marker private title";parsed.content_type="text/plain";parsed.parser_version="representation-fixture/1";parsed.index_allowed=index;
    parsed.publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");parsed.publication.set("index_allowed",index);parsed.publication.set("snippet_allowed",index&&snippets);parsed.publication.set("snippet_text",index&&snippets?text:"");parsed.publication.set("snippet_title",index?parsed.title:"");
    auto meta=Json::object();meta.set("final_url",final_url);return s.ingest_web("alice",{logical,text,"text/plain",parsed.title},parsed,meta,0);
  }
  TEST(frozen_representation_is_immutable_across_retarget_and_same_content_reuse) {
    for(bool identical:{false,true})for(bool first_restrict_after_move:{false,true})for(bool index:{false,true}) {
      Temp t;Store s(t.path/"db");const auto a=std::string(base)+"/map/a",b=std::string(base)+"/map/b",c=std::string(base)+"/map/c";
      auto old=representation(s,a,b,"marker secret original");auto frozen=freeze(s);
      if(!first_restrict_after_move)representation(s,b,b,"marker secret original",index,false);
      auto evidence=s.evidence("alice",old.at("snapshot_id").str(),0,6);
      auto moved=representation(s,a,c,identical?"marker secret original":"marker allowed replacement");CHECK(moved.at("snapshot_id").str()!=old.at("snapshot_id").str());
      CHECK(s.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()=="marker");CHECK(s.snapshot("alice",old.at("snapshot_id").str()).at("meta").at("final_url").str()==b);
      if(first_restrict_after_move)representation(s,b,b,"marker secret original",index,false);
      auto original=hits(s,frozen);if(index) { CHECK(original.size()==1);CHECK(original.at(0).at("snippet").str().empty());CHECK(original.at(0).at("title").str()==a);CHECK(original.at(0).at("highlights").size()==0); }else CHECK(original.size()==0);
      auto fresh=hits(s,freeze(s));bool found=false;for(std::size_t i=0;i<fresh.size();++i)if(fresh.at(i).at("url").str()==a) { found=true;CHECK(!fresh.at(i).at("snippet").str().empty()); }CHECK(found);
      representation(s,b,b,"marker B allowed again");if(!index)CHECK(hits(s,frozen).size()==0);else CHECK(hits(s,frozen).at(0).at("snippet").str().empty());
    }
  }
  TEST(independent_store_connections_cannot_double_reserve_the_same_origin) {
    Temp t;Fake f;Store first(t.path/"db",[&]{return f.now;});Store second(t.path/"db",[&]{return f.now;});const SiteRegistry sites(manifest());
    first.crawl_submit("alice",args({std::string(base)+"/map/a"},"first"),sites);second.map_submit("bob",args({std::string(base)+"/map/b"},"second"),sites);
    std::optional<MapLease> a,b;std::exception_ptr e1,e2;
    std::jthread left([&]{try { a=first.lease_next_map(sites); } catch(...) { e1=std::current_exception(); }});
    std::jthread right([&]{try { b=second.lease_next_map(sites); } catch(...) { e2=std::current_exception(); }});left.join();right.join();if(e1)std::rethrow_exception(e1);if(e2)std::rethrow_exception(e2);
    CHECK(a.has_value()!=b.has_value());CHECK(scalar(first.raw_handle(),"SELECT COUNT(*) FROM map_hops WHERE state='running'")==1);CHECK(scalar(first.raw_handle(),"SELECT requests FROM site_request_state")==1);
  }
  Json redirected(Reader& reader,const Json& params,const std::string& target,const std::string& observed,bool index=false,bool snippet=false) {
    auto page=reader.ok(params);page.set("final_url",target);auto chain=Json::array();
    if(observed!=params.at("url").str())chain.push(Json::string(observed));
    chain.push(Json::string(target));page.set("redirects",chain);
    page.at("usage").set("external_requests",static_cast<std::int64_t>(chain.size()+1));
    auto policy=Json::parse(page.at("publication").dump());policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippet);policy.set("snippet_text","");policy.set("snippet_title","");policy.set("directives",Json::parse(index?R"(["nosnippet"])":R"(["noindex"])"));
    auto record=Json::object();record.set("url",observed);record.set("http_status",std::int64_t{302});record.set("publication",policy);auto observations=Json::array();observations.push(record);page.set("redirect_publication_observations",observations);return page;
  }
  TEST(explicit_redirect_rules_are_versioned_for_logical_url_without_restricting_final_target) {
    for(bool batch:{false,true})for(bool index:{false,true}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));const auto a=std::string(base)+"/map/a",b=std::string(base)+"/map/b";
      representation(s,b,b,"marker independent final target");reader.response=[&](const Json& p) { auto page=reader.ok(p);page.set("final_url",b);page.set("redirects",Json::parse("[\""+b+"\"]"));return page; };
      auto p=Json::object();p.set("url",a);p.set("freshness","refresh");auto old=web.read("alice",p);auto frozen=freeze(s);
      reader.response=[&](const Json& params) { return redirected(reader,params,b,a,index,false); };
      std::string snapshot;
      if(batch) { auto job=s.read_many_submit("alice",{a},"redirect",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);auto result=s.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(result.at("state").str()=="succeeded");CHECK(result.at("external_requests").num()==2);CHECK(result.at("receipt").at("indexed").flag()==index);snapshot=result.at("receipt").at("snapshot_id").str(); }
      else { auto result=web.read("alice",p);CHECK(result.at("indexed").flag()==index);snapshot=result.at("snapshot_id").str(); }
      CHECK(snapshot!=old.at("snapshot_id").str());auto stored=s.snapshot("alice",snapshot);CHECK(stored.at("publication").at("index_allowed").flag()==index);CHECK(!stored.at("publication").at("snippet_allowed").flag());CHECK(stored.at("representation_publication").at("index_allowed").flag());CHECK(stored.at("representation_publication").at("snippet_allowed").flag());
      // The same bytes observed after a restriction have a new immutable
      // dispatch fence. Reusing and mutating the older fence would also
      // requalify frozen sessions bound to that older observation.
      auto refreshed=web.read("alice",p);CHECK(refreshed.at("snapshot_id").str()!=snapshot);
      CHECK(s.snapshot("alice",snapshot).at("meta").canonical()==stored.at("meta").canonical());
      for(const auto& session:{frozen,freeze(s)}) { auto result=hits(s,session);bool found_a=false,found_b=false;for(std::size_t i=0;i<result.size();++i) { auto hit=result.at(i);if(hit.at("url").str()==a) { found_a=true;CHECK(hit.at("snippet").str().empty()); }if(hit.at("url").str()==b) { found_b=true;CHECK(!hit.at("snippet").str().empty()); } }CHECK(found_a==index);CHECK(found_b); }
      CHECK(s.snapshot("alice",old.at("snapshot_id").str()).at("canonical_text").str().find("marker")!=std::string::npos);
    }
  }
  TEST(redirect_restriction_on_failed_or_quota_limited_read_does_not_relabel_target) {
    for(bool batch:{false,true})for(bool quota:{false,true}) {
      Temp t;Reader reader;StoreOptions options;if(quota)options.max_snapshots_per_owner=2;Store s(t.path/"db",[]{return std::int64_t{1700000000000};},options);WebService web(s,reader,{},SiteRegistry(manifest()));const auto a=std::string(base)+"/map/a",b=std::string(base)+"/map/b";
      representation(s,a,b,"marker former redirect");representation(s,b,b,"marker independent target");auto frozen=freeze(s);
      reader.response=[&](const Json& params) { auto page=redirected(reader,params,b,a);if(!quota) { page.set("status","timeout");page.set("error","timeout after redirect");page.set("http_status",Json()); }return page; };
      if(batch) { auto job=s.read_many_submit("alice",{a},"failed-redirect",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);auto item=s.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(item.at("state").str()=="failed");CHECK(item.at("error_code").str()==(quota?"E_QUOTA":"E_FETCH_TIMEOUT"));CHECK(item.at("external_requests").num()==2); }
      else { auto p=Json::object();p.set("url",a);p.set("freshness","refresh");rejects([&]{web.read("alice",p);},quota?"E_QUOTA":"E_FETCH_TIMEOUT"); }
      CHECK(s.stats("alice").at("snapshots").num()==2);auto result=hits(s,frozen);CHECK(result.size()==1);CHECK(result.at(0).at("url").str()==b);CHECK(!result.at(0).at("snippet").str().empty());CHECK(hits(s,freeze(s)).size()==1);
    }
  }
  TEST(intermediate_redirect_observations_are_bound_and_late_batch_results_are_fenced) {
    for(const std::string mode:{"live","pause","delete","unbound"}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));const auto a=std::string(base)+"/map/a",b=std::string(base)+"/map/b",x=std::string(base)+"/map/x";
      auto initial=representation(s,a,b,"marker former A");representation(s,b,b,"marker independent B");representation(s,x,x,"marker intermediate X");auto frozen=freeze(s);auto job=s.read_many_submit("alice",{a},"intermediate",{});auto lease=s.lease_next_read();CHECK(lease);
      reader.response=[&](const Json& params) { auto page=redirected(reader,params,b,x);page.set("status","timeout");page.set("http_status",Json());if(mode=="pause")s.job_control("alice",job.at("job_id").str(),"pause");if(mode=="delete")s.erase("alice",initial.at("document_id").str());if(mode=="unbound")page.at("redirect_publication_observations").at(0).set("url",std::string(base)+"/map/not-in-chain");return page; };
      web.execute_read(*lease);auto result=hits(s,frozen);CHECK(result.size()==(mode=="live"?1:mode=="delete"?2:3));bool seen_x=false;for(std::size_t i=0;i<result.size();++i)if(result.at(i).at("url").str()==x)seen_x=true;CHECK(seen_x==(mode!="live"));CHECK(s.job_get("alice",job.at("job_id").str()).at("usage").at("external_requests").num()==3);
    }
  }
  TEST(truncated_read_cannot_supply_an_allowing_versioned_publication) {
    for(bool batch:{false,true}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));reader.response=[&](const Json& p) { auto page=reader.ok(p);page.set("warnings",Json::parse(R"(["download_truncated"])"));return page; };
      if(batch) { auto job=s.read_many_submit("alice",{std::string(base)+"/map/a"},"truncated",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);auto item=s.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(item.at("error_code").str()=="E_WORKER_PROTOCOL");CHECK(item.at("external_requests").num()==1); }
      else { auto p=Json::object();p.set("url",std::string(base)+"/map/a");rejects([&]{web.read("alice",p);},"E_WORKER_PROTOCOL"); }CHECK(s.stats("alice").at("snapshots").num()==0);
    }
  }

  TEST(refused_redirect_tail_keeps_only_actual_public_response_policy) {
    for(bool batch:{false,true})for(bool future_observation:{false,true}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));const auto a=std::string(base)+"/map/a",b=std::string(base)+"/map/b";representation(s,a,a,"marker A old");representation(s,b,b,"marker B independent");auto frozen=freeze(s);
      reader.response=[&](const Json& p) { auto page=redirected(reader,p,b,a);page.set("status","blocked_by_policy");page.set("http_status",std::int64_t{302});page.set("final_url",a);page.set("redirects",Json::parse(future_observation?("[\""+b+"\"]"):R"(["http://169.254.169.254/latest/meta-data"])"));page.at("usage").set("external_requests",std::int64_t{1});if(future_observation)page.at("redirect_publication_observations").at(0).set("url",b);return page; };
      const auto error=future_observation?"E_WORKER_PROTOCOL":"E_URL_BLOCKED";
      if(batch) { auto job=s.read_many_submit("alice",{a},"denied-tail",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);auto item=s.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(item.at("error_code").str()==error);CHECK(item.at("external_requests").num()==1); }
      else { auto p=Json::object();p.set("url",a);p.set("freshness","refresh");rejects([&]{web.read("alice",p);},error); }
      auto result=hits(s,frozen);CHECK(result.size()==(future_observation?2:1));if(!future_observation)CHECK(result.at(0).at("url").str()==b);CHECK(s.stats("alice").at("snapshots").num()==2);
    }
  }
  TEST(ordinary_read_requoting_and_escaped_paths_keep_actual_hop_restrictions) {
    for(bool batch:{false,true})for(const auto& paths:std::vector<std::pair<std::string,std::string>>{{"/map/redirect-%72estricted","/map/redirect-restricted"},{"/map/escaped%2fpath","/map/escaped%2Fpath"},{"/map/part/%2e%2e/target","/map/target"}}) {
      Temp t;Reader reader;Store s(t.path/"db");WebService web(s,reader,{},SiteRegistry(manifest()));const auto a=std::string(base)+paths.first,actual=std::string(base)+paths.second,b=std::string(base)+"/map/b";s.ingest("alice",{a,"marker original A","text/plain","A"});s.ingest("alice",{actual,"marker actual A","text/plain","actual"});representation(s,b,b,"marker B independent");auto frozen=freeze(s);
      reader.response=[&](const Json& p) { auto page=redirected(reader,p,b,actual);page.set("status","blocked_by_policy");page.set("http_status",std::int64_t{302});page.set("final_url",actual);page.set("redirects",Json::parse(R"(["http://169.254.169.254/blocked"])"));page.at("usage").set("external_requests",std::int64_t{1});return page; };
      if(batch) { auto job=s.read_many_submit("alice",{a},"requote",{});auto lease=s.lease_next_read();CHECK(lease);web.execute_read(*lease);auto item=s.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(item.at("error_code").str()=="E_URL_BLOCKED");CHECK(item.at("external_requests").num()==1); }
      else { auto p=Json::object();p.set("url",a);p.set("freshness","refresh");rejects([&]{web.read("alice",p);},"E_URL_BLOCKED"); }
      auto result=hits(s,frozen);CHECK(result.size()==1);CHECK(result.at(0).at("url").str()==b);CHECK(hits(s,freeze(s)).size()==1);
    }
  }
  TEST(crawl_rejected_redirect_target_still_applies_its_admitted_response_header) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));crawl_one(w,s,"first");auto frozen=freeze(s);
    f.response=[&](const Json& p) { auto r=f.ok(p);r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url","http://169.254.169.254/blocked");publication(r,false,false,true,"","",{"noindex"});r.set("publication_observation",r.at("publication"));return r; };
    auto failed=crawl_one(w,s,"denied-redirect");CHECK(failed.at("state").str()=="failed");CHECK(failed.at("error_code").str()=="E_URL_BLOCKED");CHECK(hits(s,frozen).size()==0);CHECK(hits(s,freeze(s)).size()==0);CHECK(scalar(s.raw_handle(),"SELECT SUM(external_requests) FROM map_hops")==3);CHECK(s.stats("alice").at("snapshots").num()==1);
  }

  Json stopped_edges(Store& store,const std::string& job,const std::string& reason) {
    auto result=Json::array(),edges=store.crawl_edges("alice",job,0,1000).at("items");
    for(std::size_t i=0;i<edges.size();++i)if(!edges.at(i).at("reason").is_null()&&edges.at(i).at("reason").str()==reason)result.push(edges.at(i));
    return result;
  }
  void readable_body(Json& response,const std::string& text) {
    response.at("page").set("text",text);response.at("page").set("html","<p>"+text+"</p>");publication(response,true,true,true,text);
  }
  void execute_pages(WebService& web,int count) {
    for(int i=0;i<count;++i) { auto lease=web.lease_map();CHECK(lease);web.execute_map(*lease); }
  }
  std::string calendar_day(int day) { return std::string(base)+"/map/calendar/2024/03/"+(day<10?"0":"")+std::to_string(day); }
  std::string pagination_page(int page) { return std::string(base)+"/map/catalog/page/"+std::to_string(page); }
  const std::string repeated_body="marker shared full canonical pagination body. "+std::string(300,'x');

  TEST(trap_policy_is_versioned_separately_from_map_and_pins_finite_limits) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    auto crawl=w.submit_crawl("alice",args()),mapped=w.submit_map("alice",args({std::string(base)+"/map/meta"},"metadata"));
    CHECK(!mapped.has("trap_policy"));CHECK(crawl.at("trap_policy").at("version").str()==crawl_trap_version);
    auto policy=crawl.at("trap_policy");CHECK(policy.at("scope").str()=="job_lifetime");CHECK(policy.at("url_observations").num()==1);
    CHECK(policy.at("classification").str()=="finite_heuristics");CHECK(policy.at("limits").at("max_distinct_dates").num()==16);
    CHECK(policy.at("limits").at("max_consecutive_date_steps").num()==6);CHECK(policy.at("limits").at("repeated_body_urls").num()==3);
    CHECK(w.submit_crawl("alice",args()).at("reused").flag());CHECK(f.calls.empty());
  }
  TEST(consecutive_calendar_days_include_leap_day_and_survive_pause_restart) {
    Temp t;Fake f;std::string id;std::int64_t before=0;
    const std::vector<std::string> days={"2024-02-26","2024-02-27","2024-02-28","2024-02-29","2024-03-01","2024-03-02","2024-03-03","2024-03-04"};
    const auto url=[&](std::size_t i){return std::string(base)+"/map/calendar/"+days.at(i);};
    f.response=[&](const Json& p) { auto r=f.ok(p);for(std::size_t i=0;i+1<days.size();++i)if(p.at("url").str()==url(i)) { auto links=Json::array();links.push(candidate(p,url(i+1)));r.at("discovery").set("candidates",links); }return r; };
    auto parameters=args({url(0)});parameters.set("max_depth",std::int64_t{8});
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_crawl("alice",parameters).at("job_id").str();execute_pages(w,4);
      before=s.job_get("alice",id).at("trap_policy").at("url_observations").num();CHECK(before==4);CHECK(s.job_control("alice",id,"pause").at("state").str()=="paused"); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));CHECK(s.job_get("alice",id).at("trap_policy").at("url_observations").num()==before);
      CHECK(w.submit_crawl("alice",parameters).at("job_id").str()==id);CHECK(f.calls.size()==4);s.job_control("alice",id,"resume");drain(w);
      auto job=s.job_get("alice",id);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("partial").flag());CHECK(job.at("stop_reason").str()=="E_CRAWL_TRAP_DATE_SEQUENCE");
      CHECK(f.calls.size()==8);CHECK(job.at("used_pages").num()==7);CHECK(s.stats("alice").at("snapshots").num()==7);
      auto denied=stopped_edges(s,id,"E_CRAWL_TRAP_DATE_SEQUENCE");CHECK(denied.size()==1);CHECK(denied.at(0).at("url").str()==url(7));CHECK(!denied.at(0).at("followed").flag());
      CHECK(denied.at(0).at("trap").at("version").str()==crawl_trap_version);CHECK(denied.at(0).at("trap").at("observed").num()==7);CHECK(denied.at(0).at("trap").at("limit").num()==6); }
  }
  TEST(calendar_fanout_is_bounded_across_different_parents_and_keeps_other_documents) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);auto links=Json::array();if(p.at("url").str().ends_with("index")) {
      for(int i=1;i<=20;++i)links.push(candidate(p,calendar_day(i)));
      links.push(candidate(p,std::string(base)+"/map/useful"));
    } else if(p.at("url").str().ends_with("useful"))links.push(candidate(p,calendar_day(17)));
      if(p.at("kind").str()!="robots")r.at("discovery").set("candidates",links);
      return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
    CHECK(job.at("stop_reason").str()=="E_CRAWL_TRAP_DATE_FAMILY");CHECK(job.at("used_pages").num()==18);CHECK(f.calls.size()==19);
    const auto stopped=stopped_edges(s,id,"E_CRAWL_TRAP_DATE_FAMILY");CHECK(stopped.size()==5);
    for(std::size_t i=0;i<stopped.size();++i) { CHECK(stopped.at(i).at("trap").at("limit").num()==16);CHECK(!stopped.at(i).at("followed").flag()); }
    CHECK(s.stats("alice").at("snapshots").num()==18);CHECK(s.crawl_edges("alice",id,0,100).at("total").num()==22);
  }
  TEST(finite_dates_unique_article_slugs_versions_and_invalid_dates_are_not_calendar_stops) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));std::vector<std::string> urls;
    for(int i=1;i<=20;++i)urls.push_back(calendar_day(i)+"/article-"+std::to_string(i));
    for(int i=1;i<=8;++i)urls.push_back(std::string(base)+"/map/archive/2024-0"+std::to_string(i));
    for(const auto* path:{"/map/calendar/2023-02-29","/map/calendar/2024/02/30","/map/calendar/2024-13-01","/map/docs/2024/03","/map/docs/v1.2.3","/map/docs/v1.2.4","/map/articles/20240101"})urls.push_back(std::string(base)+path);
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(const auto& url:urls)links.push(candidate(p,url));r.at("discovery").set("candidates",links); }return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
    CHECK(!job.at("partial").flag());CHECK(job.at("used_pages").num()==static_cast<std::int64_t>(urls.size()+1));CHECK(f.calls.size()==urls.size()+2);
    CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM crawl_trap_urls WHERE date_family!=''")==28);
    CHECK(job.at("trap_policy").at("date_families").num()==21);CHECK(job.at("trap_policy").at("stops").size()==0);
  }
  TEST(calendar_month_and_backward_navigation_have_their_own_sequence_bound) {
    for(const std::string mode:{"month","backward"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      const auto url=[&](int i){return mode=="month"?std::string(base)+"/map/archive/2024-0"+std::to_string(i):calendar_day(i);};
      f.response=[&](const Json& p) { auto r=f.ok(p);for(int i=9;i>1;--i)if(p.at("url").str()==url(i)) { auto links=Json::array();links.push(candidate(p,url(i-1)));r.at("discovery").set("candidates",links); }return r; };
      auto parameters=args({url(9)});parameters.set("max_depth",std::int64_t{8});const auto id=w.submit_crawl("alice",parameters).at("job_id").str();drain(w);
      CHECK(s.job_get("alice",id).at("used_pages").num()==7);CHECK(stopped_edges(s,id,"E_CRAWL_TRAP_DATE_SEQUENCE").size()==1);CHECK(f.calls.size()==8);
    }
  }
  TEST(query_permutations_decode_only_for_analysis_and_keep_repeated_value_order) {
    for(bool repeated_values:{false,true}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      const std::vector<std::string> queries=repeated_values?std::vector<std::string>{"tag=a&tag=b&mode=all","mode=all&tag=a&tag=b","tag=b&mode=all&tag=a","mode=all&tag=b&tag=a","%74ag=a&mode=all&tag=b"}:
        std::vector<std::string>{"color=red&size=m&sort=asc","size=m&sort=asc&color=red","%63olor=red&sort=asc&size=m"};
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(const auto& q:queries)links.push(candidate(p,std::string(base)+"/map/filter?"+q));r.at("discovery").set("candidates",links); }return r; };
      const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==queries.size()+1);
      auto blocked=stopped_edges(s,id,"E_CRAWL_TRAP_QUERY_PERMUTATIONS");CHECK(blocked.size()==1);CHECK(blocked.at(0).at("url").str()==std::string(base)+"/map/filter?"+queries.back());
      CHECK(blocked.at(0).at("trap").at("observed").num()==3);CHECK(s.job_get("alice",id).at("stop_reason").str()=="E_CRAWL_TRAP_QUERY_PERMUTATIONS");
      for(std::size_t i=0;i+1<queries.size();++i)CHECK(f.calls.at(i+2).at("url").str()==std::string(base)+"/map/filter?"+queries.at(i));
    }
  }
  TEST(parameter_combinations_stop_across_pagination_paths_before_generic_limits) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(int i=1;i<=10;++i)links.push(candidate(p,pagination_page(i)+"?color=c"+std::to_string(i)+"&size=m"));r.at("discovery").set("candidates",links); }return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
    CHECK(job.at("stop_reason").str()=="E_CRAWL_TRAP_QUERY_COMBINATIONS");CHECK(job.at("used_pages").num()==9);CHECK(f.calls.size()==10);
    auto blocked=stopped_edges(s,id,"E_CRAWL_TRAP_QUERY_COMBINATIONS");CHECK(blocked.size()==2);CHECK(blocked.at(0).at("trap").at("observed").num()==9);
    CHECK(job.at("trap_policy").at("query_families").num()==1);CHECK(scalar(s.raw_handle(),"SELECT COUNT(DISTINCT query_signature) FROM crawl_trap_urls WHERE query_signature!=''")==8);
  }
  TEST(query_dimension_bounds_count_distinct_keys_and_all_repeated_pairs) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    std::string keys=std::string(base)+"/map/keys?",pairs=std::string(base)+"/map/pairs?";
    for(int i=0;i<9;++i) { if(i)keys+='&';keys+="k"+std::to_string(i)+"=x"; }
    for(int i=0;i<17;++i) { if(i)pairs+='&';pairs+="tag="+std::to_string(i); }
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();links.push(candidate(p,keys));links.push(candidate(p,pairs));r.at("discovery").set("candidates",links); }return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto blocked=stopped_edges(s,id,"E_CRAWL_TRAP_QUERY_DIMENSIONS");
    CHECK(blocked.size()==2);CHECK(blocked.at(0).at("trap").at("limit").num()==8);CHECK(blocked.at(1).at("trap").at("limit").num()==16);CHECK(f.calls.size()==2);
    const auto seed_id=w.submit_crawl("alice",args({keys,pairs},"explicit-complex")).at("job_id").str();drain(w);CHECK(s.job_get("alice",seed_id).at("used_pages").num()==2);CHECK(!s.job_get("alice",seed_id).at("partial").flag());CHECK(f.calls.at(2).at("url").str()==keys);CHECK(f.calls.at(3).at("url").str()==pairs);
  }
  TEST(changing_pagination_urls_with_identical_bodies_close_only_their_family) {
    for(const std::string mode:{"path","query"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      const auto url=[&](int i){return mode=="path"?pagination_page(i):std::string(base)+"/map/catalog?sort=up&%70age="+std::to_string(i);};
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;auto links=Json::array();
        if(p.at("url").str().ends_with("index"))links.push(candidate(p,url(1)));
        for(int i=1;i<=4;++i)if(p.at("url").str()==url(i)) { readable_body(r,repeated_body);links.push(candidate(p,url(i+1)));if(i==3)links.push(candidate(p,std::string(base)+"/map/useful")); }
        r.at("discovery").set("candidates",links);return r; };
      auto parameters=args();parameters.set("max_depth",std::int64_t{8});const auto id=w.submit_crawl("alice",parameters).at("job_id").str();drain(w);
      auto job=s.job_get("alice",id);CHECK(job.at("stop_reason").str()=="E_CRAWL_TRAP_DUPLICATE_PAGINATION");CHECK(job.at("used_pages").num()==5);CHECK(f.calls.size()==6);
      CHECK(job.at("trap_policy").at("body_observations").num()==3);CHECK(job.at("trap_policy").at("limited_pagination_families").num()==1);
      const auto blocked=stopped_edges(s,id,"E_CRAWL_TRAP_DUPLICATE_PAGINATION");CHECK(blocked.size()==1);CHECK(blocked.at(0).at("url").str()==url(4));
      const auto pages=s.job_results("alice",id,0,20).at("items");bool useful=false,third=false;
      for(std::size_t i=0;i<pages.size();++i) { const auto item=pages.at(i);if(item.at("url").str().ends_with("useful"))useful=item.at("body_read").flag();
        if(item.at("url").str()==url(3)) { third=true;CHECK(item.at("body_read").flag());CHECK(item.at("trap").at("expansion_limited").flag());CHECK(s.snapshot("alice",item.at("snapshot_id").str()).at("canonical_text").str()==repeated_body); } }
      CHECK(useful&&third);
    }
  }
  TEST(queued_repeated_pages_keep_all_parent_reasons_after_restart_and_other_job_runs) {
    Temp t;Fake f;std::string id,other;
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
      if(p.at("url").str().ends_with("root-a")||p.at("url").str().ends_with("root-b")) { auto links=Json::array();for(int i=1;i<=5;++i)links.push(candidate(p,pagination_page(i)));r.at("discovery").set("candidates",links); }
      else if(p.at("url").str().find("/catalog/page/")!=std::string::npos)readable_body(r,repeated_body);
      return r; };
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_crawl("alice",args({std::string(base)+"/map/root-a",std::string(base)+"/map/root-b"})).at("job_id").str();execute_pages(w,3);
      ++f.now;other=w.submit_crawl("alice",args({std::string(base)+"/map/useful"},"other-job")).at("job_id").str();auto lease=w.lease_map();CHECK(lease);CHECK(lease->job_id==other);w.execute_map(*lease);execute_pages(w,3);
      CHECK(s.job_get("alice",id).at("used_pages").num()==5);CHECK(s.job_get("alice",id).at("pending_documents").num()==2);CHECK(s.job_get("alice",id).at("trap_policy").at("limited_pagination_families").num()==1);s.job_control("alice",id,"pause"); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));s.job_control("alice",id,"resume");drain(w);
      const auto job=s.job_get("alice",id);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("skipped_pages").num()==2);CHECK(job.at("used_pages").num()==5);CHECK(f.calls.size()==7);
      CHECK(job.at("usage").at("external_requests").num()==6);CHECK(job.at("usage").at("reserved_requests").num()==0);CHECK(s.job_get("alice",other).at("state").str()=="succeeded");
      const auto blocked=stopped_edges(s,id,"E_CRAWL_TRAP_DUPLICATE_PAGINATION");CHECK(blocked.size()==4);for(std::size_t i=0;i<blocked.size();++i) { CHECK(!blocked.at(i).at("followed").flag());CHECK(blocked.at(i).at("trap").at("observed").num()==3); }
      auto rows=s.job_results("alice",id,0,20).at("items");std::size_t skipped=0;for(std::size_t i=0;i<rows.size();++i)if(rows.at(i).at("state").str()=="skipped") { ++skipped;CHECK(rows.at(i).at("trap").at("reason").str()=="E_CRAWL_TRAP_DUPLICATE_PAGINATION");CHECK(!rows.at(i).at("body_read").flag());CHECK(rows.at(i).at("snapshot_id").is_null()); }CHECK(skipped==2); }
  }
  TEST(explicit_pagination_seeds_are_read_after_family_limit_without_false_partial_coverage) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));std::vector<std::string> urls;for(int i=1;i<=5;++i)urls.push_back(pagination_page(i));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="page")readable_body(r,repeated_body);return r; };
    const auto id=w.submit_crawl("alice",args(urls)).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
    CHECK(!job.at("partial").flag());CHECK(job.at("used_pages").num()==5);CHECK(job.at("trap_policy").at("body_observations").num()==5);CHECK(job.at("trap_policy").at("limited_pagination_families").num()==1);CHECK(f.calls.size()==6);
  }
  TEST(duplicate_nonpagination_bodies_and_distinct_pagination_bodies_remain_readable) {
    for(const std::string mode:{"ordinary","unique"}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("kind").str()=="robots")return r;
        if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(int i=1;i<=6;++i)links.push(candidate(p,mode=="ordinary"?std::string(base)+"/map/document/"+std::to_string(i):pagination_page(i)));r.at("discovery").set("candidates",links); }
        else readable_body(r,repeated_body+(mode=="unique"?p.at("url").str():""));
        return r; };
      const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);auto job=s.job_get("alice",id);
      CHECK(!job.at("partial").flag());CHECK(job.at("used_pages").num()==7);CHECK(f.calls.size()==8);CHECK(job.at("trap_policy").at("limited_pagination_families").num()==0);
    }
  }
  TEST(discovered_redirect_target_is_checked_before_the_next_physical_request) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(int i=1;i<=16;++i)links.push(candidate(p,calendar_day(i)));links.push(candidate(p,std::string(base)+"/map/redirector"));r.at("discovery").set("candidates",links); }
      else if(p.at("url").str().ends_with("redirector")) { r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",calendar_day(17)); }
      return r; };
    const auto id=w.submit_crawl("alice",args()).at("job_id").str();drain(w);CHECK(f.calls.size()==19);for(const auto& call:f.calls)CHECK(call.at("url").str()!=calendar_day(17));
    auto denied=stopped_edges(s,id,"E_CRAWL_TRAP_DATE_FAMILY");CHECK(denied.size()==2);bool redirect=false;for(std::size_t i=0;i<denied.size();++i)if(denied.at(i).at("source").str()=="redirect")redirect=true;CHECK(redirect);
    CHECK(s.job_get("alice",id).at("used_pages").num()==18);CHECK(s.job_get("alice",id).at("usage").at("external_requests").num()==19);CHECK(s.stats("alice").at("snapshots").num()==17);
  }
  TEST(schema_six_live_job_bootstraps_body_traps_and_preserves_idempotency) {
    Temp t;Fake f;std::string id;auto parameters=args();
    f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str().ends_with("index")) { auto links=Json::array();for(int i=1;i<=5;++i)links.push(candidate(p,pagination_page(i)));r.at("discovery").set("candidates",links); }
      else if(p.at("kind").str()=="page")readable_body(r,repeated_body);
      return r; };
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));id=w.submit_crawl("alice",parameters).at("job_id").str();execute_pages(w,4);CHECK(s.job_get("alice",id).at("used_pages").num()==3);drop_schema_seven(s.raw_handle()); }
    { Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));CHECK(scalar(s.raw_handle(),"PRAGMA user_version")==8);
      const auto reused=w.submit_crawl("alice",parameters);CHECK(reused.at("reused").flag());CHECK(reused.at("job_id").str()==id);CHECK(f.calls.size()==4);CHECK(reused.at("trap_policy").is_null());drain(w);
      auto job=s.job_get("alice",id);CHECK(job.at("trap_policy").at("version").str()==crawl_trap_version);CHECK(job.at("trap_policy").at("body_observations").num()==3);CHECK(job.at("trap_policy").at("url_observations").num()==6);
      CHECK(job.at("used_pages").num()==4);CHECK(job.at("skipped_pages").num()==2);CHECK(f.calls.size()==5);CHECK(stopped_edges(s,id,"E_CRAWL_TRAP_DUPLICATE_PAGINATION").size()==2);CHECK(s.stats("alice").at("snapshots").num()==4); }
  }
  TEST(seed_redirect_is_automatic_navigation_unless_target_is_itself_an_explicit_seed) {
    for(bool target_is_seed:{false,true}) {
      Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
      const auto start=std::string(base)+"/map/start",target=std::string(base)+"/map/target?a=1&b=1&c=1&d=1&e=1&f=1&g=1&h=1&i=1";
      f.response=[&](const Json& p) { auto r=f.ok(p);if(p.at("url").str()==start) { r.set("status","redirect");r.set("http_status",std::int64_t{302});r.set("redirect_url",target); }return r; };
      const auto id=w.submit_crawl("alice",args(target_is_seed?std::vector<std::string>{start,target}:std::vector<std::string>{start})).at("job_id").str();drain(w);
      CHECK(f.calls.size()==(target_is_seed?4U:2U));const auto denied=stopped_edges(s,id,"E_CRAWL_TRAP_QUERY_DIMENSIONS");CHECK(denied.size()==(target_is_seed?0U:1U));
      if(!target_is_seed) { CHECK(denied.at(0).at("source").str()=="redirect");CHECK(s.job_get("alice",id).at("used_pages").num()==1);CHECK(s.stats("alice").at("snapshots").num()==0); }
      CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM crawl_trap_urls WHERE explicit_seed=1")== (target_is_seed?2:1));
    }
  }
  TEST(oversized_legacy_observations_settle_once_without_starving_another_job) {
    Temp t;Fake f;Store s(t.path/"db",[&]{return f.now;});WebService w(s,f,{},SiteRegistry(manifest()));
    const auto old=w.submit_crawl("alice",args()).at("job_id").str();
    const auto fill="WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<2000) INSERT INTO map_resources(job_id,position,url,fetch_url,kind,depth,parent,source,deletion_generation) SELECT '"+old+"',x+1,'https://map.example/map/legacy/'||x,'https://map.example/map/legacy/'||x,'page',1,'https://map.example/map/index','html',0 FROM n;";
    sql(s.raw_handle(),fill.c_str());sql(s.raw_handle(),"DELETE FROM crawl_trap_policy;");
    ++f.now;const auto other=w.submit_crawl("alice",args({std::string(base)+"/map/other"},"another-job")).at("job_id").str();
    auto lease=w.lease_map();CHECK(lease);CHECK(lease->job_id==other);w.execute_map(*lease);drain(w);
    auto job=s.job_get("alice",old);CHECK(job.at("partial").flag());CHECK(job.at("stop_reason").str()=="E_CRAWL_TRAP_STATE_LIMIT");CHECK(job.at("pending_documents").num()==0);
    CHECK(job.at("used_pages").num()==0);CHECK(job.at("usage").at("external_requests").num()==0);CHECK(job.at("trap_policy").at("url_observations").num()==2000);
    CHECK(s.job_get("alice",other).at("state").str()=="succeeded");CHECK(f.calls.size()==2);CHECK(!w.lease_map());
  }

}
int main() {
  std::size_t failures=0;for(const auto& [name,test]:tests()) { try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; } }
  std::cout<<tests().size()<<" crawl tests, "<<assertions<<" assertions, "<<failures<<" failures\n";return failures?1:0;
}
