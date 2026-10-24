#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include <iostream>
#include <limits>
#include <vector>
using namespace bzs;
int checks=0;
#define CHECK(x) do{++checks;if(!(x))throw std::runtime_error(#x);}while(false)
Json manifest() {
 return Json::parse(R"({"version":1,"sources":[{"id":"fixture","provider":"fixture","index_family":"fixture","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":100,"cooldown_rate_ms":17,"params":{"adapter":"offline_fixture","base_url":"https://source.example/search"},"contract":{"id":"fixture","provider_id":"fixture","index_family":"fixture","adapter_id":"offline_fixture","endpoint":"https://source.example/search","capability":"web","enabled":true,"logical_service_id":"fixture","review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://source.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved_by":"independent-offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"shared_budget_key":"shared","request_quota":100,"quota_window_seconds":86400}}}]})");
}
struct Worker:WorkerPort {
 Json value,flag;bool present=true,flag_present=false;int calls=0;Store* store=nullptr;
 Json call(const std::string& method,const Json&,int) override {
  CHECK(method=="search.multi");++calls;
  if(store)store->source_report("shared","parse_error","concurrent parse",3LL*86400000);
  auto out=Json::parse(R"({"results":{"fixture":{"status":"rate_limited","error":"429","hits":[],"http_status":429}}})");
  if(present)out.at("results").at("fixture").set("retry_after_ms",value);
  if(flag_present)out.at("results").at("fixture").set("retry_after_clamped",flag);
  return out;
 }
};
int main() {
 try {
  struct Input {const char* value;std::int64_t expected;bool clamped;};
  std::vector<Input> inputs={{"null",0,false},{"true",0,false},{"false",0,false},{"\"999999999000\"",0,false},{"86400001.5",0,false},{"-9223372036854775808",0,false},{"0",0,false},{"120000",120000,false},{"86400000",86400000,false},{"86400001",86400000,true},{"9223372036854775807",86400000,true}};
  for(const auto& input:inputs)for(bool bad_flag:{false,true}) {
   std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});Worker worker;worker.value=Json::parse(input.value);worker.flag_present=bad_flag;worker.flag=Json::string("true");WebService web(store,worker,SourceRegistry(manifest()));
   auto result=web.search("owner-a",Json::parse(R"({"query":"independent bounded protocol"})"));const auto row=result.at("sources").at(0);
   CHECK(row.at("retry_after_ms").num()==input.expected);CHECK(row.at("cooldown_ms").num()==std::max<std::int64_t>(17,input.expected));CHECK(row.at("retry_after_clamped").flag()==input.clamped);CHECK(row.has("retry_after_limit_ms")==input.clamped);
   CHECK(result.at("partial").flag());CHECK(worker.calls==1);
   const auto again=web.search("owner-b",Json::parse(R"({"query":"another owner"})"));CHECK(again.at("sources").at(0).at("reason").str()=="cooldown");CHECK(worker.calls==1);
   now+=std::max<std::int64_t>(17,input.expected);CHECK(store.source_admit("shared",0,100).empty());CHECK(store.source_states().at("shared").at("requests_today").num()==(now/86400000==1800000000000LL/86400000?2:1));
  }
  for(bool prior_parse:{false,true}) {
   std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});Worker worker;worker.value=Json::integer(86400000);worker.flag=Json::boolean(true);worker.flag_present=true;if(prior_parse)worker.store=&store;WebService web(store,worker,SourceRegistry(manifest()));
   const auto response=web.search("owner-a",Json::parse(R"({"query":"upstream clamping marker"})"));const auto row=response.at("sources").at(0);
   CHECK(row.at("retry_after_clamped").flag());CHECK(row.at("error").str().starts_with("E_RETRY_AFTER_CLAMPED:"));CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==(prior_parse?3LL*86400000:86400000));
   store.source_report("shared","ok","late success",0);CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==(prior_parse?3LL*86400000:86400000));
  }
  std::cout<<"PASS 24 independent protocol scenarios, "<<checks<<" checks\n";return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<" after "<<checks<<" checks\n";return 1;}
}
