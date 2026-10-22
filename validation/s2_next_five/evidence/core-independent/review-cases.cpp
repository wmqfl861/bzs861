#define main author_main
#include "frozen-28b60fd-revalidation.cpp"
#undef main

namespace {
TEST(independent_same_snapshot_metadata_refresh_is_fenced) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  auto first=web.read("alice",read_args());const auto sid=first.at("snapshot_id").str();
  bool nested=false;
  w.alter=[&](const Json&,Json response) {
    if(!nested) { nested=true;++w.now;auto renewed=web.read("alice",read_args());CHECK(renewed.at("snapshot_id").str()==sid); }
    return response;
  };
  rejects([&]{web.read("alice",read_args());},"E_REVALIDATION_STALE");
  CHECK(s.cached("alice",url())->at("snapshot_id").str()==sid);
  CHECK(s.cached("alice",url())->at("fetched_at_ms").num()==w.now);
}

TEST(independent_during_request_restriction_cannot_republish_old_body) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("alice",read_args());CHECK(hits(s)==1);
  w.alter=[&](const Json&,Json response) {
    s.observe_web_publication("alice",url(),url(),restriction("noindex"),s.publication_fence("alice",url()));
    return response;
  };
  auto renewed=web.read("alice",read_args());
  CHECK(!renewed.at("indexed").flag());CHECK(hits(s)==0);
}

TEST(independent_304_metadata_growth_rolls_back_at_owner_quota) {
  Temp t;Worker w;std::int64_t used=0;std::string before;
  {Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});web.read("alice",read_args());
   used=scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'");before=s.cached("alice",url())->canonical();}
  StoreOptions options;options.max_bytes_per_owner=used;
  Store s(t.path/"db",[&]{return w.now;},options);WebService web(s,w,{});++w.now;
  rejects([&]{web.read("alice",read_args());},"E_QUOTA");
  CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==used);
  CHECK(s.cached("alice",url())->canonical()==before);
}

TEST(independent_304_then_full_fetch_refunds_only_retired_cache_metadata) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{});
  web.read("alice",read_args());const auto before=scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'");
  web.read("alice",read_args());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")>before);
  w.alter=[&](const Json& p,Json){return w.page(p);};
  auto restored=web.read("alice",read_args());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==before);
  s.erase("alice",restored.at("document_id").str());CHECK(scalar(s.raw_handle(),"SELECT bytes FROM usage WHERE owner='alice'")==0);
}

TEST(independent_latest_robots_denial_survives_selective_job_compaction) {
  Temp t;Worker w;const auto start=w.now;
  Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));
  web.submit_crawl("alice",crawl_args("initial-allow"));drain(web);
  // A later crawl pins the old allowing robots record, but its longer retry
  // deadline prevents compaction when the following short denial job expires.
  w.now=start+3599000;auto pin=crawl_args("long-retry-pin");pin.set("timeout_ms",std::int64_t{3600000});
  web.submit_crawl("alice",pin);drain(web);
  w.now=start+3600001;
  w.alter=[&](const Json& p,Json response) {
    if(p.at("kind").str()!="robots")return response;
    auto denied=Json::object();denied.set("status","error");denied.set("http_status",std::int64_t{503});
    denied.set("url",p.at("url"));denied.set("final_url",p.at("url"));denied.set("error_code","E_ROBOTS_UNAVAILABLE");
    denied.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":0})"));
    auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"deny_all","rules":[],"sitemaps":[],"diagnostics":[]})");
    record.set("origin",std::string(origin));record.set("body_sha256",sha256("synthetic HTTP 503"));
    record.set("fetched_at_ms",w.now);record.set("expires_at_ms",w.now+1000);denied.set("robots",record);return denied;
  };
  auto deny=crawl_args("short-denial");deny.set("timeout_ms",std::int64_t{1000});
  const auto denial_job=web.submit_crawl("alice",deny).at("job_id").str();drain(web);
  CHECK(s.job_get("alice",denial_job).at("partial").flag());
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);
  constexpr std::int64_t retention=7LL*24*60*60*1000;
  w.now=start+3601002+retention;auto compacted=s.compact_jobs("alice",64);
  CHECK(compacted.at("compacted_jobs").num()==2);
  // No direct SQL writes or fabricated leases: all decisions and references
  // above were persisted through normal submit/execute/compact APIs.
  w.alter={};web.submit_crawl("alice",crawl_args("after-compaction"));auto lease=web.lease_map();
  CHECK(lease.has_value());CHECK(lease->kind=="robots");
  CHECK(lease->conditional.is_null());
}
}

int main(){std::size_t ran=0,failed=0;for(const auto& [name,run]:tests()){
  if(!name.starts_with("independent_"))continue;++ran;
  try{run();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n';}
}std::cout<<ran<<" independent tests / "<<assertions<<" assertions / "<<failed<<" failures\n";return failed?1:0;}
