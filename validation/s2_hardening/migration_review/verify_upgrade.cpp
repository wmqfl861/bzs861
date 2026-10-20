#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
using namespace bzs;
void check(bool okay,const std::string& label) { if(!okay)throw std::runtime_error(label); }
Json load(const std::string& file) { std::ifstream stream(file);return Json::parse(std::string(std::istreambuf_iterator<char>(stream),{})); }
Json freeze(Store& store) { return store.search("alice",parse_query(Json::string("marker"))); }
std::size_t hits(Store& store,const Json& session) { return store.page("alice",session.at("session_id").str(),"",100).at("hits").size(); }
struct Reader final:WorkerPort {
  int calls=0;
  Json call(const std::string& method,const Json& params,int)override {
    check(method=="fetch.extract","unexpected worker method");++calls;
    auto value=Json::parse(R"({"status":"ok","html":"<p>marker visible secret</p>","text":"marker visible secret","content_type":"text/html","title":"page title","extractor":"fixture-crawl/1","extract_status":"ok","final_url":"","http_status":200,"charset":"utf-8","index_allowed":true,"links":[],"metadata":{},"warnings":[],"redirects":[],"usage":{"external_requests":1,"content_bytes":100},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker visible secret","snippet_title":"page title","directives":[],"diagnostics":[]}})");
    value.set("final_url",params.at("url"));return value;
  }
};
int main(int argc,char** argv) {
  try {
    check(argc==3,"fixture and working directories are required");
    const std::filesystem::path fixture=argv[1],work=argv[2];
    const auto info=load((fixture/"info.json").string());
    const auto original=info.at("snapshot"),old_session=info.at("session");
    const auto sid=original.at("snapshot_id").str(),job=info.at("job").at("job_id").str(),url=original.at("url").str();
    auto output=Json::array();
    for(const std::string mode:{"narrowed_scope","explicit_empty"}) {
      const auto directory=work/(mode+"-"+random_id());
      std::filesystem::create_directories(directory);std::filesystem::copy_file(fixture/"db",directory/"db");
      auto config=Json::parse(info.at("original_manifest").dump());
      if(mode=="explicit_empty")config.set("sites",Json::array());
      else config.at("sites").at(0).set("path_prefixes",Json::parse(R"(["/map/docs"])"));
      Json evidence;
      {
        Store store(directory/"db",[]{return std::int64_t{1700000000000};});
        check(hits(store,freeze(store))==1,"migration before explicit activation unexpectedly hid current");
        check(hits(store,old_session)==1,"migration before explicit activation unexpectedly hid frozen");
        store.activate_site_publication(SiteRegistry(config));
        check(hits(store,freeze(store))==0,mode+": legacy crawl current remained published");
        check(hits(store,old_session)==0,mode+": legacy frozen remained published");
        const auto snapshot=store.snapshot("alice",sid);
        check(!snapshot.at("indexed").flag(),mode+": legacy snapshot indexed field stale");
        check(snapshot.at("canonical_text").str()==original.at("canonical_text").str(),"owner snapshot changed");
        evidence=store.evidence("alice",sid,0,6);
        const auto page=store.job_results("alice",job).at("items").at(0);
        check(page.at("body_read").flag()&&!page.at("indexed").flag(),"crawl receipt publication not gated");
      }
      Store store(directory/"db",[]{return std::int64_t{1700000000000};});
      check(hits(store,freeze(store))==0,"offline restart lost legacy current restriction");
      check(hits(store,old_session)==0,"offline restart revived legacy frozen row");
      check(store.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()==evidence.at("quote").str(),"evidence changed");
      Reader reader;WebService web(store,reader,{},SiteRegistry(config));auto request=Json::object();request.set("url",url);request.set("freshness","refresh");
      const auto refreshed=web.read("alice",request);
      check(reader.calls==1,"complete re-read did not call worker once");
      check(refreshed.at("snapshot_id").str()!=sid,"legacy snapshot reused after complete new read");
      check(hits(store,freeze(store))==1,"complete new read did not restore eligible current");
      check(hits(store,old_session)==0,"complete new read revived legacy frozen row");
      check(!store.snapshot("alice",sid).at("indexed").flag(),"old legacy snapshot became eligible again");
      const auto page=store.job_results("alice",job).at("items").at(0);
      check(!page.at("indexed").flag(),"old crawl receipt resurrected after new read");
      auto result=Json::object();result.set("scenario",mode);result.set("schema6_actual_crawl_fixture",true);result.set("legacy_current_withdrawn",true);result.set("legacy_frozen_withdrawn",true);result.set("private_snapshot_and_evidence_retained",true);result.set("complete_read_restores_new_current_only",true);result.set("worker_calls",std::int64_t{reader.calls});result.set("legacy_database_mutated_by_fixture",false);output.push(result);
    }
    std::cout<<output.dump()<<'\n';return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
