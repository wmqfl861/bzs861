#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <cstring>
#include <functional>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
using namespace bzs;
namespace {
int checks=0;
void check(bool pass,const char* message) { ++checks;if(!pass)throw std::runtime_error(message); }
Json request(const std::string& op,const Json& params) { auto value=Json::object();value.set("operation",op);value.set("params",params);return value; }
Json args(std::int64_t limit=100) { auto value=Json::object();value.set("query","marker");value.set("limit",limit);return value; }
Json once(Service& api,const std::string& owner="alice",std::int64_t limit=100) { return api.dispatch({owner,false},request("search.once",args(limit))); }
void rejects(const std::function<void()>& call,const char* code) { try {call();}catch(const Error& e){check(e.code==code,"unexpected rejection code");return;}throw std::runtime_error("expected rejection did not happen"); }
std::int64_t scalar(sqlite3* db,const std::string& query) {
 sqlite3_stmt* row=nullptr;check(sqlite3_prepare_v2(db,query.c_str(),-1,&row,nullptr)==SQLITE_OK,"prepare inspection");
 const auto rc=sqlite3_step(row);const auto result=rc==SQLITE_ROW?sqlite3_column_int64(row,0):-1;sqlite3_finalize(row);check(rc==SQLITE_ROW,"read inspection");return result;
}
const char* protected_tables[]={"sessions","session_rows","cursors","search_streams","search_stream_receipts","search_stream_sources","search_versions","search_version_rows","search_event_log"};
struct Guard {int refused=0;};
int authorizer(void* context,int action,const char* table,const char*,const char*,const char*) {
 if(action==SQLITE_INSERT||action==SQLITE_UPDATE||action==SQLITE_DELETE)for(const auto* name:protected_tables)
  if(table&&std::strcmp(table,name)==0) {++static_cast<Guard*>(context)->refused;return SQLITE_DENY;}
 if(action==SQLITE_CREATE_TEMP_TABLE) {++static_cast<Guard*>(context)->refused;return SQLITE_DENY;}
 return SQLITE_OK;
}
std::map<std::string,std::int64_t> footprint(sqlite3* db) {
 std::map<std::string,std::int64_t> result;
 for(const auto* table:protected_tables)if(scalar(db,"SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name='"+std::string(table)+"'"))
  result.emplace(table,scalar(db,"SELECT COUNT(*) FROM "+std::string(table)));
 return result;
}
Json web(Store& store,const std::string& url,bool index,bool snippets,bool excerpt=false) {
 ParsedDocument parsed;parsed.title="marker private title";parsed.text="marker canonical secret";parsed.content_type="text/plain";parsed.parser_version="independent-once-probe/1";parsed.index_allowed=index;
 auto policy=Json::object();policy.set("version","bzs-publication-1");policy.set("index_allowed",index);policy.set("snippet_allowed",snippets);policy.set("follow_allowed",true);
 policy.set("snippet_text",snippets?(excerpt?"marker safe excerpt":parsed.text):"");policy.set("snippet_title",snippets?"marker safe title":"");policy.set("directives",Json::array());policy.set("diagnostics",Json::array());parsed.publication=policy;
 auto meta=Json::object();meta.set("final_url",url);meta.set("redirects",Json::array());
 return store.ingest_web("alice",{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",url));
}
Json hit(const Json& result,const std::string& url) {const auto rows=result.at("hits");for(std::size_t i=0;i<rows.size();++i)if(rows.at(i).at("url").str()==url)return rows.at(i);return Json();}
}
int main(int argc,char** argv) {
 try {
  if(argc!=2)throw std::runtime_error("usage: p1_2_probe <fixture-directory>");
  std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});FixtureCatalog fixtures(argv[1]);Service api(store,fixtures,"/tmp/unused-independent-once-backup");
  const auto empty_before=footprint(store.raw_handle());
  check(once(api).at("returned_count").num()==0,"empty one-shot result");
  check(footprint(store.raw_handle())==empty_before,"empty read allocated durable search state");
  const auto a=store.ingest("alice",{"https://one.example/a","marker café 😀 first","text/plain","first"});
  const auto b=store.ingest("alice",{"https://one.example/b","marker second","text/plain","second"});
  store.ingest("bob",{"https://one.example/bob","marker private bob","text/plain","bob"});
  const auto query=parse_query(Json::string("marker"));const auto frozen=store.search("alice",query);const auto id=frozen.at("session_id").str();
  const auto first=store.page("alice",id,"",1);const auto cursor=first.at("next_cursor").str();const auto second=store.page("alice",id,cursor,1);
  for(int i=1;i<64;++i)store.search("alice",query);
  rejects([&]{store.search("alice",query);},"E_QUOTA");
  const auto before=footprint(store.raw_handle());Guard guard;check(sqlite3_set_authorizer(store.raw_handle(),authorizer,&guard)==SQLITE_OK,"install write denial");
  for(int i=0;i<70;++i) {const auto value=once(api);check(value.at("returned_count").num()==2,"one-shot failed under full quota");check(value.at("result_mode").str()=="single","wrong result mode");check(!value.has("session_id")&&!value.has("result_set_version")&&!value.has("next_cursor"),"ephemeral response exposed session identity");}
  check(once(api,"bob").at("returned_count").num()==1,"owner isolation");
  check(once(api,"never-seen-owner").at("returned_count").num()==0,"empty unknown owner");
  check(footprint(store.raw_handle())==before,"durable search footprint changed");check(guard.refused==0,"one-shot attempted forbidden mutation");
  rejects([&]{once(api,"alice",101);},"E_SCHEMA");
  check(sqlite3_set_authorizer(store.raw_handle(),nullptr,nullptr)==SQLITE_OK,"clear write denial");
  check(store.page("alice",id,"",1).canonical()==first.canonical(),"first frozen page changed");
  check(store.page("alice",id,cursor,1).canonical()==second.canonical(),"frozen continuation changed");
  store.erase("alice",b.at("document_id").str());check(once(api).at("returned_count").num()==1,"deleted import returned");
  const std::string blocked="https://one.example/noindex",suppressed="https://one.example/nosnippet",excerpt="https://one.example/excerpt";
  web(store,blocked,false,false);web(store,suppressed,true,false);web(store,excerpt,true,true,true);
  const auto projected=once(api);check(hit(projected,blocked).is_null(),"noindex result returned");
  const auto hidden=hit(projected,suppressed);check(!hidden.is_null(),"nosnippet result absent");check(hidden.at("title").str()==suppressed&&hidden.at("snippet").str().empty()&&hidden.at("highlights").size()==0,"nosnippet content escaped");
  const auto safe=hit(projected,excerpt);check(!safe.is_null(),"excerpt result absent");check(safe.at("snippet").str().find("secret")==std::string::npos&&safe.at("snippet_kind").str()=="publication_excerpt"&&safe.at("highlights").size()==0,"excerpt mislabeled canonical offsets");
  const auto canonical=hit(projected,"https://one.example/a");check(!canonical.is_null()&&canonical.at("highlights").size()>0,"canonical highlights missing");
  for(std::size_t i=0;i<canonical.at("highlights").size();++i) {const auto h=canonical.at("highlights").at(i);const auto proof=store.evidence("alice",a.at("snapshot_id").str(),h.at("start_byte").num(),h.at("end_byte").num());check(proof.at("location_verified").flag(),"invalid canonical highlight evidence");}
  store.revoke_principal("admin","alice");rejects([&]{once(api);},"E_PRINCIPAL_REVOKED");check(once(api,"bob").at("returned_count").num()==1,"revocation crossed owner boundary");
  std::cout<<"{\"checks\":"<<checks<<",\"failures\":0,\"forbidden_session_writes\":"<<guard.refused<<",\"external_requests\":0}\n";return 0;
 }catch(const Error& e){std::cerr<<"ERROR "<<e.code<<": "<<e.what()<<" (after "<<checks<<" checks)\n";return 2;}
 catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<" (after "<<checks<<" checks)\n";return 1;}
}
