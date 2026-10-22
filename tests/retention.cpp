#include "bzs/error.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <thread>
#include <vector>

namespace {
using namespace bzs;
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> values;return values; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add registered_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); } while(false)
template<class F> void rejects(F fn,const std::string& code,int status=0) {
  ++assertions;try { fn(); }catch(const Error& error) {
    if(error.code==code&&(status==0||error.http_status==status))return;
    throw std::runtime_error("expected "+code+", got "+error.code);
  }
  throw std::runtime_error("expected "+code);
}
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-retention-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
};
constexpr std::int64_t week=7LL*24*60*60*1000;
constexpr const char* base="https://map.example";
void sql(sqlite3* db,const std::string& query) { ++assertions;if(sqlite3_exec(db,query.c_str(),nullptr,nullptr,nullptr)!=SQLITE_OK)throw std::runtime_error(sqlite3_errmsg(db)); }
std::string cell(sqlite3* db,const std::string& query) {
  sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query.c_str(),-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);CHECK(sqlite3_step(raw)==SQLITE_ROW);
  const auto* value=sqlite3_column_text(raw,0);return value?std::string(reinterpret_cast<const char*>(value)):"";
}
std::int64_t count(sqlite3* db,const std::string& query) { return std::stoll(cell(db,query)); }
Json manifest() {
  return Json::parse(R"({"version":1,"sites":[{"id":"map-site","enabled":true,"origin":"https://map.example","path_prefixes":["/map/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://map.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
Json args(const std::string& key="task",const std::string& url=std::string(base)+"/map/index") {
  auto out=Json::object(),seeds=Json::array();seeds.push(Json::string(url));out.set("site_id","map-site");out.set("seeds",seeds);out.set("idempotency_key",key);return out;
}
struct Fake:WorkerPort {
  std::int64_t now=1700000000000;std::size_t calls=0;bool expand=false,noindex=false;
  Json page(const Json& params)const {
    auto out=Json::parse(R"({"status":"ok","html":"retention marker canonical body","text":"retention marker canonical body","content_type":"text/plain","title":"retention title","extractor":"retention-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"metadata":{},"warnings":[],"redirects":[],"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"retention marker canonical body","snippet_title":"retention title","directives":[],"diagnostics":[]}})");
    out.set("final_url",params.at("url"));
    if(noindex) {
      out.set("index_allowed",false);auto p=out.at("publication");p.set("index_allowed",false);p.set("snippet_allowed",false);p.set("snippet_text","");p.set("snippet_title","");p.at("directives").push(Json::string("noindex"));
    }
    return out;
  }
  Json call(const std::string& method,const Json& params,int)override {
    ++calls;
    if(method=="fetch.extract") { auto out=page(params);out.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));return out; }
    CHECK(method=="map.fetch");auto out=Json::object();out.set("status","ok");out.set("url",params.at("url"));out.set("final_url",params.at("url"));out.set("http_status",std::int64_t{200});out.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
    if(params.at("kind").str()=="robots") {
      auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","body_sha256":"","state":"rules","rules":[],"sitemaps":[],"diagnostics":[],"origin":"https://map.example","fetched_at_ms":0,"expires_at_ms":0})");
      record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);out.set("robots",record);return out;
    }
    auto discovery=Json::object();discovery.set("document_url",params.at("url"));discovery.set("document_type","html");discovery.set("parse_status","ok");discovery.set("truncated",false);discovery.set("diagnostics",Json::object());auto candidates=Json::array();
    if(expand&&params.at("url").str().ends_with("/index"))for(int i=1;i<=18;++i) {
      auto c=Json::object();c.set("url",std::string(base)+"/map/calendar/2026-01-"+(i<10?"0":"")+std::to_string(i));c.set("parent",params.at("url"));c.set("source","html");c.set("document",Json());c.set("depth",params.at("depth").num()+1);c.set("path_depth",std::int64_t{3});candidates.push(c);
    }
    discovery.set("candidates",candidates);out.set("discovery",discovery);
    auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",base);decision.set("target_url",params.at("url"));decision.set("body_sha256",params.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","robots_no_matching_rule");out.set("robots_decision",decision);
    if(params.at("kind").str()=="page") { auto body=page(params);out.set("publication",body.at("publication"));out.set("page",body); }
    return out;
  }
};
void drain(WebService& web) { std::size_t turns=0;while(auto lease=web.lease_map()) { CHECK(++turns<=100);web.execute_map(*lease); } }
Json read_params(const std::string& url) { auto out=Json::object();out.set("url",url);out.set("freshness","refresh");return out; }
Json search(Store& store) { auto session=store.search("alice",parse_query(Json::string("retention")));return store.page("alice",session.at("session_id").str(),"",100).at("hits"); }
void accounting_equal(const Json& before,const Json& after) {
  for(const auto* key:{"state","lease_epoch","dispatches","deadline_ms","used_pages","partial","error_code","external_requests","usage","budget"})CHECK(before.at(key).canonical()==after.at(key).canonical());
}

