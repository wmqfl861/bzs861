#include "bzs/error.hpp"
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace bzs;
namespace {
const std::string a="https://review.example/a",b="https://review.example/b",x="https://review.example/x";
struct Temporary {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-refresh-review-"+random_id());
  Temporary(){std::filesystem::create_directories(path);}
  ~Temporary(){std::error_code ec;std::filesystem::remove_all(path,ec);}
};
struct Reader:WorkerPort {
  bool index=true,snippets=true;
  std::string final_url;
  Json redirects=Json::array();
  std::function<void()> during;
  int calls=0;
  Json call(const std::string& method,const Json& args,int) override {
    if(method!="fetch.extract")throw std::runtime_error("unexpected worker method");
    ++calls;
    auto response=Json::parse(R"({"status":"ok","html":"marker secret body","text":"marker secret body","content_type":"text/plain","title":"marker secret title","extractor":"independent-refresh-review/1","extract_status":"ok","final_url":"","http_status":200,"index_allowed":true,"links":[],"metadata":{},"warnings":[],"redirects":[],"usage":{"external_requests":1,"content_bytes":18},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker secret body","snippet_title":"marker secret title","directives":[],"diagnostics":[]}})");
    response.set("final_url",final_url.empty()?args.at("url").str():final_url);
    response.set("redirects",redirects);
    response.at("usage").set("external_requests",static_cast<std::int64_t>(redirects.size()+1));
    response.set("index_allowed",index);
    auto policy=response.at("publication");
    policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippets);
    if(!index||!snippets)policy.set("snippet_text","");
    if(!index)policy.set("snippet_title","");
    if(!index)policy.at("directives").push(Json::string("noindex"));
    else if(!snippets)policy.at("directives").push(Json::string("nosnippet"));
    if(during)during();
    return response;
  }
};
Json args(const std::string& url){auto j=Json::object();j.set("url",url);j.set("freshness","refresh");return j;}
Json freeze(Store& store){return store.search("alice",parse_query(Json::string("marker")));}
Json hits(Store& store,const Json& session){return store.page("alice",session.at("session_id").str(),"",100).at("hits");}
Json find_url(const Json& rows,const std::string& url){for(std::size_t i=0;i<rows.size();++i)if(rows.at(i).at("url").str()==url)return rows.at(i);return Json();}
void check(bool condition,const std::string& reason){if(!condition)throw std::runtime_error(reason);}

void race(const std::string& mode) {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  const bool same=mode=="same_noindex"||mode=="same_nosnippet";
  const bool middle=mode=="middle_noindex";
  const bool restored=mode=="alias_restrict_then_allow"||mode=="alias_nosnippet_then_allow";
  const bool snippet=mode=="same_nosnippet"||mode=="alias_nosnippet_then_allow";
  if(!same){old_reader.final_url=b;old_reader.redirects.push(Json::string(middle?x:b));if(middle)old_reader.redirects.push(Json::string(b));}
  auto original=old_web.read("alice",args(a));auto original_session=freeze(first);
  auto proof=first.evidence("alice",original.at("snapshot_id").str(),0,6);
  new_reader.index=snippet;new_reader.snippets=false;
  const auto restrictive_target=same?a:middle?x:b;
  old_reader.during=[&]{
    new_web.read("alice",args(restrictive_target));
    if(restored){new_reader.index=true;new_reader.snippets=true;new_web.read("alice",args(restrictive_target));}
  };
  auto late=old_web.read("alice",args(a));old_reader.during={};
  auto current=find_url(hits(first,freeze(first)),a);
  if(snippet)check(!current.is_null()&&current.at("snippet").str().empty(),"late A published its old snippet");
  else check(current.is_null(),"late A became index-visible");
  if(restored){auto independent=find_url(hits(first,freeze(first)),b);check(!independent.is_null()&&!independent.at("snippet").str().empty(),"late A revoked newer independent B");}
  auto previous=find_url(hits(first,original_session),a);
  if(snippet)check(!previous.is_null()&&previous.at("snippet").str().empty(),"old frozen A snippet resurrected");
  else check(previous.is_null(),"old frozen A index entry resurrected");
  check(first.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="marker","private evidence changed");
  check(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","late canonical body lost");
  old_reader.index=true;old_reader.snippets=true;new_reader.index=true;new_reader.snippets=true;
  old_web.read("alice",args(a));
  check(!find_url(hits(first,freeze(first)),a).is_null(),"explicit fresh complete A cannot recover");
  previous=find_url(hits(first,original_session),a);
  if(snippet)check(!previous.is_null()&&previous.at("snippet").str().empty(),"fresh read revived old frozen snippet");
  else check(previous.is_null(),"fresh read revived old frozen row");
}

void deleted_alias() {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  auto target=new_web.read("alice",args(b));
  old_reader.final_url=b;old_reader.redirects.push(Json::string(b));
  old_reader.during=[&]{second.erase("alice",target.at("document_id").str());};
  bool refused=false;try{old_web.read("alice",args(a));}catch(const Error& error){check(error.code=="E_TARGET_DELETED","unexpected deletion error "+error.code);refused=true;}
  check(refused,"deleted final alias was re-published through late A");
  check(find_url(hits(first,freeze(first)),a).is_null(),"deleted alias A remains searchable");
}
}
int main(int argc,char** argv){
  std::vector<std::string> names={"same_noindex","same_nosnippet","alias_noindex","alias_restrict_then_allow","alias_nosnippet_then_allow","middle_noindex","deleted_final_alias"};
  int failures=0,executed=0;
  for(const auto& name:names){if(argc>1&&name!=argv[1])continue;++executed;try{if(name=="deleted_final_alias")deleted_alias();else race(name);std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& error){++failures;std::cout<<"FAIL "<<name<<": "<<error.what()<<'\n';}}
  std::cout<<executed<<" independent cases, "<<failures<<" failures\n";return failures?1:0;
}
