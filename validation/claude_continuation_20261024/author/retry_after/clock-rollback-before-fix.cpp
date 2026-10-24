#include "bzs/store.hpp"
#include <sqlite3.h>
#include <iostream>
#include <cstdint>
using namespace bzs;
std::int64_t until(Store& store) {
  sqlite3_stmt* stmt=nullptr;
  if(sqlite3_prepare_v2(store.raw_handle(),"SELECT cooldown_until FROM source_state WHERE source_id='shared'",-1,&stmt,nullptr)!=SQLITE_OK)throw std::runtime_error("prepare");
  if(sqlite3_step(stmt)!=SQLITE_ROW)throw std::runtime_error("row");
  const auto value=sqlite3_column_int64(stmt,0);sqlite3_finalize(stmt);return value;
}
int main() {
  bool changed=false;
  for(bool late_rate:{false,true}) {
    std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});
    store.source_admit("shared",0,100);store.source_report("shared","parse_error","parse",3LL*86400000);
    if(late_rate)store.source_report("shared","rate_limited","late429",120000);
    const auto original=until(store);now-=3600000;
    const auto state=store.source_states().at("shared");const auto current=until(store);
    std::cout<<"late_rate="<<late_rate<<" original="<<original<<" after_backward_clock="<<current<<" shortened_ms="<<original-current<<" remaining="<<state.at("cooldown_remaining_ms").num()<<'\n';
    changed=changed||original!=current;
  }
  return changed?1:0;
}
