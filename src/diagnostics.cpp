#include "bzs/diagnostics.hpp"
#include "bzs/json.hpp"
#include <atomic>
#include <iostream>
#include <string>
#include <typeinfo>
namespace bzs {
  namespace {
    std::atomic<std::int64_t> internal_errors {0};
  }
  std::int64_t internal_error_count() noexcept {
    return internal_errors.load(std::memory_order_relaxed);
  }
  void log_internal(const char* where,const std::exception* exception) noexcept {
    internal_errors.fetch_add(1,std::memory_order_relaxed);
    try {
      auto log=Json::object();
      log.set("event","internal_error");
      log.set("where",where!=nullptr?where:"");
      log.set("type",exception!=nullptr?std::string(typeid(*exception).name()):std::string("unknown"));
      if(exception) {
        auto message=std::string(exception->what());
        if(message.size()>200)message.resize(200);
        log.set("message",message);
      }
      std::clog<<log.dump()<<'\n';
    }catch(...) {
      // Diagnostics must never take the process down; the counter above
      // already recorded the error.
    }
  }
  void log_current_internal(const char* where) noexcept {
    try {
      throw;
    } catch(const std::exception& exception) {
      log_internal(where,&exception);
    } catch(...) {
      log_internal(where,nullptr);
    }
  }
}
