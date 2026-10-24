// Independent P1-3 genuine 44253a7 -> codepoint-gram index migration probe.
// Same source is linked to each frozen library. Business fixtures use public APIs;
// all raw SQL below is guarded read-only. Worker responses are offline fixtures.
// This is reviewer evidence in /tmp, not an implementation or formal test change.
#include "bzs/error.hpp"
#include "bzs/service.hpp"
#include "bzs/util.hpp"
#include "bzs/tokenizer.hpp"
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
[[maybe_unused]] Json crawl_args(const std::string& key,const std::string& target) {
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
[[maybe_unused]] void drain(WebService& web) { std::size_t count=0;while(auto lease=web.lease_map()) { CHECK(++count<=20);web.execute_map(*lease); } }
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
  out.set("local_first_page",store.page("alice",local,"",1));
  out.set("local_cursor_page",store.page("alice",local,ids.at("local_cursor").str(),1));
  out.set("local_state",store.session_get("alice",local));out.set("local_events",store.session_events("alice",local));out.set("local_page",store.session_page("alice",local,local,"",100));
  out.set("web_state",store.session_get("alice",web));out.set("web_events",store.session_events("alice",web));out.set("web_page",store.session_page("alice",web,ids.at("web_version").str(),"",100));
  out.set("source_states",store.source_states());out.set("delete_generation",store.deletion_generation("alice",url("deleted")));out.set("usage",store.stats("alice"));return out;
}


