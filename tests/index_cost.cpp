// Offline cost comparison. Input corpus and new output directory are explicit.
// Never download a corpus or erase an existing directory. See P2-B summary.
#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
using namespace bzs;
int main(int argc,char** argv) {
  try {
    if(argc==3&&std::string(argv[1])=="--reopen") {Store s(argv[2]);std::cout<<s.stats("alice").dump()<<'\n';return 0;}
    if(argc!=3)throw std::runtime_error("usage: index_cost CORPUS NEW_DIRECTORY | --reopen DATABASE");
    std::ifstream in(argv[1],std::ios::binary);std::stringstream ss;ss<<in.rdbuf();const auto pool=ss.str();
    if(pool.size()<=50001||!valid_utf8(pool))throw std::runtime_error("corpus must be UTF-8 and exceed 50001 bytes");
    std::filesystem::path dir=argv[2];if(std::filesystem::exists(dir))throw std::runtime_error("output directory exists");
    std::filesystem::create_directories(dir);std::mt19937_64 rng(7);
    std::uniform_int_distribution<std::size_t> pick(0,pool.size()-50001);
    std::int64_t now=1800000000000,content=0;
    const auto start=std::chrono::steady_clock::now();
    {
      Store s(dir/"metadata.sqlite",[&]{return now;});
      for(int i=0;i<300;++i) {
        now+=1000;auto at=pick(rng);while(at<pool.size()&&!utf8_boundary(pool,at))++at;
        auto end=std::min(pool.size(),at+50000);while(end>at&&!utf8_boundary(pool,end))--end;
        auto text=pool.substr(at,end-at);content+=static_cast<std::int64_t>(text.size());
        s.ingest("alice",{"https://example.com/doc/"+std::to_string(i),text,"text/plain","Doc"});
      }
      if(sqlite3_exec(s.raw_handle(),"PRAGMA wal_checkpoint(TRUNCATE);",nullptr,nullptr,nullptr)!=SQLITE_OK)throw std::runtime_error("checkpoint failed");
    }
    auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
    std::uintmax_t disk=0;for(const auto& e:std::filesystem::directory_iterator(dir))if(e.is_regular_file())disk+=e.file_size();
    std::cout<<"{\"documents\":300,\"content_bytes\":"<<content<<",\"disk_bytes\":"<<disk<<",\"elapsed_ms\":"<<ms<<"}\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
