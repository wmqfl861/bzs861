// Independent Store-level deadline/event probe. No Worker, network, sleeps,
// direct SQL writes, wall-clock waiting, or changes to the original E2E suite.
#include "bzs/error.hpp"
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace bzs;
namespace {
std::int64_t assertions=0;
void expect(bool value,const std::string& message) {
  ++assertions;
  if(!value)throw std::runtime_error(message);
}
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-deadline-review-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code ignored;std::filesystem::remove_all(path,ignored); }
};
Json manifest() {
  auto root=Json::object(),sources=Json::array();root.set("version",std::int64_t{1});
  for(int i=0;i<2;++i) {
    const auto id="source"+std::to_string(i);
    auto source=Json::parse(R"({"provider":"synthetic","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":10000,"params":{"adapter":"offline_fixture","base_url":"https://search.example/search"}})");
    auto contract=Json::parse(R"({"provider_id":"synthetic","capability":"web","endpoint":"https://search.example/search","adapter_id":"offline_fixture","enabled":true,"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://search.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"independent-synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":10000,"quota_window_seconds":86400}})");
    contract.set("id",id);contract.set("index_family",id);contract.set("logical_service_id",id);contract.at("limits").set("shared_budget_key",id);
    source.set("id",id);source.set("index_family",id);source.set("contract",contract);sources.push(source);
  }
  root.set("sources",sources);return root;
}
Json request(const std::string& key,std::int64_t deadline=1000) {
  auto input=Json::object();input.set("query","independent fixture");input.set("idempotency_key",key);
  input.set("deadline_ms",deadline);input.set("timeout_ms",std::int64_t{1000});return input;
}
Json result(const std::string& source) {
  auto value=Json::parse(R"({"results":[{"url":"https://result.example/frozen","title":"frozen title","snippet":"frozen marker","sources":[{"source_id":"source0","rank":1}]}],"partial":false})");
  value.at("results").at(0).at("sources").at(0).set("source_id",source);return value;
}
Json rows(Store& store,const char* sql,const std::string& id) {
  sqlite3_stmt* raw=nullptr;
  if(sqlite3_prepare_v2(store.raw_handle(),sql,-1,&raw,nullptr)!=SQLITE_OK)throw std::runtime_error("read-only diagnostic prepare failed");
  struct Finalize { sqlite3_stmt* p;~Finalize(){sqlite3_finalize(p);} } finalizer{raw};
  if(sqlite3_bind_text(raw,1,id.data(),static_cast<int>(id.size()),SQLITE_TRANSIENT)!=SQLITE_OK)throw std::runtime_error("read-only diagnostic bind failed");
  auto output=Json::array();int code=0;
  while((code=sqlite3_step(raw))==SQLITE_ROW) {
    auto row=Json::array();
    for(int column=0;column<sqlite3_column_count(raw);++column) {
      if(sqlite3_column_type(raw,column)==SQLITE_INTEGER)row.push(Json::integer(sqlite3_column_int64(raw,column)));
      else {const auto* text=sqlite3_column_text(raw,column);row.push(text?Json::string(reinterpret_cast<const char*>(text)):Json());}
    }
    output.push(row);
  }
  if(code!=SQLITE_DONE)throw std::runtime_error("read-only diagnostic step failed");
  return output;
}
Json snapshot(Store& store,const std::string& id) {
  auto output=Json::object();
  output.set("stream",rows(store,"SELECT state,epoch,lease_until,deadline,used_requests,unknown_requests,latest_version,error_code FROM search_streams WHERE id=?",id));
  output.set("receipt",rows(store,"SELECT state,used_requests,unknown_requests FROM search_stream_receipts WHERE id=?",id));
  output.set("sources",rows(store,"SELECT source_id,state,payload_json FROM search_stream_sources WHERE session_id=? ORDER BY position",id));
  output.set("events",rows(store,"SELECT event_id,kind,version_id FROM search_event_log WHERE session_id=? ORDER BY event_id",id));
  return output;
}
void event_kinds(Store& store,const std::string& id,std::initializer_list<const char*> wanted) {
  const auto actual=rows(store,"SELECT kind FROM search_event_log WHERE session_id=? ORDER BY event_id",id);
  expect(actual.size()==wanted.size(),"unexpected persistent event count");std::size_t i=0;
  for(const auto* kind:wanted)expect(actual.at(i++).at(0).str()==kind,"unexpected persistent event kind");
}
void counts(Store& store,const std::string& id,const std::string& state,std::int64_t used,std::int64_t unknown) {
  const auto actual=rows(store,"SELECT state,used_requests,unknown_requests FROM search_stream_receipts WHERE id=?",id);
  expect(actual.size()==1,"one durable receipt must remain");expect(actual.at(0).at(0).str()==state,"receipt state changed");
  expect(actual.at(0).at(1).num()==used,"charged attempt count changed");expect(actual.at(0).at(2).num()==unknown,"unknown attempt count changed");
}
SearchLease lease(Store& store,const SourceRegistry& registry) {
  const auto value=store.lease_next_search(registry);expect(value.has_value(),"expected a lease");return *value;
}
void direct_deadline_is_terminal_once(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());std::string id;SearchLease first;Json saved;
  {
    Store store(temp.path/"db",[&]{return now;});id=store.search_submit("alice",request("deadline"),registry).at("session_id").str();first=lease(store,registry);
    now=first.deadline_ms-1;store.fail_search(first,"E_DEADLINE");observation.set("at_deadline_minus_one",snapshot(store,id));
    expect(store.session_get("alice",id).at("state").str()=="failed","E_DEADLINE left a still-live wall-clock lease nonterminal");
    expect(store.session_get("alice",id).at("error_code").str()=="E_DEADLINE","deadline error was not retained");
    counts(store,id,"failed",1,1);event_kinds(store,id,{"queued","failed"});
    const auto source=rows(store,"SELECT state FROM search_stream_sources WHERE session_id=? ORDER BY position",id);
    expect(source.at(0).at(0).str()=="unknown"&&source.at(1).at(0).str()=="pending","deadline settlement changed pending tail");
    saved=snapshot(store,id);store.fail_search(first,"E_DEADLINE");store.fail_search(first,"E_WORKER_TIMEOUT");
    expect(!store.complete_search(first,result(first.source_id),registry),"late deadline response published");expect(!store.lease_next_search(registry),"deadline dispatched pending tail");
    expect(snapshot(store,id).canonical()==saved.canonical(),"duplicate callback changed terminal accounting or events");
  }
  now+=2;Store reopened(temp.path/"db",[&]{return now;});reopened.fail_search(first,"E_DEADLINE");
  expect(reopened.session_get("alice",id).at("state").str()=="failed","terminal deadline did not survive reopen");
  expect(snapshot(reopened,id).canonical()==saved.canonical(),"reopen or expiry counted deadline twice");observation.set("after_reopen",snapshot(reopened,id));
}
void last_source_deadline_preserves_frozen_version(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());std::string id,version,hits;SearchLease second;Json saved;
  {
    Store store(temp.path/"db",[&]{return now;});id=store.search_submit("alice",request("prefix"),registry).at("session_id").str();const auto first=lease(store,registry);
    expect(store.complete_search(first,result(first.source_id),registry),"prefix source was not committed");version=store.session_get("alice",id).at("result_set_version").str();
    const auto page=store.session_page("alice",id,version,"",20);expect(page.at("hits").size()==1,"prefix fixture is missing");hits=page.at("hits").canonical();
    second=lease(store,registry);now=second.deadline_ms-1;store.fail_search(second,"E_DEADLINE");observation.set("last_source_terminal_attempt",snapshot(store,id));
    expect(store.session_get("alice",id).at("state").str()=="failed","last-source E_DEADLINE was mislabeled completed");
    expect(store.session_get("alice",id).at("result_set_version").str()==version,"deadline replaced frozen prefix version");
    counts(store,id,"failed",2,1);event_kinds(store,id,{"queued","result_version","failed"});saved=snapshot(store,id);
    store.fail_search(second,"E_DEADLINE");expect(!store.complete_search(second,result(second.source_id),registry),"late final source published");
    expect(snapshot(store,id).canonical()==saved.canonical(),"late callback changed prefix state");
  }
  Store reopened(temp.path/"db",[&]{return now;});expect(reopened.session_page("alice",id,version,"",20).at("hits").canonical()==hits,"reopen changed frozen prefix contents");
  expect(snapshot(reopened,id).canonical()==saved.canonical(),"reopen changed prefix accounting");observation.set("frozen_version_preserved",true);
}
void cancellation_wins_over_late_deadline(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());std::string id;SearchLease first;Json saved;
  {
    Store store(temp.path/"db",[&]{return now;}),other(temp.path/"db",[&]{return now;});id=store.search_submit("alice",request("cancel"),registry).at("session_id").str();first=lease(store,registry);now=first.deadline_ms-1;
    other.session_cancel("alice",id);saved=snapshot(store,id);store.fail_search(first,"E_DEADLINE");store.fail_search(first,"E_DEADLINE");
    expect(!store.complete_search(first,result(first.source_id),registry),"cancelled source published");expect(snapshot(store,id).canonical()==saved.canonical(),"late deadline overwrote cancellation");
    counts(store,id,"cancelled",1,1);event_kinds(store,id,{"queued","cancelled"});expect(!store.lease_next_search(registry),"cancelled tail dispatched");
  }
  Store reopened(temp.path/"db",[&]{return now;});reopened.fail_search(first,"E_DEADLINE");expect(snapshot(reopened,id).canonical()==saved.canonical(),"reopen changed cancellation winner");observation.set("winner",snapshot(reopened,id));
}
void principal_revocation_wins_over_late_deadline(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());std::string id;SearchLease first;Json saved;
  {
    Store store(temp.path/"db",[&]{return now;}),other(temp.path/"db",[&]{return now;});id=store.search_submit("alice",request("revoke"),registry).at("session_id").str();first=lease(store,registry);now=first.deadline_ms-1;
    other.revoke_principal("admin","alice");saved=snapshot(store,id);store.fail_search(first,"E_DEADLINE");
    expect(!store.complete_search(first,result(first.source_id),registry),"revoked source published");expect(snapshot(store,id).canonical()==saved.canonical(),"late deadline overwrote principal revocation");
    counts(store,id,"cancelled",1,1);event_kinds(store,id,{"queued","principal_revoked"});expect(!store.lease_next_search(registry),"revoked tail dispatched");
    bool denied=false;try {store.session_get("alice",id);}catch(const Error& error){denied=error.code=="E_PRINCIPAL_REVOKED";}expect(denied,"revoked owner regained session access");
  }
  Store reopened(temp.path/"db",[&]{return now;});reopened.fail_search(first,"E_DEADLINE");reopened.revoke_principal("admin","alice");
  expect(snapshot(reopened,id).canonical()==saved.canonical(),"reopen or repeated revocation changed winner");observation.set("winner",snapshot(reopened,id));
}
void stale_epoch_cannot_kill_new_source_lease(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());Store store(temp.path/"db",[&]{return now;});
  const auto id=store.search_submit("alice",request("epoch",60000),registry).at("session_id").str();const auto lost=lease(store,registry);
  now+=11001;store.session_get("alice",id);const auto current=lease(store,registry);expect(current.epoch>lost.epoch,"replacement lease did not advance epoch");
  const auto saved=snapshot(store,id);store.fail_search(lost,"E_DEADLINE");expect(!store.complete_search(lost,result(lost.source_id),registry),"stale source result published");
  expect(snapshot(store,id).canonical()==saved.canonical(),"stale deadline killed replacement lease");
  expect(store.complete_search(current,result(current.source_id),registry),"valid replacement source did not finish");counts(store,id,"completed",2,1);event_kinds(store,id,{"queued","source_unknown","completed"});
  observation.set("after_valid_tail",snapshot(store,id));
}
void ordinary_timeout_remains_source_unknown_with_tail(Json& observation) {
  Temp temp;std::int64_t now=1800000000000;const SourceRegistry registry(manifest());Store store(temp.path/"db",[&]{return now;});
  const auto id=store.search_submit("alice",request("ordinary",60000),registry).at("session_id").str();const auto first=lease(store,registry);store.fail_search(first,"E_WORKER_TIMEOUT");
  counts(store,id,"queued",1,1);event_kinds(store,id,{"queued","source_unknown"});const auto second=lease(store,registry);
  expect(second.source_id!=first.source_id,"ordinary failure retried unknown source");expect(store.complete_search(second,result(second.source_id),registry),"ordinary failure blocked valid tail");
  counts(store,id,"completed",2,1);event_kinds(store,id,{"queued","source_unknown","completed"});observation.set("after_valid_tail",snapshot(store,id));
}
}
int main() {
  const std::vector<std::pair<std::string,std::function<void(Json&)>>> cases={
    {"direct_deadline_terminal_once_at_wall_D_minus_1",direct_deadline_is_terminal_once},
    {"last_source_deadline_preserves_frozen_prefix",last_source_deadline_preserves_frozen_version},
    {"cancellation_winner_is_not_overwritten",cancellation_wins_over_late_deadline},
    {"principal_revocation_winner_is_not_overwritten",principal_revocation_wins_over_late_deadline},
    {"stale_epoch_does_not_kill_current_source",stale_epoch_cannot_kill_new_source_lease},
    {"ordinary_timeout_preserves_unknown_and_tail",ordinary_timeout_remains_source_unknown_with_tail}};
  auto output=Json::object(),details=Json::array();std::int64_t failed=0;
  for(const auto& [name,run]:cases) {
    auto item=Json::object(),observation=Json::object();item.set("name",name);const auto before=assertions;
    try {run(observation);item.set("passed",true);}catch(const std::exception& error){++failed;item.set("passed",false);item.set("error",error.what());}
    item.set("assertions",assertions-before);item.set("observed",observation);details.push(item);
  }
  output.set("reviewer","/root/handoff_audit");output.set("scope","Store deadline transaction and prior-winner persistence; not transport timing or physical power loss");
  output.set("cases",details);output.set("passed",static_cast<std::int64_t>(cases.size())-failed);output.set("failed",failed);output.set("assertions",assertions);
  output.set("network_requests",std::int64_t{0});output.set("clock","explicit deterministic Store wall-clock fixture; no sleeps");output.set("direct_sql_mutation_by_probe",false);
  std::cout<<output.dump(true)<<'\n';return failed?1:0;
}
