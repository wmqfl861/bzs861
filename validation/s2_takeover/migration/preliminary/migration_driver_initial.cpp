// Schema 12 -> 13 integration migration review. The reviewer authored the shared-read increment;
// this separately creates the old database and checks the final integrated library. Business records are created via public
// APIs of the actual old library. SQL below is guarded read-only observation.
// Worker responses are explicit offline fixtures; no real network is claimed.
#include "bzs/error.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

using namespace bzs;
namespace {
std::size_t assertions=0;
#define CHECK(value) do { ++assertions; if(!(value)) throw std::runtime_error(std::string("check failed: ")+#value+" at "+std::to_string(__LINE__)); } while(false)
template<class F> void rejects(F fn,const std::string& code) {
  ++assertions;
  try { fn(); } catch(const Error& e) {
    if(e.code==code)return;
    throw std::runtime_error("expected "+code+", got "+e.code);
  }
  throw std::runtime_error("expected "+code);
}
constexpr std::int64_t start=1700000000000LL, day=86400000LL;
const std::string origin="https://schema-migration.example";
std::string url(const std::string& name) { return origin+"/docs/"+name; }
void save(const std::filesystem::path& path,const Json& data) {
  std::ofstream out(path);CHECK(out.good());out<<data.dump(true)<<'\n';CHECK(out.good());
}
[[maybe_unused]] Json load(const std::filesystem::path& path) { return Json::parse(read_file(path,8*1024*1024),8*1024*1024); }
std::string quote(const std::string& name) {
  std::string result="\"";for(char c:name) { result+=c;if(c=='\"')result+='\"'; }return result+'\"';
}
Json rows(sqlite3* db,const std::string& sql) {
  sqlite3_stmt* raw=nullptr;CHECK(sqlite3_prepare_v2(db,sql.c_str(),-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> stmt(raw,sqlite3_finalize);
  CHECK(sqlite3_stmt_readonly(raw)==1);
  auto result=Json::array();int rc=SQLITE_OK;
  while((rc=sqlite3_step(raw))==SQLITE_ROW) {
    auto row=Json::array();
    for(int column=0;column<sqlite3_column_count(raw);++column) {
      const auto kind=sqlite3_column_type(raw,column);
      if(kind==SQLITE_NULL)row.push(Json());
      else if(kind==SQLITE_INTEGER)row.push(Json::integer(sqlite3_column_int64(raw,column)));
      else if(kind==SQLITE_BLOB) {
        const auto* bytes=static_cast<const char*>(sqlite3_column_blob(raw,column));
        const auto length=sqlite3_column_bytes(raw,column);auto value=Json::object();
        value.set("blob_bytes",static_cast<std::int64_t>(length));value.set("blob_sha256",sha256(std::string_view(bytes?bytes:"",static_cast<std::size_t>(length))));row.push(value);
      } else {
        const auto* bytes=sqlite3_column_text(raw,column);const auto length=sqlite3_column_bytes(raw,column);
        row.push(Json::string(std::string_view(reinterpret_cast<const char*>(bytes),static_cast<std::size_t>(length))));
      }
    }
    result.push(row);
  }
  CHECK(rc==SQLITE_DONE);return result;
}
std::int64_t scalar(sqlite3* db,const std::string& sql) { return rows(db,sql).at(0).at(0).num(); }
Json footprints(sqlite3* db,const Json* previous=nullptr) {
  auto result=Json::object();
  auto tables=rows(db,"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
  for(std::size_t i=0;i<tables.size();++i) {
    const auto table=tables.at(i).at(0).str();if(previous&&!previous->has(table))continue;auto columns=Json::array();
    if(previous)columns=previous->at(table).at("columns");
    else {
      const auto info=rows(db,"PRAGMA table_info("+quote(table)+")");
      for(std::size_t n=0;n<info.size();++n)columns.push(info.at(n).at(1));
    }
    std::string query="SELECT ";
    for(std::size_t n=0;n<columns.size();++n) { if(n)query+=',';query+=quote(columns.at(n).str()); }
    query+=" FROM "+quote(table);const auto data=rows(db,query);std::vector<std::string> ordered;
    for(std::size_t n=0;n<data.size();++n)ordered.push_back(data.at(n).canonical());
    std::sort(ordered.begin(),ordered.end());std::string encoded;
    for(const auto& row:ordered)encoded+=row+'\n';
    auto entry=Json::object();entry.set("columns",columns);entry.set("rows",static_cast<std::int64_t>(ordered.size()));entry.set("rows_sha256",sha256(encoded));result.set(table,entry);
  }
  return result;
}
Json readonly_metadata(const std::filesystem::path& path) {
  sqlite3* raw=nullptr;CHECK(sqlite3_open_v2(path.string().c_str(),&raw,SQLITE_OPEN_READONLY,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);auto value=Json::object();
  value.set("schema",scalar(raw,"PRAGMA user_version"));
  value.set("restore_quarantine",rows(raw,"SELECT value FROM settings WHERE key='restore_quarantine'").at(0).at(0));return value;
}
Json site_manifest() {
  auto manifest=Json::parse(R"({"version":1,"sites":[{"id":"migration-site","enabled":true,"origin":"https://schema-migration.example","path_prefixes":["/docs/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://schema-migration.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"independent-offline-migration-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");return manifest;
}
Json source_manifest() {
  return Json::parse(R"({"version":1,"sources":[{"id":"migration-source","provider":"synthetic","index_family":"synthetic-migration","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"daily_cap":1000,"params":{"adapter":"offline_fixture","base_url":"https://schema-migration.example/search"},"contract":{"id":"migration-source","provider_id":"synthetic","index_family":"synthetic-migration","logical_service_id":"migration-service","capability":"web","endpoint":"https://schema-migration.example/search","adapter_id":"offline_fixture","enabled":true,"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://schema-migration.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"independent-offline-migration-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400,"shared_budget_key":"migration-budget"}}}]})");
}
Json read_args(const std::string& target) { auto value=Json::object();value.set("url",target);value.set("freshness","refresh");return value; }
Json crawl_args(const std::string& key,const std::string& target) {
  auto value=Json::object(),seeds=Json::array();seeds.push(Json::string(target));value.set("site_id","migration-site");value.set("idempotency_key",key);value.set("seeds",seeds);return value;
}
Json session_args() { auto value=Json::object();value.set("query","migrationneedle");value.set("idempotency_key","migration-session");return value; }
Json http_cache(const std::string& target) {
  auto value=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"migration-v1\"","last_modified":"Tue, 14 Nov 2023 22:13:20 GMT","cache_control":"max-age=3600","vary":"","date":"","age":"","expires":""})");
  value.set("url",target);value.set("request_profile",sha256("independent-offline-migration-profile"));return value;
}
struct Worker:WorkerPort {
  std::int64_t now=start;bool validators=false,allow_all=false,unsolicited=false;std::vector<Json> calls;
  Json page(const Json& p) const {
    auto value=Json::parse(R"({"status":"ok","html":"migrationneedle web body retained","text":"migrationneedle web body retained","title":"migration fixture","content_type":"text/plain","extractor":"migration-fixture/1","extract_status":"ok","http_status":200,"index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"publication":{"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"migrationneedle web body retained","snippet_title":"migration fixture","directives":[],"diagnostics":[]}})");
    value.set("final_url",p.at("url"));value.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));
    if(validators)value.set("http_cache",http_cache(p.at("url").str()));
    return value;
  }
  Json call(const std::string& method,const Json& p,int) override {
    auto saved=Json::object();saved.set("method",method);saved.set("params",p);calls.push_back(saved);
    if(method=="search.multi") {
      auto result=Json::object(),by_source=Json::object(),entry=Json::object(),hits=Json::array();
      auto hit=Json::object();hit.set("url",url("search-result"));hit.set("title","migrationneedle search result");hit.set("snippet","synthetic migration result");hit.set("rank",std::int64_t{1});hits.push(hit);
      entry.set("status","ok");entry.set("hits",hits);by_source.set(p.at("sources").at(0).str(),entry);result.set("results",by_source);return result;
    }
    CHECK(method=="fetch.extract"||method=="map.fetch");
    const bool map=method=="map.fetch",robots=map&&p.at("kind").str()=="robots",conditional=p.has("conditional");
    if(!map&&!conditional&&!unsolicited)return page(p);
    auto response=Json::object();response.set("status",conditional||unsolicited?"not_modified":"ok");
    response.set("http_status",static_cast<std::int64_t>(conditional||unsolicited?304:robots&&allow_all?404:200));response.set("final_url",p.at("url"));
    response.set("usage",Json::parse(conditional||unsolicited?R"({"external_requests":1,"content_bytes":0})":R"({"external_requests":1,"content_bytes":100})"));
    if(conditional||unsolicited) {
      response.set("binding_id",conditional?p.at("conditional").at("binding_id"):Json::string(sha256("unsolicited")));
      response.set("http_cache",http_cache(p.at("url").str()));
    }
    if(!map)return response;
    response.set("url",p.at("url"));
    if(robots) {
      auto record=conditional?Json::parse(p.at("robots_revalidation_base").at("record").dump()):Json::parse(R"({"version":"bzs-robots-1","product_token":"BzsBot","state":"rules","rules":[],"sitemaps":[],"diagnostics":[]})");
      if(!conditional) { record.set("state",allow_all?"allow_all":"rules");record.set("body_sha256",sha256(allow_all?"":"User-agent: *\nAllow: /\n")); }
      record.set("origin",origin);record.set("fetched_at_ms",now);record.set("expires_at_ms",now+3600000);response.set("robots",record);
      if(validators&&!allow_all)response.set("http_cache",http_cache(p.at("url").str()));
      return response;
    }
    auto decision=Json::object();decision.set("version","bzs-robots-1");decision.set("product_token","BzsBot");decision.set("origin",origin);decision.set("target_url",p.at("url"));decision.set("body_sha256",p.at("robots").at("body_sha256"));decision.set("allowed",true);decision.set("reason","offline_migration_allow");response.set("robots_decision",decision);
    if(!conditional) {
      auto body=page(p),nested=Json::object();for(const auto& key:body.keys())if(key!="http_cache")nested.set(key,body.at(key));response.set("page",nested);response.set("publication",body.at("publication"));
      if(validators)response.set("http_cache",body.at("http_cache"));
      auto discovery=Json::object();discovery.set("document_url",p.at("url"));discovery.set("document_type","text");discovery.set("parse_status","ok");discovery.set("truncated",false);discovery.set("diagnostics",Json::object());discovery.set("candidates",Json::array());response.set("discovery",discovery);
    }
    return response;
  }
};
void drain(WebService& web) { std::size_t count=0;while(auto lease=web.lease_map()) { CHECK(++count<=20);web.execute_map(*lease); } }
Json library_capabilities(Store& store,const std::filesystem::path& root) {
  FixtureCatalog fixtures(root/"fixtures");Service service(store,fixtures,root/"unused-service-backups");return service.capabilities();
}
Json observations(Store& store,const Json& ids) {
  auto out=Json::object(),snapshots=Json::object(),evidence=Json::object(),jobs=Json::object(),results=Json::object();
  for(const auto& name:ids.at("snapshots").keys())snapshots.set(name,store.snapshot("alice",ids.at("snapshots").at(name).str()));
  for(const auto& name:ids.at("evidence").keys())evidence.set(name,store.evidence_get("alice",ids.at("evidence").at(name).str()));
  for(const auto& name:ids.at("jobs").keys())jobs.set(name,store.job_get("alice",ids.at("jobs").at(name).str()));
  for(const auto* name:{"terminal-read","terminal-crawl"})results.set(name,store.job_results("alice",ids.at("jobs").at(name).str()));
  out.set("snapshots",snapshots);out.set("evidence",evidence);out.set("jobs",jobs);out.set("results",results);
  const auto local=ids.at("local_session").str(),web=ids.at("web_session").str();
  out.set("local_state",store.session_get("alice",local));out.set("local_events",store.session_events("alice",local));out.set("local_page",store.session_page("alice",local,local,"",100));
  out.set("web_state",store.session_get("alice",web));out.set("web_events",store.session_events("alice",web));out.set("web_page",store.session_page("alice",web,ids.at("web_version").str(),"",100));
  out.set("source_states",store.source_states());out.set("delete_generation",store.deletion_generation("alice",url("deleted")));out.set("usage",store.stats("alice"));return out;
}

