#define main baseline_crawl_test_main
#include "baseline_crawl_tests.cpp"
#undef main
#include <fstream>
int main(int argc,char** argv) {
  try {
    CHECK(argc==2);
    const std::filesystem::path fixture=argv[1];
    CHECK(!std::filesystem::exists(fixture/"db"));
    std::filesystem::create_directories(fixture);
    Fake f;
    Store store(fixture/"db",[&]{return f.now;});
    auto config=manifest();
    config.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/map"])"));
    WebService web(store,f,{},SiteRegistry(config));
    const std::string a="https://map.example/map/docs/a",x="https://map.example/map/transit/x",b="https://map.example/map/docs/b";
    f.response=[&](const Json& p) {
      auto response=f.ok(p);if(p.at("kind").str()=="robots")return response;
      const auto url=p.at("url").str();
      if(url==a||url==x) {
        response.set("status","redirect");response.set("http_status",std::int64_t{302});response.set("redirect_url",url==a?x:b);
      }
      return response;
    };
    auto request=args({a},"legacy-site-chain");request.set("scope_path","/map");request.set("max_depth",std::int64_t{0});
    const auto job=web.submit_crawl("alice",request);drain(web);
    CHECK(f.calls.size()==4);CHECK(f.calls.at(0).at("kind").str()=="robots");
    CHECK(f.calls.at(1).at("url").str()==a);CHECK(f.calls.at(2).at("url").str()==x);CHECK(f.calls.at(3).at("url").str()==b);
    CHECK(store.job_get("alice",job.at("job_id").str()).at("used_pages").num()==1);
    const auto result=store.job_results("alice",job.at("job_id").str()).at("items").at(0);
    CHECK(result.at("body_read").flag());
    const auto snapshot=store.snapshot("alice",result.at("snapshot_id").str());
    CHECK(snapshot.at("url").str()==b);CHECK(snapshot.at("meta").at("redirects").size()==0);
    const auto session=freeze(store);CHECK(hits(store,session).size()==1);
    CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==6);
    auto info=Json::object();info.set("job",store.job_get("alice",job.at("job_id").str()));info.set("snapshot",snapshot);info.set("session",session);info.set("original_manifest",config);
    std::ofstream file(fixture/"info.json");file<<info.dump();
    std::cout<<info.dump()<<'\n';return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
