#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace bzs {
struct WebReadTestAccess {
  struct State { std::size_t flights=0,members=0; };
  static State state(WebService& web) {
    std::lock_guard lock(web.direct_reads_mutex_);
    State result;result.flights=web.direct_reads_.size();
    for(const auto& [key,flight]:web.direct_reads_) {
      (void)key;result.members+=flight->members;
    }
    return result;
  }
  static void window_hook(WebService& web,std::function<void(const std::string&)> hook) {
    std::lock_guard lock(web.direct_reads_mutex_);
    web.direct_read_window_for_test_=std::move(hook);
  }
};
}

using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> values;return values; }
struct Add { Add(const char* name,std::function<void()> test) { tests().emplace_back(name,std::move(test)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add register_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" line "+std::to_string(__LINE__)); } while(false)

constexpr std::int64_t epoch=1700000000000;
const std::string target="https://shared.example/page";
const std::string body="sharedneedle 中文 independent body";
const std::string profile=sha256("direct-shared-fixture-profile");

struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-direct-shared-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
};

template<class F> void rejects(F run,const std::string& expected) {
  ++assertions;
  try { run(); } catch(const Error& error) {
    if(error.code==expected)return;
    throw std::runtime_error("expected "+expected+", got "+error.code);
  }
  throw std::runtime_error("expected "+expected);
}

void sql(sqlite3* db,const char* statement) {
  char* error=nullptr;
  const int code=sqlite3_exec(db,statement,nullptr,nullptr,&error);
  const std::string detail=error?error:"";sqlite3_free(error);
  if(code!=SQLITE_OK)throw std::runtime_error("fixture SQL failed: "+detail);
}

std::int64_t scalar(sqlite3* db,const char* statement) {
  sqlite3_stmt* raw=nullptr;
  CHECK(sqlite3_prepare_v2(db,statement,-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> row(raw,sqlite3_finalize);
  CHECK(sqlite3_step(row.get())==SQLITE_ROW);return sqlite3_column_int64(row.get(),0);
}

Json read_args(const std::string& url=target,const char* freshness="refresh") {
  auto out=Json::object();out.set("url",url);out.set("freshness",freshness);return out;
}

Json without(const Json& input,const std::string& removed) {
  auto out=Json::object();for(const auto& key:input.keys())if(key!=removed)out.set(key,input.at(key));return out;
}

Json publication(const std::string& directive="") {
  auto out=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"sharedneedle 中文 independent body","snippet_title":"shared fixture","directives":[],"diagnostics":[]})");
  if(directive=="noindex")out.set("index_allowed",false);
  if(directive=="noindex"||directive=="nosnippet")out.set("snippet_allowed",false);
  if(!directive.empty())out.at("directives").push(Json::string(directive));
  return out;
}

struct Gate {
  std::mutex mutex;std::condition_variable changed;bool entered=false,released=false;
  void wait() {
    std::unique_lock lock(mutex);entered=true;changed.notify_all();changed.wait(lock,[&]{return released;});
  }
  bool reached() {
    std::unique_lock lock(mutex);return changed.wait_for(lock,std::chrono::seconds(5),[&]{return entered;});
  }
  void release() { {std::lock_guard lock(mutex);released=true;}changed.notify_all(); }
};

// Every mutable Worker field is synchronized. No test assertion or shared
// Json mutation runs on a business thread. Each response owns its Json tree.
struct Worker final:WorkerPort {
  struct Call { std::size_t number=0;std::string parameters,profile;int rpc_timeout=0; };
  using Response=std::function<Json(const Call&)>;
  std::mutex mutex;std::condition_variable changed;
  std::vector<Call> requests;std::vector<std::string> cancelled;
  std::size_t profiles=0;std::set<std::size_t> released;bool open=false;
  std::string current_profile=profile,profile_mode="valid";
  Response response;std::function<std::int64_t()> now;

  explicit Worker(std::function<std::int64_t()> clock):now(std::move(clock)) {}

