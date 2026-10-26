#include "bzs/request_executor.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <future>
#include <iostream>
#include <stdexcept>
using namespace std::chrono_literals;
void check(bool ok) {if(!ok)throw std::runtime_error("executor regression");}
int main() {
  try {
    // Every expired task is rejected while the sole execution thread is held.
    std::promise<void> release,entered,expired;
    auto gate=release.get_future().share();
    std::atomic<int> ran=0,rejected=0;
    bzs::RequestExecutor pool(1,2,50ms);
    check(pool.submit([&] {entered.set_value();gate.wait();++ran;}));
    check(entered.get_future().wait_for(1s)==std::future_status::ready);
    for(int i=0;i<2;++i)check(pool.submit([&] {++ran;},[&] {if(++rejected==2)expired.set_value();}));
    check(!pool.submit([&] {++ran;}));
    const bool notified=expired.get_future().wait_for(1s)==std::future_status::ready;
    release.set_value();pool.stop();pool.join();
    check(notified&&ran==1&&rejected==2);
    check(!pool.submit([] {}));
    // Stop must not execute a pending task; an active task still owns its slot.
    std::promise<void> release2,entered2;
    auto gate2=release2.get_future().share();
    bzs::RequestExecutor stopped(1,1);
    check(stopped.submit([&] {entered2.set_value();gate2.wait();}));
    check(entered2.get_future().wait_for(1s)==std::future_status::ready);
    check(stopped.submit([&] {++ran;}));stopped.stop();release2.set_value();stopped.join();
    check(ran==1);
    std::cout<<"bounded capacity, prompt expiry, no late effects, joined stop: PASS\n";
  }catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
