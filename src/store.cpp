#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/highlight.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <fstream>
#include <memory>
#include <limits>
#include <tuple>
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
    const char* schema_tables=R"SQL(
 CREATE TABLE IF NOT EXISTS usage(owner TEXT PRIMARY KEY,bytes INTEGER NOT NULL DEFAULT 0,snapshots INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS source_state(source_id TEXT PRIMARY KEY,cooldown_until INTEGER NOT NULL DEFAULT 0,
     failures INTEGER NOT NULL DEFAULT 0,last_status TEXT NOT NULL DEFAULT '',last_error TEXT NOT NULL DEFAULT '',
     last_ok_at INTEGER NOT NULL DEFAULT 0,last_request_at INTEGER NOT NULL DEFAULT 0,day INTEGER NOT NULL DEFAULT 0,
     requests_today INTEGER NOT NULL DEFAULT 0);
 )SQL";
    const char* fts_schema="CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='bzs');";
    bool has_column(sqlite3* db,const char* table,const char* column) {
      Stmt columns(db,std::string("PRAGMA table_info(")+table+")");
      while(columns.step())if(columns.text(1)==column)return true;
      return false;
    }
  }
  Store::Store(const std::filesystem::path& path, std::function<std::int64_t()> clock, StoreOptions options):path_(path), clock_(clock?std::move(clock):now_ms), options_(options) {
    require(options_.max_snapshots_per_owner>=1&&options_.max_bytes_per_owner>=1&&options_.max_revisions_per_document>=1,
            "E_SCHEMA","Invalid store quotas");
    if(path!=":memory:"&&!path.parent_path().empty())std::filesystem::create_directories(path.parent_path());
    auto filename=path.string();
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
      exec(db_, "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;");
      migrate();
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
    require(version>=0&&version<=5,"E_SCHEMA_VERSION","Database is newer than this program",409);
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
 DELETE FROM usage;
 INSERT INTO usage(owner,bytes,snapshots)
   SELECT d.owner,COALESCE(SUM(length(CAST(s.raw_content AS BLOB))+length(CAST(s.canonical_text AS BLOB))),0),COUNT(s.id)
   FROM documents d JOIN snapshots s ON s.document_id=d.id GROUP BY d.owner;
 PRAGMA user_version=3;
 )SQL");
      Stmt tokenizer(db_,"INSERT OR REPLACE INTO settings(key,value)VALUES('fts_tokenizer_version',?)");
      tokenizer.bind(1,std::string(fts_tokenizer_version));
      tokenizer.step();
    } else {
      Stmt tokenizer(db_,"SELECT value FROM settings WHERE key='fts_tokenizer_version'");
      require(tokenizer.step()&&tokenizer.text(0)==fts_tokenizer_version,"E_SCHEMA_VERSION",
              "Index tokenizer version requires an explicit migration",409);
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
    Stmt s(db_, R"SQL(SELECT s.id,s.extraction_id,d.id,d.url,s.title,s.canonical_text,s.raw_hash,s.content_type,
 s.parser_version,s.created_at,s.revision,s.index_allowed,s.links_json,s.meta_json,s.source_kind
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
    j.set("indexed", s.num(11)!=0);
    j.set("links", Json::parse(s.text(12)));
    j.set("meta", Json::parse(s.text(13)));
    j.set("source_kind", s.text(14));
    j.set("content_origin", s.text(14)=="web"?"web_fetch_untrusted":"authorized_input_or_fixture");
    return j;
  }
  void Store::prune_locked(const std::string& document_id, const std::string& keep, std::int64_t newest_revision, const std::string& owner) {
    auto threshold=newest_revision-options_.max_revisions_per_document;
    if(threshold<1)return;
    Stmt list(db_, R"SQL(SELECT id,length(CAST(raw_content AS BLOB))+length(CAST(canonical_text AS BLOB)) FROM snapshots
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
  Json Store::ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed, const std::string& meta_json, const std::string& source_kind) {
    owner_valid(owner);
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
    if(!latest.empty()) {
      Stmt same(db_, "SELECT id FROM snapshots WHERE id=? AND raw_hash=? AND canonical_text=? AND title=? AND parser_version=? AND content_type=? AND index_allowed=?");
      same.bind(1, latest);
      same.bind(2, rawhash);
      same.bind(3, parsed.text);
      same.bind(4, parsed.title);
      same.bind(5, parsed.parser_version);
      same.bind(6, input.content_type);
      same.bind(7, static_cast<std::int64_t>(parsed.index_allowed));
      if(same.step()) {
        if(source_kind=="web") {
          // Unchanged content: only fetch provenance (meta.fetched_at_ms) moves; the snapshot text stays immutable.
          Stmt touch(db_, "UPDATE snapshots SET meta_json=? WHERE id=?");
          touch.bind(1, meta_json);
          touch.bind(2, latest);
          touch.step();
        }
        auto out=snapshot_locked(owner, latest);
        out.set("reused", true);
        return out;
      }
    }
    // Reclaim only old, unreferenced versions that this successful write would
    // supersede. A quota/insert failure rolls the deletions back with the write.
    if(!did.empty())prune_locked(did,"",revision+1,owner);
    auto bytes=static_cast<std::int64_t>(input.raw.size()+parsed.text.size());
    {
      Stmt used(db_, "SELECT bytes,snapshots FROM usage WHERE owner=?");
      used.bind(1, owner);
      std::int64_t b=0, n=0;
      if(used.step()) {
        b=used.num(0);
        n=used.num(1);
      }
      require(b+bytes<=options_.max_bytes_per_owner&&n+1<=options_.max_snapshots_per_owner, "E_QUOTA", "Owner snapshot quota exceeded", 507);
    }
    if(did.empty()) {
      did=random_id();
      Stmt d(db_, "INSERT INTO documents(id,owner,url,host)VALUES(?,?,?,?)");
      d.bind(1, did);
      d.bind(2, owner);
      d.bind(3, input.url);
      d.bind(4, host);
      d.step();
    }
    auto sid=random_id();
    auto eid=random_id();
    Stmt ins(db_, "INSERT INTO snapshots(id,extraction_id,document_id,revision,title,canonical_text,raw_content,raw_hash,content_type,parser_version,created_at,index_allowed,links_json,meta_json,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
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
    ins.step();
    Stmt upd(db_, "UPDATE documents SET current_snapshot=?,revision=revision+1,deleted=0 WHERE id=?");
    upd.bind(1, sid);
    upd.bind(2, did);
    upd.step();
    usage_add_locked(owner, bytes, 1);
    Stmt del(db_, "DELETE FROM content_fts WHERE document_id=?");
    del.bind(1, did);
    del.step();
    if(parsed.index_allowed) {
      Stmt ix(db_, "INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)");
      ix.bind(1, did);
      ix.bind(2, owner);
      ix.bind(3, parsed.title);
      ix.bind(4, parsed.text);
      ix.step();
    }
    prune_locked(did, sid, revision+1, owner);
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
  Json Store::ingest_web_locked(const std::string& owner, const SourceDocument& input, ParsedDocument parsed, const Json& meta, std::int64_t expected_deletion_generation) {
    require(expected_deletion_generation>=0,"E_SCHEMA","Invalid deletion fence");
    metadata_host(input.url);
    require(valid_utf8(input.raw)&&valid_utf8(parsed.text)&&valid_utf8(parsed.title), "E_UTF8", "Worker output is not UTF-8", 502);
    require(input.raw.find('\0')==std::string::npos&&parsed.text.find('\0')==std::string::npos, "E_UTF8", "Worker output contains NUL", 502);
    require(!parsed.text.empty(), "E_PARSE_FAILED", "No readable text", 422);
    Json m=meta.is_object()?meta:Json::object();
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
    return ingest_locked(owner, doc, parsed, m.dump(), "web");
  }
  Json Store::ingest_web(const std::string& owner, const SourceDocument& input, ParsedDocument parsed, const Json& meta, std::int64_t expected_deletion_generation) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto out=ingest_web_locked(owner,input,std::move(parsed),meta,expected_deletion_generation);
    tx.commit();
    return out;
  }
  std::optional<Json> Store::cached(const std::string& owner, const std::string& url) {
    std::lock_guard lock(mutex_);
    Stmt s(db_, "SELECT s.id,s.created_at FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=? AND d.deleted=0");
    s.bind(1, owner);
    s.bind(2, url);
    if(!s.step())return std::nullopt;
    auto j=snapshot_locked(owner, s.text(0));
    auto meta=j.at("meta");
    j.set("fetched_at_ms", meta.has("fetched_at_ms")&&meta.at("fetched_at_ms").is_integer()?meta.at("fetched_at_ms").num():s.num(1));
    return j;
  }
  Json Store::snapshot(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    return snapshot_locked(owner, id);
  }
  Json Store::range(const std::string& owner, const std::string& id, std::int64_t start, std::int64_t max_bytes) {
    require(start>=0&&max_bytes>=1&&max_bytes<=262144, "E_SCHEMA", "Invalid range parameters");
    std::lock_guard lock(mutex_);
    auto snap=snapshot_locked(owner, id);
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
    return out;
  }
  Json Store::erase(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    Stmt s(db_, "SELECT id FROM documents WHERE id=? AND owner=? AND deleted=0");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Document not found", 404);
    Stmt size(db_, "SELECT COALESCE(SUM(length(CAST(raw_content AS BLOB))+length(CAST(canonical_text AS BLOB))),0),COUNT(*) FROM snapshots WHERE document_id=?");
    size.bind(1, id);
    size.step();
    auto freed=size.num(0), count=size.num(1);
    Stmt ix(db_, "DELETE FROM content_fts WHERE document_id=?");
    ix.bind(1, id);
    ix.step();
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
    require(max_candidates>=1&&max_candidates<=2000&&limit>=1&&limit<=500, "E_SCHEMA", "Search budget out of range");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto epoch=epoch_locked(owner);
    auto now=clock_();
    {
      Stmt clear(db_, "DELETE FROM sessions WHERE expires_at<=?");
      clear.bind(1, now);
      clear.step();
    }
    {
      Stmt n(db_, "SELECT count(*) FROM sessions WHERE owner=?");
      n.bind(1, owner);
      n.step();
      require(n.num(0)<64, "E_QUOTA", "Too many active search sessions; wait for expiry", 429);
    }
    auto fts=fts_candidates(q);
    std::string sql;
    if(fts) {
      // Candidates in BM25 order (title weighted 4x); exact semantics verified below (B4/B5).
      sql=R"SQL(SELECT d.id,s.id,s.title,s.canonical_text,d.url,d.host,s.content_type,bm25(content_fts,0.0,0.0,4.0,1.0) AS r
 FROM content_fts JOIN documents d ON d.id=content_fts.document_id JOIN snapshots s ON s.id=d.current_snapshot
 WHERE content_fts MATCH ? AND content_fts.owner=? AND d.owner=? AND d.deleted=0 AND s.index_allowed=1
 ORDER BY r LIMIT ?)SQL";
    } else {
      sql=R"SQL(SELECT d.id,s.id,s.title,s.canonical_text,d.url,d.host,s.content_type,0.0
 FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.deleted=0 AND s.index_allowed=1
 ORDER BY s.created_at DESC,d.id LIMIT ?)SQL";
    }
    Stmt c(db_, sql);
    int bind=1;
    if(fts) {
      c.bind(bind++, *fts);
      c.bind(bind++, owner);
    }
    c.bind(bind++, owner);
    c.bind(bind, max_candidates+1);
    struct Hit {
      std::string doc, snap;
      std::int64_t score;
    };
    std::vector<Hit> hits;
    std::int64_t scanned=0;
    bool partial=false;
    while(c.step()) {
      if(scanned==max_candidates) {
        partial=true;
        break;
      }
      ++scanned;
      if(!matches(q, c.text(2), c.text(3), c.text(4), c.text(5), c.text(6)))continue;
      auto score=fts?static_cast<std::int64_t>(-c.real(7)*1000.0):static_cast<std::int64_t>(score_match(q.root, c.text(2), c.text(3)));
      hits.push_back( {
        c.text(0), c.text(1), score
      });
    }
    if(!fts)std::stable_sort(hits.begin(), hits.end(), [](const Hit&a, const Hit&b) {
      return a.score>b.score;
    });
    if(hits.size()>static_cast<std::size_t>(limit)) {
      hits.resize(static_cast<std::size_t>(limit));
      partial=true;
    }
    auto retrieval=fts?"fts5_bm25_verified":"bounded_scan_recent";
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
      Stmt row(db_, "INSERT INTO session_rows VALUES(?,?,?,?,?)");
      row.bind(1, id);
      row.bind(2, p++);
      row.bind(3, h.doc);
      row.bind(4, h.snap);
      row.bind(5, h.score);
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
    tx.commit();
    return out;
  }
  Json Store::page(const std::string& owner, const std::string& session_id, const std::string& cursor, std::int64_t page_size) {
    require(page_size>=1&&page_size<=100, "E_SCHEMA", "Page size out of range");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
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
    Stmt rows(db_, "SELECT r.position,r.document_id,r.snapshot_id,r.score,s.title,d.url,s.canonical_text,s.revision FROM session_rows r JOIN documents d ON d.id=r.document_id JOIN snapshots s ON s.id=r.snapshot_id WHERE r.session_id=? AND r.position>=? AND d.owner=? AND d.deleted=0 ORDER BY r.position LIMIT ?");
    rows.bind(1, session_id);
    rows.bind(2, pos);
    rows.bind(3, owner);
    rows.bind(4, page_size+1);
    Json hits=Json::array();
    bool more=false;
    std::int64_t nextpos=pos;
    while(rows.step()) {
      if(hits.size()==static_cast<std::size_t>(page_size)) {
        more=true;
        nextpos=rows.num(0);
        break;
      }
      Json h=Json::object();
      h.set("position", rows.num(0));
      h.set("document_id", rows.text(1));
      h.set("snapshot_id", rows.text(2));
      h.set("score", rows.num(3));
      h.set("title", rows.text(4));
      h.set("url", rows.text(5));
      auto text=rows.text(6);
      if(query) {
        auto hl=highlight(text, query->root);
        h.set("snippet", hl.at("snippet"));
        h.set("snippet_kind", hl.at("snippet_kind"));
        h.set("highlights", hl.at("highlights"));
      } else {
        h.set("snippet", utf8_prefix(text, 350));
        h.set("snippet_kind", "document_prefix");
      }
      h.set("revision", rows.num(7));
      hits.push(h);
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
    out.set("sqlite_version", sqlite3_libversion());
    return out;
  }
  Json Store::job_locked(const std::string& owner, const std::string& id) {
    Stmt s(db_, "SELECT id,fixture_id,state,epoch,attempts,max_attempts,lease_until,deadline,used_pages,result_json,error_code,kind FROM jobs WHERE id=? AND owner=?");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Job not found", 404);
    if(s.text(11)=="web.read_many")return read_job_locked(owner,id);
    if(s.text(11)=="web.map")return map_job_locked(owner,id);
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
    {
      Stmt count(db_, "SELECT count(*) FROM jobs WHERE owner=?");
      count.bind(1, owner);
      count.step();
      require(count.num(0)<10000, "E_QUOTA", "Job retention quota reached", 429);
    }
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
    if(old.at("kind").str()=="web.read_many") {
      auto out=control_read_locked(owner,id,action);
      tx.commit();
      return out;
    }
    if(old.at("kind").str()=="web.map") {
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
  std::optional<Lease> Store::lease_next(std::int64_t lease_ms) {
    require(lease_ms>=1&&lease_ms<=60000, "E_SCHEMA", "Invalid lease duration");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
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
    bool ok=status=="ok"||status=="empty";
    // A slow success may arrive after a newer CAPTCHA/rate-limit response.
    // It must not cancel a cooldown that is still in force.
    Stmt u(db_, ok?
      "UPDATE source_state SET failures=0,last_status=?,last_error=?,last_ok_at=? WHERE source_id=?":
      "UPDATE source_state SET failures=failures+1,last_status=?,last_error=?,cooldown_until=MAX(cooldown_until,?) WHERE source_id=?");
    u.bind(1, status);
    u.bind(2, utf8_prefix(error, 300));
    u.bind(3, ok?now:(cooldown_ms>0?now+cooldown_ms:0));
    u.bind(4, id);
    u.step();
    tx.commit();
  }
  Json Store::source_states() {
    std::lock_guard lock(mutex_);
    Json out=Json::object();
    auto now=clock_();
    Stmt s(db_, "SELECT source_id,cooldown_until,failures,last_status,last_error,last_ok_at,last_request_at,day,requests_today FROM source_state");
    while(s.step()) {
      Json j=Json::object();
      j.set("cooldown_remaining_ms", std::max<std::int64_t>(0, s.num(1)-now));
      j.set("consecutive_failures", s.num(2));
      j.set("last_status", s.text(3));
      j.set("last_error", s.text(4));
      j.set("last_ok_at_ms", s.num(5));
      j.set("last_request_at_ms", s.num(6));
      j.set("requests_today", s.num(7)==now/86400000?s.num(8):0);
      out.set(s.text(0), j);
    }
    return out;
  }
  Json Store::backup(const std::filesystem::path& dst) {
    std::lock_guard lock(mutex_);
    require(!std::filesystem::exists(dst), "E_BACKUP_EXISTS", "Backup destination already exists", 409);
    if(!dst.parent_path().empty())std::filesystem::create_directories(dst.parent_path());
    const auto stage=dst.parent_path()/(dst.filename().string()+".partial-"+random_id());
    struct Cleanup {
      std::filesystem::path path;
      ~Cleanup() {
        for(const auto& suffix : {"", "-wal", "-shm", "-journal"}) {
          std::error_code ignored;
          std::filesystem::remove(path.string()+suffix, ignored);
        }
      }
    } cleanup{stage};
    sqlite3*raw=nullptr;
    const auto open_rc=sqlite3_open_v2(stage.string().c_str(), &raw, SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE, nullptr);
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
    report.set("backup_id", dst.stem().string());
    report.set("pages", pages);
    report.set("restore_state", "quarantined");
    report.set("includes_credentials", false);
    report.set("contains_private_content", true);
    report.set("note", "Restoring to an active service is intentionally blocked until revocation reconciliation is implemented");
    return report;
  }
}