constexpr const char* old_marker="bzs-ascii-cjk/2";
constexpr const char* new_marker="bzs-ascii-cjk/2+codepoint-grams/1";
Json closed_state(const std::filesystem::path& path) {
  sqlite3* raw=nullptr;CHECK(sqlite3_open_v2(path.string().c_str(),&raw,SQLITE_OPEN_READONLY,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3,decltype(&sqlite3_close)> db(raw,sqlite3_close);register_fts_tokenizer(raw);
  auto out=Json::object();out.set("metadata",readonly_metadata(path));
  out.set("settings",rows(raw,"SELECT key,value FROM settings ORDER BY key"));
  out.set("table_footprints",footprints(raw));
  out.set("table_catalog",rows(raw,"SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"));
  return out;
}
void same(const Json& current,const Json& previous,const std::string& name) {
  if(current.canonical()!=previous.canonical())throw std::runtime_error("preservation mismatch: "+name);
  ++assertions;
}
Json policy(bool index,bool snippet) {
  auto out=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"migrationneedle web body retained","snippet_title":"migration fixture","directives":[],"diagnostics":[]})");
  out.set("index_allowed",index);out.set("snippet_allowed",snippet);
  if(!snippet){out.set("snippet_text","");out.set("snippet_title","");}
  return out;
}
void create_old(const std::filesystem::path& root) {
  CHECK(!std::filesystem::exists(root/"store.db"));std::filesystem::create_directories(root/"fixtures");
  save(root/"fixtures/manifest.json",Json::parse(R"({"source_id":"migration-offline","kind":"fixture","fixtures":[]})"));
  Worker worker;worker.validators=true;auto record=Json::object(),ids=Json::object(),snapshots=Json::object(),proofs=Json::object(),jobs=Json::object();
  {
    Store store(root/"store.db",[&]{return worker.now;});
    CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==13);
    CHECK(rows(store.raw_handle(),"SELECT value FROM settings WHERE key='fts_tokenizer_version'").at(0).at(0).str()==old_marker);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM sqlite_master WHERE name='substring_fts'")==0);
    record.set("library_capabilities",library_capabilities(store,root));
    const auto archived=store.read_many_submit("alice",{url("archive")},"permanent-archive",{}).at("job_id").str();store.job_control("alice",archived,"cancel");worker.now+=8*day;
    CHECK(store.compact_jobs("alice").at("compacted_jobs").num()==1);jobs.set("archived",archived);
    WebService web(store,worker,SourceRegistry(source_manifest()),SiteRegistry(site_manifest()));
    const auto imported=store.ingest("alice",{url("import"),"migrationneedle legacy apple machine learning caf\xc3\xa9 C++ canonical body","text/plain","migration old import"});
    snapshots.set("import",imported.at("snapshot_id"));proofs.set("import",store.evidence("alice",imported.at("snapshot_id").str(),0,15).at("evidence_id"));
    const auto fetched=web.read("alice",read_args(url("legacy-web")));snapshots.set("web",fetched.at("snapshot_id"));proofs.set("web",store.evidence("alice",fetched.at("snapshot_id").str(),0,15).at("evidence_id"));
    const auto noindex=web.read("alice",read_args(url("noindex-web")));snapshots.set("noindex",noindex.at("snapshot_id"));ids.set("noindex_document",noindex.at("document_id"));
    const auto nosnippet=web.read("alice",read_args(url("nosnippet-web")));snapshots.set("nosnippet",nosnippet.at("snapshot_id"));ids.set("nosnippet_document",nosnippet.at("document_id"));
    const auto deleted=store.ingest("alice",{url("deleted"),"migrationneedle deleted body","text/plain","deleted"});ids.set("deleted_snapshot",deleted.at("snapshot_id"));ids.set("deleted_document",deleted.at("document_id"));store.erase("alice",deleted.at("document_id").str());
    const auto fixture=store.job_create("alice","offline-complete","permanent-fixture").at("job_id").str();const auto fixture_lease=store.lease_next();CHECK(fixture_lease&&fixture_lease->id==fixture);
    const SourceDocument fixture_doc{url("fixture"),"migrationneedle terminal fixture retained","text/plain","migration fixture job"};
    CHECK(store.complete(*fixture_lease,fixture_doc,parse_document(fixture_doc.raw,fixture_doc.content_type,fixture_doc.title)));jobs.set("terminal-fixture",fixture);
    const auto completed=store.read_many_submit("alice",{url("terminal")},"terminal-key",{}).at("job_id").str();const auto lease=store.lease_next_read();CHECK(lease&&lease->job_id==completed);web.execute_read(*lease);CHECK(store.job_get("alice",completed).at("state").str()=="succeeded");jobs.set("terminal-read",completed);
    const auto crawl=web.submit_crawl("alice",crawl_args("crawl-key",url("crawl-alice"))).at("job_id").str();drain(web);CHECK(store.job_get("alice",crawl).at("state").str()=="succeeded");jobs.set("terminal-crawl",crawl);
    worker.allow_all=true;const auto bob=web.submit_crawl("bob",crawl_args("bob-crawl-key",url("crawl-bob"))).at("job_id").str();drain(web);CHECK(store.job_get("bob",bob).at("state").str()=="succeeded");ids.set("bob_job",bob);worker.allow_all=false;
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache")==2);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM robots_cache WHERE json_extract(record_json,'$.state')='allow_all'")==1);
    const auto unknown=store.read_many_submit("alice",{url("unknown")},"unknown-key",{}).at("job_id").str();
    CHECK(store.lease_next_read().has_value());worker.now+=26001;store.settle_interruptions();
    CHECK(store.job_get("alice",unknown).at("usage").at("unknown_requests_charged").num()>0);jobs.set("unknown-read",unknown);
    const auto paused_map=web.submit_map("alice",crawl_args("paused-map-key",url("paused-map"))).at("job_id").str();store.job_control("alice",paused_map,"pause");jobs.set("paused-map",paused_map);
    jobs.set("queued-map",web.submit_map("alice",crawl_args("queued-map-key",url("queued-map"))).at("job_id"));
    const auto active=store.read_many_submit("alice",{url("active")},"active-key",{}).at("job_id").str();const auto abandoned=store.lease_next_read();CHECK(abandoned&&abandoned->job_id==active);jobs.set("active-read",active);
    jobs.set("queued-read",store.read_many_submit("alice",{url("queued")},"queued-key",{}).at("job_id"));
    const auto paused=store.read_many_submit("alice",{url("paused")},"paused-key",{}).at("job_id").str();store.job_control("alice",paused,"pause");jobs.set("paused-read",paused);
    const auto paused_crawl=web.submit_crawl("alice",crawl_args("paused-crawl-key",url("paused-crawl"))).at("job_id").str();store.job_control("alice",paused_crawl,"pause");jobs.set("paused-crawl",paused_crawl);
    ids.set("local_session",store.search("alice",parse_query(Json::string("migrationneedle"))).at("session_id"));
    const auto local_first=store.page("alice",ids.at("local_session").str(),"",1);CHECK(local_first.at("has_more").flag());ids.set("local_cursor",local_first.at("next_cursor"));
    const auto search=web.submit_search("alice",session_args()).at("session_id").str();const auto search_lease=web.lease_search();CHECK(search_lease.has_value());web.execute_search(*search_lease);CHECK(store.session_get("alice",search).at("state").str()=="completed");ids.set("web_session",search);ids.set("web_version",store.session_get("alice",search).at("result_set_version"));
    store.observe_web_publication("alice",url("noindex-web"),url("noindex-web"),policy(false,false),store.publication_fence("alice",url("noindex-web")));
    store.observe_web_publication("alice",url("nosnippet-web"),url("nosnippet-web"),policy(true,false),store.publication_fence("alice",url("nosnippet-web")));
    const auto revision=store.ingest("alice",{url("import"),"migrationneedle revisiontwo new canonical body","text/plain","migration current import"});snapshots.set("import-current",revision.at("snapshot_id"));CHECK(revision.at("snapshot_id").str()!=imported.at("snapshot_id").str());
    const auto revoked=store.ingest("retired",{url("retired"),"migrationneedle revoked principal body","text/plain","retired"});ids.set("revoked_snapshot",revoked.at("snapshot_id"));CHECK(store.revoke_principal("migration-admin","retired").at("revoked").flag());
    ids.set("snapshots",snapshots);ids.set("evidence",proofs);ids.set("jobs",jobs);record.set("ids",ids);record.set("clock_ms",worker.now);
    record.set("observations",observations(store,ids));
    CHECK(record.at("observations").at("local_cursor_page").at("hits").size()==1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM cursors")>=1);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM document_publication WHERE restriction_generation>0")>=2);
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM principal_revocations")==1);
    CHECK(store.deletion_generation("alice",url("deleted"))>0);
    record.set("legacy_table_footprints",footprints(store.raw_handle()));
    record.set("settings",rows(store.raw_handle(),"SELECT key,value FROM settings ORDER BY key"));
    record.set("word_index_membership",rows(store.raw_handle(),"SELECT document_id,owner FROM content_fts ORDER BY owner,document_id"));
    record.set("backup",store.backup(root/"quarantined-old-backup.db"));
    CHECK(rows(store.raw_handle(),"PRAGMA integrity_check").at(0).at(0).str()=="ok");CHECK(rows(store.raw_handle(),"PRAGMA foreign_key_check").size()==0);
  }
  const auto before_backup_attempt=closed_state(root/"quarantined-old-backup.db");
  rejects([&]{Store forbidden(root/"quarantined-old-backup.db",[&]{return worker.now;});},"E_RESTORE_QUARANTINED");
  same(closed_state(root/"quarantined-old-backup.db"),before_backup_attempt,"old backup business state after quarantine denial");
  record.set("closed_database",closed_state(root/"store.db"));record.set("quarantined_backup",closed_state(root/"quarantined-old-backup.db"));
  same(record.at("closed_database").at("table_footprints"),record.at("legacy_table_footprints"),"old library fully closed row digests");
  record.set("worker_fixture_calls",static_cast<std::int64_t>(worker.calls.size()));record.set("assertions_before_report_write",static_cast<std::int64_t>(assertions));record.set("external_network_requests",std::int64_t{0});
  save(root/"old_creation.json",record);std::cout<<"CREATE real-442 schema=13 marker="<<old_marker<<" checks="<<assertions<<" old_tables="<<record.at("legacy_table_footprints").keys().size()<<" external_network_requests=0\n";
}
void verify_new(const std::filesystem::path& root) {
  const auto old=load(root/"old_creation.json"),ids=old.at("ids");Worker worker;worker.now=old.at("clock_ms").num();worker.validators=true;
  same(closed_state(root/"store.db"),old.at("closed_database"),"pristine old database before new library open");
  auto report=Json::object();const auto backup_before=closed_state(root/"quarantined-old-backup.db");
  rejects([&]{Store forbidden(root/"quarantined-old-backup.db",[&]{return worker.now;});},"E_RESTORE_QUARANTINED");
  same(closed_state(root/"quarantined-old-backup.db"),backup_before,"quarantined old backup was not migrated");
  report.set("quarantine_precedes_index_migration",true);
  {
    Store store(root/"store.db",[&]{return worker.now;});CHECK(scalar(store.raw_handle(),"PRAGMA user_version")==13);
    CHECK(rows(store.raw_handle(),"SELECT value FROM settings WHERE key='fts_tokenizer_version'").at(0).at(0).str()==new_marker);
    const auto previous=old.at("legacy_table_footprints"),current=footprints(store.raw_handle(),&previous);save(root/"after_migration_legacy_footprints.json",current);
    CHECK(current.keys().size()==previous.keys().size());
    for(const auto& table:previous.keys()) {
      same(current.at(table).at("columns"),previous.at(table).at("columns"),table+" legacy column list");
      if(table!="settings")same(current.at(table),previous.at(table),table+" full legacy rows");
    }
    const auto settings=rows(store.raw_handle(),"SELECT key,value FROM settings ORDER BY key");CHECK(settings.size()==old.at("settings").size());std::size_t marker_changes=0;
    for(std::size_t i=0;i<settings.size();++i){const auto old_row=old.at("settings").at(i),row=settings.at(i);CHECK(row.at(0).str()==old_row.at(0).str());
      if(row.at(0).str()=="fts_tokenizer_version"){CHECK(old_row.at(1).str()==old_marker);CHECK(row.at(1).str()==new_marker);++marker_changes;}else same(row,old_row,"unchanged settings row");}
    CHECK(marker_changes==1);
    same(rows(store.raw_handle(),"SELECT document_id,owner FROM content_fts ORDER BY owner,document_id"),old.at("word_index_membership"),"existing word index membership");
    same(rows(store.raw_handle(),"SELECT document_id,owner FROM substring_fts ORDER BY owner,document_id"),old.at("word_index_membership"),"migrated substring index membership");
    CHECK(scalar(store.raw_handle(),"SELECT COUNT(*) FROM substring_fts f JOIN documents d ON d.rowid=f.rowid WHERE f.document_id<>d.id OR f.owner<>d.owner")==0);
    report.set("new_tables",rows(store.raw_handle(),"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'substring_fts%' ORDER BY name"));
    report.set("preserved_existing_tables",static_cast<std::int64_t>(current.keys().size()));report.set("permitted_existing_row_changes","settings.fts_tokenizer_version only");
    WebService web(store,worker,SourceRegistry(source_manifest()),SiteRegistry(site_manifest()));
    same(observations(store,ids),old.at("observations"),"public snapshots evidence frozen pages cursors jobs accounting");
    CHECK(worker.calls.empty());
    rejects([&]{store.snapshot("alice",ids.at("deleted_snapshot").str());},"E_NOT_FOUND");
    rejects([&]{store.snapshot("retired",ids.at("revoked_snapshot").str());},"E_PRINCIPAL_REVOKED");
    rejects([&]{store.snapshot("bob",ids.at("snapshots").at("import").str());},"E_NOT_FOUND");
    rejects([&]{store.evidence_get("bob",ids.at("evidence").at("web").str());},"E_NOT_FOUND");
    rejects([&]{store.page("bob",ids.at("local_session").str(),ids.at("local_cursor").str(),1);},"E_NOT_FOUND");
    CHECK(store.read_many_submit("alice",{url("archive")},"permanent-archive",{}).at("job_id").str()==ids.at("jobs").at("archived").str());
    CHECK(store.read_many_submit("alice",{url("terminal")},"terminal-key",{}).at("reused").flag());
    CHECK(store.job_create("alice","offline-complete","permanent-fixture").at("job_id").str()==ids.at("jobs").at("terminal-fixture").str());
    rejects([&]{store.read_many_submit("alice",{url("wrong")},"permanent-archive",{});},"E_IDEMPOTENCY_CONFLICT");
    rejects([&]{store.job_create("alice","different-fixture","permanent-fixture");},"E_IDEMPOTENCY_CONFLICT");
    CHECK(web.submit_search("alice",session_args()).at("reused").flag());CHECK(!web.lease_search());
    CHECK(store.job_get("alice",ids.at("jobs").at("unknown-read").str()).at("usage").at("unknown_requests_charged").num()>0);
    CHECK(store.job_get("alice",ids.at("jobs").at("active-read").str()).at("usage").at("reserved_requests").num()>0);
    const auto after_public=footprints(store.raw_handle(),&previous);
    for(const auto& table:previous.keys())same(after_public.at(table),current.at(table),table+" unchanged by replay and observational verification");
    // Exercise current-revision and publication filtering only after the exact
    // upgrade/replay preservation boundary above; these searches create sessions.
    const auto fresh=store.search("alice",parse_query(Json::parse(R"({"op":"phrase","value":"revisiontwo new"})")));
    const auto current_page=store.page("alice",fresh.at("session_id").str(),"",100);CHECK(current_page.at("hits").size()==1);CHECK(current_page.at("hits").at(0).at("snapshot_id").str()==ids.at("snapshots").at("import-current").str());
    const auto old_phrase=store.search("alice",parse_query(Json::parse(R"({"op":"phrase","value":"achine learn"})")));CHECK(old_phrase.at("frozen_count").num()==0);
    const auto all=store.search("alice",parse_query(Json::string("migrationneedle")));const auto page=store.page("alice",all.at("session_id").str(),"",100);bool saw_nosnippet=false;
    for(std::size_t i=0;i<page.at("hits").size();++i){const auto hit=page.at("hits").at(i);CHECK(hit.at("document_id").str()!=ids.at("noindex_document").str());CHECK(hit.at("document_id").str()!=ids.at("deleted_document").str());if(hit.at("document_id").str()==ids.at("nosnippet_document").str()){saw_nosnippet=true;CHECK(hit.at("snippet").str().empty());}}
    CHECK(saw_nosnippet);CHECK(worker.calls.empty());
    CHECK(rows(store.raw_handle(),"PRAGMA integrity_check").at(0).at(0).str()=="ok");CHECK(rows(store.raw_handle(),"PRAGMA foreign_key_check").size()==0);
    report.set("migration_preservation_and_public_replay_passed",true);report.set("fresh_search_uses_current_revision_and_publication",true);
  }
  report.set("closed_database",closed_state(root/"store.db"));report.set("assertions_before_report_write",static_cast<std::int64_t>(assertions));report.set("external_network_requests",std::int64_t{0});save(root/"new_verification.json",report);
  std::cout<<"VERIFY real-442 upgraded schema=13 marker="<<new_marker<<" checks="<<assertions<<" existing_tables="<<old.at("legacy_table_footprints").keys().size()<<" external_network_requests=0\n";
}
void reject_upgraded(const std::filesystem::path& root) {
  const auto old=load(root/"old_creation.json");const auto before=closed_state(root/"store.db");CHECK(readonly_metadata(root/"store.db").at("schema").num()==13);
  rejects([&]{Store forbidden(root/"store.db",[&]{return old.at("clock_ms").num();});},"E_SCHEMA_VERSION");
  same(closed_state(root/"store.db"),before,"old program refuses new format without business/catalog changes");
  auto report=Json::object();report.set("expected_error","E_SCHEMA_VERSION");report.set("business_rows_and_catalog_unchanged",true);report.set("schema",std::int64_t{13});report.set("assertions_before_report_write",static_cast<std::int64_t>(assertions));save(root/"old_binary_rejection.json",report);
  std::cout<<"REJECT old-442 new-format=E_SCHEMA_VERSION checks="<<assertions<<"\n";
}
}
int main(int argc,char** argv) {
  try { CHECK(argc==3);const std::string mode=argv[1];const std::filesystem::path root=argv[2];
    if(mode=="create")create_old(root);else if(mode=="verify")verify_new(root);else if(mode=="reject-upgraded")reject_upgraded(root);else throw std::runtime_error("unknown mode");return 0;
  }catch(const std::exception& e){std::cerr<<"independent P1-3 migration failed after "<<assertions<<" checks: "<<e.what()<<'\n';return 1;}
}
