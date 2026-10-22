#define main author_main
#include "frozen-28b60fd-revalidation.cpp"
#undef main

namespace {
TEST(independent_failed_response_nofollow_survives_later_304) {
  Temp t;Worker w;w.links[url()]={url("/docs/child")};
  Store s(t.path/"db",[&]{return w.now;});WebService web(s,w,{},SiteRegistry(manifest()));
  web.submit_crawl("alice",crawl_args("allowing-body"));drain(web);
  const auto initial=s.cached("alice",url())->at("snapshot_id").str();
  CHECK(s.cached("alice",url())->at("publication").at("follow_allowed").flag());
  w.alter=[](const Json& p,Json) {
    auto response=Json::object();response.set("status","error");
    response.set("http_status",std::int64_t{503});response.set("final_url",p.at("url"));
    response.set("redirects",Json::array());
    response.set("publication_observation",restriction("nofollow"));
    response.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":0})"));
    return response;
  };
  bool rejected=false;try{web.read("alice",read_args());}catch(const Error&){rejected=true;}
  CHECK(rejected);CHECK(s.cached("alice",url())->at("snapshot_id").str()==initial);
  const auto before=w.calls.size();w.alter={};
  web.submit_crawl("alice",crawl_args("after-failed-nofollow"));drain(web);
  CHECK(w.calls.size()==before+1);CHECK(w.calls.back().has("conditional"));
  const auto after=s.cached("alice",url());
  CHECK(after->at("snapshot_id").str()==initial);
  CHECK(!after->at("publication").at("follow_allowed").flag());
}

TEST(independent_site_reapproval_does_not_republish_retained_304_body) {
  Temp t;Worker w;Store s(t.path/"db",[&]{return w.now;});
  WebService initial(s,w,{},SiteRegistry(manifest()));
  const auto original=initial.read("alice",read_args());CHECK(hits(s)==1);
  auto rejected=manifest();rejected.at("sites").at(std::size_t{0}).at("review").set("status","rejected");
  s.activate_site_publication(SiteRegistry(rejected));CHECK(hits(s)==0);
  s.activate_site_publication(SiteRegistry(manifest()));CHECK(hits(s)==0);
  WebService current(s,w,{},SiteRegistry(manifest()));
  const auto renewed=current.read("alice",read_args());
  CHECK(w.calls.back().has("conditional"));
  CHECK(renewed.at("snapshot_id").str()==original.at("snapshot_id").str());
  CHECK(!renewed.at("indexed").flag());CHECK(hits(s)==0);
  w.alter=[&](const Json& p,Json){return w.page(p);};
  CHECK(current.read("alice",read_args()).at("indexed").flag());CHECK(hits(s)==1);
}
}

int main(){std::size_t ran=0,failed=0;for(const auto& [name,run]:tests()){
  if(!name.starts_with("independent_"))continue;++ran;
  try{run();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n';}
}std::cout<<ran<<" independent tests / "<<assertions<<" assertions / "<<failed<<" failures\n";return failed?1:0;}
