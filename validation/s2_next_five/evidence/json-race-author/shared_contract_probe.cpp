#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <atomic>
#include <barrier>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

int main(int argc,char** argv) {
  if(argc!=3)return 2;
  const auto registry=bzs::SourceRegistry::load(argv[1]);
  const auto* source=registry.find("ddg");
  if(!source)return 3;
  source->require_review(bzs::now_ms());
  constexpr int threads=4, rounds=100;
  const std::string mode=argv[2];
  std::barrier start(threads);
  std::atomic<int> errors=0;
  std::mutex output;
  std::vector<std::thread> workers;
  std::cout<<"mode="<<mode<<" threads="<<threads<<" rounds="<<rounds<<" shared_source=ddg manifest_written_during_probe=false\n"<<std::flush;
  for(int t=0;t<threads;++t)workers.emplace_back([&,t] {
    for(int i=0;i<rounds;++i) {
      start.arrive_and_wait();
      try {
        if(mode=="serialize") {
          auto raw=source->contract.dump();
          (void)bzs::Json::parse(raw);
        } else source->require_review(bzs::now_ms());
      } catch(const std::exception& error) {
        const int count=++errors;
        if(count<=12) {
          std::lock_guard lock(output);
          std::cerr<<"ERROR thread="<<t<<" round="<<i<<" message="<<error.what()<<'\n'<<std::flush;
        }
      }
    }
  });
  for(auto& worker:workers)worker.join();
  std::cout<<"calls="<<threads*rounds<<" errors="<<errors.load()<<'\n'<<std::flush;
  return errors.load()==0?0:1;
}
