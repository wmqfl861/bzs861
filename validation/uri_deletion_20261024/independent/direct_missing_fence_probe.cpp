// Independent compatibility checks for legacy direct cache metadata. Deliberate
// SQL fixture mutation removes only the optional publication_fence metadata.
#define main historical_redirect_probe_main
#include "original_redirect_probe.cpp"
#undef main
#include <sqlite3.h>

namespace {
void change_fence(Store& store,const std::string& sid,bool malformed=false) {
  const auto current=store.snapshot(owner,sid).at("meta");auto next=Json::object();
  for(const auto& key:current.keys())if(key!="publication_fence")next.set(key,current.at(key));
  if(malformed)next.set("publication_fence",Json::object());
  const auto encoded=next.dump();sqlite3_stmt* s=nullptr;
  VERIFY(sqlite3_prepare_v2(store.raw_handle(),"UPDATE snapshots SET meta_json=? WHERE id=?",-1,&s,nullptr)==SQLITE_OK);
  struct Done{sqlite3_stmt*s;~Done(){sqlite3_finalize(s);}}done{s};
  VERIFY(sqlite3_bind_text(s,1,encoded.c_str(),static_cast<int>(encoded.size()),SQLITE_TRANSIENT)==SQLITE_OK);
  VERIFY(sqlite3_bind_text(s,2,sid.c_str(),static_cast<int>(sid.size()),SQLITE_TRANSIENT)==SQLITE_OK);
  VERIFY(sqlite3_step(s)==SQLITE_DONE);
}
void legacy_direct_positive() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request(final_uri));change_fence(store,first.at("snapshot_id").str());
  VERIFY(web.read(owner,request(final_uri,"cache_only")).at("from_cache").flag());
  VERIFY(store.revalidation_base(owner,final_uri).has_value());VERIFY(worker.fetches==1);
}
void legacy_alias_delete_revival_cross_handle() {
  Temporary temp;DeterministicWorker worker;Store first(temp.path/"db",[&]{return worker.now;}),second(temp.path/"db",[&]{return worker.now;});WebService web(first,worker,{});
  const std::string encoded="https://INDEPENDENT-CHAIN.EXAMPLE:443/%66inal";
  const auto marker=import_alias(second,encoded),cached=web.read(owner,request(final_uri));change_fence(first,cached.at("snapshot_id").str());
  VERIFY(web.read(owner,request(final_uri,"cache_only")).at("from_cache").flag());
  second.erase(owner,marker.at("document_id").str());
  expect([&]{web.read(owner,request(final_uri,"cache_only"));},"E_CACHE_STALE");VERIFY(!first.revalidation_base(owner,final_uri));
  import_alias(second,encoded);
  expect([&]{web.read(owner,request(final_uri,"cache_only"));},"E_CACHE_STALE");VERIFY(!first.revalidation_base(owner,final_uri));
  VERIFY(worker.fetches==1);preserved_body(first,cached.at("snapshot_id").str());
}
void unrelated_and_other_owner_tombstones_do_not_block() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto unrelated=import_alias(store,"https://independent-chain.example/unrelated");
  const auto other=store.ingest("other-owner",{"https://independent-chain.example/%66inal","Other marker.","text/plain","Other"});
  const auto first=web.read(owner,request(final_uri));change_fence(store,first.at("snapshot_id").str());
  store.erase(owner,unrelated.at("document_id").str());store.erase("other-owner",other.at("document_id").str());
  VERIFY(web.read(owner,request(final_uri,"cache_only")).at("from_cache").flag());
  VERIFY(store.revalidation_base(owner,final_uri).has_value());VERIFY(worker.fetches==1);
}
void reserved_separator_tombstones_remain_distinct() {
  const std::vector<std::pair<std::string,std::string>> pairs={
    {"/a%2fb","/a/b"},{"/a%3bb","/a;b"},{"/a%252Fb","/a%2Fb"},
    {"/a?x=1%26y=2","/a?x=1&y=2"},{"/a?x=1&y=2","/a?y=2&x=1"}};
  for(const auto& [encoded,path]:pairs) {
    Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
    const auto prefix="https://independent-chain.example";const auto target=prefix+path;
    const auto marker=import_alias(store,prefix+encoded),first=web.read(owner,request(target));change_fence(store,first.at("snapshot_id").str());
    store.erase(owner,marker.at("document_id").str());
    VERIFY(web.read(owner,request(target,"cache_only")).at("from_cache").flag());
    VERIFY(store.revalidation_base(owner,target).has_value());VERIFY(worker.fetches==1);
  }
}
void missing_redirect_fence_stays_closed() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request());change_fence(store,first.at("snapshot_id").str());
  expect([&]{web.read(owner,request(original,"cache_only"));},"E_CACHE_STALE");VERIFY(!store.revalidation_base(owner,original));VERIFY(worker.fetches==1);
}
void malformed_direct_fence_is_not_missing_proof() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto first=web.read(owner,request(final_uri));change_fence(store,first.at("snapshot_id").str(),true);
  bool failed=false;try{(void)web.read(owner,request(final_uri,"cache_only"));}catch(const Error&){failed=true;}
  VERIFY(failed);VERIFY(!store.revalidation_base(owner,final_uri));VERIFY(worker.fetches==1);
}
void no_proof_cannot_infer_post_delete_freshness() {
  Temporary temp;DeterministicWorker worker;Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
  const auto alias="https://independent-chain.example/%66inal";const auto marker=import_alias(store,alias);
  store.erase(owner,marker.at("document_id").str());import_alias(store,alias);
  const auto first=web.read(owner,request(final_uri));VERIFY(web.read(owner,request(final_uri,"cache_only")).at("from_cache").flag());
  change_fence(store,first.at("snapshot_id").str());
  expect([&]{web.read(owner,request(final_uri,"cache_only"));},"E_CACHE_STALE");VERIFY(!store.revalidation_base(owner,final_uri));VERIFY(worker.fetches==1);
}
}
int main() {
  const std::vector<std::pair<std::string,std::function<void()>>> cases={
    {"legacy_direct_positive",legacy_direct_positive},
    {"legacy_alias_delete_revival_cross_handle",legacy_alias_delete_revival_cross_handle},
    {"unrelated_other_owner_deletes",unrelated_and_other_owner_tombstones_do_not_block},
    {"reserved_and_query_distinctions",reserved_separator_tombstones_remain_distinct},
    {"missing_redirect_fence_closed",missing_redirect_fence_stays_closed},
    {"malformed_direct_fence_closed",malformed_direct_fence_is_not_missing_proof},
    {"no_invented_post_delete_proof",no_proof_cannot_infer_post_delete_freshness}};
  int failures=0;
  for(const auto&[name,run]:cases) {
    std::cout<<"RUN "<<name<<std::endl;
    try{run();std::cout<<"PASS "<<name<<std::endl;}
    catch(const Error&e){++failures;std::cout<<"FAIL "<<name<<" "<<e.code<<" "<<e.what()<<std::endl;}
    catch(const std::exception&e){++failures;std::cout<<"FAIL "<<name<<" "<<e.what()<<std::endl;}
  }
  std::cout<<"SUMMARY cases="<<cases.size()<<" checks="<<checks<<" failures="<<failures<<" external_network_requests=0"<<std::endl;
  return failures?2:0;
}
