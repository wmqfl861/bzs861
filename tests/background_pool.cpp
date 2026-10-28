#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <atomic>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
using namespace bzs;
using namespace std::chrono_literals;
namespace {
#define CHECK(x) do{if(!(x))throw std::runtime_error(#x);}while(false)
struct Temp {std::filesystem::path p=std::filesystem::temp_directory_path()/random_id();Temp(){std::filesystem::create_directories(p);}~Temp(){std::error_code ec;std::filesystem::remove_all(p,ec);}};
struct Worker:WorkerPort {
  std::atomic<int> active=0,peak=0,calls=0;
  std::mutex mutex;std::set<std::string> attempts;
  std::atomic<std::int64_t> clock{1700000000000LL};
  int delay=2000;
  Json call(const std::string& method,const Json& p,int) override {
    const int n=++active;int old=peak.load();while(n>old&&!peak.compare_exchange_weak(old,n)){}
    struct Guard{std::atomic<int>& n;~Guard(){--n;}}g{active};
    ++calls;{std::lock_guard lock(mutex);CHECK(attempts.insert(p.at("attempt_id").str()).second);}
    std::this_thread::sleep_for(std::chrono::milliseconds(delay));
    if(method=="map.fetch") {
      auto r=Json::object();r.set("url",p.at("url"));r.set("final_url",p.at("url"));r.set("http_status",std::int64_t{200});r.set("status","ok");
      r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
      if(p.at("kind").str()=="robots") {
        auto robots=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
        robots.set("origin",p.at("robots_origin"));robots.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));
        robots.set("fetched_at_ms",clock.load());robots.set("expires_at_ms",clock.load()+3600000);r.set("robots",robots);
      }else {
        auto d=Json::object();d.set("document_url",p.at("url"));d.set("document_type","html");d.set("parse_status","ok");d.set("candidates",Json::array());d.set("truncated",false);d.set("diagnostics",Json::object());r.set("discovery",d);
        auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",p.at("robots").at("origin"));decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","robots_no_matching_rule");r.set("robots_decision",decision);
      }
      return r;
    }
    CHECK(method=="fetch.extract");
    auto r=Json::parse(R"({"status":"ok","html":"parallel fixture body","text":"parallel fixture body","title":"parallel","content_type":"text/plain","extractor":"pool-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":100}})");r.set("final_url",p.at("url"));return r;
  }
};
template<class F> void until(F f) {auto end=std::chrono::steady_clock::now()+15s;while(!f()){CHECK(std::chrono::steady_clock::now()<end);std::this_thread::sleep_for(5ms);}}
void reads(unsigned width) {
  Temp t;Worker w;Store s(t.p/"db");FixtureCatalog fixtures(BZS_FIXTURE_DIR);WebService web(s,w,{});
  std::vector<std::string> ids;
  for(int i=0;i<4;++i)ids.push_back(s.read_many_submit("alice",{"https://p"+std::to_string(i)+".example/page"},"pool-"+std::to_string(i),{}).at("job_id").str());
  const auto started=std::chrono::steady_clock::now();auto pool=start_worker(s,fixtures,&web,{width,1,1});
  until([&]{for(const auto& id:ids)if(s.job_get("alice",id).at("state").str()!="succeeded")return false;return true;});
  pool.request_stop();pool.join();CHECK(!pool.joinable());CHECK(w.calls==4);CHECK(w.peak==static_cast<int>(width));
  for(const auto& id:ids)CHECK(s.job_get("alice",id).at("usage").at("external_requests").num()==1);
  std::cout<<"read_pool width="<<width<<" calls="<<w.calls<<" peak="<<w.peak<<" seconds="<<std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count()<<'\n';
}
void map_pacing() {
  // A controlled clock measures the durable admission interval, not RPC
  // scheduling jitter. Worker connection pacing is also covered by python_worker.
  Temp t;Worker w;w.delay=10;Store s(t.p/"db",[&]{return w.clock.load();});FixtureCatalog fixtures(BZS_FIXTURE_DIR);
  auto m=Json::parse(R"({"version":1,"sites":[{"id":"pool-site","enabled":true,"origin":"https://pool.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://pool.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-pool-test"},"limits":{"max_concurrency":1,"min_interval_ms":500,"request_quota":1000,"quota_window_seconds":86400}}]})");
  SiteRegistry sites(m);WebService web(s,w,{},sites);std::vector<std::string> ids;
  for(int i=0;i<2;++i){auto p=Json::parse(R"({"site_id":"pool-site","seeds":["https://pool.example/docs/page"],"idempotency_key":""})");p.set("idempotency_key","map-"+std::to_string(i));ids.push_back(web.submit_map("alice",p).at("job_id").str());}
  auto pool=start_worker(s,fixtures,&web,{1,4,1});until([&]{return w.calls==1&&w.active==0;});
  std::this_thread::sleep_for(80ms);CHECK(w.calls==1);
  w.clock+=499;std::this_thread::sleep_for(80ms);CHECK(w.calls==1);
  w.clock+=1;until([&]{return w.calls==2&&w.active==0;});
  std::this_thread::sleep_for(80ms);CHECK(w.calls==2);
  w.clock+=500;until([&]{return w.calls==3&&w.active==0;});
  until([&]{for(const auto& id:ids)if(s.job_get("alice",id).at("state").str()!="succeeded")return false;return true;});
  pool.request_stop();pool.join();CHECK(w.peak==1);std::cout<<"map_pacing interval=500ms physical_calls=3 max_active=1\n";
}
void options() {
  Temp t;Store s(t.p/"db");FixtureCatalog f(BZS_FIXTURE_DIR);
  for(const auto o:{BackgroundOptions{0,1,1},BackgroundOptions{1,9,1},BackgroundOptions{1,1,0}}){bool rejected=false;try{auto p=start_worker(s,f,nullptr,o);}catch(const Error&e){rejected=e.code=="E_ARGUMENT";}CHECK(rejected);}
  auto p=start_worker(s,f);CHECK(p.joinable());BackgroundWorker moved; moved=std::move(p);moved.request_stop();moved.join();CHECK(!moved.joinable());
}
void fixture_lane_shutdown_never_abandons_claimed_lease() {
  // Invariant: after request_stop+join, a claimed fixture job is always in a
  // terminal state. It must not sit in 'running' until its 10s lease expires:
  // the lane fails a claimed lease with E_SHUTDOWN when stop is observed,
  // exactly like the read/map/search lanes.
  for(int i=0;i<3;++i) {
    Temp t;Store s(t.p/"db");FixtureCatalog fixtures(BZS_FIXTURE_DIR);
    const auto id=s.job_create("alice","plain","shutdown-race-"+std::to_string(i)).at("job_id").str();
    auto pool=start_worker(s,fixtures);
    until([&]{return s.job_get("alice",id).at("state").str()!="queued";});
    pool.request_stop();pool.join();CHECK(!pool.joinable());
    const auto state=s.job_get("alice",id).at("state").str();
    CHECK(state=="succeeded"||state=="failed");
  }
  std::cout<<"fixture_lane shutdown leaves no running lease\n";
}
}
int main(){try{reads(1);reads(4);map_pacing();options();fixture_lane_shutdown_never_abandons_claimed_lease();std::cout<<"5 pool checks passed\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
