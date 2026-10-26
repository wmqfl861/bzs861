#include "bzs/store.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
using namespace bzs;
namespace {
int assertions=0;
#define CHECK(x) do {++assertions;if(!(x))throw std::runtime_error(std::string(#x)+" at "+std::to_string(__LINE__));}while(false)
std::int64_t scalar(sqlite3* db,const std::string& sql) {
  sqlite3_stmt* p=nullptr;CHECK(sqlite3_prepare_v2(db,sql.c_str(),-1,&p,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> holder(p,sqlite3_finalize);
  CHECK(sqlite3_step(p)==SQLITE_ROW);return sqlite3_column_int64(p,0);
}
void exec(sqlite3* db,const char* sql) {CHECK(sqlite3_exec(db,sql,nullptr,nullptr,nullptr)==SQLITE_OK);}
template<class F>void rejects(F f,const std::string& code){try{f();}catch(const Error& e){CHECK(e.code==code);return;}throw std::runtime_error("expected "+code);}
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-grams-"+random_id());
  Temp(){std::filesystem::create_directories(path);}
  ~Temp(){std::error_code ec;std::filesystem::remove_all(path,ec);}
};
Query query(const std::string& s){return parse_query(Json::string(s));}
std::string points(std::uint32_t start,std::uint32_t count) {
  std::string out;
  for(auto cp=start;cp<start+count;++cp) {
    out+=static_cast<char>(0xf0|(cp>>18));out+=static_cast<char>(0x80|((cp>>12)&63));
    out+=static_cast<char>(0x80|((cp>>6)&63));out+=static_cast<char>(0x80|(cp&63));
  }
  return out;
}
void cjk_windows_and_overflow() {
  CHECK(indexed_grams("中文資料検索한글",'b').empty());
  CHECK(query_substring_grams("中文資料検索한글").empty());
  CHECK(indexed_grams("中a文b",'b')=="bu61 bu62");
  CHECK(query_substring_grams("中a文b")==std::vector<std::string>({"u61","u62"}));
  CHECK(query_cjk_runs("Python教程 中文.js c++语言")==std::vector<std::string>({"教程","中文","语言"}));
  const auto large_cjk=points(0x20000,23000),large_other=points(0x10000,23000);
  CHECK(indexed_grams(large_cjk,'b').empty());
  CHECK(indexed_grams(large_other,'b')=="boverflow");
  Store s(":memory:");s.ingest("alice",{"https://x.example/large",large_cjk+" Python教程","text/plain","标题"});
  CHECK(s.search_once("alice",query(large_cjk.substr(large_cjk.size()-12)),1).at("returned_count").num()==1);
  CHECK(s.search_once("alice",query("Python教程"),1).at("returned_count").num()==1);
}
void mixed_candidate_supersets() {
  Store s(":memory:");
  const std::vector<SourceDocument> docs={
    {"https://x.example/1","Python教程 c++教程 中文.js 版本1.2 中a文b","text/plain","示例"},
    {"https://x.example/2","CPython教程 c++指南 全中文","text/plain","中文.js"},
    {"https://x.example/%E4%B8%AD","其他内容","text/plain","Python教程"},
    {"https://x.example/4","Python指南 c++ c# 中a文b国","text/plain","title"},
    {"https://x.example/5","café Москва machine learning","text/plain","版"}};
  for(const auto& doc:docs)s.ingest("alice",doc);
  s.ingest("bob",docs.front());
  std::vector<Query> queries;
  for(const auto* q:{"Python教程","\"Python教程\"","c++教程","中文.js","版本1.2","\"中a文b\"","全中文",
    "教程 OR café","Python教程 NOT 指南","教程 OR NOT c++","café AND NOT 教程","intitle:Python教程","inbody:中文.js",
    "\"machine learning\" OR 教程","中a文b国","不存在 AND 教程"})queries.push_back(query(q));
  for(const auto* value:{"%E4%B8%AD","中"}) {auto ast=Json::object();ast.set("op","phrase");ast.set("field","url");ast.set("value",value);queries.push_back(parse_query(ast));}
  for(const auto& q:queries) {
    std::set<std::string> expected,actual;
    for(const auto& d:docs)if(matches(q,d.title,d.raw,d.url,"x.example",d.content_type))expected.insert(d.url);
    const auto response=s.search_once("alice",q,2000,100);const auto hits=response.at("hits");
    for(std::size_t i=0;i<hits.size();++i)actual.insert(hits.at(i).at("url").str());
    CHECK(expected==actual);CHECK(!response.at("partial").flag());
  }
  CHECK(fts_candidates(query("Python教程")).has_value());
}
void accounting(Store& s,const std::string& owner) {
  const auto stats=s.stats(owner);
  const auto bytes=scalar(s.raw_handle(),"SELECT coalesce(sum(length(CAST(grams AS BLOB))),0) FROM substring_fts WHERE owner='"+owner+"'");
  CHECK(stats.at("substring_index_bytes").num()==bytes);
  CHECK(stats.at("accounted_storage_bytes").num()==bytes+stats.at("content_bytes").num());
  CHECK(!stats.at("substring_index_over_quota").flag());
}
void account_replace_delete_and_publication() {
  Store s(":memory:");const std::string url="https://x.example/a";
  auto first=s.ingest("alice",{url,"machine learning 中文","text/plain","label"});
  s.ingest("bob",{url,"different text","text/plain","other"});const auto bob=s.stats("bob").canonical();
  accounting(s,"alice");accounting(s,"bob");const auto before=s.stats("alice").at("substring_index_bytes").num();CHECK(before>0);
  auto reused=s.ingest("alice",{url,"machine learning 中文","text/plain","label"});
  CHECK(reused.at("reused").flag());CHECK(s.stats("alice").at("substring_index_bytes").num()==before);
  auto next=s.ingest("alice",{url,"replacement","text/plain","label"});accounting(s,"alice");
  CHECK(s.stats("alice").at("snapshots").num()==2);
  CHECK(s.snapshot("alice",first.at("snapshot_id").str()).at("canonical_text").str()=="machine learning 中文");
  s.ingest("alice",{url,"<meta name='robots' content='noindex'><body>hidden</body>","text/html","label"});
  accounting(s,"alice");CHECK(s.stats("alice").at("substring_index_bytes").num()==0);
  // Restore through a new complete import, not by changing old snapshots.
  s.ingest("alice",{url,"new permitted content","text/plain","label"});accounting(s,"alice");
  CHECK(s.stats("alice").at("substring_index_bytes").num()>0);
  auto policy=Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex","nosnippet"],"diagnostics":[]})");
  s.observe_web_publication("alice",url,url,policy,s.publication_fence("alice",url));
  CHECK(s.stats("alice").at("substring_index_bytes").num()==0);accounting(s,"alice");
  s.erase("alice",next.at("document_id").str());CHECK(s.stats("alice").at("substring_index_bytes").num()==0);
  CHECK(s.stats("bob").canonical()==bob);
}
void independent_quota_rolls_back_every_write() {
  Temp t;const auto path=t.path/"db";std::int64_t used=0;std::string snap;
  {Store s(path);snap=s.ingest("alice",{"https://x.example/a","old content","text/plain","title"}).at("snapshot_id").str();used=s.stats("alice").at("substring_index_bytes").num();}
  StoreOptions options;options.max_substring_bytes_per_owner=used;Store s(path,{},options);auto before=s.stats("alice").canonical();
  rejects([&]{s.ingest("alice",{"https://x.example/new","extra words and symbols café node.js","text/plain","title"});},"E_QUOTA");
  CHECK(s.stats("alice").canonical()==before);
  CHECK(s.snapshot("alice",snap).at("canonical_text").str()=="old content");
  rejects([&]{s.ingest("alice",{"https://x.example/a","much longer distinct changed content with many new symbols café node.js c++ abcdefghijklmnopqrstuvwxyz","text/plain","title"});},"E_QUOTA");
  CHECK(s.stats("alice").canonical()==before);accounting(s,"alice");
  s.ingest("alice",{"https://x.example/hidden","<meta name='robots' content='noindex'><body>hidden data</body>","text/html","title"});
  CHECK(s.stats("alice").at("substring_index_bytes").num()==used);
  s.ingest("bob",{"https://x.example/a","old content","text/plain","title"});accounting(s,"bob");
}
void derived_rebuild_is_atomic_and_business_schema_stays_15() {
  Temp t;const auto path=t.path/"db";Json snap,evidence,job;std::string page,session;
  auto clock=[] {return std::int64_t{1800000000000};};
  {
    Store s(path,clock);snap=s.ingest("alice",{"https://x.example/a","machine learning 中文","text/plain","label"});
    // Compare the same snapshot projection before/after; ingest adds `reused`.
    snap=s.snapshot("alice",snap.at("snapshot_id").str());
    evidence=s.evidence("alice",snap.at("snapshot_id").str(),0,7);job=s.job_create("alice","fixture","preserve-key");
    session=s.search("alice",query("machine")).at("session_id").str();page=s.page("alice",session,"",100).canonical();
    exec(s.raw_handle(),"UPDATE settings SET value='bzs-ascii-cjk/2+codepoint-grams/1' WHERE key='fts_tokenizer_version'; UPDATE substring_usage SET bytes=0; CREATE TRIGGER reject_marker BEFORE UPDATE ON settings WHEN NEW.key='fts_tokenizer_version' BEGIN SELECT RAISE(ABORT,'rollback fixture'); END;");
  }
  rejects([&]{Store blocked(path,clock);},"E_STORAGE");
  sqlite3* raw=nullptr;CHECK(sqlite3_open(path.string().c_str(),&raw)==SQLITE_OK);
  CHECK(scalar(raw,"SELECT COUNT(*) FROM settings WHERE key='fts_tokenizer_version' AND value='bzs-ascii-cjk/2+codepoint-grams/1'")==1);
  CHECK(scalar(raw,"SELECT bytes FROM substring_usage WHERE owner='alice'")==0);
  exec(raw,"DROP TRIGGER reject_marker;");sqlite3_close(raw);
  {
    Store s(path,clock);CHECK(scalar(s.raw_handle(),"PRAGMA user_version")==15);
    CHECK(s.snapshot("alice",snap.at("snapshot_id").str()).canonical()==snap.canonical());
    CHECK(s.evidence_get("alice",evidence.at("evidence_id").str()).canonical()==evidence.canonical());
    CHECK(s.page("alice",session,"",100).canonical()==page);
    CHECK(s.job_get("alice",job.at("job_id").str()).at("state").str()=="queued");
    CHECK(s.job_create("alice","fixture","preserve-key").at("reused").flag());
    accounting(s,"alice");CHECK(s.stats("alice").at("substring_index_bytes").num()>0);
  }
  // A newly smaller quota never deletes old content during derived rebuild.
  CHECK(sqlite3_open(path.string().c_str(),&raw)==SQLITE_OK);
  exec(raw,"UPDATE settings SET value='bzs-ascii-cjk/2+codepoint-grams/1' WHERE key='fts_tokenizer_version';");sqlite3_close(raw);
  StoreOptions small;small.max_substring_bytes_per_owner=1;Store s(path,clock,small);
  CHECK(s.stats("alice").at("substring_index_over_quota").flag());
  CHECK(!s.snapshot("alice",snap.at("snapshot_id").str()).at("canonical_text").str().empty());
  // Tightening publication must not fail merely because a legacy index is
  // already above a newly smaller budget. Restoring the same existing index
  // does not authorize additional bytes; the restriction must persist.
  const auto old_bytes=s.stats("alice").at("substring_index_bytes").num();
  auto restrictive=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["nosnippet"],"diagnostics":[]})");
  s.observe_web_publication("alice","https://x.example/a","https://x.example/a",restrictive,s.publication_fence("alice","https://x.example/a"));
  CHECK(s.stats("alice").at("substring_index_bytes").num()==old_bytes);
  CHECK(scalar(s.raw_handle(),"SELECT snippet_allowed FROM document_publication WHERE owner='alice'")==0);
  s.erase("alice",snap.at("document_id").str());CHECK(s.stats("alice").at("substring_index_bytes").num()==0);
}
}
int main(){try {for(const auto& [name,run]:std::vector<std::pair<const char*,std::function<void()>>>{
 {"cjk_windows_and_overflow",cjk_windows_and_overflow},{"mixed_candidate_supersets",mixed_candidate_supersets},
 {"account_replace_delete_and_publication",account_replace_delete_and_publication},
 {"independent_quota_rolls_back_every_write",independent_quota_rolls_back_every_write},
 {"derived_rebuild_is_atomic_and_business_schema_stays_15",derived_rebuild_is_atomic_and_business_schema_stays_15}}){run();std::cout<<"PASS "<<name<<'\n';}
 std::cout<<"assertions="<<assertions<<'\n';return 0;}catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}}
