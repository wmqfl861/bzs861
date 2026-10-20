#include "bzs/error.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <functional>
#include <iostream>
#include <set>
#include <string>
#include <thread>
#include <utility>
#include <vector>

using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests(){static std::vector<Test> value;return value;}
struct Add{Add(const char* name,std::function<void()> run){tests().emplace_back(name,std::move(run));}};
std::size_t assertions=0;
#define TEST(name) void name();Add register_##name(#name,name);void name()
#define CHECK(value) do{++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__));}while(false)
struct Temp{
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-fairness-"+random_id());
  Temp(){std::filesystem::create_directories(path);}
  ~Temp(){std::error_code error;std::filesystem::remove_all(path,error);}
};
constexpr const char* origin="https://fair.example";
Json manifest(){return Json::parse(R"({"version":1,"sites":[{"id":"fair-site","enabled":true,"origin":"https://fair.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://fair.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fairness-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");}
std::vector<std::string> urls(const std::string& stem,std::size_t count){std::vector<std::string> out;for(std::size_t i=0;i<count;++i)out.push_back(std::string(origin)+"/docs/"+stem+"/"+std::to_string(i));return out;}
Json map_args(const std::vector<std::string>& input,const std::string& key,const std::string& site="fair-site"){
  auto out=Json::object(),seeds=Json::array();for(const auto& url:input)seeds.push(Json::string(url));
  out.set("site_id",site);out.set("seeds",seeds);out.set("idempotency_key",key);out.set("max_depth",std::int64_t{8});return out;
}
Json child(const Json& request,const std::string& url){
  auto c=Json::object();c.set("url",url);c.set("parent",request.at("url"));c.set("source","html");c.set("document",Json());
  c.set("depth",request.at("depth").num()+1);c.set("path_depth",std::int64_t{4});return c;
}
struct Worker:WorkerPort{
  std::int64_t now=1700000000000;
  std::vector<std::pair<std::string,std::string>> calls;
  std::function<Json(const Json&,Json)> alter_map;
  Json read(const Json& p)const{
    auto r=Json::parse(R"({"status":"ok","html":"fairneedle readable body","text":"fairneedle readable body","title":"fairness fixture","content_type":"text/plain","extractor":"fairness-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":100},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"fairneedle readable body","snippet_title":"fairness fixture","directives":[],"diagnostics":[]}})");
    r.set("final_url",p.at("url"));return r;
  }
  Json map(const Json& p)const{
    auto r=Json::object();r.set("status","ok");r.set("url",p.at("url"));r.set("final_url",p.at("url"));r.set("http_status",std::int64_t{200});
    r.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
    if(p.at("kind").str()=="robots"){
      auto record=Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
      record.set("origin",p.at("robots_origin"));record.set("body_sha256",sha256("User-agent: *\nAllow: /\n"));record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);r.set("robots",record);
    }else{
      auto d=Json::object();d.set("document_url",p.at("url"));d.set("document_type","html");d.set("parse_status","ok");d.set("candidates",Json::array());d.set("truncated",false);d.set("diagnostics",Json::object());r.set("discovery",d);
      auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",p.at("robots").at("origin"));decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","robots_no_matching_rule");r.set("robots_decision",decision);
      if(p.at("kind").str()=="page"){auto page=read(p);r.set("page",page);r.set("publication",page.at("publication"));}
    }
    return r;
  }
  Json call(const std::string& method,const Json& p,int)override{
    if(method=="fetch.cancel")return Json::object();
    calls.emplace_back(method,p.at("url").str());
    if(method=="fetch.extract")return read(p);
    if(method=="map.fetch"){auto r=map(p);return alter_map?alter_map(p,r):r;}
    throw std::runtime_error("unexpected worker method");
  }
};
std::string submit(Store& s,const std::string& key,std::size_t count,const std::string& owner="alice",ReadManyOptions options={}){
  return s.read_many_submit(owner,urls(key,count),key,options).at("job_id").str();
}
ReadLease dispatch(Store& s,WebService& web){auto lease=s.lease_next_read();CHECK(lease.has_value());web.execute_read(*lease);return *lease;}
MapLease dispatch_map(WebService& web){auto lease=web.lease_map();CHECK(lease.has_value());web.execute_map(*lease);return *lease;}

