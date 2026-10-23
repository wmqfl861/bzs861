#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/contracts.hpp"
#include "bzs/search_modes.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <limits>
#include <map>
#include <set>

namespace bzs {
namespace {
void check(int rc) {
  if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
  if((rc&255)==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
  if((rc&255)==SQLITE_BUSY||(rc&255)==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
  throw Error("E_STORAGE","Search session storage operation failed",500);
}
void sql(sqlite3* db,const char* statement) { check(sqlite3_exec(db,statement,nullptr,nullptr,nullptr)); }
struct Statement {
  sqlite3_stmt* p=nullptr;
  Statement(sqlite3* db,const char* query) { check(sqlite3_prepare_v2(db,query,-1,&p,nullptr)); }
  ~Statement() { sqlite3_finalize(p); }
  Statement(const Statement&)=delete;Statement& operator=(const Statement&)=delete;
  void bind(int i,const std::string& value) { check(sqlite3_bind_text(p,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
  void bind(int i,std::int64_t value) { check(sqlite3_bind_int64(p,i,value)); }
  bool step() { const auto rc=sqlite3_step(p);check(rc);return rc==SQLITE_ROW; }
  std::int64_t num(int i) const { return sqlite3_column_int64(p,i); }
  std::string text(int i)const { const auto* s=sqlite3_column_text(p,i);return s?std::string(reinterpret_cast<const char*>(s),static_cast<std::size_t>(sqlite3_column_bytes(p,i))):""; }
};
struct Transaction {
  sqlite3* db;bool done=false;
  explicit Transaction(sqlite3* p):db(p) { sql(db,"BEGIN IMMEDIATE"); }
  ~Transaction() { if(!done)sqlite3_exec(db,"ROLLBACK",nullptr,nullptr,nullptr); }
  void commit() { sql(db,"COMMIT");done=true; }
};
std::int64_t scalar(sqlite3* db,const char* query) { Statement s(db,query);require(s.step(),"E_STORAGE","Missing session state",500);return s.num(0); }
constexpr std::int64_t stream_ttl=3600000,event_ttl=600000,max_events=64;
std::string url_key(const std::string& url) { try { return map_url(url); }catch(const Error&) { return url; } }
std::string search_registry_identity(const SourceRegistry& registry) {
  // File-backed registries bind the original bytes. Embedded registries must
  // also bind the effective ranking/review configuration (their file hash is empty).
  auto identity=Json::object(),sources=Json::array();identity.set("file_sha256",registry.manifest_digest());identity.set("ranking","family_rrf_k60_v1");
  for(const auto& source:registry.all()) {
    auto value=Json::object();value.set("id",source.id);value.set("weight",source.weight);value.set("family",source.family);
    value.set("default",source.by_default);value.set("enabled",source.enabled);value.set("contract",source.contract);
    value.set("cooldown_blocked_ms",source.cooldown_blocked_ms);value.set("cooldown_rate_ms",source.cooldown_rate_ms);value.set("cooldown_error_ms",source.cooldown_error_ms);sources.push(value);
  }
  identity.set("sources",sources);return sha256(identity.canonical());
}
void capacity(sqlite3* db,const std::string& owner) {
  Statement count(db,"SELECT (SELECT COUNT(*) FROM search_streams WHERE owner=?)+(SELECT COUNT(*) FROM sessions WHERE owner=? AND id NOT IN (SELECT id FROM search_streams)),(SELECT COUNT(*) FROM search_streams)+(SELECT COUNT(*) FROM sessions WHERE id NOT IN (SELECT id FROM search_streams)),(SELECT COUNT(*) FROM search_stream_receipts WHERE owner=?),(SELECT COUNT(*) FROM search_stream_receipts)");
  count.bind(1,owner);count.bind(2,owner);count.bind(3,owner);count.step();
  require(count.num(0)<64&&count.num(1)<256,"E_SESSION_BACKPRESSURE","Active search session capacity reached; wait for expiry",429);
  require(count.num(2)<10000&&count.num(3)<100000,"E_SESSION_BACKPRESSURE","Retained search identity capacity reached",429);
}
Json normalized_request(const Json& input,const SourceRegistry& registry,std::int64_t now) {
  input.allowed({"query","sources","category","limit","per_source","language","timeout_ms","deadline_ms","idempotency_key","mode","max_sources"});
  auto out=Json::object();const auto query=required_string(input,"query",1000);require(valid_utf8(query),"E_UTF8","Query must be UTF-8");out.set("query",query);
  const auto key=required_string(input,"idempotency_key",128);require_identifier(key);out.set("idempotency_key",key);
  const auto mode=search_mode(input,true);out.set("limit",mode.limit);out.set("per_source",mode.per_source);
  out.set("timeout_ms",mode.timeout_ms);out.set("deadline_ms",mode.deadline_ms);
  if(input.has("language")) {
    auto language=required_string(input,"language",16);
    require(std::all_of(language.begin(),language.end(),[](char c){return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||c=='-'||c=='_';}),"E_SCHEMA","Invalid language tag");out.set("language",language);
  }
  auto sources=Json::array();std::set<std::string> seen;
  const auto add=[&](const SourceSpec& s) { if(seen.insert(s.id).second)sources.push(Json::string(s.id)); };
  if(input.has("sources")) {
    auto ids=input.at("sources");require(ids.is_array()&&ids.size()>0&&ids.size()<=20,"E_SCHEMA","Require 1..20 sources");
    for(std::size_t i=0;i<ids.size();++i) { const auto* spec=registry.find(ids.at(i).str());require(spec&&spec->enabled,"E_SCHEMA","Unknown or disabled source");add(*spec); }
  } else {
    const auto category=input.has("category")?required_string(input,"category",32):"web";
    for(const auto& s:registry.all())if(s.enabled&&(category=="all"?s.by_default:category=="web"?(s.by_default&&(s.capability=="web"||s.capability=="encyclopedia")):s.capability==category))add(s);
  }
  auto omitted=Json::array();
  if(input.has("sources"))require(sources.size()<=static_cast<std::size_t>(mode.max_sources),"E_SCHEMA","Explicit sources exceed the selected mode source budget");
  else if(mode.explicit_mode||mode.explicit_source_limit) {
    auto selected=Json::array();for(std::size_t i=0;i<sources.size();++i) { if(i<static_cast<std::size_t>(mode.max_sources))selected.push(sources.at(i));else omitted.push(sources.at(i)); }sources=selected;
  }
  require(sources.size()>0&&sources.size()<=20,"E_SCHEMA","Require 1..20 approved sources");
  for(std::size_t i=0;i<sources.size();++i)registry.find(sources.at(i).str())->require_review(now);
  out.set("sources",sources);
  if(mode.explicit_mode||mode.explicit_source_limit) {
    if(mode.explicit_mode)out.set("mode",mode.name);
    out.set("max_sources",mode.max_sources);
    out.set("effective_budget",mode.budget(static_cast<std::int64_t>(sources.size())));out.set("omitted_sources",omitted);
  }
  return out;
}
}

void Store::migrate_sessions_locked() {
  sql(db_,R"SQL(
CREATE TABLE IF NOT EXISTS search_stream_receipts(id TEXT PRIMARY KEY,owner TEXT NOT NULL,idempotency_key TEXT,
  fingerprint TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,used_requests INTEGER NOT NULL DEFAULT 0,unknown_requests INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,
  UNIQUE(owner,idempotency_key));
CREATE INDEX IF NOT EXISTS search_receipt_owner ON search_stream_receipts(owner);
CREATE TABLE IF NOT EXISTS search_streams(id TEXT PRIMARY KEY REFERENCES search_stream_receipts(id),owner TEXT NOT NULL,auth_epoch INTEGER NOT NULL,
  kind TEXT NOT NULL,state TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,deadline INTEGER NOT NULL,
  request_json TEXT NOT NULL DEFAULT '{}',manifest_sha256 TEXT NOT NULL DEFAULT '',contracts_json TEXT NOT NULL DEFAULT '[]',
  next_source INTEGER NOT NULL DEFAULT 0,lease_until INTEGER NOT NULL DEFAULT 0,used_requests INTEGER NOT NULL DEFAULT 0,unknown_requests INTEGER NOT NULL DEFAULT 0,
  last_dispatch INTEGER NOT NULL DEFAULT 0,latest_version TEXT NOT NULL DEFAULT '',created_delete_sequence INTEGER NOT NULL DEFAULT 0,
  publication_sequence INTEGER NOT NULL DEFAULT 0,site_generation INTEGER NOT NULL DEFAULT 0,next_event INTEGER NOT NULL DEFAULT 1,error_code TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS search_stream_owner ON search_streams(owner,expires_at);
CREATE INDEX IF NOT EXISTS search_stream_queue ON search_streams(kind,state,last_dispatch);
CREATE TABLE IF NOT EXISTS search_stream_sources(session_id TEXT NOT NULL REFERENCES search_streams(id) ON DELETE CASCADE,position INTEGER NOT NULL,
  source_id TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',payload_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(session_id,position));
CREATE TABLE IF NOT EXISTS search_versions(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES search_streams(id) ON DELETE CASCADE,created_at INTEGER NOT NULL,partial INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS search_versions_session ON search_versions(session_id);
CREATE TABLE IF NOT EXISTS search_result_rows(version_id TEXT NOT NULL REFERENCES search_versions(id) ON DELETE CASCADE,position INTEGER NOT NULL,
  url TEXT NOT NULL,url_key TEXT NOT NULL,hit_json TEXT NOT NULL,publication_sequence INTEGER NOT NULL,site_generation INTEGER NOT NULL,site_bindings TEXT NOT NULL,
  PRIMARY KEY(version_id,position));
CREATE TABLE IF NOT EXISTS search_event_log(session_id TEXT NOT NULL REFERENCES search_streams(id) ON DELETE CASCADE,event_id INTEGER NOT NULL,
  kind TEXT NOT NULL,version_id TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,PRIMARY KEY(session_id,event_id));
CREATE TABLE IF NOT EXISTS search_page_cursors(token TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES search_streams(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL REFERENCES search_versions(id) ON DELETE CASCADE,position INTEGER NOT NULL,UNIQUE(version_id,position));
INSERT OR IGNORE INTO settings(key,value)VALUES('search_dispatch_sequence','0');
)SQL");
}
void Store::stream_receipt_locked(const std::string& id) {
  Statement s(db_,"UPDATE search_stream_receipts SET state=(SELECT state FROM search_streams WHERE id=?),used_requests=(SELECT used_requests FROM search_streams WHERE id=?),unknown_requests=(SELECT unknown_requests FROM search_streams WHERE id=?) WHERE id=?");
  for(int i=1;i<=4;++i)s.bind(i,id);
  s.step();
}
void Store::stream_event_locked(const std::string& id,const std::string& kind,const std::string& version) {
  Statement state(db_,"SELECT next_event FROM search_streams WHERE id=?");state.bind(1,id);require(state.step(),"E_STORAGE","Missing event stream",500);const auto event_id=state.num(0);
  require(event_id>0&&event_id<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Event sequence exhausted",500);
  Statement add(db_,"INSERT INTO search_event_log VALUES(?,?,?,?,?)");add.bind(1,id);add.bind(2,event_id);add.bind(3,kind);add.bind(4,version);add.bind(5,clock_());add.step();
  Statement next(db_,"UPDATE search_streams SET next_event=next_event+1 WHERE id=?");next.bind(1,id);next.step();
  Statement prune(db_,"DELETE FROM search_event_log WHERE session_id=? AND (event_id<=? OR created_at<=?)");prune.bind(1,id);prune.bind(2,event_id-max_events);prune.bind(3,clock_()-event_ttl);prune.step();
}
void Store::local_stream_locked(const std::string& owner,const std::string& id) {
  Statement exists(db_,"SELECT 1 FROM search_streams WHERE id=? AND owner=?");exists.bind(1,id);exists.bind(2,owner);if(exists.step())return;
  Statement local(db_,"SELECT auth_epoch,created_at,expires_at FROM sessions WHERE id=? AND owner=?");local.bind(1,id);local.bind(2,owner);if(!local.step())return;
  // Local sessions already occupy one slot. Only the durable identity bound is new.
  Statement count(db_,"SELECT (SELECT COUNT(*) FROM search_stream_receipts WHERE owner=?),(SELECT COUNT(*) FROM search_stream_receipts),(SELECT COUNT(*) FROM search_streams)+(SELECT COUNT(*) FROM sessions WHERE id NOT IN (SELECT id FROM search_streams)),(SELECT COUNT(*) FROM search_streams WHERE owner=?)+(SELECT COUNT(*) FROM sessions WHERE owner=? AND id NOT IN (SELECT id FROM search_streams))");count.bind(1,owner);count.bind(2,owner);count.bind(3,owner);count.step();
  require(count.num(0)<10000&&count.num(1)<100000&&count.num(2)<=256&&count.num(3)<=64,"E_SESSION_BACKPRESSURE","Search identity or event capacity reached",429);
  Statement receipt(db_,"INSERT INTO search_stream_receipts(id,owner,state,created_at)VALUES(?,?,'completed',?)");receipt.bind(1,id);receipt.bind(2,owner);receipt.bind(3,local.num(1));receipt.step();
  Statement stream(db_,"INSERT INTO search_streams(id,owner,auth_epoch,kind,state,created_at,expires_at,deadline,latest_version)VALUES(?,?,?,'local','completed',?,?,?,?)");
  stream.bind(1,id);stream.bind(2,owner);stream.bind(3,local.num(0));stream.bind(4,local.num(1));stream.bind(5,local.num(2));stream.bind(6,local.num(2));stream.bind(7,id);stream.step();stream_event_locked(id,"completed",id);
}
Json Store::stream_locked(const std::string& owner,const std::string& id) {
  require_principal_active_locked(owner);
  require_identifier(owner);local_stream_locked(owner,id);
  Statement s(db_,"SELECT kind,state,auth_epoch,expires_at,latest_version,next_event,used_requests,unknown_requests,error_code,deadline,request_json FROM search_streams WHERE id=? AND owner=?");s.bind(1,id);s.bind(2,owner);
  if(!s.step()) {
    Statement old(db_,"SELECT 1 FROM search_stream_receipts WHERE id=? AND owner=?");old.bind(1,id);old.bind(2,owner);
    if(old.step())throw Error("E_SESSION_EXPIRED","Session expired; repeat the query with a new idempotency key",410);
    throw Error("E_NOT_FOUND","Search session not found",404);
  }
  require(s.num(3)>clock_(),"E_SESSION_EXPIRED","Session expired; repeat the query with a new idempotency key",410);
  require(s.num(2)==epoch_locked(owner),"E_SESSION_INVALIDATED","Permissions changed; repeat the query",409);
  auto out=Json::object();out.set("session_id",id);out.set("kind",s.text(0));out.set("state",s.text(1));out.set("result_set_version",s.text(4));
  out.set("last_event_id",s.num(5)-1);out.set("expires_at_ms",s.num(3));out.set("deadline_ms",s.num(9));
  out.set("dispatched_source_attempts",s.num(6));out.set("unknown_source_attempts",s.num(7));out.set("model_calls",std::int64_t{0});
  if(s.text(0)=="web") {
    const auto request=Json::parse(s.text(10),16384);out.set("effective_mode",request.has("mode")?request.at("mode"):Json::string("legacy"));
    out.set("effective_budget",request.has("effective_budget")?request.at("effective_budget"):search_mode(request,true).budget(static_cast<std::int64_t>(request.at("sources").size())));
    out.set("omitted_sources",request.has("omitted_sources")?request.at("omitted_sources"):Json::array());
  }
  if(!s.text(8).empty())out.set("error_code",s.text(8));
  out.set("event_retention_ms",event_ttl);out.set("max_retained_events",max_events);out.set("poll_after_ms",std::int64_t{250});return out;
}
void Store::settle_searches_locked() {
  Statement rows(db_,"SELECT id,state,lease_until,deadline,auth_epoch,owner,expires_at FROM search_streams WHERE kind='web' AND state IN ('queued','running') ORDER BY id LIMIT 256");
  struct Change { std::string id;bool terminal;std::string error;bool unknown; };std::vector<Change> changes;
  const auto now=clock_();
  while(rows.step()) {
    const bool terminal=rows.num(3)<=now||rows.num(6)<=now||rows.num(4)!=epoch_locked(rows.text(5));
    const bool lost=rows.text(1)=="running"&&rows.num(2)<=now;
    if(terminal||lost)changes.push_back({rows.text(0),terminal,rows.num(4)!=epoch_locked(rows.text(5))?"E_POLICY_CHANGED":terminal?"E_DEADLINE":"E_SEARCH_OUTCOME_UNKNOWN",rows.text(1)=="running"});
  }
  for(const auto& change:changes) {
    Statement source(db_,"UPDATE search_stream_sources SET state='unknown' WHERE session_id=? AND state='running'");source.bind(1,change.id);source.step();
    Statement remaining(db_,"SELECT COUNT(*) FROM search_stream_sources WHERE session_id=? AND state='pending'");remaining.bind(1,change.id);remaining.step();
    const auto state=change.terminal?"failed":remaining.num(0)?"queued":"completed";
    Statement update(db_,"UPDATE search_streams SET state=?,epoch=epoch+1,lease_until=0,unknown_requests=unknown_requests+?,error_code=? WHERE id=?");
    update.bind(1,state);update.bind(2,change.unknown?1:0);update.bind(3,change.error);update.bind(4,change.id);update.step();stream_event_locked(change.id,change.terminal?"failed":"source_unknown");stream_receipt_locked(change.id);
  }
  // Payloads may expire; compact identity and charged/unknown counters remain.
  Statement expire(db_,"DELETE FROM search_streams WHERE expires_at<=? AND state NOT IN ('queued','running')");expire.bind(1,now);expire.step();
  Statement local(db_,"DELETE FROM sessions WHERE expires_at<=?");local.bind(1,now);local.step();
}
Json Store::search_submit(const std::string& owner,const Json& input,const SourceRegistry& registry) {
  (void)source_publication_capture(registry,Json::array());
  const auto request=normalized_request(input,registry,clock_());const auto encoded=request.canonical();require(encoded.size()<=16384,"E_SCHEMA","Search request too large");
  std::lock_guard lock(mutex_);Transaction tx(db_);require_principal_active_locked(owner);const auto epoch=epoch_locked(owner);settle_searches_locked();refresh_site_publication_locked();
  const auto source_generation=source_capture_locked(registry,request.at("sources"));
  const auto source_identity=search_registry_identity(registry);const auto fingerprint=sha256(encoded+"\n"+source_identity);
  Statement existing(db_,"SELECT id,fingerprint FROM search_stream_receipts WHERE owner=? AND idempotency_key=?");existing.bind(1,owner);existing.bind(2,request.at("idempotency_key").str());
  if(existing.step()) {
    require(existing.text(1)==fingerprint,"E_IDEMPOTENCY_CONFLICT","Search key reused with different inputs or sources",409);
    auto result=stream_locked(owner,existing.text(0));result.set("reused",true);tx.commit();return result;
  }
  capacity(db_,owner);const auto id=random_id();const auto now=clock_();auto contracts=Json::array();const auto ids=request.at("sources");
  for(std::size_t i=0;i<ids.size();++i)contracts.push(registry.find(ids.at(i).str())->contract);
  const auto contract_json=contracts.canonical();require(contract_json.size()<=128*1024,"E_SCHEMA","Source contracts too large");
  const auto sequence=scalar(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='search_dispatch_sequence'");require(sequence<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Dispatch sequence exhausted",500);
  sql(db_,"UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='search_dispatch_sequence'");
  Statement receipt(db_,"INSERT INTO search_stream_receipts(id,owner,idempotency_key,fingerprint,state,created_at)VALUES(?,?,?,?,'queued',?)");
  receipt.bind(1,id);receipt.bind(2,owner);receipt.bind(3,request.at("idempotency_key").str());receipt.bind(4,fingerprint);receipt.bind(5,now);receipt.step();
  Statement add(db_,"INSERT INTO search_streams(id,owner,auth_epoch,kind,state,created_at,expires_at,deadline,request_json,manifest_sha256,contracts_json,created_delete_sequence,publication_sequence,site_generation)VALUES(?,?,?,'web','queued',?,?,?,?,?,?,?,?,?)");
  add.bind(1,id);add.bind(2,owner);add.bind(3,epoch);add.bind(4,now);add.bind(5,now+stream_ttl);add.bind(6,now+request.at("deadline_ms").num());add.bind(7,encoded);add.bind(8,source_identity);add.bind(9,contract_json);add.bind(10,delete_sequence_locked());add.bind(11,scalar(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'"));add.bind(12,site_publication_cached_generation_);add.step();
  Statement queued(db_,"UPDATE search_streams SET last_dispatch=?,source_generation=? WHERE id=?");queued.bind(1,sequence+1);queued.bind(2,source_generation);queued.bind(3,id);queued.step();
  for(std::size_t i=0;i<ids.size();++i) { Statement source(db_,"INSERT INTO search_stream_sources(session_id,position,source_id)VALUES(?,?,?)");source.bind(1,id);source.bind(2,static_cast<std::int64_t>(i));source.bind(3,ids.at(i).str());source.step(); }
  stream_event_locked(id,"queued");auto out=stream_locked(owner,id);out.set("reused",false);tx.commit();return out;
}
Json Store::session_get(const std::string& owner,const std::string& id) {
  std::lock_guard lock(mutex_);Transaction tx(db_);settle_searches_locked();auto out=stream_locked(owner,id);tx.commit();return out;
}
Json Store::session_events(const std::string& owner,const std::string& id,std::int64_t after,std::int64_t limit) {
  require(after>=0&&limit>=1&&limit<=32,"E_SCHEMA","Event cursor or page limit out of range");
  std::lock_guard lock(mutex_);Transaction tx(db_);settle_searches_locked();auto out=stream_locked(owner,id);
  const auto last=out.at("last_event_id").num();require(after<=last,"E_EVENT_CURSOR","Event cursor is beyond this stream",400);
  Statement prune(db_,"DELETE FROM search_event_log WHERE session_id=? AND created_at<=?");prune.bind(1,id);prune.bind(2,clock_()-event_ttl);prune.step();
  Statement first(db_,"SELECT MIN(event_id) FROM search_event_log WHERE session_id=?");first.bind(1,id);first.step();const auto earliest=first.num(0)>0?first.num(0):last+1;
  require(after>=earliest-1,"E_EVENT_HISTORY_EXPIRED","Event history expired; get session status and fetch its available result version, or repeat the query",410);
  auto events=Json::array();Statement rows(db_,"SELECT event_id,kind,version_id,created_at FROM search_event_log WHERE session_id=? AND event_id>? ORDER BY event_id LIMIT ?");rows.bind(1,id);rows.bind(2,after);rows.bind(3,limit);auto next=after;
  while(rows.step()) { auto event=Json::object();next=rows.num(0);event.set("event_id",next);event.set("kind",rows.text(1));event.set("created_at_ms",rows.num(3));if(!rows.text(2).empty())event.set("result_set_version",rows.text(2));events.push(event); }
  // Events carry no historical titles, snippets, URLs or candidate counts.
  // Content is fetched separately and reauthorized on every page request.
  out.set("events",events);out.set("next_event_id",next);out.set("has_more",next<last);out.set("heartbeat_at_ms",clock_());tx.commit();return out;
}
Json Store::session_cancel(const std::string& owner,const std::string& id) {
  std::lock_guard lock(mutex_);Transaction tx(db_);settle_searches_locked();auto out=stream_locked(owner,id);const auto state=out.at("state").str();
  if(state=="queued"||state=="running") {
    Statement sources(db_,"UPDATE search_stream_sources SET state=CASE WHEN state='running' THEN 'unknown' ELSE 'cancelled' END WHERE session_id=? AND state IN ('pending','running')");sources.bind(1,id);sources.step();
    Statement update(db_,"UPDATE search_streams SET state='cancelled',epoch=epoch+1,lease_until=0,unknown_requests=unknown_requests+? WHERE id=?");update.bind(1,state=="running"?1:0);update.bind(2,id);update.step();
    stream_event_locked(id,"cancelled",out.at("result_set_version").str());stream_receipt_locked(id);out=stream_locked(owner,id);
  }
  out.set("completed_results_preserved",true);tx.commit();return out;
}
std::optional<SearchLease> Store::lease_next_search(const SourceRegistry& registry) {
  std::lock_guard lock(mutex_);refresh_source_publication_locked();Transaction tx(db_);settle_searches_locked();
  for(std::size_t attempts=0;attempts<256;++attempts) {
    Statement s(db_,"SELECT id,owner,epoch,manifest_sha256,request_json,deadline,source_generation FROM search_streams WHERE kind='web' AND state='queued' ORDER BY last_dispatch,created_at,id LIMIT 1");if(!s.step()) { tx.commit();return std::nullopt; }
    const auto id=s.text(0);bool approved=s.text(3)==search_registry_identity(registry);auto request=Json::parse(s.text(4),16384);
    if(approved)try { const auto ids=request.at("sources");(void)source_capture_locked(registry,ids);for(std::size_t i=0;i<ids.size();++i) { const auto* source=registry.find(ids.at(i).str());require(source,"E_SOURCE_REVIEW","Source removed",409);source->require_review(clock_());require(source_publication_mode_locked(source->id,s.num(6))<2,"E_SOURCE_REVIEW","Source permission was withdrawn",409); } }catch(const Error&) { approved=false; }
    if(!approved) { Statement fail(db_,"UPDATE search_streams SET state='failed',error_code='E_SOURCE_POLICY_CHANGED' WHERE id=?");fail.bind(1,id);fail.step();stream_event_locked(id,"failed");stream_receipt_locked(id);continue; }
    Statement source(db_,"SELECT position,source_id FROM search_stream_sources WHERE session_id=? AND state='pending' ORDER BY position LIMIT 1");source.bind(1,id);
    if(!source.step()) { Statement done(db_,"UPDATE search_streams SET state='completed' WHERE id=?");done.bind(1,id);done.step();stream_event_locked(id,"completed");stream_receipt_locked(id);continue; }
    const auto sequence=scalar(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='search_dispatch_sequence'");require(sequence<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Dispatch sequence exhausted",500);
    sql(db_,"UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='search_dispatch_sequence'");
    SearchLease lease{id,s.text(1),source.text(1),s.text(3),s.num(2)+1,source.num(0),s.num(5),request};
    const auto lease_until=std::min(lease.deadline_ms,clock_()+request.at("timeout_ms").num()+10000);
    Statement update(db_,"UPDATE search_streams SET state='running',epoch=?,lease_until=?,used_requests=used_requests+1,last_dispatch=? WHERE id=?");update.bind(1,lease.epoch);update.bind(2,lease_until);update.bind(3,sequence+1);update.bind(4,id);update.step();
    Statement dispatched(db_,"UPDATE search_stream_sources SET state='running' WHERE session_id=? AND position=?");dispatched.bind(1,id);dispatched.bind(2,lease.position);dispatched.step();stream_receipt_locked(id);tx.commit();return lease;
  }
  tx.commit();return std::nullopt;
}
bool Store::search_lease_locked(const SearchLease& lease) {
  Statement s(db_,"SELECT s.state,s.epoch,s.lease_until,s.deadline,s.auth_epoch,s.manifest_sha256,r.state,r.source_id FROM search_streams s JOIN search_stream_sources r ON r.session_id=s.id AND r.position=? WHERE s.id=? AND s.owner=?");
  s.bind(1,lease.position);s.bind(2,lease.id);s.bind(3,lease.owner);
  return s.step()&&s.text(0)=="running"&&s.num(1)==lease.epoch&&s.num(2)>clock_()&&s.num(3)>clock_()&&s.num(4)==epoch_locked(lease.owner)&&s.text(5)==lease.manifest_sha256&&s.text(6)=="running"&&s.text(7)==lease.source_id;
}
bool Store::search_lease_live(const SearchLease& lease) { std::lock_guard lock(mutex_);return search_lease_locked(lease); }
Json Store::search_bindings_locked(const std::string& owner,const std::string& url,const Json& contributing_urls) {
  auto bindings=site_bindings_locked(url,Json::object());std::set<std::string> urls;urls.insert(publication_url_identity(url));
  for(std::size_t i=0;i<contributing_urls.size()&&urls.size()<=24;++i)urls.insert(publication_url_identity(contributing_urls.at(i).str()));
  if(urls.size()>24||contributing_urls.size()>600) { bindings.set("captured_mode",std::int64_t{2});return bindings; }
  const auto candidates=urls;
  bool incomplete=false;std::int64_t records=0;
  for(const auto& candidate:candidates) {
  Statement known(db_,"SELECT d.representation_key,s.meta_json,s.site_bindings_json FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url_key=? AND s.source_kind='web' ORDER BY d.id LIMIT 25");known.bind(1,owner);known.bind(2,url_key(candidate));
  while(known.step()) {
    if(++records>24) { incomplete=true;break; }
    const auto current=site_bindings_locked(candidate,Json::parse(known.text(1),8*1024*1024));const auto chain=current.at("urls");
    for(std::size_t i=0;i<chain.size();++i)urls.insert(chain.at(i).str());
    if(!known.text(0).empty())urls.insert(publication_url_identity(known.text(0)));
    const auto stored=Json::parse(known.text(2),128*1024);
    if(stored.has("legacy_crawl_unverified")&&stored.at("legacy_crawl_unverified").flag())bindings.set("legacy_crawl_unverified",true);
    if(urls.size()>24) { incomplete=true;break; }
  }
  if(incomplete)break;
  }
  if(incomplete) { bindings.set("captured_mode",std::int64_t{2});return bindings; }
  auto values=Json::array();for(const auto& value:urls)values.push(Json::string(value));bindings.set("urls",values);return bindings;
}
std::string Store::freeze_search_locked(const std::string& id,const SourceRegistry& registry) {
  Statement state(db_,"SELECT owner,request_json,publication_sequence,site_generation,created_delete_sequence,source_generation FROM search_streams WHERE id=?");state.bind(1,id);state.step();
  const auto request=Json::parse(state.text(1),16384);std::vector<SourceHits> collected;bool partial=false;
  std::map<std::string,std::set<std::string>> contributing;
  Statement sources(db_,"SELECT source_id,state,payload_json FROM search_stream_sources WHERE session_id=? ORDER BY position");sources.bind(1,id);
  while(sources.step()) {
    if(sources.text(1)!="completed") { partial=true;continue; }
    auto payload=Json::parse(sources.text(2),256*1024);partial=partial||payload.at("partial").flag();SourceHits source;source.source_id=sources.text(0);
    const auto results=payload.at("results");
    for(std::size_t i=0;i<results.size();++i) {
      const auto hit=results.at(i);std::int64_t rank=0;const auto provenance=hit.at("sources");
      auto& urls=contributing[dedup_key(hit.at("url").str())];urls.insert(hit.at("url").str());
      if(hit.has("publication_urls")) {
        const auto original=hit.at("publication_urls");require(original.is_array()&&original.size()<=30,"E_WORKER_PROTOCOL","Invalid source publication provenance",502);
        for(std::size_t j=0;j<original.size();++j) { const auto actual=original.at(j).str();require(dedup_key(actual)==dedup_key(hit.at("url").str()),"E_WORKER_PROTOCOL","Unrelated source publication URL",502);urls.insert(actual); }
      }
      for(std::size_t j=0;j<provenance.size();++j)if(provenance.at(j).at("source_id").str()==source.source_id) {
        const auto candidate=provenance.at(j).at("rank").num();if(candidate>0&&(rank==0||candidate<rank))rank=candidate;
      }
      if(rank>0)source.hits.push_back({hit.at("url").str(),hit.at("title").str(),hit.at("snippet").str(),rank});
    }
    collected.push_back(std::move(source));
  }
  auto hits=fuse(collected,registry,static_cast<std::size_t>(request.at("limit").num()));const auto version=random_id();
  Statement add(db_,"INSERT INTO search_versions VALUES(?,?,?,?)");add.bind(1,version);add.bind(2,id);add.bind(3,clock_());add.bind(4,partial?1:0);add.step();refresh_site_publication_locked();
  for(std::size_t i=0;i<hits.size();++i) {
    auto hit=hits.at(i);auto url=hit.at("url").str();bool links_only=false;
    const auto provenance=hit.at("sources");for(std::size_t j=0;j<provenance.size();++j) {
      const auto* source=registry.find(provenance.at(j).at("source_id").str());if(source&&source->contract.at("review").at("redistribution_policy").str()=="links_only")links_only=true;
    }
    if(links_only) { hit.set("title",url);hit.set("snippet","");hit.set("snippet_kind","publication_suppressed"); }
    const auto key=url_key(url);auto actual=Json::array();for(const auto& value:contributing[dedup_key(url)])actual.push(Json::string(value));
    auto bindings=search_bindings_locked(state.text(0),url,actual);
    auto mode=std::max(source_hit_mode_locked(hit,state.num(5)),site_publication_mode_locked("web",bindings.dump(),state.num(3),url,"{}"));
    // Freeze the ceilings already in force, even when their watermarks predate
    // this session. A later complete read may enable a new version, never this one.
    const auto bound=bindings.at("urls");
    for(std::size_t j=0;j<bound.size()&&mode<2;++j) {
      const auto actual_key=url_key(bound.at(j).str());
      Statement deleted(db_,"SELECT 1 FROM documents WHERE owner=? AND url_key=? AND (deleted=1 OR delete_generation>?) LIMIT 1");deleted.bind(1,state.text(0));deleted.bind(2,actual_key);deleted.bind(3,state.num(4));if(deleted.step()) { mode=2;break; }
      Statement policy(db_,"SELECT index_allowed,snippet_allowed,restriction_generation FROM document_publication WHERE owner=? AND url_key=?");policy.bind(1,state.text(0));policy.bind(2,actual_key);
      if(policy.step()) { if(policy.num(0)==0||policy.num(2)>state.num(2))mode=2;else if(policy.num(1)==0)mode=std::max(mode,1); }
    }
    bindings.set("captured_mode",static_cast<std::int64_t>(mode));
    Statement row(db_,"INSERT INTO search_result_rows VALUES(?,?,?,?,?,?,?,?)");row.bind(1,version);row.bind(2,static_cast<std::int64_t>(i));row.bind(3,url);row.bind(4,key);row.bind(5,hit.dump());row.bind(6,state.num(2));row.bind(7,state.num(3));row.bind(8,bindings.dump());row.step();
  }
  Statement usage(db_,"SELECT (SELECT COALESCE(SUM(length(CAST(hit_json AS BLOB))+length(CAST(site_bindings AS BLOB))),0) FROM search_result_rows WHERE version_id IN (SELECT id FROM search_versions WHERE session_id=?))+(SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM search_stream_sources WHERE session_id=?)+(SELECT length(CAST(request_json AS BLOB))+length(CAST(contracts_json AS BLOB)) FROM search_streams WHERE id=?)");usage.bind(1,id);usage.bind(2,id);usage.bind(3,id);usage.step();
  require(usage.num(0)<=4*1024*1024,"E_SESSION_BACKPRESSURE","Frozen session result capacity reached",429);
  Statement latest(db_,"UPDATE search_streams SET latest_version=? WHERE id=?");latest.bind(1,version);latest.bind(2,id);latest.step();return version;
}
bool Store::complete_search(const SearchLease& lease,const Json& result,const SourceRegistry& registry) {
  require(result.is_object()&&result.has("results")&&result.at("results").is_array()&&result.at("results").size()<=50&&result.has("partial")&&result.at("partial").is_bool(),"E_WORKER_PROTOCOL","Invalid source search result",502);
  const auto payload=result.dump();require(payload.size()<=256*1024,"E_WORKER_PROTOCOL","Source result exceeds session bound",502);
  std::lock_guard lock(mutex_);refresh_source_publication_locked();Transaction tx(db_);
  if(!search_lease_locked(lease)||search_registry_identity(registry)!=lease.manifest_sha256) { tx.commit();return false; }
  Statement source_generation(db_,"SELECT source_generation FROM search_streams WHERE id=?");source_generation.bind(1,lease.id);source_generation.step();
  if(source_publication_mode_locked(lease.source_id,source_generation.num(0))>=2) {
    Statement reject(db_,"UPDATE search_streams SET state='failed',lease_until=0,error_code='E_SOURCE_POLICY_CHANGED' WHERE id=?");reject.bind(1,lease.id);reject.step();
    Statement withdraw(db_,"UPDATE search_stream_sources SET state='cancelled' WHERE session_id=? AND state IN ('pending','running')");withdraw.bind(1,lease.id);withdraw.step();
    stream_event_locked(lease.id,"failed");stream_receipt_locked(lease.id);tx.commit();return false;
  }
  Statement save(db_,"UPDATE search_stream_sources SET state='completed',payload_json=? WHERE session_id=? AND position=?");save.bind(1,payload);save.bind(2,lease.id);save.bind(3,lease.position);save.step();
  Statement count(db_,"SELECT COUNT(*) FROM search_stream_sources WHERE session_id=? AND state='pending'");count.bind(1,lease.id);count.step();const auto state=count.num(0)?"queued":"completed";
  Statement update(db_,"UPDATE search_streams SET state=?,lease_until=0 WHERE id=?");update.bind(1,state);update.bind(2,lease.id);update.step();
  const auto version=freeze_search_locked(lease.id,registry);stream_event_locked(lease.id,count.num(0)?"result_version":"completed",version);stream_receipt_locked(lease.id);tx.commit();return true;
}
void Store::fail_search(const SearchLease& lease,const std::string& error) {
  std::lock_guard lock(mutex_);Transaction tx(db_);
  if(!search_lease_locked(lease)) { tx.commit();return; }
  Statement source(db_,"UPDATE search_stream_sources SET state='unknown' WHERE session_id=? AND position=?");source.bind(1,lease.id);source.bind(2,lease.position);source.step();
  Statement count(db_,"SELECT COUNT(*) FROM search_stream_sources WHERE session_id=? AND state='pending'");count.bind(1,lease.id);count.step();
  Statement update(db_,"UPDATE search_streams SET state=?,lease_until=0,unknown_requests=unknown_requests+1,error_code=? WHERE id=?");update.bind(1,count.num(0)?"queued":"completed");update.bind(2,utf8_prefix(error,128));update.bind(3,lease.id);update.step();stream_event_locked(lease.id,"source_unknown");stream_receipt_locked(lease.id);tx.commit();
}
Json Store::session_page(const std::string& owner,const std::string& id,const std::string& version,const std::string& cursor,std::int64_t page_size) {
  require(page_size>=1&&page_size<=100,"E_SCHEMA","Page size out of range");
  std::unique_lock lock(mutex_);refresh_source_publication_locked();Transaction tx(db_);auto state=stream_locked(owner,id);
  if(state.at("kind").str()=="local") { require(version.empty()||version==id,"E_RESULT_VERSION","Version does not belong to this session",400);tx.commit();lock.unlock();return page(owner,id,cursor,page_size); }
  require(!version.empty(),"E_RESULT_VERSION","A frozen result_set_version is required",400);
  Statement v(db_,"SELECT partial FROM search_versions WHERE id=? AND session_id=?");v.bind(1,version);v.bind(2,id);require(v.step(),"E_RESULT_VERSION","Version does not belong to this session",400);
  Statement s(db_,"SELECT contracts_json,created_delete_sequence,source_generation FROM search_streams WHERE id=?");s.bind(1,id);s.step();const auto contracts=Json::parse(s.text(0),128*1024);
  for(std::size_t i=0;i<contracts.size();++i)try { (void)validate_contract("source-manifest",contracts.at(i),clock_()); }catch(const Error&) { throw Error("E_SESSION_INVALIDATED","Source review expired; repeat the query under current approved sources",409); }
  for(std::size_t i=0;i<contracts.size();++i) { const auto contract=contracts.at(i);require(source_publication_mode_locked(contract.at("id").str(),s.num(2),&contract)<2,"E_SESSION_INVALIDATED","Source publication permission was withdrawn; repeat under current approved sources",409); }
  refresh_site_publication_locked();std::int64_t pos=0;
  if(!cursor.empty()) { Statement c(db_,"SELECT position FROM search_page_cursors WHERE token=? AND session_id=? AND version_id=?");c.bind(1,cursor);c.bind(2,id);c.bind(3,version);require(c.step(),"E_CURSOR","Cursor does not belong to this session and result version",400);pos=c.num(0); }
  Statement rows(db_,"SELECT position,url,url_key,hit_json,publication_sequence,site_generation,site_bindings FROM search_result_rows WHERE version_id=? AND position>=? ORDER BY position LIMIT 51");rows.bind(1,version);rows.bind(2,pos);
  auto hits=Json::array();bool more=false;auto next=pos;
  while(rows.step()) {
    const auto frozen_bindings=Json::parse(rows.text(6),128*1024),current_bindings=search_bindings_locked(owner,rows.text(1),frozen_bindings.at("urls"));
    std::set<std::string> keys;for(const auto& bindings:{frozen_bindings,current_bindings}) { const auto urls=bindings.at("urls");for(std::size_t i=0;i<urls.size();++i)keys.insert(url_key(urls.at(i).str())); }
    bool snippet=true,hidden=false;
    for(const auto& key:keys) {
      Statement deleted(db_,"SELECT 1 FROM documents WHERE owner=? AND url_key=? AND (deleted=1 OR delete_generation>?) LIMIT 1");deleted.bind(1,owner);deleted.bind(2,key);deleted.bind(3,s.num(1));if(deleted.step()) { hidden=true;break; }
      Statement policy(db_,"SELECT index_allowed,snippet_allowed,restriction_generation FROM document_publication WHERE owner=? AND url_key=?");policy.bind(1,owner);policy.bind(2,key);
      if(policy.step()) { if(policy.num(0)==0||policy.num(2)>rows.num(4)) { hidden=true;break; }snippet=snippet&&policy.num(1)!=0; }
    }
    if(hidden)continue;
    const auto mode=std::max(site_publication_mode_locked("web",rows.text(6),rows.num(5),rows.text(1),"{}"),site_publication_mode_locked("web",current_bindings.dump(),rows.num(5),rows.text(1),"{}"));if(mode>=2)continue;snippet=snippet&&mode==0;
    if(hits.size()==static_cast<std::size_t>(page_size)) { more=true;next=rows.num(0);break; }
    auto hit=Json::parse(rows.text(3),16384);snippet=snippet&&source_hit_mode_locked(hit,s.num(2))==0;hit.set("position",rows.num(0));hit.set("body_read",false);
    if(!snippet) { hit.set("title",rows.text(1));hit.set("snippet","");hit.set("snippet_kind","publication_suppressed"); }
    hits.push(hit);next=rows.num(0)+1;
  }
  auto out=Json::object();out.set("session_id",id);out.set("result_set_version",version);out.set("hits",hits);out.set("has_more",more);out.set("partial",v.num(0)!=0);out.set("coverage","external_search_snippets_unread");
  for(const auto* field:{"effective_mode","effective_budget","omitted_sources"})if(state.has(field))out.set(field,state.at(field));
  if(more) {
    Statement c(db_,"SELECT token FROM search_page_cursors WHERE version_id=? AND position=?");c.bind(1,version);c.bind(2,next);auto token=c.step()?c.text(0):random_id();
    Statement insert(db_,"INSERT OR IGNORE INTO search_page_cursors VALUES(?,?,?,?)");insert.bind(1,token);insert.bind(2,id);insert.bind(3,version);insert.bind(4,next);insert.step();out.set("next_cursor",token);
  }
  tx.commit();return out;
}
Json WebService::submit_search(const std::string& owner,const Json& args) { return store_.search_submit(owner,args,registry_); }
std::optional<SearchLease> WebService::lease_search() { return store_.lease_next_search(registry_); }
void WebService::execute_search(const SearchLease& lease) {
  if(!store_.search_lease_live(lease))return;
  try {
    const auto started=std::chrono::steady_clock::now();
    const auto left=lease.deadline_ms-store_.now();require(left>0,"E_DEADLINE","Search deadline exhausted before dispatch",408);
    // Public request minima validate the original grant. Its remaining time
    // can be below one second; carry that absolute deadline separately so no
    // public revalidation or function-call delay rounds it up or restarts it.
    const auto deadline=started+std::chrono::milliseconds(left);
    auto args=Json::object();for(const auto* field:{"query","per_source","timeout_ms","deadline_ms","language","mode"})if(lease.request.has(field))args.set(field,lease.request.at(field));
    args.set("limit",lease.request.at("per_source"));args.set("refresh",true);auto sources=Json::array();sources.push(Json::string(lease.source_id));args.set("sources",sources);
    const auto result=search(lease.owner,args,true,deadline);store_.complete_search(lease,result,registry_);
  }catch(const Error& error) { store_.fail_search(lease,error.code); }catch(...) { store_.fail_search(lease,"E_INTERNAL"); }
}
}
