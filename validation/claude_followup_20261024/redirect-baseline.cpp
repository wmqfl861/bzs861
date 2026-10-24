// Independent P2-2 baseline observation. No sockets, DB tampering, or sleeps.
// Links the unmodified core; the only Worker is this deterministic fixture.
// This is not an assertion that a redirect lacking per-hop cache policy is safe
// to reuse. It records the current limitation and the controls that must remain.
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace bzs;
namespace {
const std::string original="http://redirect.example/article";
const std::string final="https://redirect.example/article/";
constexpr std::int64_t epoch=1800000000000;
std::size_t assertions=0;
void check(bool value,const char* message) {
  ++assertions;if(!value)throw std::runtime_error(message);
}
struct Temp {
  std::filesystem::path directory=std::filesystem::temp_directory_path()/("bzs-p2-2-"+random_id());
  Temp(){std::filesystem::create_directories(directory);}
  ~Temp(){std::error_code error;std::filesystem::remove_all(directory,error);}
};
Json request(const std::string& url,const std::string& freshness="cache_first") {
  auto value=Json::object();value.set("url",url);value.set("freshness",freshness);return value;
}
struct Worker : WorkerPort {
  std::int64_t now=epoch;
  std::string profile=sha256("p2-2-fixture-profile");
  std::vector<Json> fetches;
  std::string control="max-age=3600";
  Json call(const std::string& method,const Json& parameters,int timeout) override {
    check(timeout>0,"RPC timeout must remain positive");
    if(method=="fetch.profile") {
      auto result=Json::object();result.set("version","bzs-http-freshness-1");
      result.set("request_profile",profile);return result;
    }
    check(method=="fetch.extract","unexpected Worker method");
    fetches.push_back(Json::parse(parameters.dump()));
    const auto url=parameters.at("url").str();
    const bool redirected=url==original;
    const auto target=redirected?final:url;
    auto result=Json::parse(R"({"status":"ok","http_status":200,"html":"<p>redirect fixture body</p>","text":"redirect fixture body","title":"fixture","content_type":"text/html","extractor":"p2-2-offline/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"redirect fixture body","snippet_title":"fixture","directives":[],"diagnostics":[]}})");
    result.set("final_url",target);
    if(redirected){auto chain=Json::array();chain.push(Json::string(target));result.set("redirects",chain);}
    auto metadata=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"fixture-v1\"","last_modified":"","cache_control":"max-age=3600","vary":"","date":"","age":"","expires":""})");
    metadata.set("url",target);metadata.set("request_profile",profile);
    // The existing Worker omits conditional metadata for no-store responses.
    if(control!="no-store")result.set("http_cache",metadata);
    auto freshness=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":0,"response_delay_ms":0,"invalid":false})");
    freshness.set("url",target);freshness.set("request_profile",profile);
    freshness.set("cache_control",control);freshness.set("received_at_ms",now);
    result.set("http_freshness",freshness);
    auto usage=Json::object();usage.set("external_requests",std::int64_t{redirected?2:1});
    usage.set("content_bytes",std::int64_t{64});result.set("usage",usage);
    return result;
  }
};
template<class F> std::string error_code(F run) {
  try{run();return "";}catch(const Error& error){return error.code;}
}

Json observe_redirect_and_direct_control() {
  Temp temp;Worker worker;Store store(temp.directory/"db",[&]{return worker.now;});WebService service(store,worker,{});
  const auto first=service.read("alice",request(original));
  ++worker.now;const auto second=service.read("alice",request(original));
  ++worker.now;service.read("alice",request(original,"refresh"));
  const auto count_before_cache_only=worker.fetches.size();
  const auto cached_error=error_code([&]{service.read("alice",request(original,"cache_only"));});
  auto result=Json::object();result.set("scenario","redirected_original_url_vs_explicit_final_url");
  result.set("first_from_cache",first.at("from_cache"));result.set("second_from_cache",second.at("from_cache"));
  result.set("original_fetch_extract_calls",static_cast<std::int64_t>(count_before_cache_only));
  result.set("original_cache_only_error",cached_error);
  result.set("original_has_revalidation_base",store.revalidation_base("alice",original).has_value());
  result.set("target_cached_before_explicit_read",store.cached("alice",final).has_value());
  auto observed=Json::array();for(const auto& parameters:worker.fetches){auto entry=Json::object();entry.set("url",parameters.at("url"));entry.set("has_conditional",parameters.has("conditional"));observed.push(entry);}result.set("original_dispatches",observed);
  check(!first.at("from_cache").flag()&&!second.at("from_cache").flag(),"baseline original unexpectedly reused redirect cache");
  check(count_before_cache_only==3&&worker.fetches.size()==3,"cache_only performed a fetch or baseline did not refetch");
  check(cached_error=="E_CACHE_STALE","baseline redirected cache_only error changed");
  check(!store.revalidation_base("alice",original),"baseline original unexpectedly gained a validator");
  for(const auto& parameters:worker.fetches)check(!parameters.has("conditional"),"baseline redirect sent final validator to original URL");
  check(!store.cached("alice",final),"baseline unexpectedly created a final-URL alias");

  service.read("alice",request(final));const auto direct=service.read("alice",request(final));
  result.set("direct_final_second_from_cache",direct.at("from_cache"));
  result.set("direct_final_has_revalidation_base",store.revalidation_base("alice",final).has_value());
  result.set("total_fetch_extract_calls",static_cast<std::int64_t>(worker.fetches.size()));
  result.set("other_owner_cache_only_error",error_code([&]{service.read("bob",request(final,"cache_only"));}));
  check(direct.at("from_cache").flag()&&worker.fetches.size()==4,"direct final URL cache control failed");
  check(store.revalidation_base("alice",final).has_value(),"direct final URL lost its validator control");
  check(result.at("other_owner_cache_only_error").str()=="E_NOT_CACHED","owner cache isolation failed");
  return result;
}
Json observe_no_store_and_url_guard() {
  Temp temp;Worker worker;worker.control="no-store";Store store(temp.directory/"db",[&]{return worker.now;});WebService service(store,worker,{});
  auto result=Json::object();result.set("scenario","terminal_no_store_and_nonpublic_input_controls");
  result.set("no_store_error",error_code([&]{service.read("alice",request(original));}));
  result.set("snapshots",store.stats("alice").at("snapshots"));
  const auto before=worker.fetches.size();
  result.set("private_input_error",error_code([&]{service.read("alice",request("http://127.0.0.1/private"));}));
  result.set("private_input_fetches",static_cast<std::int64_t>(worker.fetches.size()-before));
  check(result.at("no_store_error").str()=="E_CACHE_POLICY","terminal no-store was not rejected");
  check(result.at("snapshots").num()==0,"terminal no-store persisted a new body");
  check(!result.at("private_input_error").str().empty()&&worker.fetches.size()==before,"private input reached Worker");
  return result;
}
}
int main() {
  try {
    auto report=Json::object();report.set("baseline","44253a752f945fdfab9810192e583b39fe5f6d83");
    report.set("scope","offline deterministic Worker boundary and real Store; no network");
    report.set("per_hop_redirect_cache_policy_available",false);
    report.set("proves_safe_alias_implementation",false);
    auto cases=Json::array();cases.push(observe_redirect_and_direct_control());cases.push(observe_no_store_and_url_guard());
    report.set("cases",cases);report.set("assertions",static_cast<std::int64_t>(assertions));
    report.set("baseline_observation_reproduced",true);std::cout<<report.dump()<<'\n';return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
