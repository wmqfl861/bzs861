#include "bzs/store.hpp"
#include <iostream>
#include <string>

int main(int argc,char** argv) {
  if(argc!=3)return 2;
  const bool seed=std::string(argv[1])=="seed";
  const std::int64_t now=1700000000000LL+(seed?0:3600000);
  bzs::Store store(argv[2],[&]{return now;});
  if(seed) {
    if(!store.source_admit("fixture-shared",0,50).empty())return 3;
    store.source_report("fixture-shared","rate_limited","HTTP 429",999999999000LL);
  }
  std::cout<<store.source_states().dump()<<'\n';
}
