#define main bzs_author_sessions_main
#include "/workspace/scratch/80f41eb198a3/bzs861-sessions/tests/sessions.cpp"
#undef main
struct WideWorker:WorkerPort {
  static std::string url(int i){return "http://results.example/alias?utm_id="+std::to_string(i);}
  Json call(const std::string&,const Json&,int)override {
    auto out=Json::object(),results=Json::object(),entry=Json::object(),hits=Json::array();
    for(int i=0;i<24;++i){auto h=Json::object();h.set("url",url(i));h.set("title",i==0?"protected legacy title":"");h.set("snippet",i==0?"protected legacy canary":"");h.set("rank",static_cast<std::int64_t>(i+1));hits.push(h);}
    entry.set("status","ok");entry.set("hits",hits);results.set("source0",entry);out.set("results",results);return out;
  }
};
int main(){
  Temp t;Store store(t.path/"db");WideWorker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  const auto a=WideWorker::url(0);ParsedDocument parsed;parsed.title="marker";parsed.text="body";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);parsed.publication.set("snippet_title",parsed.title);parsed.publication.set("snippet_text",parsed.text);
  auto meta=Json::object();meta.set("final_url","https://target.example/extra");meta.set("redirects",Json::array());store.ingest_web("alice",{a,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",a));
  // Isolated synthetic equivalent of schema6 migration's incomplete crawl provenance.
  CHECK(sqlite3_exec(store.raw_handle(),R"(UPDATE snapshots SET site_bindings_json='{"version":"bzs-site-bindings-1","legacy_crawl_unverified":true,"urls":[]}'; UPDATE site_publication_state SET legacy_crawl_unverified=1;)",nullptr,nullptr,nullptr)==SQLITE_OK);
  auto query=input();query.set("per_source",std::int64_t{30});const auto id=web.submit_search("alice",query).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();
  const auto restricted=WideWorker::url(23);store.observe_web_publication("alice",restricted,restricted,policy(false,false),store.publication_fence("alice",restricted));
  const auto page=store.session_page("alice",id,version,"",20);std::cout<<page.canonical()<<'\n';
  return page.at("hits").size()==0?0:1;
}
