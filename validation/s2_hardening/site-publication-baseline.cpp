#include "bzs/error.hpp"
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <sqlite3.h>

namespace {
using namespace bzs;
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> value;return value; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(name) void name();Add registered_##name(#name,name);void name()
#define CHECK(value) do { ++assertions;if(!(value))throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); } while(false)
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-site-publication-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code error;std::filesystem::remove_all(path,error); }
};
struct NoHttp:WorkerPort {
  std::size_t requests=0;
  Json call(const std::string&,const Json&,int)override { ++requests;throw std::runtime_error("This scenario must not make a worker request"); }
};
Json manifest() {
  return Json::parse(R"({"version":1,"sites":[{"id":"policy-site","enabled":true,"origin":"https://policy.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"2020-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://policy.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
Json artifact(Store& store,const std::string& owner,const std::string& url,const std::string& final_url="",Json redirects=Json::array()) {
  ParsedDocument parsed;parsed.title="marker private title";parsed.text="marker canonical secret";parsed.content_type="text/plain";parsed.parser_version="site-publication-fixture/1";
  parsed.publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker canonical secret","snippet_title":"marker private title","directives":[],"diagnostics":[]})");
  auto meta=Json::object();meta.set("final_url",final_url.empty()?url:final_url);meta.set("redirects",redirects);
  return store.ingest_web(owner,{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation(owner,url));
}
Json frozen(Store& store,const std::string& owner="alice") { return store.search(owner,parse_query(Json::string("marker"))); }
Json hits(Store& store,const Json& session,const std::string& owner="alice") { return store.page(owner,session.at("session_id").str(),"",100).at("hits"); }
void restarted_tightening(const std::string& mode) {
  Temp temp;NoHttp worker;std::int64_t now=1800000000000;Json old,snapshot;const auto url=std::string("https://policy.example/docs/a");
  {
    Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(manifest()));snapshot=artifact(store,"alice",url);old=frozen(store);CHECK(!hits(store,old).at(0).at("snippet").str().empty());
  }
  auto config=manifest();auto review=config.at("sites").at(0).at("review");
  if(mode=="links_only")review.set("redistribution_policy","links_only");
  else if(mode=="expired")review.set("expires_at","2021-01-01T00:00:00Z");
  else review.set("status","rejected");
  Store store(temp.path/"db",[&]{return now;});WebService web(store,worker,{},SiteRegistry(config));
  const auto current=hits(store,frozen(store)),previous=hits(store,old);
  CHECK(worker.requests==0);CHECK(store.snapshot("alice",snapshot.at("snapshot_id").str()).at("canonical_text").str()=="marker canonical secret");
  if(mode=="links_only") {
    CHECK(current.size()==1);CHECK(previous.size()==1);CHECK(current.at(0).at("snippet").str().empty());CHECK(previous.at(0).at("snippet").str().empty());CHECK(current.at(0).at("title").str()==url);CHECK(previous.at(0).at("title").str()==url);
  } else { CHECK(current.size()==0);CHECK(previous.size()==0); }
}
TEST(restart_links_only_with_zero_http_restricts_stored_and_frozen_publication) { restarted_tightening("links_only"); }
TEST(restart_rejected_review_with_zero_http_withdraws_stored_and_frozen_publication) { restarted_tightening("rejected"); }
TEST(restart_expired_review_with_zero_http_withdraws_stored_and_frozen_publication) { restarted_tightening("expired"); }
}
int main() {
  std::size_t failed=0;
  for(const auto& [name,test]:tests())try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  std::cout<<tests().size()<<" site publication tests, "<<assertions<<" assertions, "<<failed<<" failures\n";return failed?1:0;
}