  Json freshness(const std::string& url,const std::string& selected,const std::string& control="max-age=60") const {
    auto out=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":0,"response_delay_ms":0,"invalid":false})");
    out.set("url",url);out.set("request_profile",selected);out.set("cache_control",control);out.set("received_at_ms",now());return out;
  }

  Json validator(const std::string& url,const std::string& selected) const {
    auto out=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"shared-v1\"","last_modified":"","cache_control":"max-age=60","vary":"","date":"","age":"","expires":""})");
    out.set("url",url);out.set("request_profile",selected);return out;
  }

  Json page(const Call& call,bool with_validator=false,const std::string& control="max-age=60") const {
    const auto params=Json::parse(call.parameters);const auto url=params.at("url").str();
    auto out=Json::parse(R"({"status":"ok","http_status":200,"html":"","text":"","title":"shared fixture","content_type":"text/plain","extractor":"direct-shared-fixture/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":100}})");
    out.set("html",body);out.set("text",body);out.set("final_url",url);out.set("publication",publication());
    out.set("http_freshness",freshness(url,call.profile,control));
    if(with_validator)out.set("http_cache",validator(url,call.profile));
    return out;
  }

  Json conditional(const Call& call) const {
    const auto params=Json::parse(call.parameters);
    if(!params.has("conditional"))return page(call,true);
    const auto url=params.at("url").str();auto out=Json::object();
    out.set("status","not_modified");out.set("http_status",std::int64_t{304});out.set("final_url",url);
    out.set("binding_id",params.at("conditional").at("binding_id"));
    out.set("http_cache",validator(url,call.profile));out.set("http_freshness",freshness(url,call.profile));
    out.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":0})"));return out;
  }

  Json call(const std::string& method,const Json& params,int timeout) override {
    if(method=="fetch.profile") {
      std::string selected,mode;
      {std::lock_guard lock(mutex);++profiles;selected=current_profile;mode=profile_mode;}
      changed.notify_all();
      if(mode=="unavailable")throw Error("E_WORKER_UNAVAILABLE","offline profile fixture",503);
      if(mode=="missing")return Json::object();
      auto out=Json::object();out.set("version",mode=="bad"?"bad-profile-version":"bzs-http-freshness-1");
      out.set("request_profile",selected);return out;
    }
    if(method=="fetch.cancel") {
      {std::lock_guard lock(mutex);cancelled.push_back(params.at("attempt_id").str());}
      changed.notify_all();return Json::object();
    }
    if(method!="fetch.extract")throw std::runtime_error("unexpected Worker method "+method);
    Call captured;Response make;
    {
      std::unique_lock lock(mutex);captured={requests.size()+1,params.canonical(),current_profile,timeout};
      requests.push_back(captured);make=response;changed.notify_all();
      changed.wait(lock,[&]{return open||released.contains(captured.number);});
    }
    return make?make(captured):page(captured);
  }

  void responder(Response make) { std::lock_guard lock(mutex);response=std::move(make); }
  void select_profile(const std::string& selected,const std::string& mode="valid") {
    std::lock_guard lock(mutex);current_profile=selected;profile_mode=mode;
  }
  void hold() { std::lock_guard lock(mutex);open=false; }
  void release(std::size_t number) { {std::lock_guard lock(mutex);released.insert(number);}changed.notify_all(); }
  void release_all() { {std::lock_guard lock(mutex);open=true;}changed.notify_all(); }
  bool wait_requests(std::size_t count) {
    std::unique_lock lock(mutex);return changed.wait_for(lock,std::chrono::seconds(5),[&]{return requests.size()>=count;});
  }
  std::vector<Call> calls() { std::lock_guard lock(mutex);return requests; }
  std::vector<std::string> cancellations() { std::lock_guard lock(mutex);return cancelled; }
};

struct Fixture {
  Temp temp;std::atomic<std::int64_t> now{epoch};Store store;Worker worker;WebService web;
  Fixture():store(temp.path/"db",[this]{return now.load();}),worker([this]{return now.load();}),web(store,worker,{}) {}
};

struct Outcome { Json value;std::exception_ptr error;std::atomic<bool> done{false}; };
struct Running {
  Worker& worker;std::vector<std::thread> threads;std::vector<std::shared_ptr<Outcome>> outcomes;
  std::vector<std::function<void()>> cleanup;
  explicit Running(Worker& port):worker(port) {}
  ~Running() {
    worker.release_all();for(auto& action:cleanup)action();join_all();
  }
  std::size_t start(std::function<Json()> run) {
    const auto slot=outcomes.size();auto outcome=std::make_shared<Outcome>();outcomes.push_back(outcome);
    threads.emplace_back([outcome,run=std::move(run)] {
      try {outcome->value=run();}catch(...) {outcome->error=std::current_exception();}
      outcome->done.store(true);
    });return slot;
  }
  std::size_t read(WebService& web,const std::string& owner,const Json& args=read_args()) {
    const auto encoded=args.canonical();return start([&web,owner,encoded]{return web.read(owner,Json::parse(encoded));});
  }
  void join(std::size_t slot) { if(threads.at(slot).joinable())threads.at(slot).join(); }
  void join_all() { for(auto& thread:threads)if(thread.joinable())thread.join(); }
  Json success(std::size_t slot) {
    join(slot);if(outcomes.at(slot)->error)std::rethrow_exception(outcomes.at(slot)->error);return outcomes.at(slot)->value;
  }
  Json failure(std::size_t slot,const std::string& expected) {
    join(slot);CHECK(outcomes.at(slot)->error!=nullptr);
    ++assertions;
    try {std::rethrow_exception(outcomes.at(slot)->error);}
    catch(const Error& error) {
      if(error.code!=expected)throw std::runtime_error("expected "+expected+", got "+error.code);
      return error.details;
    }
  }
};

// Poll only the locked observable state. The five-second bound detects a
// missing admission/deadlock; it is not a delay used to manufacture overlap.
bool wait_state(WebService& web,std::size_t flights,std::size_t members) {
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  do {
    const auto state=WebReadTestAccess::state(web);
    if(state.flights==flights&&state.members==members)return true;
    std::this_thread::yield();
  }while(std::chrono::steady_clock::now()<deadline);
  return false;
}

