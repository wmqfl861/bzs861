#include "bzs/query.hpp"
#include "bzs/util.hpp"
#include <iostream>
#include <random>
#include <string>
#include <vector>
using namespace bzs;
int main(){
  struct Doc{std::string title,body,url,host,type;};
  const std::vector<std::string> tokens={"marker","MARKER","c++","c++20","ac++","c++foo","C#","node.js","node.js2","café","CAFÉ","Москва","москва","开发者","开发","iPhone手机","MACHINE learning","alpha beta","alphabet","forbidden","v1.2","a_b","foo:bar","https://x.example/marker","plain","absent"};
  const std::vector<std::string> expressions={"marker","MARKER","c++","c#","node.js","café","Москва","开发者","iPhone手机","\"alpha beta\"","\"achine learn\"","\"arker\"","NOT absent","NOT marker","marker OR absent","marker AND NOT forbidden","(marker OR café) AND NOT forbidden","c++ OR c# OR node.js","\"alpha beta\" OR marker OR Москва","intitle:marker","inbody:marker","intitle:marker OR inbody:café","site:example marker","site:x.example marker","site:other.example marker","filetype:txt marker","filetype:html marker","NOT (marker OR café)","NOT (NOT marker)","v1.2","a_b","foo:bar","\"C++\" AND NOT c#"};
  std::vector<Query> queries;for(const auto& expression:expressions)queries.push_back(parse_query(Json::string(expression)));
  for(const auto* field:{"title","body","url"})for(const auto* value:{"marker","c++","café","Москва","alpha beta"}){
    auto node=Json::object();node.set("op","phrase");node.set("field",field);node.set("value",value);queries.push_back(parse_query(node));
  }
  std::mt19937 generator(8612026);auto words=[&](std::size_t count){std::string result;for(std::size_t i=0;i<count;++i){if(i)result+=' ';result+=tokens[generator()%tokens.size()];}return result;};
  std::vector<Doc> docs;
  for(int i=0;i<180;++i){const std::string host=i%3==0?"x.example":i%3==1?"sub.x.example":"other.example";docs.push_back({words(i%7),words(static_cast<std::size_t>(i%31)),"https://"+host+"/"+tokens[static_cast<std::size_t>(i)%tokens.size()],host,i%2==0?"text/plain":"text/html"});}
  docs.push_back({"marker marker marker marker marker marker marker","marker marker marker marker marker marker marker marker marker marker marker marker marker","https://x.example/marker","x.example","text/plain"});
  docs.push_back({"","","https://x.example/marker","x.example","text/plain"});
  auto rows=Json::array();std::int64_t matched=0,zero_matches=0;
  for(std::size_t q=0;q<queries.size();++q)for(std::size_t d=0;d<docs.size();++d){const auto& query=queries[q];const auto& doc=docs[d];
    bool match;int value=0;
#if BZS_NEW_LIBRARY
    const auto combined=match_score(query,doc.title,doc.body,doc.url,doc.host,doc.type);match=combined.has_value();if(combined)value=*combined;
#else
    match=matches(query,doc.title,doc.body,doc.url,doc.host,doc.type);if(match)value=score_match(query.root,doc.title,doc.body);
#endif
    auto row=Json::array();row.push(Json::integer(static_cast<std::int64_t>(q)));row.push(Json::integer(static_cast<std::int64_t>(d)));row.push(Json::boolean(match));row.push(match?Json::integer(value):Json());row.push(Json::integer(score_match(query.root,doc.title,doc.body)));rows.push(row);
    if(match){++matched;if(value==0)++zero_matches;}
  }
  auto result=Json::object();result.set("queries",static_cast<std::int64_t>(queries.size()));result.set("documents",static_cast<std::int64_t>(docs.size()));result.set("comparisons",static_cast<std::int64_t>(rows.size()));result.set("matched",matched);result.set("matching_zero_scores",zero_matches);result.set("matrix_sha256",sha256(rows.canonical()));std::cout<<result.canonical()<<'\n';
}