#if !BZS_MIGRATION_NEW
void old_create(const std::filesystem::path& root) {
  CHECK(!std::filesystem::exists(root/"store.db"));std::filesystem::create_directories(root/"fixtures");
  save(root/"fixtures/manifest.json",Json::parse(R"({"source_id":"migration-offline","kind":"fixture","fixtures":[]})"));
  Worker worker;worker.validators=true;auto record=Json::object(),ids=Json::object(),snapshots=Json::object(),proofs=Json::object(),jobs=Json::object();
  {
    Store store(root/"store.db",[&]{return worker.now;});CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==12);
    const auto capabilities=library_capabilities(store,root);CHECK(capabilities.at("version").str()=="0.1.9");record.set("library_capabilities",capabilities);
    const auto archived=store.read_many_submit("alice",{url("archive")},"permanent-archive",{}).at("job_id").str();store.job_control("alice",archived,"cancel");worker.now+=8*day;
    CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==1);jobs.set("archived",archived);
    WebService web(store,worker,SourceRegistry(source_manifest()),SiteRegistry(site_manifest()));
    const auto imported=store.ingest("alice",{url("import"),"migrationneedle import retained body","text/plain","migration import"});snapshots.set("import",imported.at("snapshot_id"));proofs.set("import",store.evidence("alice",imported.at("snapshot_id").str(),0,15).at("evidence_id"));
    const auto fetched=web.read("alice",read_args(url("legacy-web")));snapshots.set("web",fetched.at("snapshot_id"));proofs.set("web",store.evidence("alice",fetched.at("snapshot_id").str(),0,15).at("evidence_id"));
    const auto deleted=store.ingest("alice",{url("deleted"),"migrationneedle deleted body","text/plain","deleted"});ids.set("deleted_snapshot",deleted.at("snapshot_id"));ids.set("deleted_document",deleted.at("document_id"));store.erase("alice",deleted.at("document_id").str());
    const auto completed=store.read_many_submit("alice",{url("terminal")},"terminal-key",{}).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease&&lease->job_id==completed);web.execute_read(*lease);CHECK(store.job_get("alice",completed).at("state").str()=="succeeded");jobs.set("terminal-read",completed);
    const auto crawl=web.submit_crawl("alice",crawl_args("crawl-key",url("crawl-alice"))).at("job_id").str();drain(web);CHECK(store.job_get("alice",crawl).at("state").str()=="succeeded");jobs.set("terminal-crawl",crawl);
    worker.allow_all=true;const auto bob=web.submit_crawl("bob",crawl_args("bob-crawl-key",url("crawl-bob"))).at("job_id").str();drain(web);CHECK(store.job_get("bob",bob).at("state").str()=="succeeded");ids.set("bob_job",bob);worker.allow_all=false;
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache WHERE json_extract(record_json,'$.state')='allow_all'")==1);
    const auto unknown=store.read_many_submit("alice",{url("unknown")},"unknown-key",{}).at("job_id").str();
    CHECK(store.lease_next_read().has_value());worker.now+=26001;store.settle_interruptions();
    CHECK(store.job_get("alice",unknown).at("usage").at("unknown_requests_charged").num()>0);jobs.set("unknown-read",unknown);
    const auto paused_map=web.submit_map("alice",crawl_args("paused-map-key",url("paused-map"))).at("job_id").str();
    store.job_control("alice",paused_map,"pause");jobs.set("paused-map",paused_map);
    jobs.set("queued-map",web.submit_map("alice",crawl_args("queued-map-key",url("queued-map"))).at("job_id"));
    const auto active=store.read_many_submit("alice",{url("active")},"active-key",{}).at("job_id").str();const auto abandoned=store.lease_next_read();CHECK(abandoned&&abandoned->job_id==active);jobs.set("active-read",active);
    jobs.set("queued-read",store.read_many_submit("alice",{url("queued")},"queued-key",{}).at("job_id"));
    const auto paused=store.read_many_submit("alice",{url("paused")},"paused-key",{}).at("job_id").str();store.job_control("alice",paused,"pause");jobs.set("paused-read",paused);
    const auto paused_crawl=web.submit_crawl("alice",crawl_args("paused-crawl-key",url("paused-crawl"))).at("job_id").str();store.job_control("alice",paused_crawl,"pause");jobs.set("paused-crawl",paused_crawl);
    ids.set("local_session",store.search("alice",parse_query(Json::string("migrationneedle"))).at("session_id"));
    const auto search=web.submit_search("alice",session_args()).at("session_id").str();const auto search_lease=web.lease_search();CHECK(search_lease.has_value());web.execute_search(*search_lease);CHECK(store.session_get("alice",search).at("state").str()=="completed");ids.set("web_session",search);ids.set("web_version",store.session_get("alice",search).at("result_set_version"));
    ids.set("snapshots",snapshots);ids.set("evidence",proofs);ids.set("jobs",jobs);record.set("ids",ids);record.set("clock_ms",worker.now);
    record.set("observations",observations(store,ids));record.set("legacy_table_footprints",footprints(store.raw_handle()));
    record.set("backup",store.backup(root/"quarantined-old-backup.db"));
    record.set("schema",scalar(store.raw_handle(),"PRAGMA user_version"));
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM pragma_table_info('robots_cache') WHERE name='http_cache_json'")==1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM pragma_table_info('snapshots') WHERE name='http_cache_bytes'")==1);
    CHECK(rows(store.raw_handle(),"PRAGMA integrity_check").at(0).at(0).str()=="ok");CHECK(rows(store.raw_handle(),"PRAGMA foreign_key_check").size()==0);
  }
  rejects([&]{Store forbidden(root/"quarantined-old-backup.db",[&]{return worker.now;});},"E_RESTORE_QUARANTINED");
  record.set("closed_database",readonly_metadata(root/"store.db"));record.set("quarantined_backup",readonly_metadata(root/"quarantined-old-backup.db"));
  auto calls=Json::array();for(const auto& call:worker.calls)calls.push(call);record.set("worker_calls",calls);record.set("assertions",static_cast<std::int64_t>(assertions));record.set("external_network_requests",std::int64_t{0});
  save(root/"old_creation.json",record);std::cout<<"old 0.1.9 schema=12 assertions="<<assertions<<" worker_fixture_calls="<<worker.calls.size()<<"\n";
}
#else
void preserves(const Json& current,const Json& old,const std::string& at) {
  if(old.is_object()) {
    CHECK(current.is_object());
    for(const auto& key:old.keys()) {
      if(!current.has(key))throw std::runtime_error("missing old public field "+at+"."+key);
      preserves(current.at(key),old.at(key),at+"."+key);
    }
  } else if(old.is_array()) {
    CHECK(current.is_array()&&current.size()==old.size());
    for(std::size_t i=0;i<old.size();++i)preserves(current.at(i),old.at(i),at+"["+std::to_string(i)+"]");
  } else {
    if(current.canonical()!=old.canonical())throw std::runtime_error("changed old public field "+at+": "+old.dump()+" -> "+current.dump());
    CHECK(current.canonical()==old.canonical());
  }
}
Json request(const std::string& operation,const Json& params) {
  auto result=Json::object();result.set("operation",operation);result.set("params",params);return result;
}
void new_verify(const std::filesystem::path& root) {
  const auto old=load(root/"old_creation.json"),ids=old.at("ids");
  Worker worker;worker.now=old.at("clock_ms").num();worker.validators=true;
  CHECK(readonly_metadata(root/"store.db").at("schema").num()==12);
  auto report=Json::object();report.set("before_open",readonly_metadata(root/"store.db"));
  {
    Store store(root/"store.db",[&]{return worker.now;});CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==13);
    const auto previous=old.at("legacy_table_footprints"),current=footprints(store.raw_handle(),&previous);
    save(root/"after_migration_legacy_footprints.json",current);
    CHECK(current.keys().size()==previous.keys().size());
    for(const auto& table:previous.keys()) {
      if(current.at(table).canonical()!=previous.at(table).canonical())std::cerr<<"legacy table mismatch "<<table<<'\n';
      CHECK(current.at(table).canonical()==previous.at(table).canonical());
    }
    report.set("preserved_legacy_tables",static_cast<std::int64_t>(current.keys().size()));
    report.set("new_tables",rows(store.raw_handle(),"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('principal_revocations','source_publication_state','source_publication_rules','read_shared_attempts','read_shared_members') ORDER BY name"));
    CHECK(report.at("new_tables").size()==5);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM principal_revocations")==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_attempts")==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM read_shared_members")==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM source_publication_rules")==0);
    CHECK(scalar(store.raw_handle(),"SELECT generation FROM source_publication_state WHERE singleton=1")==0);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM search_streams WHERE source_generation<>0")==0);
    // Old source results are not authorized by merely upgrading the database.
    // The same independently reviewed manifest must be activated explicitly.
    rejects([&]{store.session_page("alice",ids.at("web_session").str(),ids.at("web_version").str(),"",100);},"E_SESSION_INVALIDATED");
    WebService web(store,worker,SourceRegistry(source_manifest()),SiteRegistry(site_manifest()));
    preserves(observations(store,ids),old.at("observations"),"observations");
    report.set("legacy_public_fields_preserved_after_explicit_registry_activation",true);
    report.set("legacy_web_page_without_source_activation","E_SESSION_INVALIDATED");
    CHECK(worker.calls.empty());
    rejects([&]{store.snapshot("alice",ids.at("deleted_snapshot").str());},"E_NOT_FOUND");
    CHECK(!store.cached("alice",url("deleted")));CHECK(store.deletion_generation("alice",url("deleted"))>0);
    rejects([&]{store.snapshot("bob",ids.at("snapshots").at("import").str());},"E_NOT_FOUND");
    rejects([&]{store.evidence_get("bob",ids.at("evidence").at("web").str());},"E_NOT_FOUND");
    rejects([&]{store.job_get("bob",ids.at("jobs").at("queued-read").str());},"E_NOT_FOUND");
    rejects([&]{store.session_get("bob",ids.at("web_session").str());},"E_NOT_FOUND");
    CHECK(store.read_many_submit("alice",{url("archive")},"permanent-archive",{}).at("job_id").str()==ids.at("jobs").at("archived").str());
    CHECK(store.read_many_submit("alice",{url("terminal")},"terminal-key",{}).at("reused").flag());
    CHECK(store.read_many_submit("alice",{url("queued")},"queued-key",{}).at("reused").flag());
    rejects([&]{store.read_many_submit("alice",{url("wrong")},"permanent-archive",{});},"E_IDEMPOTENCY_CONFLICT");
    rejects([&]{store.job_results("alice",ids.at("jobs").at("archived").str());},"E_JOB_RESULTS_EXPIRED");
    CHECK(store.job_get("alice",ids.at("jobs").at("active-read").str()).at("usage").at("reserved_requests").num()>0);
    CHECK(store.job_get("alice",ids.at("jobs").at("unknown-read").str()).at("usage").at("unknown_requests_charged").num()>0);
    CHECK(store.job_get("alice",ids.at("jobs").at("paused-crawl").str()).at("state").str()=="paused");
    CHECK(store.job_get("alice",ids.at("jobs").at("paused-map").str()).at("state").str()=="paused");
    CHECK(web.submit_search("alice",session_args()).at("reused").flag());CHECK(!web.lease_search());
    CHECK(worker.calls.empty());
    // A schema12 validator alone does not prove freshness of its cached body.
    auto cached_only=read_args(url("legacy-web"));cached_only.set("freshness","cache_only");
    rejects([&]{web.read("alice",cached_only);},"E_CACHE_STALE");CHECK(worker.calls.empty());
    CHECK(store.evidence_get("alice",ids.at("evidence").at("web").str()).canonical()==old.at("observations").at("evidence").at("web").canonical());
    report.set("legacy_cache_metadata_not_assumed_fresh","E_CACHE_STALE_without_network");
    rejects([&]{Store forbidden(root/"quarantined-old-backup.db",[&]{return worker.now;});},"E_RESTORE_QUARANTINED");
    CHECK(readonly_metadata(root/"quarantined-old-backup.db").at("schema").num()==12);
    CHECK(readonly_metadata(root/"quarantined-old-backup.db").at("restore_quarantine").str()=="1");
    report.set("library_capabilities",library_capabilities(store,root));

    // Exercise new tables on the genuinely upgraded store only after all
    // preservation checks. No SQL below mutates business records.
    store.job_control("alice",ids.at("jobs").at("queued-read").str(),"pause");
    const auto payer=store.read_many_submit("carol",{url("post-upgrade-shared")},"upgrade-payer",{}).at("job_id").str();
    const auto member=store.read_many_submit("carol",{url("post-upgrade-shared")},"upgrade-member",{}).at("job_id").str();
    const auto shared=store.lease_next_read();CHECK(shared.has_value());CHECK(shared->job_id==payer);CHECK(!store.lease_next_read());
    CHECK(store.job_get("carol",member).at("state").str()=="running");
    store.job_control("carol",payer,"cancel");CHECK(store.read_lease_live(*shared));web.execute_read(*shared);
    CHECK(store.job_get("carol",payer).at("state").str()=="cancelled");CHECK(store.job_get("carol",member).at("state").str()=="succeeded");
    CHECK(store.job_get("carol",payer).at("usage").at("external_requests").num()==1);
    CHECK(store.job_get("carol",member).at("usage").at("external_requests").num()==0);
    CHECK(store.job_results("carol",member).at("items").at(0).at("shared_read").at("payer_job_id").str()==payer);
    CHECK(worker.calls.size()==1);report.set("new_shared_read_after_upgrade",true);

    const auto revoked_snapshot=store.ingest("revoked-test",{url("revocation"),"revocation body","text/plain","revocation"});
    FixtureCatalog fixtures(root/"fixtures");Service service(store,fixtures,root/"unused-service-backups",&web);
    auto revoke=Json::object();revoke.set("subject","revoked-test");
    auto receipt=service.dispatch({"migration-admin",true},request("principal.revoke",revoke));CHECK(receipt.at("revoked").flag());
    rejects([&]{store.require_principal_active("revoked-test");},"E_PRINCIPAL_REVOKED");
    rejects([&]{store.snapshot("revoked-test",revoked_snapshot.at("snapshot_id").str());},"E_PRINCIPAL_REVOKED");
    CHECK(service.dispatch({"migration-admin",true},request("principal.revoke",revoke)).at("already_revoked").flag());
    report.set("new_principal_revocation_after_upgrade",true);
    CHECK(store.evidence_get("alice",ids.at("evidence").at("web").str()).canonical()==old.at("observations").at("evidence").at("web").canonical());
    report.set("new_backup_receipt",store.backup(root/"quarantined-new-backup.db"));
    rejects([&]{Store forbidden(root/"quarantined-new-backup.db",[&]{return worker.now;});},"E_RESTORE_QUARANTINED");
    CHECK(rows(store.raw_handle(),"PRAGMA integrity_check").at(0).at(0).str()=="ok");CHECK(rows(store.raw_handle(),"PRAGMA foreign_key_check").size()==0);
    report.set("integrity_check","ok");report.set("foreign_key_violations",std::int64_t{0});
  }
  report.set("closed_database",readonly_metadata(root/"store.db"));report.set("old_backup_after_new_open_attempt",readonly_metadata(root/"quarantined-old-backup.db"));
  report.set("new_backup",readonly_metadata(root/"quarantined-new-backup.db"));
  auto calls=Json::array();for(const auto& call:worker.calls)calls.push(call);report.set("worker_calls",calls);
  report.set("assertions_before_output_checks",static_cast<std::int64_t>(assertions));report.set("external_network_requests",std::int64_t{0});
  save(root/"new_verification.json",report);std::cout<<"new schema=13 assertions="<<assertions<<" worker_fixture_calls="<<worker.calls.size()<<"\n";
}
#endif
}
int main(int argc,char** argv) {
  try {
    CHECK(argc==2);const auto root=std::filesystem::path(argv[1]);std::filesystem::create_directories(root);
#if BZS_MIGRATION_NEW
    new_verify(root);
#else
    old_create(root);
#endif
    return 0;
  } catch(const std::exception& e) { std::cerr<<"migration driver failed: "<<e.what()<<" after "<<assertions<<" assertions\n";return 1; }
}