void usage(const Json& result,const std::string& role,std::int64_t members,
           std::int64_t requests=1,std::int64_t bytes=100,bool known=true) {
  const auto shared=result.at("shared_read"),own=result.at("usage"),physical=shared.at("physical_usage");
  CHECK(shared.at("scope").str()=="process");CHECK(shared.at("role").str()==role);CHECK(shared.at("members").num()==members);
  const auto attempt=shared.at("attempt_id").str();CHECK(attempt.size()==48);
  CHECK(std::all_of(attempt.begin(),attempt.end(),[](char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');}));
  CHECK(own.at("scope").str()=="direct_read");CHECK(own.at("model_calls").num()==0);
  CHECK(physical.at("known").flag()==known);
  if(known) {CHECK(physical.at("external_requests").num()==requests);CHECK(physical.at("content_bytes").num()==bytes);}
  else {CHECK(physical.at("external_requests").is_null());CHECK(physical.at("content_bytes").is_null());}
  if(role=="subscriber") {
    CHECK(own.at("known").flag());CHECK(own.at("external_requests").num()==0);CHECK(own.at("content_bytes").num()==0);
  } else {
    CHECK(own.at("known").flag()==known);
    if(known) {CHECK(own.at("external_requests").num()==requests);CHECK(own.at("content_bytes").num()==bytes);}
    else {CHECK(own.at("external_requests").is_null());CHECK(own.at("content_bytes").is_null());}
  }
}

void same_physical_receipt(const Json& first,const Json& second) {
  CHECK(first.at("shared_read").at("attempt_id").str()==second.at("shared_read").at("attempt_id").str());
  CHECK(first.at("shared_read").at("physical_usage").canonical()==second.at("shared_read").at("physical_usage").canonical());
}

void same_attempt(const Json& first,const Json& second) {
  CHECK(first.at("snapshot_id").str()==second.at("snapshot_id").str());same_physical_receipt(first,second);
}

TEST(same_owner_network_miss_shares_once_and_attributes_known_or_unknown_usage) {
  for(bool known:{true,false}) {
    Fixture f;if(!known)f.worker.responder([&](const Worker::Call& call){return without(f.worker.page(call),"usage");});
    Running run(f.worker);const auto payer=run.read(f.web,"alice",read_args(target,"cache_first"));
    CHECK(f.worker.wait_requests(1));
    rejects([&]{f.web.read("alice",read_args(target,"cache_only"));},"E_NOT_CACHED");CHECK(f.worker.calls().size()==1);
    const auto member=run.read(f.web,"alice",read_args(target,"cache_first"));
    CHECK(wait_state(f.web,1,2));f.worker.release_all();const auto a=run.success(payer),b=run.success(member);
    CHECK(f.worker.calls().size()==1);CHECK(!a.at("from_cache").flag());CHECK(!b.at("from_cache").flag());
    same_attempt(a,b);usage(a,"payer",2,1,100,known);usage(b,"subscriber",2,1,100,known);
    CHECK(f.store.stats("alice").at("snapshots").num()==1);CHECK(wait_state(f.web,0,0));
    const auto cached=f.web.read("alice",read_args(target,"cache_only"));CHECK(cached.at("from_cache").flag());
    CHECK(!cached.has("shared_read"));CHECK(cached.at("usage").at("scope").str()=="direct_read");
    CHECK(cached.at("usage").at("known").flag());CHECK(cached.at("usage").at("external_requests").num()==0);
    CHECK(cached.at("usage").at("content_bytes").num()==0);CHECK(cached.at("usage").at("model_calls").num()==0);
    CHECK(f.worker.calls().size()==1);
    const auto next=f.web.read("alice",read_args());usage(next,"payer",1,1,100,known);
    CHECK(next.at("shared_read").at("attempt_id").str()!=a.at("shared_read").at("attempt_id").str());CHECK(f.worker.calls().size()==2);
  }
}

TEST(shared_conditional_refresh_commits_the_bound_metadata_only_once) {
  Fixture f;f.worker.responder([&](const Worker::Call& call){return f.worker.conditional(call);});f.worker.release_all();
  const auto initial=f.web.read("alice",read_args());const auto proof=f.store.evidence("alice",initial.at("snapshot_id").str(),0,12);
  f.now.fetch_add(61000);f.worker.hold();Running run(f.worker);const auto payer=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));
  rejects([&]{f.web.read("alice",read_args(target,"cache_only"));},"E_CACHE_STALE");CHECK(f.worker.calls().size()==2);
  const auto member=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));f.worker.release_all();
  const auto a=run.success(payer),b=run.success(member);same_attempt(a,b);usage(a,"payer",2,1,0);usage(b,"subscriber",2,1,0);
  for(const auto& result:{a,b}) {
    CHECK(result.at("snapshot_id").str()==initial.at("snapshot_id").str());CHECK(result.at("revision").num()==initial.at("revision").num());
    CHECK(result.at("fetch").at("http_status").num()==304);CHECK(result.at("fetch").at("revalidated").flag());
    CHECK(result.at("fetched_at_ms").num()==f.now.load());CHECK(result.at("content").at("text").str()==body);
  }
  CHECK(f.worker.calls().size()==2);CHECK(Json::parse(f.worker.calls().back().parameters).has("conditional"));
  CHECK(f.store.stats("alice").at("snapshots").num()==1);
  CHECK(f.store.evidence_get("alice",proof.at("evidence_id").str()).at("quote").str()=="sharedneedle");CHECK(wait_state(f.web,0,0));
}

