#include "bzs/error.hpp"
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

using namespace bzs;
namespace {
constexpr std::int64_t initial=1700000000000LL,week=7LL*24*60*60*1000;
const std::string origin="https://review-retention.example";
std::size_t checks=0;
void check(bool value,const std::string& message) { ++checks;if(!value)throw std::runtime_error(message); }
template<class F> void refusal(F fn,const std::string& expected,int http) {
  ++checks;try { fn(); } catch(const Error& e) { if(e.code==expected&&e.http_status==http)return;throw std::runtime_error("expected "+expected+", got "+e.code); }
  throw std::runtime_error("expected refusal "+expected);
}
std::string read_cell(Store& store,const std::string& query) {
  sqlite3_stmt* raw=nullptr;
  check(sqlite3_prepare_v2(store.raw_handle(),query.c_str(),-1,&raw,nullptr)==SQLITE_OK,"readonly prepare failed");
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> handle(raw,sqlite3_finalize);
  check(sqlite3_stmt_readonly(raw)!=0,"review helper was given a mutation");
  check(sqlite3_step(raw)==SQLITE_ROW,"readonly query returned no row");
  const auto* p=sqlite3_column_text(raw,0);return p?std::string(reinterpret_cast<const char*>(p)):"";
}
std::int64_t rows(Store& store,const std::string& table) { return std::stoll(read_cell(store,"SELECT COUNT(*) FROM "+table)); }
Json get_file(const std::filesystem::path& path) { return Json::parse(read_file(path,1024*1024)); }
void save_file(const std::filesystem::path& path,const Json& value) { std::ofstream file(path);file<<value.canonical();check(file.good(),"write fixture metadata failed"); }
Json site_manifest() {
  return Json::parse(R"({"version":1,"sites":[{"id":"retention-review","enabled":true,"origin":"https://review-retention.example","path_prefixes":["/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://review-retention.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-independent-review"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
Json map_args(const std::string& key) {
  auto value=Json::object(),seeds=Json::array();seeds.push(Json::string(origin+"/"+key));
  value.set("site_id","retention-review");value.set("seeds",seeds);value.set("idempotency_key",key);return value;
}
struct MapWorker:WorkerPort {
  std::int64_t& now;std::size_t calls=0;
  explicit MapWorker(std::int64_t& clock):now(clock) {}
  Json call(const std::string& method,const Json& args,int)override {
    check(method=="map.fetch","unexpected worker method");++calls;
    auto out=Json::parse(R"({"status":"ok","http_status":200,"usage":{"external_requests":1,"content_bytes":32}})");
    out.set("url",args.at("url"));out.set("final_url",args.at("url"));
    if(args.at("kind").str()=="robots") {
      auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
      record.set("origin",origin);record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);out.set("robots",record);
    } else {
      auto discovery=Json::parse(R"({"document_type":"html","parse_status":"ok","candidates":[],"truncated":false,"diagnostics":{}})");
      discovery.set("document_url",args.at("url"));out.set("discovery",discovery);
      auto decision=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","allowed":true,"reason":"robots_no_matching_rule"})");
      decision.set("origin",origin);decision.set("target_url",args.at("url"));decision.set("body_sha256",args.at("robots").at("body_sha256"));out.set("robots_decision",decision);
    }
    return out;
  }
};
void drain(WebService& web) { std::size_t dispatched=0;while(auto lease=web.lease_map()) { check(++dispatched<10,"unbounded map fixture");web.execute_map(*lease); } }
Json read_lease_json(const ReadLease& l) {
  auto j=Json::object();j.set("job_id",l.job_id);j.set("owner",l.owner);j.set("url",l.url);j.set("attempt_id",l.attempt_id);
  j.set("epoch",l.epoch);j.set("input_index",l.input_index);j.set("deletion_generation",l.deletion_generation);j.set("request_grant",l.request_grant);j.set("byte_grant",l.byte_grant);j.set("timeout_ms",l.timeout_ms);j.set("deadline_ms",l.deadline_ms);j.set("publication_sequence",l.publication_sequence);return j;
}
ReadLease load_read_lease(const Json& j) {
  ReadLease l;l.job_id=j.at("job_id").str();l.owner=j.at("owner").str();l.url=j.at("url").str();l.attempt_id=j.at("attempt_id").str();
  l.epoch=j.at("epoch").num();l.input_index=j.at("input_index").num();l.deletion_generation=j.at("deletion_generation").num();l.request_grant=j.at("request_grant").num();l.byte_grant=j.at("byte_grant").num();l.timeout_ms=j.at("timeout_ms").num();l.deadline_ms=j.at("deadline_ms").num();l.publication_sequence=j.at("publication_sequence").num();return l;
}
Json map_lease_json(const MapLease& l) {
  auto j=Json::object();j.set("job_id",l.job_id);j.set("owner",l.owner);j.set("site_id",l.site_id);j.set("manifest_sha256",l.manifest_sha256);j.set("origin",l.origin);j.set("url",l.url);j.set("logical_url",l.logical_url);j.set("kind",l.kind);j.set("attempt_id",l.attempt_id);
  j.set("epoch",l.epoch);j.set("position",l.position);j.set("resource_position",l.resource_position);j.set("depth",l.depth);j.set("redirect_hop",l.redirect_hop);j.set("deletion_generation",l.deletion_generation);j.set("request_grant",l.request_grant);j.set("byte_grant",l.byte_grant);j.set("timeout_ms",l.timeout_ms);j.set("deadline_ms",l.deadline_ms);j.set("robots_record",l.robots_record);j.set("publication_sequence",l.publication_sequence);return j;
}
MapLease load_map_lease(const Json& j) {
  MapLease l;l.job_id=j.at("job_id").str();l.owner=j.at("owner").str();l.site_id=j.at("site_id").str();l.manifest_sha256=j.at("manifest_sha256").str();l.origin=j.at("origin").str();l.url=j.at("url").str();l.logical_url=j.at("logical_url").str();l.kind=j.at("kind").str();l.attempt_id=j.at("attempt_id").str();
  l.epoch=j.at("epoch").num();l.position=j.at("position").num();l.resource_position=j.at("resource_position").num();l.depth=j.at("depth").num();l.redirect_hop=j.at("redirect_hop").num();l.deletion_generation=j.at("deletion_generation").num();l.request_grant=j.at("request_grant").num();l.byte_grant=j.at("byte_grant").num();l.timeout_ms=j.at("timeout_ms").num();l.deadline_ms=j.at("deadline_ms").num();l.robots_record=j.at("robots_record");l.publication_sequence=j.at("publication_sequence").num();return l;
}
ReadManyOptions read_options() { ReadManyOptions opts;opts.max_requests=4;opts.max_bytes=1000;return opts; }
std::vector<std::string> read_urls() { return {origin+"/known",origin+"/lost"}; }
void equal_summary(const Json& before,const Json& after) {
  auto reduced=Json::object();for(const auto& key:after.keys())if(key!="retention")reduced.set(key,after.at(key));
  check(reduced.canonical()==before.canonical(),"permanent summary changed original job data");
}
void prepare_reads(const std::filesystem::path& folder) {
  std::int64_t now=initial;Store store(folder/"state.db",[&]{return now;});check(read_cell(store,"PRAGMA user_version")=="8","old producer is not schema8");
  const auto imported=store.ingest("alice",{origin+"/import","reclamation independent body","text/plain","import"});
  const auto proof=store.evidence("alice",imported.at("snapshot_id").str(),0,11);
  const auto id=store.read_many_submit("alice",read_urls(),"original",read_options()).at("job_id").str();
  const auto known=store.lease_next_read();check(known&&known->job_id==id,"missing known old read lease");
  const SourceDocument doc{known->url,"reclamation known body","text/plain","known"};auto meta=Json::object();meta.set("final_url",known->url);
  check(store.complete_read(*known,doc,parse_document(doc.raw,doc.content_type,doc.title),meta,1,17),"old known read failed");
  const auto lost=store.lease_next_read();check(lost&&lost->job_id==id&&lost->request_grant==3&&lost->byte_grant==983,"old unknown grant differs");
  const auto paused=store.read_many_submit("alice",{origin+"/paused"},"paused",{}).at("job_id").str();store.job_control("alice",paused,"pause");
  const auto bob=store.read_many_submit("bob",{origin+"/bob"},"bob",{}).at("job_id").str();store.job_control("bob",bob,"cancel");
  auto state=Json::object();state.set("job_id",id);state.set("paused_id",paused);state.set("bob_id",bob);state.set("lease",read_lease_json(*lost));state.set("original_job",store.job_get("alice",id));state.set("import",imported);state.set("proof",proof);
  state.set("known_snapshot",store.job_results("alice",id).at("items").at(0).at("receipt").at("snapshot_id"));
  save_file(folder/"metadata.json",state);std::cout<<"PREPARED genuine schema8 read fixture: known=1/17, unresolved=3/983, paused=1, other_owner=1\n";
}
void prepare_robots(const std::filesystem::path& folder) {
  std::int64_t now=initial;Store store(folder/"state.db",[&]{return now;});check(read_cell(store,"PRAGMA user_version")=="8","old producer is not schema8");MapWorker worker(now);WebService web(store,worker,{},SiteRegistry(site_manifest()));
  auto first=map_args("receipt");first.set("max_requests",std::int64_t{1});const auto receipt=web.submit_map("alice",first).at("job_id").str();drain(web);
  check(store.job_get("alice",receipt).at("state").str()=="failed","robots-only job should exhaust its grant");++now;
  const auto admitted=web.submit_map("alice",map_args("admitted")).at("job_id").str();drain(web);
  check(store.job_get("alice",admitted).at("state").str()=="succeeded","cached-robots admission did not complete");++now;
  const auto running=web.submit_map("alice",map_args("unresolved")).at("job_id").str();const auto lost=web.lease_map();check(lost&&lost->job_id==running&&lost->kind!="robots","missing active old map admission");
  check(worker.calls==2&&rows(store,"robots_cache")==1,"old fixture should use exactly one robots exchange and one document exchange");
  auto state=Json::object();state.set("receipt_id",receipt);state.set("admitted_id",admitted);state.set("running_id",running);state.set("lease",map_lease_json(*lost));state.set("receipt_summary",store.job_get("alice",receipt));state.set("admitted_summary",store.job_get("alice",admitted));
  state.set("site_state",read_cell(store,"SELECT json_object('requests',requests,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started,'active_attempt',active_attempt,'active_until',active_until) FROM site_request_state"));
  state.set("dispatch_sequence",read_cell(store,"SELECT value FROM settings WHERE key='map_dispatch_sequence'"));save_file(folder/"metadata.json",state);
  std::cout<<"PREPARED genuine schema8 robots fixture: one receipt, one completed admission, one unresolved admission, worker_calls=2\n";
}
#ifndef BZS_RETENTION_PREPARE
void upgrade_reads(const std::filesystem::path& folder) {
  auto state=get_file(folder/"metadata.json");std::int64_t now=initial;const auto id=state.at("job_id").str();Json summary;
  {
    Store store(folder/"state.db",[&]{return now;});check(read_cell(store,"PRAGMA user_version")=="9","candidate did not migrate to schema9");
    check(store.job_get("alice",id).canonical()==state.at("original_job").canonical(),"upgrade changed active old read accounting");
    check(store.compact_jobs("alice").at("compacted_jobs").num()==0,"young active job was compacted");now+=week+3600000;
    check(store.compact_jobs("alice").at("compacted_jobs").num()==0,"unresolved expired grant was compacted before settlement");
    check(store.job_get("alice",id).at("usage").at("reserved_requests").num()==3,"unresolved reservation was refunded");
    store.settle_interruptions();summary=store.job_get("alice",id);
    check(summary.at("usage").at("unknown_requests_charged").num()==3&&summary.at("usage").at("unknown_bytes_charged").num()==983,"settlement lost full unknown grant");
    const auto first=store.compact_jobs("alice",1);check(first.at("compacted_jobs").num()==1&&!first.at("has_more_jobs").flag(),"wrong eligible job count");
    equal_summary(summary,store.job_get("alice",id));check(rows(store,"read_job_items")==2&&rows(store,"read_job_budgets")==2,"compaction deleted paused/other-owner details");
    check(store.job_get("alice",state.at("paused_id").str()).at("state").str()=="paused","paused task changed");check(!store.job_get("bob",state.at("bob_id").str()).has("retention"),"other owner's terminal job compacted");
  }
  Store store(folder/"state.db",[&]{return now;});equal_summary(summary,store.job_get("alice",id));
  refusal([&]{store.job_results("alice",id);},"E_JOB_RESULTS_EXPIRED",410);refusal([&]{store.job_results("bob",id);},"E_NOT_FOUND",404);refusal([&]{store.job_control("alice",id,"retry_failed");},"E_JOB_RESULTS_EXPIRED",410);
  const auto replay=store.read_many_submit("alice",read_urls(),"original",read_options());check(replay.at("reused").flag()&&replay.at("job_id").str()==id,"key replay changed identity");check(!store.lease_next_read(),"archived read was redispatched");
  auto late=load_read_lease(state.at("lease"));const SourceDocument doc{late.url,"late body","text/plain","late"};check(!store.complete_read(late,doc,parse_document(doc.raw,doc.content_type),Json::object(),1,9),"late old callback reopened archived read");store.fail_read(late,"E_FETCH_HTTP",1,9);equal_summary(summary,store.job_get("alice",id));
  check(store.snapshot("alice",state.at("import").at("snapshot_id").str()).at("canonical_text").str()=="reclamation independent body","private import changed");check(store.evidence_get("alice",state.at("proof").at("evidence_id").str()).at("quote").str()=="reclamation","private evidence changed");check(store.snapshot("alice",state.at("known_snapshot").str()).at("canonical_text").str()=="reclamation known body","completed read snapshot removed");
  std::cout<<"PASS schema8_to9_unknown_read_archive_owner_pins_replay: known=1/17, unknown=3/983, charged=4/1000, canonical_and_evidence_preserved, no_network\n";
}
void upgrade_robots(const std::filesystem::path& folder) {
  auto state=get_file(folder/"metadata.json");std::int64_t now=initial+week+3600000;Json running_summary;
  const auto site_query="SELECT json_object('requests',requests,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started,'active_attempt',active_attempt,'active_until',active_until) FROM site_request_state";
  {
    Store store(folder/"state.db",[&]{return now;});check(read_cell(store,"PRAGMA user_version")=="9","candidate did not migrate robots fixture");
    const auto first=store.compact_jobs("alice",1);check(first.at("compacted_jobs").num()==1&&first.at("has_more_jobs").flag()&&first.at("deleted_robots_records").num()==0,"first compaction lost admission pin or bounded continuation");equal_summary(state.at("receipt_summary"),store.job_get("alice",state.at("receipt_id").str()));
    const auto second=store.compact_jobs("alice",1);check(second.at("compacted_jobs").num()==1&&!second.at("has_more_jobs").flag()&&second.at("deleted_robots_records").num()==0,"second compaction removed unsettled admission's robots");equal_summary(state.at("admitted_summary"),store.job_get("alice",state.at("admitted_id").str()));
    check(rows(store,"map_hops")==1&&rows(store,"robots_cache")==1,"active admission lost its exact robots record");check(read_cell(store,site_query)==state.at("site_state").str(),"compaction altered active site quota/lane");check(read_cell(store,"SELECT value FROM settings WHERE key='map_dispatch_sequence'")==state.at("dispatch_sequence").str(),"compaction reset dispatch ordering");
    store.settle_interruptions();running_summary=store.job_get("alice",state.at("running_id").str());check(running_summary.at("usage").at("unknown_requests_charged").num()==1,"unknown map grant was refunded");
    const auto settled_site=read_cell(store,site_query);const auto third=store.compact_jobs("alice",1);check(third.at("compacted_jobs").num()==1&&third.at("deleted_robots_records").num()==1,"unreferenced expired robots were not reclaimed");check(rows(store,"map_jobs")==0&&rows(store,"map_resources")==0&&rows(store,"map_hops")==0&&rows(store,"robots_cache")==0,"terminal detail rows remain");check(read_cell(store,site_query)==settled_site,"compaction altered already settled origin state");equal_summary(running_summary,store.job_get("alice",state.at("running_id").str()));
  }
  Store store(folder/"state.db",[&]{return now;});MapWorker worker(now);WebService web(store,worker,{},SiteRegistry(site_manifest()));
  auto first=map_args("receipt");first.set("max_requests",std::int64_t{1});for(const auto& arguments:{first,map_args("admitted"),map_args("unresolved")})check(web.submit_map("alice",arguments).at("reused").flag(),"archived map key not reusable");
  check(!web.lease_map(),"archived map was scheduled after restart");web.execute_map(load_map_lease(state.at("lease")));check(worker.calls==0,"late map callback or replay performed a worker exchange");equal_summary(running_summary,store.job_get("alice",state.at("running_id").str()));
  refusal([&]{store.job_results("alice",state.at("admitted_id").str());},"E_JOB_RESULTS_EXPIRED",410);refusal([&]{store.job_get("bob",state.at("admitted_id").str());},"E_NOT_FOUND",404);
  std::cout<<"PASS schema8_to9_robots_receipt_completed_and_unsettled_admission_pins: limits_1_then1_then1, unknown_full_grant, no_quota_or_sequence_reset, late_worker_calls=0\n";
}
#endif
}
int main(int argc,char** argv) {
  try {
    check(argc==4,"usage: review-retention prepare|upgrade reads|robots folder");const std::string command=argv[1],scenario=argv[2];const std::filesystem::path folder=argv[3];
    if(command=="prepare") { if(scenario=="reads")prepare_reads(folder);else if(scenario=="robots")prepare_robots(folder);else throw std::runtime_error("unknown scenario"); }
#ifndef BZS_RETENTION_PREPARE
    else if(command=="upgrade") { if(scenario=="reads")upgrade_reads(folder);else if(scenario=="robots")upgrade_robots(folder);else throw std::runtime_error("unknown scenario"); }
#endif
    else throw std::runtime_error("unknown command");
    std::cout<<"independent retention assertions="<<checks<<", failures=0\n";return 0;
  }catch(const std::exception& e) { std::cerr<<"FAIL independent retention: "<<e.what()<<" (assertions="<<checks<<")\n";return 1; }
}
