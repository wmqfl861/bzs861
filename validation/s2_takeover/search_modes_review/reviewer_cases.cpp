// Independent MODE-01 review cases. Reuse only the author's offline fixture
// helpers; the four review transitions below were selected separately.
// Compile with -I <candidate>/tests and link the candidate libbzs_core.a.
#define main mode_author_test_main
#include "search_modes.cpp"
#undef main

namespace {
TEST(review_persistent_fused_limit_does_not_expand_per_source_or_fanout) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(5)));
  auto request=input("fast",true);request.set("limit",std::int64_t{1});request.set("per_source",std::int64_t{5});
  const auto id=web.submit_search("alice",request).at("session_id").str();drain(web);
  const auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="completed");
  const auto page=store.session_page("alice",id,state.at("result_set_version").str(),"",20);
  CHECK(page.at("hits").size()==1);CHECK(worker.requests.size()==2);
  for(const auto& attempt:worker.requests) { CHECK(attempt.at("limit").num()==5);CHECK(attempt.at("sources").size()==1); }
  const auto budget=page.at("effective_budget");CHECK(budget.at("limit").num()==1);
  CHECK(budget.at("per_source").num()==5);CHECK(budget.at("max_source_attempts").num()==2);
  CHECK(page.at("omitted_sources").size()==3);
}
TEST(review_tighter_override_changes_persistent_idempotency_identity) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  auto original=input("fast",true);const auto id=web.submit_search("alice",original).at("session_id").str();
  CHECK(web.submit_search("alice",original).at("reused").flag());
  for(const auto& [key,value]:std::vector<std::pair<std::string,std::int64_t>>{
      {"max_sources",1},{"limit",1},{"per_source",1},{"timeout_ms",1000},{"deadline_ms",1000}}) {
    auto changed=Json::parse(original.dump());changed.set(key,value);
    rejects([&]{web.submit_search("alice",changed);},"E_IDEMPOTENCY_CONFLICT");
  }
  CHECK(store.session_get("alice",id).at("state").str()=="queued");CHECK(worker.requests.empty());
}
TEST(review_subsecond_persistent_remainder_never_calls_worker) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;
  WebService web(store,worker,SourceRegistry(manifest(1)));auto request=input("fast",true);request.set("deadline_ms",std::int64_t{1000});
  const auto id=web.submit_search("alice",request).at("session_id").str();++now;
  const auto lease=web.lease_search();CHECK(lease.has_value());web.execute_search(*lease);
  CHECK(worker.requests.empty());const auto state=store.session_get("alice",id);
  CHECK(state.at("error_code").str()=="E_DEADLINE");CHECK(state.at("result_set_version").str().empty());
  CHECK(!web.lease_search());
}
TEST(review_politeness_wait_cannot_outlive_tightened_deadline) {
  Temp t;Store store(t.path/"db");Worker worker;auto sources=manifest(1);
  sources.at("sources").at(0).set("min_interval_ms",std::int64_t{2000});
  sources.at("sources").at(0).at("contract").at("limits").set("min_interval_ms",std::int64_t{2000});
  WebService web(store,worker,SourceRegistry(sources));auto request=input("fast");request.set("deadline_ms",std::int64_t{1000});
  web.search("alice",request);CHECK(worker.requests.size()==1);request.set("refresh",true);
  rejects([&]{web.search("alice",request);},"E_DEADLINE");CHECK(worker.requests.size()==1);
}
}

int main() {
  std::size_t failed=0,executed=0;
  for(const auto& [name,test]:tests()) {
    if(!name.starts_with("review_"))continue;
    ++executed;try { test();std::cout<<"PASS "<<name<<'\n'; }
    catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  }
  std::cout<<executed<<" reviewer cases, "<<assertions<<" assertions, "<<failed<<" failures\n";
  return failed||executed!=4?1:0;
}