TEST(shared_members_keep_their_own_windows_and_freshness_bounds) {
  Fixture f;auto first=read_args("https://shared.example/中文"),second=read_args("https://shared.example/%E4%B8%AD%E6%96%87");
  first.set("max_bytes",std::int64_t{12});first.set("max_age_s",std::int64_t{0});
  second.set("start_byte",std::int64_t{13});second.set("max_bytes",std::int64_t{6});second.set("max_age_s",std::int64_t{60});
  Running run(f.worker);const auto payer=run.read(f.web,"alice",first);CHECK(f.worker.wait_requests(1));
  const auto member=run.read(f.web,"alice",second);CHECK(wait_state(f.web,1,2));f.worker.release_all();
  const auto a=run.success(payer),b=run.success(member);same_attempt(a,b);CHECK(f.worker.calls().size()==1);
  CHECK(a.at("content").at("text").str()=="sharedneedle");CHECK(a.at("content").at("start_byte").num()==0);
  CHECK(b.at("content").at("text").str()=="中文");CHECK(b.at("content").at("start_byte").num()==13);
  CHECK(!a.at("cache").at("fresh").flag());CHECK(b.at("cache").at("fresh").flag());
  CHECK(Json::parse(f.worker.calls().at(0).parameters).at("url").str()=="https://shared.example/%E4%B8%AD%E6%96%87");
}

TEST(an_invalid_leader_or_subscriber_window_does_not_poison_the_other_member) {
  for(bool bad_leader:{true,false})for(const std::string kind:{"beyond","utf8","too_small"}) {
    Fixture f;auto bad=read_args();
    if(kind=="beyond")bad.set("start_byte",std::int64_t{1000});
    if(kind=="utf8")bad.set("start_byte",std::int64_t{14});
    if(kind=="too_small") {bad.set("start_byte",std::int64_t{13});bad.set("max_bytes",std::int64_t{1});}
    Running run(f.worker);const auto a=run.read(f.web,"alice",bad_leader?bad:read_args());CHECK(f.worker.wait_requests(1));
    const auto b=run.read(f.web,"alice",bad_leader?read_args():bad);CHECK(wait_state(f.web,1,2));f.worker.release_all();
    const auto failed=run.failure(bad_leader?a:b,"E_EVIDENCE_RANGE");const auto good=run.success(bad_leader?b:a);
    CHECK(good.at("content").at("text").str()==body);usage(good,bad_leader?"subscriber":"payer",2);
    usage(failed,bad_leader?"payer":"subscriber",2);same_physical_receipt(failed,good);
    CHECK(f.worker.calls().size()==1);CHECK(f.store.stats("alice").at("snapshots").num()==1);CHECK(wait_state(f.web,0,0));
  }
}

TEST(owner_and_exact_uri_boundaries_do_not_use_search_or_deletion_equivalence) {
  struct Case { std::string first,second,owner; };
  const std::vector<Case> cases={
    {target,target,"bob"},{target,"https://shared.example/%70age","alice"},
    {target,target+"?view=two","alice"},{"https://shared.example/a/b","https://shared.example/a%2Fb","alice"},
    {"https://shared.example/a?x=b","https://shared.example/a%3Fx=b","alice"},
    {"https://shared.example/a%25b","https://shared.example/a%2525b","alice"}};
  for(const auto& input:cases) {
    Fixture f;Running run(f.worker);const auto a=run.read(f.web,"alice",read_args(input.first));CHECK(f.worker.wait_requests(1));
    const auto b=run.read(f.web,input.owner,read_args(input.second));CHECK(f.worker.wait_requests(2));CHECK(wait_state(f.web,2,2));
    f.worker.release_all();const auto first=run.success(a),second=run.success(b);usage(first,"payer",1);usage(second,"payer",1);
    CHECK(first.at("shared_read").at("attempt_id").str()!=second.at("shared_read").at("attempt_id").str());
    CHECK(first.at("document_id").str()!=second.at("document_id").str());CHECK(f.worker.calls().size()==2);
    CHECK(Json::parse(f.worker.calls().at(0).parameters).at("url").str()==input.first);
    CHECK(Json::parse(f.worker.calls().at(1).parameters).at("url").str()==input.second);
  }
}

TEST(timeout_and_direct_parameters_do_not_borrow_durable_read_budgets) {
  for(bool same_timeout:{true,false}) {
    Fixture f;auto first=read_args(),second=read_args();first.set("timeout_ms",std::int64_t{1000});
    second.set("timeout_ms",std::int64_t{same_timeout?1000:20000});
    Running run(f.worker);const auto a=run.read(f.web,"alice",first);CHECK(f.worker.wait_requests(1));
    f.now.fetch_add(500);const auto b=run.read(f.web,"alice",second);
    if(same_timeout)CHECK(wait_state(f.web,1,2));else CHECK(f.worker.wait_requests(2));
    f.worker.release_all();const auto x=run.success(a),y=run.success(b);usage(x,"payer",same_timeout?2:1);usage(y,same_timeout?"subscriber":"payer",same_timeout?2:1);
    const auto calls=f.worker.calls();CHECK(calls.size()==(same_timeout?1U:2U));
    CHECK(Json::parse(calls.at(0).parameters).at("timeout_ms").num()==1000);CHECK(calls.at(0).rpc_timeout==6000);
    if(!same_timeout) {CHECK(Json::parse(calls.at(1).parameters).at("timeout_ms").num()==20000);CHECK(calls.at(1).rpc_timeout==25000);}
    for(const auto& call:calls) {const auto params=Json::parse(call.parameters);CHECK(!params.has("max_requests"));CHECK(!params.has("max_content_bytes"));CHECK(!params.has("attempt_id"));}
  }
  Fixture f;f.worker.release_all();
  for(const std::string key:{"max_requests","max_content_bytes","attempt_id","cancel"}) {
    auto args=read_args();args.set(key,std::int64_t{1});rejects([&]{f.web.read("alice",args);},"E_SCHEMA");
  }
  CHECK(f.worker.calls().empty());
}

