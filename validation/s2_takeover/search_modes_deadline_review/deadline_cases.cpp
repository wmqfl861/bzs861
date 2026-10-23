// Corrected independent review: public request minima do not apply to the
// positive remainder of an already admitted request. The prior contrary test
// remains in search_modes_review/reviewer_cases.cpp as historical evidence.
#define main mode_author_test_main
#include "search_modes.cpp"
#undef main

namespace {
TEST(corrected_public_request_minimum_and_internal_argument_boundary) {
  Temp t;Store store(t.path/"db");Worker worker;WebService web(store,worker,SourceRegistry(manifest(1)));
  for(const auto* field:{"timeout_ms","deadline_ms"})for(const std::int64_t value:{1,500,999}) {
    auto direct=input("fast");direct.set(field,value);rejects([&]{web.search("alice",direct);},"E_SCHEMA");
    direct.set("idempotency_key","invalid-public-budget");rejects([&]{web.submit_search("alice",direct);},"E_SCHEMA");
  }
  for(const auto* field:{"remaining_budget_ms","internal_deadline","remaining_ms"}) {
    auto direct=input("fast");direct.set(field,std::int64_t{500});rejects([&]{web.search("alice",direct);},"E_SCHEMA");
    direct.set("idempotency_key","invalid-internal-field");rejects([&]{web.submit_search("alice",direct);},"E_SCHEMA");
  }
  CHECK(worker.requests.empty());
}
TEST(corrected_500ms_remainder_dispatches_without_enlarging_budget) {
  for(const auto* mode:{"","fast"}) {
    Temp t;std::int64_t now=1800000000000;const auto admitted_at=now;Store store(t.path/"db",[&]{return now;});Worker worker;
    WebService web(store,worker,SourceRegistry(manifest(1)));auto request=input(mode,true);
    request.set("timeout_ms",std::int64_t{1000});request.set("deadline_ms",std::int64_t{1000});
    const auto id=web.submit_search("alice",request).at("session_id").str();now+=500;
    const auto lease=web.lease_search();CHECK(lease.has_value());CHECK(lease->deadline_ms==admitted_at+1000);web.execute_search(*lease);
    CHECK(worker.requests.size()==1);const auto sent=worker.requests.at(0).at("timeout_ms").num();CHECK(sent>0&&sent<=500);
    CHECK(worker.timeouts.at(0)>0&&worker.timeouts.at(0)<=500);
    const auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="completed");
    CHECK(!state.at("result_set_version").str().empty());CHECK(state.at("deadline_ms").num()==admitted_at+1000);
    CHECK(state.at("effective_budget").at("timeout_ms").num()==1000);CHECK(state.at("effective_budget").at("deadline_ms").num()==1000);
  }
}
TEST(corrected_expired_lease_never_dispatches) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;
  WebService web(store,worker,SourceRegistry(manifest(2)));auto request=input("fast",true);request.set("deadline_ms",std::int64_t{1000});
  const auto id=web.submit_search("alice",request).at("session_id").str();const auto lease=web.lease_search();CHECK(lease.has_value());
  now+=1000;web.execute_search(*lease);CHECK(worker.requests.empty());
  const auto state=store.session_get("alice",id);CHECK(state.at("state").str()=="failed");
  CHECK(state.at("error_code").str()=="E_DEADLINE");CHECK(state.at("result_set_version").str().empty());CHECK(!web.lease_search());
}
TEST(corrected_late_response_never_publishes_or_dispatches_tail) {
  Temp t;std::int64_t now=1800000000000;Store store(t.path/"db",[&]{return now;});Worker worker;
  WebService web(store,worker,SourceRegistry(manifest(2)));auto request=input("fast",true);request.set("deadline_ms",std::int64_t{1000});
  const auto id=web.submit_search("alice",request).at("session_id").str();now+=500;const auto lease=web.lease_search();CHECK(lease.has_value());
  worker.during=[&] { std::this_thread::sleep_for(std::chrono::milliseconds(550));now+=550; };
  web.execute_search(*lease);CHECK(worker.requests.size()==1);CHECK(worker.requests.at(0).at("timeout_ms").num()<=500);
  CHECK(worker.timeouts.at(0)<=500);const auto state=store.session_get("alice",id);
  CHECK(state.at("state").str()=="failed");CHECK(state.at("error_code").str()=="E_DEADLINE");
  CHECK(state.at("result_set_version").str().empty());CHECK(state.at("dispatched_source_attempts").num()==1);
  CHECK(state.at("unknown_source_attempts").num()==1);CHECK(!web.lease_search());CHECK(worker.requests.size()==1);
}
}

int main() {
  std::size_t failed=0,executed=0;
  for(const auto& [name,test]:tests()) {
    if(!name.starts_with("corrected_"))continue;
    ++executed;try { test();std::cout<<"PASS "<<name<<'\n'; }
    catch(const std::exception& error) { ++failed;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n'; }
  }
  std::cout<<executed<<" corrected deadline review cases, "<<assertions<<" assertions, "<<failed<<" failures\n";
  return failed||executed!=4?1:0;
}
