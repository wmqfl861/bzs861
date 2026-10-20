#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace bzs;
namespace {
constexpr const char* origin="https://upgrade.example";
void check(bool value,const std::string& why){if(!value)throw std::runtime_error(why);}
Json manifest(){return Json::parse(R"({"version":1,"sites":[{"id":"upgrade","enabled":true,"origin":"https://upgrade.example","path_prefixes":["/site/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://upgrade.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-migration-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");}
std::vector<std::string> urls(const std::string& label,int count){std::vector<std::string> out;for(int i=0;i<count;++i)out.push_back(std::string(origin)+"/site/"+label+"/"+std::to_string(i));return out;}
Json map_args(const std::string& key,int count){auto out=Json::object(),seeds=Json::array();for(const auto& url:urls(key,count))seeds.push(Json::string(url));out.set("site_id","upgrade");out.set("seeds",seeds);out.set("idempotency_key",key);return out;}
struct Worker:WorkerPort {
  std::int64_t now=1700000000000;int calls=0;
  Json call(const std::string& method,const Json& p,int) override {
    ++calls;
    if(method=="fetch.extract"){
      auto r=Json::parse(R"({"status":"ok","html":"marker migrated body","text":"marker migrated body","content_type":"text/plain","title":"migration fixture","extractor":"independent-upgrade/1","extract_status":"ok","index_allowed":true,"http_status":200,"links":[],"metadata":{},"warnings":[],"redirects":[],"usage":{"external_requests":1,"content_bytes":20},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker migrated body","snippet_title":"migration fixture","directives":[],"diagnostics":[]}})");r.set("final_url",p.at("url"));return r;
    }
    check(method=="map.fetch","unexpected worker method");auto r=Json::object();r.set("status","ok");r.set("url",p.at("url"));r.set("final_url",p.at("url"));r.set("http_status",std::int64_t{200});r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":20})"));
    if(p.at("kind").str()=="robots"){
      auto robots=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","body_sha256":"","state":"rules","rules":[],"sitemaps":[],"diagnostics":[],"origin":"https://upgrade.example","fetched_at_ms":0,"expires_at_ms":0})");robots.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));robots.set("fetched_at_ms",now);robots.set("expires_at_ms",now+3600000);r.set("robots",robots);
    }else{
      auto discovery=Json::parse(R"({"document_type":"html","parse_status":"ok","candidates":[],"truncated":false,"diagnostics":{}})");discovery.set("document_url",p.at("url"));r.set("discovery",discovery);
      auto decision=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","origin":"https://upgrade.example","allowed":true,"reason":"robots_no_matching_rule"})");decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));r.set("robots_decision",decision);
    }return r;
  }
};
std::int64_t schema(Store& store){sqlite3_stmt* statement=nullptr;check(sqlite3_prepare_v2(store.raw_handle(),"PRAGMA user_version",-1,&statement,nullptr)==SQLITE_OK,"read schema prepare");check(sqlite3_step(statement)==SQLITE_ROW,"read schema row");auto result=sqlite3_column_int64(statement,0);sqlite3_finalize(statement);return result;}
std::string read_turn(Store& store,WebService& web){auto lease=store.lease_next_read();check(lease.has_value(),"missing read lease");web.execute_read(*lease);return lease->job_id;}
std::string map_turn(WebService& web){auto lease=web.lease_map();check(lease.has_value(),"missing map lease");web.execute_map(*lease);return lease->job_id;}
Json read_metadata(const std::filesystem::path& path){std::ifstream in(path);std::string value((std::istreambuf_iterator<char>(in)),{});return Json::parse(value);}
}
int main(int argc,char** argv){try{
  check(argc==4,"usage: migration_queue create|upgrade db metadata");const std::string mode=argv[1];Worker worker;Store store(argv[2],[&]{return worker.now;});WebService web(store,worker,{},SiteRegistry(manifest()));
  if(mode=="create"){
    check(schema(store)==7,"generator must use actual published schema 7");
    auto doc=store.ingest("alice",{std::string(origin)+"/private/kept","marker unchanged private body","text/plain","kept"});auto proof=store.evidence("alice",doc.at("snapshot_id").str(),0,6);auto session=store.search("alice",parse_query(Json::string("marker")));
    auto first=store.read_many_submit("alice",urls("read-first",2),"read-first",{});++worker.now;auto second=store.read_many_submit("alice",urls("read-second",2),"read-second",{});check(read_turn(store,web)==first.at("job_id").str(),"baseline oldest read dispatch");
    auto map_first=web.submit_map("alice",map_args("map-first",3));check(map_turn(web)==map_first.at("job_id").str(),"baseline robots dispatch");check(map_turn(web)==map_first.at("job_id").str(),"baseline first map body dispatch");++worker.now;auto map_second=web.submit_map("alice",map_args("map-second",1));
    auto meta=Json::object();meta.set("read_first",first.at("job_id"));meta.set("read_second",second.at("job_id"));meta.set("map_first",map_first.at("job_id"));meta.set("map_second",map_second.at("job_id"));meta.set("snapshot_id",doc.at("snapshot_id"));meta.set("evidence_id",proof.at("evidence_id"));meta.set("session_id",session.at("session_id"));meta.set("now",worker.now);meta.set("read_usage",store.job_get("alice",first.at("job_id").str()).at("usage"));meta.set("map_usage",store.job_get("alice",map_first.at("job_id").str()).at("usage"));std::ofstream(argv[3])<<meta.dump();
    check(worker.calls==3,"generator should make exactly three fake worker calls");std::cout<<"PASS published schema 7 generated real pending read/map jobs with immutable snapshot, evidence and frozen session; 3 fake calls\n";
  }else{
    check(mode=="upgrade","invalid mode");auto meta=read_metadata(argv[3]);worker.now=meta.at("now").num();check(schema(store)==8,"candidate scheduler migration schema expected");
    auto first=store.read_many_submit("alice",urls("read-first",2),"read-first",{}),map_first=web.submit_map("alice",map_args("map-first",3));
    check(first.at("reused").flag()&&first.at("job_id").str()==meta.at("read_first").str(),"read idempotency identity drifted");check(map_first.at("reused").flag()&&map_first.at("job_id").str()==meta.at("map_first").str(),"map idempotency identity drifted");check(worker.calls==0,"reusing old keys performed work");
    check(store.job_get("alice",meta.at("read_first").str()).at("usage").canonical()==meta.at("read_usage").canonical(),"read usage changed before dispatch");check(store.job_get("alice",meta.at("map_first").str()).at("usage").canonical()==meta.at("map_usage").canonical(),"map usage changed before dispatch");
    auto third=store.read_many_submit("alice",urls("read-third",1),"read-third",{});auto map_third=web.submit_map("alice",map_args("map-third",1));
    for(const auto& id:{meta.at("read_first").str(),meta.at("read_second").str(),third.at("job_id").str(),meta.at("read_second").str()})check(read_turn(store,web)==id,"migrated read queue/newcomer order incorrect");
    for(const auto& id:{meta.at("map_second").str(),meta.at("map_first").str(),map_third.at("job_id").str(),meta.at("map_first").str()})check(map_turn(web)==id,"existing map queue/counter/newcomer order incorrect");
    check(!store.lease_next_read()&&!web.lease_map(),"unexpected residual work");check(worker.calls==8,"upgrade replay or lost task produced wrong dispatch count");
    check(store.snapshot("alice",meta.at("snapshot_id").str()).at("canonical_text").str()=="marker unchanged private body","immutable snapshot changed");check(store.evidence_get("alice",meta.at("evidence_id").str()).at("quote").str()=="marker","evidence changed");auto frozen=store.page("alice",meta.at("session_id").str(),"",100);check(frozen.at("hits").size()==1&&frozen.at("hits").at(0).at("snapshot_id").str()==meta.at("snapshot_id").str(),"frozen import row changed");
    std::cout<<"PASS genuine schema 7 read queue upgrades: original IDs/usage, legacy order then durable rotation, new arrivals behind; 4 dispatched items\n";
    std::cout<<"PASS genuine schema 7 map queue upgrades: original dispatch counter/order, warm robots cache, no extra work on key reuse; 4 dispatched items\n";
    std::cout<<"PASS snapshot/evidence/frozen import preserved; no SQL data mutation used by harness; no real network\n";
  }return 0;
}catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}}
