#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <iostream>
using namespace bzs;
int main() {
  const auto root=std::filesystem::temp_directory_path()/("bzs-retention-review-"+random_id());std::filesystem::create_directories(root);
  const auto registry=Json::parse(R"({"version":1,"sites":[{"id":"policy-site","enabled":true,"origin":"https://policy.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"2020-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://policy.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"review-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
  std::int64_t now=1700000000000;
  const SiteRegistry sites(registry);auto request=Json::parse(R"({"site_id":"policy-site","seeds":["https://policy.example/docs/a"],"idempotency_key":"first"})");
  {
    Store store(root/"db",[&]{return now;});store.activate_site_publication(sites);
    for(int i=0;i<512;++i) { request.set("idempotency_key","job-"+std::to_string(i));const auto accepted=store.map_submit("alice",request,sites);store.job_control("alice",accepted.at("job_id").str(),"cancel"); }
    request.set("idempotency_key","new-after-512-cancelled");try { store.map_submit("alice",request,sites);std::cout<<"unexpected new task accepted\n"; }catch(const Error& error) { std::cout<<"512 completed cancellations; next submission="<<error.code<<" "<<error.what()<<"; HTTP=0\n"; }
  }
  {
    now+=8LL*24*60*60*1000;
    Store reopened(root/"db",[&]{return now;});try { reopened.map_submit("alice",request,sites);std::cout<<"unexpected post-restart acceptance\n"; }catch(const Error& error) { std::cout<<"after restart and 8 days="<<error.code<<'\n'; }
    request.set("idempotency_key","job-0");const auto replay=reopened.map_submit("alice",request,sites);std::cout<<"old key reused="<<replay.at("reused").flag()<<" state="<<replay.at("state").str()<<'\n';
  }
  std::filesystem::remove_all(root);
}