TEST(aged_terminal_frontiers_release_the_real_512_job_cap_after_restart) {
  Temp temp;Fake worker;SiteRegistry sites(manifest());std::string first;
  {
    Store store(temp.path/"db",[&]{return worker.now;});store.activate_site_publication(sites);
    for(int i=0;i<512;++i) { const auto id=store.map_submit("alice",args("job-"+std::to_string(i)),sites).at("job_id").str();if(i==0)first=id;store.job_control("alice",id,"cancel"); }
    rejects([&]{store.map_submit("alice",args("full"),sites);},"E_QUOTA");CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_jobs")==512);
  }
  worker.now+=week+86400000;
  Store store(temp.path/"db",[&]{return worker.now;});const auto fresh=store.map_submit("alice",args("after-window"),sites);
  CHECK(!fresh.at("reused").flag());CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM jobs")==513);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_jobs")==505);
  for(int i=0;i<8;++i) { const auto compacted=store.compact_jobs("alice",64);if(i==0)CHECK(compacted.at("has_more_jobs").flag()); }
  CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_jobs")==1);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_resources")==2);
  const auto replay=store.map_submit("alice",args("job-0"),sites);CHECK(replay.at("job_id").str()==first);CHECK(replay.at("reused").flag());CHECK(replay.at("state").str()=="cancelled");CHECK(!replay.at("retention").at("details_available").flag());
  rejects([&]{store.map_submit("alice",args("job-0",std::string(base)+"/map/different"),sites);},"E_IDEMPOTENCY_CONFLICT",409);
  rejects([&]{store.job_results("alice",first);},"E_JOB_RESULTS_EXPIRED",410);CHECK(worker.calls==0);
}

TEST(the_full_deadline_window_and_clock_extremes_are_respected) {
  Temp temp;std::int64_t now=1700000000000;Store store(temp.path/"db",[&]{return now;});ReadManyOptions options;options.timeout_ms=3600000;options.max_attempts=2;
  const auto id=store.read_many_submit("alice",{"https://read.example/window"},"window",options).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);store.fail_read(*lease,"E_FETCH_HTTP",1,40);
  CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);CHECK(store.job_control("alice",id,"retry_failed").at("state").str()=="queued");store.job_control("alice",id,"cancel");
  const auto start=now;now=start+options.timeout_ms+week-1;CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);
  now=-1;CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);
  now=start+options.timeout_ms+week;CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==1);
  now=start;const auto future=store.read_many_submit("alice",{"https://read.example/future"},"future",{}).at("job_id").str();store.job_control("alice",future,"cancel");
  sql(store.raw_handle(),"UPDATE jobs SET deadline=9223372036854775807 WHERE id='"+future+"'");now=std::numeric_limits<std::int64_t>::max();CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);
}