TEST(changed_or_unproven_request_profiles_never_join_an_active_flight) {
  for(const std::string mode:{"changed","unavailable","missing","bad"}) {
    Fixture f;Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));
    f.worker.select_profile(mode=="changed"?sha256("another profile"):profile,mode=="changed"?"valid":mode);
    const auto b=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));
    const auto state=WebReadTestAccess::state(f.web);CHECK(state.members==(mode=="changed"?2U:1U));
    // Both physical requests are already dispatched. Deliver them in order so
    // a legal in-place metadata replacement cannot race the first window.
    f.worker.release(1);const auto x=run.success(a);f.worker.release(2);const auto y=run.success(b);
    usage(x,"payer",1);usage(y,"payer",1);
    CHECK(x.at("shared_read").at("attempt_id").str()!=y.at("shared_read").at("attempt_id").str());CHECK(f.worker.calls().size()==2);
  }
}

TEST(shared_responses_require_a_valid_freshness_profile_matching_admission) {
  for(const std::string kind:{"missing","invalid","different"}) {
    Fixture f;f.worker.responder([&](const Worker::Call& call) {
      auto out=f.worker.page(call);
      if(kind=="missing")return without(out,"http_freshness");
      out.at("http_freshness").set(kind=="different"?"request_profile":"version",kind=="different"?sha256("changed during fetch"):"bad-version");return out;
    });
    Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));const auto b=run.read(f.web,"alice");
    CHECK(wait_state(f.web,1,2));f.worker.release_all();const auto expected=kind=="different"?"E_READ_PROFILE_CHANGED":"E_WORKER_PROTOCOL";
    run.failure(a,expected);run.failure(b,expected);CHECK(f.worker.calls().size()==1);CHECK(f.store.stats("alice").at("snapshots").num()==0);
    CHECK(wait_state(f.web,0,0));f.worker.responder({});CHECK(f.web.read("alice",read_args()).at("content").at("text").str()==body);
    CHECK(f.worker.calls().size()==2);
  }
}

TEST(permission_snapshot_metadata_and_publication_fences_split_new_admissions) {
  for(const std::string kind:{"epoch","snapshot","metadata","publication","unrelated_delete","delete_revive"}) {
    Fixture f;f.worker.release_all();const auto initial=f.web.read("alice",read_args());
    const auto marker=f.store.ingest("alice",{"https://shared.example/other","other body","text/plain","other"});
    f.worker.hold();Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));
    Store writer(f.temp.path/"db",[&]{return f.now.load();});
    if(kind=="epoch")sql(writer.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");
    if(kind=="snapshot")writer.ingest("alice",{target,"a different authorized snapshot","text/plain","different"});
    if(kind=="metadata")sql(writer.raw_handle(),"UPDATE snapshots SET meta_json=json_set(meta_json,'$.fixture_metadata_version',2) WHERE document_id IN(SELECT id FROM documents WHERE owner='alice' AND url='https://shared.example/page');");
    if(kind=="publication")writer.observe_web_publication("alice",target,target,publication("noindex"),writer.publication_fence("alice",target));
    if(kind=="unrelated_delete")writer.erase("alice",marker.at("document_id").str());
    if(kind=="delete_revive") {writer.erase("alice",initial.at("document_id").str());writer.ingest("alice",{target,"explicit new authorization","text/plain","new"});}
    const auto b=run.read(f.web,"alice");CHECK(f.worker.wait_requests(3));CHECK(wait_state(f.web,2,2));
    f.worker.release(2);
    if(kind=="epoch")run.failure(a,"E_POLICY_CHANGED");else if(kind=="delete_revive")run.failure(a,"E_TARGET_DELETED");else usage(run.success(a),"payer",1);
    f.worker.release(3);usage(run.success(b),"payer",1);CHECK(f.worker.calls().size()==3);CHECK(wait_state(f.web,0,0));
  }
}

