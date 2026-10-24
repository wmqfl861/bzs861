#include "bzs/web.hpp"
#include "bzs/http_cache.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace bzs;
namespace {
int assertions=0;
#define CHECK(x) do { ++assertions;if(!(x))throw std::runtime_error(std::string("check failed: ")+ #x); } while(false)
std::vector<std::pair<std::string,std::function<void()>>> cases;
#define TEST(name) void name();struct name##_registration {name##_registration(){cases.emplace_back(#name,name);}} name##_register;void name()
template<class F> void rejects(F f,const std::string& code){try{f();}catch(const Error& e){CHECK(e.code==code);return;}throw std::runtime_error("expected "+code);}
struct Temp { std::filesystem::path path=std::filesystem::temp_directory_path()/random_id();Temp(){std::filesystem::create_directories(path);}~Temp(){std::filesystem::remove_all(path);} };
const std::string url="https://cache.example/page";
constexpr std::int64_t epoch=1700000000000;
Json policy(std::string control="max-age=60") {
  auto r=Json::parse(R"({"version":"bzs-http-freshness-1","url":"https://cache.example/page","request_profile":"","cache_control":"","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":1700000000000,"response_delay_ms":0,"invalid":false})");
  r.set("request_profile",sha256("profile"));r.set("cache_control",control);return r;
}
struct Worker:WorkerPort {
  std::int64_t now=epoch;int requests=0,profiles=0;Json headers=policy();bool fail=false,omit=false;
  std::string profile=sha256("profile");std::function<void()> during;
  Json call(const std::string& method,const Json& params,int)override {
    if(method=="fetch.profile"){++profiles;auto r=Json::object();r.set("version","bzs-http-freshness-1");r.set("request_profile",profile);return r;}
    CHECK(method=="fetch.extract");++requests;if(during)during();
    if(fail)return Json::parse(R"({"status":"network_error","error":"fixture failure"})");
    auto r=Json::parse(R"({"status":"ok","http_status":200,"html":"cache fixture body","text":"cache fixture body","title":"cache fixture","content_type":"text/plain","extractor":"fixture/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{}})");
    r.set("final_url",params.at("url"));
    if(!omit){auto h=Json::parse(headers.dump());h.set("url",params.at("url"));h.set("request_profile",profile);h.set("received_at_ms",now);r.set("http_freshness",h);}
    r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":18})"));return r;
  }
};
Json args(const char* freshness="cache_first"){auto r=Json::object();r.set("url",url);r.set("freshness",freshness);return r;}
TEST(age_date_delay_and_residence_shorten_lifetime) {
  auto r=policy();r.set("date","Tue, 14 Nov 2023 22:13:20 GMT");r.set("age","20");r.set("response_delay_ms",std::int64_t{500});
  auto s=http_freshness(r,epoch+10000,86400);CHECK(s.at("current_age_ms").num()==30500);CHECK(s.at("fresh").flag());
  CHECK(!http_freshness(r,epoch+39500,86400).at("fresh").flag());
  CHECK(!http_freshness(r,epoch,10).at("fresh").flag());
}
TEST(expires_and_explicit_zero_are_hard_ceilings) {
  auto r=policy("public");r.set("date","Tue, 14 Nov 2023 22:13:20 GMT");r.set("expires","Tue, 14 Nov 2023 22:13:25 GMT");
  CHECK(http_freshness(r,epoch+4999,86400).at("fresh").flag());CHECK(!http_freshness(r,epoch+5000,86400).at("fresh").flag());
  CHECK(!http_freshness(policy("max-age=0"),epoch,86400).at("fresh").flag());CHECK(!http_freshness(policy(),epoch,0).at("fresh").flag());
}
TEST(restrictive_and_malformed_fields_never_gain_default_freshness) {
  for(const auto& control:{"no-cache","no-cache=\"ETag, Date\"","max-age=0","must-understand"})CHECK(!http_freshness(policy(control),epoch,86400).at("fresh").flag());
  for(const auto& control:{"no-store","max-age=1,max-age=2","max-age=-1","max-age=abc","public,,max-age=60"})CHECK(!http_freshness(policy(control),epoch,86400).at("store_allowed").flag());
  for(const auto& key:{"date","expires","age","vary"}){auto r=policy();r.set(key,"broken");CHECK(!http_freshness(r,epoch,86400).at("fresh").flag());}
  auto r=policy("");r.set("pragma","no-cache");CHECK(!http_freshness(r,epoch,86400).at("fresh").flag());
  CHECK(!http_freshness(Json(),epoch,86400).at("fresh").flag());
}
TEST(vary_accept_encoding_is_the_only_supported_nonempty_selection) {
  for(const auto* value:{"Accept-Encoding","aCcEpT-EnCoDiNg"," Accept-Encoding , ACCEPT-ENCODING "}) {
    auto r=policy();r.set("vary",value);const auto status=http_freshness(r,epoch+1000,86400);
    CHECK(status.at("fresh").flag());CHECK(status.at("store_allowed").flag());
  }
  for(const auto* value:{" ",",",",Accept-Encoding","Accept-Encoding,","Accept-Encoding,,Accept-Encoding",
                         "Accept-Encoding, ,Accept-Encoding","*","Accept-Encoding,*","Accept-Encoding,Cookie",
                         "Accept-Encoding,Accept-Language","Accept-Encoding;mode=fast","\"Accept-Encoding\""}) {
    auto r=policy();r.set("vary",value);const auto status=http_freshness(r,epoch,86400);
    CHECK(!status.at("fresh").flag());CHECK(status.at("reason").str()=="unsupported_vary");
  }
}
TEST(accept_encoding_does_not_override_other_cache_restrictions) {
  for(const auto* control:{"no-cache","max-age=0","must-understand"}) {
    auto r=policy(control);r.set("vary","Accept-Encoding");CHECK(!http_freshness(r,epoch,86400).at("fresh").flag());
  }
  auto r=policy("no-store");r.set("vary","Accept-Encoding");const auto status=http_freshness(r,epoch,86400);
  CHECK(!status.at("store_allowed").flag());CHECK(status.at("reason").str()=="no_store");
}
TEST(accept_encoding_cache_reuse_keeps_owner_and_request_profile_fences) {
  Temp t;Worker w;w.headers.set("vary","Accept-Encoding, ACCEPT-ENCODING");Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  const auto first=web.read("a",args());CHECK(!first.at("from_cache").flag());
  const auto hit=web.read("a",args("cache_only"));CHECK(hit.at("from_cache").flag());CHECK(hit.at("snapshot_id").str()==first.at("snapshot_id").str());
  CHECK(web.read("a",args()).at("from_cache").flag());CHECK(w.requests==1);
  rejects([&]{web.read("b",args("cache_only"));},"E_NOT_CACHED");
  w.profile=sha256("changed Accept-Encoding");rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==1);
  CHECK(!web.read("a",args()).at("from_cache").flag());CHECK(w.requests==2);
}
TEST(private_owner_cache_and_quoted_ages_remain_bounded) {
  CHECK(http_freshness(policy("private, max-age=\"60\""),epoch+1000,86400).at("fresh").flag());
  CHECK(!http_freshness(policy("max-age=60,s-maxage=10"),epoch+10000,86400).at("fresh").flag());
  CHECK(!http_freshness(policy(),epoch-1,86400).at("fresh").flag());
}
TEST(no_validator_page_uses_origin_max_age_and_never_implicit_stale_fallback) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",args());CHECK(web.read("a",args()).at("from_cache").flag());CHECK(w.requests==1);
  w.now+=60000;w.fail=true;rejects([&]{web.read("a",args());},"E_FETCH_NETWORK");CHECK(w.requests==2);
  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);
}
TEST(no_cache_and_zero_age_force_fresh_authorized_fetch) {
  for(const auto* control:{"no-cache","max-age=0"}){Temp t;Worker w;w.headers=policy(control);Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",args());web.read("a",args());CHECK(w.requests==2);rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);}
}
TEST(no_store_never_persists_new_body_and_fences_old_http_reuse) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  const auto old=web.read("a",args());w.headers=policy("no-store");
  rejects([&]{web.read("a",args("refresh"));},"E_CACHE_POLICY");CHECK(s.stats("a").at("snapshots").num()==1);
  CHECK(s.snapshot("a",old.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");
  rejects([&]{web.read("b",args());},"E_CACHE_POLICY");CHECK(!s.cached("b",url));
}
TEST(old_worker_metadata_requires_refresh_and_no_cache_only_network) {
  Temp t;Worker w;w.omit=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",args());web.read("a",args());CHECK(w.requests==2);
  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2&&w.profiles==0);
}
TEST(restart_owner_and_changed_request_profile_do_not_share_cache) {
  Temp t;Worker w;{Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",args());}
  Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});CHECK(web.read("a",args("cache_only")).at("from_cache").flag());CHECK(w.requests==1);
  rejects([&]{web.read("b",args("cache_only"));},"E_NOT_CACHED");w.profile=sha256("new language");
  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==1);
  CHECK(!web.read("a",args()).at("from_cache").flag());CHECK(w.requests==2);
}
struct SlowProfileWorker:Worker {
  bool slow=false;
  Json call(const std::string& method,const Json& params,int timeout)override {
    if(method=="fetch.profile"&&slow) { now+=60001;slow=false; }
    return Worker::call(method,params,timeout);
  }
};
TEST(cache_only_rechecks_expiry_after_profile_rpc) {
  Temp t;SlowProfileWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",args());w.slow=true;
  rejects([&]{web.read("a",args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==1);
}
TEST(cache_first_fetches_if_profile_rpc_outlasts_freshness) {
  Temp t;SlowProfileWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",args());w.slow=true;
  CHECK(!web.read("a",args()).at("from_cache").flag());CHECK(w.requests==2);
}
TEST(late_invalidation_cannot_clear_newer_representation) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",args());auto old=*s.cached("a",url);
  ++w.now;web.read("a",args("refresh"));s.invalidate_web_cache("a",url,old.at("snapshot_id").str(),sha256(old.at("meta").canonical()));
  CHECK(web.read("a",args("cache_only")).at("from_cache").flag());
}
TEST(merge_304_preserves_omitted_restrictions_and_refreshes_time) {
  auto old=policy("max-age=10"),update=policy("");old.set("vary","Accept-Encoding");update.set("received_at_ms",epoch+10000);
  auto merged=merge_http_freshness(old,update);CHECK(merged.at("cache_control").str()=="max-age=10");CHECK(merged.at("vary").str()=="Accept-Encoding");CHECK(http_freshness(merged,epoch+10001,86400).at("fresh").flag());
  update.set("request_profile",sha256("other"));rejects([&]{merge_http_freshness(old,update);},"E_WORKER_PROTOCOL");
}
// P2-2 regressions in the existing cache_freshness native suite.
const std::string redirect_original="http://cache.example/start";
Json redirect_args(const char* freshness="cache_first") { auto r=args(freshness);r.set("url",redirect_original);return r; }
struct RedirectWorker:Worker {
  std::string prefix_control="max-age=10";int hops=1;bool legacy=false,force200=false;
  std::vector<Json> calls;std::function<void()> during_profile;
  std::function<Json(const Json&,Json)> alter;
  Json call(const std::string& method,const Json& parameters,int timeout)override {
    if(method=="fetch.profile") {if(during_profile)during_profile();return Worker::call(method,parameters,timeout);}
    if(parameters.at("url").str()!=redirect_original)return Worker::call(method,parameters,timeout);
    ++requests;calls.push_back(Json::parse(parameters.dump()));if(during)during();
    const bool conditional=!force200&&parameters.has("redirect_conditional")&&
      parameters.at("redirect_conditional").at("conditional").at("request_profile").str()==profile;
    auto r=conditional?Json::object():Json::parse(R"({"html":"cache fixture body","text":"cache fixture body","title":"cache fixture","content_type":"text/plain","extractor":"fixture/1","extract_status":"ok","index_allowed":true,"links":[],"warnings":[],"metadata":{}})");
    r.set("status",conditional?"not_modified":"ok");r.set("http_status",std::int64_t{conditional?304:200});r.set("final_url",url);
    auto chain=Json::object(),route=Json::array(),proof=Json::array(),observations=Json::array();std::string previous=redirect_original;
    for(int i=0;i<hops;++i) {
      const auto target=i+1==hops?url:"https://cache.example/hop-"+std::to_string(i);
      auto h=policy(prefix_control);h.set("url",previous);h.set("received_at_ms",now);h.set("request_profile",profile);
      auto hop=Json::object();hop.set("url",previous);hop.set("target_url",target);hop.set("http_status",std::int64_t{301});hop.set("content_bytes",std::int64_t{3});hop.set("http_freshness",h);proof.push(hop);route.push(Json::string(target));
      auto observed=Json::object();observed.set("url",previous);observed.set("http_status",std::int64_t{301});observed.set("http_freshness",h);observations.push(observed);previous=target;
    }
    r.set("redirects",route);
    chain.set("version","bzs-http-redirect-chain-1");chain.set("request_url",redirect_original);chain.set("final_url",url);chain.set("hops",proof);
    if(!legacy) {r.set("http_redirect_chain",chain);r.set("http_redirect_observations",observations);}
    auto final=Json::parse(headers.dump());final.set("url",url);final.set("received_at_ms",now);final.set("request_profile",profile);r.set("http_freshness",final);
    auto cache=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"redirect-v1\"","last_modified":"","cache_control":"max-age=60","vary":"","date":"","age":"","expires":""})");cache.set("url",url);cache.set("request_profile",profile);r.set("http_cache",cache);
    auto usage=Json::object();usage.set("external_requests",std::int64_t{hops+1});usage.set("content_bytes",std::int64_t{3*hops+(conditional?0:18)});r.set("usage",usage);
    if(conditional) {
      const auto binding=parameters.at("redirect_conditional").at("conditional").at("binding_id");r.set("binding_id",binding);
      auto receipt=Json::object();receipt.set("version","bzs-http-redirect-conditional-1");receipt.set("request_url",redirect_original);receipt.set("final_url",url);receipt.set("binding_id",binding);r.set("redirect_revalidation",receipt);
    }
    return alter?alter(parameters,r):r;
  }
};
TEST(http_redirect_proof_caches_original_without_creating_a_global_alias) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  auto first=web.read("a",redirect_args());CHECK(!first.at("from_cache").flag());CHECK(first.at("cache").at("redirect_hops").num()==1);
  CHECK(web.read("a",redirect_args()).at("from_cache").flag());CHECK(web.read("a",redirect_args("cache_only")).at("from_cache").flag());CHECK(w.requests==1);
  CHECK(!s.cached("a",url));rejects([&]{web.read("b",redirect_args("cache_only"));},"E_NOT_CACHED");
  web.read("a",args());CHECK(web.read("a",args()).at("from_cache").flag());CHECK(w.requests==2);
}
TEST(http_redirect_proof_survives_restart_and_expires_at_prefix_intersection) {
  Temp t;RedirectWorker w;{Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());}
  Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});w.now+=9999;CHECK(web.read("a",redirect_args("cache_only")).at("from_cache").flag());
  ++w.now;rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==1);
  auto renewed=web.read("a",redirect_args());CHECK(!renewed.at("from_cache").flag());CHECK(renewed.at("fetch").at("revalidated").flag());CHECK(w.calls.back().has("redirect_conditional"));CHECK(!w.calls.back().has("conditional"));
  CHECK(w.calls.back().at("url").str()==redirect_original);CHECK(w.calls.back().at("redirect_conditional").at("conditional").at("url").str()==url);
}
TEST(http_redirect_profile_changes_require_a_complete_attempt) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());w.profile=sha256("changed-profile");
  rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==1);
  auto fresh=web.read("a",redirect_args());CHECK(fresh.at("fetch").at("http_status").num()==200);CHECK(w.requests==2);
}
TEST(legacy_redirects_without_proof_keep_original_conservative_contract) {
  Temp t;RedirectWorker w;w.legacy=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",redirect_args());web.read("a",redirect_args());CHECK(w.requests==2);CHECK(!s.revalidation_base("a",redirect_original));
  CHECK(!w.calls.back().has("redirect_conditional")&&!w.calls.back().has("conditional"));rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");
}
TEST(http_redirect_each_hop_requires_explicit_cacheable_lifetime) {
  for(const auto* control:{"","no-cache","max-age=0"}) {
    Temp t;RedirectWorker w;w.prefix_control=control;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    web.read("a",redirect_args());web.read("a",redirect_args());CHECK(w.requests==2);CHECK(!s.cached("a",redirect_original)->at("meta").has("http_redirect_chain"));CHECK(!s.revalidation_base("a",redirect_original));
    rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");
  }
}
TEST(http_redirect_no_store_creates_no_new_body_and_invalidates_bound_old_reuse) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());w.prefix_control="no-store";
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_CACHE_POLICY");CHECK(s.stats("a").at("snapshots").num()==1);
  CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
  CHECK(!s.cached("a",redirect_original)->at("meta").has("http_redirect_chain"));rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");
  rejects([&]{web.read("b",redirect_args());},"E_CACHE_POLICY");CHECK(s.stats("b").at("snapshots").num()==0);
}
TEST(http_redirect_304_batch_accounts_for_every_request_and_redirect_body) {
  Temp t;RedirectWorker w;w.hops=2;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());
  const auto id=s.read_many_submit("a",{redirect_original},"redirect-304",{}).at("job_id").str();auto lease=s.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);
  const auto job=s.job_get("a",id);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("usage").at("external_requests").num()==3);CHECK(job.at("usage").at("content_bytes").num()==6);CHECK(job.at("usage").at("reserved_bytes").num()==0);
  CHECK(s.cached("a",redirect_original)->at("canonical_text").str()=="cache fixture body");CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
}
TEST(http_redirect_target_deletion_blocks_reuse_but_not_owned_history) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());auto target=web.read("a",args());s.erase("a",target.at("document_id").str());
  rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(!s.revalidation_base("a",redirect_original));
  CHECK(s.range("a",first.at("snapshot_id").str(),0,100).at("text").str()=="cache fixture body");CHECK(w.requests==2);
}
TEST(http_redirect_target_deletion_during_profile_rpc_is_checked_at_hit) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());auto target=web.read("a",args());bool done=false;
  w.during_profile=[&]{if(!done){done=true;s.erase("a",target.at("document_id").str());}};
  rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);
}
TEST(http_redirect_304_same_snapshot_metadata_race_is_not_retried) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());bool nested=false;
  w.during=[&]{if(!nested){nested=true;++w.now;web.read("a",redirect_args("refresh"));}};
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_REVALIDATION_STALE");CHECK(w.requests==3);CHECK(web.read("a",redirect_args("cache_only")).at("from_cache").flag());
}
TEST(http_redirect_304_cannot_erase_inflight_final_publication_restriction) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());bool observed=false;
  const auto denied=Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":false,"snippet_text":"","snippet_title":"","directives":["noindex","nosnippet","nofollow"],"diagnostics":[]})");
  w.during=[&]{if(!observed){observed=true;s.observe_web_publication("a",redirect_original,url,denied,s.publication_fence("a",redirect_original));}};
  auto result=web.read("a",redirect_args("refresh"));CHECK(!result.at("indexed").flag());CHECK(!s.cached("a",redirect_original)->at("publication").at("follow_allowed").flag());
  CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
}
TEST(http_redirect_changed_prefix_never_accepts_a_late_final_304) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());w.hops=2;
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_WORKER_PROTOCOL");CHECK(w.requests==2);CHECK(!s.cached("a",redirect_original)->at("meta").has("http_redirect_chain"));
  rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");
}
Json as_direct_response(const Json& response) {
  auto direct=Json::object();
  for(const auto& key:response.keys())if(key!="http_redirect_chain"&&key!="http_redirect_observations")direct.set(key,response.at(key));
  direct.set("final_url",redirect_original);direct.set("redirects",Json::array());
  direct.at("http_freshness").set("url",redirect_original);direct.at("http_cache").set("url",redirect_original);
  direct.at("usage").set("external_requests",std::int64_t{1});direct.at("usage").set("content_bytes",std::int64_t{18});
  return direct;
}
TEST(http_redirect_failed_new_route_invalidates_previously_direct_cache) {
  for(const auto* status:{"http_error","unsupported_content"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[&](const Json&,Json response) {
      if(w.requests==1)return as_direct_response(response);
      response.set("status",status);response.set("http_status",std::int64_t{std::string(status)=="http_error"?503:200});
      response.set("error","new target could not produce a replacement");return response;
    };
    const auto first=web.read("a",redirect_args());CHECK(web.read("a",redirect_args("cache_only")).at("from_cache").flag());
    CHECK(!s.cached("a",redirect_original)->at("meta").has("http_redirect_chain"));
    rejects([&]{web.read("a",redirect_args("refresh"));},std::string(status)=="http_error"?"E_FETCH_HTTP":"E_UNSUPPORTED_CONTENT");
    rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);
    CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
    CHECK(s.stats("a").at("snapshots").num()==1);
  }
}
TEST(http_redirect_late_route_failure_cannot_clear_new_direct_metadata) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});bool nested=false,renewed=false;
  w.alter=[&](const Json&,Json response) {
    if(w.requests==1||nested)return as_direct_response(response);
    response.set("status","http_error");response.set("http_status",std::int64_t{503});response.set("error","late route failure");return response;
  };
  const auto first=web.read("a",redirect_args());const auto old_meta=s.cached("a",redirect_original)->at("meta").canonical();
  w.during=[&] {
    if(!renewed) {renewed=true;nested=true;++w.now;const auto fresh=web.read("a",redirect_args("refresh"));CHECK(fresh.at("snapshot_id").str()==first.at("snapshot_id").str());nested=false;}
  };
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_FETCH_HTTP");CHECK(w.requests==3);
  CHECK(s.cached("a",redirect_original)->at("meta").canonical()!=old_meta);
  const auto hit=web.read("a",redirect_args("cache_only"));CHECK(hit.at("from_cache").flag());CHECK(hit.at("snapshot_id").str()==first.at("snapshot_id").str());CHECK(w.requests==3);
}
Json redirect_allowing_publication() {
  return Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"cache fixture body","snippet_title":"cache fixture","directives":[],"diagnostics":[]})");
}
Json redirect_noindex() {
  return Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex"],"diagnostics":[]})");
}
TEST(http_redirect_intermediate_policy_never_becomes_independent_final_policy) {
  for(const auto* timing:{"response","before_dispatch","inflight"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[](const Json&,Json response) {response.set("publication",redirect_allowing_publication());return response;};
    const auto first=web.read("a",redirect_args());CHECK(s.cached("a",redirect_original)->at("representation_publication").size()==0);
    const auto final=web.read("a",args());const auto query=parse_query(Json::string("cache"));
    const auto frozen=s.search("a",query);CHECK(s.page("a",frozen.at("session_id").str(),"",20).at("hits").size()==2);
    const auto restrict_original=[&] {s.observe_web_publication("a",redirect_original,redirect_original,redirect_noindex(),s.publication_fence("a",redirect_original));};
    if(std::string(timing)=="before_dispatch")restrict_original();
    bool restricted=false;
    w.during=[&] {if(std::string(timing)=="inflight"&&!restricted){restricted=true;restrict_original();}};
    w.alter=[&](const Json&,Json response) {
      if(std::string(timing)=="response") {
        auto record=Json::object(),observations=Json::array();record.set("url",redirect_original);record.set("http_status",std::int64_t{301});record.set("publication",redirect_noindex());observations.push(record);
        response.set("redirect_publication_observations",observations);
      }
      return response;
    };
    // Repeat to verify the first restricted 304 did not persist a poisoned final base.
    for(int refresh=0;refresh<2;++refresh) {
      CHECK(!web.read("a",redirect_args("refresh")).at("indexed").flag());
      const auto current=s.search("a",query);
      for(const auto& session:{frozen,current}) {
        const auto hits=s.page("a",session.at("session_id").str(),"",20).at("hits");
        CHECK(hits.size()==1);CHECK(hits.at(std::size_t{0}).at("url").str()==url);CHECK(!hits.at(std::size_t{0}).at("snippet").str().empty());
      }
      const auto base=s.revalidation_base("a",redirect_original);CHECK(base.has_value());CHECK(base->parsed.representation_publication.at("index_allowed").flag());
      CHECK(web.read("a",args("cache_only")).at("indexed").flag());
    }
    CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
    CHECK(s.snapshot("a",final.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
    // A real restriction on the final response still withdraws both identities.
    w.alter=[](const Json&,Json response) {response.set("publication_observation",redirect_noindex());return response;};
    CHECK(!web.read("a",redirect_args("refresh")).at("indexed").flag());
    CHECK(s.page("a",frozen.at("session_id").str(),"",20).at("hits").size()==0);
    CHECK(s.search("a",query).at("frozen_count").num()==0);
  }
}
TEST(http_redirect_original_site_ceiling_still_limits_only_its_aggregate) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});
  const auto sites=Json::parse(R"({"version":1,"sites":[{"id":"prefix-only","enabled":true,"origin":"http://cache.example","path_prefixes":["/start"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://example.org/offline-terms","cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved_by":"offline-chain-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
  WebService web(s,w,{},SiteRegistry(sites));
  w.alter=[](const Json&,Json response) {response.set("publication",redirect_allowing_publication());return response;};
  web.read("a",redirect_args());web.read("a",args());
  CHECK(!s.cached("a",redirect_original)->at("publication").at("snippet_allowed").flag());
  CHECK(s.cached("a",redirect_original)->at("representation_publication").at("snippet_allowed").flag());
  const auto query=parse_query(Json::string("cache"));const auto frozen=s.search("a",query);
  w.alter={};web.read("a",redirect_args("refresh"));
  const auto current=s.search("a",query);
  for(const auto& session:{frozen,current}) {
    const auto hits=s.page("a",session.at("session_id").str(),"",20).at("hits");CHECK(hits.size()==2);
    for(std::size_t i=0;i<hits.size();++i)CHECK(hits.at(i).at("snippet").str().empty()==(hits.at(i).at("url").str()==redirect_original));
  }
  CHECK(s.revalidation_base("a",redirect_original)->parsed.representation_publication.at("snippet_allowed").flag());
}
TEST(http_redirect_proof_protocol_tampering_cannot_replace_retained_body) {
  for(const auto* kind:{"observations","profile","usage","receipt"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());
    w.alter=[&](const Json&,Json response) {
      if(std::string(kind)=="observations")response.set("http_redirect_observations",Json::array());
      else if(std::string(kind)=="profile")response.at("http_freshness").set("request_profile",sha256("other"));
      else if(std::string(kind)=="usage")response.at("usage").set("content_bytes",std::int64_t{0});
      else response.at("redirect_revalidation").set("binding_id",sha256("other"));
      return response;
    };
    rejects([&]{web.read("a",redirect_args("refresh"));},"E_WORKER_PROTOCOL");CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");CHECK(w.requests==2);
  }
}

TEST(http_redirect_proof_bytes_are_charged_and_refunded_on_full_replacement) {
  Temp t;RedirectWorker w;w.legacy=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto first=web.read("a",redirect_args());
  const auto before=s.stats("a").at("content_bytes").num();const auto old=s.cached("a",redirect_original)->at("meta");
  w.legacy=false;++w.now;auto proof=web.read("a",redirect_args("refresh"));const auto meta=s.cached("a",redirect_original)->at("meta");
  auto old_http=Json::object(),new_http=Json::object();for(const auto* key:{"http_cache","http_freshness","http_redirect_chain","http_revalidation"}) {if(old.has(key))old_http.set(key,old.at(key));if(meta.has(key))new_http.set(key,meta.at(key));}
  const auto delta=static_cast<std::int64_t>(new_http.canonical().size())-static_cast<std::int64_t>(old_http.canonical().size());
  CHECK(delta>0);CHECK(s.stats("a").at("content_bytes").num()==before+delta);CHECK(proof.at("snapshot_id").str()==first.at("snapshot_id").str());
  w.legacy=true;w.force200=true;++w.now;web.read("a",redirect_args("refresh"));CHECK(!s.cached("a",redirect_original)->at("meta").has("http_redirect_chain"));CHECK(s.stats("a").at("content_bytes").num()==before);
}
TEST(http_redirect_304_metadata_growth_rolls_back_at_owner_quota) {
  Temp t;RedirectWorker w;std::int64_t used=0;std::string before;
  {Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());used=s.stats("a").at("content_bytes").num();before=s.cached("a",redirect_original)->canonical();}
  StoreOptions options;options.max_bytes_per_owner=used;Store s(t.path/"db",[&]{return w.now;},options);WebService web(s,w,{});++w.now;
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_QUOTA");CHECK(s.stats("a").at("content_bytes").num()==used);CHECK(s.cached("a",redirect_original)->canonical()==before);CHECK(w.requests==2);
}
TEST(http_redirect_hop_and_encoded_limits_are_not_trusted_from_worker) {
  for(bool oversized:{false,true}) {
    Temp t;RedirectWorker w;w.hops=oversized?2:6;if(oversized)w.prefix_control="max-age=10, extension=\""+std::string(4000,'x')+"\"";
    Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});rejects([&]{web.read("a",redirect_args());},"E_WORKER_PROTOCOL");CHECK(s.stats("a").at("snapshots").num()==0);
  }
}
TEST(http_redirect_expiry_is_rechecked_after_profile_rpc_before_reuse) {
  for(const auto* mode:{"cache_only","cache_first"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("a",redirect_args());bool once=false;
    w.during_profile=[&]{if(!once){once=true;w.now+=10000;}};
    if(std::string(mode)=="cache_only") {rejects([&]{web.read("a",redirect_args(mode));},"E_CACHE_STALE");CHECK(w.requests==1);}
    else {CHECK(!web.read("a",redirect_args(mode)).at("from_cache").flag());CHECK(w.requests==2);}
  }
}
TEST(direct_cache_hits_project_current_noindex_without_rewriting_history) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  const auto first=web.read("a",args()),evidence=s.evidence("a",first.at("snapshot_id").str(),0,5);
  const auto query=parse_query(Json::string("cache"));const auto frozen=s.search("a",query);
  s.observe_web_publication("a",url,url,redirect_noindex(),s.publication_fence("a",url));
  for(const auto* freshness:{"cache_only","cache_first"}) {
    const auto hit=web.read("a",args(freshness));CHECK(hit.at("from_cache").flag());CHECK(!hit.at("indexed").flag());
    CHECK(hit.at("snapshot_id").str()==first.at("snapshot_id").str());CHECK(hit.at("content").at("text").str()=="cache fixture body");
    CHECK(!hit.at("content").has("http_publication_projection"));
  }
  CHECK(w.requests==1);CHECK(s.search("a",query).at("frozen_count").num()==0);
  CHECK(s.page("a",frozen.at("session_id").str(),"",20).at("hits").size()==0);
  const auto historical=s.snapshot("a",first.at("snapshot_id").str());CHECK(historical.at("indexed").flag());CHECK(historical.at("canonical_text").str()=="cache fixture body");
  const auto range=s.range("a",first.at("snapshot_id").str(),0,100);CHECK(range.at("text").str()=="cache fixture body");CHECK(!range.has("http_publication_projection"));
  CHECK(s.evidence_get("a",evidence.at("evidence_id").str()).at("quote").str()=="cache");
}
TEST(direct_cache_hit_rechecks_publication_after_profile_rpc) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});const auto first=web.read("a",args());bool observed=false;
  w.during_profile=[&] {if(!observed){observed=true;s.observe_web_publication("a",url,url,redirect_noindex(),s.publication_fence("a",url));}};
  const auto hit=web.read("a",args("cache_only"));CHECK(observed);CHECK(hit.at("from_cache").flag());CHECK(!hit.at("indexed").flag());
  CHECK(hit.at("snapshot_id").str()==first.at("snapshot_id").str());CHECK(w.requests==1);
}
TEST(direct_cache_hit_rechecks_same_snapshot_metadata_after_profile_rpc) {
  for(const auto* freshness:{"cache_only","cache_first"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    const auto first=web.read("a",args());const auto before=s.cached("a",url)->at("meta").canonical();bool refreshed=false;
    w.during_profile=[&] {
      if(!refreshed) {refreshed=true;++w.now;const auto newer=web.read("a",args("refresh"));CHECK(newer.at("snapshot_id").str()==first.at("snapshot_id").str());}
    };
    if(std::string(freshness)=="cache_only") {
      rejects([&]{web.read("a",args(freshness));},"E_CACHE_STALE");CHECK(w.requests==2);
    } else {
      const auto fetched=web.read("a",args(freshness));CHECK(!fetched.at("from_cache").flag());CHECK(w.requests==3);
    }
    CHECK(s.cached("a",url)->at("meta").canonical()!=before);
    const auto requests=w.requests;CHECK(web.read("a",args("cache_only")).at("from_cache").flag());CHECK(w.requests==requests);
  }
}
TEST(direct_cache_guard_does_not_require_a_redirect_publication_fence) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  SourceDocument document{url,"cache fixture body","text/plain","cache fixture"};ParsedDocument parsed;
  parsed.title=document.title;parsed.text=document.raw;parsed.content_type=document.content_type;parsed.parser_version="fixture/1";parsed.index_allowed=true;
  auto meta=Json::object();meta.set("final_url",url);meta.set("redirects",Json::array());meta.set("http_freshness",policy());meta.set("fetched_at_ms",w.now);
  const auto stored=s.ingest_web("a",document,parsed,meta,s.deletion_generation("a",url));CHECK(!stored.at("meta").has("publication_fence"));
  const auto hit=web.read("a",args("cache_only"));CHECK(hit.at("from_cache").flag());CHECK(hit.at("indexed").flag());CHECK(hit.at("snapshot_id").str()==stored.at("snapshot_id").str());CHECK(w.requests==0);
}
Json oversized_redirect_grant_response(Json response) {
  const auto prefix=std::string("https://cache.example/");const auto final=prefix+std::string(3500-prefix.size(),'x');
  response.set("final_url",final);auto redirects=Json::array();redirects.push(Json::string(final));response.set("redirects",redirects);
  response.at("http_freshness").set("url",final);response.at("http_cache").set("url",final);
  response.at("http_cache").set("etag","\""+std::string(1022,'v')+"\"");
  response.at("http_redirect_chain").set("final_url",final);
  response.at("http_redirect_chain").at("hops").at(std::size_t{0}).set("target_url",final);
  return response;
}
void oversized_grant_is_optional(Store& store) {
  const auto meta=store.cached("a",redirect_original)->at("meta");CHECK(meta.has("http_redirect_chain"));
  CHECK(meta.at("http_redirect_chain").canonical().size()<=8192);
  auto accounted=Json::object();for(const auto* key:{"http_cache","http_freshness","http_redirect_chain"})accounted.set(key,meta.at(key));CHECK(accounted.canonical().size()<=16384);
  const auto conditional=http_conditional(meta.at("http_cache"),sha256("oversized-grant-boundary"));
  auto encoded=Json::object();encoded.set("version","bzs-http-redirect-conditional-1");encoded.set("request_url",redirect_original);encoded.set("redirects",meta.at("redirects"));encoded.set("conditional",conditional);
  CHECK(encoded.canonical().size()>8192);
  rejects([&]{(void)http_redirect_conditional(meta.at("http_redirect_chain"),conditional);},"E_WORKER_PROTOCOL");
  CHECK(!store.revalidation_base("a",redirect_original));
}
TEST(oversized_optional_redirect_grant_uses_one_full_refresh_without_retry) {
  Temp t;RedirectWorker w;w.force200=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  w.alter=[](const Json& parameters,Json response) {CHECK(!parameters.has("conditional")&&!parameters.has("redirect_conditional"));return oversized_redirect_grant_response(response);};
  const auto first=web.read("a",redirect_args());oversized_grant_is_optional(s);
  CHECK(web.read("a",redirect_args("cache_only")).at("from_cache").flag());CHECK(w.requests==1);
  ++w.now;const auto refreshed=web.read("a",redirect_args("refresh"));CHECK(w.requests==2);CHECK(!refreshed.at("from_cache").flag());CHECK(refreshed.at("fetch").at("http_status").num()==200);
  CHECK(refreshed.at("snapshot_id").str()==first.at("snapshot_id").str());CHECK(refreshed.at("content").at("text").str()=="cache fixture body");
}
TEST(oversized_optional_redirect_grant_batch_keeps_full_get_known_usage) {
  Temp t;RedirectWorker w;w.force200=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  w.alter=[](const Json& parameters,Json response) {CHECK(!parameters.has("conditional")&&!parameters.has("redirect_conditional"));return oversized_redirect_grant_response(response);};
  web.read("a",redirect_args());oversized_grant_is_optional(s);
  const auto job=s.read_many_submit("a",{redirect_original},"oversized-grant-full",{});const auto lease=s.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);
  const auto result=s.job_get("a",job.at("job_id").str());CHECK(result.at("state").str()=="succeeded");CHECK(w.requests==2);
  CHECK(result.at("usage").at("external_requests").num()==2);CHECK(result.at("usage").at("content_bytes").num()==21);
  CHECK(result.at("usage").at("unknown_requests_charged").num()==0);CHECK(result.at("usage").at("reserved_requests").num()==0);
}
TEST(oversized_optional_redirect_grant_does_not_authorize_unsolicited_304) {
  Temp t;RedirectWorker w;w.force200=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  w.alter=[&](const Json& parameters,Json response) {
    CHECK(!parameters.has("conditional")&&!parameters.has("redirect_conditional"));response=oversized_redirect_grant_response(response);
    if(w.requests==1)return response;
    auto unbound=Json::object();for(const auto& key:response.keys())if(key!="html"&&key!="text")unbound.set(key,response.at(key));
    unbound.set("status","not_modified");unbound.set("http_status",std::int64_t{304});unbound.set("binding_id",sha256("ungranted"));unbound.at("usage").set("content_bytes",std::int64_t{3});return unbound;
  };
  const auto first=web.read("a",redirect_args());oversized_grant_is_optional(s);const auto meta=s.cached("a",redirect_original)->at("meta").canonical();
  w.now+=5000;rejects([&]{web.read("a",redirect_args("refresh"));},"E_WORKER_PROTOCOL");CHECK(w.requests==2);
  CHECK(s.cached("a",redirect_original)->at("meta").canonical()==meta);CHECK(s.stats("a").at("snapshots").num()==1);
  CHECK(s.snapshot("a",first.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
  w.now=epoch+10000;rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);
}

Json final_publication_record(const Json& policy,const std::string& final=url) {
  auto record=Json::object();record.set("version","bzs-http-representation-publication-1");record.set("url",final);record.set("publication",policy);return record;
}
Json prefix_links_only_publication() {
  auto policy=redirect_allowing_publication();policy.set("snippet_allowed",false);policy.set("snippet_text","");policy.set("snippet_title","");
  policy.at("diagnostics").push(Json::string("site_links_only"));return policy;
}
Json without_field(const Json& input,const std::string& field) {
  auto result=Json::object();for(const auto& key:input.keys())if(key!=field)result.set(key,input.at(key));return result;
}
TEST(explicit_final_policy_preserves_worker_aggregate_and_independent_final) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("a",args());const auto query=parse_query(Json::string("cache"));const auto frozen=s.search("a",query);
  w.alter=[](const Json&,Json response) {
    if(response.at("http_status").num()==200) {
      response.set("publication",prefix_links_only_publication());response.set("representation_publication",final_publication_record(redirect_allowing_publication()));
    }
    return response;
  };
  web.read("a",redirect_args());
  const auto saved=s.cached("a",redirect_original);CHECK(!saved->at("publication").at("snippet_allowed").flag());CHECK(saved->at("representation_publication").at("snippet_allowed").flag());
  CHECK(!saved->at("meta").has("validated_final_publication_scope"));
  for(int i=0;i<2;++i) {
    web.read("a",redirect_args("refresh"));
    const auto original=s.page("a",frozen.at("session_id").str(),"",20).at("hits");CHECK(original.size()==1);CHECK(!original.at(std::size_t{0}).at("snippet").str().empty());
    const auto current=s.search("a",query),hits=s.page("a",current.at("session_id").str(),"",20).at("hits");CHECK(hits.size()==2);
    for(std::size_t k=0;k<hits.size();++k)CHECK(hits.at(k).at("snippet").str().empty()==(hits.at(k).at("url").str()==redirect_original));
  }
}
TEST(final_publication_wrapper_is_strictly_versioned_typed_and_bound) {
  for(const std::string kind:{"extra","missing","version","url","url_type","record_type","policy_type","policy_extra","proof","observations"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[&](const Json&,Json response) {
      response.set("publication",prefix_links_only_publication());auto wrapper=final_publication_record(redirect_allowing_publication());
      if(kind=="extra")wrapper.set("unknown",true);
      if(kind=="missing")wrapper=without_field(wrapper,"version");
      if(kind=="version")wrapper.set("version","future");
      if(kind=="url")wrapper.set("url",redirect_original);
      if(kind=="url_type")wrapper.set("url",std::int64_t{1});
      if(kind=="record_type")wrapper=Json::array();
      if(kind=="policy_type")wrapper.set("publication",false);
      if(kind=="policy_extra")wrapper.at("publication").set("unknown",true);
      if(kind=="proof")response.at("http_redirect_chain").set("request_url",url);
      if(kind=="observations")response=without_field(response,"http_redirect_observations");
      response.set("representation_publication",wrapper);return response;
    };
    rejects([&]{web.read("a",redirect_args());},"E_WORKER_PROTOCOL");CHECK(w.requests==1);CHECK(s.stats("a").at("snapshots").num()==0);
  }
}
TEST(final_publication_wrapper_requires_complete_successful_200) {
  for(const std::string kind:{"unproven","empty","error","non200","truncated","no_aggregate"}) {
    Temp t;RedirectWorker w;w.legacy=kind=="unproven";Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[&](const Json&,Json response) {
      response.set("publication",redirect_allowing_publication());response.set("representation_publication",final_publication_record(redirect_allowing_publication()));
      if(kind=="empty")response.set("status","empty_content");
      if(kind=="error"){response.set("status","http_error");response.set("http_status",std::int64_t{503});}
      if(kind=="non200")response.set("http_status",std::int64_t{203});
      if(kind=="truncated")response.set("warnings",Json::parse(R"(["download_truncated"])"));
      if(kind=="no_aggregate")response=without_field(response,"publication");
      return response;
    };
    rejects([&]{web.read("a",redirect_args());},"E_WORKER_PROTOCOL");CHECK(w.requests==1);CHECK(s.stats("a").at("snapshots").num()==0);
  }
}
TEST(final_publication_wrapper_cannot_relax_actual_response_or_aggregate) {
  for(const std::string kind:{"aggregate","final_observation","inconsistent_index"}) {
    Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[&](const Json&,Json response) {
      response.set("publication",kind=="aggregate"?redirect_allowing_publication():redirect_noindex());
      response.set("index_allowed",kind=="aggregate"||kind=="inconsistent_index");
      response.set("representation_publication",final_publication_record(kind=="aggregate"?redirect_noindex():redirect_allowing_publication()));
      if(kind=="final_observation")response.set("publication_observation",redirect_noindex());
      return response;
    };
    rejects([&]{web.read("a",redirect_args());},"E_WORKER_PROTOCOL");CHECK(w.requests==1);CHECK(s.stats("a").at("snapshots").num()==0);
  }
}
TEST(bodyless_304_cannot_supply_final_publication_wrapper) {
  Temp t;RedirectWorker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  const auto first=web.read("a",redirect_args());
  w.alter=[](const Json&,Json response) {response.set("representation_publication",final_publication_record(redirect_allowing_publication()));return response;};
  rejects([&]{web.read("a",redirect_args("refresh"));},"E_WORKER_PROTOCOL");CHECK(w.requests==2);
  CHECK(s.cached("a",redirect_original)->at("snapshot_id").str()==first.at("snapshot_id").str());CHECK(s.stats("a").at("snapshots").num()==1);
}
TEST(complete_uncacheable_receipt_keeps_final_history_separate) {
  for(const std::string mode:{"no-cache","max-age=0","metadata_limit"})for(bool explicit_wrapper:{false,true}) {
    Temp t;RedirectWorker w;w.force200=true;if(mode!="metadata_limit")w.prefix_control=mode;
    std::string final=url;if(mode=="metadata_limit"){const std::string prefix="https://cache.example/";final=prefix+std::string(3500-prefix.size(),'x');}
    Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});auto final_args=args();final_args.set("url",final);
    const auto independent=web.read("a",final_args);const auto query=parse_query(Json::string("cache"));const auto frozen=s.search("a",query);
    bool restricted=false;
    w.during=[&] {if(!restricted){restricted=true;s.observe_web_publication("a",redirect_original,redirect_original,redirect_noindex(),s.publication_fence("a",redirect_original));}};
    w.alter=[&](const Json& parameters,Json response) {
      CHECK(!parameters.has("conditional")&&!parameters.has("redirect_conditional"));
      if(mode=="metadata_limit") {
        response=oversized_redirect_grant_response(response);const auto control="max-age=60, extension=\""+std::string(1024,'x')+"\"";
        response.at("http_freshness").set("cache_control",control);response.at("http_cache").set("cache_control",control);
        auto accounted=Json::object();for(const auto* key:{"http_cache","http_freshness","http_redirect_chain"})accounted.set(key,response.at(key));
        CHECK(response.at("http_redirect_chain").canonical().size()<=8192);CHECK(accounted.canonical().size()>16384);
      }
      response.set("publication",explicit_wrapper?prefix_links_only_publication():redirect_allowing_publication());
      if(explicit_wrapper)response.set("representation_publication",final_publication_record(redirect_allowing_publication(),final));
      return response;
    };
    CHECK(!web.read("a",redirect_args()).at("indexed").flag());CHECK(restricted);
    const auto saved=s.cached("a",redirect_original);const auto meta=saved->at("meta");
    CHECK(!meta.has("http_redirect_chain")&&!meta.has("validated_final_publication_scope"));
    CHECK(saved->at("representation_publication").at("index_allowed").flag());CHECK(saved->at("representation_publication").at("snippet_allowed").flag());
    CHECK(!s.revalidation_base("a",redirect_original));rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");CHECK(w.requests==2);
    const auto current=s.search("a",query);
    for(const auto& session:{frozen,current}) {const auto hits=s.page("a",session.at("session_id").str(),"",20).at("hits");CHECK(hits.size()==1);CHECK(hits.at(std::size_t{0}).at("url").str()==final);CHECK(!hits.at(std::size_t{0}).at("snippet").str().empty());}
    final_args.set("freshness","cache_only");CHECK(web.read("a",final_args).at("indexed").flag());CHECK(w.requests==2);
    CHECK(s.snapshot("a",independent.at("snapshot_id").str()).at("canonical_text").str()=="cache fixture body");
  }
}
TEST(worker_cannot_inject_the_core_final_scope_marker) {
  for(bool nested:{false,true}) {
    Temp t;RedirectWorker w;w.legacy=true;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
    w.alter=[&](const Json&,Json response) {
      response.set("publication",prefix_links_only_publication());auto marker=Json::object();marker.set("version","bzs-final-publication-scope-1");marker.set("url",url);
      if(nested)response.at("metadata").set("validated_final_publication_scope",marker);else response.set("validated_final_publication_scope",marker);
      return response;
    };
    web.read("a",redirect_args());const auto saved=s.cached("a",redirect_original);
    CHECK(!saved->at("meta").has("validated_final_publication_scope"));CHECK(!saved->at("meta").has("http_redirect_chain"));
    CHECK(!s.revalidation_base("a",redirect_original));rejects([&]{web.read("a",redirect_args("cache_only"));},"E_CACHE_STALE");
    CHECK(!saved->at("publication").at("snippet_allowed").flag());CHECK(!saved->at("representation_publication").has("version"));
  }
}

}
int main(){int failed=0;for(const auto& [name,test]:cases){try{test();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cout<<"FAIL "<<name<<": "<<e.what()<<'\n';}}std::cout<<cases.size()<<" cases, "<<assertions<<" assertions, "<<failed<<" failed\n";return failed?1:0;}