TEST(unknown_read_grants_survive_compaction_and_late_callbacks_cannot_reopen_work) {
  Temp temp;std::int64_t now=1700000000000;ReadLease abandoned;Json before,snapshot,proof;std::string id;
  const std::vector<std::string> urls={"https://read.example/known","https://read.example/unknown"};ReadManyOptions options;options.max_requests=10;options.max_bytes=10000;
  {
    Store store(temp.path/"db",[&]{return now;});id=store.read_many_submit("alice",urls,"mixed",options).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease);
    const SourceDocument input{lease->url,"retention marker body","text/plain","retention title"};auto parsed=parse_document(input.raw,input.content_type,input.title);auto meta=Json::object();meta.set("final_url",input.url);
    CHECK(store.complete_read(*lease,input,parsed,meta,1,100));snapshot=store.snapshot("alice",store.job_results("alice",id).at("items").at(0).at("receipt").at("snapshot_id").str());proof=store.evidence("alice",snapshot.at("snapshot_id").str(),0,9);
    const auto next=store.lease_next_read();CHECK(next);abandoned=*next;now+=week+86400000;
    CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);CHECK(store.job_get("alice",id).at("usage").at("reserved_requests").num()==9);
    store.settle_interruptions();before=store.job_get("alice",id);CHECK(before.at("state").str()=="failed");CHECK(before.at("used_pages").num()==1);CHECK(before.at("usage").at("unknown_requests_charged").num()==9);CHECK(before.at("usage").at("unknown_bytes_charged").num()==9900);
    const auto compacted=store.compact_jobs("alice");CHECK(compacted.at("compacted_jobs").num()==1);CHECK(compacted.at("external_requests").num()==0);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM read_job_items")==0);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM read_job_budgets")==0);
  }
  Store store(temp.path/"db",[&]{return now;});const auto after=store.job_get("alice",id);accounting_equal(before,after);CHECK(after.at("retention").at("version").str()=="bzs-job-retention-1");
  const auto replay=store.read_many_submit("alice",urls,"mixed",options);CHECK(replay.at("reused").flag());CHECK(replay.at("job_id").str()==id);CHECK(!store.lease_next_read());
  CHECK(!store.complete_read(abandoned,{abandoned.url,"late","text/plain","late"},parse_document("late","text/plain"),Json::object(),1,4));store.fail_read(abandoned,"E_INTERNAL");accounting_equal(after,store.job_get("alice",id));
  for(const auto* action:{"resume","retry_failed","pause","cancel"})rejects([&]{store.job_control("alice",id,action);},"E_JOB_RESULTS_EXPIRED",410);
  rejects([&]{store.job_results("bob",id);},"E_NOT_FOUND",404);rejects([&]{store.job_control("bob",id,"resume");},"E_NOT_FOUND",404);rejects([&]{store.job_get("bob",id);},"E_NOT_FOUND",404);
  CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="retention marker body");CHECK(store.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="retention");
}

TEST(paused_and_pending_jobs_remain_pinned_and_maintenance_is_owner_scoped) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});SiteRegistry sites(manifest());
  const auto paused=store.crawl_submit("alice",args("paused"),sites).at("job_id").str();store.job_control("alice",paused,"pause");
  const auto pending=store.read_many_submit("alice",{"https://read.example/pending"},"pending",{}).at("job_id").str();
  const auto other=store.map_submit("bob",args("other"),sites).at("job_id").str();store.job_control("bob",other,"cancel");f.now+=week+86400000;
  CHECK(store.compact_jobs("alice",64).at("compacted_jobs").num()==0);CHECK(store.job_get("alice",paused).at("state").str()=="paused");CHECK(store.job_get("alice",pending).at("state").str()=="queued");CHECK(!store.job_get("bob",other).has("retention"));
  CHECK(store.compact_jobs("bob",1).at("compacted_jobs").num()==1);CHECK(store.job_results("alice",pending).at("items").size()==1);CHECK(store.crawl_edges("alice",paused).at("items").size()==0);
}