TEST(shared_inflight_alias_deletion_and_revival_reject_both_full_and_304_results) {
  for(bool conditional:{false,true})for(bool revive:{false,true})for(bool encoded_request:{false,true}) {
    Fixture f;const auto requested=encoded_request?"https://shared.example/%70age":target;
    const auto alias=encoded_request?target:"https://shared.example/%70age";
    const auto marker=f.store.ingest("alice",{alias,"alias marker","text/plain","alias"});
    if(conditional)f.worker.responder([&](const Worker::Call& call){return f.worker.conditional(call);});
    f.worker.release_all();const auto initial=f.web.read("alice",read_args(requested));f.worker.hold();
    Running run(f.worker);const auto a=run.read(f.web,"alice",read_args(requested));CHECK(f.worker.wait_requests(2));
    const auto b=run.read(f.web,"alice",read_args(requested));CHECK(wait_state(f.web,1,2));
    Store writer(f.temp.path/"db",[&]{return f.now.load();});writer.erase("alice",marker.at("document_id").str());
    if(revive)writer.ingest("alice",{alias,"explicit revived alias","text/plain","revived"});
    f.worker.release_all();run.failure(a,"E_TARGET_DELETED");run.failure(b,"E_TARGET_DELETED");CHECK(f.worker.calls().size()==2);
    CHECK(f.store.snapshot("alice",initial.at("snapshot_id").str()).at("canonical_text").str()==body);
    CHECK(Json::parse(f.worker.calls().back().parameters).has("conditional")==conditional);CHECK(wait_state(f.web,0,0));
  }
  struct Distinct {std::string requested,deleted,owner;};
  const std::vector<Distinct> separate={
    {target,"https://shared.example/%70age","bob"},
    {"https://shared.example/a/b","https://shared.example/a%2Fb","alice"},
    {"https://shared.example/a?x=b","https://shared.example/a%3Fx=b","alice"},
    {"https://shared.example/a%25b","https://shared.example/a%2525b","alice"}};
  for(const auto& input:separate) {
    Fixture f;const auto marker=f.store.ingest(input.owner,{input.deleted,"distinct marker","text/plain","distinct"});
    Running run(f.worker);const auto a=run.read(f.web,"alice",read_args(input.requested));CHECK(f.worker.wait_requests(1));
    const auto b=run.read(f.web,"alice",read_args(input.requested));CHECK(wait_state(f.web,1,2));
    f.store.erase(input.owner,marker.at("document_id").str());f.worker.release_all();
    const auto x=run.success(a),y=run.success(b);same_attempt(x,y);usage(x,"payer",2);usage(y,"subscriber",2);
    CHECK(f.worker.calls().size()==1);CHECK(x.at("content").at("text").str()==body);
  }
}

TEST(shared_inflight_principal_revocation_denies_all_members_without_new_body) {
  Fixture f;Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));
  const auto b=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));Store writer(f.temp.path/"db");writer.revoke_principal("admin","alice");
  f.worker.release_all();run.failure(a,"E_PRINCIPAL_REVOKED");run.failure(b,"E_PRINCIPAL_REVOKED");
  CHECK(f.worker.calls().size()==1);CHECK(scalar(f.store.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);
  rejects([&]{f.web.read("alice",read_args());},"E_PRINCIPAL_REVOKED");CHECK(f.worker.calls().size()==1);CHECK(wait_state(f.web,0,0));
}

TEST(shared_old_publication_cannot_be_restored_by_a_newer_allowing_response) {
  for(const std::string directive:{"noindex","nosnippet"}) {
    Fixture f;f.worker.release_all();f.web.read("alice",read_args());const auto frozen=f.store.search("alice",parse_query(Json::string("sharedneedle")));
    f.worker.hold();Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));
    const auto b=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));Store writer(f.temp.path/"db",[&]{return f.now.load();});
    writer.observe_web_publication("alice",target,target,publication(directive),writer.publication_fence("alice",target));
    Worker newer([&]{return f.now.load();});newer.release_all();WebService new_web(writer,newer,{});const auto allowed=new_web.read("alice",read_args());
    f.worker.release_all();const auto x=run.success(a),y=run.success(b);same_attempt(x,y);
    for(const auto& result:{x,y}) {
      const auto snap=f.store.snapshot("alice",result.at("snapshot_id").str());CHECK(!snap.at("publication").at("snippet_allowed").flag());
      if(directive=="noindex") {CHECK(!snap.at("publication").at("index_allowed").flag());CHECK(!result.at("indexed").flag());}
    }
    CHECK(f.store.cached("alice",target)->at("snapshot_id").str()==allowed.at("snapshot_id").str());
    const auto old=f.store.page("alice",frozen.at("session_id").str(),"").at("hits");
    if(directive=="noindex")CHECK(old.size()==0);else {CHECK(old.size()==1);CHECK(old.at(std::size_t{0}).at("snippet").str().empty());}
    CHECK(f.worker.calls().size()==2);CHECK(newer.calls().size()==1);
  }
}

TEST(each_post_commit_window_rechecks_current_revocation_deletion_and_policy) {
  for(const std::string change:{"revoke","epoch","target_delete","alias_delete","publication"}) {
    Fixture f;const auto marker=f.store.ingest("alice",{"https://shared.example/%70age","alias marker","text/plain","alias"});
    Gate window;WebReadTestAccess::window_hook(f.web,[&](const std::string& role){if(role=="subscriber")window.wait();});
    Running run(f.worker);run.cleanup.push_back([&]{window.release();WebReadTestAccess::window_hook(f.web,{});});
    const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));const auto b=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));
    f.worker.release_all();CHECK(window.reached());const auto payer=run.success(a);usage(payer,"payer",2);CHECK(wait_state(f.web,0,0));
    Store writer(f.temp.path/"db",[&]{return f.now.load();});
    if(change=="revoke")writer.revoke_principal("admin","alice");
    if(change=="epoch")sql(writer.raw_handle(),"UPDATE subjects SET epoch=epoch+1 WHERE owner='alice';");
    if(change=="target_delete")writer.erase("alice",payer.at("document_id").str());
    if(change=="alias_delete") {writer.erase("alice",marker.at("document_id").str());writer.ingest("alice",{"https://shared.example/%70age","revived","text/plain","new"});}
    if(change=="publication")writer.observe_web_publication("alice",target,target,publication("noindex"),writer.publication_fence("alice",target));
    window.release();
    if(change=="publication") {const auto member=run.success(b);CHECK(!member.at("indexed").flag());CHECK(member.at("content").at("text").str()==body);usage(member,"subscriber",2);}
    else {
      const auto failed=run.failure(b,change=="revoke"?"E_PRINCIPAL_REVOKED":change=="epoch"?"E_POLICY_CHANGED":"E_TARGET_DELETED");
      usage(failed,"subscriber",2);same_physical_receipt(payer,failed);
    }
    CHECK(f.worker.calls().size()==1);CHECK(f.worker.cancellations().empty());WebReadTestAccess::window_hook(f.web,{});
  }
}

