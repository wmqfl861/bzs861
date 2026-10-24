// Independent, small P1-3 semantic oracle. Not a repository test or production change.
// Compare public one-shot result sets to the existing exact predicate; no network.
#include "bzs/store.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
using namespace bzs;
std::size_t checks=0, comparisons=0;
#define CHECK(x) do { ++checks; if(!(x))throw std::runtime_error(std::string("failed ")+#x+" line "+std::to_string(__LINE__)); } while(false)
Json leaf(const std::string& op,const std::string& value,const std::string& field="any") {auto j=Json::object();j.set("op",op);j.set("value",value);j.set("field",field);return j;}
Json compound(const std::string& op,const std::vector<Json>& children) {auto j=Json::object(),a=Json::array();for(const auto& c:children)a.push(c);j.set("op",op);if(op=="not")j.set("child",a.at(0));else j.set("children",a);return j;}
void utf8(std::string& out,char32_t cp) { if(cp<0x80)out+=char(cp);else if(cp<0x800){out+=char(0xc0|(cp>>6));out+=char(0x80|(cp&63));}else if(cp<0x10000){out+=char(0xe0|(cp>>12));out+=char(0x80|((cp>>6)&63));out+=char(0x80|(cp&63));}else {out+=char(0xf0|(cp>>18));out+=char(0x80|((cp>>12)&63));out+=char(0x80|((cp>>6)&63));out+=char(0x80|(cp&63));}}
struct Document {std::string owner,id,url,title,body,type;};
int main() {
 try {
  std::int64_t clock=1800000000000LL;Store store(":memory:",[&]{return clock;});std::vector<Document> docs;
  const auto add=[&](const std::string& owner,const std::string& slug,const std::string& title,const std::string& body){
    const auto u="https://independent-candidates.example/"+slug;const auto snap=store.ingest(owner,{u,body,"text/plain",title});
    docs.push_back({owner,snap.at("document_id").str(),u,snap.at("title").str(),snap.at("canonical_text").str(),snap.at("content_type").str()});++clock;return docs.back();
  };
  add("alice","apple","TITLE Apple","apple pineapple machine learning");
  add("alice","unicode","tést Title","testé test\xcc\x81x café cafe\xcc\x81 Москва МОСКВА xiphone手机y 中英文");
  add("alice","symbols","C++ module","modern C++20 coroutines C# .NET foo-bar module:thing v1.2.3");
  add("alice","spaces","only title needle","machine  learning phrase with gap");
  add("alice","urlneedle","title independent","body independent");
  add("alice","emoji","😀 title","😀🚀𠮷 unicode end");
  add("alice","cross","machine","learning split across title and body");
  add("bob","private-urlneedle","TITLE Apple private","apple machine learning café C++20 overflowneedle exact tail");
  std::string overflow="overflow leading ";for(char32_t cp=0x4000;cp<0x4000+23000;++cp)utf8(overflow,cp);overflow+=" overflowneedle exact tail";
  const auto wide=add("alice","overflow","bounded overflow title",overflow);CHECK(indexed_grams(wide.body,'b')=="boverflow");
  std::vector<Json> queries;
  const std::vector<std::pair<std::string,std::string>> leaves={{"phrase","app"},{"phrase","achine learn"},{"term","test"},{"phrase","fé"},{"term","iPhone手机"},{"literal","C++"},{"phrase","C++20"},{"literal","C#"},{"literal","v1.2"},{"literal","v1.2.3"},{"phrase","machine learning"},{"phrase","machine  learning"},{"term","Москва"},{"term","москва"},{"term","МОСКВА"},{"phrase","😀🚀"},{"phrase","𠮷"},{"phrase","中英文"},{"phrase","needle"},{"phrase","overflowneedle exact"},{"phrase","not-present"},{"term","TITLE"},{"phrase","cafe\xcc\x81"},{"literal","foo-bar"}};
  for(const auto& field:{"any","title","body","url"})for(const auto& [op,value]:leaves)queries.push_back(leaf(op,value,field));
  const auto originals=queries;
  for(std::size_t i=0;i<originals.size();i+=7) {
    const auto& a=originals[i];const auto& b=originals[(i+5)%originals.size()];
    queries.push_back(compound("and",{a,b}));queries.push_back(compound("or",{a,b}));
    queries.push_back(compound("and",{a,compound("not",{b})}));
    queries.push_back(compound("or",{a,compound("not",{b})}));
  }
  for(const auto& request:queries) {
    const auto q=parse_query(request);std::set<std::string> expected,actual;
    for(const auto& d:docs){++comparisons;if(d.owner=="alice"&&matches(q,d.title,d.body,d.url,metadata_host(d.url),d.type))expected.insert(d.id);}
    const auto answer=store.search_once("alice",q,2000,100);CHECK(!answer.at("partial").flag());
    const auto hits=answer.at("hits");for(std::size_t i=0;i<hits.size();++i)actual.insert(hits.at(i).at("document_id").str());
    if(actual!=expected){std::cerr<<"query="<<request.canonical()<<" expected="<<expected.size()<<" actual="<<actual.size()<<'\n';}
    CHECK(actual==expected);
  }
  // A short candidate budget must be applied after intersecting both safe
  // constraints. Newer word matches and another owner's exact match are noise.
  Store bounded(":memory:",[&]{return clock;});
  const auto wanted=bounded.ingest("alice",{"https://independent-candidates.example/old","anchor rare phrase survives","text/plain","old wanted"});++clock;
  for(int i=0;i<32;++i){bounded.ingest("alice",{"https://independent-candidates.example/recent-"+std::to_string(i),"anchor noisy","text/plain","newer noise"});++clock;}
  bounded.ingest("bob",{"https://independent-candidates.example/foreign","anchor rare phrase survives","text/plain","foreign exact"});
  const auto and_query=parse_query(compound("and",{leaf("term","anchor"),leaf("phrase","rare phrase survives")}));
  CHECK(fts_candidates(and_query).has_value());CHECK(substring_candidates(and_query).has_value());
  const auto and_answer=bounded.search_once("alice",and_query,1,100);CHECK(and_answer.at("returned_count").num()==1);CHECK(and_answer.at("scanned_candidates").num()==1);CHECK(!and_answer.at("partial").flag());CHECK(and_answer.at("hits").at(0).at("document_id").str()==wanted.at("document_id").str());
  const auto phrase_answer=bounded.search_once("alice",parse_query(leaf("phrase","rare phrase survives")),1,100);CHECK(phrase_answer.at("returned_count").num()==1);CHECK(phrase_answer.at("hits").at(0).at("document_id").str()==wanted.at("document_id").str());
  auto report=Json::object();report.set("checks",static_cast<std::int64_t>(checks));report.set("query_shapes",static_cast<std::int64_t>(queries.size()));report.set("exact_document_comparisons",static_cast<std::int64_t>(comparisons));report.set("candidate_result_set_mismatches",std::int64_t{0});report.set("overflow_field_retained",true);report.set("and_intersection_before_limit",true);report.set("owner_isolation_before_limit",true);report.set("external_network_requests",std::int64_t{0});std::cout<<report.dump(true)<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"independent candidate probe after "<<checks<<" checks: "<<e.what()<<'\n';return 1;}
}
