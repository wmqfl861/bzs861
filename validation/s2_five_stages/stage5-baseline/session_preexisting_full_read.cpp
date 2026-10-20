#define main bzs_prior_tests_main
#include "/workspace/scratch/80f41eb198a3/bzs861-sessions/tests/sessions.cpp"
#undef main
int main() {
  int failed=0;
  for(int mode=0;mode<3;++mode) {
    Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));const auto url=std::string("https://results.example/0");
    if(mode==2) { const auto doc=store.ingest("alice",{url,"marker body","text/plain","marker"});store.erase("alice",doc.at("document_id").str()); }
    else store.observe_web_publication("alice",url,url,policy(mode==1,false),store.publication_fence("alice",url));
    const auto id=web.submit_search("alice",input()).at("session_id").str();execute_one(web);const auto version=store.session_get("alice",id).at("result_set_version").str();const auto before=store.session_page("alice",id,version,"",20).at("hits");
    if(mode==2)store.ingest("alice",{url,"new marker body","text/plain","marker"});
    else { ParsedDocument parsed;parsed.title="fresh marker";parsed.text="fresh marker body";parsed.content_type="text/plain";parsed.parser_version="synthetic/1";parsed.publication=policy(true,true);parsed.publication.set("snippet_title",parsed.title);parsed.publication.set("snippet_text",parsed.text);auto meta=Json::object();meta.set("final_url",url);meta.set("redirects",Json::array());store.ingest_web("alice",{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",url)); }
    const auto after=store.session_page("alice",id,version,"",20).at("hits");bool safe=true;
    if(mode==1) { for(std::size_t i=0;i<after.size();++i)if(after.at(i).at("url").str()==url&&!after.at(i).at("snippet").str().empty())safe=false; }
    else safe=after.size()==2;
    std::cout<<"preexisting_mode="<<mode<<" before="<<before.size()<<" after="<<after.size()<<" sealed="<<safe<<'\n';if(!safe)++failed;
  }
  std::cout<<"3 cases, "<<failed<<" failures\n";return failed?1:0;
}