TEST(shared_fetch_errors_broadcast_once_and_do_not_cache_failure_or_retry) {
  for(const std::string kind:{"network","network_unknown","timeout","unavailable","malformed","no_store"}) {
    Fixture f;f.worker.responder([&](const Worker::Call& call) {
      if(kind=="unavailable")throw Error("E_WORKER_UNAVAILABLE","synthetic unavailable",503);
      if(kind=="network")return Json::parse(R"({"status":"network_error","error":"synthetic network failure","usage":{"external_requests":1,"content_bytes":25}})");
      if(kind=="network_unknown")return Json::parse(R"({"status":"network_error","error":"synthetic unknown network usage"})");
      if(kind=="timeout")return Json::parse(R"({"status":"timeout","error":"synthetic timeout"})");
      auto out=f.worker.page(call,false,kind=="no_store"?"no-store":"max-age=60");
      if(kind=="malformed")out.set("text",std::int64_t{42});
      return out;
    });
    Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));const auto b=run.read(f.web,"alice");
    CHECK(wait_state(f.web,1,2));f.worker.release_all();
    const std::string expected=(kind=="network"||kind=="network_unknown")?"E_FETCH_NETWORK":kind=="timeout"?"E_FETCH_TIMEOUT":kind=="unavailable"?"E_WORKER_UNAVAILABLE":kind=="no_store"?"E_CACHE_POLICY":"E_WORKER_PROTOCOL";
    const auto paid=run.failure(a,expected),associated=run.failure(b,expected);
    const bool known=kind=="network"||kind=="malformed"||kind=="no_store";
    usage(paid,"payer",2,1,kind=="network"?25:100,known);usage(associated,"subscriber",2,1,kind=="network"?25:100,known);
    same_physical_receipt(paid,associated);CHECK(f.worker.calls().size()==1);CHECK(f.store.stats("alice").at("snapshots").num()==0);
    CHECK(wait_state(f.web,0,0));f.worker.responder({});usage(f.web.read("alice",read_args()),"payer",1);CHECK(f.worker.calls().size()==2);
  }
}

TEST(late_shared_no_store_cannot_invalidate_a_newer_successful_representation) {
  Fixture f;f.worker.release_all();f.web.read("alice",read_args());f.worker.hold();
  f.worker.responder([&](const Worker::Call& call){return f.worker.page(call,false,"no-store");});
  Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));const auto b=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));
  f.now.fetch_add(1000);Store writer(f.temp.path/"db",[&]{return f.now.load();});Worker current([&]{return f.now.load();});current.release_all();WebService new_web(writer,current,{});
  const auto latest=new_web.read("alice",read_args());const auto metadata=writer.cached("alice",target)->at("meta").canonical();
  f.worker.release_all();run.failure(a,"E_CACHE_POLICY");run.failure(b,"E_CACHE_POLICY");
  CHECK(f.store.cached("alice",target)->at("meta").canonical()==metadata);
  const auto hit=f.web.read("alice",read_args(target,"cache_only"));CHECK(hit.at("from_cache").flag());CHECK(hit.at("snapshot_id").str()==latest.at("snapshot_id").str());
  CHECK(f.worker.calls().size()==2);CHECK(current.calls().size()==1);CHECK(wait_state(f.web,0,0));
}

TEST(shared_storage_failure_rolls_back_body_and_index_and_releases_the_flight) {
  Fixture f;Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));
  const auto b=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));
  sql(f.store.raw_handle(),"CREATE TRIGGER reject_shared_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT,'synthetic snapshot failure'); END;");
  f.worker.release_all();run.failure(a,"E_STORAGE");run.failure(b,"E_STORAGE");
  CHECK(scalar(f.store.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);CHECK(scalar(f.store.raw_handle(),"SELECT COUNT(*) FROM content_fts")==0);
  CHECK(f.store.stats("alice").at("content_bytes").num()==0);CHECK(f.worker.calls().size()==1);CHECK(wait_state(f.web,0,0));
  sql(f.store.raw_handle(),"DROP TRIGGER reject_shared_snapshot;");usage(f.web.read("alice",read_args()),"payer",1);
  CHECK(f.worker.calls().size()==2);CHECK(f.store.stats("alice").at("snapshots").num()==1);
}

