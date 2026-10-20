#define main bzs_author_sessions_main
#include "/workspace/scratch/80f41eb198a3/bzs861-sessions/tests/sessions.cpp"
#undef main
struct AliasWorker: WorkerPort {
  Json call(const std::string&,const Json& args,int) override {
    const auto source=args.at("sources").at(0).str();auto body=Json::object(),results=Json::object(),entry=Json::object(),hits=Json::array(),hit=Json::object();
    hit.set("url",source=="source0"?"http://results.example/alias":"https://results.example/alias");hit.set("title",source=="source0"?"protected title":"");hit.set("snippet",source=="source0"?"protected canary":"");hit.set("rank",std::int64_t{1});hits.push(hit);entry.set("status","ok");entry.set("hits",hits);results.set(source,entry);body.set("results",results);return body;
  }
};
int main() {
  { Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
    for(int i=0;i<64;++i)store.search("alice",parse_query(Json::string("marker")));
    now+=3600001;
    try { const auto s=web.submit_search("alice",input());std::cout<<"expired_local_slots: "<<s.at("state").str()<<'\n'; }catch(const Error& e) { std::cout<<"expired_local_slots: "<<e.code<<'\n'; }
  }
  { Temp t;Store store(t.path/"db");AliasWorker worker;WebService web(store,worker,SourceRegistry(manifest()));const auto id=web.submit_search("alice",input()).at("session_id").str();
    for(int i=0;i<2;++i){auto lease=web.lease_search();web.execute_search(*lease);}const auto version=store.session_get("alice",id).at("result_set_version").str();
    const auto a=std::string("http://results.example/alias");store.observe_web_publication("alice",a,a,policy(false,false),store.publication_fence("alice",a));
    std::cout<<"dedup_alias_page: "<<store.session_page("alice",id,version,"",20).canonical()<<'\n';
  }
  std::cout<<"author_suite_result: "<<bzs_author_sessions_main()<<'\n';
}
