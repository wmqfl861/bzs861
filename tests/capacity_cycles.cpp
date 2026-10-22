// Repeated public-API capacity/recovery scenarios. SQL is read-only observation;
// no fixture inserts, lease edits, deleted permanent keys or counter resets.
// Fake Worker + explicit clock advancement, not real HTTP or a long soak run.
#include "bzs/error.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <chrono>
#include <deque>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <string>
#include <vector>

using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> values;return values; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add registered_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); } while(false)
template<class F> void rejects(F fn,const std::string& code,int status) {
  ++assertions;try { fn(); }catch(const Error& error) {
    if(error.code==code&&error.http_status==status)return;
    throw std::runtime_error("expected "+code+", got "+error.code);
  }
  throw std::runtime_error("expected "+code);
}
constexpr std::int64_t week=604800000,hour=3600000;
constexpr const char* origin="https://cycles.example";
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-capacity-cycles-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
};
std::string cell(Store& store,const std::string& query,const std::vector<std::string>& parameters={}) {
  sqlite3_stmt* raw=nullptr;
  CHECK(sqlite3_prepare_v2(store.raw_handle(),query.c_str(),-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);
  CHECK(sqlite3_stmt_readonly(raw)!=0);
  for(std::size_t i=0;i<parameters.size();++i)CHECK(sqlite3_bind_text(raw,static_cast<int>(i+1),parameters[i].c_str(),-1,SQLITE_TRANSIENT)==SQLITE_OK);
  CHECK(sqlite3_step(raw)==SQLITE_ROW);const auto* value=sqlite3_column_text(raw,0);
  return value?std::string(reinterpret_cast<const char*>(value)):"";
}
std::int64_t count(Store& store,const std::string& query,const std::vector<std::string>& parameters={}) {
  return std::stoll(cell(store,query,parameters));
}
Json manifest() {
  return Json::parse(R"({"version":1,"sites":[{"id":"cycle-site","enabled":true,"origin":"https://cycles.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://cycles.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-capacity-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
std::vector<std::string> urls(const std::string& stem,std::size_t size=1) {
  std::vector<std::string> values;for(std::size_t i=0;i<size;++i)values.push_back(std::string(origin)+"/docs/"+stem+"/"+std::to_string(i));return values;
}
Json map_args(const std::string& key,std::size_t seeds=1) {
  auto out=Json::object(),values=Json::array();for(const auto& url:urls(key,seeds))values.push(Json::string(url));
  out.set("site_id","cycle-site");out.set("seeds",values);out.set("idempotency_key",key);return out;
}
struct Worker:WorkerPort {
  std::int64_t now=1700000000000,calls=0;
  Json page(const Json& params)const {
    auto out=Json::parse(R"({"status":"ok","html":"capacity cycle retained body","text":"capacity cycle retained body","title":"capacity cycle","content_type":"text/plain","extractor":"capacity-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":100},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"capacity cycle retained body","snippet_title":"capacity cycle","directives":[],"diagnostics":[]}})");
    out.set("final_url",params.at("url"));return out;
  }
  Json call(const std::string& method,const Json& params,int)override {
    ++calls;if(method=="fetch.extract")return page(params);
    CHECK(method=="map.fetch");auto out=Json::object();out.set("status","ok");out.set("url",params.at("url"));out.set("final_url",params.at("url"));out.set("http_status",std::int64_t{200});
    out.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
    if(params.at("kind").str()=="robots") {
      auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
      record.set("origin",params.at("robots_origin"));record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+hour);out.set("robots",record);
    } else {
      auto discovery=Json::object();discovery.set("document_url",params.at("url"));discovery.set("document_type","html");discovery.set("parse_status","ok");discovery.set("candidates",Json::array());discovery.set("truncated",false);discovery.set("diagnostics",Json::object());out.set("discovery",discovery);
      auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",params.at("robots").at("origin"));decision.set("target_url",params.at("url"));decision.set("body_sha256",params.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","robots_no_matching_rule");out.set("robots_decision",decision);
      if(params.at("kind").str()=="page") { const auto body=page(params);out.set("page",body);out.set("publication",body.at("publication")); }
    }
    return out;
  }
};
struct Harness {
  Temp temp;Worker worker;SiteRegistry sites{manifest()};
  std::unique_ptr<Store> store;std::unique_ptr<WebService> web;
  std::int64_t submissions=0,read_leases=0,map_leases=0,reopens=0;
  const std::int64_t initial_time=worker.now;
  const std::chrono::steady_clock::time_point started=std::chrono::steady_clock::now();
  Harness() { open(); }
  void open() { store=std::make_unique<Store>(temp.path/"db",[this]{return worker.now;});web=std::make_unique<WebService>(*store,worker,SourceRegistry{},sites); }
  void reopen() { web.reset();store.reset();open();++reopens; }
  std::string read(const std::string& owner,const std::string& key,std::size_t size=1,ReadManyOptions options={}) {
    auto out=store->read_many_submit(owner,urls(key,size),key,options);CHECK(!out.at("reused").flag());++submissions;return out.at("job_id").str();
  }
  std::string map(const std::string& owner,const Json& args,bool crawl=false) {
    auto out=crawl?web->submit_crawl(owner,args):web->submit_map(owner,args);CHECK(!out.at("reused").flag());++submissions;return out.at("job_id").str();
  }
  ReadLease read_one() { auto lease=store->lease_next_read();CHECK(lease);++read_leases;web->execute_read(*lease);return *lease; }
  MapLease map_one() { auto lease=web->lease_map();CHECK(lease);++map_leases;web->execute_map(*lease);return *lease; }
  void drain_map(std::int64_t bound=100) {
    std::int64_t turns=0;while(auto lease=web->lease_map()) { CHECK(++turns<=bound);++map_leases;web->execute_map(*lease); }
  }
  Json compact(const std::string& owner,std::int64_t limit=64) {
    const auto calls=worker.calls;auto out=store->compact_jobs(owner,limit);CHECK(worker.calls==calls);CHECK(out.at("external_requests").num()==0);CHECK(out.at("model_calls").num()==0);return out;
  }
  std::int64_t compact_all(const std::string& owner) {
    std::int64_t total=0,turns=0;for(;;) { CHECK(++turns<=32);auto out=compact(owner);total+=out.at("compacted_jobs").num();if(!out.at("has_more_jobs").flag())return total; }
  }
  void report(const std::string& scenario,std::int64_t cycles)const {
    auto out=Json::object();out.set("scenario",scenario);out.set("cycles",cycles);out.set("submissions",submissions);out.set("read_leases",read_leases);out.set("map_leases",map_leases);out.set("store_reopens",reopens);out.set("fake_worker_calls",worker.calls);
    out.set("simulated_elapsed_ms",worker.now-initial_time);out.set("wall_elapsed_ms",static_cast<std::int64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-started).count()));out.set("fixture_sql_writes",false);out.set("real_http",false);out.set("long_running_throughput_evidence",false);std::cout<<out.canonical()<<'\n';
  }
};
void same_accounting(const Json& before,const Json& after) {
  for(const auto* key:{"state","lease_epoch","dispatches","deadline_ms","used_pages","partial","error_code","external_requests","usage","budget"})CHECK(before.at(key).canonical()==after.at(key).canonical());
}
std::string sequence(Store& store) {
  return cell(store,"SELECT json_group_array(json_object('key',key,'value',value)) FROM (SELECT key,value FROM settings WHERE key IN ('read_dispatch_sequence','map_dispatch_sequence') ORDER BY key)");
}
std::string origin_accounting(Store& store) {
  return cell(store,"SELECT json_group_array(json_object('origin',origin,'requests',requests,'next_start',next_start,'cooldown_until',cooldown_until,'window_started',window_started,'active_attempt',active_attempt,'active_until',active_until)) FROM (SELECT * FROM site_request_state ORDER BY origin)");
}

