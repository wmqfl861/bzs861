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
const std::string a="https://review.example/a",b="https://review.example/b",x="https://review.example/x",c="https://review.example/c";
struct Temporary {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-refresh-review-"+random_id());
  Temporary(){std::filesystem::create_directories(path);}
  ~Temporary(){std::error_code ec;std::filesystem::remove_all(path,ec);}
};
struct Reader:WorkerPort {
  bool index=true,snippets=true;
  bool failed=false,omit_publication=false;
  std::string text="marker secret body",title="marker secret title";
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
    response.set("html",text);response.set("text",text);response.set("title",title);
    response.at("usage").set("external_requests",static_cast<std::int64_t>(redirects.size()+1));
    response.set("index_allowed",index);
    auto policy=response.at("publication");
    policy.set("snippet_text",text);policy.set("snippet_title",title);
    policy.set("index_allowed",index);policy.set("snippet_allowed",index&&snippets);
    if(!index||!snippets)policy.set("snippet_text","");
    if(!index)policy.set("snippet_title","");
    if(!index)policy.at("directives").push(Json::string("noindex"));
    else if(!snippets)policy.at("directives").push(Json::string("nosnippet"));
    if(failed) {
      auto observed=Json::parse(policy.dump());observed.set("snippet_text","");observed.set("snippet_title","");
      response.set("publication_observation",observed);response.set("status","timeout");response.set("error","independent response body timeout");
    }
    if(omit_publication) {
      auto legacy=Json::object();for(const auto& key:response.keys())if(key!="publication")legacy.set(key,response.at(key));response=legacy;
    }
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

void failed_restriction(WebService& web,const std::string& target) {
  bool failed=false;try{web.read("alice",args(target));}catch(const Error& error){check(error.code=="E_FETCH_TIMEOUT","unexpected failed observation result "+error.code);failed=true;}
  check(failed,"failed observation manufactured a snapshot");
}
void repeat_failure(bool snippet) {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  auto original=old_web.read("alice",args(a));auto original_session=freeze(first);
  auto proof=first.evidence("alice",original.at("snapshot_id").str(),0,6);
  new_reader.index=snippet;new_reader.snippets=false;new_reader.failed=true;
  failed_restriction(new_web,a);
  old_reader.during=[&]{failed_restriction(new_web,a);};
  auto late=old_web.read("alice",args(a));old_reader.during={};
  auto current=find_url(hits(first,freeze(first)),a);
  if(snippet)check(!current.is_null()&&current.at("snippet").str().empty(),"second identical failed nosnippet did not fence late snippet");
  else check(current.is_null(),"second identical failed noindex did not fence late index");
  auto previous=find_url(hits(first,original_session),a);
  if(snippet)check(!previous.is_null()&&previous.at("snippet").str().empty(),"failed nosnippet revived frozen summary");else check(previous.is_null(),"failed noindex revived frozen row");
  check(first.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="marker","failed observation changed evidence");
  check(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","failed observation lost private late body");
  new_reader.failed=false;new_reader.index=true;new_reader.snippets=true;new_web.read("alice",args(a));
  check(!find_url(hits(first,freeze(first)),a).at("snippet").str().empty(),"complete fresh read after pulses did not recover");
}
void legacy_nosnippet() {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;old_reader.final_url=b;old_reader.redirects.push(Json::string(b));
  WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  old_web.read("alice",args(a));auto original_session=freeze(first);
  old_reader.omit_publication=true;new_reader.snippets=false;
  old_reader.during=[&]{new_web.read("alice",args(b));};
  auto late=old_web.read("alice",args(a));old_reader.during={};
  new_reader.snippets=true;new_web.read("alice",args(b));
  auto current=find_url(hits(first,freeze(first)),a);
  check(!current.is_null()&&current.at("snippet").str().empty(),"compatible unversioned artifact fell back to canonical snippet after B allowed");
  check(find_url(hits(first,original_session),a).at("snippet").str().empty(),"compatible artifact revived original frozen snippet");
  check(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","compatible artifact body lost");
  check(!find_url(hits(first,freeze(first)),b).at("snippet").str().empty(),"compatible artifact revoked new independent B");
}
void newer_current(bool snippet,bool missing_at_start=false) {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  if(!missing_at_start)old_web.read("alice",args(a));
  auto original_session=freeze(first);Json newest,new_session;
  old_reader.during=[&]{
    new_reader.index=snippet;new_reader.snippets=false;new_web.read("alice",args(a));
    new_reader.index=true;new_reader.snippets=true;new_reader.text="marker newest independent body";new_reader.title="marker newest title";
    newest=new_web.read("alice",args(a));new_session=freeze(second);
  };
  auto late=old_web.read("alice",args(a));old_reader.during={};
  auto cached=first.cached("alice",a);check(cached&&cached->at("snapshot_id").str()==newest.at("snapshot_id").str(),"late old response replaced a newer current snapshot");
  check(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","late private artifact was not retained");
  for(const auto& session:{freeze(first),new_session}){auto row=find_url(hits(first,session),a);check(!row.is_null()&&row.at("snippet").str().find("newest")!=std::string::npos,"late old response revoked newer fresh current/frozen row");}
  auto previous=find_url(hits(first,original_session),a);if(snippet&&!missing_at_start)check(!previous.is_null()&&previous.at("snippet").str().empty(),"old frozen snippet revived");else check(previous.is_null(),"old frozen row revived");
}
void retargeted_current() {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;old_reader.final_url=b;old_reader.redirects.push(Json::string(b));WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});
  old_web.read("alice",args(a));auto old_session=freeze(first);Json current,new_session;
  old_reader.during=[&]{
    new_reader.final_url=c;new_reader.redirects=Json::parse("[\""+c+"\"]");new_reader.text="marker current C body";
    current=new_web.read("alice",args(a));new_session=freeze(second);
    new_reader.final_url="";new_reader.redirects=Json::array();new_reader.index=false;new_reader.snippets=false;new_web.read("alice",args(b));
    new_reader.index=true;new_reader.snippets=true;new_web.read("alice",args(b));
  };
  auto late=old_web.read("alice",args(a));old_reader.during={};
  check(first.cached("alice",a)->at("snapshot_id").str()==current.at("snapshot_id").str(),"late A-to-B replaced newer A-to-C representation");
  check(find_url(hits(first,old_session),a).is_null(),"old A-to-B frozen row survived B noindex");
  for(const auto& session:{freeze(first),new_session})check(!find_url(hits(first,session),a).at("snippet").str().empty(),"B restriction suppressed independent current/frozen A-to-C");
  check(!find_url(hits(first,freeze(first)),b).at("snippet").str().empty(),"old A-to-B restricted new B");
  check(first.snapshot("alice",late.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","retargeted late body lost");
}
void unrelated_observation(bool other_owner) {
  Temporary temp;Store first(temp.path/"state"),second(temp.path/"state");
  Reader old_reader,new_reader;WebService old_web(first,old_reader,{}),new_web(second,new_reader,{});old_web.read("alice",args(a));auto frozen=freeze(first);
  new_reader.index=false;new_reader.snippets=false;old_reader.during=[&]{new_web.read(other_owner?"bob":"alice",args(other_owner?a:b));};
  old_web.read("alice",args(a));old_reader.during={};
  for(const auto& session:{freeze(first),frozen})check(!find_url(hits(first,session),a).at("snippet").str().empty(),"unrelated owner or URL global sequence suppressed A");
}
}
int main(int argc,char** argv){
  std::vector<std::string> names={"same_noindex","same_nosnippet","alias_noindex","alias_restrict_then_allow","alias_nosnippet_then_allow","middle_noindex","deleted_final_alias","repeat_failed_noindex","repeat_failed_nosnippet","legacy_unversioned_nosnippet","newer_current_noindex","newer_current_nosnippet","retargeted_current","other_owner_negative_control","other_url_negative_control","empty_capture_newer_current_noindex","empty_capture_newer_current_nosnippet"};
  int failures=0,executed=0;
  for(const auto& name:names){if(argc>1&&name!=argv[1])continue;++executed;try{if(name=="deleted_final_alias")deleted_alias();else if(name=="repeat_failed_noindex"||name=="repeat_failed_nosnippet")repeat_failure(name=="repeat_failed_nosnippet");else if(name=="legacy_unversioned_nosnippet")legacy_nosnippet();else if(name=="newer_current_noindex"||name=="newer_current_nosnippet")newer_current(name=="newer_current_nosnippet");else if(name=="empty_capture_newer_current_noindex"||name=="empty_capture_newer_current_nosnippet")newer_current(name=="empty_capture_newer_current_nosnippet",true);else if(name=="retargeted_current")retargeted_current();else if(name=="other_owner_negative_control"||name=="other_url_negative_control")unrelated_observation(name=="other_owner_negative_control");else race(name);std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& error){++failures;std::cout<<"FAIL "<<name<<": "<<error.what()<<'\n';}}
  std::cout<<executed<<" independent cases, "<<failures<<" failures\n";return failures?1:0;
}
