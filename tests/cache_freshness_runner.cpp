// Keep all original assertions and registrations byte-for-byte in their source.
#define main original_cache_freshness_main
#include "cache_freshness.cpp"
#undef main
#include <charconv>
#include <chrono>
#include <string_view>
int main(int argc,char** argv) {
  std::size_t shard=1,total=1;bool list=false,has_shard=false;
  const auto integer=[](const char* text,std::size_t& out){std::string_view s(text);auto r=std::from_chars(s.data(),s.data()+s.size(),out);return r.ec==std::errc{}&&r.ptr==s.data()+s.size()&&out>0;};
  for(int i=1;i<argc;++i) {
    const std::string_view a(argv[i]);
    if(a=="--list"&&!list)list=true;
    else if(a=="--shard"&&!has_shard&&i+2<argc) {
      has_shard=true;
      if(!integer(argv[++i],shard)||!integer(argv[++i],total)||shard>total||total>cases.size())return 2;
    }else return 2;
  }
  int failed=0;std::size_t executed=0;
  for(std::size_t i=0;i<cases.size();++i)if(i%total==shard-1) {
    const auto& [name,test]=cases[i];++executed;
    if(list){std::cout<<name<<'\n';continue;}
    const auto begin=std::chrono::steady_clock::now();
    std::cout<<"RUN "<<name<<std::endl;
    try{test();std::cout<<"PASS "<<name;}catch(const std::exception&e){++failed;std::cout<<"FAIL "<<name<<": "<<e.what();}
    std::cout<<" seconds="<<std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count()<<std::endl;
  }
  if(!list)std::cout<<executed<<" cases, "<<assertions<<" assertions, "<<failed<<" failed\n";
  return executed==0?2:failed?1:0;
}