TEST(three_full_frontier_capacity_cycles_preserve_paused_keys_and_owner_boundaries) {
  Harness h;std::vector<std::pair<std::string,std::string>> paused;
  for(const auto* owner:{"alice","bob"}) { const auto id=h.map(owner,map_args(std::string("paused-")+owner),true);h.store->job_control(owner,id,"pause");paused.emplace_back(owner,id); }
  std::vector<std::pair<std::string,std::string>> old_keys;
  for(int cycle=0;cycle<3;++cycle) {
    std::string alice,bob,alice_key,bob_key;
    for(int i=0;i<510;++i) {
      const auto owner=i%2==0?"alice":"bob";const auto key="fill-"+std::to_string(cycle)+"-"+std::to_string(i);const auto id=h.map(owner,map_args(key),i%2!=0);
      if(i==0) { alice=id;alice_key=key; }else if(i==1) { bob=id;bob_key=key; }else h.store->job_control(owner,id,"cancel");
    }
    CHECK(count(*h.store,"SELECT COUNT(*) FROM map_jobs")==512);
    for(const auto* owner:{"alice","bob"})rejects([&]{h.web->submit_map(owner,map_args("overflow-"+std::to_string(cycle)+owner));},"E_QUOTA",429);
    h.drain_map();CHECK(h.store->job_get("alice",alice).at("state").str()=="succeeded");CHECK(h.store->job_get("bob",bob).at("state").str()=="succeeded");
    const auto before=h.store->job_get("alice",alice);const auto queue=sequence(*h.store),quota=origin_accounting(*h.store);const auto calls=h.worker.calls;
    h.worker.now=before.at("deadline_ms").num()+week+1;h.reopen();
    CHECK(h.compact_all("alice")==255);CHECK(count(*h.store,"SELECT COUNT(*) FROM map_jobs")==257);CHECK(!h.store->job_get("bob",bob).has("retention"));
    same_accounting(before,h.store->job_get("alice",alice));CHECK(h.web->submit_map("alice",map_args(alice_key)).at("job_id").str()==alice);CHECK(h.web->submit_map("alice",map_args(alice_key)).at("reused").flag());
    rejects([&]{h.store->job_results("alice",alice);},"E_JOB_RESULTS_EXPIRED",410);rejects([&]{h.store->job_get("bob",alice);},"E_NOT_FOUND",404);
    CHECK(h.compact_all("bob")==255);CHECK(count(*h.store,"SELECT COUNT(*) FROM map_jobs")==2);CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs")==2+510*(cycle+1));
    CHECK(sequence(*h.store)==queue);CHECK(origin_accounting(*h.store)==quota);CHECK(h.worker.calls==calls);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache")==0);
    CHECK(h.web->submit_crawl("bob",map_args(bob_key)).at("job_id").str()==bob);old_keys.emplace_back(alice_key,alice);
    for(const auto& [owner,id]:paused) { CHECK(h.store->job_get(owner,id).at("state").str()=="paused");CHECK(!h.store->job_get(owner,id).has("retention")); }
    h.reopen();for(const auto& [key,id]:old_keys) { const auto replay=h.web->submit_map("alice",map_args(key));CHECK(replay.at("job_id").str()==id);CHECK(replay.at("retention").at("details_available").flag()==false); }
  }
  const auto fresh=h.map("alice",map_args("after-third-full-cycle"));CHECK(h.store->job_get("alice",fresh).at("state").str()=="queued");CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs")==1533);CHECK(count(*h.store,"SELECT COUNT(*) FROM map_jobs")==3);
  h.report("frontier_512_capacity_refill",3);
}

