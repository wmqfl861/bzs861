#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
using namespace bzs;
#define CHECK(x) do{if(!(x))throw std::runtime_error(#x);}while(false)
static std::int64_t num(Store&s,const char*q){sqlite3_stmt*p=nullptr;CHECK(sqlite3_prepare_v2(s.raw_handle(),q,-1,&p,nullptr)==SQLITE_OK);CHECK(sqlite3_step(p)==SQLITE_ROW);auto n=sqlite3_column_int64(p,0);sqlite3_finalize(p);return n;}
int main(){auto path=std::filesystem::temp_directory_path()/random_id();try{
 std::int64_t now=1800000000000;std::string id;std::int64_t archived=0;
 {Store s(path,[&]{return now;});id=s.job_create("alice","fixture","permanent-key",1,1000).at("job_id").str();s.job_control("alice",id,"cancel");}
 now+=8LL*86400000;
 {StoreOptions o;o.max_bytes_per_owner=10;Store s(path,[&]{return now;},o);auto c=s.compact_jobs("alice");CHECK(c.at("compacted_jobs").num()==0);CHECK(c.at("archive_quota_blocked_jobs").num()==1);CHECK(c.at("has_more_jobs").flag());CHECK(num(s,"SELECT archived_at FROM jobs")==0);CHECK(s.job_create("alice","fixture","permanent-key",1,1000).at("reused").flag());}
 {Store s(path,[&]{return now;});CHECK(s.compact_jobs("alice").at("compacted_jobs").num()==1);auto stats=s.stats("alice");archived=stats.at("archive_bytes").num();CHECK(archived>0);CHECK(archived==num(s,"SELECT SUM(length(CAST(archive_json AS BLOB))) FROM jobs"));CHECK(stats.at("content_bytes").num()==0);CHECK(stats.at("accounted_storage_bytes").num()==archived);CHECK(s.stats("bob").at("archive_bytes").num()==0);}
 {StoreOptions o;o.max_bytes_per_owner=archived+1;Store s(path,[&]{return now;},o);bool quota=false;try{s.ingest("alice",{"https://test.example/page","body","text/plain","title"});}catch(const Error&e){quota=e.code=="E_QUOTA";}CHECK(quota);CHECK(s.stats("alice").at("documents").num()==0);CHECK(s.stats("alice").at("archive_bytes").num()==archived);CHECK(s.job_get("alice",id).at("retention").at("details_available").flag()==false);s.ingest("bob",{"https://test.example/page","body","text/plain","title"});CHECK(s.stats("bob").at("documents").num()==1);}
 {StoreOptions o;o.max_bytes_per_owner=10;Store s(path,[&]{return now;},o);CHECK(s.stats("alice").at("payload_over_quota").flag());CHECK(s.job_create("alice","fixture","permanent-key",1,1000).at("reused").flag());CHECK(num(s,"PRAGMA user_version")==15);CHECK(num(s,"SELECT COUNT(*) FROM pragma_foreign_key_check")==0);}
 std::filesystem::remove(path);std::cout<<"archive bytes, quota rollback, cross-owner isolation, legacy reopening and keyed replay passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';std::error_code ec;std::filesystem::remove(path,ec);return 1;}}