TEST(same_thread_reentry_does_not_wait_on_itself_or_reuse_the_outer_304) {
  Fixture f;f.worker.release_all();f.worker.responder([&](const Worker::Call& call){return f.worker.conditional(call);});
  const auto initial=f.web.read("alice",read_args());Json nested;
  f.worker.responder([&](const Worker::Call& call) {
    auto out=f.worker.conditional(call);
    if(call.number==2) {f.now.fetch_add(1000);nested=f.web.read("alice",read_args());}
    return out;
  });
  rejects([&]{f.web.read("alice",read_args());},"E_REVALIDATION_STALE");
  CHECK(f.worker.calls().size()==3);usage(nested,"payer",1,1,0);CHECK(nested.at("snapshot_id").str()==initial.at("snapshot_id").str());
  CHECK(f.store.cached("alice",target)->at("fetched_at_ms").num()==f.now.load());CHECK(wait_state(f.web,0,0));
}

TEST(lifetime_member_limit_keeps_the_first_32_and_runs_overflow_independently) {
  Fixture f;Running run(f.worker);run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));
  for(std::size_t i=1;i<32;++i)run.read(f.web,"alice");
  CHECK(wait_state(f.web,1,32));
  const auto overflow=run.read(f.web,"alice");CHECK(f.worker.wait_requests(2));CHECK(wait_state(f.web,1,32));
  f.worker.release_all();const auto payer=run.success(0);usage(payer,"payer",32);
  for(std::size_t i=1;i<32;++i) {const auto member=run.success(i);usage(member,"subscriber",32);same_attempt(payer,member);}
  const auto excess=run.success(overflow);usage(excess,"payer",1);
  CHECK(excess.at("shared_read").at("attempt_id").str()!=payer.at("shared_read").at("attempt_id").str());
  CHECK(f.worker.calls().size()==2);CHECK(wait_state(f.web,0,0));usage(f.web.read("alice",read_args()),"payer",1);CHECK(f.worker.calls().size()==3);
}

TEST(registry_capacity_is_bounded_and_distinct_service_instances_do_not_share) {
  {
    Fixture f;Running run(f.worker);
    for(std::size_t i=0;i<64;++i)run.read(f.web,"alice",read_args("https://shared.example/slot/"+std::to_string(i)));
    CHECK(f.worker.wait_requests(64));CHECK(wait_state(f.web,64,64));
    const auto overflow=run.read(f.web,"alice",read_args("https://shared.example/overflow"));CHECK(f.worker.wait_requests(65));CHECK(wait_state(f.web,64,64));
    f.worker.release_all();for(std::size_t i=0;i<=overflow;++i)usage(run.success(i),"payer",1);
    CHECK(f.worker.calls().size()==65);CHECK(wait_state(f.web,0,0));
    usage(f.web.read("alice",read_args("https://shared.example/after-capacity")),"payer",1);CHECK(f.worker.calls().size()==66);
  }
  {
    Fixture f;Store second(f.temp.path/"db",[&]{return f.now.load();});WebService other(second,f.worker,{});
    Running run(f.worker);const auto a=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));const auto b=run.read(other,"alice");
    CHECK(f.worker.wait_requests(2));CHECK(wait_state(f.web,1,1));CHECK(wait_state(other,1,1));f.worker.release_all();
    const auto first=run.success(a),last=run.success(b);usage(first,"payer",1);usage(last,"payer",1);
    CHECK(first.at("shared_read").at("attempt_id").str()!=last.at("shared_read").at("attempt_id").str());CHECK(f.worker.calls().size()==2);
  }
}

TEST(direct_reads_do_not_join_durable_attempts_or_receive_their_cancellation) {
  Fixture f;const auto job=f.store.read_many_submit("alice",{target},"separate-durable-attempt",{}).at("job_id").str();
  const auto lease=f.store.lease_next_read();CHECK(lease.has_value());Running run(f.worker);
  const auto payer=run.read(f.web,"alice");CHECK(f.worker.wait_requests(1));
  const auto batch=run.start([&]{f.web.execute_read(*lease);return Json::object();});CHECK(f.worker.wait_requests(2));
  const auto member=run.read(f.web,"alice");CHECK(wait_state(f.web,1,2));
  f.store.job_control("alice",job,"cancel");const auto cancelled=f.store.active_read_attempt("alice",job);CHECK(cancelled==lease->attempt_id);
  f.web.cancel_read_attempt(cancelled);CHECK(f.worker.cancellations()==std::vector<std::string>{lease->attempt_id});
  f.worker.release_all();const auto a=run.success(payer),b=run.success(member);run.success(batch);same_attempt(a,b);usage(a,"payer",2);usage(b,"subscriber",2);
  CHECK(f.worker.calls().size()==2);CHECK(!Json::parse(f.worker.calls().at(0).parameters).has("attempt_id"));
  CHECK(Json::parse(f.worker.calls().at(1).parameters).at("attempt_id").str()==lease->attempt_id);
  CHECK(f.store.job_get("alice",job).at("state").str()=="cancelled");CHECK(f.store.job_get("alice",job).at("usage").at("external_requests").num()==1);
  CHECK(!f.store.job_results("alice",job).at("items").at(std::size_t{0}).has("receipt"));CHECK(wait_state(f.web,0,0));
}
}

int main() {
  std::size_t failures=0;
  for(const auto& [name,test]:tests())try {test();std::cout<<"PASS "<<name<<'\n';}
  catch(const std::exception& error) {++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n';}
  std::cout<<tests().size()<<" direct shared tests, "<<assertions<<" assertions, "<<failures<<" failures\n";return failures?1:0;
}