TEST(unknown_crawl_page_grants_and_origin_charges_survive_expiry_and_compaction) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});SiteRegistry sites(manifest());WebService web(store,f,{},sites);
  auto parameters=args();parameters.set("max_requests",std::int64_t{3});parameters.set("max_bytes",std::int64_t{9000});parameters.set("max_pages",std::int64_t{2});
  const auto id=web.submit_crawl("alice",parameters).at("job_id").str();const auto robots=web.lease_map();CHECK(robots&&robots->kind=="robots");web.execute_map(*robots);
  const auto page=web.lease_map();CHECK(page&&page->kind=="page");f.now+=week+86400000;CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);
  store.settle_interruptions();const auto before=store.job_get("alice",id);CHECK(before.at("state").str()=="failed");CHECK(before.at("used_pages").num()==1);CHECK(before.at("remaining_pages").num()==1);
  CHECK(before.at("usage").at("external_requests").num()==1);CHECK(before.at("usage").at("unknown_requests_charged").num()==1);CHECK(before.at("usage").at("unknown_bytes_charged").num()==8900);CHECK(before.at("budget").at("charged_bytes").num()==9000);
  const auto quota=cell(store.raw_handle(),"SELECT json_object('requests',requests,'active_attempt',active_attempt,'active_until',active_until,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started) FROM site_request_state");
  CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==1);accounting_equal(before,store.job_get("alice",id));CHECK(store.job_get("alice",id).at("remaining_pages").num()==1);
  CHECK(!store.complete_map(*page,Json::object(),sites,1,100));store.fail_map(*page,"E_INTERNAL",1,100);accounting_equal(before,store.job_get("alice",id));
  CHECK(cell(store.raw_handle(),"SELECT json_object('requests',requests,'active_attempt',active_attempt,'active_until',active_until,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started) FROM site_request_state")==quota);CHECK(store.crawl_submit("alice",parameters,sites).at("reused").flag());CHECK(!web.lease_map());CHECK(f.calls==1);
}

TEST(robots_receipt_and_admission_each_pin_the_exact_expired_record) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});WebService web(store,f,{},SiteRegistry(manifest()));
  auto first=args("robots-receipt");first.set("max_requests",std::int64_t{1});const auto a=web.submit_map("alice",first).at("job_id").str();drain(web);CHECK(store.job_get("alice",a).at("state").str()=="failed");
  ++f.now;const auto b=web.submit_map("alice",args("robots-admission")).at("job_id").str();drain(web);CHECK(store.job_get("alice",b).at("state").str()=="succeeded");CHECK(f.calls==2);
  const auto cache=cell(store.raw_handle(),"SELECT id FROM robots_cache");f.now+=3600001;CHECK(store.compact_jobs("alice").at("deleted_robots_records").num()==0);
  CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_hops WHERE json_extract(response_json,'$.robots_cache_id')='"+cache+"'")==1);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_hops WHERE json_extract(admission_json,'$.robots_cache_id')='"+cache+"'")==1);
  f.now+=week;CHECK(store.compact_jobs("alice",1).at("compacted_jobs").num()==1);CHECK(store.job_get("alice",a).has("retention"));CHECK(!store.job_get("alice",b).has("retention"));CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==1);
  const auto last=store.compact_jobs("alice",1);CHECK(last.at("compacted_jobs").num()==1);CHECK(last.at("deleted_robots_records").num()==1);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==0);CHECK(f.calls==2);
}

TEST(expired_unreferenced_robots_are_bounded_and_fresh_or_other_owner_records_survive) {
  Temp temp;std::int64_t now=1700000000000;Store store(temp.path/"db",[&]{return now;});
  sql(store.raw_handle(),"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10) INSERT INTO robots_cache(id,owner,origin,manifest_sha256,record_json,fetched_at,expires_at) SELECT 'expired-'||x,'alice','https://other.example','old','{}',0,1 FROM n");
  sql(store.raw_handle(),"INSERT INTO robots_cache(id,owner,origin,manifest_sha256,record_json,fetched_at,expires_at) VALUES('fresh','alice','https://fresh.example','old','{}',0,9223372036854775807),('other-owner','bob','https://other.example','old','{}',0,1)");
  const auto one=store.compact_jobs("alice",1);CHECK(one.at("deleted_robots_records").num()==4);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==8);
  store.compact_jobs("alice",64);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache WHERE id IN ('fresh','other-owner')")==2);
}

TEST(robots_capacity_can_recover_at_admission_without_erasing_origin_accounting) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});WebService web(store,f,{},SiteRegistry(manifest()));const auto id=web.submit_map("alice",args()).at("job_id").str();
  sql(store.raw_handle(),"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<512) INSERT INTO robots_cache(id,owner,origin,manifest_sha256,record_json,fetched_at,expires_at) SELECT 'expired-'||x,'alice','https://other.example','old','{}',0,1 FROM n");
  drain(web);CHECK(f.calls==2);CHECK(store.job_get("alice",id).at("state").str()=="succeeded");CHECK(count(store.raw_handle(),"SELECT requests FROM site_request_state")==2);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==449);
}

