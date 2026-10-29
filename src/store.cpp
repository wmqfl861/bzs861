#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/highlight.hpp"
#include "bzs/http_cache.hpp"
#include "bzs/publication.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/sites.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <fstream>
#include <memory>
#include <limits>
#include <tuple>
#include <unordered_set>
namespace bzs {
  namespace {
    void checked(int rc, sqlite3*db) {
      if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
      int primary=rc&0xff;
      if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL", "Storage capacity exhausted", 507);
      if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY", "Storage is busy; retry later", 503);
      // Do not expose SQL text, path names or document content to clients.
      (void)db;
      throw Error("E_STORAGE", "Storage operation failed (SQLite "+std::to_string(primary)+")", 500);
    }
    void exec(sqlite3*db, const char*s) {
      checked(sqlite3_exec(db, s, nullptr, nullptr, nullptr), db);
    }
    class Stmt {
      sqlite3*db_;
      sqlite3_stmt*s_=nullptr;
      public: Stmt(sqlite3*d, const std::string&s):db_(d) {
        checked(sqlite3_prepare_v2(d, s.c_str(), -1, &s_, nullptr), d);
      }
      ~Stmt() {
        sqlite3_finalize(s_);
      }
      Stmt(const Stmt&)=delete;
      Stmt&operator=(const Stmt&)=delete;
      void bind(int i, const std::string&v) {
        checked(sqlite3_bind_text(s_, i, v.data(), static_cast<int>(v.size()), SQLITE_TRANSIENT), db_);
      }
      void bind(int i, std::int64_t v) {
        checked(sqlite3_bind_int64(s_, i, v), db_);
      }
      bool step() {
        int rc=sqlite3_step(s_);
        checked(rc, db_);
        return rc==SQLITE_ROW;
      }
      std::string text(int i)const {
        auto*p=sqlite3_column_text(s_, i);
        return p?std::string(reinterpret_cast<const char*>(p), static_cast<std::size_t>(sqlite3_column_bytes(s_, i))):"";
      }
      std::int64_t num(int i)const {
        return sqlite3_column_int64(s_, i);
      }
      double real(int i)const {
        return sqlite3_column_double(s_, i);
      }
    };
    class Tx {
      sqlite3*db_;
      bool done_=false;
      public: explicit Tx(sqlite3*d):db_(d) {
        exec(db_, "BEGIN IMMEDIATE");
      }
      ~Tx() {
        if(!done_)sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr);
      }
      void commit() {
        exec(db_, "COMMIT");
        done_=true;
      }
    };
    std::int64_t source_cooldown_until(std::int64_t now,std::int64_t delay) {
      delay=std::max<std::int64_t>(0,delay);
      return now>std::numeric_limits<std::int64_t>::max()-delay?
        std::numeric_limits<std::int64_t>::max():now+delay;
    }
    std::int64_t source_cooldown_remaining(std::int64_t until,std::int64_t now) {
      if(until<=now)return 0;
      if(now<0&&until>std::numeric_limits<std::int64_t>::max()+now)
        return std::numeric_limits<std::int64_t>::max();
      return until-now;
    }
    Json source_cooldown_proof(sqlite3* db,const std::string& id) {
      Stmt saved(db,"SELECT value FROM settings WHERE key=?");saved.bind(1,"source_cooldown_proof/"+id);
      if(!saved.step()) {
        auto value=Json::object();value.set("version",std::int64_t{1});value.set("verified_until_ms",std::int64_t{0});return value;
      }
      auto value=Json::parse(saved.text(0),4096);
      require(value.is_object()&&value.has("version")&&value.at("version").is_integer()&&value.at("version").num()==1&&
              value.has("verified_until_ms")&&value.at("verified_until_ms").is_integer()&&value.at("verified_until_ms").num()>=0,
              "E_STORAGE","Invalid source cooldown proof",500);
      return value;
    }
    void save_source_cooldown_proof(sqlite3* db,const std::string& id,const Json& value) {
      Stmt save(db,"INSERT INTO settings(key,value)VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value");
      save.bind(1,"source_cooldown_proof/"+id);save.bind(2,value.dump());save.step();
    }
    void repair_source_cooldowns(sqlite3* db,std::int64_t now,const std::string* id=nullptr) {
      struct Repair { std::string id,error;std::int64_t until;Json proof; };
      std::vector<Repair> repairs;
      {
        Stmt rows(db,id?
          "SELECT source_id,last_request_at,last_status,cooldown_until,last_error,last_ok_at FROM source_state WHERE source_id=?":
          "SELECT source_id,last_request_at,last_status,cooldown_until,last_error,last_ok_at FROM source_state");
        if(id)rows.bind(1,*id);
        while(rows.step()) {
          auto proof=source_cooldown_proof(db,rows.text(0));
          const auto verified=proof.at("verified_until_ms").num();
          // Only individually bounded reports create proof, never an unknown
          // older MAX. A clock rollback cannot revoke a proven absolute fence.
          if(rows.num(3)<=verified)continue;
          const auto event=std::max(rows.num(1),rows.num(5));
          // The old schema has no response timestamp. An attempt is not its
          // response, and a late response can replace last_status. Conservatively
          // retain ambiguous old fences, including future event/rollback state.
          constexpr auto legacy_extreme=365*source_retry_after_limit_ms;
          if(event>now||rows.num(3)<=source_cooldown_until(now,legacy_extreme)||
             rows.num(3)<=source_cooldown_until(event,legacy_extreme))continue;
          const auto maximum=source_retry_after_limit_ms*(rows.text(2)=="rate_limited"?1:3);
          // Persist one fixed deadline, anchored to the most recent known
          // event when it exists. Reading state must not renew the cooldown.
          const auto anchor=event>0?event:now;
          const auto until=std::max(verified,source_cooldown_until(anchor,maximum));
          auto audit=Json::object();audit.set("original_until_ms",rows.num(3));audit.set("repaired_until_ms",until);
          audit.set("anchor_ms",anchor);audit.set("observed_at_ms",now);audit.set("reason","unproven_legacy_backoff_over_one_year");
          proof.set("last_repair",audit);proof.set("verified_until_ms",until);
          repairs.push_back({rows.text(0),utf8_prefix("E_SOURCE_COOLDOWN_CLAMPED: unproven legacy deadline "+std::to_string(rows.num(3))+" exceeded one year; "+rows.text(4),300),until,proof});
        }
      }
      for(const auto& repair:repairs) {
        Stmt update(db,"UPDATE source_state SET cooldown_until=?,last_error=? WHERE source_id=?");
        update.bind(1,repair.until);update.bind(2,repair.error);update.bind(3,repair.id);update.step();
        save_source_cooldown_proof(db,repair.id,repair.proof);
      }
    }
    std::int64_t scalar(sqlite3*d, const std::string&sql) {
      Stmt s(d, sql);
      require(s.step(), "E_STORAGE", "Scalar missing", 500);
      return s.num(0);
    }
    void owner_valid(const std::string& owner) {
      require_identifier(owner);
    }
    constexpr std::int64_t max_raw_bytes=4*1024*1024;
    constexpr std::size_t max_text_bytes=1024*1024;
    Json quoted(const Json& snap, std::int64_t start, std::int64_t end) {
      auto text=snap.at("canonical_text").str();
      require(start>=0&&end>start&&end<=static_cast<std::int64_t>(text.size())&&end-start<=65536, "E_EVIDENCE_RANGE", "Invalid evidence range");
      require(utf8_boundary(text, static_cast<std::size_t>(start))&&utf8_boundary(text, static_cast<std::size_t>(end)), "E_EVIDENCE_RANGE", "Evidence splits a UTF-8 character");
      Json out=Json::object();
      out.set("snapshot_id", snap.at("snapshot_id"));
      out.set("extraction_id", snap.at("extraction_id"));
      out.set("document_id", snap.at("document_id"));
      out.set("url", snap.at("url"));
      out.set("start_byte", start);
      out.set("end_byte", end);
      out.set("quote", text.substr(static_cast<std::size_t>(start), static_cast<std::size_t>(end-start)));
      out.set("location_verified", true);
      out.set("semantic_support", "not_evaluated");
      out.set("content_origin", snap.at("content_origin"));
      return out;
    }
    const char* local_result_sql=R"SQL(SELECT r.position,r.document_id,r.snapshot_id,r.score,s.title,d.url,s.canonical_text,s.revision,
 s.publication_json,p.snapshot_id,COALESCE(p.restricted,0),MAX(COALESCE(p.restriction_generation,0),COALESCE(rp.restriction_generation,0),COALESCE(cp.restriction_generation,0)),r.publication_generation,rp.snapshot_id,COALESCE(rp.restricted,0),cp.snapshot_id,COALESCE(cp.restricted,0),
 CASE WHEN s.source_kind='web' THEN MAX(bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,r.representation_key),bzs_site_publication(current.source_kind,current.site_bindings_json,current.site_publication_generation,d.url,current.meta_json,'')) ELSE 0 END AS site_mode,r.snippet_suppressed
 FROM local_results r JOIN documents d ON d.id=r.document_id JOIN snapshots s ON s.id=r.snapshot_id
 JOIN snapshots current ON current.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=r.representation_key LEFT JOIN document_publication cp ON cp.owner=d.owner AND cp.url_key=d.representation_key
 WHERE r.position>=? AND d.owner=? AND d.deleted=0 AND current.index_allowed=1
 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1 AND COALESCE(cp.index_allowed,1)=1 AND site_mode<2
 AND (?=0 OR r.site_binding_version<>0 OR s.source_kind<>'web') ORDER BY r.position LIMIT ?)SQL";
    Json local_result(Stmt& rows,const std::optional<Query>& query) {
      Json h=Json::object();
      h.set("position", rows.num(0));
      h.set("document_id", rows.text(1));
      h.set("snapshot_id", rows.text(2));
      h.set("score", rows.num(3));
      h.set("title", rows.text(4));
      h.set("url", rows.text(5));
      auto text=rows.text(6);auto publication=Json::parse(rows.text(8),8*1024*1024);
      const bool stale_restricted=rows.num(18)>0||rows.num(17)>0||rows.num(11)>rows.num(12)||(rows.num(10)!=0&&rows.text(9)!=rows.text(2))||(rows.num(14)!=0&&rows.text(13)!=rows.text(2))||(rows.num(16)!=0&&rows.text(15)!=rows.text(2));
      bool snippet_allowed=!stale_restricted;
      if(stale_restricted) { text.clear();h.set("title",rows.text(5)); }
      else if(publication.has("version")) {
        snippet_allowed=publication.at("snippet_allowed").flag();text=publication.at("snippet_text").str();
        auto safe_title=publication.at("snippet_title").str();h.set("title",safe_title.empty()?rows.text(5):safe_title);
      }
      if(!snippet_allowed) {
        h.set("snippet","");h.set("snippet_kind","publication_suppressed");h.set("highlights",Json::array());
      } else if(query) {
        auto hl=highlight(text, query->root);
        h.set("snippet", hl.at("snippet"));
        // Sanitized publication text is a different representation. Never
        // label its byte offsets as locations in the canonical owner snapshot.
        const bool canonical=text==rows.text(6);
        h.set("snippet_kind",canonical?hl.at("snippet_kind"):Json::string("publication_excerpt"));
        h.set("highlights",canonical?hl.at("highlights"):Json::array());
      } else {
        h.set("snippet", utf8_prefix(text, 350));
        h.set("snippet_kind", "document_prefix");
      }
      h.set("revision", rows.num(7));
      return h;
    }
    const char* schema_tables=R"SQL(
 CREATE TABLE IF NOT EXISTS usage(owner TEXT PRIMARY KEY,bytes INTEGER NOT NULL DEFAULT 0,snapshots INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS source_state(source_id TEXT PRIMARY KEY,cooldown_until INTEGER NOT NULL DEFAULT 0,
     failures INTEGER NOT NULL DEFAULT 0,last_status TEXT NOT NULL DEFAULT '',last_error TEXT NOT NULL DEFAULT '',
     last_ok_at INTEGER NOT NULL DEFAULT 0,last_request_at INTEGER NOT NULL DEFAULT 0,day INTEGER NOT NULL DEFAULT 0,
     requests_today INTEGER NOT NULL DEFAULT 0);
 )SQL";
    const char* fts_schema="CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='bzs');";
    const char* substring_schema="CREATE VIRTUAL TABLE substring_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,grams,tokenize='ascii',detail=none,columnsize=0);";
    bool has_column(sqlite3* db,const char* table,const char* column) {
      Stmt columns(db,std::string("PRAGMA table_info(")+table+")");
      while(columns.step())if(columns.text(1)==column)return true;
      return false;
    }
  }
  Store::Store(const std::filesystem::path& path, std::function<std::int64_t()> clock, StoreOptions options):path_(path), clock_(clock?std::move(clock):now_ms), options_(options) {
    require(options_.max_snapshots_per_owner>=1&&options_.max_bytes_per_owner>=1&&options_.max_revisions_per_document>=1&&options_.max_substring_bytes_per_owner>=1,
            "E_SCHEMA","Invalid store quotas");
    if(path!=":memory:"&&!path.parent_path().empty())std::filesystem::create_directories(path.parent_path());
    const auto filename=path==":memory:"?path.string():utf8_path(path);
    auto rc=sqlite3_open_v2(filename.c_str(), &db_, SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_FULLMUTEX, nullptr);
    if(rc!=SQLITE_OK) {
      if(db_)sqlite3_close(db_);
      db_=nullptr;
      throw Error("E_STORAGE", "Cannot open metadata database", 500);
    }
    try {
      sqlite3_extended_result_codes(db_, 1);
      sqlite3_busy_timeout(db_, 3000);
      sqlite3_limit(db_, SQLITE_LIMIT_LENGTH, 16*1024*1024);
      sqlite3_limit(db_, SQLITE_LIMIT_SQL_LENGTH, 65536);
      sqlite3_limit(db_, SQLITE_LIMIT_EXPR_DEPTH, 64);
      register_fts_tokenizer(db_);
      // Only an empty new database can select this mode without a full rewrite.
      // Existing NONE/FULL databases remain untouched; never VACUUM at startup.
      if(scalar(db_,"PRAGMA user_version")==0&&scalar(db_,"SELECT COUNT(*) FROM sqlite_master")==0)
        exec(db_,"PRAGMA auto_vacuum=INCREMENTAL;");
      exec(db_, "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;");
      migrate();
      checked(sqlite3_create_function_v2(db_,"bzs_site_publication",6,SQLITE_UTF8,this,&Store::site_publication_sql,nullptr,nullptr,nullptr),db_);
      Tx publication(db_);
      refresh_site_publication_locked();
      Stmt state(db_,"SELECT generation,manifest_generation FROM site_publication_state WHERE singleton=1");
      require(state.step(),"E_STORAGE","Missing deployment publication state",500);
      site_publication_bound_generation_=state.num(0);site_publication_manifest_generation_=state.num(1);
      refresh_source_publication_locked();
      Stmt sources(db_,"SELECT manifest_generation FROM source_publication_state WHERE singleton=1");
      require(sources.step(),"E_STORAGE","Missing source publication state",500);
      source_publication_manifest_generation_=sources.num(0);
      publication.commit();
    }catch(...) {
      sqlite3_close(db_);
      db_=nullptr;
      throw;
    }
  }
  Store::~Store() {
    if(db_)sqlite3_close(db_);
  }
  void Store::migrate() {
    auto version=scalar(db_,"PRAGMA user_version");
    require(version>=0&&version<=15,"E_SCHEMA_VERSION","Database is newer than this program",409);
    bool rebuild_substrings=version<3;
    Tx tx(db_);
    if(version>0) {
      Stmt quarantine(db_,"SELECT value FROM settings WHERE key='restore_quarantine'");
      require(quarantine.step()&&quarantine.text(0)=="0","E_RESTORE_QUARANTINED",
              "Backup cannot be served before an independently verified revocation reconciliation",409);
    }
    if(version==0) {
      exec(db_,R"SQL(
 CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 INSERT INTO settings VALUES('restore_quarantine','0');
 CREATE TABLE subjects(owner TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE documents(id TEXT PRIMARY KEY,owner TEXT NOT NULL,url TEXT NOT NULL,host TEXT NOT NULL,
     current_snapshot TEXT,revision INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,
     deleted_at INTEGER NOT NULL DEFAULT 0,delete_generation INTEGER NOT NULL DEFAULT 0,UNIQUE(owner,url));
 CREATE TABLE snapshots(id TEXT PRIMARY KEY,extraction_id TEXT UNIQUE NOT NULL,
     document_id TEXT NOT NULL REFERENCES documents(id),revision INTEGER NOT NULL,title TEXT NOT NULL,
     canonical_text TEXT NOT NULL,raw_content TEXT NOT NULL,raw_hash TEXT NOT NULL,content_type TEXT NOT NULL,
     parser_version TEXT NOT NULL,created_at INTEGER NOT NULL,index_allowed INTEGER NOT NULL,
     links_json TEXT NOT NULL,meta_json TEXT NOT NULL DEFAULT '{}',source_kind TEXT NOT NULL DEFAULT 'import',
     UNIQUE(document_id,revision));
 CREATE TABLE evidence(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
     start_byte INTEGER NOT NULL,end_byte INTEGER NOT NULL,UNIQUE(snapshot_id,start_byte,end_byte));
 CREATE TABLE sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,auth_epoch INTEGER NOT NULL,
     query_json TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
     scanned INTEGER NOT NULL,partial INTEGER NOT NULL,retrieval TEXT NOT NULL);
 CREATE TABLE session_rows(session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
     position INTEGER NOT NULL,document_id TEXT NOT NULL,snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
     score INTEGER NOT NULL,PRIMARY KEY(session_id,position));
 CREATE TABLE cursors(token TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
     position INTEGER NOT NULL,UNIQUE(session_id,position));
 CREATE TABLE jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,fixture_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,
     fingerprint TEXT NOT NULL,state TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,auth_epoch INTEGER NOT NULL,
     attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL,lease_until INTEGER NOT NULL DEFAULT 0,
     deadline INTEGER NOT NULL,created_at INTEGER NOT NULL,used_pages INTEGER NOT NULL DEFAULT 0,
     result_json TEXT,error_code TEXT,created_delete_sequence INTEGER NOT NULL DEFAULT -1,UNIQUE(owner,idempotency_key));
 CREATE INDEX jobs_state ON jobs(state,created_at);
 CREATE INDEX snapshots_doc ON snapshots(document_id);
 CREATE INDEX sessions_owner ON sessions(owner,expires_at);
 )SQL");
    } else if(version<3) {
      // Both published 0.0.2 and the independent S1 attachment called their
      // different layouts schema 2. Add missing S1 columns by shape, keeping
      // every existing immutable snapshot, evidence record and task intact.
      if(!has_column(db_,"documents","deleted_at"))
        exec(db_,"ALTER TABLE documents ADD COLUMN deleted_at INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"documents","delete_generation"))
        exec(db_,"ALTER TABLE documents ADD COLUMN delete_generation INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"snapshots","meta_json"))
        exec(db_,"ALTER TABLE snapshots ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"snapshots","source_kind"))
        exec(db_,"ALTER TABLE snapshots ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'import';");
      if(!has_column(db_,"jobs","created_delete_sequence"))
        exec(db_,"ALTER TABLE jobs ADD COLUMN created_delete_sequence INTEGER NOT NULL DEFAULT -1;");
    }
    if(version<3) {
      exec(db_,schema_tables);
      exec(db_,R"SQL(
 CREATE INDEX IF NOT EXISTS evidence_snapshot ON evidence(snapshot_id);
 CREATE INDEX IF NOT EXISTS session_rows_snapshot ON session_rows(snapshot_id);
 INSERT OR IGNORE INTO settings(key,value)VALUES('deletion_sequence','0');
 UPDATE documents SET delete_generation=1 WHERE deleted!=0 AND delete_generation=0;
 UPDATE settings SET value=MAX(CAST(value AS INTEGER),(SELECT COALESCE(MAX(delete_generation),0) FROM documents))
   WHERE key='deletion_sequence';
 DROP TABLE IF EXISTS content_fts;
 )SQL");
      exec(db_,fts_schema);
      exec(db_,R"SQL(
 INSERT INTO content_fts(document_id,owner,title,body)
   SELECT d.id,d.owner,s.title,s.canonical_text FROM documents d JOIN snapshots s ON s.id=d.current_snapshot
   WHERE d.deleted=0 AND s.index_allowed=1;
 PRAGMA user_version=3;
 )SQL");
      Stmt tokenizer(db_,"INSERT OR REPLACE INTO settings(key,value)VALUES('fts_tokenizer_version',?)");
      tokenizer.bind(1,std::string(fts_tokenizer_version));
      tokenizer.step();
    } else {
      Stmt tokenizer(db_,"SELECT value FROM settings WHERE key='fts_tokenizer_version'");
      require(tokenizer.step()&&(tokenizer.text(0)==fts_tokenizer_version||tokenizer.text(0)==fts_legacy_gram_index_format_version||tokenizer.text(0)==fts_index_format_version),"E_SCHEMA_VERSION",
              "Index tokenizer version requires an explicit migration",409);
      rebuild_substrings=tokenizer.text(0)!=fts_index_format_version;
    }
    if(rebuild_substrings) {
      exec(db_,"DROP TABLE IF EXISTS substring_fts; DROP TABLE IF EXISTS substring_usage;");exec(db_,substring_schema);
      exec(db_,"CREATE TABLE substring_usage(owner TEXT PRIMARY KEY,bytes INTEGER NOT NULL CHECK(bytes>=0));");
    } else {
      Stmt index(db_,"SELECT 1 FROM sqlite_master WHERE type='table' AND name='substring_fts'");
      require(index.step(),"E_SCHEMA_VERSION","Substring index is missing; restore a complete database",409);
      Stmt accounting(db_,"SELECT 1 FROM sqlite_master WHERE type='table' AND name='substring_usage'");
      require(accounting.step(),"E_SCHEMA_VERSION","Substring accounting is missing; rebuild the derived index",409);
    }
    if(version<4) {
      if(!has_column(db_,"jobs","kind"))exec(db_,"ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'fixture.ingest';");
      exec(db_,R"SQL(
 CREATE TABLE read_job_budgets(job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
   max_requests INTEGER NOT NULL,max_bytes INTEGER NOT NULL,per_page_timeout_ms INTEGER NOT NULL,
   charged_requests INTEGER NOT NULL DEFAULT 0,charged_bytes INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE read_job_items(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
   position INTEGER NOT NULL,url TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
   deletion_generation INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
   lease_epoch INTEGER NOT NULL DEFAULT 0,attempt_id TEXT NOT NULL DEFAULT '',
   request_grant INTEGER NOT NULL DEFAULT 0,byte_grant INTEGER NOT NULL DEFAULT 0,
   external_requests INTEGER NOT NULL DEFAULT 0,content_bytes INTEGER NOT NULL DEFAULT 0,
   unknown_requests INTEGER NOT NULL DEFAULT 0,unknown_bytes INTEGER NOT NULL DEFAULT 0,
   usage_unknown INTEGER NOT NULL DEFAULT 0,snapshot_id TEXT REFERENCES snapshots(id) ON DELETE SET NULL,
   receipt_json TEXT,error_code TEXT,PRIMARY KEY(job_id,position),UNIQUE(job_id,url));
 CREATE INDEX read_job_items_state ON read_job_items(job_id,state,position);
 CREATE UNIQUE INDEX read_job_one_running ON read_job_items(job_id) WHERE state='running';
 CREATE INDEX read_job_snapshot ON read_job_items(snapshot_id);
 PRAGMA user_version=4;
 )SQL");
    }
    if(version<5) {
      exec(db_,R"SQL(
 CREATE TABLE map_jobs(job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
   site_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,origin TEXT NOT NULL,scope_path TEXT NOT NULL,
   max_urls INTEGER NOT NULL,max_depth INTEGER NOT NULL,max_requests INTEGER NOT NULL,max_bytes INTEGER NOT NULL,
   per_request_timeout_ms INTEGER NOT NULL,charged_requests INTEGER NOT NULL DEFAULT 0,charged_bytes INTEGER NOT NULL DEFAULT 0,
   external_requests INTEGER NOT NULL DEFAULT 0,content_bytes INTEGER NOT NULL DEFAULT 0,
   unknown_requests INTEGER NOT NULL DEFAULT 0,unknown_bytes INTEGER NOT NULL DEFAULT 0,
   partial INTEGER NOT NULL DEFAULT 0,stop_reason TEXT NOT NULL DEFAULT '',filtered_urls INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE map_resources(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,position INTEGER NOT NULL,
   url TEXT NOT NULL,fetch_url TEXT NOT NULL,kind TEXT NOT NULL,depth INTEGER NOT NULL,
   redirect_hop INTEGER NOT NULL DEFAULT 0,deletion_generation INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
   error_code TEXT NOT NULL DEFAULT '',PRIMARY KEY(job_id,position),UNIQUE(job_id,url));
 CREATE INDEX map_resources_state ON map_resources(job_id,state,position);
 CREATE TABLE map_hops(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,position INTEGER NOT NULL,
   resource_position INTEGER NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL,attempt_id TEXT NOT NULL UNIQUE,
   lease_epoch INTEGER NOT NULL,state TEXT NOT NULL,byte_grant INTEGER NOT NULL,request_grant INTEGER NOT NULL,
   external_requests INTEGER NOT NULL DEFAULT 0,content_bytes INTEGER NOT NULL DEFAULT 0,usage_unknown INTEGER NOT NULL DEFAULT 0,
   admission_json TEXT NOT NULL,response_json TEXT,error_code TEXT NOT NULL DEFAULT '',PRIMARY KEY(job_id,position),
   FOREIGN KEY(job_id,resource_position) REFERENCES map_resources(job_id,position));
 CREATE UNIQUE INDEX map_one_running ON map_hops(job_id) WHERE state='running';
 CREATE TABLE map_candidates(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,position INTEGER NOT NULL,
   url TEXT NOT NULL,parent TEXT NOT NULL,source TEXT NOT NULL,depth INTEGER NOT NULL,path_depth INTEGER NOT NULL,
   deletion_generation INTEGER NOT NULL,history_document_id TEXT NOT NULL DEFAULT '',history_generation INTEGER NOT NULL DEFAULT 0,
   PRIMARY KEY(job_id,position),UNIQUE(job_id,url));
 CREATE TABLE robots_cache(id TEXT PRIMARY KEY,owner TEXT NOT NULL,origin TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
   record_json TEXT NOT NULL,fetched_at INTEGER NOT NULL,expires_at INTEGER NOT NULL);
 CREATE INDEX robots_cache_lookup ON robots_cache(owner,origin,manifest_sha256,expires_at);
 CREATE TABLE site_request_state(origin TEXT PRIMARY KEY,window_started INTEGER NOT NULL DEFAULT 0,
   requests INTEGER NOT NULL DEFAULT 0,next_start INTEGER NOT NULL DEFAULT 0,cooldown_until INTEGER NOT NULL DEFAULT 0,
   active_attempt TEXT NOT NULL DEFAULT '',active_until INTEGER NOT NULL DEFAULT 0);
 PRAGMA user_version=5;
 )SQL");
    }
    if(version<6) {
      if(!has_column(db_,"documents","url_key"))exec(db_,"ALTER TABLE documents ADD COLUMN url_key TEXT NOT NULL DEFAULT '';");
      if(!has_column(db_,"documents","representation_key"))exec(db_,"ALTER TABLE documents ADD COLUMN representation_key TEXT NOT NULL DEFAULT '';");
      if(!has_column(db_,"snapshots","publication_json"))exec(db_,"ALTER TABLE snapshots ADD COLUMN publication_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"snapshots","representation_publication_json"))exec(db_,"ALTER TABLE snapshots ADD COLUMN representation_publication_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"session_rows","representation_key"))exec(db_,"ALTER TABLE session_rows ADD COLUMN representation_key TEXT NOT NULL DEFAULT '';");
      if(!has_column(db_,"session_rows","publication_generation"))exec(db_,"ALTER TABLE session_rows ADD COLUMN publication_generation INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"map_jobs","max_pages"))exec(db_,"ALTER TABLE map_jobs ADD COLUMN max_pages INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"map_jobs","used_pages"))exec(db_,"ALTER TABLE map_jobs ADD COLUMN used_pages INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"map_jobs","last_dispatch_sequence"))exec(db_,"ALTER TABLE map_jobs ADD COLUMN last_dispatch_sequence INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"map_resources","parent"))exec(db_,"ALTER TABLE map_resources ADD COLUMN parent TEXT NOT NULL DEFAULT '';");
      if(!has_column(db_,"map_resources","source"))exec(db_,"ALTER TABLE map_resources ADD COLUMN source TEXT NOT NULL DEFAULT 'seed';");
      if(!has_column(db_,"map_resources","page_charged"))exec(db_,"ALTER TABLE map_resources ADD COLUMN page_charged INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"map_resources","snapshot_id"))exec(db_,"ALTER TABLE map_resources ADD COLUMN snapshot_id TEXT REFERENCES snapshots(id) ON DELETE SET NULL;");
      if(!has_column(db_,"map_resources","publication_ceiling_json"))exec(db_,"ALTER TABLE map_resources ADD COLUMN publication_ceiling_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"map_resources","receipt_json"))exec(db_,"ALTER TABLE map_resources ADD COLUMN receipt_json TEXT;");
      exec(db_,R"SQL(
 CREATE INDEX IF NOT EXISTS documents_url_key ON documents(owner,url_key);
 CREATE INDEX IF NOT EXISTS documents_representation_key ON documents(owner,representation_key);
 CREATE INDEX IF NOT EXISTS session_rows_representation ON session_rows(representation_key);
 CREATE INDEX IF NOT EXISTS map_resource_snapshot ON map_resources(snapshot_id);
 CREATE TABLE IF NOT EXISTS document_publication(owner TEXT NOT NULL,url_key TEXT NOT NULL,
   snapshot_id TEXT REFERENCES snapshots(id) ON DELETE SET NULL,record_json TEXT NOT NULL,
   index_allowed INTEGER NOT NULL,snippet_allowed INTEGER NOT NULL,restricted INTEGER NOT NULL,
   restriction_generation INTEGER NOT NULL DEFAULT 0,observed_at INTEGER NOT NULL,
   PRIMARY KEY(owner,url_key));
 CREATE TABLE IF NOT EXISTS crawl_edges(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,position INTEGER NOT NULL,
   parent TEXT NOT NULL,url TEXT NOT NULL,source TEXT NOT NULL,depth INTEGER NOT NULL,followed INTEGER NOT NULL,
   reason TEXT NOT NULL DEFAULT '',parent_generation INTEGER NOT NULL,target_generation INTEGER NOT NULL,
   PRIMARY KEY(job_id,position),UNIQUE(job_id,parent,url,source));
 INSERT OR IGNORE INTO settings(key,value)VALUES('map_dispatch_sequence','0');
 INSERT OR IGNORE INTO settings(key,value)VALUES('publication_restriction_sequence','0');
 PRAGMA user_version=6;
 )SQL");
      Stmt docs(db_,"SELECT id,url FROM documents WHERE url_key=''");
      std::vector<std::pair<std::string,std::string>> keys;
      while(docs.step()) {
        auto url=docs.text(1);try { url=map_url(url); } catch(const Error&) {}
        keys.emplace_back(docs.text(0),std::move(url));
      }
      for(const auto& [id,key]:keys) { Stmt key_update(db_,"UPDATE documents SET url_key=? WHERE id=?");key_update.bind(1,key);key_update.bind(2,id);key_update.step(); }
      Stmt representations(db_,"SELECT d.id,d.url_key,s.meta_json,s.source_kind FROM documents d LEFT JOIN snapshots s ON s.id=d.current_snapshot WHERE d.representation_key=''");
      keys.clear();
      while(representations.step()) {
        auto key=representations.text(1);
        if(representations.text(3)=="web")try { const auto meta=Json::parse(representations.text(2),8*1024*1024);if(meta.has("final_url")) { key=meta.at("final_url").str();try { key=map_url(key); } catch(const Error&) {} } } catch(const Error&) {}
        keys.emplace_back(representations.text(0),std::move(key));
      }
      for(const auto& [id,key]:keys) { Stmt key_update(db_,"UPDATE documents SET representation_key=? WHERE id=?");key_update.bind(1,key);key_update.bind(2,id);key_update.step(); }
      Stmt frozen(db_,"SELECT r.session_id,r.position,d.url_key,s.meta_json,s.source_kind FROM session_rows r JOIN documents d ON d.id=r.document_id JOIN snapshots s ON s.id=r.snapshot_id WHERE r.representation_key=''");
      std::vector<std::tuple<std::string,std::int64_t,std::string>> frozen_keys;
      while(frozen.step()) {
        auto key=frozen.text(2);
        if(frozen.text(4)=="web")try { const auto meta=Json::parse(frozen.text(3),8*1024*1024);if(meta.has("final_url")) { key=meta.at("final_url").str();try { key=map_url(key); } catch(const Error&) {} } } catch(const Error&) {}
        frozen_keys.emplace_back(frozen.text(0),frozen.num(1),std::move(key));
      }
      for(const auto& [session,position,key]:frozen_keys) { Stmt update(db_,"UPDATE session_rows SET representation_key=? WHERE session_id=? AND position=?");update.bind(1,key);update.bind(2,session);update.bind(3,position);update.step(); }
    }
    if(version<7) {
      if(!has_column(db_,"snapshots","site_bindings_json"))exec(db_,"ALTER TABLE snapshots ADD COLUMN site_bindings_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"snapshots","site_publication_generation"))exec(db_,"ALTER TABLE snapshots ADD COLUMN site_publication_generation INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"session_rows","site_binding_version"))exec(db_,"ALTER TABLE session_rows ADD COLUMN site_binding_version INTEGER NOT NULL DEFAULT 0;");
      exec(db_,R"SQL(
 CREATE TABLE IF NOT EXISTS site_publication_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version TEXT NOT NULL,
   manifest_sha256 TEXT NOT NULL,manifest_json TEXT NOT NULL,manifest_generation INTEGER NOT NULL DEFAULT 0,
   generation INTEGER NOT NULL DEFAULT 0,legacy_frozen_withdrawn INTEGER NOT NULL DEFAULT 0,
   legacy_crawl_unverified INTEGER NOT NULL DEFAULT 0,
   sweep_after INTEGER NOT NULL DEFAULT 0,sweep_complete INTEGER NOT NULL DEFAULT 1);
 INSERT OR IGNORE INTO site_publication_state(singleton,version,manifest_sha256,manifest_json)
   VALUES(1,'bzs-site-publication-1','','{"version":1,"sites":[]}');
 CREATE TABLE IF NOT EXISTS site_publication_rules(origin TEXT NOT NULL,path_prefix TEXT NOT NULL,mode INTEGER NOT NULL,
   snippet_generation INTEGER NOT NULL DEFAULT 0,index_generation INTEGER NOT NULL DEFAULT 0,
   PRIMARY KEY(origin,path_prefix));
 CREATE TABLE IF NOT EXISTS crawl_trap_policy(job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,version TEXT NOT NULL,config_json TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS crawl_trap_urls(job_id TEXT NOT NULL REFERENCES crawl_trap_policy(job_id) ON DELETE CASCADE,
   url TEXT NOT NULL,explicit_seed INTEGER NOT NULL DEFAULT 0,parent TEXT NOT NULL DEFAULT '',
   date_family TEXT NOT NULL DEFAULT '',date_value INTEGER NOT NULL DEFAULT 0,date_run INTEGER NOT NULL DEFAULT 0,
   query_family TEXT NOT NULL DEFAULT '',query_signature TEXT NOT NULL DEFAULT '',ordering_signature TEXT NOT NULL DEFAULT '',
   page_family TEXT NOT NULL DEFAULT '',body_hash TEXT NOT NULL DEFAULT '',body_bytes INTEGER NOT NULL DEFAULT 0,
   PRIMARY KEY(job_id,url));
 CREATE INDEX IF NOT EXISTS crawl_trap_dates ON crawl_trap_urls(job_id,date_family,date_value);
 CREATE INDEX IF NOT EXISTS crawl_trap_queries ON crawl_trap_urls(job_id,query_family,query_signature);
 CREATE INDEX IF NOT EXISTS crawl_trap_ordering ON crawl_trap_urls(job_id,ordering_signature);
 CREATE INDEX IF NOT EXISTS crawl_trap_pages ON crawl_trap_urls(job_id,page_family,body_hash);
 CREATE TABLE IF NOT EXISTS crawl_trap_edges(job_id TEXT NOT NULL,position INTEGER NOT NULL,decision_json TEXT NOT NULL,
   PRIMARY KEY(job_id,position),FOREIGN KEY(job_id,position) REFERENCES crawl_edges(job_id,position) ON DELETE CASCADE);
 PRAGMA user_version=7;
 )SQL");
      // Schema 6 crawl artifacts contain one-hop metadata. Their former site
      // scopes and overwritten intermediate routes cannot be reconstructed
      // from the final body. Preserve the owner artifact, but require a new
      // complete read before publishing it under an explicitly activated policy.
      require(scalar(db_,"SELECT COUNT(*) FROM map_resources")<=20000,"E_SCHEMA_VERSION","Legacy crawl resources exceed the supported migration bound",409);
      exec(db_,R"SQL(
 INSERT INTO usage(owner,bytes,snapshots)
 SELECT d.owner,COUNT(*)*length('{"version":"bzs-site-bindings-1","legacy_crawl_unverified":true,"urls":[]}'),0
 FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE s.source_kind='web' AND s.site_bindings_json='{}'
 AND s.id IN (SELECT snapshot_id FROM map_resources WHERE kind='page' AND snapshot_id IS NOT NULL) GROUP BY d.owner
 ON CONFLICT(owner) DO UPDATE SET bytes=usage.bytes+excluded.bytes;
 UPDATE snapshots SET site_bindings_json='{"version":"bzs-site-bindings-1","legacy_crawl_unverified":true,"urls":[]}'
 WHERE source_kind='web' AND site_bindings_json='{}' AND id IN (SELECT snapshot_id FROM map_resources WHERE kind='page' AND snapshot_id IS NOT NULL);
 UPDATE site_publication_state SET legacy_crawl_unverified=1 WHERE EXISTS
   (SELECT 1 FROM map_resources r JOIN snapshots s ON s.id=r.snapshot_id WHERE s.site_bindings_json='{"version":"bzs-site-bindings-1","legacy_crawl_unverified":true,"urls":[]}');
 )SQL");
    }
    if(version<8) {
      if(!has_column(db_,"document_publication","index_generation")) {
        exec(db_,"ALTER TABLE document_publication ADD COLUMN index_generation INTEGER NOT NULL DEFAULT 0;");
        // Old rows preserve a restriction watermark but not its former kind
        // after an allowing refresh. Unknown old kinds conservatively seal
        // reads which were already in flight before this migration.
        exec(db_,"UPDATE document_publication SET index_generation=restriction_generation;");
      }
      if(!has_column(db_,"document_publication","snippet_generation")) {
        exec(db_,"ALTER TABLE document_publication ADD COLUMN snippet_generation INTEGER NOT NULL DEFAULT 0;");
        exec(db_,"UPDATE document_publication SET snippet_generation=restriction_generation;");
      }
      if(!has_column(db_,"session_rows","snippet_suppressed"))exec(db_,"ALTER TABLE session_rows ADD COLUMN snippet_suppressed INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"read_job_items","publication_sequence"))exec(db_,"ALTER TABLE read_job_items ADD COLUMN publication_sequence INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"read_job_items","publication_snapshot_id"))exec(db_,"ALTER TABLE read_job_items ADD COLUMN publication_snapshot_id TEXT NOT NULL DEFAULT '@legacy';");
      if(!has_column(db_,"map_resources","publication_snapshot_id"))exec(db_,"ALTER TABLE map_resources ADD COLUMN publication_snapshot_id TEXT NOT NULL DEFAULT '@legacy';");
      if(!has_column(db_,"map_resources","publication_sequence")) {
        exec(db_,"ALTER TABLE map_resources ADD COLUMN publication_sequence INTEGER NOT NULL DEFAULT -1;");
        exec(db_,"UPDATE map_resources SET publication_sequence=0 WHERE page_charged<>0 OR redirect_hop<>0;");
      }
      exec(db_,"PRAGMA user_version=8;");
    }
    if(version<9) {
      if(!has_column(db_,"jobs","archive_json"))exec(db_,"ALTER TABLE jobs ADD COLUMN archive_json TEXT NOT NULL DEFAULT '';");
      if(!has_column(db_,"jobs","archived_at"))exec(db_,"ALTER TABLE jobs ADD COLUMN archived_at INTEGER NOT NULL DEFAULT 0;");
      exec(db_,R"SQL(
 CREATE INDEX IF NOT EXISTS jobs_retention ON jobs(owner,archived_at,state,deadline,id);
 CREATE INDEX IF NOT EXISTS robots_retention ON robots_cache(owner,expires_at,id);
 CREATE INDEX IF NOT EXISTS map_hops_robots_admission ON map_hops(json_extract(admission_json,'$.robots_cache_id'));
 CREATE INDEX IF NOT EXISTS map_hops_robots_receipt ON map_hops(json_extract(response_json,'$.robots_cache_id'));
 PRAGMA user_version=9;
 )SQL");
    }
    if(version<10) {
      if(!has_column(db_,"read_job_budgets","last_dispatch_sequence"))
        exec(db_,"ALTER TABLE read_job_budgets ADD COLUMN last_dispatch_sequence INTEGER NOT NULL DEFAULT 0;");
      // Legacy jobs have no captured admission order. Their existing stable
      // created_at/id tie break is retained until each receives a first turn;
      // all new admissions and dispatches get a unique durable queue ticket.
      exec(db_,R"SQL(
 INSERT OR IGNORE INTO settings(key,value)VALUES('read_dispatch_sequence','0');
 CREATE INDEX IF NOT EXISTS read_job_scheduling ON read_job_budgets(last_dispatch_sequence,job_id);
 CREATE INDEX IF NOT EXISTS map_job_scheduling ON map_jobs(last_dispatch_sequence,job_id);
 PRAGMA user_version=10;
 )SQL");
    }
    if(version<3) {
      // Shape-compatible legacy attachments may already contain some newer
      // immutable metadata. Rebuild capacity after all optional columns exist,
      // counting the records actually retained by this additive migration.
      exec(db_,R"SQL(
 DELETE FROM usage;
 INSERT INTO usage(owner,bytes,snapshots)
 SELECT d.owner,COALESCE(SUM(length(CAST(s.raw_content AS BLOB))+length(CAST(s.canonical_text AS BLOB))
   +length(CAST(CASE WHEN s.publication_json='{}' THEN '' ELSE s.publication_json END AS BLOB))
   +length(CAST(CASE WHEN s.representation_publication_json='{}' THEN '' ELSE s.representation_publication_json END AS BLOB))
   +length(CAST(CASE WHEN s.site_bindings_json='{}' THEN '' ELSE s.site_bindings_json END AS BLOB))),0),COUNT(s.id)
 FROM documents d JOIN snapshots s ON s.document_id=d.id GROUP BY d.owner;
 )SQL");
    }
    if(version<11) { migrate_sessions_locked();exec(db_,"PRAGMA user_version=11"); }
    if(version<12) {
      // Legacy robots decisions have no proven validators. They keep their
      // existing expiry, and can only acquire cache metadata from a full 200.
      if(!has_column(db_,"robots_cache","http_cache_json"))exec(db_,"ALTER TABLE robots_cache ADD COLUMN http_cache_json TEXT NOT NULL DEFAULT '{}';");
      if(!has_column(db_,"snapshots","http_cache_bytes"))exec(db_,"ALTER TABLE snapshots ADD COLUMN http_cache_bytes INTEGER NOT NULL DEFAULT 0;");
      exec(db_,"PRAGMA user_version=12;");
    }
    if(version<13) {
      migrate_source_publication_locked();migrate_revocations_locked();migrate_shared_reads_locked();
      exec(db_,"PRAGMA user_version=13;");
    }
    if(version<14) {
      // Derived comparison keys cover old aliases and retained tombstones too.
      // Document/snapshot identity, deletion generations and payloads stay put.
      if(!has_column(db_,"documents","deletion_key"))
        exec(db_,"ALTER TABLE documents ADD COLUMN deletion_key TEXT NOT NULL DEFAULT '';");
      Stmt rows(db_,"SELECT id,url FROM documents");
      while(rows.step()) {
        Stmt update(db_,"UPDATE documents SET deletion_key=? WHERE id=?");
        update.bind(1,deletion_url_key(rows.text(1)));update.bind(2,rows.text(0));update.step();
      }
      exec(db_,"CREATE INDEX IF NOT EXISTS documents_deletion_key ON documents(owner,deletion_key,id);");
      exec(db_,"PRAGMA user_version=14;");
    }
    if(version<15) {
      // Existing attempts have no proven dispatch timeout/admission state.
      // They still settle normally, but cannot acquire new subscribers.
      if(!has_column(db_,"read_job_items","dispatch_timeout_ms"))
        exec(db_,"ALTER TABLE read_job_items ADD COLUMN dispatch_timeout_ms INTEGER NOT NULL DEFAULT 0;");
      if(!has_column(db_,"read_job_items","sharing_closed"))
        exec(db_,"ALTER TABLE read_job_items ADD COLUMN sharing_closed INTEGER NOT NULL DEFAULT 1;");
      exec(db_,"UPDATE read_job_items SET dispatch_timeout_ms=0,sharing_closed=1;");
      exec(db_,"CREATE INDEX IF NOT EXISTS read_shared_open_uri ON read_job_items(url,job_id,position) WHERE state='running' AND sharing_closed=0 AND request_grant>0;");
      exec(db_,"PRAGMA user_version=15;");
    }
    // Permanent key history must not make active-detail counts scan that history.
    exec(db_,"CREATE INDEX IF NOT EXISTS jobs_unarchived_owner ON jobs(owner) WHERE archived_at=0;");
    if(rebuild_substrings) {
      // Preserve the existing word-index membership and immutable snapshots.
      // Policy SQL functions are registered after migration; reads still check
      // current publication and the startup sweep can retract either index.
      Stmt rows(db_,"SELECT d.id,d.owner,s.title,s.canonical_text,d.url FROM content_fts f JOIN documents d ON d.id=f.document_id AND d.owner=f.owner JOIN snapshots s ON s.id=d.current_snapshot WHERE d.deleted=0 AND s.index_allowed=1");
      while(rows.step())index_substrings_locked(rows.text(1),rows.text(0),rows.text(2),rows.text(3),rows.text(4),false);
      Stmt marker(db_,"UPDATE settings SET value=? WHERE key='fts_tokenizer_version'");
      marker.bind(1,std::string(fts_index_format_version));marker.step();
    }
    tx.commit();
  }
  std::int64_t Store::epoch_locked(const std::string& owner) {
    owner_valid(owner);
    Stmt add(db_, "INSERT OR IGNORE INTO subjects(owner)VALUES(?)");
    add.bind(1, owner);
    add.step();
    Stmt s(db_, "SELECT epoch FROM subjects WHERE owner=?");
    s.bind(1, owner);
    require(s.step(), "E_STORAGE", "Missing subject", 500);
    return s.num(0);
  }
  std::int64_t Store::delete_sequence_locked() {
    Stmt s(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='deletion_sequence'");
    require(s.step()&&s.num(0)>=0,"E_STORAGE","Missing deletion sequence",500);
    return s.num(0);
  }
  std::int64_t Store::deletion_generation_locked(const std::string& owner,const std::string& url) {
    Stmt s(db_,"SELECT delete_generation FROM documents WHERE owner=? AND url=?");
    s.bind(1,owner);
    s.bind(2,url);
    return s.step()?s.num(0):0;
  }
  std::int64_t Store::deletion_generation(const std::string& owner,const std::string& url) {
    owner_valid(owner);
    std::lock_guard lock(mutex_);
    require_principal_active_locked(owner);
    return deletion_generation_locked(owner,url);
  }
  void Store::usage_add_locked(const std::string& owner, std::int64_t bytes, std::int64_t snapshots) {
    Stmt s(db_, "INSERT INTO usage(owner,bytes,snapshots)VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET bytes=MAX(0,bytes+excluded.bytes),snapshots=MAX(0,snapshots+excluded.snapshots)");
    s.bind(1, owner);
    s.bind(2, bytes);
    s.bind(3, snapshots);
    s.step();
  }
  Json Store::snapshot_locked(const std::string& owner, const std::string& id) {
    require_principal_active_locked(owner);
    refresh_site_publication_locked(false);
    Stmt s(db_, R"SQL(SELECT s.id,s.extraction_id,d.id,d.url,s.title,s.canonical_text,s.raw_hash,s.content_type,
 s.parser_version,s.created_at,s.revision,s.index_allowed,s.links_json,s.meta_json,s.source_kind,s.publication_json,s.representation_publication_json,s.site_bindings_json,s.site_publication_generation
 FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE s.id=? AND d.owner=? AND d.deleted=0)SQL");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Document snapshot not found", 404);
    Json j=Json::object();
    j.set("snapshot_id", s.text(0));
    j.set("extraction_id", s.text(1));
    j.set("document_id", s.text(2));
    j.set("url", s.text(3));
    j.set("title", s.text(4));
    j.set("canonical_text", s.text(5));
    j.set("raw_hash", s.text(6));
    j.set("content_type", s.text(7));
    j.set("parser_version", s.text(8));
    j.set("ingested_at_ms", s.num(9));
    j.set("revision", s.num(10));
    const auto deployment_mode=site_publication_mode_locked(s.text(14),s.text(17),s.num(18),s.text(3),s.text(13));
    j.set("indexed", s.num(11)!=0&&deployment_mode<2);
    auto deployment=Json::object();deployment.set("version","bzs-site-publication-1");deployment.set("mode",deployment_mode==2?"withdrawn":deployment_mode==1?"links_only":"allowed");
    deployment.set("generation",site_publication_cached_generation_);deployment.set("artifact_generation",s.num(18));j.set("site_publication",deployment);
    j.set("links", Json::parse(s.text(12)));
    j.set("meta", Json::parse(s.text(13)));
    j.set("source_kind", s.text(14));
    j.set("publication",Json::parse(s.text(15),8*1024*1024));
    j.set("representation_publication",Json::parse(s.text(16),8*1024*1024));
    j.set("content_origin", s.text(14)=="web"?"web_fetch_untrusted":"authorized_input_or_fixture");
    return j;
  }
  void Store::prune_locked(const std::string& document_id, const std::string& keep, std::int64_t newest_revision, const std::string& owner) {
    auto threshold=newest_revision-options_.max_revisions_per_document;
    if(threshold<1)return;
    Stmt list(db_, R"SQL(SELECT id,length(CAST(raw_content AS BLOB))+length(CAST(canonical_text AS BLOB))+length(CAST(CASE WHEN publication_json='{}' THEN '' ELSE publication_json END AS BLOB))+length(CAST(CASE WHEN representation_publication_json='{}' THEN '' ELSE representation_publication_json END AS BLOB))+length(CAST(CASE WHEN site_bindings_json='{}' THEN '' ELSE site_bindings_json END AS BLOB))+http_cache_bytes FROM snapshots
 WHERE document_id=? AND id!=? AND revision<=? AND id NOT IN (SELECT snapshot_id FROM evidence)
 AND id NOT IN (SELECT r.snapshot_id FROM session_rows r JOIN sessions q ON q.id=r.session_id WHERE q.expires_at>?))SQL");
    list.bind(1, document_id);
    list.bind(2, keep);
    list.bind(3, threshold);
    list.bind(4, clock_());
    std::vector<std::pair<std::string, std::int64_t>> doomed;
    while(list.step())doomed.emplace_back(list.text(0), list.num(1));
    std::int64_t freed=0;
    for(const auto& [id, bytes]:doomed) {
      Stmt del(db_, "DELETE FROM snapshots WHERE id=?");
      del.bind(1, id);
      del.step();
      freed+=bytes;
    }
    if(!doomed.empty())usage_add_locked(owner, -freed, -static_cast<std::int64_t>(doomed.size()));
  }
  void Store::remove_document_index_locked(const std::string& id) {
    Stmt word(db_,"DELETE FROM content_fts WHERE document_id=?");word.bind(1,id);word.step();
    remove_substrings_locked(id);
  }
  void Store::remove_substrings_locked(const std::string& id) {
    {
      Stmt prior(db_,"SELECT owner,length(CAST(grams AS BLOB)) FROM substring_fts WHERE rowid=(SELECT rowid FROM documents WHERE id=?)");
      prior.bind(1,id);
      if(prior.step()) {
        Stmt account(db_,"UPDATE substring_usage SET bytes=bytes-? WHERE owner=?");
        account.bind(1,prior.num(1));account.bind(2,prior.text(0));account.step();
        require(sqlite3_changes(db_)==1,"E_STORAGE","Missing substring usage account",500);
      }
    }
    Stmt substring(db_,"DELETE FROM substring_fts WHERE rowid=(SELECT rowid FROM documents WHERE id=?)");
    substring.bind(1,id);substring.step();
  }
  void Store::index_substrings_locked(const std::string& owner,const std::string& id,const std::string& title,const std::string& body,const std::string& url,bool enforce_quota) {
    // Align the derived rowid with the document for bounded replacements and
    // candidate intersections; field prefixes distinguish title/body/URL.
    const auto grams=indexed_grams(title,'t')+" "+indexed_grams(body,'b')+" "+indexed_grams(url,'r');
    const auto bytes=static_cast<std::int64_t>(grams.size());
    const auto limit=enforce_quota?options_.max_substring_bytes_per_owner:std::numeric_limits<std::int64_t>::max();
    require(bytes<=limit,"E_QUOTA","Owner substring index quota exceeded",507);
    // Reserve the logical bytes atomically in the caller's transaction. The
    // conditional upsert avoids a separate SELECT on every indexed document.
    Stmt account(db_,"INSERT INTO substring_usage(owner,bytes) VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET bytes=bytes+excluded.bytes WHERE bytes<=?-excluded.bytes");
    account.bind(1,owner);account.bind(2,bytes);account.bind(3,limit);account.step();
    require(sqlite3_changes(db_)==1,"E_QUOTA","Owner substring index quota exceeded",507);
    Stmt index(db_,"INSERT INTO substring_fts(rowid,document_id,owner,grams) SELECT rowid,id,owner,? FROM documents WHERE id=? AND owner=?");
    index.bind(1,grams);index.bind(2,id);index.bind(3,owner);index.step();
    require(sqlite3_changes(db_)==1,"E_STORAGE","Missing document for substring index",500);
  }
  void Store::index_document_locked(const std::string& owner,const std::string& id,const std::string& title,const std::string& body,const std::string& url,bool enforce_quota) {
    Stmt word(db_,"INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)");
    word.bind(1,id);word.bind(2,owner);word.bind(3,title);word.bind(4,body);word.step();
    index_substrings_locked(owner,id,title,body,url,enforce_quota);
  }
  Json Store::ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed, const std::string& meta_json, const std::string& source_kind) {
    require_principal_active_locked(owner);
    owner_valid(owner);
    refresh_site_publication_locked(false);
    auto host=metadata_host(input.url);
    epoch_locked(owner);
    std::string did, latest;
    std::int64_t revision=0;
    {
      Stmt d(db_, "SELECT id,current_snapshot,revision FROM documents WHERE owner=? AND url=?");
      d.bind(1, owner);
      d.bind(2, input.url);
      if(d.step()) {
        did=d.text(0);
        latest=d.text(1);
        revision=d.num(2);
      }
    }
    auto rawhash=sha256(input.raw);
    const auto publication=parsed.publication.canonical(),representation_publication=parsed.representation_publication.canonical();
    const auto& final_policy=parsed.representation_publication.has("version")?parsed.representation_publication:parsed.publication;
    auto url_key=input.url;try { url_key=map_url(input.url); } catch(const Error&) {}
    auto representation_key=url_key;
    std::string site_bindings="{}";std::int64_t site_generation=0;
    const auto metadata=Json::parse(meta_json,8*1024*1024);
    auto http_metadata=Json::object();
    for(const auto* key:{"http_cache","http_freshness","http_redirect_chain","http_revalidation","revalidation_discovery"})if(metadata.has(key))http_metadata.set(key,metadata.at(key));
    const auto http_bytes=http_metadata.size()?static_cast<std::int64_t>(http_metadata.canonical().size()):0;
    require(http_bytes<=16384,"E_WORKER_PROTOCOL","HTTP revalidation metadata exceeds its bound",502);
    const bool stale_publication=source_kind=="web"&&metadata.has("page_publication_stale")&&metadata.at("page_publication_stale").flag();
    const auto page_sequence=source_kind=="web"&&metadata.has("page_publication_sequence")?metadata.at("page_publication_sequence").num():0;
    bool preserve_current=false;
    if(source_kind=="web") {
      if(metadata.has("final_url")) { representation_key=metadata.at("final_url").str();try { representation_key=map_url(representation_key); } catch(const Error&) {} }
      site_generation=metadata.has("site_publication_generation")?metadata.at("site_publication_generation").num():site_publication_bound_generation_;
      require(site_generation>=0&&site_generation<=site_publication_cached_generation_,"E_SITE_POLICY_STALE","Read publication generation is not active in this store",409);
      auto bindings=site_bindings_locked(input.url,metadata);
      // Even a read started at the newest configuration generation may have
      // occurred while publication was prohibited. A later allow cannot turn
      // that private artifact into a newly authorized public representation.
      bindings.set("captured_mode",static_cast<std::int64_t>(site_publication_mode_locked(source_kind,bindings.canonical(),site_generation,input.url,meta_json)));
      site_bindings=bindings.canonical();
      if(stale_publication&&!did.empty()) {
        const auto history=metadata.at("page_publication_history");
        seal_frozen_publication_locked(owner,did,history);
        Stmt current(db_,"SELECT source_kind,COALESCE(json_extract(meta_json,'$.page_publication_sequence'),0) FROM snapshots WHERE id=?");current.bind(1,latest);
        if(current.step())preserve_current=current.text(0)!="web"||current.num(1)>=std::max(history.at("index_generation").num(),history.at("snippet_generation").num())||
          (metadata.has("publication_fence")&&metadata.at("publication_fence").has("snapshot_id")&&metadata.at("publication_fence").at("snapshot_id").str()!=latest);
      }
    }
    if(!latest.empty()&&!preserve_current) {
      Stmt same(db_, "SELECT id FROM snapshots WHERE id=? AND raw_hash=? AND canonical_text=? AND title=? AND parser_version=? AND content_type=? AND index_allowed=? AND publication_json=? AND representation_publication_json=? AND site_bindings_json=? AND site_publication_generation=? AND source_kind=? AND COALESCE(json_extract(meta_json,'$.page_publication_sequence'),0)=?");
      same.bind(1, latest);
      same.bind(2, rawhash);
      same.bind(3, parsed.text);
      same.bind(4, parsed.title);
      same.bind(5, parsed.parser_version);
      same.bind(6, input.content_type);
      same.bind(7, static_cast<std::int64_t>(parsed.index_allowed));
      same.bind(8,publication);same.bind(9,representation_publication);
      same.bind(10,site_bindings);same.bind(11,site_generation);same.bind(12,source_kind);
      same.bind(13,page_sequence);
      if(same.step()) {
        if(source_kind=="web") {
          // Unchanged content: only fetch provenance (meta.fetched_at_ms) moves; the snapshot text stays immutable.
          Stmt accounting(db_,"SELECT s.http_cache_bytes,u.bytes FROM snapshots s JOIN usage u ON u.owner=? WHERE s.id=?");
          accounting.bind(1,owner);accounting.bind(2,latest);require(accounting.step(),"E_STORAGE","Missing snapshot accounting",500);
          const auto delta=http_bytes-accounting.num(0);
          require(payload_quota_fits_locked(owner,delta),"E_QUOTA","Owner snapshot and archive quota exceeded",507);
          Stmt touch(db_, "UPDATE snapshots SET meta_json=?,http_cache_bytes=? WHERE id=?");
          touch.bind(1, meta_json);
          touch.bind(2, http_bytes);touch.bind(3,latest);
          touch.step();
          if(delta)usage_add_locked(owner,delta,0);
        }
        Stmt representation(db_,"UPDATE documents SET representation_key=? WHERE id=?");representation.bind(1,representation_key);representation.bind(2,did);representation.step();
        if(parsed.publication.has("version")&&!stale_publication) {
          publish_policy_locked(owner,url_key,latest,parsed.publication);
          if(representation_key!=url_key)publish_policy_locked(owner,representation_key,latest,final_policy);
        }
        auto out=snapshot_locked(owner, latest);
        out.set("reused", true);
        return out;
      }
    }
    // Reclaim only old, unreferenced versions that this successful write would
    // supersede. A quota/insert failure rolls the deletions back with the write.
    if(!did.empty())prune_locked(did,preserve_current?latest:"",revision+1,owner);
    auto bytes=static_cast<std::int64_t>(input.raw.size()+parsed.text.size()+(publication=="{}"?0:publication.size())+(representation_publication=="{}"?0:representation_publication.size())+(site_bindings=="{}"?0:site_bindings.size()))+http_bytes;
    {
      Stmt used(db_, "SELECT bytes,snapshots FROM usage WHERE owner=?");
      used.bind(1, owner);
      std::int64_t n=0;
      if(used.step()) {
        n=used.num(1);
      }
      require(payload_quota_fits_locked(owner,bytes)&&n+1<=options_.max_snapshots_per_owner, "E_QUOTA", "Owner snapshot and archive quota exceeded", 507);
    }
    const bool new_document=did.empty();
    if(new_document) {
      did=random_id();
      Stmt d(db_, "INSERT INTO documents(id,owner,url,host,url_key,representation_key,deletion_key)VALUES(?,?,?,?,?,?,?)");
      d.bind(1, did);
      d.bind(2, owner);
      d.bind(3, input.url);
      d.bind(4, host);
      d.bind(5, url_key);d.bind(6,representation_key);
      d.bind(7,deletion_url_key(input.url));
      d.step();
    }
    auto sid=random_id();
    auto eid=random_id();
    Stmt ins(db_, "INSERT INTO snapshots(id,extraction_id,document_id,revision,title,canonical_text,raw_content,raw_hash,content_type,parser_version,created_at,index_allowed,links_json,meta_json,source_kind,publication_json,representation_publication_json,site_bindings_json,site_publication_generation,http_cache_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
    ins.bind(1, sid);
    ins.bind(2, eid);
    ins.bind(3, did);
    ins.bind(4, revision+1);
    ins.bind(5, parsed.title);
    ins.bind(6, parsed.text);
    ins.bind(7, input.raw);
    ins.bind(8, rawhash);
    ins.bind(9, input.content_type);
    ins.bind(10, parsed.parser_version);
    ins.bind(11, clock_());
    ins.bind(12, static_cast<std::int64_t>(parsed.index_allowed));
    ins.bind(13, parsed.links.dump());
    ins.bind(14, meta_json);
    ins.bind(15, source_kind);
    ins.bind(16, publication);ins.bind(17,representation_publication);
    ins.bind(18,site_bindings);ins.bind(19,site_generation);ins.bind(20,http_bytes);
    ins.step();
    if(preserve_current) {
      Stmt upd(db_,"UPDATE documents SET revision=revision+1 WHERE id=?");upd.bind(1,did);upd.step();
    } else {
      Stmt upd(db_, "UPDATE documents SET current_snapshot=?,revision=revision+1,deleted=0,representation_key=? WHERE id=?");
      upd.bind(1, sid);upd.bind(2, representation_key);upd.bind(3,did);upd.step();
    }
    usage_add_locked(owner, bytes, 1);
    if(!preserve_current) {
    // A freshly inserted document cannot own an old derived row. Avoid a full
    // content_fts UNINDEXED-column scan on every first import. Replacements,
    // restores and publication retractions still use the metered removal path.
    if(!new_document)remove_document_index_locked(did);
    Stmt active_policy(db_,"SELECT 1 FROM document_publication WHERE owner=? AND url_key IN (?,?) AND index_allowed=0");
    active_policy.bind(1,owner);active_policy.bind(2,url_key);active_policy.bind(3,representation_key);
    if(parsed.index_allowed&&!active_policy.step()&&site_publication_mode_locked(source_kind,site_bindings,site_generation,input.url,meta_json)<2) {
      index_document_locked(owner,did,parsed.title,parsed.text,input.url);
    }
    if(parsed.publication.has("version")&&!stale_publication) {
      publish_policy_locked(owner,url_key,sid,parsed.publication);
      if(representation_key!=url_key)publish_policy_locked(owner,representation_key,sid,final_policy);
    }
    }
    prune_locked(did, preserve_current?latest:sid, revision+1, owner);
    auto out=snapshot_locked(owner, sid);
    out.set("reused", false);
    return out;
  }
  Json Store::ingest(const std::string& owner, const SourceDocument& input) {
    metadata_host(input.url);
    auto parsed=parse_document(input.raw, input.content_type, input.title);
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto out=ingest_locked(owner, input, parsed, "{}", "import");
    tx.commit();
    return out;
  }
  Json Store::ingest_web_locked(const std::string& owner, const SourceDocument& input, ParsedDocument parsed, const Json& meta, std::int64_t expected_deletion_generation,bool allow_empty) {
    require_principal_active_locked(owner);
    require(expected_deletion_generation>=0,"E_SCHEMA","Invalid deletion fence");
    metadata_host(input.url);
    require(valid_utf8(input.raw)&&valid_utf8(parsed.text)&&valid_utf8(parsed.title), "E_UTF8", "Worker output is not UTF-8", 502);
    require(input.raw.find('\0')==std::string::npos&&parsed.text.find('\0')==std::string::npos, "E_UTF8", "Worker output contains NUL", 502);
    require(allow_empty||!parsed.text.empty(), "E_PARSE_FAILED", "No readable text", 422);
    Json m=meta.is_object()?meta:Json::object();
    require(deletion_generation_locked(owner,input.url)==expected_deletion_generation,"E_TARGET_DELETED",
            "Document was deleted after this read started",409);
    if(m.has("http_revalidation")) {
      const auto binding=m.at("http_revalidation");
      binding.allowed({"version","base_snapshot_id","base_cache_sha256","base_metadata_sha256","binding_id"});
      require(binding.at("version").str()=="bzs-http-revalidation-1","E_WORKER_PROTOCOL","Invalid revalidation version",502);
      Stmt base(db_,"SELECT s.id,s.meta_json,s.raw_hash,s.canonical_text,s.title,s.parser_version,s.content_type,s.links_json,s.publication_json,s.representation_publication_json FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=? AND d.deleted=0 AND s.source_kind='web'");
      base.bind(1,owner);base.bind(2,input.url);
      require(base.step()&&base.text(0)==binding.at("base_snapshot_id").str(),"E_REVALIDATION_STALE","Revalidated body is no longer current",409);
      const auto old=Json::parse(base.text(1),8*1024*1024);
      require(old.has("http_cache")&&sha256(old.at("http_cache").canonical())==binding.at("base_cache_sha256").str()&&
              sha256(old.canonical())==binding.at("base_metadata_sha256").str()&&
              sha256(owner+"\n"+base.text(0)+"\n"+sha256(old.canonical()))==binding.at("binding_id").str(),
              "E_REVALIDATION_STALE","Revalidation base metadata changed",409);
      require(old.has("final_url")&&m.has("final_url")&&old.at("final_url").str()==m.at("final_url").str()&&
              m.has("http_cache")&&m.at("http_cache").at("url").str()==old.at("final_url").str(),
              "E_WORKER_PROTOCOL","304 cannot retarget the retained representation",502);
      require(base.text(2)==sha256(input.raw)&&base.text(3)==parsed.text&&base.text(4)==parsed.title&&base.text(5)==parsed.parser_version&&
              base.text(6)==input.content_type&&Json::parse(base.text(7)).canonical()==parsed.links.canonical(),
              "E_WORKER_PROTOCOL","304 cannot replace the retained body",502);
      const auto old_publication=Json::parse(base.text(8),8*1024*1024);
      require(publication_covers(parsed.publication,old_publication),"E_WORKER_PROTOCOL","304 cannot relax the retained publication policy",502);
      if(m.has("http_redirect_chain")) {
        const auto old_representation=Json::parse(base.text(9),8*1024*1024);
        require(parsed.representation_publication.has("version")&&
                publication_covers(parsed.representation_publication,old_representation.has("version")?old_representation:old_publication),
                "E_WORKER_PROTOCOL","304 cannot relax the retained final publication policy",502);
      }
      parsed.publication=current_publication_ceiling_locked(owner,input.url,parsed.publication);
      if(m.has("http_redirect_chain"))for(std::size_t i=0;i<m.at("redirects").size();++i)
        parsed.publication=current_publication_ceiling_locked(owner,m.at("redirects").at(i).str(),parsed.publication);
      if(parsed.representation_publication.has("version"))parsed.representation_publication=current_publication_ceiling_locked(owner,m.at("final_url").str(),parsed.representation_publication);
      parsed.index_allowed=parsed.publication.at("index_allowed").flag();
      // This is the original body's history boundary, not the newer request's
      // boundary. Restrictions observed before this 304 was dispatched count.
      m.set("page_publication_sequence",old.at("page_publication_sequence"));
      m.set("site_publication_generation",old.at("site_publication_generation"));
    }
    if(m.has("http_redirect_chain")) {
      require(m.has("http_freshness")&&m.has("final_url")&&m.has("redirects"),"E_WORKER_PROTOCOL","Stored redirect proof lacks its final representation",502);
      const auto chain=validate_http_redirect_chain(m.at("http_redirect_chain"),input.url,m.at("final_url").str(),m.at("redirects"));
      require(http_redirect_chain_cacheable(chain),"E_WORKER_PROTOCOL","Stored redirect proof lacks explicit cacheable hop lifetimes",502);
      require(http_read_freshness(m,input.url,clock_(),7*86400).at("store_allowed").flag(),"E_CACHE_POLICY","Redirect cache policy forbids persistent snapshots",409);
    }
    if(m.has("http_freshness"))
      require(http_freshness(m.at("http_freshness"),clock_(),7*86400).at("store_allowed").flag(),
              "E_CACHE_POLICY","Response cache policy forbids persistent snapshots",409);
    SourceDocument doc=input;
    if(static_cast<std::int64_t>(doc.raw.size())>max_raw_bytes) {
      doc.raw.clear();
      m.set("raw_stored", false);
    } else m.set("raw_stored", true);
    if(parsed.text.size()>max_text_bytes) {
      parsed.text=utf8_prefix(parsed.text, max_text_bytes);
      m.set("text_truncated", true);
    }
    parsed.title=utf8_prefix(parsed.title.empty()?std::string("Untitled"):parsed.title, 1024);
    require(deletion_generation_locked(owner,doc.url)==expected_deletion_generation,"E_TARGET_DELETED",
            "Document was deleted after this read started",409);
    prepare_web_publication_locked(owner,doc.url,parsed,m);
    return ingest_locked(owner, doc, parsed, m.dump(), "web");
  }
  Json Store::ingest_web(const std::string& owner, const SourceDocument& input, ParsedDocument parsed, const Json& meta, std::int64_t expected_deletion_generation) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto out=ingest_web_locked(owner,input,std::move(parsed),meta,expected_deletion_generation);
    tx.commit();
    return out;
  }
  Json Store::project_transient_web(const std::string& owner, const std::string& url, ParsedDocument parsed, const Json& meta) {
    // Same fence, history and observation discipline as an ingest, without the
    // snapshot: a persisted success would publish this artifact's policy, so a
    // transient one retains a restrictive policy as an observation instead.
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    require_principal_active_locked(owner);
    refresh_site_publication_locked(false);
    auto m=Json::parse(meta.dump());
    prepare_web_publication_locked(owner,url,parsed,m);
    if(parsed.publication.has("version")&&restrictive_publication(parsed.publication))
      observe_policy_locked(owner,url,m.has("final_url")?m.at("final_url").str():url,parsed.publication);
    const auto generation=m.has("site_publication_generation")?m.at("site_publication_generation").num():site_publication_bound_generation_;
    require(generation>=0&&generation<=site_publication_cached_generation_,"E_SITE_POLICY_STALE","Read publication generation is not active in this store",409);
    const auto mode=site_publication_mode_locked("web",site_bindings_locked(url,m).canonical(),generation,url,m.dump());
    auto deployment=Json::object();deployment.set("version","bzs-site-publication-1");
    deployment.set("mode",mode==2?"withdrawn":mode==1?"links_only":"allowed");
    deployment.set("generation",site_publication_cached_generation_);deployment.set("artifact_generation",generation);
    Json out=Json::object();
    out.set("indexed",parsed.publication.at("index_allowed").flag()&&mode<2);
    out.set("site_publication",deployment);out.set("publication",parsed.publication);
    tx.commit();
    return out;
  }
  Json Store::transient_delivery_projection(const std::string& owner, const std::string& url, const std::string& final_url,
                                            const Json& redirects, const Json& fence, const Json& publication,
                                            const Json& site_publication) {
    // Delivery-time recheck mirroring the stored-result window guard: the body
    // itself is shared, permission to publish it to this member is not.
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    require_principal_active_locked(owner);
    require(epoch_locked(owner)==fence.at("epoch").num(),"E_POLICY_CHANGED","Permissions changed during this read",409);
    require(deletion_generation_locked(owner,url)==fence.at("generation").num(),
            "E_TARGET_DELETED","Document was deleted after this read started",409);
    const auto sequence=fence.at("sequence").num();
    (void)alias_fences_locked(owner,url,final_url,sequence);
    for(std::size_t i=0;i<redirects.size();++i)
      (void)alias_fences_locked(owner,url,redirects.at(i).str(),sequence);
    refresh_site_publication_locked(false);
    auto policy=current_publication_ceiling_locked(owner,url,publication);
    policy=current_publication_ceiling_locked(owner,final_url,policy);
    for(std::size_t i=0;i<redirects.size();++i)policy=current_publication_ceiling_locked(owner,redirects.at(i).str(),policy);
    auto bindings_meta=Json::object();bindings_meta.set("final_url",final_url);bindings_meta.set("redirects",redirects);
    const auto generation=site_publication.at("artifact_generation").num();
    const auto mode=site_publication_mode_locked("web",site_bindings_locked(url,bindings_meta).canonical(),generation,url,bindings_meta.dump());
    auto current=Json::object();current.set("version","bzs-site-publication-1");
    current.set("mode",mode==2?"withdrawn":mode==1?"links_only":"allowed");
    current.set("generation",site_publication_cached_generation_);current.set("artifact_generation",generation);
    Json out=Json::object();
    out.set("indexed",publication.at("index_allowed").flag()&&policy.at("index_allowed").flag()&&mode<2);
    out.set("site_publication",current);
    tx.commit();
    return out;
  }
  std::optional<Json> Store::cached(const std::string& owner, const std::string& url) {
    std::lock_guard lock(mutex_);
    require_principal_active_locked(owner);
    Stmt s(db_, "SELECT s.id,s.created_at FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=? AND d.deleted=0");
    s.bind(1, owner);
    s.bind(2, url);
    if(!s.step())return std::nullopt;
    auto j=snapshot_locked(owner, s.text(0));
    auto meta=j.at("meta");
    j.set("fetched_at_ms", meta.has("fetched_at_ms")&&meta.at("fetched_at_ms").is_integer()?meta.at("fetched_at_ms").num():s.num(1));
    return j;
  }
  void Store::invalidate_web_cache(const std::string& owner,const std::string& url,
                                   const std::string& snapshot_id,const std::string& metadata_sha256) {
    std::lock_guard lock(mutex_);Tx tx(db_);
    Stmt row(db_,"SELECT s.meta_json,s.http_cache_bytes FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=? AND d.deleted=0 AND s.id=? AND s.source_kind='web'");
    row.bind(1,owner);row.bind(2,url);row.bind(3,snapshot_id);
    if(!row.step())return;
    const auto old=Json::parse(row.text(0),8*1024*1024);
    if(sha256(old.canonical())!=metadata_sha256)return;
    auto meta=Json::object(),accounted=Json::object();
    for(const auto& key:old.keys())if(key!="http_cache"&&key!="http_freshness"&&key!="http_redirect_chain"&&key!="http_revalidation")meta.set(key,old.at(key));
    if(meta.has("revalidation_discovery"))accounted.set("revalidation_discovery",meta.at("revalidation_discovery"));
    const auto bytes=accounted.size()?static_cast<std::int64_t>(accounted.canonical().size()):0;
    Stmt update(db_,"UPDATE snapshots SET meta_json=?,http_cache_bytes=? WHERE id=?");
    update.bind(1,meta.dump());update.bind(2,bytes);update.bind(3,snapshot_id);update.step();
    usage_add_locked(owner,bytes-row.num(1),0);tx.commit();
  }
  std::optional<RevalidationBase> Store::revalidation_base(const std::string& owner,const std::string& url) {
    std::lock_guard lock(mutex_);
    require_principal_active_locked(owner);
    Stmt row(db_,"SELECT s.id,s.raw_content,s.canonical_text,s.title,s.parser_version,s.content_type,s.index_allowed,s.links_json,s.meta_json,s.publication_json,s.representation_publication_json FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=? AND d.deleted=0 AND s.source_kind='web'");
    row.bind(1,owner);row.bind(2,url);if(!row.step())return std::nullopt;
    auto meta=Json::parse(row.text(8),8*1024*1024);
    if(!meta.has("http_cache")||!meta.has("page_publication_sequence")||!meta.has("site_publication_generation")||
       !meta.has("raw_stored")||!meta.at("raw_stored").flag()||!meta.has("final_url")||
       !meta.has("redirects")||!meta.at("redirects").is_array()||
       (meta.has("text_truncated")&&meta.at("text_truncated").flag())||
       (meta.has("crawl_publication_urls")&&(meta.at("crawl_publication_urls").size()!=1||meta.at("crawl_publication_urls").at(std::size_t{0}).str()!=url)))return std::nullopt;
    const auto final_url=meta.at("final_url").str();
    const bool direct=final_url==url&&meta.at("redirects").size()==0;
    Json cache;
    try {
      cache=validate_http_cache(meta.at("http_cache"),final_url);
      // A direct response needs the same original-URL deletion fence as a
      // redirect chain. Reimporting an alias cannot reauthorize the old body.
      if(!direct&&!meta.has("publication_fence"))return std::nullopt;
      // Older direct snapshots have no captured sequence. Zero grants no
      // exemption from any retained deletion, including a revived alias.
      const auto sequence=meta.has("publication_fence")?meta.at("publication_fence").at("sequence").num():0;
      (void)alias_fences_locked(owner,url,final_url,sequence);
      if(!direct) {
        if(!meta.has("http_redirect_chain")||!meta.has("http_freshness")||!meta.has("publication_fence"))return std::nullopt;
        const auto chain=validate_http_redirect_chain(meta.at("http_redirect_chain"),url,final_url,meta.at("redirects"));
        if(!http_redirect_chain_cacheable(chain)||cache.at("request_profile").str()!=meta.at("http_freshness").at("request_profile").str()||
           cache.at("request_profile").str()!=chain.at("hops").at(std::size_t{0}).at("http_freshness").at("request_profile").str())return std::nullopt;
        for(std::size_t i=0;i<meta.at("redirects").size();++i) {
          const auto target=meta.at("redirects").at(i).str();
          (void)alias_fences_locked(owner,url,target,sequence);
        }
      }
    } catch(const Error&) { return std::nullopt; }
    RevalidationBase base;base.snapshot_id=row.text(0);base.cache_sha256=sha256(cache.canonical());base.metadata_sha256=sha256(meta.canonical());
    base.document={url,row.text(1),row.text(5),row.text(3)};
    base.parsed.title=row.text(3);base.parsed.text=row.text(2);base.parsed.parser_version=row.text(4);base.parsed.content_type=row.text(5);
    base.parsed.index_allowed=row.num(6)!=0;base.parsed.links=Json::parse(row.text(7));
    base.parsed.publication=Json::parse(row.text(9),8*1024*1024);base.parsed.representation_publication=Json::parse(row.text(10),8*1024*1024);
    if(!base.parsed.publication.has("version"))return std::nullopt;
    // An omitted final policy means it equalled the stored aggregate, before
    // any newer original/intermediate URL restriction is applied to this base.
    if(!direct&&!base.parsed.representation_publication.has("version"))
      base.parsed.representation_publication=base.parsed.publication;
    base.parsed.publication=current_publication_ceiling_locked(owner,url,base.parsed.publication);
    if(!direct)for(std::size_t i=0;i<meta.at("redirects").size();++i)
      base.parsed.publication=current_publication_ceiling_locked(owner,meta.at("redirects").at(i).str(),base.parsed.publication);
    if(base.parsed.representation_publication.has("version"))base.parsed.representation_publication=current_publication_ceiling_locked(owner,final_url,base.parsed.representation_publication);
    base.parsed.index_allowed=base.parsed.publication.at("index_allowed").flag();
    base.meta=meta;base.conditional=http_conditional(cache,sha256(owner+"\n"+base.snapshot_id+"\n"+base.metadata_sha256));
    if(!direct) {
      // A reusable proof can fit storage while its optional conditional grant
      // exceeds the separate wire bound. Skip that optimization before dispatch;
      // the same authorized read will fetch a complete representation once.
      try { (void)http_redirect_conditional(meta.at("http_redirect_chain"),base.conditional); }
      catch(const Error& error) { if(error.code!="E_WORKER_PROTOCOL")throw;return std::nullopt; }
    }
    return base;
  }
  Json Store::snapshot(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    return snapshot_locked(owner, id);
  }
  Json Store::range(const std::string& owner, const std::string& id, std::int64_t start, std::int64_t max_bytes,const Json& http_guard) {
    require(start>=0&&max_bytes>=1&&max_bytes<=262144, "E_SCHEMA", "Invalid range parameters");
    std::lock_guard lock(mutex_);
    const bool read_result=http_guard.is_object()&&http_guard.has("version")&&http_guard.at("version").is_string()&&
      http_guard.at("version").str()=="bzs-http-read-result-1";
    std::unique_ptr<Tx> result_tx;
    if(read_result) {
      // Serialize this member's complete delivery projection with writes from
      // other Store handles. No Worker call or wait occurs in this transaction.
      result_tx=std::make_unique<Tx>(db_);require_principal_active_locked(owner);
      const auto fence=http_guard.at("publication_fence");const auto url=http_guard.at("url").str();
      require(epoch_locked(owner)==fence.at("epoch").num(),"E_POLICY_CHANGED","Permissions changed during this read",409);
      require(deletion_generation_locked(owner,url)==fence.at("generation").num(),
              "E_TARGET_DELETED","Document was deleted after this read started",409);
    }
    auto snap=snapshot_locked(owner, id);
    if(!http_guard.is_null()) {
      require(http_guard.is_object()&&http_guard.has("version")&&http_guard.at("version").is_string(),
              "E_WORKER_PROTOCOL","Invalid internal read guard",502);
      const bool fresh_result=http_guard.at("version").str()=="bzs-http-read-result-1";
      if(fresh_result)http_guard.allowed({"version","url","metadata_sha256","publication_fence"});
      else http_guard.allowed({"version","url","metadata_sha256","max_age_s"});
      require(http_guard.size()==4&&(fresh_result||http_guard.at("version").str()=="bzs-http-cache-hit-1"),
              "E_WORKER_PROTOCOL","Invalid internal read guard",502);
      const auto url=http_guard.at("url").str();const auto meta=snap.at("meta");
      require(snap.at("url").str()==url,"E_WORKER_PROTOCOL","Read guard URL differs from its snapshot",502);
      if(fresh_result) {
        // A 304 updates metadata in place. Do not combine that newer metadata
        // with another physical attempt's response fields or receipt.
        require(sha256(meta.canonical())==http_guard.at("metadata_sha256").str(),
                "E_REVALIDATION_STALE","Read response metadata changed before delivery",409);
      } else {
        Stmt current(db_,"SELECT 1 FROM documents WHERE owner=? AND url=? AND deleted=0 AND current_snapshot=?");
        current.bind(1,owner);current.bind(2,url);current.bind(3,id);
        require(current.step()&&sha256(meta.canonical())==http_guard.at("metadata_sha256").str()&&
                http_read_freshness(meta,url,clock_(),http_guard.at("max_age_s").num()).at("fresh").flag(),
                "E_CACHE_STALE","Cached representation changed or expired",409);
      }
      const bool direct=meta.at("final_url").str()==url&&meta.at("redirects").size()==0;
      require(direct||meta.has("publication_fence"),"E_CACHE_STALE","Cached redirects lack a persistent deletion fence",409);
      const auto sequence=fresh_result?http_guard.at("publication_fence").at("sequence").num():
        (meta.has("publication_fence")?meta.at("publication_fence").at("sequence").num():0);
      try {
        (void)alias_fences_locked(owner,url,meta.at("final_url").str(),sequence);
        if(fresh_result||meta.has("http_redirect_chain")) {
          for(std::size_t i=0;i<meta.at("redirects").size();++i) {
            const auto target=meta.at("redirects").at(i).str();
            (void)alias_fences_locked(owner,url,target,sequence);
          }
        }
      } catch(const Error& error) {
        if(fresh_result||error.code!="E_TARGET_DELETED")throw;
        throw Error("E_CACHE_STALE","A cached URL alias was deleted",409);
      }
    }
    auto text=snap.at("canonical_text").str();
    auto total=static_cast<std::int64_t>(text.size());
    require(start<=total, "E_EVIDENCE_RANGE", "start_byte beyond end of text");
    require(utf8_boundary(text, static_cast<std::size_t>(start)), "E_EVIDENCE_RANGE", "start_byte splits a UTF-8 character");
    auto end=std::min(total, start+max_bytes);
    while(end>start&&!utf8_boundary(text, static_cast<std::size_t>(end)))--end;
    require(start==total||end>start,"E_EVIDENCE_RANGE","max_bytes cannot hold the next UTF-8 character");
    Json out=Json::object();
    out.set("snapshot_id", id);
    out.set("text", text.substr(static_cast<std::size_t>(start), static_cast<std::size_t>(end-start)));
    out.set("start_byte", start);
    out.set("end_byte", end);
    out.set("total_bytes", total);
    out.set("truncated", end<total);
    out.set("next_start_byte", end<total?Json::integer(end):Json());
    if(!http_guard.is_null()) {
      auto current=Json::object();auto policy=current_publication_ceiling_locked(owner,snap.at("url").str(),snap.at("publication"));
      policy=current_publication_ceiling_locked(owner,snap.at("meta").at("final_url").str(),policy);
      const auto redirects=snap.at("meta").at("redirects");
      for(std::size_t i=0;i<redirects.size();++i)policy=current_publication_ceiling_locked(owner,redirects.at(i).str(),policy);
      if(read_result) {
        auto urls=Json::array();urls.push(snap.at("url"));urls.push(snap.at("meta").at("final_url"));
        for(std::size_t i=0;i<redirects.size();++i)urls.push(redirects.at(i));
        policy=seal_publication_locked(policy,publication_history_locked(owner,urls,snap.at("meta").at("page_publication_sequence").num()));
      }
      current.set("indexed",snap.at("indexed").flag()&&policy.at("index_allowed").flag());current.set("site_publication",snap.at("site_publication"));
      out.set("http_publication_projection",current);
    }
    if(result_tx)result_tx->commit();
    return out;
  }
  Json Store::erase(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    require_principal_active_locked(owner);
    Stmt s(db_, "SELECT id FROM documents WHERE id=? AND owner=? AND deleted=0");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Document not found", 404);
    Stmt size(db_, "SELECT COALESCE(SUM(length(CAST(raw_content AS BLOB))+length(CAST(canonical_text AS BLOB))+length(CAST(CASE WHEN publication_json='{}' THEN '' ELSE publication_json END AS BLOB))+length(CAST(CASE WHEN representation_publication_json='{}' THEN '' ELSE representation_publication_json END AS BLOB))+length(CAST(CASE WHEN site_bindings_json='{}' THEN '' ELSE site_bindings_json END AS BLOB))+http_cache_bytes),0),COUNT(*) FROM snapshots WHERE document_id=?");
    size.bind(1, id);
    size.step();
    auto freed=size.num(0), count=size.num(1);
    remove_document_index_locked(id);
    // Evidence, frozen search rows and their cursors that point at these snapshots cascade away;
    // other documents, sessions and jobs of the same owner are unaffected (B2).
    Stmt snap(db_, "DELETE FROM snapshots WHERE document_id=?");
    snap.bind(1, id);
    snap.step();
    auto generation=delete_sequence_locked();
    require(generation<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Deletion sequence exhausted",500);
    ++generation;
    Stmt sequence(db_,"UPDATE settings SET value=? WHERE key='deletion_sequence'");
    sequence.bind(1,generation);
    sequence.step();
    Stmt d(db_, "UPDATE documents SET deleted=1,current_snapshot=NULL,deleted_at=?,delete_generation=? WHERE id=?");
    d.bind(1, clock_());
    d.bind(2, generation);
    d.bind(3, id);
    d.step();
    usage_add_locked(owner, -freed, -count);
    Json out=Json::object();
    out.set("document_id", id);
    out.set("deleted", true);
    out.set("freed_bytes", freed);
    tx.commit();
    return out;
  }
  Json Store::search(const std::string& owner, const Query&q, std::int64_t max_candidates, std::int64_t limit) {
    return search_impl(owner,q,max_candidates,limit,true);
  }
  Json Store::search_once(const std::string& owner,const Query& q,std::int64_t max_candidates,std::int64_t limit) {
    require(limit>=1&&limit<=100,"E_SCHEMA","Single-response result limit out of range");
    return search_impl(owner,q,max_candidates,limit,false);
  }
  Json Store::search_impl(const std::string& owner,const Query& q,std::int64_t max_candidates,std::int64_t limit,bool freeze) {
    require(max_candidates>=1&&max_candidates<=2000&&limit>=1&&limit<=500, "E_SCHEMA", "Search budget out of range");
    auto fts=fts_candidates(q);
    const auto substrings=substring_candidates(q);
    const auto priority=priority_fts_candidates(q);
    std::string sql,word_sql;
    if(fts||priority) {
      // Candidates in BM25 order (title weighted 4x); exact semantics verified below (B4/B5).
      sql=R"SQL(SELECT d.id,s.id,bm25(content_fts,0.0,0.0,4.0,1.0) AS r,MAX(COALESCE(p.restriction_generation,0),COALESCE(rp.restriction_generation,0)),d.representation_key,d.delete_generation
 FROM content_fts JOIN documents d ON d.id=content_fts.document_id JOIN snapshots s ON s.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key
 WHERE content_fts MATCH ? AND content_fts.owner=? AND d.owner=? AND d.deleted=0 AND s.index_allowed=1 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1
 AND bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,'')<2
 ORDER BY r LIMIT ?)SQL";
      if(substrings)sql.insert(sql.find(" ORDER BY r LIMIT ?"),
        " AND d.rowid IN (SELECT rowid FROM substring_fts WHERE substring_fts MATCH ? AND owner=?)\n");
      word_sql=sql;
    }
    if(!fts&&substrings) {
      sql=R"SQL(SELECT d.id,s.id,0.0,MAX(COALESCE(p.restriction_generation,0),COALESCE(rp.restriction_generation,0)),d.representation_key,d.delete_generation
 FROM substring_fts JOIN documents d ON d.rowid=substring_fts.rowid JOIN snapshots s ON s.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key
 WHERE substring_fts MATCH ? AND substring_fts.owner=? AND d.owner=? AND d.deleted=0 AND s.index_allowed=1 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1
 AND bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,'')<2
 ORDER BY s.created_at DESC,d.id LIMIT ?)SQL";
    } else if(!fts) {
      sql=R"SQL(SELECT d.id,s.id,0.0,MAX(COALESCE(p.restriction_generation,0),COALESCE(rp.restriction_generation,0)),d.representation_key,d.delete_generation
 FROM documents d JOIN snapshots s ON s.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key WHERE d.owner=? AND d.deleted=0 AND s.index_allowed=1 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1
 AND bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,'')<2
 ORDER BY s.created_at DESC,d.id LIMIT ?)SQL";
    }
    struct Hit {
      std::string doc,snap;
      std::int64_t score=0,publication_generation=0;
      std::string representation_key;
      std::int64_t deletion_generation=0;
      bool snippet_suppressed=false;
    };
    std::vector<Hit> candidates,hits;
    bool partial=false;
    std::int64_t epoch=0,publication_sequence=0;
    std::function<void()> unlocked;
    const auto current_epoch=[&] {
      Stmt row(db_,"SELECT epoch FROM subjects WHERE owner=?");row.bind(1,owner);
      return row.step()?row.num(0):std::int64_t{0};
    };
    const auto session_capacity=[&] {
      settle_searches_locked();
      Stmt count(db_,"SELECT COUNT(*) FROM sessions WHERE owner=?");count.bind(1,owner);count.step();
      require(count.num(0)<64,"E_QUOTA","Too many active search sessions; wait for expiry",429);
    };
    {
      std::lock_guard lock(mutex_);Tx tx(db_);
      require_principal_active_locked(owner);
      if(freeze)session_capacity();
      refresh_site_publication_locked();
      epoch=freeze?epoch_locked(owner):current_epoch();
      publication_sequence=publication_sequence_locked();
      unlocked=search_unlocked_for_test_;
      // Freeze only small immutable identities/ranks. Copying every candidate's
      // body at once could allocate 2000 MiB for a single allowed query.
      std::unordered_set<std::string> seen;
      const auto gather=[&](const std::string& statement,const std::optional<std::string>& words) {
        Stmt rows(db_,statement);int bind=1;
        if(words||substrings) { rows.bind(bind++,words?*words:*substrings);rows.bind(bind++,owner); }
        rows.bind(bind++,owner);
        if(words&&substrings) { rows.bind(bind++,*substrings);rows.bind(bind++,owner); }
        // Even with zero remaining slots, inspect one additional unique row
        // so partial is truthful. At most max_candidates duplicates can exist.
        rows.bind(bind,max_candidates+1);
        while(rows.step()) {
          const auto id=rows.text(0);
          if(!seen.insert(id).second)continue;
          if(candidates.size()==static_cast<std::size_t>(max_candidates)) { partial=true;break; }
          candidates.push_back({id,rows.text(1),words?static_cast<std::int64_t>(-rows.real(2)*1000.0):0,
                                rows.num(3),rows.text(4),rows.num(5),false});
        }
      };
      if(priority)gather(word_sql,priority);
      // Never intersect the fallback with the priority expression: doing that
      // would change the published raw-substring/OR/field semantics.
      gather(sql,fts);
      tx.commit();
    }
    for(auto candidate:candidates) {
      std::string title,body,url,host,type;
      {
        std::lock_guard lock(mutex_);
        require_principal_active_locked(owner);
        require(current_epoch()==epoch,"E_SESSION_INVALIDATED","Permissions changed; repeat the query",409);
        Stmt row(db_,"SELECT s.title,s.canonical_text,d.url,d.host,s.content_type FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE s.id=? AND d.id=? AND d.owner=? AND d.deleted=0 AND d.delete_generation=?");
        row.bind(1,candidate.snap);row.bind(2,candidate.doc);row.bind(3,owner);row.bind(4,candidate.deletion_generation);
        if(!row.step()) { partial=true;continue; }
        title=row.text(0);body=row.text(1);url=row.text(2);host=row.text(3);type=row.text(4);
      }
      // The statement and transaction are closed before releasing the lock.
      // Exact matching/scoring sees owned strings, never SQLite-backed views.
      if(unlocked)unlocked();
      if(fts&&!priority) {
        if(!matches(q,title,body,url,host,type))continue;
      } else {
        const auto score=match_score(q,title,body,url,host,type);
        if(!score)continue;
        candidate.score=*score;
      }
      hits.push_back(std::move(candidate));
    }
    if(!fts||priority)std::stable_sort(hits.begin(),hits.end(),[](const Hit& a,const Hit& b) {return a.score>b.score;});
    const auto scanned=static_cast<std::int64_t>(candidates.size());
    auto retrieval=priority?"fts5_substring_verified":fts?"fts5_bm25_verified":substrings?"fts5_substring_verified":"bounded_scan_recent";
    std::lock_guard lock(mutex_);Tx tx(db_);
    require_principal_active_locked(owner);
    require(current_epoch()==epoch,"E_SESSION_INVALIDATED","Permissions changed; repeat the query",409);
    refresh_site_publication_locked();
    // New searches may have consumed the last active slot while matching.
    if(freeze)session_capacity();
    const bool changed_publication=publication_sequence_locked()>publication_sequence;
    std::vector<Hit> visible;
    for(auto hit:hits) {
      Stmt row(db_,R"SQL(SELECT s.source_kind,s.site_bindings_json,s.meta_json,d.url,
 MAX(COALESCE(p.index_generation,0),COALESCE(rp.index_generation,0),COALESCE(cp.index_generation,0)),
 MAX(COALESCE(p.snippet_generation,0),COALESCE(rp.snippet_generation,0),COALESCE(cp.snippet_generation,0)),
 CASE WHEN s.source_kind='web' THEN MAX(bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,?),bzs_site_publication(current.source_kind,current.site_bindings_json,current.site_publication_generation,d.url,current.meta_json,'')) ELSE 0 END AS site_mode
 FROM snapshots s JOIN documents d ON d.id=s.document_id JOIN snapshots current ON current.id=d.current_snapshot
 LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key
 LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=?
 LEFT JOIN document_publication cp ON cp.owner=d.owner AND cp.url_key=d.representation_key
 WHERE s.id=? AND d.id=? AND d.owner=? AND d.deleted=0 AND d.delete_generation=?
 AND s.index_allowed=1 AND current.index_allowed=1
 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1 AND COALESCE(cp.index_allowed,1)=1 AND site_mode<2)SQL");
      row.bind(1,hit.representation_key);row.bind(2,hit.representation_key);row.bind(3,hit.snap);
      row.bind(4,hit.doc);row.bind(5,owner);row.bind(6,hit.deletion_generation);
      if(!row.step()||row.num(4)>publication_sequence) { partial=true;continue; }
      hit.snippet_suppressed=row.num(5)>publication_sequence;
      if(changed_publication&&row.text(0)=="web") {
        auto bindings=Json::parse(row.text(1),128*1024);
        if(!bindings.has("version"))bindings=site_bindings_locked(row.text(3),Json::parse(row.text(2),8*1024*1024));
        const auto history=publication_history_locked(owner,bindings.at("urls"),publication_sequence);
        // A restrict-then-allow observation during matching cannot revive an
        // older candidate. Check every frozen redirect identity as well.
        if(history.at("index_generation").num()>0) { partial=true;continue; }
        hit.snippet_suppressed=hit.snippet_suppressed||history.at("snippet_generation").num()>0;
      }
      visible.push_back(std::move(hit));
    }
    hits=std::move(visible);
    if(hits.size()>static_cast<std::size_t>(limit)) {hits.resize(static_cast<std::size_t>(limit));partial=true;}
    if(!freeze) {
      Json results=Json::array();
      if(!hits.empty()) {
        // Reuse the frozen-page authorization/publication projection without
        // persisting a session, its rows, cursor, event log or identity receipt.
        std::string values="WITH local_results(position,document_id,snapshot_id,score,publication_generation,representation_key,site_binding_version,snippet_suppressed) AS (VALUES ";
        for(std::size_t i=0;i<hits.size();++i)values+=(i?",":"")+std::string("(?,?,?,?,?,?,1,?)");
        Stmt rows(db_,values+")"+local_result_sql);
        int parameter=1;
        for(std::size_t i=0;i<hits.size();++i) {
          const auto& h=hits[i];
          rows.bind(parameter++,static_cast<std::int64_t>(i));
          rows.bind(parameter++,h.doc);rows.bind(parameter++,h.snap);rows.bind(parameter++,h.score);
          rows.bind(parameter++,h.publication_generation);rows.bind(parameter++,h.representation_key);
          rows.bind(parameter++,h.snippet_suppressed?1:0);
        }
        rows.bind(parameter++,0);rows.bind(parameter++,owner);
        rows.bind(parameter++,site_publication_legacy_withdrawn_?1:0);rows.bind(parameter,limit);
        const std::optional<Query> query=q;
        while(rows.step())results.push(local_result(rows,query));
      }
      Json out=Json::object();
      out.set("result_mode","single");out.set("hits",results);
      out.set("returned_count",static_cast<std::int64_t>(results.size()));
      out.set("scanned_candidates",scanned);out.set("partial",partial);
      out.set("coverage","authorized_local_documents_only");out.set("retrieval",retrieval);out.set("query",q.json());
      tx.commit();return out;
    }
    const auto now=clock_();
    auto id=random_id();
    Stmt ins(db_, "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)");
    ins.bind(1, id);
    ins.bind(2, owner);
    ins.bind(3, epoch);
    ins.bind(4, q.json().dump());
    ins.bind(5, now);
    ins.bind(6, now+3600000);
    ins.bind(7, scanned);
    ins.bind(8, static_cast<std::int64_t>(partial));
    ins.bind(9, retrieval);
    ins.step();
    std::int64_t p=0;
    for(const auto&h:hits) {
      Stmt row(db_, "INSERT INTO session_rows(session_id,position,document_id,snapshot_id,score,publication_generation,representation_key,site_binding_version,snippet_suppressed)VALUES(?,?,?,?,?,?,?,1,?)");
      row.bind(1, id);
      row.bind(2, p++);
      row.bind(3, h.doc);
      row.bind(4, h.snap);
      row.bind(5, h.score);
      row.bind(6,h.publication_generation);row.bind(7,h.representation_key);row.bind(8,h.snippet_suppressed?1:0);
      row.step();
    }
    Json out=Json::object();
    out.set("session_id", id);
    out.set("result_set_version", id);
    out.set("frozen_count", p);
    out.set("scanned_candidates", scanned);
    out.set("partial", partial);
    out.set("coverage", "authorized_local_documents_only");
    out.set("retrieval", retrieval);
    out.set("query", q.json());
    local_stream_locked(owner,id);
    tx.commit();
    return out;
  }
  Json Store::page(const std::string& owner, const std::string& session_id, const std::string& cursor, std::int64_t page_size) {
    require(page_size>=1&&page_size<=100, "E_SCHEMA", "Page size out of range");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    require_principal_active_locked(owner);
    refresh_site_publication_locked();
    Stmt s(db_, "SELECT auth_epoch,expires_at,partial,retrieval,query_json FROM sessions WHERE id=? AND owner=?");
    s.bind(1, session_id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Search session not found", 404);
    require(s.num(1)>clock_(), "E_SESSION_EXPIRED", "Search session expired", 410);
    require(s.num(0)==epoch_locked(owner), "E_SESSION_INVALIDATED", "Permissions were revoked; repeat search", 409);
    std::optional<Query> query;
    try {
      query=parse_query(Json::parse(s.text(4)).at("ast"));
    }catch(const Error&) {
      query.reset();
    }
    std::int64_t pos=0;
    if(!cursor.empty()) {
      Stmt cu(db_, "SELECT position FROM cursors WHERE token=? AND session_id=?");
      cu.bind(1, cursor);
      cu.bind(2, session_id);
      require(cu.step(), "E_CURSOR", "Cursor does not belong to this session", 400);
      pos=cu.num(0);
    }
    Stmt rows(db_,std::string("WITH local_results AS (SELECT * FROM session_rows WHERE session_id=?)")+local_result_sql);
    rows.bind(1, session_id);
    rows.bind(2, pos);
    rows.bind(3, owner);
    rows.bind(4,site_publication_legacy_withdrawn_?1:0);rows.bind(5, page_size+1);
    Json hits=Json::array();
    bool more=false;
    std::int64_t nextpos=pos;
    while(rows.step()) {
      if(hits.size()==static_cast<std::size_t>(page_size)) {
        more=true;
        nextpos=rows.num(0);
        break;
      }
      hits.push(local_result(rows,query));
      nextpos=rows.num(0)+1;
    }
    Json out=Json::object();
    out.set("session_id", session_id);
    out.set("result_set_version", session_id);
    out.set("hits", hits);
    out.set("has_more", more);
    out.set("partial", s.num(2)!=0);
    out.set("retrieval", s.text(3));
    out.set("coverage", "authorized_local_documents_only");
    if(more) {
      Stmt existing(db_, "SELECT token FROM cursors WHERE session_id=? AND position=?");
      existing.bind(1, session_id);
      existing.bind(2, nextpos);
      std::string token;
      if(existing.step())token=existing.text(0);
      else {
        token=random_id();
        Stmt add(db_, "INSERT INTO cursors VALUES(?,?,?)");
        add.bind(1, token);
        add.bind(2, session_id);
        add.bind(3, nextpos);
        add.step();
      }
      out.set("next_cursor", token);
    }else out.set("next_cursor", Json());
    tx.commit();
    return out;
  }
  Json Store::find(const std::string& owner, const std::string& sid, const std::string& needle, std::int64_t limit) {
    require(!needle.empty()&&needle.size()<=4096&&valid_utf8(needle)&&limit>=1&&limit<=64, "E_SCHEMA", "Invalid find parameters");
    std::lock_guard lock(mutex_);
    auto snap=snapshot_locked(owner, sid);
    auto text=snap.at("canonical_text").str();
    Json a=Json::array();
    std::size_t p=0;
    while((p=text.find(needle, p))!=std::string::npos) {
      auto m=quoted(snap, static_cast<std::int64_t>(p), static_cast<std::int64_t>(p+needle.size()));
      // Surrounding context for reading; the quote itself stays exact.
      auto cs=p>120?p-120:0;
      while(cs>0&&!utf8_boundary(text, cs))--cs;
      auto ce=std::min(text.size(), p+needle.size()+120);
      while(ce<text.size()&&!utf8_boundary(text, ce))++ce;
      m.set("context", text.substr(cs, ce-cs));
      m.set("context_start_byte", static_cast<std::int64_t>(cs));
      a.push(m);
      p+=needle.size();
      if(a.size()>=static_cast<std::size_t>(limit))break;
    }
    Json out=Json::object();
    out.set("matches", a);
    out.set("snapshot_id", sid);
    out.set("more_possible", a.size()>=static_cast<std::size_t>(limit));
    return out;
  }
  Json Store::evidence(const std::string& owner, const std::string& sid, std::int64_t start, std::int64_t end) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto snap=snapshot_locked(owner, sid);
    auto out=quoted(snap, start, end);
    std::string id;
    Stmt old(db_, "SELECT id FROM evidence WHERE snapshot_id=? AND start_byte=? AND end_byte=?");
    old.bind(1, sid);
    old.bind(2, start);
    old.bind(3, end);
    if(old.step())id=old.text(0);
    else {
      require(scalar(db_, "SELECT count(*) FROM evidence")<500000, "E_QUOTA", "Evidence quota reached", 507);
      id=random_id();
      Stmt ins(db_, "INSERT INTO evidence VALUES(?,?,?,?)");
      ins.bind(1, id);
      ins.bind(2, sid);
      ins.bind(3, start);
      ins.bind(4, end);
      ins.step();
    }
    out.set("evidence_id", id);
    tx.commit();
    return out;
  }
  Json Store::evidence_get(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    Stmt s(db_, "SELECT e.snapshot_id,e.start_byte,e.end_byte FROM evidence e JOIN snapshots sn ON sn.id=e.snapshot_id JOIN documents d ON d.id=sn.document_id WHERE e.id=? AND d.owner=? AND d.deleted=0");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Evidence not found", 404);
    auto out=quoted(snapshot_locked(owner, s.text(0)), s.num(1), s.num(2));
    out.set("evidence_id", id);
    return out;
  }
  Json Store::stats(const std::string& owner) {
    std::lock_guard lock(mutex_);
    require_principal_active_locked(owner);
    Json out=Json::object();
    Stmt n(db_, "SELECT count(*) FROM documents WHERE owner=? AND deleted=0");
    n.bind(1, owner);
    n.step();
    out.set("documents", n.num(0));
    Stmt s(db_, "SELECT bytes,snapshots FROM usage WHERE owner=?");
    s.bind(1, owner);
    std::int64_t bytes=0, snaps=0;
    if(s.step()) {
      bytes=s.num(0);
      snaps=s.num(1);
    }
    out.set("snapshots", snaps);
    out.set("content_bytes", bytes);
    out.set("quota_snapshots", options_.max_snapshots_per_owner);
    out.set("quota_bytes", options_.max_bytes_per_owner);
    Stmt grams(db_,"SELECT bytes FROM substring_usage WHERE owner=?");grams.bind(1,owner);
    const auto index_bytes=grams.step()?grams.num(0):0;
    out.set("substring_index_bytes",index_bytes);
    out.set("quota_substring_bytes",options_.max_substring_bytes_per_owner);
    require(bytes>=0&&index_bytes>=0&&bytes<=std::numeric_limits<std::int64_t>::max()-index_bytes,
            "E_STORAGE","Invalid storage byte accounting",500);
    const auto archive_bytes=archive_bytes_locked(owner);
    require(archive_bytes<=std::numeric_limits<std::int64_t>::max()-bytes-index_bytes,"E_STORAGE","Invalid archive accounting",500);
    out.set("archive_bytes",archive_bytes);
    out.set("quota_payload_bytes",options_.max_bytes_per_owner);
    out.set("payload_bytes",bytes+archive_bytes);
    out.set("payload_over_quota",bytes>options_.max_bytes_per_owner||archive_bytes>options_.max_bytes_per_owner-bytes);
    out.set("accounted_storage_bytes",bytes+index_bytes+archive_bytes);
    out.set("substring_index_over_quota",index_bytes>options_.max_substring_bytes_per_owner);
    out.set("storage_accounting_scope","snapshot_payloads_archive_summaries_and_serialized_substring_tokens_not_physical_sqlite_size");
    out.set("sqlite_version", sqlite3_libversion());
    return out;
  }
  Json Store::job_locked(const std::string& owner, const std::string& id) {
    require_principal_active_locked(owner);
    if(auto archived=compacted_job_locked(owner,id))return *archived;
    return job_details_locked(owner,id);
  }
  Json Store::job_details_locked(const std::string& owner,const std::string& id) {
    Stmt s(db_, "SELECT id,fixture_id,state,epoch,attempts,max_attempts,lease_until,deadline,used_pages,result_json,error_code,kind FROM jobs WHERE id=? AND owner=?");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Job not found", 404);
    if(s.text(11)=="web.read_many")return read_job_locked(owner,id);
    if(s.text(11)=="web.map"||s.text(11)=="web.crawl")return map_job_details_locked(owner,id);
    Json j=Json::object();
    j.set("job_id", s.text(0));
    j.set("kind", "fixture.ingest");
    j.set("fixture_id", s.text(1));
    j.set("state", s.text(2));
    j.set("lease_epoch", s.num(3));
    j.set("attempts", s.num(4));
    j.set("max_attempts", s.num(5));
    j.set("lease_until_ms", s.num(6));
    j.set("deadline_ms", s.num(7));
    j.set("used_pages", s.num(8));
    j.set("external_requests", static_cast<std::int64_t>(0));
    j.set("result", s.text(9).empty()?Json():Json::parse(s.text(9)));
    j.set("error_code", s.text(10).empty()?Json():Json::string(s.text(10)));
    return j;
  }
  Json Store::job_create(const std::string& owner, const std::string& fixture, const std::string& key, std::int64_t attempts, std::int64_t timeout) {
    require_identifier(fixture);
    require_identifier(key);
    require(attempts>=1&&attempts<=5&&timeout>=100&&timeout<=3600000, "E_SCHEMA", "Invalid job limits");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    require_principal_active_locked(owner);
    auto epoch=epoch_locked(owner);
    auto fingerprint=sha256(fixture+"|"+std::to_string(attempts)+"|"+std::to_string(timeout));
    {
      Stmt existing(db_, "SELECT id,fingerprint FROM jobs WHERE owner=? AND idempotency_key=?");
      existing.bind(1, owner);
      existing.bind(2, key);
      if(existing.step()) {
        require(existing.text(1)==fingerprint, "E_IDEMPOTENCY_CONFLICT", "Idempotency key reused with different parameters", 409);
        auto out=job_locked(owner, existing.text(0));
        out.set("reused", true);
        tx.commit();
        return out;
      }
    }
    compact_jobs_locked(owner,8);
    job_capacity_locked(owner);
    auto now=clock_();
    auto id=random_id();
    Stmt ins(db_, "INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at,created_delete_sequence)VALUES(?,?,?,?,?,'queued',?,?,?,?,?)");
    ins.bind(1, id);
    ins.bind(2, owner);
    ins.bind(3, fixture);
    ins.bind(4, key);
    ins.bind(5, fingerprint);
    ins.bind(6, epoch);
    ins.bind(7, attempts);
    ins.bind(8, now+timeout);
    ins.bind(9, now);
    ins.bind(10, delete_sequence_locked());
    ins.step();
    auto out=job_locked(owner, id);
    out.set("reused", false);
    tx.commit();
    return out;
  }
  Json Store::job_get(const std::string&owner, const std::string&id) {
    std::lock_guard lock(mutex_);
    return job_locked(owner, id);
  }
  Json Store::job_control(const std::string&owner, const std::string&id, const std::string&action) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto old=job_locked(owner, id);
    require(!old.has("retention"),"E_JOB_RESULTS_EXPIRED","Job details have expired; its permanent summary cannot be resumed",410);
    if(old.at("kind").str()=="web.read_many") {
      auto out=control_read_locked(owner,id,action);
      tx.commit();
      return out;
    }
    if(old.at("kind").str()=="web.map"||old.at("kind").str()=="web.crawl") {
      auto out=control_map_locked(owner,id,action);tx.commit();return out;
    }
    auto state=old.at("state").str();
    std::string next;
    if(action=="pause") {
      if(state=="queued")next="paused";
      else if(state=="running")next="pausing";
      else if(state=="paused"||state=="pausing")next=state;
    } else if(action=="resume") {
      if(state=="paused")next="queued";
    } else if(action=="cancel") {
      if(state=="queued"||state=="paused")next="cancelled";
      else if(state=="running"||state=="pausing")next="cancelling";
      else if(state=="cancelled"||state=="cancelling")next=state;
    }
    require(!next.empty(), "E_JOB_STATE", "Action is not valid for this job state", 409);
    if(next!=state) {
      Stmt upd(db_, "UPDATE jobs SET state=?,epoch=epoch+1 WHERE id=?");
      upd.bind(1, next);
      upd.bind(2, id);
      upd.step();
    }
    auto out=job_locked(owner, id);
    tx.commit();
    return out;
  }
  void Store::settle_interruptions_locked() {
    Stmt s(db_, "UPDATE jobs SET state=CASE state WHEN 'cancelling' THEN 'cancelled' ELSE 'paused' END WHERE kind='fixture.ingest' AND state IN ('cancelling','pausing') AND lease_until<=?");
    s.bind(1, clock_());
    s.step();
  }
  void Store::settle_interruptions() {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    settle_interruptions_locked();
    settle_read_jobs_locked();
    settle_map_jobs_locked();
    tx.commit();
  }
  std::optional<Lease> Store::lease_next(std::int64_t lease_ms,std::stop_token stopping) {
    require(lease_ms>=1&&lease_ms<=60000, "E_SCHEMA", "Invalid lease duration");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    if(stopping.stop_requested())return std::nullopt;
    auto now=clock_();
    settle_interruptions_locked();
    // auth_epoch only moves on real permission changes; document deletes use per-document tombstones.
    Stmt invalid(db_, R"SQL(UPDATE jobs SET state='failed',epoch=epoch+1,error_code=CASE WHEN auth_epoch!=(SELECT epoch FROM subjects WHERE owner=jobs.owner) THEN 'E_POLICY_CHANGED' WHEN deadline<=? THEN 'E_DEADLINE' ELSE 'E_ATTEMPT_BUDGET' END WHERE kind='fixture.ingest' AND state IN ('queued','running') AND (deadline<=? OR auth_epoch!=(SELECT epoch FROM subjects WHERE owner=jobs.owner) OR (attempts>=max_attempts AND (state='queued' OR lease_until<=?))))SQL");
    invalid.bind(1, now);
    invalid.bind(2, now);
    invalid.bind(3, now);
    invalid.step();
    Stmt s(db_, "SELECT id,owner,fixture_id,epoch FROM jobs WHERE kind='fixture.ingest' AND (state='queued' OR (state='running' AND lease_until<=?)) ORDER BY created_at,id LIMIT 1");
    s.bind(1, now);
    if(!s.step()) {
      tx.commit();
      return std::nullopt;
    }
    if(stopping.stop_requested())return std::nullopt;
    Lease l {
      s.text(0), s.text(1), s.text(2), s.num(3)+1
    };
    Stmt u(db_, "UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,used_pages=1,lease_until=? WHERE id=?");
    u.bind(1, l.epoch);
    u.bind(2, now+lease_ms);
    u.bind(3, l.id);
    u.step();
    tx.commit();
    return l;
  }
  bool Store::live_lease_locked(const Lease& l) {
    Stmt s(db_, "SELECT state,epoch,lease_until,deadline,auth_epoch FROM jobs WHERE id=? AND owner=?");
    s.bind(1, l.id);
    s.bind(2, l.owner);
    if(!s.step()||s.text(0)!="running"||s.num(1)!=l.epoch||s.num(2)<=clock_()||s.num(3)<=clock_())return false;
    return s.num(4)==epoch_locked(l.owner);
  }
  bool Store::complete(const Lease& l, const SourceDocument& input, const ParsedDocument& parsed) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    if(!live_lease_locked(l)) {
      tx.commit();
      return false;
    }
    {
      // A target deleted after the job was created must not be resurrected by it.
      Stmt t(db_, "SELECT d.delete_generation,d.deleted_at,j.created_at,j.created_delete_sequence FROM jobs j JOIN documents d ON d.owner=j.owner AND d.url=? WHERE j.id=?");
      t.bind(1, input.url);
      t.bind(2, l.id);
      bool deleted_after_start=false;
      if(t.step()) {
        // Legacy jobs have no sequence. Preserve their old timestamp fence,
        // and reject any deletion recorded after the schema-3 upgrade.
        deleted_after_start=t.num(3)>=0?t.num(0)>t.num(3):
          (t.num(0)>0||(t.num(1)!=0&&t.num(1)>=t.num(2)));
      }
      if(deleted_after_start) {
        Stmt u(db_, "UPDATE jobs SET state='failed',error_code='E_TARGET_DELETED',lease_until=0 WHERE id=? AND epoch=?");
        u.bind(1, l.id);
        u.bind(2, l.epoch);
        u.step();
        tx.commit();
        return false;
      }
    }
    auto result=ingest_locked(l.owner, input, parsed, "{}", "import");
    require(live_lease_locked(l), "E_LEASE_EXPIRED", "Lease/deadline expired before commit", 409);
    // A publish and successful terminal state are committed in the same transaction.
    // Persist identifiers only, not a second copy of private document text.
    Json receipt=Json::object();
    for(const auto&key: {
      "document_id", "snapshot_id", "extraction_id", "revision", "indexed"
    })receipt.set(key, result.at(key));
    Stmt u(db_, "UPDATE jobs SET state='succeeded',result_json=?,lease_until=0 WHERE id=? AND epoch=?");
    u.bind(1, receipt.dump());
    u.bind(2, l.id);
    u.bind(3, l.epoch);
    u.step();
    tx.commit();
    return true;
  }
  void Store::fail(const Lease&l, const std::string&code) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    if(live_lease_locked(l)) {
      Stmt u(db_, "UPDATE jobs SET state='failed',error_code=?,lease_until=0 WHERE id=? AND epoch=?");
      u.bind(1, code);
      u.bind(2, l.id);
      u.bind(3, l.epoch);
      u.step();
    }
    tx.commit();
  }
  std::string Store::source_admit(const std::string& id, std::int64_t min_interval_ms, std::int64_t daily_cap) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto now=clock_();
    auto day=now/86400000;
    Stmt add(db_, "INSERT OR IGNORE INTO source_state(source_id)VALUES(?)");
    add.bind(1, id);
    add.step();
    repair_source_cooldowns(db_,now,&id);
    Stmt s(db_, "SELECT cooldown_until,last_request_at,day,requests_today FROM source_state WHERE source_id=?");
    s.bind(1, id);
    require(s.step(), "E_STORAGE", "Missing source state", 500);
    std::string reason;
    auto today=s.num(2)==day?s.num(3):0;
    if(s.num(0)>now)reason="cooldown";
    else if(min_interval_ms>0&&s.num(1)>0&&now-s.num(1)<min_interval_ms)reason="min_interval";
    else if(daily_cap>0&&today>=daily_cap)reason="daily_cap";
    if(reason.empty()) {
      Stmt u(db_, "UPDATE source_state SET last_request_at=?,day=?,requests_today=? WHERE source_id=?");
      u.bind(1, now);
      u.bind(2, day);
      u.bind(3, today+1);
      u.bind(4, id);
      u.step();
    }
    tx.commit();
    return reason;
  }
  void Store::source_report(const std::string& id, const std::string& status, const std::string& error, std::int64_t cooldown_ms) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto now=clock_();
    Stmt add(db_, "INSERT OR IGNORE INTO source_state(source_id)VALUES(?)");
    add.bind(1, id);
    add.step();
    repair_source_cooldowns(db_,now,&id);
    bool ok=status=="ok"||status=="empty";
    const auto maximum=source_retry_after_limit_ms*(status=="parse_error"?3:1);
    const bool clamped=!ok&&cooldown_ms>maximum;
    cooldown_ms=std::clamp(cooldown_ms,std::int64_t{0},maximum);
    auto diagnostic=error;
    if(clamped&&!diagnostic.starts_with("E_RETRY_AFTER_CLAMPED:"))
      diagnostic="E_RETRY_AFTER_CLAMPED: source backoff exceeded bounded policy; "+diagnostic;
    // A slow success may arrive after a newer CAPTCHA/rate-limit response.
    // It must not cancel a cooldown that is still in force.
    Stmt u(db_, ok?
      "UPDATE source_state SET failures=0,last_status=?,last_error=?,last_ok_at=? WHERE source_id=?":
      "UPDATE source_state SET failures=failures+1,last_status=?,last_error=?,cooldown_until=MAX(cooldown_until,?) WHERE source_id=?");
    u.bind(1, status);
    u.bind(2, utf8_prefix(diagnostic, 300));
    u.bind(3, ok?now:(cooldown_ms>0?source_cooldown_until(now,cooldown_ms):0));
    u.bind(4, id);
    u.step();
    if(!ok&&cooldown_ms>0) {
      auto proof=source_cooldown_proof(db_,id);
      // Do not bless the unknown older MAX deadline in source_state. This
      // marker covers only this bounded report and previous proven reports.
      proof.set("verified_until_ms",std::max(proof.at("verified_until_ms").num(),source_cooldown_until(now,cooldown_ms)));
      save_source_cooldown_proof(db_,id,proof);
    }
    tx.commit();
  }
  Json Store::source_states() {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    Json out=Json::object();
    auto now=clock_();
    repair_source_cooldowns(db_,now);
    Stmt s(db_, "SELECT source_id,cooldown_until,failures,last_status,last_error,last_ok_at,last_request_at,day,requests_today FROM source_state");
    while(s.step()) {
      Json j=Json::object();
      j.set("cooldown_remaining_ms", source_cooldown_remaining(s.num(1),now));
      j.set("consecutive_failures", s.num(2));
      j.set("last_status", s.text(3));
      j.set("last_error", s.text(4));
      j.set("last_ok_at_ms", s.num(5));
      j.set("last_request_at_ms", s.num(6));
      j.set("requests_today", s.num(7)==now/86400000?s.num(8):0);
      const auto proof=source_cooldown_proof(db_,s.text(0));
      if(proof.has("last_repair"))j.set("cooldown_last_repair",proof.at("last_repair"));
      out.set(s.text(0), j);
    }
    tx.commit();
    return out;
  }
  Json Store::backup(const std::filesystem::path& dst) {
    std::lock_guard lock(mutex_);
    require(!std::filesystem::exists(dst), "E_BACKUP_EXISTS", "Backup destination already exists", 409);
    if(!dst.parent_path().empty())std::filesystem::create_directories(dst.parent_path());
    // The staging name stays ASCII: deriving it from a non-ASCII destination
    // filename would round-trip through the ANSI code page and can corrupt it.
    const auto stage=dst.parent_path()/("bzs-backup-"+random_id()+".partial");
    struct Cleanup {
      std::filesystem::path path;
      ~Cleanup() {
        for(const auto& suffix : {"", "-wal", "-shm", "-journal"}) {
          std::error_code ignored;
          std::filesystem::path victim=path; victim+=suffix;
          std::filesystem::remove(victim, ignored);
        }
      }
    } cleanup{stage};
    sqlite3*raw=nullptr;
    const auto open_rc=sqlite3_open_v2(utf8_path(stage).c_str(), &raw, SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE, nullptr);
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> target(raw, sqlite3_close);
    checked(open_rc, raw);
#ifndef _WIN32
    std::filesystem::permissions(stage, std::filesystem::perms::owner_read|std::filesystem::perms::owner_write);
#endif
    auto*b=sqlite3_backup_init(raw, "main", db_, "main");
    require(b!=nullptr, "E_BACKUP", "Backup initialization failed", 500);
    auto step=sqlite3_backup_step(b, -1);
    auto finish=sqlite3_backup_finish(b);
    checked(step, raw);
    checked(finish, raw);
    require(step==SQLITE_DONE, "E_BACKUP", "Backup is incomplete", 500);
    exec(raw, "PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; UPDATE settings SET value='1' WHERE key='restore_quarantine';");
    auto pages=scalar(raw, "PRAGMA page_count");
    target.reset();
    // Publish only a closed, quarantined file. A hard link refuses to overwrite
    // an existing destination atomically. Unsupported filesystems fail closed.
    std::error_code ec;
    std::filesystem::create_hard_link(stage, dst, ec);
    if(ec) {
      if(std::filesystem::exists(dst))throw Error("E_BACKUP_EXISTS", "Backup destination already exists", 409);
      throw Error("E_BACKUP", "Cannot publish backup safely on this filesystem", 500);
    }
    Json report=Json::object();
    report.set("backup_id", utf8_path(dst.stem()));
    report.set("pages", pages);
    report.set("restore_state", "quarantined");
    report.set("includes_credentials", false);
    report.set("contains_private_content", true);
    report.set("note", "Restoring to an active service is intentionally blocked until revocation reconciliation is implemented");
    return report;
  }
}
