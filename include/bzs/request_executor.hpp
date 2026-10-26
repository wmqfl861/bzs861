#pragma once
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace bzs {
// Bounded admission. Only a task removed by a worker may execute Service.
// Expiry removes a queued task under the same lock; rejection is therefore
// side-effect-free and safe for the caller to retry. Running work never expires
// through this queue. stop() drops pending tasks and join() waits for all owners.
class RequestExecutor {
  using Clock=std::chrono::steady_clock;
  struct Pending {
    std::function<void()> run, expire;
    Clock::time_point deadline;
  };
  std::mutex mutex_;
  std::condition_variable wake_, deadlines_;
  std::deque<Pending> pending_;
  std::vector<std::jthread> workers_;
  std::jthread reaper_;
  const unsigned capacity_, queue_;
  const std::chrono::milliseconds wait_;
  unsigned admitted_=0;
  bool stopped_=false;
  static void invoke(std::function<void()>& task) noexcept {
    try { if(task)task(); } catch(...) { /* Capacity and shutdown still unwind. */ }
  }
  void run() {
    for(;;) {
      Pending task;
      bool expired=false;
      {
        std::unique_lock lock(mutex_);
        wake_.wait(lock,[&] {return stopped_||!pending_.empty();});
        if(stopped_)return;
        task=std::move(pending_.front());pending_.pop_front();
        expired=Clock::now()>=task.deadline;
        deadlines_.notify_one();
      }
      invoke(expired?task.expire:task.run);
      {std::lock_guard lock(mutex_);--admitted_;}
    }
  }
  void reap() {
    std::unique_lock lock(mutex_);
    while(!stopped_) {
      if(pending_.empty()) {deadlines_.wait(lock);continue;}
      const auto deadline=pending_.front().deadline;
      if(Clock::now()<deadline) {deadlines_.wait_until(lock,deadline);continue;}
      auto callback=std::move(pending_.front().expire);
      pending_.pop_front();--admitted_;
      lock.unlock();invoke(callback);lock.lock();
    }
  }
 public:
  explicit RequestExecutor(unsigned threads,unsigned queue=0,
                           std::chrono::milliseconds wait=std::chrono::seconds(30))
      :capacity_(threads+queue),queue_(queue),wait_(wait) {
    try {
      for(unsigned i=0;i<threads;++i)workers_.emplace_back([this] {run();});
      if(queue_)reaper_=std::jthread([this] {reap();});
    }catch(...) {stop();join();throw;}
  }
  ~RequestExecutor() {stop();join();}
  bool submit(std::function<void()> task,std::function<void()> expire={}) {
    std::lock_guard lock(mutex_);
    if(stopped_||admitted_>=capacity_)return false;
    pending_.push_back({std::move(task),std::move(expire),
                       queue_?Clock::now()+wait_:Clock::time_point::max()});
    ++admitted_;wake_.notify_one();deadlines_.notify_one();return true;
  }
  void stop() {
    std::lock_guard lock(mutex_);
    stopped_=true;admitted_-=static_cast<unsigned>(pending_.size());pending_.clear();
    wake_.notify_all();deadlines_.notify_all();
  }
  void join() {
    if(reaper_.joinable())reaper_.join();
    for(auto& worker:workers_)if(worker.joinable())worker.join();
  }
};
}
