#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <iostream>

using namespace bzs;
namespace {
constexpr std::int64_t now=1700000000000;
const std::string original="https://route-review.example/start";
const std::string target="https://route-review.example/failing-target";
Json policy(const std::string& url) {
  auto r=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"max-age=60","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":1700000000000,"response_delay_ms":0,"invalid":false})");
  r.set("url",url);r.set("request_profile",sha256("review-profile"));return r;
}
Json parameters(const char* freshness) {
  auto r=Json::object();r.set("url",original);r.set("freshness",freshness);return r;
}
struct Worker:WorkerPort {
  int reads=0;
  Json call(const std::string& method,const Json&,int) override {
    if(method=="fetch.profile") {auto r=Json::object();r.set("version","bzs-http-freshness-1");r.set("request_profile",sha256("review-profile"));return r;}
    require(method=="fetch.extract","E_REVIEW","Unexpected worker method");
    if(++reads==1) {
      auto r=Json::parse(R"({"status":"ok","http_status":200,"html":"retained direct body","text":"retained direct body","title":"direct","content_type":"text/plain","extractor":"review/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":20}})");
      r.set("final_url",original);r.set("http_freshness",policy(original));return r;
    }
    // One current 301 response, then a complete empty 503 response. Both raw
    // policies have a positive TTL; the new route itself invalidates the old
    // direct representation even though final extraction cannot replace it.
    auto r=Json::parse(R"({"status":"http_error","http_status":503,"error":"HTTP 503","usage":{"external_requests":2,"content_bytes":0}})");
    auto route=Json::array();route.push(Json::string(target));r.set("redirects",route);
    r.set("final_url",target);r.set("http_freshness",policy(target));
    auto observed=Json::object();observed.set("url",original);observed.set("http_status",std::int64_t{301});observed.set("http_freshness",policy(original));
    auto observations=Json::array();observations.push(observed);r.set("http_redirect_observations",observations);
    auto hop=Json::object();hop.set("url",original);hop.set("target_url",target);hop.set("http_status",std::int64_t{301});hop.set("content_bytes",std::int64_t{0});hop.set("http_freshness",policy(original));
    auto hops=Json::array();hops.push(hop);
    auto chain=Json::object();chain.set("version","bzs-http-redirect-chain-1");chain.set("request_url",original);chain.set("final_url",target);chain.set("hops",hops);r.set("http_redirect_chain",chain);
    return r;
  }
};
}
int main() {
  const auto path=std::filesystem::temp_directory_path()/("bzs-route-independent-"+random_id());
  int result=1;
  try {
    Store store(path,[]{return now;});Worker worker;WebService web(store,worker,{});
    const auto first=web.read("review-owner",parameters("cache_first"));
    bool failed_refresh=false;
    try { web.read("review-owner",parameters("refresh")); }
    catch(const Error& error) { failed_refresh=error.code=="E_FETCH_HTTP";std::cout<<"refresh="<<error.code<<'\n'; }
    require(failed_refresh,"E_REVIEW","Expected the actual target's HTTP failure");
    try {
      const auto cached=web.read("review-owner",parameters("cache_only"));
      std::cout<<"FAIL cache_only accepted superseded direct body; from_cache="<<cached.at("from_cache").flag()<<'\n';
    } catch(const Error& error) {
      require(error.code=="E_CACHE_STALE","E_REVIEW","Unexpected cache-only error");
      require(store.snapshot("review-owner",first.at("snapshot_id").str()).at("canonical_text").str()=="retained direct body","E_REVIEW","Historical evidence was removed");
      require(worker.reads==2,"E_REVIEW","Cache-only performed an origin request");
      std::cout<<"PASS old HTTP reuse invalidated, history retained, no extra request\n";result=0;
    }
  } catch(const std::exception& error) { std::cout<<"ERROR "<<error.what()<<'\n'; }
  std::filesystem::remove_all(path);
  return result;
}
