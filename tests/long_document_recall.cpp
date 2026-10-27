#include "bzs/store.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
using namespace bzs;
#define CHECK(c) do { if(!(c))throw std::runtime_error(std::string(#c)+" at "+std::to_string(__LINE__)); } while(false)
static std::int64_t gram_count(Store& store,const Query& query) {
  auto expression=substring_candidates(query);CHECK(expression.has_value());
  sqlite3_stmt* s=nullptr;
  CHECK(sqlite3_prepare_v2(store.raw_handle(),"SELECT count(*) FROM substring_fts WHERE substring_fts MATCH ?",-1,&s,nullptr)==SQLITE_OK);
  sqlite3_bind_text(s,1,expression->c_str(),-1,SQLITE_TRANSIENT);
  CHECK(sqlite3_step(s)==SQLITE_ROW);auto n=sqlite3_column_int64(s,0);sqlite3_finalize(s);return n;
}
static bool has(const Json& result,const std::string& url) {
  const auto hits=result.at("hits");
  for(std::size_t i=0;i<hits.size();++i)if(hits.at(i).at("url").str()==url)return true;
  return false;
}
static void long_document_recall() {
  std::ifstream file(std::string(BZS_FIXTURE_DIR)+"/recall_corpus.txt",std::ios::binary);
  std::stringstream input;input<<file.rdbuf();const auto seed=input.str();CHECK(seed.size()>1000&&seed.size()<1024*1024);
  std::string pool;while(pool.size()<100000)pool+=seed;
  std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});
  const std::string target="https://recall.example/old";
  const std::string body=pool.substr(0,30000)+"\nThe reference covers machine learning, node.js, and café examples.\n";
  store.ingest("alice",{target,body,"text/plain","Archive"});
  for(int i=0;i<4000;++i) {
    ++now;auto offset=(static_cast<std::size_t>(i)*7919)%(pool.size()-30000);
    while(!utf8_boundary(pool,offset))++offset;
    auto end=offset+30000;while(!utf8_boundary(pool,end))++end;
    const auto text=pool.substr(offset,end-offset);
    CHECK(text.size()>=30000);
    store.ingest("alice",{"https://recall.example/"+std::to_string(i),text,"text/plain","Archive"});
  }
  // The natural prose deliberately saturates the old necessary-gram filter:
  // machine learns / learning, node.json, and cafeteria / afe-with-accent.
  // It contains no true target. No corpus or live network dependency is used.
  for(const auto* expression:{"\"machine learning\"","node.js","café","\"machine learning\" OR node.js",
                              "reference AND \"machine learning\"","inbody:node.js"}) {
    auto query=parse_query(Json::string(expression));
    const auto count=gram_count(store,query);
    if(std::string(expression)=="\"machine learning\""||std::string(expression)=="node.js"||std::string(expression)=="café")CHECK(count>2000);
    auto start=std::chrono::steady_clock::now();auto result=store.search_once("alice",query,2000,100);
    std::cout<<expression<<" gram_candidates="<<count<<" found="<<has(result,target)
             <<" scanned="<<result.at("scanned_candidates").num()<<" partial="<<result.at("partial").flag()
             <<" elapsed_ms="<<std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count()<<std::endl;
    CHECK(has(result,target));CHECK(result.at("scanned_candidates").num()<=2000);
  }
  const auto frozen=store.search("alice",parse_query(Json::string("\"machine learning\"")),2000,100);
  CHECK(has(store.page("alice",frozen.at("session_id").str(),"",100),target));
}
static void priority_fallback_semantics() {
  Store store(":memory:");
  const std::vector<SourceDocument> documents={
    {"https://cases.example/one","machine learning node.js café","text/plain","one"},
    {"https://cases.example/two","amachine learningz node.jsx cafeteria afé","text/plain","two"},
    {"https://cases.example/three","nothing here","text/plain","machine learning"},
    {"https://cases.example/machine-learning","unrelated","text/plain","four"},
    {"https://cases.example/five","Москва","text/plain","five"}};
  for(const auto& doc:documents)store.ingest("alice",doc);
  store.ingest("bob",documents.front());
  for(const auto* expression:{"\"machine learning\"","\"achine learn\"","node.js","café",
    "\"machine learning\" OR Москва","\"machine learning\" OR NOT node.js",
    "\"machine learning\" AND NOT node.js","inbody:node.js","intitle:\"machine learning\"",
    "\"machine learning\" AND café","NOT \"machine learning\""}) {
    auto query=parse_query(Json::string(expression));std::set<std::string> expected,actual;
    for(const auto& doc:documents)if(matches(query,doc.title,doc.raw,doc.url,"cases.example",doc.content_type))expected.insert(doc.url);
    auto result=store.search_once("alice",query,2000,100);auto hits=result.at("hits");
    for(std::size_t i=0;i<hits.size();++i)actual.insert(hits.at(i).at("url").str());
    CHECK(actual==expected);CHECK(!result.at("partial").flag());
  }
  // Duplicate priority rows must not consume fallback slots.
  auto full=store.search_once("alice",parse_query(Json::string("\"machine learning\"")),3,100);
  CHECK(full.at("returned_count").num()==3);CHECK(!full.at("partial").flag());
  auto small=store.search_once("alice",parse_query(Json::string("\"machine learning\"")),1,100);
  CHECK(small.at("returned_count").num()==1);CHECK(small.at("partial").flag());
}
int main() {
  try {priority_fallback_semantics();long_document_recall();std::cout<<"PASS long_document_recall and priority_fallback_semantics\n";return 0;}
  catch(const std::exception& e) {std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}
}