TEST(continuous_arrivals_follow_durable_round_robin_across_twelve_reopens) {
  Harness h;
  for(const auto* owner:{"alice","bob"}) { const auto id=h.map(owner,map_args(std::string("warm-")+owner));h.drain_map();CHECK(h.store->job_get(owner,id).at("state").str()=="succeeded"); }
  const auto paused_read=h.read("alice","paused-read"),paused_map=h.map("bob",map_args("paused-map"));h.store->job_control("alice",paused_read,"pause");h.store->job_control("bob",paused_map,"pause");
  const auto old_read=h.read("bob","old-read",12),old_map=h.map("alice",map_args("old-map",10),true);
  struct Waiting { std::string id;int remaining; };
  std::deque<Waiting> reads={{old_read,12}},maps={{old_map,10}};
  auto advance=[&](std::deque<Waiting>& queue,bool map) {
    CHECK(!queue.empty());auto expected=queue.front();queue.pop_front();const auto actual=map?h.map_one().job_id:h.read_one().job_id;CHECK(actual==expected.id);if(--expected.remaining>0)queue.push_back(expected);
  };
  for(int arrival=0;arrival<96;++arrival) {
    const auto owner=arrival%2==0?"alice":"bob",other=arrival%2==0?"bob":"alice";const auto key="arrival-"+std::to_string(arrival);
    reads.push_back({h.read(owner,"read-"+key),1});maps.push_back({h.map(other,map_args("map-"+key),arrival%2!=0),1});
    if(arrival==47) { h.store->job_control("alice",paused_read,"resume");h.store->job_control("bob",paused_map,"resume");reads.push_front({paused_read,1});maps.push_front({paused_map,1}); }
    advance(reads,false);advance(maps,true);
    if(arrival<47) { CHECK(h.store->job_get("alice",paused_read).at("state").str()=="paused");CHECK(h.store->job_get("bob",paused_map).at("state").str()=="paused"); }
    if((arrival+1)%8==0) { const auto before=sequence(*h.store);h.reopen();CHECK(sequence(*h.store)==before); }
  }
  CHECK(h.store->job_get("bob",old_read).at("state").str()=="succeeded");CHECK(h.store->job_get("alice",old_map).at("state").str()=="succeeded");
  CHECK(h.store->job_get("bob",old_read).at("succeeded_items").num()==12);CHECK(h.store->job_get("alice",old_map).at("succeeded_pages").num()==10);
  while(!reads.empty())advance(reads,false);
  while(!maps.empty())advance(maps,true);
  CHECK(!h.store->lease_next_read());CHECK(!h.web->lease_map());
  const auto snapshot=h.store->job_results("bob",old_read).at("items").at(0).at("receipt").at("snapshot_id").str();const auto proof=h.store->evidence("bob",snapshot,0,8).at("evidence_id").str();
  const auto before=h.store->job_get("alice",old_map);h.worker.now+=week+hour+1;
  const auto session=h.store->search("bob",parse_query(Json::string("capacity"))).at("session_id").str();const auto page=h.store->page("bob",session,"",100).canonical();
  const auto queue=sequence(*h.store),quota=origin_accounting(*h.store);CHECK(h.compact_all("alice")+h.compact_all("bob")==h.submissions);
  CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs")==h.submissions);CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs WHERE archived_at>0")==h.submissions);CHECK(sequence(*h.store)==queue);CHECK(origin_accounting(*h.store)==quota);
  h.reopen();same_accounting(before,h.store->job_get("alice",old_map));CHECK(h.store->snapshot("bob",snapshot).at("canonical_text").str()=="capacity cycle retained body");CHECK(h.store->evidence_get("bob",proof).at("quote").str()=="capacity");CHECK(h.store->page("bob",session,"",100).canonical()==page);
  h.report("continuous_arrivals_96_opportunities_per_queue",12);
}

