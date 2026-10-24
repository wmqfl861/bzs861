#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <iostream>
#include <stdexcept>
using namespace bzs;
int main(int argc,char** argv) {
 try {
  if(argc!=2)throw std::runtime_error("query JSON path required");Store store(":memory:");const auto requests=Json::parse(read_file(argv[1]));auto* db=store.raw_handle();
  const char* seed="WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10100) INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at,archived_at,archive_json) SELECT 'history-'||x,'history','fixture','key-'||x,'fixture','cancelled',0,1,1,0,CASE WHEN x<=10000 THEN 1 ELSE 0 END,'{}' FROM n; ANALYZE;";
  if(sqlite3_exec(db,seed,nullptr,nullptr,nullptr)!=SQLITE_OK)throw std::runtime_error(sqlite3_errmsg(db));
  auto result=Json::object();result.set("sqlite_version",sqlite3_libversion());auto plans=Json::object();
  for(const auto& name:requests.keys()) {
   const auto request=requests.at(name);const auto query="EXPLAIN QUERY PLAN "+request.at("sql").str();sqlite3_stmt* s=nullptr;
   if(sqlite3_prepare_v2(db,query.c_str(),-1,&s,nullptr)!=SQLITE_OK)throw std::runtime_error(sqlite3_errmsg(db));
   const auto params=request.at("params");for(std::size_t i=0;i<params.size();++i) {const auto v=params.at(i);if(v.is_string()){const auto str=v.str();sqlite3_bind_text(s,static_cast<int>(i+1),str.c_str(),-1,SQLITE_TRANSIENT);}else sqlite3_bind_int64(s,static_cast<int>(i+1),v.num());}
   auto lines=Json::array();int rc;while((rc=sqlite3_step(s))==SQLITE_ROW)lines.push(Json::string(reinterpret_cast<const char*>(sqlite3_column_text(s,3))));sqlite3_finalize(s);if(rc!=SQLITE_DONE)throw std::runtime_error(sqlite3_errmsg(db));plans.set(name,lines);
  }
  result.set("plans",plans);result.set("archived_fixture_rows",std::int64_t{10000});result.set("unarchived_fixture_rows",std::int64_t{100});std::cout<<result.dump()<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
