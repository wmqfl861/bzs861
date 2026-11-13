#include "bzs/store.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
using namespace bzs;
#define CHECK(x) do{if(!(x))throw std::runtime_error(#x);}while(false)
static std::int64_t num(Store&s,const char*q){sqlite3_stmt*p=nullptr;CHECK(sqlite3_prepare_v2(s.raw_handle(),q,-1,&p,nullptr)==SQLITE_OK);CHECK(sqlite3_step(p)==SQLITE_ROW);auto n=sqlite3_column_int64(p,0);sqlite3_finalize(p);return n;}
int main(){auto p=std::filesystem::temp_directory_path()/random_id();try{
 std::filesystem::create_directories(p);std::int64_t before=0,after=0;
 {Store s(p/"new.db");CHECK(num(s,"PRAGMA auto_vacuum")==2);
 const auto doc=s.ingest("alice",{"https://keep.example/page","keep evidence marker","text/plain","retained"});const auto snap=doc.at("snapshot_id").str();const auto proof=s.evidence("alice",snap,0,4).at("evidence_id").str();
 auto job=s.job_create("alice","fixture","forever",1,1000).at("job_id").str();s.job_control("alice",job,"pause");
 CHECK(sqlite3_exec(s.raw_handle(),"CREATE TABLE filler(id INTEGER PRIMARY KEY,data BLOB); WITH RECURSIVE n(x) AS(SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<32)INSERT INTO filler SELECT x,zeroblob(32768) FROM n; DROP TABLE filler;PRAGMA wal_checkpoint(TRUNCATE)",nullptr,nullptr,nullptr)==SQLITE_OK);
 before=num(s,"PRAGMA page_count");const auto free=num(s,"PRAGMA freelist_count");CHECK(free>128);
 auto out=s.compact_jobs("alice");CHECK(out.at("storage_reclamation").at("status").str()=="completed");auto nowfree=num(s,"PRAGMA freelist_count");CHECK(free-nowfree>0&&free-nowfree<=128);
 for(int i=0;i<10&&num(s,"PRAGMA freelist_count")>0;++i)s.compact_jobs("alice");
 CHECK(sqlite3_exec(s.raw_handle(),"PRAGMA wal_checkpoint(TRUNCATE)",nullptr,nullptr,nullptr)==SQLITE_OK);after=num(s,"PRAGMA page_count");CHECK(after<before);
 CHECK(s.evidence_get("alice",proof).at("quote").str()=="keep");CHECK(s.job_get("alice",job).at("state").str()=="paused");CHECK(s.job_create("alice","fixture","forever",1,1000).at("reused").flag());CHECK(num(s,"PRAGMA user_version")==15);CHECK(num(s,"SELECT COUNT(*) FROM pragma_foreign_key_check")==0);
 s.backup(p/"quarantine.db");}
 // Convert a disposable fixture to legacy NONE using registered tokenizer.
 {sqlite3* db=nullptr;CHECK(sqlite3_open((p/"new.db").string().c_str(),&db)==SQLITE_OK);register_fts_tokenizer(db);CHECK(sqlite3_exec(db,"PRAGMA auto_vacuum=NONE; VACUUM",nullptr,nullptr,nullptr)==SQLITE_OK);sqlite3_close(db);}
 {Store s(p/"new.db");CHECK(num(s,"PRAGMA auto_vacuum")==0);CHECK(s.compact_jobs("alice").at("storage_reclamation").at("status").str()=="requires_offline_conversion");CHECK(num(s,"PRAGMA auto_vacuum")==0);}
 bool denied=false;try{Store s(p/"quarantine.db");}catch(const Error&e){denied=e.code=="E_RESTORE_QUARANTINED";}CHECK(denied);
 std::cout<<"incremental pages "<<before<<" -> "<<after<<"; 128-page bound, keyed/evidence retention, legacy nonconversion and quarantine preserved\n";
 std::filesystem::remove_all(p);return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';std::error_code ec;std::filesystem::remove_all(p,ec);return 1;}}
