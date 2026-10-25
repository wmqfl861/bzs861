#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <iostream>
#include <mutex>
#include <set>
#include <thread>
#include <vector>

// Independent assertions, mock and execution sequencing. The friend accessor
// only observes coordination state or advances an explicitly synthetic expiry;
// all business requests and cancellations use the public C++ methods.
namespace bzs {
struct WebReadTestAccess {
  static std::size_t joined(WebService& w) {
    std::lock_guard lock(w.direct_reads_mutex_);std::size_t n=0;
    for(const auto& [key,f]:w.direct_reads_){(void)key;n+=f->members;}return n;
  }
  static void expire(WebService& w,const std::string& id) {
    std::lock_guard lock(w.direct_reads_mutex_);
    w.direct_read_members_.at(id)->expires=std::chrono::steady_clock::now()-std::chrono::seconds(1);
  }
  static bool running(WebService& w,const std::string& id) {
    std::lock_guard lock(w.direct_reads_mutex_);return w.direct_read_members_.at(id)->running;
  }
  static void window_gate(WebService& w,std::function<void(const std::string&)> fn) {
    std::lock_guard lock(w.direct_reads_mutex_);w.direct_read_window_for_test_=std::move(fn);
  }
};
}
using namespace bzs;
namespace {
int checks=0;
#define REQUIRE(x) do {++checks;if(!(x))throw std::runtime_error(std::string(#x)+" at "+std::to_string(__LINE__));}while(false)
const std::string origin="https://independent.example/document";
const std::string profile=sha256("independent-cancellation-review");
struct Gate {
  std::mutex mu;std::condition_variable cv;bool entered=false,open=false;
  void block(){std::unique_lock l(mu);entered=true;cv.notify_all();cv.wait(l,[&]{return open;});}
  bool reached(){std::unique_lock l(mu);return cv.wait_for(l,std::chrono::seconds(5),[&]{return entered;});}
  void release(){std::lock_guard l(mu);open=true;cv.notify_all();}
};
struct Mock final:WorkerPort {
  struct Call {std::string method,params;std::size_t ordinal;};
  std::mutex mu;std::condition_variable cv;std::vector<Call> calls;
  std::vector<std::string> cancels;std::set<std::size_t> allowed;bool open=false;
  std::function<Json(const Call&)> response;std::function<void()> profile_hook;
  Json page(const Call& call) {
    const auto url=Json::parse(call.params).at("url").str();
    auto out=Json::parse(R"({"status":"ok","http_status":200,"html":"independent cancellation body","text":"independent cancellation body","title":"Independent read","content_type":"text/plain","extractor":"independent/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":29},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"independent cancellation body","snippet_title":"Independent read","directives":[],"diagnostics":[]}})");
    out.set("final_url",url);
    auto fresh=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"max-age=600","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":1700000000000,"response_delay_ms":0,"invalid":false})");
    fresh.set("url",url);fresh.set("request_profile",profile);out.set("http_freshness",fresh);return out;
  }
  Json call(const std::string& method,const Json& params,int timeout) override {
    (void)timeout;
    if(method=="fetch.profile") {
      std::function<void()> hook;{std::lock_guard l(mu);hook=profile_hook;}if(hook)hook();
      auto proof=Json::object();proof.set("version","bzs-http-freshness-1");proof.set("request_profile",profile);return proof;
    }
    if(method=="fetch.cancel_direct") {
      std::lock_guard l(mu);cancels.push_back(params.at("control_id").str());return Json::object();
    }
    if(method!="fetch.extract_direct"&&method!="fetch.extract")throw Error("E_METHOD","unexpected mock method",502);
    Call c;std::function<Json(const Call&)> make;
    {std::unique_lock l(mu);c={method,params.canonical(),calls.size()+1};calls.push_back(c);make=response;cv.notify_all();
      cv.wait(l,[&]{return open||allowed.contains(c.ordinal);});}
    return make?make(c):page(c);
  }
  bool reached(std::size_t n){std::unique_lock l(mu);return cv.wait_for(l,std::chrono::seconds(5),[&]{return calls.size()>=n;});}
  void release(std::size_t n){std::lock_guard l(mu);allowed.insert(n);cv.notify_all();}
  void release_all(){std::lock_guard l(mu);open=true;cv.notify_all();}
  std::size_t count(){std::lock_guard l(mu);return calls.size();}
  std::size_t cancelled(){std::lock_guard l(mu);return cancels.size();}
};
struct Fixture {
  std::filesystem::path root=std::filesystem::temp_directory_path()/("bzs-independent-cancel-"+random_id());
  Store store;Mock worker;WebService web;
  Fixture():store(root,[]{return std::int64_t{1700000000000};}),web(store,worker,{}){}
  ~Fixture(){std::error_code ec;std::filesystem::remove_all(root,ec);}
};
Json identity(const std::string& id){auto a=Json::object();a.set("read_id",id);return a;}
Json args(const std::string& id="",const std::string& url=origin,const std::string& freshness="refresh"){
  auto a=Json::object();a.set("url",url);a.set("freshness",freshness);if(!id.empty())a.set("read_id",id);return a;
}
std::string prepare(Fixture& f,const std::string& owner="alice"){return f.web.prepare_read(owner,Json::object()).at("read_id").str();}
template<class Fn> Json error(Fn fn,const std::string& code){
  try {fn();}catch(const Error& e){REQUIRE(e.code==code);return e.details;}throw std::runtime_error("missing "+code);
}
struct Async {
  Mock& worker;std::atomic<bool> done=false;Json result;std::exception_ptr exception;std::function<void()> cleanup;std::thread thread;
  Async(Mock& w,std::function<Json()> fn):worker(w),thread([this,fn=std::move(fn)]{
    try{result=fn();}catch(...){exception=std::current_exception();}done.store(true);
  }){}
  ~Async(){worker.release_all();if(cleanup)cleanup();if(thread.joinable())thread.join();}
  bool ready(){auto end=std::chrono::steady_clock::now()+std::chrono::seconds(5);while(!done.load()&&std::chrono::steady_clock::now()<end)std::this_thread::yield();return done.load();}
  Json get(){if(thread.joinable())thread.join();if(exception)std::rethrow_exception(exception);return result;}
};
bool joined(Fixture& f,std::size_t n){auto end=std::chrono::steady_clock::now()+std::chrono::seconds(5);do{if(WebReadTestAccess::joined(f.web)==n)return true;std::this_thread::yield();}while(std::chrono::steady_clock::now()<end);return false;}
void known(const Json& v,bool payer,std::int64_t n){
  REQUIRE(v.at("usage").at("known").flag());REQUIRE(v.at("usage").at("external_requests").num()==(payer?1:0));
  REQUIRE(v.at("shared_read").at("physical_usage").at("known").flag());
  REQUIRE(v.at("shared_read").at("members").num()==n);
}
void pre_cancel_and_ownership(){
  Fixture f;const auto id=prepare(f);
  error([&]{f.web.cancel_read("bob",identity(id));},"E_NOT_FOUND");
  REQUIRE(f.web.cancel_read("alice",identity(id)).at("cancel_requested").flag());
  const auto d=error([&]{f.web.read("alice",args(id));},"E_CANCELLED");
  REQUIRE(d.at("usage").at("known").flag());REQUIRE(d.at("usage").at("external_requests").num()==0);REQUIRE(f.worker.count()==0);
  error([&]{f.web.read("alice",args(id));},"E_READ_STATE");
}
void concurrent_one_use(){
  Fixture f;const auto id=prepare(f);Async a(f.worker,[&]{return f.web.read("alice",args(id));});REQUIRE(f.worker.reached(1));
  error([&]{f.web.read("alice",args(id));},"E_READ_STATE");REQUIRE(f.worker.count()==1);
  f.worker.release_all();known(a.get(),true,1);const auto c=f.web.cancel_read("alice",identity(id));REQUIRE(!c.at("cancel_requested").flag());
}
void payer_cancel_late_join(){
  Fixture f;const auto p=prepare(f),s=prepare(f),late=prepare(f);
  Async a(f.worker,[&]{return f.web.read("alice",args(p));});REQUIRE(f.worker.reached(1));
  Async b(f.worker,[&]{return f.web.read("alice",args(s));});REQUIRE(joined(f,2));
  f.web.cancel_read("alice",identity(p));REQUIRE(f.worker.cancelled()==0);
  Async c(f.worker,[&]{return f.web.read("alice",args(late));});REQUIRE(joined(f,3));
  f.worker.release_all();known(error([&]{a.get();},"E_CANCELLED"),true,3);known(b.get(),false,3);known(c.get(),false,3);REQUIRE(f.worker.count()==1);
}
void subscriber_early_cancel(){
  Fixture f;const auto p=prepare(f),s=prepare(f);
  Async a(f.worker,[&]{return f.web.read("alice",args(p));});REQUIRE(f.worker.reached(1));
  Async b(f.worker,[&]{return f.web.read("alice",args(s));});REQUIRE(joined(f,2));
  f.web.cancel_read("alice",identity(s));REQUIRE(b.ready());REQUIRE(!a.done.load());
  const auto d=error([&]{b.get();},"E_CANCELLED");REQUIRE(d.at("usage").at("external_requests").num()==0);
  REQUIRE(d.at("usage").at("known").flag());REQUIRE(!d.at("shared_read").at("physical_usage").at("known").flag());
  f.web.cancel_read("alice",identity(s));REQUIRE(f.worker.cancelled()==0);f.worker.release_all();known(a.get(),true,2);
}
void all_cancel_no_orphan_commit(){
  Fixture f;const auto id=prepare(f);Async a(f.worker,[&]{return f.web.read("alice",args(id));});REQUIRE(f.worker.reached(1));
  const auto cancelled=f.web.cancel_read("alice",identity(id));REQUIRE(cancelled.at("physical_cancel_requested").flag());REQUIRE(f.worker.cancelled()==1);
  f.worker.release_all();known(error([&]{a.get();},"E_CANCELLED"),true,1);REQUIRE(!f.store.cached("alice",origin));
  const auto fresh=prepare(f);known(f.web.read("alice",args(fresh)),true,1);REQUIRE(f.worker.count()==2);REQUIRE(f.store.cached("alice",origin).has_value());
}
void cancelled_running_does_not_expire(){
  Fixture f;const auto id=prepare(f);Async a(f.worker,[&]{return f.web.read("alice",args(id));});REQUIRE(f.worker.reached(1));
  f.web.cancel_read("alice",identity(id));WebReadTestAccess::expire(f.web,id);prepare(f);
  REQUIRE(WebReadTestAccess::running(f.web,id));REQUIRE(f.web.cancel_read("alice",identity(id)).at("cancel_requested").flag());
  f.worker.release_all();error([&]{a.get();},"E_CANCELLED");WebReadTestAccess::expire(f.web,id);prepare(f);
  error([&]{f.web.cancel_read("alice",identity(id));},"E_NOT_FOUND");
}
void expiry_reuse_and_terminal_errors(){
  Fixture f;const auto expired=prepare(f);WebReadTestAccess::expire(f.web,expired);
  error([&]{f.web.read("alice",args(expired));},"E_NOT_FOUND");
  const auto invalid=prepare(f);auto bad=args(invalid);bad.set("timeout_ms",std::int64_t{1});
  error([&]{f.web.read("alice",bad);},"E_SCHEMA");REQUIRE(!WebReadTestAccess::running(f.web,invalid));
  REQUIRE(!f.web.cancel_read("alice",identity(invalid)).at("cancel_requested").flag());
  error([&]{f.web.read("alice",args(invalid));},"E_READ_STATE");REQUIRE(f.worker.count()==0);
}
void cache_profile_cancel(){
  Fixture f;f.worker.release_all();f.web.read("alice",args());const auto id=prepare(f);Gate gate;
  f.worker.profile_hook=[&]{gate.block();};Async a(f.worker,[&]{return f.web.read("alice",args(id,origin,"cache_only"));});
  a.cleanup=[&]{gate.release();};
  REQUIRE(gate.reached());f.web.cancel_read("alice",identity(id));gate.release();const auto d=error([&]{a.get();},"E_CANCELLED");
  REQUIRE(d.at("usage").at("known").flag());REQUIRE(d.at("usage").at("external_requests").num()==0);REQUIRE(!d.has("shared_read"));REQUIRE(f.worker.count()==1);
}
void delivery_cancel_after_commit(){
  Fixture f;const auto id=prepare(f);Gate gate;f.worker.release_all();WebReadTestAccess::window_gate(f.web,[&](const std::string&){gate.block();});
  Async a(f.worker,[&]{return f.web.read("alice",args(id));});a.cleanup=[&]{gate.release();};
  REQUIRE(gate.reached());REQUIRE(f.store.cached("alice",origin).has_value());
  const auto c=f.web.cancel_read("alice",identity(id));REQUIRE(!c.at("physical_cancel_requested").flag());gate.release();
  known(error([&]{a.get();},"E_CANCELLED"),true,1);REQUIRE(f.worker.cancelled()==0);
}
void uncertain_rpc_cooperation(){
  Fixture f;const auto id=prepare(f);f.worker.response=[](const Mock::Call&)->Json{throw Error("E_WORKER_TIMEOUT","reply lost",504);};f.worker.release_all();
  const auto d=error([&]{f.web.read("alice",args(id));},"E_WORKER_TIMEOUT");REQUIRE(!d.at("usage").at("known").flag());
  REQUIRE(d.at("usage").at("external_requests").is_null());REQUIRE(f.worker.count()==1);REQUIRE(f.worker.cancelled()==1);
}
void namespace_and_legacy_contract(){
  Fixture f;const auto id=prepare(f);Async legacy(f.worker,[&]{return f.web.read("alice",args());});REQUIRE(f.worker.reached(1));
  Async controlled(f.worker,[&]{auto a=args(id);a.set("timeout_ms",std::int64_t{60000});return f.web.read("alice",a);});REQUIRE(f.worker.reached(2));
  f.web.cancel_read("alice",identity(id));REQUIRE(f.worker.cancelled()==1);f.worker.release_all();known(legacy.get(),true,1);error([&]{controlled.get();},"E_CANCELLED");
  REQUIRE(f.worker.calls[0].method=="fetch.extract");REQUIRE(!Json::parse(f.worker.calls[0].params).has("control_id"));
  const auto p=Json::parse(f.worker.calls[1].params);REQUIRE(p.has("control_id"));REQUIRE(!p.has("attempt_id"));REQUIRE(!p.has("max_requests"));REQUIRE(p.at("timeout_ms").num()==60000);
}
void bounded_handles(){
  Fixture f;std::vector<std::string> ids;for(int i=0;i<1024;++i)ids.push_back(prepare(f));
  error([&]{prepare(f);},"E_READ_CAPACITY");f.web.cancel_read("alice",identity(ids[0]));error([&]{prepare(f);},"E_READ_CAPACITY");
  WebReadTestAccess::expire(f.web,ids[0]);REQUIRE(prepare(f)!=ids[0]);REQUIRE(f.worker.count()==0);
}
}
int main(){
  const std::vector<std::pair<const char*,std::function<void()>>> cases={
    {"pre_cancel_and_ownership",pre_cancel_and_ownership},{"concurrent_one_use",concurrent_one_use},
    {"payer_cancel_late_join",payer_cancel_late_join},{"subscriber_early_cancel",subscriber_early_cancel},
    {"all_cancel_no_orphan_commit",all_cancel_no_orphan_commit},{"cancelled_running_does_not_expire",cancelled_running_does_not_expire},
    {"expiry_reuse_and_terminal_errors",expiry_reuse_and_terminal_errors},{"cache_profile_cancel",cache_profile_cancel},
    {"delivery_cancel_after_commit",delivery_cancel_after_commit},{"uncertain_rpc_cooperation",uncertain_rpc_cooperation},
    {"namespace_and_legacy_contract",namespace_and_legacy_contract},{"bounded_handles",bounded_handles}};
  int failed=0;for(const auto& [name,run]:cases){std::cout<<"RUN "<<name<<std::endl;try{run();std::cout<<"PASS "<<name<<std::endl;}catch(const std::exception& e){++failed;std::cout<<"FAIL "<<name<<": "<<e.what()<<std::endl;}}
  std::cout<<"SUMMARY cases="<<cases.size()<<" failed="<<failed<<" checks="<<checks<<std::endl;return failed?1:0;
}