TEST(six_unknown_grant_cycles_keep_active_and_paused_work_then_compact_without_refunds) {
  Harness h;ReadManyOptions options;options.max_requests=10;options.max_bytes=10000;
  std::vector<std::pair<std::string,Json>> archives;
  for(int cycle=0;cycle<6;++cycle) {
    const auto key="unknown-"+std::to_string(cycle);const auto read=h.read("alice",key,2,options);CHECK(h.read_one().job_id==read);
    auto abandoned=h.store->lease_next_read();CHECK(abandoned&&abandoned->job_id==read);++h.read_leases;CHECK(abandoned->request_grant==9);CHECK(abandoned->byte_grant==9900);
    auto args=map_args(key);args.set("max_requests",std::int64_t{3});args.set("max_bytes",std::int64_t{9000});const auto crawl=h.map("bob",args,true);CHECK(h.map_one().kind=="robots");
    auto page=h.web->lease_map();CHECK(page&&page->job_id==crawl&&page->kind=="page");++h.map_leases;CHECK(page->request_grant==1);CHECK(page->byte_grant==8900);
    const auto pending=h.read("alice","pending-"+key),paused=h.map("alice",map_args("paused-"+key),true);h.store->job_control("alice",paused,"pause");
    CHECK(h.compact("alice").at("compacted_jobs").num()==0);CHECK(h.compact("bob").at("compacted_jobs").num()==0);
    h.worker.now+=week+hour+1;h.reopen();
    CHECK(h.compact("alice").at("compacted_jobs").num()==0);CHECK(h.compact("bob").at("compacted_jobs").num()==0);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache")==1);
    CHECK(h.store->job_get("alice",read).at("usage").at("reserved_requests").num()==9);CHECK(h.store->job_get("bob",crawl).at("usage").at("reserved_bytes").num()==8900);
    CHECK(h.store->job_get("alice",pending).at("state").str()=="queued");CHECK(h.store->job_get("alice",paused).at("state").str()=="paused");
    h.store->settle_interruptions();const auto before=h.store->job_get("alice",read),mapped=h.store->job_get("bob",crawl);
    CHECK(before.at("state").str()=="failed");CHECK(mapped.at("state").str()=="failed");CHECK(before.at("usage").at("unknown_requests_charged").num()==9);CHECK(before.at("usage").at("unknown_bytes_charged").num()==9900);CHECK(before.at("budget").at("charged_requests").num()==10);CHECK(before.at("budget").at("charged_bytes").num()==10000);
    CHECK(mapped.at("usage").at("unknown_requests_charged").num()==1);CHECK(mapped.at("usage").at("unknown_bytes_charged").num()==8900);CHECK(mapped.at("budget").at("charged_requests").num()==2);CHECK(mapped.at("budget").at("charged_bytes").num()==9000);
    const auto snapshot=h.store->job_results("alice",read).at("items").at(0).at("receipt").at("snapshot_id").str();const auto proof=h.store->evidence("alice",snapshot,0,8).at("evidence_id").str();const auto quota=origin_accounting(*h.store),queue=sequence(*h.store);const auto calls=h.worker.calls;
    CHECK(h.compact("alice",1).at("compacted_jobs").num()==1);CHECK(h.compact("bob",1).at("compacted_jobs").num()==1);same_accounting(before,h.store->job_get("alice",read));same_accounting(mapped,h.store->job_get("bob",crawl));
    CHECK(h.store->read_many_submit("alice",urls(key,2),key,options).at("job_id").str()==read);CHECK(h.web->submit_crawl("bob",args).at("job_id").str()==crawl);
    CHECK(!h.store->complete_read(*abandoned,{abandoned->url,"late","text/plain","late"},parse_document("late","text/plain"),Json::object(),1,4));h.store->fail_read(*abandoned,"E_INTERNAL");CHECK(!h.store->complete_map(*page,Json::object(),h.sites,1,4));h.store->fail_map(*page,"E_INTERNAL");
    same_accounting(before,h.store->job_get("alice",read));same_accounting(mapped,h.store->job_get("bob",crawl));CHECK(h.worker.calls==calls);CHECK(origin_accounting(*h.store)==quota);CHECK(sequence(*h.store)==queue);
    CHECK(h.store->snapshot("alice",snapshot).at("canonical_text").str()=="capacity cycle retained body");CHECK(h.store->evidence_get("alice",proof).at("quote").str()=="capacity");rejects([&]{h.store->job_get("bob",read);},"E_NOT_FOUND",404);
    CHECK(!h.store->job_get("alice",pending).has("retention"));CHECK(!h.store->job_get("alice",paused).has("retention"));h.store->job_control("alice",pending,"cancel");h.store->job_control("alice",paused,"cancel");CHECK(h.compact("alice").at("compacted_jobs").num()==2);
    archives.emplace_back(read,before);h.reopen();for(const auto& [id,summary]:archives)same_accounting(summary,h.store->job_get("alice",id));CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs")==4*(cycle+1));CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs WHERE archived_at>0")==4*(cycle+1));CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache")==0);
  }
  h.report("unknown_grant_recovery_and_retention",6);
}

