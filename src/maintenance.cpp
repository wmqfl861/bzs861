#include "bzs/maintenance.hpp"
#include "bzs/error.hpp"
#include "bzs/tokenizer.hpp"
#include <sqlite3.h>
#include <openssl/evp.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstring>
#include <limits>
#include <memory>
#include <string>
#include <vector>

namespace bzs { namespace {
using Clock=std::chrono::steady_clock;
void check(int rc) {
  if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
  switch(rc&0xff) {
    case SQLITE_BUSY: case SQLITE_LOCKED:
      throw Error("E_STORAGE_BUSY","Stop all database users before offline maintenance",409);
    case SQLITE_FULL: throw Error("E_STORAGE_FULL","Insufficient maintenance workspace",507);
    case SQLITE_INTERRUPT: throw Error("E_MAINTENANCE_TIMEOUT","Maintenance deadline exceeded",408);
    default: throw Error("E_STORAGE","Offline database operation failed (SQLite "+std::to_string(rc&0xff)+")",500);
  }
}
void exec(sqlite3* db,const char* sql){check(sqlite3_exec(db,sql,nullptr,nullptr,nullptr));}
struct Database {
  sqlite3* db=nullptr;
  explicit Database(const std::string& file,int flags) {
    const int rc=sqlite3_open_v2(file.c_str(),&db,flags,nullptr);
    if(rc!=SQLITE_OK){if(db)sqlite3_close(db);db=nullptr;check(rc);}
    sqlite3_extended_result_codes(db,1);sqlite3_busy_timeout(db,0);
    sqlite3_limit(db,SQLITE_LIMIT_LENGTH,16*1024*1024);
    sqlite3_limit(db,SQLITE_LIMIT_SQL_LENGTH,65536);
    sqlite3_limit(db,SQLITE_LIMIT_EXPR_DEPTH,64);
  }
  ~Database(){if(db)sqlite3_close(db);}
  Database(const Database&)=delete;
};
struct Statement {
  sqlite3_stmt* s=nullptr;
  Statement(sqlite3* db,const std::string& sql){check(sqlite3_prepare_v2(db,sql.c_str(),-1,&s,nullptr));}
  ~Statement(){sqlite3_finalize(s);}
  void bind(const std::string& text){check(sqlite3_bind_text(s,1,text.data(),static_cast<int>(text.size()),SQLITE_TRANSIENT));}
  bool next(){const int rc=sqlite3_step(s);check(rc);return rc==SQLITE_ROW;}
  std::string text(int n)const {
    const auto* p=sqlite3_column_text(s,n);
    return p?std::string(reinterpret_cast<const char*>(p),static_cast<std::size_t>(sqlite3_column_bytes(s,n))):"";
  }
  std::int64_t number(int n=0)const{return sqlite3_column_int64(s,n);}
};
std::int64_t scalar(sqlite3* db,const char* sql){Statement s(db,sql);require(s.next(),"E_STORAGE","Missing maintenance metadata");return s.number();}
std::string quote(const std::string& s){std::string out="\"";for(char c:s){out+=c;if(c=='"')out+=c;}return out+'"';}
std::filesystem::path existing_file(const std::filesystem::path& p) {
  std::error_code ec;
  require(!p.empty()&&p!=":memory:"&&!std::filesystem::is_symlink(p,ec)&&std::filesystem::is_regular_file(p,ec),
          "E_ARGUMENT","Maintenance requires an existing regular database, not a symlink");
  require(std::filesystem::hard_link_count(p,ec)==1&&!ec,"E_ARGUMENT","Hard-linked databases are not supported");
  return std::filesystem::canonical(p);
}
void supported(sqlite3* db) {
  require(scalar(db,"PRAGMA user_version")==15,"E_SCHEMA_VERSION","Maintenance accepts schema15 only; no automatic migration",409);
  Statement q(db,"SELECT value FROM settings WHERE key='restore_quarantine'");
  require(q.next()&&q.text(0)=="0","E_RESTORE_QUARANTINED","Quarantined backups cannot be converted or activated",409);
  Statement f(db,"SELECT value FROM settings WHERE key='fts_tokenizer_version'");
  require(f.next()&&f.text(0)==fts_index_format_version,"E_SCHEMA_VERSION","Unsupported derived index format",409);
  require(scalar(db,"SELECT COUNT(*) FROM sqlite_schema WHERE type IN ('view','trigger')")==0,
          "E_SCHEMA_VERSION","Custom views/triggers require separate maintenance review",409);
  Statement v(db,"SELECT name FROM pragma_table_list WHERE schema='main' AND type='virtual' ORDER BY name");
  std::vector<std::string> names;while(v.next())names.push_back(v.text(0));
  require(names==std::vector<std::string>{"content_fts","substring_fts"},"E_SCHEMA_VERSION","Unsupported virtual tables",409);
}
void integrity(sqlite3* db,bool fts) {
  {Statement s(db,"PRAGMA integrity_check");require(s.next()&&s.text(0)=="ok"&&!s.next(),"E_STORAGE_INTEGRITY","Database integrity check failed",409);}
  {Statement s(db,"PRAGMA foreign_key_check");require(!s.next(),"E_STORAGE_INTEGRITY","Foreign key check failed",409);}
  if(fts) {
    exec(db,"INSERT INTO content_fts(content_fts) VALUES('integrity-check')");
    exec(db,"INSERT INTO substring_fts(substring_fts) VALUES('integrity-check')");
  }
}
// Streaming, typed, length-delimited digest. Includes rowids, FTS shadow bytes
// and virtual content; no document payload/hash is printed or persisted.
class Digest {
  std::unique_ptr<EVP_MD_CTX,decltype(&EVP_MD_CTX_free)> ctx{EVP_MD_CTX_new(),EVP_MD_CTX_free};
 public:
  Digest(){require(ctx&&EVP_DigestInit_ex(ctx.get(),EVP_sha256(),nullptr)==1,"E_STORAGE","Digest initialization failed");}
  void raw(const void* p,std::size_t n){if(n)require(EVP_DigestUpdate(ctx.get(),p,n)==1,"E_STORAGE","Digest update failed");}
  void integer(std::uint64_t n){std::array<unsigned char,8> b{};for(int i=7;i>=0;--i){b[static_cast<std::size_t>(i)]=static_cast<unsigned char>(n&255U);n>>=8;}raw(b.data(),b.size());}
  void field(const void* p,std::size_t n){integer(n);raw(p,n);}
  void row(sqlite3_stmt* s) {
    integer(static_cast<std::uint64_t>(sqlite3_column_count(s)));
    for(int i=0;i<sqlite3_column_count(s);++i){int t=sqlite3_column_type(s,i);integer(static_cast<std::uint64_t>(t));
      if(t==SQLITE_INTEGER)integer(static_cast<std::uint64_t>(sqlite3_column_int64(s,i)));
      else if(t==SQLITE_FLOAT){double d=sqlite3_column_double(s,i);std::uint64_t bits=0;static_assert(sizeof(d)==sizeof(bits));std::memcpy(&bits,&d,sizeof(bits));integer(bits);}
      else if(t==SQLITE_TEXT||t==SQLITE_BLOB){const void* p=sqlite3_column_blob(s,i);field(p,static_cast<std::size_t>(sqlite3_column_bytes(s,i)));}
    }
  }
  std::string finish(){std::array<unsigned char,EVP_MAX_MD_SIZE>b{};unsigned n=0;require(EVP_DigestFinal_ex(ctx.get(),b.data(),&n)==1,"E_STORAGE","Digest finalization failed");return {reinterpret_cast<char*>(b.data()),n};}
};
struct Snapshot {std::string schema;std::vector<std::pair<std::string,std::string>> tables;std::int64_t ordinary=0;bool operator==(const Snapshot&)const=default;};
Snapshot snapshot(sqlite3* db) {
  Snapshot out;Digest schema;
  {Statement s(db,"SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name");while(s.next())schema.row(s.s);}
  out.schema=schema.finish();
  Statement list(db,"SELECT name,type,wr FROM pragma_table_list WHERE schema='main' AND name NOT IN ('sqlite_schema','sqlite_temp_schema') ORDER BY name");
  while(list.next()) {
    const auto name=list.text(0);std::string order,projection="*";std::vector<std::string> columns;
    Statement cols(db,"SELECT name,pk FROM pragma_table_info(?1) ORDER BY pk,cid");cols.bind(name);
    while(cols.next()){auto column=cols.text(0);std::transform(column.begin(),column.end(),column.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});columns.push_back(column);if(cols.number(1)>0){if(!order.empty())order+=',';order+=quote(cols.text(0));}}
    if(list.number(2)==0) {
      std::string alias;
      for(const auto* candidate:{"_rowid_","rowid","oid"})if(std::find(columns.begin(),columns.end(),candidate)==columns.end()){alias=candidate;break;}
      require(!alias.empty(),"E_SCHEMA_VERSION","All rowid aliases are shadowed",409);
      projection=quote(alias)+",*";order=quote(alias);
    }
    require(!order.empty(),"E_SCHEMA_VERSION","Cannot order maintenance table",409);
    Digest d;Statement rows(db,"SELECT "+projection+" FROM "+quote(name)+" ORDER BY "+order);
    while(rows.next())d.row(rows.s);
    out.tables.emplace_back(name,d.finish());if(list.text(1)=="table")++out.ordinary;
  }
  return out;
}
struct Deadline {
  Clock::time_point end;
  static int expired(void* p){return Clock::now()>=static_cast<Deadline*>(p)->end?1:0;}
  void check_now(){if(expired(this))check(SQLITE_INTERRUPT);}
};
void copy(sqlite3* dest,sqlite3* source,Deadline& deadline) {
  sqlite3_backup* b=sqlite3_backup_init(dest,"main",source,"main");
  require(b!=nullptr,"E_STORAGE","Cannot prepare temporary maintenance copy",500);
  int rc=SQLITE_OK;
  while(rc==SQLITE_OK){if(Deadline::expired(&deadline)){rc=SQLITE_INTERRUPT;break;}rc=sqlite3_backup_step(b,128);}
  const int finish=sqlite3_backup_finish(b);check(rc);check(finish);
  require(rc==SQLITE_DONE,"E_STORAGE","Temporary copy was not completed",500);
}
Json describe(sqlite3* db) {
  auto result=Json::object();result.set("business_schema",scalar(db,"PRAGMA user_version"));
  result.set("auto_vacuum",scalar(db,"PRAGMA auto_vacuum"));result.set("page_count",scalar(db,"PRAGMA page_count"));
  result.set("free_pages",scalar(db,"PRAGMA freelist_count"));result.set("page_size",scalar(db,"PRAGMA page_size"));return result;
}
} // namespace
Json inspect_storage(const std::filesystem::path& file) {
  auto path=existing_file(file);Database src(path.string(),SQLITE_OPEN_READONLY|SQLITE_OPEN_FULLMUTEX);
  register_fts_tokenizer(src.db);exec(src.db,"BEGIN");supported(src.db);auto out=describe(src.db);
  out.set("status",out.at("auto_vacuum").num()==2?"incremental":"requires_offline_conversion");
  out.set("read_only",true);exec(src.db,"ROLLBACK");return out;
}
Json convert_storage_incremental(const std::filesystem::path& file,int timeout_ms) {
  require(timeout_ms>=1&&timeout_ms<=600000,"E_ARGUMENT","Maintenance timeout must be 1..600000ms");
  auto path=existing_file(file);Deadline deadline{Clock::now()+std::chrono::milliseconds(timeout_ms)};
  Database src(path.string(),SQLITE_OPEN_READWRITE|SQLITE_OPEN_FULLMUTEX);
  // Persist the exclusive SQLite lock across autocommit statements. No rename,
  // sidecar deletion or reopening of the source while the lock is held.
  exec(src.db,"PRAGMA locking_mode=EXCLUSIVE; BEGIN EXCLUSIVE; COMMIT;");
  register_fts_tokenizer(src.db);sqlite3_progress_handler(src.db,1000,Deadline::expired,&deadline);
  supported(src.db);integrity(src.db,false);auto before=describe(src.db);
  if(before.at("auto_vacuum").num()==2){
    // A no-op conversion is still a maintenance request: verify FTS shadow
    // consistency too; ordinary SQLite integrity_check is not a substitute.
    integrity(src.db,true);
    before.set("status","already_incremental");return before;
  }
  const auto pages=before.at("page_count").num(),size=before.at("page_size").num();
  require(pages>0&&size>0&&pages<=(std::numeric_limits<std::int64_t>::max()-16*1024*1024)/(size*3),"E_STORAGE_FULL","Maintenance size is out of range",507);
  const auto needed=static_cast<std::uintmax_t>(pages*size*3+16*1024*1024);
  require(std::filesystem::space(path.parent_path()).available>=needed&&std::filesystem::space(std::filesystem::temp_directory_path()).available>=needed,
          "E_STORAGE_FULL","Insufficient free space for guarded full-file maintenance",507);
  const auto original=snapshot(src.db);deadline.check_now();
  // Empty filename is SQLite's disposable temporary database, not an exported
  // backup. It is never published, promoted or opened through Store.
  Database scratch("",SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_FULLMUTEX);
  register_fts_tokenizer(scratch.db);copy(scratch.db,src.db,deadline);
  sqlite3_progress_handler(scratch.db,1000,Deadline::expired,&deadline);
  exec(scratch.db,"PRAGMA auto_vacuum=INCREMENTAL; VACUUM;");integrity(scratch.db,true);
  require(scalar(scratch.db,"PRAGMA auto_vacuum")==2&&snapshot(scratch.db)==original,
          "E_MAINTENANCE_MISMATCH","Preflight changed logical contents or rowid/index correspondence; source not converted",409);
  deadline.check_now();
  // SQLite journals the source VACUUM atomically. Interrupted work rolls back;
  // this is a rewrite of the still-locked original, not restoration of a copy.
  exec(src.db,"PRAGMA auto_vacuum=INCREMENTAL; VACUUM;");
  try {
    integrity(src.db,true);
    require(snapshot(src.db)==original,"E_MAINTENANCE_MISMATCH","Post-conversion verification failed",500);
  } catch(...) {
    // A completed rewrite with unconfirmed post-validation must not be served.
    // Only tighten the existing gate; no code here can clear a quarantine.
    sqlite3_progress_handler(src.db,0,nullptr,nullptr);
    try {exec(src.db,"UPDATE settings SET value='1' WHERE key='restore_quarantine'");}
    catch(...) {throw Error("E_MAINTENANCE_UNVERIFIED","Post-validation and quarantine write failed; keep the database offline for operator review",500);}
    throw Error("E_MAINTENANCE_QUARANTINED","Rewrite finished but verification did not; database remains quarantined",409);
  }
  auto result=Json::object();result.set("status","converted");result.set("before",before);result.set("after",describe(src.db));
  result.set("ordinary_tables_verified",original.ordinary);result.set("rowids_and_indexes_preserved",true);
  result.set("backup_restore_performed",false);return result;
}
} // namespace bzs
