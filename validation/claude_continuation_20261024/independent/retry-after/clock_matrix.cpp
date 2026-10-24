#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <memory>
using namespace bzs;
std::int64_t deadline(Store& s) {
 sqlite3_stmt* q=nullptr;if(sqlite3_prepare_v2(s.raw_handle(),"SELECT cooldown_until FROM source_state WHERE source_id='shared'",-1,&q,nullptr)!=SQLITE_OK)throw std::runtime_error("prepare");
 if(sqlite3_step(q)!=SQLITE_ROW)throw std::runtime_error("row");auto value=sqlite3_column_int64(q,0);sqlite3_finalize(q);return value;
}
int main() {
 int scenarios=0,failures=0;
 for(const auto route:{"states","admit","report"})for(bool reopen:{false,true})for(bool late_rate:{false,true})for(std::int64_t delay:{0LL,60000LL}) {
  const auto path=std::filesystem::temp_directory_path()/("independent-cooldown-"+random_id()+".sqlite");std::int64_t now=1800000000000;
  {
   auto primary=std::make_unique<Store>(path,[&]{return now;});auto actor=std::make_unique<Store>(path,[&]{return now;});
   primary->source_admit("shared",0,100);now+=delay;primary->source_report("shared","parse_error","legitimate",3LL*86400000);
   if(late_rate)primary->source_report("shared","rate_limited","late",120000);
   const auto original=deadline(*primary);now-=30000;
   if(reopen){actor.reset();primary.reset();actor=std::make_unique<Store>(path,[&]{return now;});}
   if(std::string(route)=="states")actor->source_states();
   else if(std::string(route)=="admit")actor->source_admit("shared",0,100);
   else actor->source_report("shared","rate_limited","another shorter response",120000);
   const auto after=deadline(*actor);++scenarios;
   if(after!=original){++failures;std::cout<<"FAIL route="<<route<<" reopen="<<reopen<<" late_rate="<<late_rate<<" report_delay="<<delay<<" shortened_ms="<<original-after<<'\n';}
  }
  std::error_code ec;std::filesystem::remove(path,ec);std::filesystem::remove(path.string()+"-wal",ec);std::filesystem::remove(path.string()+"-shm",ec);
 }
 std::cout<<scenarios<<" temporal cases, "<<failures<<" failures\n";return failures?1:0;
}
