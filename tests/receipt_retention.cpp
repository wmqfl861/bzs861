#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <sqlite3.h>
#include <iostream>
#include <stdexcept>
using namespace bzs;
#define CHECK(x) do{if(!(x))throw std::runtime_error(#x);}while(false)
static std::int64_t count(Store&s,const char*q){sqlite3_stmt*p=nullptr;CHECK(sqlite3_prepare_v2(s.raw_handle(),q,-1,&p,nullptr)==SQLITE_OK);CHECK(sqlite3_step(p)==SQLITE_ROW);auto n=sqlite3_column_int64(p,0);sqlite3_finalize(p);return n;}
int main(){try{
 std::int64_t now=1800000000000;Store s(":memory:",[&]{return now;});auto query=parse_query(Json::string("marker"));
 auto id=s.search("alice",query).at("session_id").str();CHECK(s.session_get("alice",id).at("kind").str()=="local");
 CHECK(sqlite3_exec(s.raw_handle(),R"(WITH RECURSIVE n(x) AS(SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1200)
 INSERT INTO search_stream_receipts(id,owner,state,created_at) SELECT 'legacy-'||x,'alice','completed',0 FROM n)",nullptr,nullptr,nullptr)==SQLITE_OK);
 s.session_get("alice",id);CHECK(count(s,"SELECT COUNT(*) FROM search_stream_receipts WHERE id LIKE 'legacy-%'")==688);
 s.session_get("alice",id);CHECK(count(s,"SELECT COUNT(*) FROM search_stream_receipts WHERE id LIKE 'legacy-%'")==176);
 s.session_get("alice",id);CHECK(count(s,"SELECT COUNT(*) FROM search_stream_receipts WHERE id LIKE 'legacy-%'")==0);
 CHECK(s.session_get("alice",id).at("kind").str()=="local"); // Active FK/paging retained.
 CHECK(sqlite3_exec(s.raw_handle(),"INSERT INTO search_stream_receipts(id,owner,idempotency_key,state,created_at)VALUES('keyed','alice','keep-key','completed',0);INSERT INTO search_stream_receipts(id,owner,state,used_requests,created_at)VALUES('charged','alice','completed',1,0)",nullptr,nullptr,nullptr)==SQLITE_OK);
 now+=3600001;s.search("bob",query);
 CHECK(count(s,"SELECT COUNT(*) FROM search_stream_receipts WHERE id IN ('keyed','charged')")==2);
 CHECK(count(s,"SELECT COUNT(*) FROM search_stream_receipts WHERE idempotency_key IS NULL AND used_requests=0")==1);
 bool missing=false;try{s.session_get("alice",id);}catch(const Error&e){missing=e.code=="E_NOT_FOUND";}CHECK(missing);
 CHECK(count(s,"PRAGMA user_version")==15);CHECK(count(s,"SELECT COUNT(*) FROM pragma_foreign_key_check")==0);
 std::cout<<"bounded legacy cleanup, active payload, keyed/charged preservation and schema15 checks passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