TEST(crawl_frontier_edges_traps_and_map_candidates_compact_with_original_summaries) {
  Temp temp;Fake f;f.expand=true;Store store(temp.path/"db",[&]{return f.now;});WebService web(store,f,{},SiteRegistry(manifest()));
  const auto a=web.submit_crawl("alice",args("crawl")).at("job_id").str();drain(web);const auto before=store.job_get("alice",a);CHECK(before.at("succeeded_pages").num()==17);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM crawl_trap_edges")>0);
  ++f.now;const auto b=web.submit_map("alice",args("map")).at("job_id").str();drain(web);CHECK(store.job_get("alice",b).at("candidate_count").num()>0);
  const auto snapshot=store.job_results("alice",a).at("items").at(0).at("snapshot_id").str(),proof=store.evidence("alice",snapshot,0,9).at("evidence_id").str();
  const auto lane=cell(store.raw_handle(),"SELECT json_object('requests',requests,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started,'active_attempt',active_attempt,'active_until',active_until) FROM site_request_state");
  sql(store.raw_handle(),"INSERT OR REPLACE INTO settings VALUES('read_dispatch_sequence','73')");const auto sequence=cell(store.raw_handle(),"SELECT value FROM settings WHERE key='map_dispatch_sequence'");const auto calls=f.calls;f.now+=week+86400000;
  CHECK(store.compact_jobs("alice",2).at("compacted_jobs").num()==2);accounting_equal(before,store.job_get("alice",a));
  for(const auto* table:{"map_jobs","map_resources","map_hops","map_candidates","crawl_edges","crawl_trap_policy","crawl_trap_urls","crawl_trap_edges"})CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM "+std::string(table))==0);
  CHECK(cell(store.raw_handle(),"SELECT json_object('requests',requests,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started,'active_attempt',active_attempt,'active_until',active_until) FROM site_request_state")==lane);
  CHECK(cell(store.raw_handle(),"SELECT value FROM settings WHERE key='read_dispatch_sequence'")=="73");CHECK(cell(store.raw_handle(),"SELECT value FROM settings WHERE key='map_dispatch_sequence'")==sequence);CHECK(f.calls==calls);
  CHECK(store.snapshot("alice",snapshot).at("canonical_text").str()=="retention marker canonical body");CHECK(store.evidence_get("alice",proof).at("quote").str()=="retention");
  const auto archive=cell(store.raw_handle(),"SELECT archive_json FROM jobs WHERE id='"+a+"'");CHECK(archive.size()<=8192);CHECK(archive.find("canonical body")==std::string::npos);CHECK(archive.find(snapshot)==std::string::npos);
  rejects([&]{store.crawl_edges("alice",a);},"E_JOB_RESULTS_EXPIRED",410);rejects([&]{store.crawl_edges("bob",a);},"E_NOT_FOUND",404);
  CHECK(web.submit_crawl("alice",args("crawl")).at("reused").flag());CHECK(web.submit_map("alice",args("map")).at("reused").flag());CHECK(!web.lease_map());CHECK(f.calls==calls);
}

TEST(a_storage_failure_rolls_back_the_summary_and_all_detail_deletions) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});WebService web(store,f,{},SiteRegistry(manifest()));const auto id=web.submit_crawl("alice",args()).at("job_id").str();drain(web);const auto before=store.job_get("alice",id);const auto hops=count(store.raw_handle(),"SELECT COUNT(*) FROM map_hops");f.now+=week+86400000;
  sql(store.raw_handle(),"CREATE TRIGGER refuse_retention BEFORE DELETE ON map_resources BEGIN SELECT RAISE(ABORT,'retention fixture'); END");
  rejects([&]{store.compact_jobs("alice");},"E_STORAGE");CHECK(!store.job_get("alice",id).has("retention"));accounting_equal(before,store.job_get("alice",id));CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM map_hops")==hops);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM crawl_trap_policy")==1);CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==1);
  sql(store.raw_handle(),"DROP TRIGGER refuse_retention");CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==1);
}

