#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/query.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <iostream>
#include <set>

using namespace bzs;
namespace {
constexpr std::int64_t now=1700000000000;
const std::string owner="review-owner";
const std::string original="https://route-review.example/start";
const std::string final_url="https://route-review.example/final";
Json policy(const std::string& target) {
  auto r=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"max-age=60","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":1700000000000,"response_delay_ms":0,"invalid":false})");
  r.set("url",target);r.set("request_profile",sha256("review-profile"));return r;
}
Json validators() {
  auto r=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"review-v1\"","last_modified":"","cache_control":"max-age=60","vary":"","date":"","age":"","expires":""})");
  r.set("url",final_url);r.set("request_profile",sha256("review-profile"));return r;
}
Json denied() {
  return Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex"],"diagnostics":[]})");
}
Json parameters(const std::string& target,const char* freshness="refresh") {
  auto r=Json::object();r.set("url",target);r.set("freshness",freshness);return r;
}
struct Worker:WorkerPort {
  bool refresh=false,prefix_noindex=false,final_noindex=false;
  int reads=0,routed_304=0;
  Json call(const std::string& method,const Json& p,int) override {
    if(method=="fetch.profile") {auto r=Json::object();r.set("version","bzs-http-freshness-1");r.set("request_profile",sha256("review-profile"));return r;}
    require(method=="fetch.extract","E_REVIEW","Unexpected worker method");++reads;
    const bool routed=p.at("url").str()==original;
    const bool not_modified=routed&&refresh;
    if(not_modified)require(p.has("redirect_conditional"),"E_REVIEW","Routed refresh did not supply its exact final-URI grant");
    auto r=not_modified?Json::object():Json::parse(R"({"html":"scopeprobe retained body","text":"scopeprobe retained body","title":"scopeprobe","content_type":"text/plain","extractor":"review/1","extract_status":"ok","index_allowed":true,"links":[],"warnings":[],"metadata":{},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"scopeprobe retained body","snippet_title":"scopeprobe","directives":[],"diagnostics":[]}})");
    r.set("status",not_modified?"not_modified":"ok");r.set("http_status",std::int64_t{not_modified?304:200});
    r.set("final_url",final_url);r.set("http_freshness",policy(final_url));r.set("http_cache",validators());
    auto route=Json::array();if(routed)route.push(Json::string(final_url));r.set("redirects",route);
    auto usage=Json::object();usage.set("external_requests",std::int64_t{routed?2:1});usage.set("content_bytes",std::int64_t{not_modified?0:24});r.set("usage",usage);
    if(routed) {
      auto hop=Json::object();hop.set("url",original);hop.set("target_url",final_url);hop.set("http_status",std::int64_t{301});hop.set("content_bytes",std::int64_t{0});hop.set("http_freshness",policy(original));
      auto hops=Json::array();hops.push(hop);auto chain=Json::object();chain.set("version","bzs-http-redirect-chain-1");chain.set("request_url",original);chain.set("final_url",final_url);chain.set("hops",hops);r.set("http_redirect_chain",chain);
      auto observed=Json::object();observed.set("url",original);observed.set("http_status",std::int64_t{301});observed.set("http_freshness",policy(original));auto observations=Json::array();observations.push(observed);r.set("http_redirect_observations",observations);
    }
    if(not_modified) {
      ++routed_304;const auto binding=p.at("redirect_conditional").at("conditional").at("binding_id");r.set("binding_id",binding);
      auto receipt=Json::object();receipt.set("version","bzs-http-redirect-conditional-1");receipt.set("request_url",original);receipt.set("final_url",final_url);receipt.set("binding_id",binding);r.set("redirect_revalidation",receipt);
      if(prefix_noindex) {
        auto observed=Json::object();observed.set("url",original);observed.set("http_status",std::int64_t{301});observed.set("publication",denied());auto observations=Json::array();observations.push(observed);r.set("redirect_publication_observations",observations);
      }
      if(final_noindex)r.set("publication_observation",denied());
    }
    return r;
  }
};
Json freeze(Store& store) {return store.search(owner,parse_query(Json::string("scopeprobe")));}
std::set<std::string> urls(Store& store,const Json& session) {
  auto hits=store.page(owner,session.at("session_id").str(),"",20).at("hits");std::set<std::string> result;
  for(std::size_t i=0;i<hits.size();++i)result.insert(hits.at(i).at("url").str());return result;
}
void run(const std::filesystem::path& path,bool preexisting) {
  Store store(path,[]{return now;});Worker worker;WebService web(store,worker,{});
  const auto a=web.read(owner,parameters(original));const auto c=web.read(owner,parameters(final_url));const auto frozen=freeze(store);
  require(urls(store,frozen)==std::set<std::string>{original,final_url},"E_REVIEW","Expected independent initial A and C results");
  worker.refresh=true;
  if(preexisting)store.observe_web_publication(owner,original,original,denied(),store.publication_fence(owner,original));
  else worker.prefix_noindex=true;
  const auto restricted=web.read(owner,parameters(original));
  require(!restricted.at("indexed").flag(),"E_REVIEW","A did not retain its own noindex restriction");
  require(urls(store,freeze(store))==std::set<std::string>{final_url},"E_REVIEW","A's restriction contaminated independent C current results");
  require(urls(store,frozen)==std::set<std::string>{final_url},"E_REVIEW","A's restriction retracted independent C frozen results");
  require(web.read(owner,parameters(final_url,"cache_only")).at("indexed").flag(),"E_REVIEW","Independent C cache was incorrectly restricted");
  worker.prefix_noindex=false;
  require(!web.read(owner,parameters(original)).at("indexed").flag(),"E_REVIEW","An allowing 304 lifted A's retained restriction");
  require(urls(store,freeze(store))==std::set<std::string>{final_url},"E_REVIEW","Later 304 contaminated independent C");
  worker.final_noindex=true;
  require(!web.read(owner,parameters(original)).at("indexed").flag(),"E_REVIEW","Final noindex was ignored");
  require(urls(store,freeze(store)).empty()&&urls(store,frozen).empty(),"E_REVIEW","C's own observed final noindex did not retract C");
  require(!web.read(owner,parameters(final_url,"cache_only")).at("indexed").flag(),"E_REVIEW","Final noindex did not tighten C cache projection");
  require(store.snapshot(owner,a.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body","E_REVIEW","A history was removed");
  require(store.snapshot(owner,c.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body","E_REVIEW","C history was removed");
  require(worker.reads==5&&worker.routed_304==3,"E_REVIEW","Unexpected extra origin request or retry");
  std::cout<<"PASS "<<(preexisting?"preexisting":"response")<<" prefix restriction: A denied; independent C preserved; final C denial enforced; immutable evidence retained\n";
}
}
int main() {
  const auto root=std::filesystem::temp_directory_path()/("bzs-scope-independent-"+random_id());int failed=0;
  for(bool preexisting:{false,true}) {
    try {run(root/(preexisting?"preexisting":"response"),preexisting);}
    catch(const std::exception& error) {++failed;std::cout<<"FAIL "<<(preexisting?"preexisting":"response")<<": "<<error.what()<<'\n';}
  }
  std::filesystem::remove_all(root);return failed?1:0;
}
