#include "bzs/json.hpp"
#include "bzs/error.hpp"
#include <atomic>
#include <barrier>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// A scalar global exercises a creation path that need not read shared nodes.
// Its destruction occurs during program teardown, after the parallel tests.
static const bzs::Json shutdown_probe=bzs::Json::parse("7");

int main(int argc,char** argv) {
  using bzs::Json;
  const std::string mode=argc>1?argv[1]:"serialize";
  const int iterations=argc>2?std::stoi(argv[2]):400;
  constexpr int workers=4;
  Json root=Json::parse(R"({"review":{"approved_by":"reviewer","cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved":true,"revision":17,"ratio":1.25,"optional":null,"labels":["alpha","beta","gamma"]},"id":"shared-json-reader","payload":""})");
  root.set("payload",std::string(2048,'x'));
  const auto expected_plain=root.dump(false),expected_pretty=root.dump(true);
  const auto expected_review=root.at("review").canonical();
  std::atomic<int> failures{0},completed{0};
  std::mutex diagnostic_mutex;
  std::string first_error;
  const auto fail=[&](const std::string& message) {
    failures.fetch_add(1,std::memory_order_relaxed);
    std::lock_guard lock(diagnostic_mutex);
    if(first_error.empty())first_error=message;
  };
  std::barrier ready(workers);
  std::vector<std::thread> threads;
  for(int worker=0;worker<workers;++worker)threads.emplace_back([&,worker] {
    ready.arrive_and_wait();
    for(int i=0;i<iterations;++i) {
      try {
        if(mode=="serialize") {
          const bool pretty=(i+worker)%2;
          auto encoded=root.dump(pretty);
          // Parsing the independently owned return string also checks the
          // same malformed-input symptom as the production contract clone.
          auto decoded=Json::parse(encoded);
          if(encoded!=(pretty?expected_pretty:expected_plain)||decoded.at("id").str()!="shared-json-reader")
            fail("Concurrent serialization changed bytes");
        } else if(mode=="reference_aliases") {
          Json alias=root;
          auto shared_child=alias.at("review");
          auto local_array=Json::array();local_array.push(shared_child);
          auto local_object=Json::object();local_object.set("review",shared_child);
          // Mutate only a thread-private container. Replacing its field
          // releases its reference to a shared child; the child is immutable.
          local_object.set("review",Json::integer(i));
          if(local_array.at(std::size_t{0}).at("cache_policy").str()!="allowed_with_limits"||
             !shared_child.at("approved").flag()||shared_child.at("revision").num()!=17)
            fail("Shared child/reference lifetime changed");
        } else if(mode=="canonical_reads") {
          auto child=root.at("review");
          if(child.canonical()!=expected_review||!child.has("optional")||
             !child.at("optional").is_null()||child.keys().size()!=8||
             child.at("labels").at(std::size_t{1}).str()!="beta")
            fail("Canonical or field read changed");
          try { (void)root.at("absent");fail("Missing-field read unexpectedly succeeded"); }
          catch(const bzs::Error& error) { if(error.code!="E_SCHEMA")throw; }
        } else {
          fail("Unknown test mode");break;
        }
        completed.fetch_add(1,std::memory_order_relaxed);
      } catch(const bzs::Error& error) {
        fail(error.code+": "+error.what());
      } catch(const std::exception& error) {
        fail(error.what());
      }
    }
  });
  for(auto& thread:threads)thread.join();
  auto result=Json::object();
  result.set("scenario",mode);result.set("threads",std::int64_t{workers});
  result.set("iterations_per_thread",static_cast<std::int64_t>(iterations));
  result.set("completed",static_cast<std::int64_t>(completed.load()));
  result.set("failures",static_cast<std::int64_t>(failures.load()));
  result.set("first_error",first_error);
  result.set("shutdown_probe",shutdown_probe.num());
  std::cout<<result.dump()<<std::endl;
  return failures.load()==0&&completed.load()==workers*iterations?0:1;
}