TEST(read_batches_follow_submission_order_and_rotate_with_identical_timestamps){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});
  std::vector<std::string> submitted;for(int i=0;i<8;++i)submitted.push_back(submit(s,"equal-clock-"+std::to_string(i),3));
  for(int round=0;round<3;++round)for(const auto& id:submitted){auto lease=dispatch(s,web);CHECK(lease.job_id==id);CHECK(lease.input_index==round);}
  CHECK(!s.lease_next_read());CHECK(worker.calls.size()==24);
  for(const auto& id:submitted){auto job=s.job_get("alice",id);CHECK(job.at("state").str()=="succeeded");CHECK(job.at("usage").at("external_requests").num()==3);}
}
TEST(late_short_batch_gets_a_turn_before_an_old_twenty_item_batch_drains){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});
  auto long_id=submit(s,"long",20);CHECK(dispatch(s,web).job_id==long_id);++worker.now;auto short_id=submit(s,"short",1,"bob");
  bool served=false;for(int opportunity=0;opportunity<2;++opportunity){if(dispatch(s,web).job_id==short_id){served=true;break;}}
  CHECK(served);CHECK(s.job_get("alice",long_id).at("pending_items").num()>0);CHECK(s.job_get("bob",short_id).at("succeeded_items").num()==1);
}
TEST(read_queue_position_survives_restart_and_owner_isolation){
  Temp t;Worker worker;std::string first,second;
  {Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});first=submit(s,"restart-first",3);++worker.now;second=submit(s,"restart-second",2,"bob");CHECK(dispatch(s,web).job_id==first);}
  {Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});CHECK(dispatch(s,web).job_id==second);CHECK(dispatch(s,web).job_id==first);CHECK(dispatch(s,web).job_id==second);
    bool denied=false;try{(void)s.job_get("alice",second);}catch(const Error& e){denied=e.code=="E_NOT_FOUND";}CHECK(denied);CHECK(s.job_get("bob",second).at("state").str()=="succeeded");}
}
TEST(restarted_unknown_attempt_keeps_its_grant_and_does_not_jump_a_waiting_batch){
  Temp t;Worker worker;std::string first,second;std::int64_t request_grant=0,byte_grant=0;
  {Store s(t.path/"db",[&]{return worker.now;});first=submit(s,"unknown-first",3);++worker.now;second=submit(s,"unknown-second",1);auto lease=s.lease_next_read();CHECK(lease.has_value());CHECK(lease->job_id==first);request_grant=lease->request_grant;byte_grant=lease->byte_grant;}
  worker.now+=30000;
  {Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});CHECK(dispatch(s,web).job_id==second);auto job=s.job_get("alice",first);CHECK(job.at("usage").at("unknown_requests_charged").num()==request_grant);CHECK(job.at("usage").at("unknown_bytes_charged").num()==byte_grant);CHECK(job.at("pending_items").num()==2);CHECK(worker.calls.size()==1);}
}
TEST(two_store_connections_reserve_distinct_jobs_and_rounds){
  Temp t;Worker worker;Store first(t.path/"db",[&]{return worker.now;}),second(t.path/"db",[&]{return worker.now;});WebService left(first,worker,{}),right(second,worker,{});
  auto a=submit(first,"connection-a",2),b=submit(second,"connection-b",2);auto one=first.lease_next_read(),two=second.lease_next_read();CHECK(one.has_value());CHECK(two.has_value());CHECK(one->job_id==a);CHECK(two->job_id==b);CHECK(one->attempt_id!=two->attempt_id);
  right.execute_read(*two);left.execute_read(*one);CHECK(dispatch(second,right).job_id==a);CHECK(dispatch(first,left).job_id==b);CHECK(worker.calls.size()==4);
}
TEST(paused_cancelled_and_expired_batches_do_not_consume_other_jobs_turns){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{});
  ReadManyOptions brief;brief.timeout_ms=1000;auto expired=submit(s,"expired",1,"alice",brief);auto paused=submit(s,"paused",2);s.job_control("alice",paused,"pause");auto cancelled=submit(s,"cancelled",1);s.job_control("alice",cancelled,"cancel");auto active=submit(s,"active",2);worker.now+=1001;
  CHECK(dispatch(s,web).job_id==active);CHECK(s.job_get("alice",expired).at("usage").at("external_requests").num()==0);CHECK(s.job_get("alice",cancelled).at("dispatches").num()==0);s.job_control("alice",paused,"resume");CHECK(dispatch(s,web).job_id==paused);CHECK(dispatch(s,web).job_id==active);CHECK(s.job_get("alice",paused).at("pending_items").num()==1);
}
TEST(continuous_new_map_jobs_do_not_starve_an_existing_frontier){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(manifest()));
  auto original=web.submit_map("alice",map_args(urls("existing",3),"existing")).at("job_id").str();CHECK(dispatch_map(web).job_id==original);CHECK(dispatch_map(web).job_id==original);
  std::size_t original_progress=0;
  for(int i=0;i<6;++i){web.submit_map("alice",map_args(urls("arrival-"+std::to_string(i),1),"arrival-"+std::to_string(i)));if(dispatch_map(web).job_id==original)++original_progress;}
  CHECK(original_progress==2);CHECK(s.job_get("alice",original).at("state").str()=="succeeded");
}
TEST(new_map_jobs_have_a_strict_submission_order_at_the_same_clock){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(manifest()));
  auto warm=web.submit_map("alice",map_args(urls("warm",1),"warm")).at("job_id").str();CHECK(dispatch_map(web).job_id==warm);CHECK(dispatch_map(web).job_id==warm);
  std::vector<std::string> ids;for(int i=0;i<8;++i)ids.push_back(web.submit_map("alice",map_args(urls("same-map-"+std::to_string(i),1),"same-map-"+std::to_string(i))).at("job_id").str());
  for(const auto& id:ids)CHECK(dispatch_map(web).job_id==id);
  CHECK(!web.lease_map());
}
TEST(cold_or_cooled_origin_does_not_stop_another_origin_or_read_batch){
  Temp t;Worker worker;auto config=manifest();config.at("sites").at(0).at("limits").set("min_interval_ms",std::int64_t{60000});
  auto other=Json::parse(config.at("sites").at(0).dump());other.set("id","other-site");other.set("origin","https://other-fair.example");other.at("review").set("terms_url","https://other-fair.example/terms");other.at("limits").set("min_interval_ms",std::int64_t{0});config.at("sites").push(other);
  Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(config));auto blocked=web.submit_map("alice",map_args(urls("cold",1),"cold")).at("job_id").str();CHECK(dispatch_map(web).job_id==blocked);CHECK(!web.lease_map());
  auto other_id=web.submit_map("alice",map_args({"https://other-fair.example/docs/a"},"other","other-site")).at("job_id").str();auto read_id=submit(s,"while-cooled",1);
  CHECK(dispatch_map(web).job_id==other_id);CHECK(dispatch(s,web).job_id==read_id);CHECK(dispatch_map(web).job_id==other_id);CHECK(s.job_get("alice",blocked).at("state").str()=="queued");CHECK(s.job_get("alice",blocked).at("usage").at("external_requests").num()==1);
}
TEST(worker_loop_gives_batch_and_discovery_categories_progress){
  Temp t;Worker worker;Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(manifest()));FixtureCatalog fixtures(BZS_FIXTURE_DIR);
  auto read_id=submit(s,"loop-long",20);auto map_id=web.submit_map("alice",map_args(urls("loop-map",1),"loop-map")).at("job_id").str();
  auto thread=start_worker(s,fixtures,&web);bool done=false;
  for(int poll=0;poll<1000;++poll){if(s.job_get("alice",map_id).at("state").str()=="succeeded"){done=true;break;}std::this_thread::sleep_for(std::chrono::milliseconds(5));}
  thread.request_stop();thread.join();CHECK(done);CHECK(s.job_get("alice",read_id).at("succeeded_items").num()>0);
  CHECK(worker.calls.size()>=4);CHECK(worker.calls[0].first=="fetch.extract");CHECK(worker.calls[1].first=="map.fetch");CHECK(worker.calls[2].first=="fetch.extract");CHECK(worker.calls[3].first=="map.fetch");
}