TEST(eight_robots_reference_cycles_protect_receipt_and_admission_independently) {
  Harness h;std::set<std::string> records;
  for(int cycle=0;cycle<8;++cycle) {
    const auto key="robots-"+std::to_string(cycle);const bool receipt_first=cycle%2==0;
    auto first=map_args(key+"-receipt");first.set("max_requests",std::int64_t{1});first.set("timeout_ms",receipt_first?std::int64_t{1000}:hour);const auto receipt=h.map("alice",first);h.drain_map();CHECK(h.store->job_get("alice",receipt).at("state").str()=="failed");
    ++h.worker.now;auto second=map_args(key+"-admission");second.set("timeout_ms",receipt_first?hour:std::int64_t{1000});const auto admission=h.map("alice",second);h.drain_map();CHECK(h.store->job_get("alice",admission).at("state").str()=="succeeded");
    auto other_args=map_args(key+"-other-owner");other_args.set("max_requests",std::int64_t{1});const auto other=h.map("bob",other_args);h.drain_map();
    const auto record=cell(*h.store,"SELECT id FROM robots_cache WHERE owner='alice'");CHECK(records.insert(record).second);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache")==2);
    CHECK(count(*h.store,"SELECT COUNT(*) FROM map_hops WHERE json_extract(response_json,'$.robots_cache_id')=?",{record})==1);CHECK(count(*h.store,"SELECT COUNT(*) FROM map_hops WHERE json_extract(admission_json,'$.robots_cache_id')=?",{record})==1);
    const auto calls=h.worker.calls;h.worker.now+=hour+1;CHECK(h.compact("alice",1).at("deleted_robots_records").num()==0);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache WHERE id=?",{record})==1);
    h.worker.now+=week;const auto one=h.compact("alice",1);CHECK(one.at("compacted_jobs").num()==1);CHECK(one.at("deleted_robots_records").num()==0);
    CHECK(h.store->job_get("alice",receipt_first?receipt:admission).has("retention"));CHECK(!h.store->job_get("alice",receipt_first?admission:receipt).has("retention"));CHECK(!h.store->job_get("bob",other).has("retention"));
    CHECK(count(*h.store,"SELECT COUNT(*) FROM map_hops WHERE json_extract(response_json,'$.robots_cache_id')=?",{record})==(receipt_first?0:1));CHECK(count(*h.store,"SELECT COUNT(*) FROM map_hops WHERE json_extract(admission_json,'$.robots_cache_id')=?",{record})==(receipt_first?1:0));
    h.reopen();CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache WHERE id=?",{record})==1);const auto last=h.compact("alice",1);CHECK(last.at("compacted_jobs").num()==1);CHECK(last.at("deleted_robots_records").num()==1);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache WHERE owner='bob'")==1);
    CHECK(h.compact("bob",1).at("deleted_robots_records").num()==1);CHECK(count(*h.store,"SELECT COUNT(*) FROM robots_cache")==0);CHECK(h.web->submit_map("alice",first).at("job_id").str()==receipt);CHECK(h.web->submit_map("alice",second).at("job_id").str()==admission);CHECK(h.worker.calls==calls);
    CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs")==3*(cycle+1));CHECK(count(*h.store,"SELECT COUNT(*) FROM jobs WHERE archived_at>0")==3*(cycle+1));
  }
  h.report("robots_receipt_and_admission_pins",8);
}
}

int main(int argc,char** argv) {
  const auto start=std::chrono::steady_clock::now();std::size_t failed=0,executed=0;
  for(const auto& [name,test]:tests()) {
    if(argc>1&&name!=argv[1])continue;
    ++executed;
    try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  }
  if(executed==0) { std::cerr<<"No matching capacity cycle test\n";return 1; }
  const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
  std::cout<<executed<<" capacity cycle tests, "<<assertions<<" assertions, "<<failed<<" failures, "<<elapsed<<" wall ms\n";return failed?1:0;
}