TEST(compaction_never_recreates_publication_deleted_aliases_or_owner_epochs) {
  Temp temp;Fake f;Store store(temp.path/"db",[&]{return f.now;});WebService web(store,f,{},SiteRegistry(manifest()));const auto id=web.submit_crawl("alice",args()).at("job_id").str();drain(web);const auto saved=store.job_results("alice",id).at("items").at(0).at("snapshot_id").str();
  const auto deleted=store.ingest("alice",{"https://deleted.example/a","retention private deleted","text/plain","deleted"});store.erase("alice",deleted.at("document_id").str());f.noindex=true;web.read("alice",read_params(std::string(base)+"/map/index"));CHECK(search(store).size()==0);
  const auto policies=cell(store.raw_handle(),"SELECT json_group_array(json_object('key',url_key,'record',record_json,'index',index_generation,'snippet',snippet_generation)) FROM document_publication ORDER BY url_key");const auto epoch=cell(store.raw_handle(),"SELECT epoch FROM subjects WHERE owner='alice'");const auto deletion=cell(store.raw_handle(),"SELECT value FROM settings WHERE key='deletion_sequence'");f.now+=week+86400000;store.compact_jobs("alice");
  CHECK(search(store).size()==0);CHECK(store.snapshot("alice",saved).at("canonical_text").str()=="retention marker canonical body");CHECK(cell(store.raw_handle(),"SELECT json_group_array(json_object('key',url_key,'record',record_json,'index',index_generation,'snippet',snippet_generation)) FROM document_publication ORDER BY url_key")==policies);CHECK(cell(store.raw_handle(),"SELECT epoch FROM subjects WHERE owner='alice'")==epoch);CHECK(cell(store.raw_handle(),"SELECT value FROM settings WHERE key='deletion_sequence'")==deletion);CHECK(count(store.raw_handle(),"SELECT deleted FROM documents WHERE url='https://deleted.example/a'")==1);
}

TEST(schema_eight_upgrade_keeps_live_jobs_accounting_and_private_pins) {
  Temp temp;Fake f;std::string id,snapshot,proof;
  {
    Store store(temp.path/"db",[&]{return f.now;});const auto doc=store.ingest("alice",{"https://import.example/a","retention import","text/plain","import"});snapshot=doc.at("snapshot_id").str();proof=store.evidence("alice",snapshot,0,9).at("evidence_id").str();id=store.read_many_submit("alice",{"https://read.example/live"},"live",{}).at("job_id").str();store.job_control("alice",id,"pause");
    sql(store.raw_handle(),"DROP INDEX jobs_retention; DROP INDEX robots_retention; DROP INDEX map_hops_robots_admission; DROP INDEX map_hops_robots_receipt; ALTER TABLE jobs DROP COLUMN archive_json; ALTER TABLE jobs DROP COLUMN archived_at; PRAGMA user_version=8");
  }
  Store store(temp.path/"db",[&]{return f.now;});CHECK(count(store.raw_handle(),"PRAGMA user_version")==12);CHECK(store.job_get("alice",id).at("state").str()=="paused");CHECK(store.read_many_submit("alice",{"https://read.example/live"},"live",{}).at("reused").flag());f.now+=week+86400000;CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==0);CHECK(store.snapshot("alice",snapshot).at("canonical_text").str()=="retention import");CHECK(store.evidence_get("alice",proof).at("quote").str()=="retention");
}

TEST(permanent_key_quotas_remain_bounded_per_owner_and_globally) {
  Temp temp;Store store(temp.path/"db");const auto first=store.job_create("alice","fixture","permanent").at("job_id").str();store.job_control("alice",first,"cancel");
  sql(store.raw_handle(),"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<9999) INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at) SELECT 'own-'||x,'alice','fixture','own-'||x,'retained','cancelled',0,1,1,0 FROM n");
  rejects([&]{store.job_create("alice","fixture","new");},"E_QUOTA");CHECK(store.job_create("alice","fixture","permanent").at("reused").flag());CHECK(store.compact_jobs("alice").at("retained_job_keys").num()==10000);
  sql(store.raw_handle(),"WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<40000) INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at) SELECT 'global-'||x,'owner-'||((x-1)/10000),'fixture','global-'||x,'retained','cancelled',0,1,1,0 FROM n");
  rejects([&]{store.read_many_submit("new-owner",{"https://read.example/a"},"global",{});},"E_QUOTA");rejects([&]{store.map_submit("new-owner",args("global"),SiteRegistry(manifest()));},"E_QUOTA");CHECK(count(store.raw_handle(),"SELECT COUNT(*) FROM jobs")==50000);
}