TEST(calendar_stop_and_finite_date_query_negative_controls_survive_interleaved_restart){
  Temp t;Worker worker;std::string calendar,finite;
  std::vector<std::string> normal;
  for(int i=1;i<=18;++i)normal.push_back(std::string(origin)+"/docs/articles/2026-10-"+(i<10?"0":"")+std::to_string(i)+"/topic-"+std::to_string(i));
  for(const auto* tail:{"list?tag=cpp&tag=python&sort=asc","list?tag=python&tag=cpp&sort=asc","list?sort=asc&tag=cpp&tag=python","version/1.2.3","version/2.0.1","item/123456789"})normal.push_back(std::string(origin)+"/docs/"+tail);
  worker.alter_map=[&](const Json& p,Json r){if(p.at("kind").str()!="page")return r;auto candidates=Json::array();auto url=p.at("url").str();
    if(url.find("/calendar/")!=std::string::npos){auto day=std::stoi(url.substr(url.size()-2));if(day<20)candidates.push(child(p,std::string(origin)+"/docs/calendar/2026-10-"+(day+1<10?"0":"")+std::to_string(day+1)));}
    else if(url.ends_with("/normal"))for(const auto& candidate_url:normal)candidates.push(child(p,candidate_url));
    r.at("discovery").set("candidates",candidates);return r;};
  {Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(manifest()));calendar=web.submit_crawl("alice",map_args({std::string(origin)+"/docs/calendar/2026-10-01"},"calendar")).at("job_id").str();finite=web.submit_crawl("alice",map_args({std::string(origin)+"/docs/normal"},"normal")).at("job_id").str();for(int i=0;i<4;++i)dispatch_map(web);}
  {Store s(t.path/"db",[&]{return worker.now;});WebService web(s,worker,{},SiteRegistry(manifest()));std::size_t turns=0;while(auto lease=web.lease_map()){CHECK(++turns<=60);web.execute_map(*lease);}
    auto trapped=s.job_get("alice",calendar),allowed=s.job_get("alice",finite);CHECK(trapped.at("partial").flag());CHECK(trapped.at("stop_reason").str()=="E_CRAWL_TRAP_DATE_SEQUENCE");CHECK(allowed.at("state").str()=="succeeded");CHECK(!allowed.at("partial").flag());CHECK(allowed.at("used_pages").num()==static_cast<std::int64_t>(normal.size()+1));
    auto edges=s.crawl_edges("alice",calendar);bool reason=false;for(std::size_t i=0;i<edges.at("items").size();++i){auto edge=edges.at("items").at(i);if(edge.at("reason").is_string()&&edge.at("reason").str()=="E_CRAWL_TRAP_DATE_SEQUENCE"){reason=true;CHECK(!edge.at("followed").flag());CHECK(edge.has("trap"));}}CHECK(reason);
    std::set<std::string> fetched;for(const auto& [method,url]:worker.calls)if(method=="map.fetch")fetched.insert(url);for(const auto& url:normal)CHECK(fetched.contains(url));CHECK(!fetched.contains(std::string(origin)+"/docs/calendar/2026-10-08"));}
}
}
int main(int argc,char** argv){std::size_t failed=0,executed=0;for(const auto& [name,test]:tests()){if(argc>1&&name!=argv[1])continue;++executed;try{test();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& error){++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n';}}std::cout<<executed<<" fairness tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0;}
