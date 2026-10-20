#define main migration_review_main
#include "verify_upgrade.cpp"
#undef main
#include <functional>

struct GatedReader final:WorkerPort {
  Reader reader;
  std::function<void()> before_return;
  Json call(const std::string& method,const Json& params,int timeout)override {
    auto response=reader.call(method,params,timeout);
    if(before_return)before_return();
    return response;
  }
};
Json page_hits(Store& store,const Json& session) {
  return store.page("alice",session.at("session_id").str(),"",100).at("hits");
}
Json scan(Store& store) {
  return store.search("alice",parse_query(Json::parse(R"({"op":"not","child":{"op":"term","value":"absent"}})")));
}
void restricted_hits(Store& store,const Json& session,const std::string& mode) {
  const auto values=page_hits(store,session);
  if(mode=="rejected")check(values.size()==0,"restricted artifact appeared in query");
  else {
    check(values.size()==1,"links-only artifact missing from query");
    check(values.at(0).at("snippet").str().empty(),"links-only artifact exposed snippet");
    check(values.at(0).at("title").str()==values.at(0).at("url").str(),"links-only artifact exposed stored title");
  }
}
int main(int argc,char** argv) {
  try {
    check(argc==3,"fixture and working directories are required");
    const std::filesystem::path fixture=argv[1],work=argv[2];
    const auto config=load((fixture/"info.json").string()).at("original_manifest");
    auto output=Json::array();
    for(const std::string mode:{"rejected","links_only"})for(const bool inflight:{false,true})for(const bool child_scope:{false,true}) {
      const auto directory=work/("epoch-"+random_id());std::filesystem::create_directories(directory);
      Store store(directory/"db",[]{return std::int64_t{1700000000000};});
      auto denied=Json::parse(config.dump());
      if(mode=="rejected")denied.at("sites").at(0).at("review").set("status","rejected");
      else denied.at("sites").at(0).at("review").set("redistribution_policy","links_only");
      auto same_denied=Json::parse(denied.dump());same_denied.at("sites").at(0).at("review").set("approved_by","metadata-only-generation");
      store.activate_site_publication(SiteRegistry(config));
      store.activate_site_publication(SiteRegistry(denied));
      store.activate_site_publication(SiteRegistry(same_denied));
      GatedReader reader;WebService restricted(store,reader,{});
      Store activation(directory/"db",[]{return std::int64_t{1700000000000};});
      auto allowing=Json::parse(config.dump());if(child_scope)allowing.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/map/docs"])"));
      if(inflight)reader.before_return=[&]{activation.activate_site_publication(SiteRegistry(allowing));};
      auto request=Json::object();request.set("url","https://map.example/map/docs/a");request.set("freshness","refresh");
      const auto first=restricted.read("alice",request);const auto sid=first.at("snapshot_id").str();reader.before_return={};
      const auto previous=freeze(store);
      if(!inflight)activation.activate_site_publication(SiteRegistry(allowing));
      const auto old=store.snapshot("alice",sid);
      check(old.at("site_publication").at("artifact_generation").num()==3,"fixture did not acquire at metadata-only generation 3");
      check(old.at("site_publication").at("generation").num()==4,"fixture did not relax at generation 4");
      check(old.at("canonical_text").str()=="marker visible secret","private canonical body changed");
      check(old.at("indexed").flag()==(mode=="links_only"),"old artifact indexed field revived");
      restricted_hits(store,freeze(store),mode);restricted_hits(store,scan(store),mode);restricted_hits(store,previous,mode);
      WebService allowed(activation,reader,{},SiteRegistry(allowing));const auto fresh=allowed.read("alice",request);
      check(fresh.at("snapshot_id").str()!=sid,"allowing complete read reused restricted snapshot");
      check(reader.reader.calls==2,"unexpected number of complete synthetic worker responses");
      const auto current=page_hits(store,freeze(store));check(current.size()==1&&!current.at(0).at("snippet").str().empty(),"new allowing read did not restore current publication");
      restricted_hits(store,previous,mode);
      check(store.snapshot("alice",sid).at("indexed").flag()==(mode=="links_only"),"fresh read revived old snapshot");
      auto result=Json::object();result.set("mode",mode);result.set("inflight_relaxation",inflight);result.set("new_child_scope",child_scope);
      result.set("old_artifact_remains_restricted",true);result.set("fts_and_non_fts_checked",true);result.set("complete_read_restores_new_current_only",true);result.set("worker_calls",std::int64_t{reader.reader.calls});output.push(result);
    }
    std::cout<<output.dump()<<'\n';return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
