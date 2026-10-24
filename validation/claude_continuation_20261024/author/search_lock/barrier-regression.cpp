// Exact formal helper and case extracted from tests/unit.cpp.
#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <sqlite3.h>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>
namespace bzs {
  struct StoreSearchTestAccess {
    static void set(Store& store,std::function<void()> callback) {
      std::lock_guard lock(store.mutex_);store.search_unlocked_for_test_=std::move(callback);
    }
  };
}
using namespace bzs;
namespace {
std::size_t assertions=0;
#define TEST(n) void n()
#define CHECK(c) do{++assertions;if(!(c))throw std::runtime_error(std::string("check failed: ")+#c+" line "+std::to_string(__LINE__));}while(false)
  SourceDocument text(const std::string&url, const std::string&body, const std::string&title="Test") {
    return {
      url, body, "text/plain", title
    };
  }

  Query q(const std::string&s) {
    return parse_query(Json::string(s));
  }

  Json search_while(Store& store,bool freeze,const std::function<void()>& action,const std::string& query="NOT absent") {
    std::mutex mutex;std::condition_variable changed;
    bool entered=false,released=false,finished=false,action_finished=false,transaction_free=false;
    Json response;std::exception_ptr search_error,action_error;
    StoreSearchTestAccess::set(store,[&] {
      std::unique_lock lock(mutex);if(entered)return;
      transaction_free=sqlite3_get_autocommit(store.raw_handle())!=0;entered=true;changed.notify_all();
      changed.wait(lock,[&]{return released;});
    });
    std::thread searcher([&] {
      try {response=freeze?store.search("alice",q(query),2000,100):store.search_once("alice",q(query),2000,100);}
      catch(...) {search_error=std::current_exception();}
      {std::lock_guard lock(mutex);finished=true;}changed.notify_all();
    });
    bool reached=false,progressed=false;
    {
      std::unique_lock lock(mutex);
      reached=changed.wait_for(lock,std::chrono::seconds(5),[&]{return entered||finished;})&&entered;
    }
    std::thread actor;
    if(reached) {
      actor=std::thread([&] {
        try {action();}catch(...) {action_error=std::current_exception();}
        {std::lock_guard lock(mutex);action_finished=true;}changed.notify_all();
      });
      std::unique_lock lock(mutex);
      progressed=changed.wait_for(lock,std::chrono::seconds(5),[&]{return action_finished;});
    }
    {std::lock_guard lock(mutex);released=true;}changed.notify_all();
    searcher.join();if(actor.joinable())actor.join();StoreSearchTestAccess::set(store,{});
    CHECK(reached);CHECK(progressed);CHECK(transaction_free);
    if(action_error)std::rethrow_exception(action_error);
    if(search_error)std::rethrow_exception(search_error);
    return response;
  }

  Json search_hits(Store& store,bool freeze,const Json& result) {
    return freeze?store.page("alice",result.at("session_id").str(),"",100).at("hits"):result.at("hits");
  }

  TEST(search_matching_releases_store_lock_and_sqlite_transaction_for_every_path) {
    for(bool freeze:{false,true})for(const auto* query:{"marker","\"arker\"","NOT absent"}) {
      Store store(":memory:");store.ingest("alice",text("https://x.example/a","marker local content"));
      store.ingest("bob",text("https://x.example/b","marker other owner"));
      const auto result=search_while(store,freeze,[&] {
        CHECK(store.stats("bob").at("documents").num()==1);
        store.ingest("bob",text("https://x.example/new","marker concurrent import"));
        CHECK(store.stats("bob").at("documents").num()==2);
      },query);
      CHECK(search_hits(store,freeze,result).size()==1);CHECK(!result.at("partial").flag());
      CHECK(result.at("scanned_candidates").num()==1);
    }
  }
}
int main() {
  try { search_matching_releases_store_lock_and_sqlite_transaction_for_every_path();std::cout<<"PASS "<<assertions<<" assertions\n";return 0; }
  catch(const std::exception& error) {std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}
}
