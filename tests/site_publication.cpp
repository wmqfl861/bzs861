#include "bzs/error.hpp"
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <algorithm>
#include <sqlite3.h>

namespace {
using namespace bzs;
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> value;return value; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add registered_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); } while(false)
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-site-publication-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
};
struct NoHttp:WorkerPort {
  std::size_t requests=0;
  Json call(const std::string&,const Json&,int)override { ++requests;throw std::runtime_error("This scenario must not make a worker request"); }
};
template<class F> void rejects(F fn,const std::string& code) {
  ++assertions;try { fn(); }catch(const Error& error) { if(error.code==code)return;throw std::runtime_error("expected "+code+", got "+error.code); }
  throw std::runtime_error("expected "+code);
}
void sql(sqlite3* db,const char* query) { CHECK(sqlite3_exec(db,query,nullptr,nullptr,nullptr)==SQLITE_OK); }
std::int64_t scalar(sqlite3* db,const char* query) {
  sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query,-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);CHECK(sqlite3_step(raw)==SQLITE_ROW);return sqlite3_column_int64(raw,0);
}
Json manifest() {
  return Json::parse(R"({"version":1,"sites":[{"id":"policy-site","enabled":true,"origin":"https://policy.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"2020-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://policy.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
Json configured(const std::string& mode) {
  auto config=manifest();auto site=config.at("sites").at(0),review=site.at("review");
  if(mode=="links_only")review.set("redistribution_policy","links_only");
  else if(mode=="rejected")review.set("status","rejected");
  else if(mode=="disabled")site.set("enabled",false);
  else if(mode=="removed")config.set("sites",Json::array());
  else if(mode=="expired")review.set("expires_at","2021-01-01T00:00:00Z");
  return config;
}
Json read_args(const std::string& url) { auto args=Json::object();args.set("url",url);args.set("freshness","refresh");return args; }
struct ReadWorker:WorkerPort {
  std::size_t requests=0;std::function<void()> during;
  Json call(const std::string& method,const Json& args,int) override {
    CHECK(method=="fetch.extract");++requests;if(during)during();
    auto result=Json::parse(R"({"status":"ok","html":"marker canonical secret","text":"marker canonical secret","content_type":"text/plain","title":"marker private title","extractor":"site-read/1","extract_status":"ok","final_url":"","http_status":200,"index_allowed":true,"links":[],"metadata":{},"warnings":[],"redirects":[],"usage":{"external_requests":1,"content_bytes":23},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker canonical secret","snippet_title":"marker private title","directives":[],"diagnostics":[]}})");
    result.set("final_url",args.at("url"));return result;
  }
};
Json artifact(Store& store,const std::string& owner,const std::string& url,const std::string& final_url="",Json redirects=Json::array()) {
  ParsedDocument parsed;parsed.title="marker private title";parsed.text="marker canonical secret";parsed.content_type="text/plain";parsed.parser_version="site-publication-fixture/1";
  parsed.publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker canonical secret","snippet_title":"marker private title","directives":[],"diagnostics":[]})");
  auto meta=Json::object();meta.set("final_url",final_url.empty()?url:final_url);meta.set("redirects",redirects);
  return store.ingest_web(owner,{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation(owner,url));
}
Json frozen(Store& store,const std::string& owner="alice") { return store.search(owner,parse_query(Json::string("marker"))); }
Json hits(Store& store,const Json& session,const std::string& owner="alice") { return store.page(owner,session.at("session_id").str(),"",100).at("hits"); }
void restarted_tightening(const std::string& mode) {
  Temp temp;NoHttp worker;std::int64_t now=1800000000000;Json old,snapshot;const auto url=std::string("https://policy.example/docs/a");
  {
    Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(manifest()));snapshot=artifact(store,"alice",url);old=frozen(store);CHECK(!hits(store,old).at(0).at("snippet").str().empty());
  }
  auto config=manifest();auto review=config.at("sites").at(0).at("review");
  if(mode=="links_only")review.set("redistribution_policy","links_only");
  else if(mode=="expired")review.set("expires_at","2021-01-01T00:00:00Z");
  else review.set("status","rejected");
  Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(config));
  const auto current=hits(store,frozen(store)),previous=hits(store,old);
  CHECK(worker.requests==0);CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");
  if(mode=="links_only") {
    CHECK(current.size()==1);CHECK(previous.size()==1);CHECK(current.at(0).at("snippet").str().empty());CHECK(previous.at(0).at("snippet").str().empty());CHECK(current.at(0).at("title").str()==url);CHECK(previous.at(0).at("title").str()==url);
  } else { CHECK(current.size()==0);CHECK(previous.size()==0); }
}
TEST(restart_links_only_with_zero_http_restricts_stored_and_frozen_publication) { restarted_tightening("links_only"); }
TEST(restart_rejected_review_with_zero_http_withdraws_stored_and_frozen_publication) { restarted_tightening("rejected"); }
TEST(restart_expired_review_with_zero_http_withdraws_stored_and_frozen_publication) { restarted_tightening("expired"); }
TEST(disabled_removed_and_unconfigured_restart_keep_durable_restrictions) {
  for(const auto* mode:{"disabled","removed"}) {
    Temp temp;NoHttp worker;Json previous,snap;std::int64_t now=1800000000000;
    { Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(manifest()));snap=artifact(store,"alice","https://policy.example/docs/a");previous=frozen(store);store.activate_site_publication(SiteRegistry(configured(mode))); }
    Store offline(temp.path/"db",[&]{return now;});CHECK(hits(offline,previous).size()==0);CHECK(hits(offline,frozen(offline)).size()==0);CHECK(!offline.snapshot("alice",snap.at("snapshot_id").str()).at("indexed").flag());CHECK(worker.requests==0);
  }
}
TEST(review_expiry_is_durable_without_restart_or_http_and_cannot_be_rewound) {
  Temp temp;NoHttp worker;std::int64_t now=1700000000000;auto config=manifest();config.at("sites").at(0).at("review").set("expires_at","2023-11-14T22:13:21Z");
  Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(config));auto snapshot=artifact(store,"alice","https://policy.example/docs/a");auto previous=frozen(store);
  const auto before=scalar(store.raw_handle(),"SELECT generation FROM site_publication_state");now+=1001;
  CHECK(hits(store,previous).size()==0);CHECK(hits(store,frozen(store)).size()==0);CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM content_fts")==0);
  CHECK(scalar(store.raw_handle(),"SELECT generation FROM site_publication_state")==before+1);now-=1001;
  CHECK(hits(store,frozen(store)).size()==0);CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");CHECK(worker.requests==0);
}
TEST(relaxation_requires_a_new_complete_artifact_and_never_revives_frozen_content) {
  for(const auto* mode:{"links_only","rejected"}) {
    Temp temp;std::int64_t now=1800000000000;Store store(temp.path/"db",[&]{return now;});store.activate_site_publication(SiteRegistry(manifest()));
    const auto url=std::string("https://policy.example/docs/a"),old_snapshot=artifact(store,"alice",url).at("snapshot_id").str();const auto previous=frozen(store);
    store.activate_site_publication(SiteRegistry(configured(mode)));store.activate_site_publication(SiteRegistry(manifest()));
    if(std::string(mode)=="rejected")CHECK(hits(store,frozen(store)).size()==0);else CHECK(hits(store,frozen(store)).at(0).at("snippet").str().empty());
    const auto refreshed=artifact(store,"alice",url);CHECK(refreshed.at("snapshot_id").str()!=old_snapshot);CHECK(!hits(store,frozen(store)).at(0).at("snippet").str().empty());
    const auto old=hits(store,previous);if(std::string(mode)=="rejected")CHECK(old.size()==0);else { CHECK(old.size()==1);CHECK(old.at(0).at("snippet").str().empty());CHECK(old.at(0).at("title").str()==url); }
    CHECK(store.snapshot("alice",old_snapshot).at("canonical_text").str()=="marker canonical secret");
  }
}
TEST(scope_shrink_preserves_the_retained_child_and_expansion_does_not_restore_retired_content) {
  Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));
  artifact(store,"alice","https://policy.example/docs/keep/a");artifact(store,"alice","https://policy.example/docs/drop/a");const auto previous=frozen(store);
  auto narrowed=manifest();narrowed.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/docs/keep"] )"));store.activate_site_publication(SiteRegistry(narrowed));
  auto current=hits(store,frozen(store));CHECK(current.size()==1);CHECK(current.at(0).at("url").str().ends_with("/keep/a"));CHECK(!current.at(0).at("snippet").str().empty());
  store.activate_site_publication(SiteRegistry(manifest()));CHECK(hits(store,frozen(store)).size()==1);artifact(store,"alice","https://policy.example/docs/drop/a");CHECK(hits(store,frozen(store)).size()==2);
  const auto old=hits(store,previous);CHECK(old.size()==1);CHECK(old.at(0).at("url").str().ends_with("/keep/a"));
}
TEST(new_child_scopes_inherit_prior_ancestor_index_and_snippet_watermarks) {
  for(const auto* prefix:{"/docs","/"})for(const auto* mode:{"links_only","rejected"}) {
    Temp temp;Store store(temp.path/"db");auto initial=manifest();auto paths=Json::array();paths.push(Json::string(prefix));initial.at("sites").at(0).set("path_prefixes",paths);store.activate_site_publication(SiteRegistry(initial));
    const auto url=std::string("https://policy.example/docs/sub/a");artifact(store,"alice",url);const auto previous=frozen(store);
    auto denied=Json::parse(initial.dump());if(std::string(mode)=="links_only")denied.at("sites").at(0).at("review").set("redistribution_policy","links_only");else denied.at("sites").at(0).at("review").set("status","rejected");store.activate_site_publication(SiteRegistry(denied));
    auto child=manifest();child.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/docs/sub"])"));store.activate_site_publication(SiteRegistry(child));
    auto old=hits(store,previous);if(std::string(mode)=="rejected")CHECK(old.size()==0);else CHECK(old.at(0).at("snippet").str().empty());
    artifact(store,"alice",url);CHECK(!hits(store,frozen(store)).at(0).at("snippet").str().empty());old=hits(store,previous);if(std::string(mode)=="rejected")CHECK(old.size()==0);else CHECK(old.at(0).at("snippet").str().empty());
  }
}
TEST(intermediate_site_bindings_are_immutable_even_when_body_and_final_url_reuse) {
  for(const auto* mode:{"links_only","rejected"}) {
    Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));
    const auto a=std::string("https://logical.example/a"),b=std::string("https://representation.example/b");
    const auto first=artifact(store,"alice",a,b,Json::parse(R"(["https://policy.example/docs/middle","https://representation.example/b"])"));const auto previous=frozen(store);
    const auto second=artifact(store,"alice",a,b,Json::parse(R"(["https://independent.example/middle","https://representation.example/b"])"));CHECK(first.at("snapshot_id").str()!=second.at("snapshot_id").str());
    store.activate_site_publication(SiteRegistry(configured(mode)));auto current=hits(store,frozen(store));CHECK(current.size()==1);CHECK(!current.at(0).at("snippet").str().empty());
    const auto old=hits(store,previous);if(std::string(mode)=="rejected")CHECK(old.size()==0);else { CHECK(old.size()==1);CHECK(old.at(0).at("snippet").str().empty()); }
    CHECK(store.snapshot("alice",first.at("snapshot_id").str()).at("meta").at("redirects").at(0).str()=="https://policy.example/docs/middle");
  }
}
TEST(final_representation_retarget_does_not_release_frozen_site_constraints) {
  Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));const auto a=std::string("https://logical.example/a");
  artifact(store,"alice",a,"https://policy.example/docs/b");const auto previous=frozen(store);artifact(store,"alice",a,"https://independent.example/c");store.activate_site_publication(SiteRegistry(configured("rejected")));
  CHECK(hits(store,previous).size()==0);CHECK(hits(store,frozen(store)).size()==1);store.activate_site_publication(SiteRegistry(manifest()));CHECK(hits(store,previous).size()==0);
}
TEST(import_snapshots_and_frozen_imports_do_not_inherit_deployment_rules_from_later_web_reads) {
  Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));const auto url=std::string("https://policy.example/docs/a");
  const auto imported=store.ingest("alice",{url,"marker imported private","text/plain","marker import"});const auto private_session=frozen(store);artifact(store,"alice",url);const auto web_session=frozen(store);
  store.ingest("bob",{url,"marker bob private","text/plain","marker bob"});const auto bob_session=frozen(store,"bob");
  store.activate_site_publication(SiteRegistry(configured("rejected")));CHECK(hits(store,private_session).size()==1);CHECK(hits(store,web_session).size()==0);CHECK(hits(store,bob_session,"bob").size()==1);
  store.ingest("alice",{url,"marker newer private","text/plain","marker import"});CHECK(hits(store,frozen(store)).size()==1);CHECK(hits(store,web_session).size()==0);
  CHECK(store.snapshot("alice",imported.at("snapshot_id").str()).at("canonical_text").str()=="marker imported private");rejects([&]{store.snapshot("bob",imported.at("snapshot_id").str());},"E_NOT_FOUND");
}
TEST(stale_connections_cannot_reactivate_or_publish_using_an_older_allowing_service) {
  Temp temp;ReadWorker worker;Store older(temp.path/"db");WebService old_web(older,worker,{},SiteRegistry(manifest()));const auto url=std::string("https://policy.example/docs/a");old_web.read("alice",read_args(url));const auto previous=frozen(older);
  Store newer(temp.path/"db");newer.activate_site_publication(SiteRegistry(configured("rejected")));rejects([&]{older.activate_site_publication(SiteRegistry(manifest()));},"E_SITE_POLICY_STALE");
  CHECK(!old_web.read("alice",read_args(url)).at("indexed").flag());CHECK(hits(older,frozen(older)).size()==0);
  newer.activate_site_publication(SiteRegistry(manifest()));CHECK(!old_web.read("alice",read_args(url)).at("indexed").flag());CHECK(hits(older,previous).size()==0);
  WebService new_web(newer,worker,{},SiteRegistry(manifest()));CHECK(new_web.read("alice",read_args(url)).at("indexed").flag());CHECK(hits(newer,frozen(newer)).size()==1);CHECK(hits(older,previous).size()==0);CHECK(worker.requests==4);
}
TEST(inflight_allowing_response_cannot_cross_a_tighten_then_relax_epoch) {
  Temp temp;ReadWorker worker;Store store(temp.path/"db");WebService web(store,worker,{},SiteRegistry(manifest()));Store activation(temp.path/"db");
  worker.during=[&] { activation.activate_site_publication(SiteRegistry(configured("rejected")));activation.activate_site_publication(SiteRegistry(manifest())); };
  const auto result=web.read("alice",read_args("https://policy.example/docs/a"));CHECK(!result.at("indexed").flag());CHECK(result.at("content").at("text").str()=="marker canonical secret");CHECK(hits(store,frozen(store)).size()==0);
}
TEST(sql_candidate_limits_and_counts_exclude_withdrawn_rows_before_bounded_sweep_finishes) {
  Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));
  for(int i=0;i<400;++i)artifact(store,"alice","https://policy.example/docs/"+std::to_string(i));
  artifact(store,"alice","https://unregistered.example/a");
  store.activate_site_publication(SiteRegistry(configured("rejected")));CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM content_fts")>1);
  for(const auto& query:{parse_query(Json::string("marker")),parse_query(Json::parse(R"({"op":"not","child":{"op":"term","value":"absent"}})"))}) {
    const auto session=store.search("alice",query,1,1);CHECK(session.at("frozen_count").num()==1);CHECK(session.at("scanned_candidates").num()==1);CHECK(!session.at("partial").flag());CHECK(hits(store,session).at(0).at("url").str()=="https://unregistered.example/a");
  }
  CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM content_fts")==1);
}
TEST(escaped_and_requoted_paths_cannot_bypass_a_registered_origin_ceiling) {
  for(const auto* mode:{"links_only","rejected"}) {
    Temp temp;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));
    for(const auto* path:{"/docs/a%2Fb","/docs%2Fa","/%64ocs/%E4%B8%AD%E6%96%87","/docs/x/../a"})artifact(store,"alice",std::string("https://policy.example")+path);
    artifact(store,"alice","https://policy.example/elsewhere/a");store.activate_site_publication(SiteRegistry(configured(mode)));const auto current=hits(store,frozen(store));
    if(std::string(mode)=="rejected") { CHECK(current.size()==1);CHECK(current.at(0).at("url").str().ends_with("/elsewhere/a")); }
    else { CHECK(current.size()==5);for(std::size_t i=0;i<current.size();++i)CHECK(current.at(i).at("snippet").str().empty()==!current.at(i).at("url").str().ends_with("/elsewhere/a")); }
  }
}
TEST(history_capacity_failure_is_atomic_and_does_not_discard_restriction_watermarks) {
  Temp temp;
  { Store store(temp.path/"db");sql(store.raw_handle(),"WITH RECURSIVE n(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM n WHERE i<4096) INSERT INTO site_publication_rules(origin,path_prefix,mode,snippet_generation,index_generation) SELECT 'https://policy.example','/history/'||i,2,1,1 FROM n;");sql(store.raw_handle(),"UPDATE site_publication_state SET generation=1;"); }
  Store store(temp.path/"db");rejects([&]{store.activate_site_publication(SiteRegistry(manifest()));},"E_SITE_POLICY_CAPACITY");CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM site_publication_rules")==4096);CHECK(scalar(store.raw_handle(),"SELECT generation FROM site_publication_state")==1);CHECK(scalar(store.raw_handle(),"SELECT manifest_generation FROM site_publication_state")==0);CHECK(scalar(store.raw_handle(),"SELECT MIN(index_generation) FROM site_publication_rules")==1);
}
TEST(site_tightening_cannot_revive_an_inflight_deleted_owner_document) {
  Temp temp;ReadWorker worker;Store store(temp.path/"db");WebService web(store,worker,{},SiteRegistry(manifest()));const auto url=std::string("https://policy.example/docs/a");const auto snapshot=artifact(store,"alice",url);artifact(store,"bob",url);
  worker.during=[&] { store.erase("alice",snapshot.at("document_id").str());store.activate_site_publication(SiteRegistry(configured("links_only"))); };
  rejects([&]{web.read("alice",read_args(url));},"E_TARGET_DELETED");CHECK(hits(store,frozen(store)).size()==0);CHECK(hits(store,frozen(store,"bob"),"bob").size()==1);CHECK(worker.requests==1);
}
void schema_six(sqlite3* db) {
  sql(db,R"SQL(
 UPDATE usage SET bytes=bytes-COALESCE((SELECT SUM(length(CAST(s.site_bindings_json AS BLOB))) FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE d.owner=usage.owner AND s.site_bindings_json<>'{}'),0);
 UPDATE snapshots SET meta_json=json_remove(meta_json,'$.site_publication_generation','$.crawl_publication_urls');
 DROP TABLE site_publication_rules;DROP TABLE site_publication_state;
 DROP TABLE crawl_trap_edges;DROP TABLE crawl_trap_urls;DROP TABLE crawl_trap_policy;
 ALTER TABLE snapshots DROP COLUMN site_bindings_json;
 ALTER TABLE snapshots DROP COLUMN site_publication_generation;
 ALTER TABLE session_rows DROP COLUMN site_binding_version;
 PRAGMA user_version=6;
 )SQL");
}
TEST(schema_six_empty_unconfigured_upgrade_does_not_withdraw_unregistered_explicit_reads) {
  Temp temp;Json previous,snapshot;
  { Store store(temp.path/"db");snapshot=artifact(store,"alice","https://unregistered.example/a");previous=frozen(store);schema_six(store.raw_handle()); }
  Store store(temp.path/"db");CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==12);CHECK(hits(store,previous).size()==1);store.activate_site_publication(SiteRegistry(configured("removed")));
  CHECK(hits(store,previous).size()==1);CHECK(hits(store,frozen(store)).size()==1);CHECK(scalar(store.raw_handle(),"SELECT legacy_frozen_withdrawn FROM site_publication_state")==0);
  CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");
}
TEST(schema_six_first_managed_activation_withdraws_only_legacy_web_frozen_rows) {
  Temp temp;Json web_session,private_session,snapshot,evidence;
  { Store store(temp.path/"db");snapshot=artifact(store,"alice","https://policy.example/docs/a");web_session=frozen(store);evidence=store.evidence("alice",snapshot.at("snapshot_id").str(),0,6);store.ingest("bob",{"https://policy.example/docs/a","marker private import","text/plain","marker"});private_session=frozen(store,"bob");schema_six(store.raw_handle()); }
  Store store(temp.path/"db");CHECK(hits(store,web_session).size()==1);const auto generation=store.activate_site_publication(SiteRegistry(manifest()));CHECK(hits(store,web_session).size()==0);CHECK(hits(store,private_session,"bob").size()==1);CHECK(hits(store,frozen(store)).size()==1);
  CHECK(store.activate_site_publication(SiteRegistry(manifest()))==generation);CHECK(hits(store,web_session).size()==0);CHECK(store.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()=="marker");
}
TEST(schema_six_unverified_crawl_current_requires_complete_reread_after_explicit_activation) {
  for(const bool empty_manifest:{false,true}) {
  Temp temp;Json previous,snapshot;
  {
    Store store(temp.path/"db");auto initial=manifest();initial.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/docs","/transit"])"));store.activate_site_publication(SiteRegistry(initial));
    snapshot=artifact(store,"alice","https://policy.example/docs/b");previous=frozen(store);
    auto args=Json::object();args.set("site_id","policy-site");args.set("seeds",Json::parse(R"(["https://policy.example/docs/a"])"));args.set("idempotency_key","legacy-crawl");const auto job=store.crawl_submit("alice",args,SiteRegistry(initial));
    sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(store.raw_handle(),"UPDATE map_resources SET snapshot_id=?,state='succeeded',fetch_url='https://policy.example/docs/b' WHERE job_id=? AND kind='page'",-1,&raw,nullptr)==SQLITE_OK);
    std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);const auto sid=snapshot.at("snapshot_id").str(),jid=job.at("job_id").str();CHECK(sqlite3_bind_text(raw,1,sid.c_str(),-1,SQLITE_TRANSIENT)==SQLITE_OK);CHECK(sqlite3_bind_text(raw,2,jid.c_str(),-1,SQLITE_TRANSIENT)==SQLITE_OK);CHECK(sqlite3_step(raw)==SQLITE_DONE);statement.reset();schema_six(store.raw_handle());
  }
  Store store(temp.path/"db");CHECK(hits(store,frozen(store)).size()==1);store.activate_site_publication(SiteRegistry(empty_manifest?configured("removed"):manifest()));CHECK(hits(store,previous).size()==0);CHECK(hits(store,frozen(store)).size()==0);
  CHECK(scalar(store.raw_handle(),"SELECT legacy_crawl_unverified FROM site_publication_state")==1);
  if(empty_manifest) { store.activate_site_publication(SiteRegistry(manifest()));CHECK(hits(store,frozen(store)).size()==0); }
  CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");const auto fresh=artifact(store,"alice","https://policy.example/docs/b");CHECK(fresh.at("snapshot_id").str()!=snapshot.at("snapshot_id").str());CHECK(hits(store,frozen(store)).size()==1);CHECK(hits(store,previous).size()==0);
  CHECK(store.stats("alice").at("content_bytes").num()==scalar(store.raw_handle(),"SELECT SUM(length(CAST(raw_content AS BLOB))+length(CAST(canonical_text AS BLOB))+length(CAST(publication_json AS BLOB))+length(CAST(CASE WHEN representation_publication_json='{}' THEN '' ELSE representation_publication_json END AS BLOB))+length(CAST(site_bindings_json AS BLOB))) FROM snapshots"));
  }
}
TEST(schema_six_quarantine_is_checked_before_deployment_policy_migration) {
  Temp temp;{ Store store(temp.path/"db");artifact(store,"alice","https://policy.example/docs/a");schema_six(store.raw_handle());sql(store.raw_handle(),"UPDATE settings SET value='1' WHERE key='restore_quarantine';"); }
  rejects([&]{Store forbidden(temp.path/"db");},"E_RESTORE_QUARANTINED");sqlite3* raw=nullptr;CHECK(sqlite3_open((temp.path/"db").string().c_str(),&raw)==SQLITE_OK);std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);
  CHECK(scalar(raw,"PRAGMA user_version")==6);CHECK(scalar(raw,"SELECT COUNT(*) FROM sqlite_master WHERE name='site_publication_state'")==0);
}
TEST(read_many_receipts_recheck_current_deployment_policy_without_new_requests) {
  Temp temp;ReadWorker worker;Store store(temp.path/"db");WebService web(store,worker,{},SiteRegistry(manifest()));auto args=Json::parse(R"({"urls":["https://policy.example/docs/a"],"idempotency_key":"policy-batch"})");
  const auto job=web.submit_reads("alice",args);auto lease=store.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);const auto before=store.job_results("alice",job.at("job_id").str());CHECK(before.at("items").at(0).at("receipt").at("indexed").flag());
  const auto previous=frozen(store);store.activate_site_publication(SiteRegistry(configured("rejected")));const auto after=store.job_results("alice",job.at("job_id").str());CHECK(!after.at("items").at(0).at("receipt").at("indexed").flag());CHECK(after.at("items").at(0).at("available").flag());CHECK(worker.requests==1);CHECK(hits(store,previous).size()==0);
}
TEST(artifacts_captured_while_restricted_do_not_publish_after_an_unrelated_generation_and_later_allow) {
  for(const auto* mode:{"rejected","links_only"}) {
    Temp temp;ReadWorker worker;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));store.activate_site_publication(SiteRegistry(configured(mode)));
    auto changed=configured(mode);changed.at("sites").at(0).at("review").set("approved_by","different-reviewer-same-restriction");store.activate_site_publication(SiteRegistry(changed));
    // An empty local registry must use the persisted restriction, even at a
    // generation newer than the rule's first restrictive transition.
    WebService web(store,worker,{});const auto url=std::string("https://policy.example/docs/a");const auto first=web.read("alice",read_args(url));const auto repeated=web.read("alice",read_args(url));CHECK(first.at("snapshot_id").str()==repeated.at("snapshot_id").str());
    const auto previous=frozen(store);store.activate_site_publication(SiteRegistry(manifest()));const auto after=store.snapshot("alice",first.at("snapshot_id").str());
    const auto term=frozen(store),scan=store.search("alice",parse_query(Json::parse(R"({"op":"not","child":{"op":"term","value":"absent"}})")));
    if(std::string(mode)=="rejected") { CHECK(!after.at("indexed").flag());CHECK(hits(store,term).size()==0);CHECK(hits(store,scan).size()==0); }
    else { CHECK(after.at("indexed").flag());CHECK(hits(store,term).at(0).at("snippet").str().empty());CHECK(hits(store,scan).at(0).at("snippet").str().empty());CHECK(hits(store,previous).at(0).at("snippet").str().empty()); }
    WebService allowed(store,worker,{},SiteRegistry(manifest()));const auto fresh=allowed.read("alice",read_args(url));CHECK(fresh.at("snapshot_id").str()!=first.at("snapshot_id").str());CHECK(!hits(store,frozen(store)).at(0).at("snippet").str().empty());
    if(std::string(mode)=="rejected")CHECK(hits(store,previous).size()==0);else CHECK(hits(store,previous).at(0).at("snippet").str().empty());CHECK(worker.requests==3);
  }
}
TEST(inflight_restricted_response_cannot_borrow_a_later_allowing_generation) {
  for(const auto* mode:{"rejected","links_only"})for(const bool child_scope:{false,true}) {
    Temp temp;ReadWorker worker;Store store(temp.path/"db");store.activate_site_publication(SiteRegistry(manifest()));store.activate_site_publication(SiteRegistry(configured(mode)));
    auto changed=configured(mode);changed.at("sites").at(0).at("review").set("approved_by","different-reviewer-same-restriction");store.activate_site_publication(SiteRegistry(changed));
    WebService restricted(store,worker,{});Store activation(temp.path/"db");auto allowing=manifest();if(child_scope)allowing.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/docs/sub"])"));
    worker.during=[&] { activation.activate_site_publication(SiteRegistry(allowing)); };
    const auto url=std::string("https://policy.example/docs/sub/a"),old=restricted.read("alice",read_args(url)).at("snapshot_id").str();worker.during={};
    const auto previous=frozen(store),scan=store.search("alice",parse_query(Json::parse(R"({"op":"not","child":{"op":"term","value":"absent"}})")));
    CHECK(store.snapshot("alice",old).at("canonical_text").str()=="marker canonical secret");
    if(std::string(mode)=="rejected") { CHECK(!store.snapshot("alice",old).at("indexed").flag());CHECK(hits(store,previous).size()==0);CHECK(hits(store,scan).size()==0); }
    else { CHECK(hits(store,previous).at(0).at("snippet").str().empty());CHECK(hits(store,scan).at(0).at("snippet").str().empty()); }
    WebService allowed(activation,worker,{},SiteRegistry(allowing));const auto fresh=allowed.read("alice",read_args(url));CHECK(fresh.at("snapshot_id").str()!=old);CHECK(!hits(store,frozen(store)).at(0).at("snippet").str().empty());
    if(std::string(mode)=="rejected")CHECK(hits(store,previous).size()==0);else CHECK(hits(store,previous).at(0).at("snippet").str().empty());CHECK(worker.requests==2);
  }
}
struct OrderedReadWorker:ReadWorker {
  bool index=true,snippets=true,legacy=false,failed=false,partial_snippets=false;
  std::string final_url;Json redirects=Json::array();
  Json call(const std::string& method,const Json& args,int timeout)override {
    auto callback=std::exchange(during,{});auto response=ReadWorker::call(method,args,timeout);during=callback;
    response.set("final_url",final_url.empty()?args.at("url").str():final_url);response.set("redirects",redirects);
    response.at("usage").set("external_requests",static_cast<std::int64_t>(redirects.size()+1));
    response.set("index_allowed",index);auto policy=response.at("publication");policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippets);
    if(!index||!snippets)policy.set("snippet_text","");
    if(!index)policy.set("snippet_title","");
    if(!index)policy.at("directives").push(Json::string("noindex"));else if(!snippets)policy.at("directives").push(Json::string("nosnippet"));
    if(partial_snippets) { policy.at("directives").push(Json::string("data-nosnippet"));policy.set("snippet_text","marker safe excerpt");policy.set("snippet_title","marker safe title"); }
    if(failed) {
      response.set("status","http_error");response.set("http_status",std::int64_t{503});auto observed=Json::parse(policy.dump());observed.set("snippet_text","");observed.set("snippet_title","");response.set("publication_observation",observed);
    }
    if(legacy) { auto old=Json::object();for(const auto& key:response.keys())if(key!="publication")old.set(key,response.at(key));response=old; }
    if(callback)callback();
    return response;
  }
};
Json find_url(const Json& rows,const std::string& url) { for(std::size_t i=0;i<rows.size();++i)if(rows.at(i).at("url").str()==url)return rows.at(i);return Json(); }
Json ordered_read(Store& store,WebService& web,const std::string& url,bool batch) {
  if(!batch)return web.read("alice",read_args(url));
  const auto job=store.read_many_submit("alice",{url},random_id(),{});auto lease=store.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);
  const auto done=store.job_get("alice",job.at("job_id").str());CHECK(done.at("state").str()=="succeeded");
  auto result=store.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(result.at("external_requests").num()>=1);CHECK(!result.at("usage_unknown").flag());
  return store.snapshot("alice",result.at("receipt").at("snapshot_id").str());
}
TEST(refresh_order_fences_direct_and_bounded_reads_across_all_known_aliases) {
  const std::string a="https://order.example/a",b="https://order.example/b",x="https://order.example/x";
  for(bool batch:{false,true})for(const auto* mode:{"noindex","nosnippet","data-nosnippet"})for(int chain=0;chain<3;++chain)for(bool restore_before:{false,true}) {
    Temp temp;Store first(temp.path/"db"),second(temp.path/"db");OrderedReadWorker slow,newer;WebService old_web(first,slow,{}),new_web(second,newer,{});
    if(chain) { slow.final_url=b;slow.redirects.push(Json::string(chain==2?x:b));if(chain==2)slow.redirects.push(Json::string(b)); }
    const auto original=old_web.read("alice",read_args(a));const auto previous=frozen(first);const auto evidence=first.evidence("alice",original.at("snapshot_id").str(),0,6);
    artifact(second,"bob",a);const auto other=frozen(second,"bob");const auto target=chain==0?a:chain==1?b:x;
    newer.index=std::string(mode)!="noindex";newer.snippets=std::string(mode)!="nosnippet";newer.partial_snippets=std::string(mode)=="data-nosnippet";
    slow.during=[&] { new_web.read("alice",read_args(target));if(restore_before) { newer.index=true;newer.snippets=true;newer.partial_snippets=false;new_web.read("alice",read_args(target)); } };
    const auto late=ordered_read(first,old_web,a,batch);slow.during={};
    const auto policy=first.snapshot("alice",late.at("snapshot_id").str()).at("publication");CHECK(!policy.at("snippet_allowed").flag());
    if(std::string(mode)=="noindex")CHECK(!policy.at("index_allowed").flag());
    const auto current=find_url(hits(first,frozen(first)),a);const auto old=find_url(hits(first,previous),a);
    if(restore_before&&chain==0) { CHECK(!current.is_null());CHECK(!current.at("snippet").str().empty()); }
    else if(chain==0&&std::string(mode)=="data-nosnippet") { CHECK(!current.is_null());CHECK(current.at("snippet").str().find("safe")!=std::string::npos);CHECK(current.at("snippet").str().find("secret")==std::string::npos); }
    else if(std::string(mode)=="noindex")CHECK(current.is_null());
    else { CHECK(!current.is_null());CHECK(current.at("snippet").str().empty()); }
    if(std::string(mode)=="noindex")CHECK(old.is_null());else { CHECK(!old.is_null());CHECK(old.at("snippet").str().empty());CHECK(old.at("title").str()==a); }
    if(restore_before&&chain) { const auto independent=find_url(hits(first,frozen(first)),target);CHECK(!independent.is_null());CHECK(!independent.at("snippet").str().empty()); }
    CHECK(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");CHECK(first.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()=="marker");CHECK(!hits(second,other,"bob").at(0).at("snippet").str().empty());
    old_web.read("alice",read_args(a));CHECK(!find_url(hits(first,frozen(first)),a).is_null());const auto after=find_url(hits(first,previous),a);
    if(std::string(mode)=="noindex")CHECK(after.is_null());else { CHECK(!after.is_null());CHECK(after.at("snippet").str().empty()); }
  }
}
TEST(legacy_allowing_responses_are_permanently_sealed_after_a_new_snippet_restriction) {
  for(bool batch:{false,true}) {
    Temp temp;Store first(temp.path/"db"),second(temp.path/"db");OrderedReadWorker slow,newer;WebService old_web(first,slow,{}),new_web(second,newer,{});
    const std::string a="https://order.example/a",b="https://order.example/b";slow.final_url=b;slow.redirects.push(Json::string(b));slow.legacy=true;
    old_web.read("alice",read_args(a));const auto previous=frozen(first);newer.snippets=false;slow.during=[&] { new_web.read("alice",read_args(b)); };
    const auto late=ordered_read(first,old_web,a,batch);slow.during={};newer.snippets=true;new_web.read("alice",read_args(b));
    CHECK(first.snapshot("alice",late.at("snapshot_id").str()).at("publication").at("version").str()=="bzs-publication-1");CHECK(find_url(hits(first,frozen(first)),a).at("snippet").str().empty());CHECK(find_url(hits(first,previous),a).at("snippet").str().empty());
  }
}
TEST(identical_failed_header_observations_each_fence_a_new_inflight_allow) {
  for(bool batch:{false,true})for(bool noindex:{false,true}) {
    Temp temp;Store first(temp.path/"db"),second(temp.path/"db");OrderedReadWorker slow,newer;WebService old_web(first,slow,{}),new_web(second,newer,{});const auto url=std::string("https://order.example/a");
    old_web.read("alice",read_args(url));const auto previous=frozen(first);newer.index=!noindex;newer.snippets=false;newer.failed=true;
    rejects([&]{new_web.read("alice",read_args(url));},"E_FETCH_HTTP");const auto before=scalar(first.raw_handle(),"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'");
    slow.during=[&] { rejects([&]{new_web.read("alice",read_args(url));},"E_FETCH_HTTP"); };
    const auto late=ordered_read(first,old_web,url,batch);slow.during={};CHECK(scalar(first.raw_handle(),"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'")>before);
    CHECK(!first.snapshot("alice",late.at("snapshot_id").str()).at("publication").at("snippet_allowed").flag());
    if(noindex)CHECK(hits(first,frozen(first)).size()==0);else CHECK(hits(first,frozen(first)).at(0).at("snippet").str().empty());
    old_web.read("alice",read_args(url));CHECK(!hits(first,frozen(first)).at(0).at("snippet").str().empty());if(noindex)CHECK(hits(first,previous).size()==0);else CHECK(hits(first,previous).at(0).at("snippet").str().empty());
  }
}
TEST(late_alias_response_keeps_a_later_logical_retarget_and_its_frozen_row) {
  for(bool batch:{false,true}) {
    Temp temp;Store first(temp.path/"db"),second(temp.path/"db");OrderedReadWorker slow,newer;WebService old_web(first,slow,{}),new_web(second,newer,{});const std::string a="https://order.example/a",b="https://order.example/b",c="https://order.example/c";
    slow.final_url=b;slow.redirects.push(Json::string(b));old_web.read("alice",read_args(a));const auto old=frozen(first);Json current,new_session;
    slow.during=[&] { newer.final_url=c;newer.redirects=Json::array();newer.redirects.push(Json::string(c));current=new_web.read("alice",read_args(a));new_session=frozen(second);newer.final_url="";newer.redirects=Json::array();newer.index=false;new_web.read("alice",read_args(b)); };
    const auto late=ordered_read(first,old_web,a,batch);CHECK(late.at("snapshot_id").str()!=current.at("snapshot_id").str());CHECK(first.cached("alice",a)->at("snapshot_id").str()==current.at("snapshot_id").str());CHECK(!find_url(hits(first,new_session),a).is_null());CHECK(find_url(hits(first,old),a).is_null());
  }
}
TEST(deletion_fences_cover_final_and_intermediate_aliases_for_direct_and_bounded_reads) {
  for(bool batch:{false,true})for(bool middle:{false,true}) {
    Temp temp;Store first(temp.path/"db"),second(temp.path/"db");OrderedReadWorker slow,newer;WebService old_web(first,slow,{}),new_web(second,newer,{});const std::string a="https://order.example/a",b="https://order.example/b",x="https://order.example/x";
    const auto target=new_web.read("alice",read_args(middle?x:b));slow.final_url=b;slow.redirects.push(Json::string(middle?x:b));if(middle)slow.redirects.push(Json::string(b));
    slow.during=[&]{second.erase("alice",target.at("document_id").str());};
    if(!batch)rejects([&]{old_web.read("alice",read_args(a));},"E_TARGET_DELETED");
    else { const auto job=first.read_many_submit("alice",{a},random_id(),{});auto lease=first.lease_next_read();CHECK(lease);old_web.execute_read(*lease);const auto item=first.job_results("alice",job.at("job_id").str()).at("items").at(0);CHECK(item.at("state").str()=="failed");CHECK(item.at("error_code").str()=="E_TARGET_DELETED");CHECK(item.at("external_requests").num()==(middle?3:2));CHECK(!item.at("usage_unknown").flag()); }
    CHECK(find_url(hits(first,frozen(first)),a).is_null());
  }
}
TEST(bounded_read_dispatch_fence_survives_a_new_store_connection) {
  Temp temp;OrderedReadWorker older,newer;std::int64_t now=1800000000000;ReadLease lease;Json previous;std::string job_id;
  {
    Store store(temp.path/"db",[&]{return now;});WebService web(store,older,{});web.read("alice",read_args("https://order.example/a"));previous=frozen(store);const auto job=store.read_many_submit("alice",{"https://order.example/a"},"retained-fence",{});job_id=job.at("job_id").str();lease=*store.lease_next_read();
  }
  Store reopened(temp.path/"db",[&]{return now;});Store second(temp.path/"db",[&]{return now;});WebService newer_web(second,newer,{});newer.index=false;newer_web.read("alice",read_args(lease.url));WebService old_web(reopened,older,{});old_web.execute_read(lease);
  CHECK(reopened.job_get("alice",job_id).at("state").str()=="succeeded");CHECK(hits(reopened,frozen(reopened)).size()==0);CHECK(hits(reopened,previous).size()==0);CHECK(older.requests==2&&newer.requests==1);
}
TEST(schema_seven_inflight_read_has_an_unknown_capture_and_cannot_republish_a_legacy_alias) {
  Temp temp;OrderedReadWorker older,newer;std::int64_t now=1800000000000;ReadLease lease;Json previous,proof;std::string job_id;const std::string a="https://order.example/a",b="https://order.example/b";
  older.final_url=b;older.redirects.push(Json::string(b));
  {
    Store store(temp.path/"db",[&]{return now;});WebService old_web(store,older,{}),new_web(store,newer,{});const auto original=old_web.read("alice",read_args(a));previous=frozen(store);proof=store.evidence("alice",original.at("snapshot_id").str(),0,6);
    job_id=store.read_many_submit("alice",{a},"legacy-inflight",{}).at("job_id").str();lease=*store.lease_next_read();newer.index=false;new_web.read("alice",read_args(b));
    sql(store.raw_handle(),"ALTER TABLE document_publication DROP COLUMN index_generation; ALTER TABLE document_publication DROP COLUMN snippet_generation; ALTER TABLE session_rows DROP COLUMN snippet_suppressed; ALTER TABLE read_job_items DROP COLUMN publication_sequence; ALTER TABLE read_job_items DROP COLUMN publication_snapshot_id; ALTER TABLE map_resources DROP COLUMN publication_sequence; ALTER TABLE map_resources DROP COLUMN publication_snapshot_id; UPDATE snapshots SET meta_json=json_remove(meta_json,'$.page_publication_sequence','$.page_publication_history','$.page_publication_stale','$.publication_fence'); PRAGMA user_version=7;");
  }
  Store reopened(temp.path/"db",[&]{return now;});WebService old_web(reopened,older,{}),new_web(reopened,newer,{});CHECK(scalar(reopened.raw_handle(),"PRAGMA user_version")==12);CHECK(scalar(reopened.raw_handle(),"SELECT COUNT(*) FROM read_job_items WHERE publication_snapshot_id='@legacy'")==1);
  old_web.execute_read(lease);CHECK(reopened.job_get("alice",job_id).at("state").str()=="succeeded");CHECK(hits(reopened,frozen(reopened)).size()==0);
  newer.index=true;new_web.read("alice",read_args(b));const auto current=hits(reopened,frozen(reopened));CHECK(find_url(current,a).is_null());CHECK(!find_url(current,b).is_null());CHECK(hits(reopened,previous).size()==0);CHECK(reopened.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="marker");
  old_web.read("alice",read_args(a));CHECK(!find_url(hits(reopened,frozen(reopened)),a).is_null());CHECK(hits(reopened,previous).size()==0);
}
struct IncompleteLegacyRead:OrderedReadWorker {
  std::string failure;
  Json call(const std::string& method,const Json& args,int timeout)override {
    auto response=OrderedReadWorker::call(method,args,timeout);
    if(failure=="truncated")response.at("warnings").push(Json::string("download_truncated"));
    else if(!failure.empty())response.set("http_status",std::int64_t{failure=="partial"?206:500});
    return response;
  }
};
TEST(compatibility_responses_cannot_relax_restrictions_with_incomplete_http_bodies) {
  for(const bool batch:{false,true})for(const auto* mode:{"truncated","partial","http_error"}) {
    Temp temp;Store store(temp.path/"db");OrderedReadWorker warm;IncompleteLegacyRead legacy;
    legacy.legacy=true;legacy.failure=mode;WebService original(store,warm,{}),incomplete(store,legacy,{});
    const std::string url="https://order.example/incomplete";
    const auto saved=original.read("alice",read_args(url)),previous=frozen(store);
    const auto proof=store.evidence("alice",saved.at("snapshot_id").str(),0,6);
    warm.index=false;original.read("alice",read_args(url));CHECK(hits(store,frozen(store)).size()==0);
    const auto snapshots=store.stats("alice").at("snapshots").num();
    if(!batch)rejects([&]{incomplete.read("alice",read_args(url));},"E_WORKER_PROTOCOL");
    else {
      const auto id=store.read_many_submit("alice",{url},random_id(),{}).at("job_id").str();
      const auto lease=store.lease_next_read();CHECK(lease);incomplete.execute_read(*lease);
      const auto result=store.job_results("alice",id).at("items").at(0);
      CHECK(result.at("state").str()=="failed");CHECK(result.at("error_code").str()=="E_WORKER_PROTOCOL");
      CHECK(result.at("external_requests").num()==1);CHECK(!result.at("usage_unknown").flag());
    }
    CHECK(store.stats("alice").at("snapshots").num()==snapshots);CHECK(hits(store,frozen(store)).size()==0);CHECK(hits(store,previous).size()==0);
    CHECK(store.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="marker");
    legacy.failure.clear();ordered_read(store,incomplete,url,batch);CHECK(hits(store,frozen(store)).size()==1);CHECK(hits(store,previous).size()==0);
  }
}
}
int main() {
  std::size_t failed=0;
  for(const auto& [name,test]:tests())try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  std::cout<<tests().size()<<" site publication tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0;
}
