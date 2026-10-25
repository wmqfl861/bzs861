#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <vector>
using namespace bzs;
namespace {
using Test=std::pair<std::string,std::function<void()>>;
std::vector<Test>& tests() { static std::vector<Test> values;return values; }
struct Add { Add(const char* name,std::function<void()> fn) { tests().emplace_back(name,std::move(fn)); } };
std::size_t assertions=0;
#define TEST(n) void n();Add reg_##n(#n,n);void n()
#define CHECK(x) do { ++assertions;if(!(x))throw std::runtime_error(std::string("check failed: ")+#x+" line "+std::to_string(__LINE__)); }while(false)
template<class F> void rejects(F f,const std::string& code) { ++assertions;try { f(); }catch(const Error& e) { if(e.code==code)return;throw std::runtime_error("expected "+code+", got "+e.code); }throw std::runtime_error("expected "+code); }
struct Temp {
  std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-revocation-"+random_id());
  Temp() { std::filesystem::create_directories(path); }
  ~Temp() { std::error_code e;std::filesystem::remove_all(path,e); }
};
std::int64_t scalar(sqlite3* db,const char* query) {
  sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,query,-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> stmt(raw,sqlite3_finalize);
  CHECK(sqlite3_step(stmt.get())==SQLITE_ROW);return sqlite3_column_int64(stmt.get(),0);
}
Json request(const std::string& op,Json params=Json::object()) { auto q=Json::object();q.set("operation",op);q.set("params",params);return q; }
Json target(const std::string& owner="alice") { auto p=Json::object();p.set("subject",owner);return p; }
SourceDocument document(const std::string& url="https://read.example/doc") { return {url,"revocationmarker original private content","text/plain","Private"}; }
Json sources() {
  return Json::parse(R"({"version":1,"sources":[{"id":"synthetic","provider":"synthetic","capability":"web","kind":"api","enabled":true,"default":true,"index_family":"synthetic","min_interval_ms":0,"daily_cap":10000,"params":{"adapter":"offline_fixture","base_url":"https://search.example/search"},"contract":{"id":"synthetic","provider_id":"synthetic","capability":"web","endpoint":"https://search.example/search","adapter_id":"offline_fixture","enabled":true,"index_family":"synthetic","logical_service_id":"synthetic","review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://search.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":10000,"quota_window_seconds":86400,"shared_budget_key":"synthetic"}}}]})");
}
Json sites() {
  return Json::parse(R"({"version":1,"sites":[{"id":"site","enabled":true,"origin":"https://read.example","path_prefixes":["/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://read.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved_by":"synthetic-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
}
struct Worker:WorkerPort {
  std::function<void()> during;std::size_t calls=0;
  Json call(const std::string& method,const Json& args,int) override {
    if(method=="fetch.profile")throw Error("E_WORKER","Legacy fixture has no profile proof",502);
    ++calls;if(during)during();
    if(method=="search.multi")return Json::parse(R"({"results":{"synthetic":{"status":"ok","hits":[{"url":"https://read.example/result","title":"Sensitive title","snippet":"revocationmarker secret","rank":1}]}}})");
    CHECK(method=="fetch.extract");auto out=Json::parse(R"({"status":"ok","html":"<main>revocationmarker late page</main>","text":"revocationmarker late page","title":"Late page","content_type":"text/html","extractor":"fixture/1","index_allowed":true,"redirects":[],"links":[],"usage":{"external_requests":1,"content_bytes":48}})");out.set("final_url",args.at("url"));return out;
  }
};
TEST(admin_authorization_and_strict_contract) {
  Temp t;Store s(t.path/"db");FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service api(s,fixtures,t.path/"backup");
  rejects([&]{api.dispatch({"bob",false},request("principal.revoke",target()));},"E_FORBIDDEN");
  auto bad=target();bad.set("token","must-not-be-accepted");rejects([&]{api.dispatch({"admin",true},request("principal.revoke",bad));},"E_SCHEMA");
  rejects([&]{api.dispatch({"admin",true},request("principal.revoke",target("admin")));},"E_FORBIDDEN");
  rejects([&]{api.dispatch({"admin",true},request("principal.revoke",target("bad subject")));},"E_SCHEMA");
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM principal_revocations")==0);
  auto result=api.dispatch({"admin",true},request("principal.revoke",target()));CHECK(result.at("revoked").flag());CHECK(result.at("auth_epoch").num()==1);
  rejects([&]{api.dispatch({"alice",true},request("principal.revoke",target("bob")));},"E_PRINCIPAL_REVOKED");
  CHECK(api.dispatch({"bob",false},request("stats")).at("documents").num()==0);
}
TEST(every_token_for_subject_and_restart_remain_revoked) {
  Temp t;const auto token1=std::string(64,'a'),token2=std::string(64,'b');
  auto config=Json::object(),tokens=Json::array();for(const auto& token:{token1,token2}) { auto entry=target();entry.set("sha256",sha256(token));entry.set("admin",false);tokens.push(entry); }config.set("tokens",tokens);
  { std::ofstream out(t.path/"auth.json");out<<config.dump(); }
  std::filesystem::permissions(t.path/"auth.json",std::filesystem::perms::owner_read|std::filesystem::perms::owner_write);
  Auth auth(t.path/"auth.json");FixtureCatalog fixtures(BZS_FIXTURE_DIR);std::string id;
  { Store s(t.path/"db");Service api(s,fixtures,t.path/"backup");id=api.dispatch({"admin",true},request("principal.revoke",target())).at("revocation_id").str();
    for(const auto& token:{token1,token2})rejects([&]{api.dispatch(auth.authenticate("Bearer "+token),request("capabilities"));},"E_PRINCIPAL_REVOKED"); }
  Store reopened(t.path/"db");Service api(reopened,fixtures,t.path/"backup");auto again=api.dispatch({"admin",true},request("principal.revoke",target()));
  CHECK(again.at("already_revoked").flag());CHECK(again.at("revocation_id").str()==id);CHECK(again.at("auth_epoch").num()==1);
  for(const auto& token:{token1,token2})rejects([&]{api.dispatch(auth.authenticate("Bearer "+token),request("stats"));},"E_PRINCIPAL_REVOKED");
  CHECK(scalar(reopened.raw_handle(),"PRAGMA user_version")==15);
}
TEST(snapshots_evidence_sessions_and_new_work_denied_without_erasure) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");auto snap=s.ingest("alice",document());auto own=s.ingest("bob",document());
  const auto sid=snap.at("snapshot_id").str();const auto proof=s.evidence("alice",sid,0,16).at("evidence_id").str();
  const auto session=s.search("alice",parse_query(Json::string("revocationmarker"))).at("session_id").str();
  auto first=other.revoke_principal("admin","alice");auto second=s.revoke_principal("admin","alice");CHECK(first.at("revocation_id").str()==second.at("revocation_id").str());
  rejects([&]{s.snapshot("alice",sid);},"E_PRINCIPAL_REVOKED");rejects([&]{s.range("alice",sid,0,10);},"E_PRINCIPAL_REVOKED");
  rejects([&]{s.evidence_get("alice",proof);},"E_PRINCIPAL_REVOKED");rejects([&]{s.page("alice",session,"");},"E_PRINCIPAL_REVOKED");
  rejects([&]{s.session_get("alice",session);},"E_PRINCIPAL_REVOKED");rejects([&]{s.publication_fence("alice",document().url);},"E_PRINCIPAL_REVOKED");
  rejects([&]{s.ingest("alice",document());},"E_PRINCIPAL_REVOKED");rejects([&]{s.read_many_submit("alice",{document().url},"new",{});},"E_PRINCIPAL_REVOKED");
  CHECK(s.snapshot("bob",own.at("snapshot_id").str()).at("canonical_text").str()==document().raw);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM snapshots")==2);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM evidence")==1);
}
TEST(queued_paused_fixture_batch_map_crawl_work_is_stopped) {
  Temp t;Store s(t.path/"db");SiteRegistry registry(sites());
  const auto fixture=s.job_create("alice","plain","fixture").at("job_id").str();s.job_control("alice",fixture,"pause");
  s.read_many_submit("alice",{document().url},"batch",{});
  auto params=Json::parse(R"({"site_id":"site","seeds":["https://read.example/index"],"idempotency_key":"map"})");s.map_submit("alice",params,registry);
  params.set("idempotency_key","crawl");s.crawl_submit("alice",params,registry);s.revoke_principal("admin","alice");
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE owner='alice' AND state='cancelled'")==4);
  CHECK(!s.lease_next());CHECK(!s.lease_next_read());CHECK(!s.lease_next_map(registry));
  CHECK(scalar(s.raw_handle(),"SELECT SUM(charged_requests) FROM read_job_budgets")==0);
  CHECK(scalar(s.raw_handle(),"SELECT SUM(charged_requests) FROM map_jobs")==0);
  rejects([&]{s.job_control("alice",fixture,"resume");},"E_PRINCIPAL_REVOKED");
  rejects([&]{s.map_submit("alice",params,registry);},"E_PRINCIPAL_REVOKED");
}
TEST(fixture_late_completion_cannot_publish) {
  Temp t;Store s(t.path/"db");s.job_create("alice","plain","fixture");auto lease=s.lease_next();CHECK(lease);
  s.revoke_principal("admin","alice");auto doc=document();CHECK(!s.complete(*lease,doc,parse_document(doc.raw,doc.content_type,doc.title)));
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);CHECK(scalar(s.raw_handle(),"SELECT used_pages FROM jobs")==1);
}
TEST(known_inflight_read_is_settled_without_publication_or_retry) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");Worker worker;WebService web(s,worker,{});
  s.read_many_submit("alice",{document().url,"https://read.example/next"},"batch",{});auto lease=s.lease_next_read();CHECK(lease);
  worker.during=[&]{other.revoke_principal("admin","alice");};web.execute_read(*lease);
  CHECK(worker.calls==1);CHECK(!s.lease_next_read());CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE state='cancelled'")==1);
  CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM read_job_budgets")==1);
  CHECK(scalar(s.raw_handle(),"SELECT SUM(external_requests) FROM read_job_items")==1);
  CHECK(scalar(s.raw_handle(),"SELECT SUM(unknown_requests) FROM read_job_items")==0);
  s.fail_read(*lease,"E_LATE",1,48);CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM read_job_budgets")==1);
}
TEST(unknown_inflight_read_keeps_full_charge_across_restart) {
  Temp t;std::int64_t now=1800000000000,charged=0;
  { Store s(t.path/"db",[&]{return now;});s.read_many_submit("alice",{document().url},"batch",{});const auto lease=s.lease_next_read();CHECK(lease);
    charged=lease->request_grant;s.revoke_principal("admin","alice");CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM read_job_budgets")==charged);
    CHECK(scalar(s.raw_handle(),"SELECT SUM(request_grant) FROM read_job_items")==charged); }
  now+=600000;Store s(t.path/"db",[&]{return now;});s.settle_interruptions();
  CHECK(!s.lease_next_read());CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM read_job_budgets")==charged);
  CHECK(scalar(s.raw_handle(),"SELECT SUM(unknown_requests) FROM read_job_items")==charged);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE state='cancelled'")==1);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);
}
TEST(inflight_map_unknown_reservation_and_site_lock_survive_revoke) {
  Temp t;std::int64_t now=1800000000000;Store s(t.path/"db",[&]{return now;});SiteRegistry registry(sites());
  auto args=Json::parse(R"({"site_id":"site","seeds":["https://read.example/index"],"idempotency_key":"map"})");s.map_submit("alice",args,registry);const auto lease=s.lease_next_map(registry);CHECK(lease);
  s.revoke_principal("admin","alice");CHECK(!s.map_lease_live(*lease));
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM site_request_state WHERE active_attempt!=''")==1);
  CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM map_jobs")==1);now+=600000;s.settle_interruptions();
  CHECK(scalar(s.raw_handle(),"SELECT unknown_requests FROM map_jobs")==1);CHECK(scalar(s.raw_handle(),"SELECT charged_requests FROM map_jobs")==1);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM jobs WHERE state='cancelled'")==1);CHECK(!s.lease_next_map(registry));
}
TEST(persistent_search_late_result_is_fenced_and_receipt_retained) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");Worker worker;WebService web(s,worker,SourceRegistry(sources()));
  const auto args=Json::parse(R"({"query":"marker","idempotency_key":"search"})");web.submit_search("alice",args);const auto lease=web.lease_search();CHECK(lease);
  worker.during=[&]{other.revoke_principal("admin","alice");};web.execute_search(*lease);
  CHECK(!web.lease_search());CHECK(worker.calls==1);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM search_versions")==0);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM search_stream_receipts WHERE state='cancelled' AND used_requests=1 AND unknown_requests=1")==1);
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM search_event_log WHERE kind='principal_revoked'")==1);
  rejects([&]{web.submit_search("alice",args);},"E_PRINCIPAL_REVOKED");
}
TEST(synchronous_search_response_is_rechecked_after_worker_returns) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");Worker worker;WebService web(s,worker,SourceRegistry(sources()));FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service api(s,fixtures,t.path/"backup",&web);
  worker.during=[&]{other.revoke_principal("admin","alice");};
  rejects([&]{api.dispatch({"alice",false},request("web.search",Json::parse(R"({"query":"marker"})")));},"E_PRINCIPAL_REVOKED");
  CHECK(worker.calls==1);CHECK(api.dispatch({"bob",false},request("stats")).at("documents").num()==0);
}
TEST(synchronous_read_cannot_publish_after_revocation) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");Worker worker;WebService web(s,worker,{});FixtureCatalog fixtures(BZS_FIXTURE_DIR);Service api(s,fixtures,t.path/"backup",&web);
  worker.during=[&]{other.revoke_principal("admin","alice");};
  rejects([&]{api.dispatch({"alice",false},request("web.read",Json::parse(R"({"url":"https://read.example/late","freshness":"refresh"})")));},"E_PRINCIPAL_REVOKED");
  CHECK(worker.calls==1);CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM snapshots")==0);
}
TEST(backup_remains_quarantined_and_actor_recheck_is_durable) {
  Temp t;Store s(t.path/"db"),other(t.path/"db");s.ingest("alice",document());s.revoke_principal("admin","alice");
  const auto backup=t.path/"backup.sqlite";s.backup(backup);rejects([&]{Store restored(backup);},"E_RESTORE_QUARANTINED");
  other.revoke_principal("operator","admin");rejects([&]{s.revoke_principal("admin","bob");},"E_PRINCIPAL_REVOKED");
  CHECK(scalar(s.raw_handle(),"SELECT COUNT(*) FROM principal_revocations")==2);s.require_principal_active("bob");
}
}
int main() {
  std::size_t failures=0;
  for(const auto& [name,test]:tests())try { test();std::cout<<"PASS "<<name<<'\n'; }catch(const std::exception& e) { ++failures;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n'; }
  std::cout<<(tests().size()-failures)<<'/'<<tests().size()<<" tests, "<<assertions<<" assertions\n";return failures?1:0;
}
