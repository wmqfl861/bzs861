#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <iostream>
#include <mutex>
#include <thread>
#include <vector>
namespace bzs {
struct StoreSearchTestAccess {
  static void set(Store& store,std::function<void()> hook){std::lock_guard lock(store.mutex_);store.search_unlocked_for_test_=std::move(hook);}
};
}
using namespace bzs;
namespace {
int checks=0,cases=0;
void check(bool value,const char* text){++checks;if(!value)throw std::runtime_error(text);}
struct Temp {std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-search-independent-"+random_id());Temp(){std::filesystem::create_directories(path);}~Temp(){std::error_code ec;std::filesystem::remove_all(path,ec);}};
std::int64_t scalar(Store& s,const char* sql){sqlite3_stmt* stmt=nullptr;check(sqlite3_prepare_v2(s.raw_handle(),sql,-1,&stmt,nullptr)==SQLITE_OK,"scalar prepare");const auto rc=sqlite3_step(stmt);const auto n=sqlite3_column_int64(stmt,0);sqlite3_finalize(stmt);check(rc==SQLITE_ROW,"scalar row");return n;}
Query query(const std::string& value){return parse_query(Json::string(value));}
Json web(Store& store,const std::string& owner,const std::string& url,const std::string& target,const std::vector<std::string>& redirects={},const std::string& mode="allow",const std::string& body="marker original confidential body"){
  ParsedDocument parsed;parsed.text=body;parsed.title="marker confidential title";parsed.content_type="text/plain";parsed.parser_version="independent-search/1";parsed.index_allowed=mode!="noindex";
  parsed.publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");
  parsed.publication.set("snippet_text",mode=="allow"?body:"");parsed.publication.set("snippet_title",mode=="allow"?parsed.title:"");
  parsed.publication.set("snippet_allowed",mode=="allow");parsed.publication.set("index_allowed",mode!="noindex");
  auto meta=Json::object(),chain=Json::array();meta.set("final_url",target);
  for(const auto& item:redirects)chain.push(Json::string(item));
  meta.set("redirects",chain);
  return store.ingest_web(owner,{url,body,"text/plain",parsed.title},parsed,meta,store.deletion_generation(owner,url));
}
struct Outcome {Json response;std::string error;};
Outcome interleave(Store& store,bool freeze,int pause_after,const std::function<void()>& actor,const std::string& expression="NOT absent"){
  std::mutex mutex;std::condition_variable cv;bool paused=false,finished=false,released=false,actor_finished=false;
  bool no_transaction=false,no_busy_statement=false;int seen=0;Outcome out;std::exception_ptr other_error;
  StoreSearchTestAccess::set(store,[&]{
    std::unique_lock lock(mutex);if(++seen!=pause_after)return;
    no_transaction=sqlite3_get_autocommit(store.raw_handle())!=0;no_busy_statement=true;
    for(auto* stmt=sqlite3_next_stmt(store.raw_handle(),nullptr);stmt;stmt=sqlite3_next_stmt(store.raw_handle(),stmt))
      if(sqlite3_stmt_busy(stmt))no_busy_statement=false;
    paused=true;cv.notify_all();cv.wait(lock,[&]{return released;});
  });
  std::thread reader([&]{
    try{out.response=freeze?store.search("alice",query(expression),2000,100):store.search_once("alice",query(expression),2000,100);}
    catch(const Error& e){out.error=e.code;}catch(const std::exception& e){out.error=e.what();}
    {std::lock_guard lock(mutex);finished=true;}cv.notify_all();
  });
  bool reached=false,progress=false;
  {std::unique_lock lock(mutex);reached=cv.wait_for(lock,std::chrono::seconds(5),[&]{return paused||finished;})&&paused;}
  std::thread writer;
  if(reached){
    writer=std::thread([&]{try{actor();}catch(...){other_error=std::current_exception();}{std::lock_guard lock(mutex);actor_finished=true;}cv.notify_all();});
    std::unique_lock lock(mutex);progress=cv.wait_for(lock,std::chrono::seconds(5),[&]{return actor_finished;});
  }
  {std::lock_guard lock(mutex);released=true;}cv.notify_all();reader.join();if(writer.joinable())writer.join();StoreSearchTestAccess::set(store,{});
  check(reached,"matching gate reached");check(progress,"actor completed before releasing matching");check(no_transaction,"matching held no SQLite transaction");check(no_busy_statement,"matching held no busy SQLite statement");
  if(other_error)std::rethrow_exception(other_error);
  return out;
}
Json hits(Store& store,bool freeze,const Outcome& out){check(out.error.empty(),"query unexpectedly failed");return freeze?store.page("alice",out.response.at("session_id").str(),"",100).at("hits"):out.response.at("hits");}
void late_redirect_history(){
  for(bool freeze:{false,true})for(const std::string mode:{"noindex","nosnippet"})for(bool intermediate:{false,true}){
    ++cases;Temp t;std::atomic<std::int64_t> clock{1800000000000};const auto now=[&]{return clock.load();};Store first(t.path/"db",now),second(t.path/"db",now);
    const std::string logical="https://a.example/page",old_final="https://b.example/page",old_middle="https://middle.example/hop",new_final="https://c.example/fresh";
    first.ingest("alice",{"https://other.example/plain","marker second candidate","text/plain","plain"});++clock;
    const auto captured=web(first,"alice",logical,old_final,{old_middle,old_final});
    const auto out=interleave(first,freeze,2,[&]{
      const auto target=intermediate?old_middle:old_final;
      web(second,"alice",target,target,{},mode);
      web(second,"alice",target,target); // Current permission is restored.
      web(second,"alice",logical,new_final,{new_final},"allow","new representation without the earlier secret");
    });
    const auto found=hits(first,freeze,out);bool old_seen=false;
    for(std::size_t i=0;i<found.size();++i)if(found.at(i).at("snapshot_id").str()==captured.at("snapshot_id").str()){
      old_seen=true;check(mode=="nosnippet","withdrawn old representation leaked");check(found.at(i).at("snippet").str().empty(),"old redirect snippet leaked");check(found.at(i).at("title").str()==logical,"old redirect title leaked");
    }
    check(old_seen==(mode=="nosnippet"),"captured identity history was not preserved");
    check(out.response.at("partial").flag()==(mode=="noindex"),"concurrent withdrawal partial flag");
    check(out.response.at("scanned_candidates").num()==2,"later imported candidates unexpectedly joined search");
    const auto fresh=first.search_once("alice",query("NOT absent"));check(fresh.at("returned_count").num()>=2,"new allowed representation was permanently blacklisted");
  }
}
void owner_negative_controls(){
  for(bool freeze:{false,true}){
    ++cases;Temp t;Store first(t.path/"db"),second(t.path/"db");const std::string url="https://same.example/owner";
    const auto mine=web(first,"alice",url,url);const auto theirs=web(first,"bob",url,url);
    const auto out=interleave(first,freeze,1,[&]{web(second,"bob",url,url,{},"noindex");second.erase("bob",theirs.at("document_id").str());second.revoke_principal("admin","bob");});
    const auto found=hits(first,freeze,out);check(found.size()==1,"unrelated owner changed candidate visibility");check(found.at(0).at("snapshot_id").str()==mine.at("snapshot_id").str(),"cross-owner snapshot mixed");
    check(!found.at(0).at("snippet").str().empty(),"unrelated owner suppressed authorized snippet");check(!out.response.at("partial").flag(),"unrelated owner falsely marked partial");
  }
}
void prune_and_revocation(){
  for(bool freeze:{false,true})for(bool revoke:{false,true}){
    ++cases;Temp t;StoreOptions opts;opts.max_revisions_per_document=1;Store first(t.path/"db",{},opts),second(t.path/"db",{},opts);
    first.ingest("alice",{"https://a.example/prune","marker captured","text/plain","title"});
    const auto out=interleave(first,freeze,1,[&]{if(revoke)second.revoke_principal("admin","alice");else second.ingest("alice",{"https://a.example/prune","replacement","text/plain","title"});},"marker");
    if(revoke){check(out.error=="E_PRINCIPAL_REVOKED","late multi-handle principal denial missing");check(scalar(second,"SELECT COUNT(*) FROM sessions")==0,"revoked query created session");check(scalar(second,"SELECT COUNT(*) FROM search_stream_receipts")==0,"revoked query created receipt");}
    else {check(hits(first,freeze,out).size()==0,"pruned snapshot returned");check(out.response.at("partial").flag(),"pruning did not report partial");}
  }
}
Json site_config(const std::string& mode){
  auto value=Json::parse(R"({"version":1,"sites":[{"id":"independent-site","enabled":true,"origin":"https://site.example","path_prefixes":["/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://site.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"independent-synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
  auto review=value.at("sites").at(0).at("review");if(mode=="noindex")review.set("status","rejected");if(mode=="nosnippet")review.set("redistribution_policy","links_only");return value;
}
void cached_site_generation_reloads(){
  for(bool freeze:{false,true})for(const std::string mode:{"noindex","nosnippet"}){
    ++cases;Temp t;Store first(t.path/"db");first.activate_site_publication(SiteRegistry(site_config("allow")));
    // Explicit activation intentionally rejects a handle opened before a newer manifest.
    // Open the actor on the current manifest, then change it while first is scanning.
    Store second(t.path/"db");
    const std::string url="https://site.example/frozen";const auto captured=web(first,"alice",url,url);
    const auto out=interleave(first,freeze,1,[&]{second.activate_site_publication(SiteRegistry(site_config(mode)));second.activate_site_publication(SiteRegistry(site_config("allow")));});
    const auto found=hits(first,freeze,out);check(found.size()==(mode=="noindex"?0U:1U),"other-handle site generation history ignored");
    if(mode=="nosnippet"){check(found.at(0).at("snapshot_id").str()==captured.at("snapshot_id").str(),"site policy changed captured identity");check(found.at(0).at("snippet").str().empty(),"other-handle site suppression leaked snippet");check(found.at(0).at("title").str()==url,"other-handle site suppression leaked title");}
    else check(out.response.at("partial").flag(),"site withdrawal must be partial");
  }
}
void global_capacity_race(){
  ++cases;Temp t;Store first(t.path/"db"),second(t.path/"db");
  first.ingest("alice",{"https://quota.example/a","marker","text/plain","title"});
  for(int owner=0;owner<4;++owner)for(int slot=0;slot<(owner==0?63:64);++slot)first.search("owner"+std::to_string(owner),query("marker"));
  check(scalar(first,"SELECT COUNT(*) FROM sessions")==255,"capacity fixture not at 255");
  const auto out=interleave(first,true,1,[&]{second.search("last-slot",query("marker"));});
  check(out.error=="E_SESSION_BACKPRESSURE","global final capacity not rechecked");check(scalar(first,"SELECT COUNT(*) FROM sessions")==256,"global session cap exceeded");check(scalar(first,"SELECT COUNT(*) FROM search_stream_receipts")==256,"rejected search leaked identity receipt");
  check(scalar(first,"SELECT COUNT(*) FROM sessions WHERE owner='alice'")==0,"failed frozen search committed rows");
  const auto once=first.search_once("alice",query("marker"));check(once.at("returned_count").num()==1,"once incorrectly consumes frozen-session capacity");check(scalar(first,"SELECT COUNT(*) FROM search_stream_receipts")==256,"once created durable receipt");
}
}
int main(){try{late_redirect_history();owner_negative_controls();prune_and_revocation();cached_site_generation_reloads();global_capacity_race();std::cout<<"PASS "<<cases<<" independent scenarios, "<<checks<<" checks\n";return 0;}catch(const std::exception& e){std::cerr<<"FAIL after "<<cases<<" scenarios / "<<checks<<" checks: "<<e.what()<<'\n';return 1;}}
