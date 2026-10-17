#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <fstream>
#include <memory>
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
    Json quoted(const Json& snap, std::int64_t start, std::int64_t end) {
      auto text=snap.at("canonical_text").str();
      require(start>=0&&end>start&&end<=static_cast<std::int64_t>(text.size())&&end-start<=65536, "E_EVIDENCE_RANGE", "Invalid evidence range");
      require(utf8_boundary(text, static_cast<std::size_t>(start))&&utf8_boundary(text, static_cast<std::size_t>(end)), "E_EVIDENCE_RANGE", "Evidence splits a UTF-8 character");
      Json out=Json::object();
      out.set("snapshot_id", snap.at("snapshot_id"));
      out.set("extraction_id", snap.at("extraction_id"));
      out.set("document_id", snap.at("document_id"));
      out.set("start_byte", start);
      out.set("end_byte", end);
      out.set("quote", text.substr(static_cast<std::size_t>(start), static_cast<std::size_t>(end-start)));
      out.set("location_verified", true);
      out.set("semantic_support", "not_evaluated");
      out.set("content_origin", snap.at("content_origin"));
      return out;
    }
  }
  Store::Store(const std::filesystem::path& path, std::function<std::int64_t()> clock):path_(path), clock_(clock?std::move(clock):now_ms) {
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
      sqlite3_limit(db_, SQLITE_LIMIT_LENGTH, 4*1024*1024);
      sqlite3_limit(db_, SQLITE_LIMIT_SQL_LENGTH, 65536);
      sqlite3_limit(db_, SQLITE_LIMIT_EXPR_DEPTH, 64);
      exec(db_, "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA max_page_count=65536;");
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
    auto version=scalar(db_, "PRAGMA user_version");
    require(version<=2, "E_SCHEMA_VERSION", "Database is newer than this program", 409);
    Tx tx(db_);
    if(version==0) {
      exec(db_, R"SQL(
 CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 INSERT INTO settings VALUES('restore_quarantine','0');
 CREATE TABLE subjects(owner TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE documents(id TEXT PRIMARY KEY,owner TEXT NOT NULL,url TEXT NOT NULL,host TEXT NOT NULL,
     current_snapshot TEXT,revision INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,
     UNIQUE(owner,url));
 CREATE TABLE snapshots(id TEXT PRIMARY KEY,extraction_id TEXT UNIQUE NOT NULL,
     document_id TEXT NOT NULL REFERENCES documents(id),revision INTEGER NOT NULL,title TEXT NOT NULL,
     canonical_text TEXT NOT NULL,raw_content TEXT NOT NULL,raw_hash TEXT NOT NULL,content_type TEXT NOT NULL,
     parser_version TEXT NOT NULL,created_at INTEGER NOT NULL,index_allowed INTEGER NOT NULL,
     links_json TEXT NOT NULL,UNIQUE(document_id,revision));
 CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='unicode61');
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
     result_json TEXT,error_code TEXT,UNIQUE(owner,idempotency_key));
 CREATE INDEX jobs_state ON jobs(state,created_at);
 CREATE INDEX snapshots_doc ON snapshots(document_id);
 CREATE INDEX sessions_owner ON sessions(owner,expires_at);
 PRAGMA user_version=1;
 )SQL");
    }
    {
      Stmt s(db_, "SELECT value FROM settings WHERE key='restore_quarantine'");
      require(s.step()&&s.text(0)=="0", "E_RESTORE_QUARANTINED", "Backup cannot be served before an independently verified revocation reconciliation", 409);
    }
    if(version<2) {
      // Rebuild candidates from current publishable snapshots in one transaction.
      // A private ASCII projection makes FTS token boundaries agree with the
      // exact predicate, including mixed Unicode text and combining marks.
      exec(db_, R"SQL(
 DROP TABLE content_fts;
 CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,
     tokenize="unicode61 remove_diacritics 0 tokenchars '_'");
 )SQL");
      Stmt rows(db_, "SELECT d.id,d.owner,s.title,s.canonical_text FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.deleted=0 AND s.index_allowed=1");
      while(rows.step()) {
        Stmt add(db_, "INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)");
        add.bind(1, rows.text(0));
        add.bind(2, rows.text(1));
        add.bind(3, fts_index_text(rows.text(2)));
        add.bind(4, fts_index_text(rows.text(3)));
        add.step();
      }
      exec(db_, "PRAGMA user_version=2;");
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
  Json Store::snapshot_locked(const std::string& owner, const std::string& id) {
    Stmt s(db_, R"SQL(SELECT s.id,s.extraction_id,d.id,d.url,s.title,s.canonical_text,s.raw_hash,s.content_type,
 s.parser_version,s.created_at,s.revision,s.index_allowed,s.links_json
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
    j.set("content_origin", "authorized_input_or_fixture");
    return j;
  }
  Json Store::ingest_locked(const std::string& owner, const SourceDocument& input, const ParsedDocument& parsed) {
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
      Stmt same(db_, "SELECT id FROM snapshots WHERE id=? AND raw_hash=? AND canonical_text=? AND title=? AND parser_version=? AND content_type=?");
      same.bind(1, latest);
      same.bind(2, rawhash);
      same.bind(3, parsed.text);
      same.bind(4, parsed.title);
      same.bind(5, parsed.parser_version);
      same.bind(6, input.content_type);
      if(same.step()) {
        auto out=snapshot_locked(owner, latest);
        out.set("reused", true);
        return out;
      }
    }
    Stmt used(db_, "SELECT COALESCE(SUM(length(CAST(s.raw_content AS BLOB))+length(CAST(s.canonical_text AS BLOB))),0),COUNT(*) FROM snapshots s JOIN documents d ON d.id=s.document_id WHERE d.owner=?");
    used.bind(1, owner);
    used.step();
    require(used.num(0)+static_cast<std::int64_t>(input.raw.size()+parsed.text.size())<=32*1024*1024 && used.num(1)<2000, "E_QUOTA", "Owner snapshot quota exceeded", 507);
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
    Stmt ins(db_, "INSERT INTO snapshots(id,extraction_id,document_id,revision,title,canonical_text,raw_content,raw_hash,content_type,parser_version,created_at,index_allowed,links_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)");
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
    ins.step();
    Stmt upd(db_, "UPDATE documents SET current_snapshot=?,revision=revision+1,deleted=0 WHERE id=?");
    upd.bind(1, sid);
    upd.bind(2, did);
    upd.step();
    Stmt del(db_, "DELETE FROM content_fts WHERE document_id=?");
    del.bind(1, did);
    del.step();
    if(parsed.index_allowed) {
      Stmt ix(db_, "INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)");
      ix.bind(1, did);
      ix.bind(2, owner);
      ix.bind(3, fts_index_text(parsed.title));
      ix.bind(4, fts_index_text(parsed.text));
      ix.step();
    }
    auto out=snapshot_locked(owner, sid);
    out.set("reused", false);
    return out;
  }
  Json Store::ingest(const std::string& owner, const SourceDocument& input) {
    metadata_host(input.url);
    auto parsed=parse_document(input.raw, input.content_type, input.title);
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto out=ingest_locked(owner, input, parsed);
    tx.commit();
    return out;
  }
  Json Store::snapshot(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    return snapshot_locked(owner, id);
  }
  Json Store::erase(const std::string& owner, const std::string& id) {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    Stmt s(db_, "SELECT id FROM documents WHERE id=? AND owner=? AND deleted=0");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Document not found", 404);
    Stmt ix(db_, "DELETE FROM content_fts WHERE document_id=?");
    ix.bind(1, id);
    ix.step();
    Stmt snap(db_, "DELETE FROM snapshots WHERE document_id=?");
    snap.bind(1, id);
    snap.step();
    Stmt d(db_, "UPDATE documents SET deleted=1,current_snapshot=NULL WHERE id=?");
    d.bind(1, id);
    d.step();
    Stmt ep(db_, "UPDATE subjects SET epoch=epoch+1 WHERE owner=?");
    ep.bind(1, owner);
    ep.step();
    Json out=Json::object();
    out.set("document_id", id);
    out.set("deleted", true);
    out.set("auth_epoch", epoch_locked(owner));
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
    std::string sql=R"SQL(SELECT d.id,s.id,s.title,s.canonical_text,d.url,d.host,s.content_type FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.deleted=0 AND s.index_allowed=1 )SQL";
    if(fts)sql+=" AND d.id IN (SELECT document_id FROM content_fts WHERE content_fts MATCH ? AND owner=?) ";
    sql+=" ORDER BY d.id LIMIT ?";
    Stmt c(db_, sql);
    c.bind(1, owner);
    int bind=2;
    if(fts) {
      c.bind(bind++, *fts);
      c.bind(bind++, owner);
    }
    c.bind(bind, max_candidates+1);
    struct Hit {
      std::string doc, snap;
      int score;
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
      if(matches(q, c.text(2), c.text(3), c.text(4), c.text(5), c.text(6)))hits.push_back( {
        c.text(0), c.text(1), score_match(q.root, c.text(2), c.text(3))
      });
    }
    std::sort(hits.begin(), hits.end(), [](const Hit&a, const Hit&b) {
      return a.score!=b.score?a.score>b.score:a.doc<b.doc;
    });
    if(hits.size()>static_cast<std::size_t>(limit)) {
      hits.resize(static_cast<std::size_t>(limit));
      partial=true;
    }
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
    ins.bind(9, fts?"fts5_candidates_verified":"bounded_literal_scan");
    ins.step();
    std::int64_t p=0;
    for(const auto&h:hits) {
      Stmt row(db_, "INSERT INTO session_rows VALUES(?,?,?,?,?)");
      row.bind(1, id);
      row.bind(2, p++);
      row.bind(3, h.doc);
      row.bind(4, h.snap);
      row.bind(5, static_cast<std::int64_t>(h.score));
      row.step();
    }
    Json out=Json::object();
    out.set("session_id", id);
    out.set("result_set_version", id);
    out.set("frozen_count", p);
    out.set("scanned_candidates", scanned);
    out.set("partial", partial);
    out.set("coverage", "authorized_local_documents_only");
    out.set("retrieval", fts?"fts5_candidates_verified":"bounded_literal_scan");
    out.set("query", q.json());
    tx.commit();
    return out;
  }
  Json Store::page(const std::string& owner, const std::string& session_id, const std::string& cursor, std::int64_t page_size) {
    require(page_size>=1&&page_size<=100, "E_SCHEMA", "Page size out of range");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    Stmt s(db_, "SELECT auth_epoch,expires_at,partial,retrieval FROM sessions WHERE id=? AND owner=?");
    s.bind(1, session_id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Search session not found", 404);
    require(s.num(1)>clock_(), "E_SESSION_EXPIRED", "Search session expired", 410);
    require(s.num(0)==epoch_locked(owner), "E_SESSION_INVALIDATED", "Permissions/content were revoked; repeat search", 409);
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
      h.set("snippet", utf8_prefix(rows.text(6), 350));
      h.set("revision", rows.num(7));
      h.set("snippet_kind", "stored_text_preview");
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
      a.push(quoted(snap, static_cast<std::int64_t>(p), static_cast<std::int64_t>(p+needle.size())));
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
      require(scalar(db_, "SELECT count(*) FROM evidence")<50000, "E_QUOTA", "Evidence quota reached", 507);
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
    Stmt s(db_, "SELECT count(*),COALESCE(SUM(length(CAST(sn.raw_content AS BLOB))+length(CAST(sn.canonical_text AS BLOB))),0) FROM snapshots sn JOIN documents d ON d.id=sn.document_id WHERE d.owner=? AND d.deleted=0");
    s.bind(1, owner);
    s.step();
    out.set("snapshots", s.num(0));
    out.set("content_bytes", s.num(1));
    out.set("sqlite_version", sqlite3_libversion());
    return out;
  }
  Json Store::job_locked(const std::string& owner, const std::string& id) {
    Stmt s(db_, "SELECT id,fixture_id,state,epoch,attempts,max_attempts,lease_until,deadline,used_pages,result_json,error_code FROM jobs WHERE id=? AND owner=?");
    s.bind(1, id);
    s.bind(2, owner);
    require(s.step(), "E_NOT_FOUND", "Job not found", 404);
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
      require(count.num(0)<1000, "E_QUOTA", "Job retention quota reached", 429);
    }
    auto now=clock_();
    auto id=random_id();
    Stmt ins(db_, "INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at)VALUES(?,?,?,?,?,'queued',?,?,?,?)");
    ins.bind(1, id);
    ins.bind(2, owner);
    ins.bind(3, fixture);
    ins.bind(4, key);
    ins.bind(5, fingerprint);
    ins.bind(6, epoch);
    ins.bind(7, attempts);
    ins.bind(8, now+timeout);
    ins.bind(9, now);
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
    Stmt s(db_, "UPDATE jobs SET state=CASE state WHEN 'cancelling' THEN 'cancelled' ELSE 'paused' END WHERE state IN ('cancelling','pausing') AND lease_until<=?");
    s.bind(1, clock_());
    s.step();
  }
  void Store::settle_interruptions() {
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    settle_interruptions_locked();
    tx.commit();
  }
  std::optional<Lease> Store::lease_next(std::int64_t lease_ms) {
    require(lease_ms>=1&&lease_ms<=60000, "E_SCHEMA", "Invalid lease duration");
    std::lock_guard lock(mutex_);
    Tx tx(db_);
    auto now=clock_();
    settle_interruptions_locked();
    Stmt invalid(db_, R"SQL(UPDATE jobs SET state='failed',epoch=epoch+1,error_code=CASE WHEN auth_epoch!=(SELECT epoch FROM subjects WHERE owner=jobs.owner) THEN 'E_POLICY_CHANGED' WHEN deadline<=? THEN 'E_DEADLINE' ELSE 'E_ATTEMPT_BUDGET' END WHERE state IN ('queued','running') AND (deadline<=? OR auth_epoch!=(SELECT epoch FROM subjects WHERE owner=jobs.owner) OR (attempts>=max_attempts AND (state='queued' OR lease_until<=?))))SQL");
    invalid.bind(1, now);
    invalid.bind(2, now);
    invalid.bind(3, now);
    invalid.step();
    Stmt s(db_, "SELECT id,owner,fixture_id,epoch FROM jobs WHERE state='queued' OR (state='running' AND lease_until<=?) ORDER BY created_at,id LIMIT 1");
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
    auto result=ingest_locked(l.owner, input, parsed);
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
