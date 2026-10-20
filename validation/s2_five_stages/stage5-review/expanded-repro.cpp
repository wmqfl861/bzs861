#define main bzs_author_sessions_main
#include "/workspace/scratch/80f41eb198a3/bzs861-sessions/tests/sessions.cpp"
#undef main
struct PairWorker: WorkerPort {
  std::string restricted, displayed;bool together=false, ranking_case=false;
  Json call(const std::string&,const Json& args,int) override {
    const auto source=args.at("sources").at(0).str();auto body=Json::object(),results=Json::object(),entry=Json::object(),hits=Json::array();
    const auto add=[&](const std::string& url,std::int64_t rank,const std::string& snippet) { auto hit=Json::object();hit.set("url",url);hit.set("title",snippet);hit.set("snippet",snippet);hit.set("rank",rank);hits.push(hit); };
    if(together||source=="source0")add(restricted,1,"protected canary");
    if(ranking_case)add("https://results.example/other",2,"other result");
    if(together||source=="source1")add(displayed,ranking_case?30:1,"");
    entry.set("status","ok");entry.set("hits",hits);results.set(source,entry);body.set("results",results);return body;
  }
};
std::pair<std::string,std::string> run_session(Store& store,WebService& web) {
  const auto id=web.submit_search("alice",input()).at("session_id").str();
  while(const auto lease=web.lease_search())web.execute_search(*lease);
  return {id,store.session_get("alice",id).at("result_set_version").str()};
}
int main() {
  try {
    { Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
      for(int i=0;i<64;++i)store.search("alice",parse_query(Json::string("marker")));
      now+=3600001;CHECK(web.submit_search("alice",input()).at("state").str()=="queued");std::cout<<"PASS expired_local_slots\n";
    }
    for(bool together:{false,true})for(bool utm:{false,true})for(bool deletion:{false,true}) {
      Temp t;Store store(t.path/"db");PairWorker worker;worker.together=together;worker.restricted=utm?"https://results.example/alias?utm_source=private":"http://results.example/alias";worker.displayed=utm?"https://results.example/alias?utm_source=public":"https://results.example/alias";
      if(utm)std::swap(worker.restricted,worker.displayed);  // The displayed URL may be chosen by an explicit better-ranked hit below.
      WebService web(store,worker,SourceRegistry(manifest(together?1:2)));
      const auto target=std::string("https://target.example/private");
      const auto doc=store.ingest("alice",{target,"private target","text/plain","private target"});
      ParsedDocument parsed;parsed.title="target";parsed.text="target text";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);parsed.publication.set("snippet_text",parsed.text);parsed.publication.set("snippet_title",parsed.title);
      auto meta=Json::object();meta.set("final_url",target);meta.set("redirects",Json::array());store.ingest_web("alice",{worker.restricted,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",worker.restricted));
      const auto [id,version]=run_session(store,web);CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==1);
      if(deletion) { store.erase("alice",doc.at("document_id").str());store.ingest("alice",{target,"revived target","text/plain","revived target"}); }
      else store.observe_web_publication("alice",target,target,policy(false,false),store.publication_fence("alice",target));
      CHECK(store.session_page("alice",id,version,"",20).at("hits").size()==0);
      Store reopened(t.path/"db");CHECK(reopened.session_page("alice",id,version,"",20).at("hits").size()==0);
      std::cout<<"PASS contributor_chain "<<together<<' '<<utm<<' '<<deletion<<'\n';
    }
    { Temp t;Store store(t.path/"db");PairWorker worker;worker.together=true;worker.ranking_case=true;worker.restricted="http://results.example/alias";worker.displayed="https://results.example/alias";WebService web(store,worker,SourceRegistry(manifest(1)));
      const auto [id,version]=run_session(store,web);auto page=store.session_page("alice",id,version,"",20);
      std::cout<<"ranked_first="<<page.at("hits").at(0).at("url").str()<<'\n';
      CHECK(page.at("hits").at(0).at("url").str()==worker.displayed);
    }
    std::cout<<"All independent cases passed; "<<assertions<<" assertions\n";return 0;
  }catch(const std::exception& e) { std::cerr<<"FAIL independent: "<<e.what()<<'\n';return 1; }
}
