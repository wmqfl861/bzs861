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
  auto old=policy("max-age=10"),update=policy("");update.set("received_at_ms",epoch+10000);
  auto merged=merge_http_freshness(old,update);CHECK(merged.at("cache_control").str()=="max-age=10");CHECK(http_freshness(merged,epoch+10001,86400).at("fresh").flag());
  update.set("request_profile",sha256("other"));rejects([&]{merge_http_freshness(old,update);},"E_WORKER_PROTOCOL");
}
}
int main(){int failed=0;for(const auto& [name,test]:cases){try{test();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cout<<"FAIL "<<name<<": "<<e.what()<<'\n';}}std::cout<<cases.size()<<" cases, "<<assertions<<" assertions, "<<failed<<" failed\n";return failed?1:0;}