TEST(two_store_compactors_serialize_without_duplicate_summaries_or_charges) {
  Temp temp;std::int64_t now=1700000000000;Store first(temp.path/"db",[&]{return now;});
  const auto id=first.read_many_submit("alice",{"https://read.example/one"},"one",{}).at("job_id").str();const auto lease=first.lease_next_read();CHECK(lease);first.fail_read(*lease,"E_FETCH_HTTP",1,100);const auto before=first.job_get("alice",id);now+=week+86400000;
  Store second(temp.path/"db",[&]{return now;});std::int64_t results[2]={-1,-1};std::exception_ptr errors[2];
  auto compact=[&](int index,Store& store) { try { results[index]=store.compact_jobs("alice",1).at("compacted_jobs").num(); }catch(...) { errors[index]=std::current_exception(); } };
  std::thread a([&]{compact(0,first);}),b([&]{compact(1,second);});a.join();b.join();for(const auto& error:errors)if(error)std::rethrow_exception(error);
  CHECK(results[0]+results[1]==1);accounting_equal(before,first.job_get("alice",id));CHECK(first.job_get("alice",id).canonical()==second.job_get("alice",id).canonical());CHECK(count(first.raw_handle(),"SELECT COUNT(*) FROM jobs")==1);
}

TEST(compacted_backups_keep_the_restore_quarantine_gate) {
  Temp temp;std::int64_t now=1700000000000;Store store(temp.path/"db",[&]{return now;});const auto id=store.read_many_submit("alice",{"https://read.example/backup"},"backup",{}).at("job_id").str();store.job_control("alice",id,"cancel");now+=week+86400000;store.compact_jobs("alice");
  const auto backup=temp.path/"backup.db";store.backup(backup);rejects([&]{Store forbidden(backup,[&]{return now;});},"E_RESTORE_QUARANTINED",409);
  sqlite3* raw=nullptr;CHECK(sqlite3_open(backup.string().c_str(),&raw)==SQLITE_OK);std::unique_ptr<sqlite3,decltype(&sqlite3_close)> database(raw,sqlite3_close);CHECK(count(raw,"PRAGMA user_version")==12);CHECK(count(raw,"SELECT COUNT(*) FROM jobs WHERE archived_at>0")==1);CHECK(count(raw,"SELECT COUNT(*) FROM read_job_items")==0);
}

TEST(rpc_is_available_offline_and_validates_raw_limit_and_unknown_parameters) {
  Temp temp;Store store(temp.path/"db");FixtureCatalog catalog(BZS_FIXTURE_DIR);Service service(store,catalog,temp.path/"backups");
  const auto operations=service.capabilities().at("operations");bool found=false;for(std::size_t i=0;i<operations.size();++i)found=found||operations.at(i).str()=="jobs.compact";CHECK(found);
  auto request=Json::object();request.set("operation","jobs.compact");request.set("params",Json::object());const auto result=service.dispatch({"alice",false},request);CHECK(result.at("limit").num()==16);CHECK(result.at("minimum_retention_ms").num()==week);
  for(const auto& value:{Json(),Json::boolean(true),Json::string("1"),Json::integer(0),Json::integer(65)}) { auto invalid=Json::parse(request.dump());invalid.at("params").set("limit",value);rejects([&]{service.dispatch({"alice",false},invalid);},"E_SCHEMA"); }
  for(const auto* key:{"owner","retention_ms","force"}) { auto invalid=Json::parse(request.dump());invalid.at("params").set(key,std::int64_t{0});rejects([&]{service.dispatch({"alice",false},invalid);},"E_SCHEMA"); }
}
}
int main() {
  std::size_t failed=0;for(const auto& [name,test]:tests())try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  std::cout<<tests().size()<<" retention tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0;
}
