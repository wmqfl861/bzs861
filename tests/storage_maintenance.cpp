#include "bzs/maintenance.hpp"
#include "bzs/store.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <memory>
using namespace bzs;
namespace {
int assertions=0;
#define CHECK(x) do {++assertions;if(!(x))throw std::runtime_error(std::string(#x)+" at "+std::to_string(__LINE__));}while(false)
void sql(sqlite3* db,const char* s){CHECK(sqlite3_exec(db,s,nullptr,nullptr,nullptr)==SQLITE_OK);}
struct DB {sqlite3* p=nullptr;explicit DB(const std::filesystem::path& f){CHECK(sqlite3_open(utf8_path(f).c_str(),&p)==SQLITE_OK);register_fts_tokenizer(p);}~DB(){sqlite3_close(p);}};
std::int64_t num(sqlite3* db,const char* q){sqlite3_stmt*s=nullptr;CHECK(sqlite3_prepare_v2(db,q,-1,&s,nullptr)==SQLITE_OK);std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> h(s,sqlite3_finalize);CHECK(sqlite3_step(s)==SQLITE_ROW);return sqlite3_column_int64(s,0);}
template<class F>void rejects(F f,const std::string& c){try{f();}catch(const Error&e){if(e.code!=c)throw std::runtime_error("expected "+c+", got "+e.code);CHECK(e.code==c);return;}throw std::runtime_error("expected "+c);}
struct Temp {std::filesystem::path p=std::filesystem::temp_directory_path()/("bzs-maint-"+random_id());Temp(){std::filesystem::create_directories(p);}~Temp(){std::error_code e;std::filesystem::remove_all(p,e);}};
struct Fixture {std::string evidence,job,session,doc;};
Fixture fixture(const std::filesystem::path& file,int mode) {
 Fixture f;
 {Store s(file);const auto removed=s.ingest("alice",{"https://x.example/gap","removed", "text/plain","gap"});
 auto doc=s.ingest("alice",{"https://x.example/keep","keep café 中文 evidence node.js", "text/plain","retained"});f.doc=doc.at("document_id").str();
 s.erase("alice",removed.at("document_id").str());
 f.evidence=s.evidence("alice",doc.at("snapshot_id").str(),0,4).at("evidence_id").str();
 f.job=s.job_create("alice","fixture","permanent-key",1,600000).at("job_id").str();s.job_control("alice",f.job,"pause");
 f.session=s.search("alice",parse_query(Json::string("keep"))).at("session_id").str();
 s.revoke_principal("alice","revoked");
 sql(s.raw_handle(),"CREATE TABLE discard(data BLOB); INSERT INTO discard VALUES(zeroblob(1048576)); DROP TABLE discard;");}
 {DB db(file);if(mode==0)sql(db.p,"PRAGMA auto_vacuum=NONE;VACUUM;");if(mode==1)sql(db.p,"PRAGMA auto_vacuum=FULL;");
 // Make physical space to reclaim without disturbing business rows.
 sql(db.p,"CREATE TABLE discard(data BLOB); INSERT INTO discard VALUES(zeroblob(1048576)); DROP TABLE discard;");}
 return f;
}
void convert_keeps_current_database() {
 for(int mode:{0,1}){Temp t;const auto path=t.p/"state.db";auto f=fixture(path,mode);
 auto scan=inspect_storage(path);CHECK(scan.at("read_only").flag());CHECK(scan.at("auto_vacuum").num()==mode);
 const auto result=convert_storage_incremental(path);CHECK(result.at("status").str()=="converted");CHECK(result.at("after").at("auto_vacuum").num()==2);
 CHECK(result.at("ordinary_tables_verified").num()>=38);CHECK(result.at("rowids_and_indexes_preserved").flag());CHECK(!result.at("backup_restore_performed").flag());
  CHECK(convert_storage_incremental(path).at("status").str()=="already_incremental");
  // The no-op path must prove its FTS-specific check actually ran: SQLite
  // >= 3.44 also inspects virtual tables from PRAGMA integrity_check, so the
  // corruption test cannot distinguish the two checks by itself.
  CHECK(convert_storage_incremental(path).at("fts_integrity_checked").flag());
  {Store s(path);CHECK(s.evidence_get("alice",f.evidence).at("quote").str()=="keep");CHECK(s.job_get("alice",f.job).at("state").str()=="paused");
 CHECK(s.job_create("alice","fixture","permanent-key",1,600000).at("reused").flag());CHECK(s.session_get("alice",f.session).is_object());
 CHECK(s.search_once("alice",parse_query(Json::string("café"))).at("returned_count").num()==1);
 CHECK(s.search_once("alice",parse_query(Json::string("中文"))).at("returned_count").num()==1);
 CHECK(num(s.raw_handle(),"PRAGMA user_version")==15);rejects([&]{s.stats("revoked");},"E_PRINCIPAL_REVOKED");}
 }
}
void refusals_do_not_convert() {
 Temp t;const auto path=t.p/"state.db";fixture(path,0);
 rejects([&]{convert_storage_incremental(t.p/"missing");},"E_ARGUMENT");CHECK(!std::filesystem::exists(t.p/"missing"));
 rejects([&]{convert_storage_incremental(path,0);},"E_ARGUMENT");
 {Store live(path);rejects([&]{convert_storage_incremental(path);},"E_STORAGE_BUSY");}
 {DB busy(path);sql(busy.p,"BEGIN IMMEDIATE");rejects([&]{convert_storage_incremental(path);},"E_STORAGE_BUSY");sql(busy.p,"ROLLBACK");}
 {DB db(path);sql(db.p,"PRAGMA user_version=16");}rejects([&]{convert_storage_incremental(path);},"E_SCHEMA_VERSION");
 {DB db(path);sql(db.p,"PRAGMA user_version=15; UPDATE settings SET value='1' WHERE key='restore_quarantine';");}
 rejects([&]{convert_storage_incremental(path);},"E_RESTORE_QUARANTINED");rejects([&]{inspect_storage(path);},"E_RESTORE_QUARANTINED");
 // Test-only reset of a disposable fixture, not a product restore operation.
 {DB db(path);CHECK(num(db.p,"PRAGMA auto_vacuum")==0);sql(db.p,"UPDATE settings SET value='0' WHERE key='restore_quarantine'; CREATE VIEW custom AS SELECT 1;");}
 rejects([&]{convert_storage_incremental(path);},"E_SCHEMA_VERSION");
 {DB db(path);sql(db.p,"DROP VIEW custom; UPDATE settings SET value='unknown-format' WHERE key='fts_tokenizer_version';");}
 rejects([&]{convert_storage_incremental(path);},"E_SCHEMA_VERSION");
}
void already_incremental_still_checks_fts_integrity() {
 Temp t;const auto path=t.p/"state.db";fixture(path,0);
 CHECK(convert_storage_incremental(path).at("status").str()=="converted");
 // Corrupt derived FTS storage: a no-op conversion must never report success.
 {DB db(path);sql(db.p,"DELETE FROM content_fts_data WHERE id=(SELECT min(id) FROM content_fts_data)");}
 rejects([&]{convert_storage_incremental(path);},"E_STORAGE_INTEGRITY");
 {DB db(path);CHECK(num(db.p,"PRAGMA auto_vacuum")==2);}
}
void rowid_change_rejected_before_source_rewrite() {
 Temp t;auto path=t.p/"state.db";fixture(path,0);
 {DB db(path);sql(db.p,"CREATE TABLE custom_gap(v TEXT);INSERT INTO custom_gap(rowid,v)VALUES(77,'keep'),(99,'keep2');");}
 rejects([&]{convert_storage_incremental(path);},"E_MAINTENANCE_MISMATCH");
 {DB db(path);CHECK(num(db.p,"SELECT min(rowid) FROM custom_gap")==77);CHECK(num(db.p,"SELECT max(rowid) FROM custom_gap")==99);CHECK(num(db.p,"PRAGMA auto_vacuum")==0);}
}
void case_insensitive_rowid_aliases_are_refused() {
 Temp t;auto path=t.p/"state.db";fixture(path,0);
 {DB db(path);sql(db.p,"CREATE TABLE custom_aliases(ROWID TEXT, _ROWID_ TEXT, OID TEXT);INSERT INTO custom_aliases VALUES('one','two','three');");}
 rejects([&]{convert_storage_incremental(path);},"E_SCHEMA_VERSION");
 {DB db(path);CHECK(num(db.p,"PRAGMA auto_vacuum")==0);CHECK(num(db.p,"SELECT count(*) FROM custom_aliases")==1);}
}
void deadline_and_readonly_inspection() {
 Temp t;auto path=t.p/"state.db";fixture(path,0);
 // No business sweeps or schema writes by read-only inspection.
 auto before=read_file(path,8*1024*1024);inspect_storage(path);CHECK(read_file(path,8*1024*1024)==before);
 {DB db(path);sql(db.p,"CREATE TABLE large_table(id INTEGER PRIMARY KEY,v BLOB);WITH RECURSIVE n(x)AS(SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<512)INSERT INTO large_table SELECT x,zeroblob(65536) FROM n;");}
 rejects([&]{convert_storage_incremental(path,1);},"E_MAINTENANCE_TIMEOUT");
 {DB db(path);CHECK(num(db.p,"PRAGMA auto_vacuum")==0);CHECK(num(db.p,"SELECT count(*) FROM large_table")==512);CHECK(num(db.p,"PRAGMA user_version")==15);}
}
void unicode_and_space_paths_are_first_class() {
 // Windows narrow strings are ANSI, while SQLite expects UTF-8: paths with
 // non-ASCII characters and spaces must still open, inspect, convert and back
 // up. Wide literals create the real Unicode names on every platform.
 Temp t;const auto dir=t.p/std::filesystem::path(L"数据 库 目录");std::filesystem::create_directories(dir);const auto path=dir/std::filesystem::path(L"元数据 副本.db");
 fixture(path,0);
 auto scan=inspect_storage(path);CHECK(scan.at("auto_vacuum").num()==0);
 const auto result=convert_storage_incremental(path);CHECK(result.at("status").str()=="converted");CHECK(result.at("after").at("auto_vacuum").num()==2);
 CHECK(convert_storage_incremental(path).at("status").str()=="already_incremental");
 {Store s(path);CHECK(s.search_once("alice",parse_query(Json::string("中文"))).at("returned_count").num()==1);}
 const auto copy=dir/std::filesystem::path(L"备份 copy.sqlite");
 {Store s(path);CHECK(s.backup(copy).at("restore_state").str()=="quarantined");}
 CHECK(std::filesystem::exists(copy));
}
}
int main(){try{convert_keeps_current_database();std::cout<<"PASS none_full_conversion_preserves_state\n";refusals_do_not_convert();std::cout<<"PASS lock_schema_quarantine_refusals\n";already_incremental_still_checks_fts_integrity();std::cout<<"PASS already_incremental_fts_integrity\n";rowid_change_rejected_before_source_rewrite();std::cout<<"PASS rowid_change_refused\n";case_insensitive_rowid_aliases_are_refused();std::cout<<"PASS alias_shadowing_refused\n";deadline_and_readonly_inspection();std::cout<<"PASS deadline_and_readonly\n";unicode_and_space_paths_are_first_class();std::cout<<"PASS unicode_space_paths\n";std::cout<<"7 groups / "<<assertions<<" assertions / 0 failures\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
