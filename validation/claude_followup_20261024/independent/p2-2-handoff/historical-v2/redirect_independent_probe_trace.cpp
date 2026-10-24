// Independent P2-2 interaction probe. Only public Store/WebService APIs;
// deterministic WorkerPort, temporary DB, no sockets, sleeps or SQL mutation.
// Reviewer authored historical shared-read code, but not P2-2 redirect code.
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
using namespace bzs;
namespace {
std::int64_t checks=0;
#define VERIFY(x) do {++checks; if(!(x)) throw std::runtime_error(std::string("assertion: ")+ #x);} while(false)
template<class F> void expect(F&& operation,const std::string& expected) {
  try {operation();} catch(const Error& error) {VERIFY(error.code==expected);return;}
  throw std::runtime_error("expected error "+expected);
}
Json copy(const Json& in) {return Json::parse(in.dump(),8*1024*1024);}
const std::string original="http://independent-chain.example/original";
const std::string middle="https://independent-chain.example/middle";
const std::string final_uri="https://independent-chain.example/final";
const std::string body="Independent immutable redirected body.";
const std::string title="Independent redirected artifact";
const std::string owner="independent-owner";
struct Temporary {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-independent-p2-2-"+random_id());
  Temporary() {std::filesystem::create_directories(path);}
  ~Temporary() {std::error_code ignored;std::filesystem::remove_all(path,ignored);}
};
Json policy(bool allowed) {
  Json p=Json::object();p.set("version","bzs-publication-1");
  p.set("index_allowed",allowed);p.set("snippet_allowed",allowed);p.set("follow_allowed",allowed);
  p.set("snippet_text",allowed?body:"");p.set("snippet_title",allowed?title:"");
  p.set("directives",allowed?Json::array():Json::parse(R"(["noindex","nosnippet","nofollow"])"));
  p.set("diagnostics",Json::array());return p;
}
Json request(const std::string& uri=original,const std::string& freshness="cache_first") {
  Json r=Json::object();r.set("url",uri);r.set("freshness",freshness);return r;
}
struct DeterministicWorker:WorkerPort {
  std::int64_t now=1700000000000;
  std::string profile=sha256("independent explicit request header profile");
  int fetches=0,profiles=0;
  std::vector<Json> calls;
  std::function<void()> on_fetch;
  bool force_full=false;
  Json freshness(const std::string& uri,std::int64_t seconds) {
    Json p=Json::object();p.set("version","bzs-http-freshness-1");p.set("url",uri);
    p.set("request_profile",profile);p.set("cache_control","max-age="+std::to_string(seconds));
    for(const char* field:{"vary","date","age","expires","pragma"})p.set(field,"");
    p.set("received_at_ms",now);p.set("response_delay_ms",std::int64_t{0});p.set("invalid",false);return p;
  }
  Json validators(const std::string& uri) {
    Json c=Json::object();c.set("version","bzs-http-revalidation-1");c.set("url",uri);
    c.set("request_profile",profile);c.set("etag","\"independent-body-v1\"");
    c.set("last_modified","");c.set("cache_control","max-age=120");
    for(const char* field:{"vary","date","age","expires"})c.set(field,"");return c;
  }
  Json call(const std::string& method,const Json& parameters,int timeout) override {
    VERIFY(timeout>0);
    if(method=="fetch.profile") {
      ++profiles;Json p=Json::object();p.set("version","bzs-http-freshness-1");p.set("request_profile",profile);return p;
    }
    VERIFY(method=="fetch.extract");++fetches;calls.push_back(copy(parameters));
    const auto uri=parameters.at("url").str();const bool routed=uri==original;
    const bool not_modified=routed&&!force_full&&parameters.has("redirect_conditional");
    auto hook=std::move(on_fetch);on_fetch={};if(hook)hook();
    Json response=Json::object();response.set("status",not_modified?"not_modified":"ok");
    response.set("http_status",std::int64_t{not_modified?304:200});
    response.set("final_url",routed?final_uri:uri);
    Json route=Json::array(),hops=Json::array(),observations=Json::array();
    std::int64_t redirect_bytes=0;
    if(routed) {
      const std::vector<std::string> targets={middle,final_uri};std::string from=original;
      for(std::size_t i=0;i<targets.size();++i) {
        const std::int64_t count=i==0?2:5;redirect_bytes+=count;
        auto p=freshness(from,i==0?30:60);Json hop=Json::object();
        hop.set("url",from);hop.set("target_url",targets[i]);hop.set("http_status",std::int64_t{302});
        hop.set("content_bytes",count);hop.set("http_freshness",p);hops.push(hop);
        Json observation=Json::object();observation.set("url",from);observation.set("http_status",std::int64_t{302});
        observation.set("http_freshness",p);observations.push(observation);route.push(Json::string(targets[i]));from=targets[i];
      }
      Json chain=Json::object();chain.set("version","bzs-http-redirect-chain-1");
      chain.set("request_url",original);chain.set("final_url",final_uri);chain.set("hops",hops);
      response.set("http_redirect_chain",chain);response.set("http_redirect_observations",observations);
    }
    response.set("redirects",route);response.set("http_freshness",freshness(routed?final_uri:uri,120));
    response.set("http_cache",validators(routed?final_uri:uri));
    Json usage=Json::object();usage.set("external_requests",std::int64_t{routed?3:1});
    usage.set("content_bytes",redirect_bytes+(not_modified?0:static_cast<std::int64_t>(body.size())));response.set("usage",usage);
    if(not_modified) {
      const auto grant=parameters.at("redirect_conditional");
      VERIFY(grant.at("request_url").str()==original);VERIFY(grant.at("redirects").canonical()==route.canonical());
      VERIFY(grant.at("conditional").at("url").str()==final_uri);
      VERIFY(grant.at("conditional").at("request_profile").str()==profile);
      const auto binding=grant.at("conditional").at("binding_id");response.set("binding_id",binding);
      Json receipt=Json::object();receipt.set("version","bzs-http-redirect-conditional-1");
      receipt.set("request_url",original);receipt.set("final_url",final_uri);receipt.set("binding_id",binding);
      response.set("redirect_revalidation",receipt);
    } else {
      response.set("html",body);response.set("text",body);response.set("title",title);
      response.set("content_type","text/plain");response.set("extractor","independent-probe/1");
      response.set("extract_status","ok");response.set("index_allowed",true);response.set("links",Json::array());
      response.set("warnings",Json::array());response.set("metadata",Json::object());response.set("publication",policy(true));
    }
    return response;
  }
};
Json import_alias(Store& store,const std::string& uri) {return store.ingest(owner,{uri,"Independently owned alias marker.","text/plain","Alias marker"});}
void preserved_body(Store& store,const std::string& snapshot) {
  VERIFY(store.snapshot(owner,snapshot).at("canonical_text").str()==body);
  VERIFY(store.range(owner,snapshot,0,512).at("text").str()==body);
}
void positive_and_owner_scope() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request());const auto sid=first.at("snapshot_id").str();
  VERIFY(!first.at("from_cache").flag());VERIFY(first.at("cache").at("redirect_hops").num()==2);
  VERIFY(web.read(owner,request(original,"cache_only")).at("from_cache").flag());
  VERIFY(worker.fetches==1);VERIFY(!store.cached(owner,middle));VERIFY(!store.cached(owner,final_uri));
  expect([&]{web.read("other-owner",request(original,"cache_only"));},"E_NOT_CACHED");VERIFY(worker.fetches==1);
  worker.now+=30000;
  expect([&]{web.read(owner,request(original,"cache_only"));},"E_CACHE_STALE");VERIFY(worker.fetches==1);
  auto result=web.read(owner,request());VERIFY(result.at("fetch").at("revalidated").flag());
  VERIFY(worker.fetches==2);VERIFY(worker.calls.back().has("redirect_conditional"));VERIFY(!worker.calls.back().has("conditional"));
  preserved_body(store,sid);
}
void every_alias_deletion_and_revival() {
  const std::vector<std::string> aliases={middle,final_uri,"https://INDEPENDENT-CHAIN.EXAMPLE:443/middle","https://independent-chain.example/%6Diddle","https://INDEPENDENT-CHAIN.EXAMPLE:443/%66inal"};
  for(const auto& alias:aliases) {
    std::cout<<"ALIAS "<<alias<<std::endl;
    Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
    const auto marker=import_alias(store,alias);const auto first=web.read(owner,request());const auto sid=first.at("snapshot_id").str();
    VERIFY(web.read(owner,request(original,"cache_only")).at("from_cache").flag());
    store.erase(owner,marker.at("document_id").str());
    expect([&]{web.read(owner,request(original,"cache_only"));},"E_CACHE_STALE");VERIFY(!store.revalidation_base(owner,original));
    preserved_body(store,sid);VERIFY(worker.fetches==1);
    import_alias(store,alias);
    expect([&]{web.read(owner,request(original,"cache_only"));},"E_CACHE_STALE");VERIFY(!store.revalidation_base(owner,original));
    preserved_body(store,sid);VERIFY(worker.fetches==1);
  }
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request());store.erase(owner,first.at("document_id").str());
  expect([&]{web.read(owner,request(original,"cache_only"));},"E_NOT_CACHED");VERIFY(!store.revalidation_base(owner,original));VERIFY(worker.fetches==1);
}
void inflight_alias_deletion_charges_known_usage() {
  for(const auto& alias:std::vector<std::string>{middle,"https://INDEPENDENT-CHAIN.EXAMPLE:443/%66inal"}) {
    std::cout<<"INFLIGHT_ALIAS "<<alias<<std::endl;
    Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
    const auto marker=import_alias(store,alias);const auto first=web.read(owner,request());
    const auto id=store.read_many_submit(owner,{original},"inflight-delete",{}).at("job_id").str();
    const auto lease=store.lease_next_read();VERIFY(lease.has_value());
    worker.on_fetch=[&]{store.erase(owner,marker.at("document_id").str());};web.execute_read(*lease);
    const auto job=store.job_get(owner,id),item=store.job_results(owner,id).at("items").at(0);
    std::cout<<"INFLIGHT_STATE "<<job.at("state").str()<<" ERROR "<<item.at("error_code").canonical()<<" REQUESTS "<<item.at("external_requests").num()<<std::endl;
    VERIFY(job.at("state").str()=="failed");VERIFY(item.at("error_code").str()=="E_TARGET_DELETED");
    VERIFY(item.at("external_requests").num()==3);VERIFY(item.at("content_bytes").num()==7);VERIFY(!item.at("usage_unknown").flag());
    VERIFY(job.at("usage").at("reserved_requests").num()==0);VERIFY(job.at("usage").at("reserved_bytes").num()==0);
    VERIFY(worker.fetches==2);preserved_body(store,first.at("snapshot_id").str());
  }
}
void final_history_is_not_relaxed_by_routed_304() {
  for(bool allow_new_final:{false,true}) {
    Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
    const auto first=web.read(owner,request());const auto sid=first.at("snapshot_id").str();
    web.read(owner,request(final_uri));
    store.observe_web_publication(owner,final_uri,final_uri,policy(false),store.publication_fence(owner,final_uri));
    if(allow_new_final) {
      ++worker.now;const auto new_final=web.read(owner,request(final_uri,"refresh"));VERIFY(new_final.at("indexed").flag());
      VERIFY(store.cached(owner,final_uri)->at("publication").at("index_allowed").flag());
    }
    ++worker.now;const auto renewal=web.read(owner,request(original,"refresh"));
    VERIFY(renewal.at("fetch").at("revalidated").flag());VERIFY(!renewal.at("indexed").flag());
    const auto cached=*store.cached(owner,original);const auto aggregate=cached.at("publication");
    const auto stored_final=cached.at("representation_publication");const auto represented=stored_final.has("version")?stored_final:aggregate;
    VERIFY(!aggregate.at("index_allowed").flag());VERIFY(!aggregate.at("snippet_allowed").flag());
    VERIFY(!represented.at("index_allowed").flag());VERIFY(!represented.at("snippet_allowed").flag());
    if(!allow_new_final) {VERIFY(!aggregate.at("follow_allowed").flag());VERIFY(!represented.at("follow_allowed").flag());}
    if(allow_new_final) VERIFY(store.cached(owner,final_uri)->at("publication").at("index_allowed").flag());
    preserved_body(store,sid);
  }
}
void shared_payer_cancel_keeps_single_routed_settlement() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request());const auto sid=first.at("snapshot_id").str();
  const auto payer=store.read_many_submit(owner,{original},"independent-payer",{}).at("job_id").str();
  const auto subscriber=store.read_many_submit(owner,{original},"independent-subscriber",{}).at("job_id").str();
  const auto foreign=store.read_many_submit("other-owner",{original},"independent-foreign",{}).at("job_id").str();
  const auto lease=store.lease_next_read();VERIFY(lease.has_value());VERIFY(lease->job_id==payer);
  worker.on_fetch=[&]{store.job_control(owner,payer,"cancel");};web.execute_read(*lease);
  const auto pj=store.job_get(owner,payer),sj=store.job_get(owner,subscriber);
  const auto pi=store.job_results(owner,payer).at("items").at(0),si=store.job_results(owner,subscriber).at("items").at(0);
  VERIFY(pj.at("state").str()=="cancelled");VERIFY(sj.at("state").str()=="succeeded");VERIFY(!pi.has("receipt"));VERIFY(si.has("receipt"));
  VERIFY(pj.at("usage").at("external_requests").num()==3);VERIFY(pj.at("usage").at("content_bytes").num()==7);
  VERIFY(sj.at("usage").at("external_requests").num()==0);VERIFY(sj.at("usage").at("content_bytes").num()==0);
  for(const auto& job:{pj,sj}) {VERIFY(job.at("usage").at("reserved_requests").num()==0);VERIFY(job.at("usage").at("reserved_bytes").num()==0);}
  VERIFY(si.at("shared_read").at("payer_job_id").str()==payer);VERIFY(si.at("shared_read").at("associated_external_requests").num()==3);
  VERIFY(si.at("shared_read").at("associated_content_bytes").num()==7);VERIFY(worker.fetches==2);
  VERIFY(store.job_get("other-owner",foreign).at("state").str()=="queued");
  const auto other=store.lease_next_read();VERIFY(other.has_value());VERIFY(other->job_id==foreign);web.execute_read(*other);
  VERIFY(!worker.calls.back().has("redirect_conditional"));VERIFY(worker.fetches==3);
  VERIFY(store.job_get("other-owner",foreign).at("usage").at("external_requests").num()==3);
  expect([&]{store.job_get("other-owner",subscriber);},"E_NOT_FOUND");preserved_body(store,sid);
}
void metadata_cas_preserves_winning_refresh() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request());const auto sid=first.at("snapshot_id").str();std::string winning_metadata;
  worker.on_fetch=[&]{++worker.now;web.read(owner,request(original,"refresh"));winning_metadata=store.cached(owner,original)->at("meta").canonical();};
  expect([&]{web.read(owner,request(original,"refresh"));},"E_REVALIDATION_STALE");
  VERIFY(worker.fetches==3);VERIFY(store.cached(owner,original)->at("meta").canonical()==winning_metadata);
  VERIFY(web.read(owner,request(original,"cache_only")).at("from_cache").flag());VERIFY(worker.fetches==3);preserved_body(store,sid);
}
}
int main() {
  int failures=0;
  const std::vector<std::pair<std::string,std::function<void()>>> cases={
    {"positive_owner_and_prefix_expiry",positive_and_owner_scope},
    {"all_alias_deletions_and_revival",every_alias_deletion_and_revival},
    {"inflight_alias_delete_known_usage",inflight_alias_deletion_charges_known_usage},
    {"final_history_304_is_monotone",final_history_is_not_relaxed_by_routed_304},
    {"shared_payer_cancel_routed_settlement",shared_payer_cancel_keeps_single_routed_settlement},
    {"same_snapshot_metadata_cas",metadata_cas_preserves_winning_refresh}};
  for(const auto& [name,run]:cases) {
    std::cout<<"RUN "<<name<<std::endl;
    try {run();std::cout<<"PASS "<<name<<std::endl;}
    catch(const Error& error) {++failures;std::cout<<"FAIL "<<name<<" "<<error.code<<" "<<error.what()<<std::endl;}
    catch(const std::exception& error) {++failures;std::cout<<"FAIL "<<name<<" "<<error.what()<<std::endl;}
  }
  std::cout<<"SUMMARY cases="<<cases.size()<<" checks="<<checks<<" failures="<<failures<<" external_network_requests=0"<<std::endl;
  return failures?2:0;
}
