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
        if(message.size()>200) {
          message.resize(200);
          // CL-14: a hard byte cut can split a multi-byte UTF-8 character,
          // making the whole JSON line undecodable; trim a trailing partial
          // sequence so the message ends on a character boundary (the total
          // length may land slightly under 200).
          const auto size=message.size();
          auto after_lead=size;
          while(after_lead>0&&size-after_lead<3&&(static_cast<unsigned char>(message[after_lead-1])&0xC0)==0x80)--after_lead;
          if(after_lead>0) {
            const auto lead=static_cast<unsigned char>(message[after_lead-1]);
            std::size_t width=1;
            if(lead>=0xC2&&lead<=0xDF)width=2;
            else if(lead>=0xE0&&lead<=0xEF)width=3;
            else if(lead>=0xF0&&lead<=0xF4)width=4;
            if(width>1&&size-after_lead+1<width)message.resize(after_lead-1);
          }
        }
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
