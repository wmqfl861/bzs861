#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <array>
#include <atomic>
#include <barrier>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// Independent baseline observation. Only the public pre-change Store and
// WebService APIs are used. The Worker is a synchronized, offline test double.
// Its gate proves both physical fetches entered before either response returned.
namespace {
using namespace bzs;
constexpr std::int64_t observed_now = 1800000000000;
const std::string target = "https://independent.example/direct-shared-baseline";
const std::string body = "Independent direct read baseline body.";

struct GateWorker final : WorkerPort {
  std::mutex mutex;
  std::condition_variable changed;
  int fetches = 0, profiles = 0, active = 0, peak_active = 0;
  bool released = false, worker_watchdog_fired = false;
  std::vector<std::string> parameters;

  Json call(const std::string& method, const Json& params, int timeout_ms) override {
    if (method == "fetch.profile") {
      std::lock_guard lock(mutex);
      ++profiles;
      auto response = Json::object();
      response.set("version", "bzs-http-freshness-1");
      response.set("request_profile", sha256("independent-direct-profile"));
      changed.notify_all();
      return response;
    }
    require(method == "fetch.extract", "E_TEST_FIXTURE", "Unexpected Worker method");
    {
      std::unique_lock lock(mutex);
      ++fetches;
      ++active;
      peak_active = std::max(peak_active, active);
      auto captured = Json::object();
      captured.set("params", Json::parse(params.dump()));
      captured.set("rpc_timeout_ms", static_cast<std::int64_t>(timeout_ms));
      parameters.push_back(captured.dump());
      changed.notify_all();
      if (!changed.wait_for(lock, std::chrono::seconds(10), [&] { return released; })) {
        worker_watchdog_fired = true;
        released = true;
        changed.notify_all();
      }
      --active;
    }
    auto response = Json::parse(R"({"status":"ok","http_status":200,"title":"Independent baseline","content_type":"text/plain","extractor":"independent/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{}})");
    response.set("final_url", params.at("url"));
    response.set("html", body);
    response.set("text", body);
    auto freshness = Json::parse(R"({"version":"bzs-http-freshness-1","cache_control":"max-age=60","vary":"","date":"","age":"","expires":"","pragma":"","response_delay_ms":0,"invalid":false})");
    freshness.set("url", params.at("url"));
    freshness.set("request_profile", sha256("independent-direct-profile"));
    freshness.set("received_at_ms", observed_now);
    response.set("http_freshness", freshness);
    auto usage = Json::object();
    usage.set("external_requests", std::int64_t{1});
    usage.set("content_bytes", static_cast<std::int64_t>(body.size()));
    response.set("usage", usage);
    return response;
  }
};

Json arguments() {
  auto args = Json::object();
  args.set("url", target);
  args.set("freshness", "refresh");
  args.set("timeout_ms", std::int64_t{20000});
  return args;
}
}

int main(int argc, char** argv) {
  using namespace bzs;
  try {
    require(argc == 2, "E_TEST_USAGE", "Pass a new, nonexistent database directory");
    const std::filesystem::path root(argv[1]);
    require(!std::filesystem::exists(root), "E_TEST_USAGE", "Refusing to reuse an existing fixture directory");
    std::filesystem::create_directories(root);
    Store store(root / "state.sqlite", [] { return observed_now; });
    GateWorker worker;
    WebService web(store, worker, {});
    std::array<Json, 2> replies;
    std::array<std::string, 2> errors;
    std::atomic<int> completed{0};
    std::barrier launch(3);
    auto caller = [&](std::size_t index) {
      launch.arrive_and_wait();
      try { replies[index] = web.read("alice", arguments()); }
      catch (const Error& error) { errors[index] = error.code + ": " + error.what(); }
      catch (const std::exception& error) { errors[index] = error.what(); }
      ++completed;
      worker.changed.notify_all();
    };
    std::thread first(caller, std::size_t{0});
    std::thread second(caller, std::size_t{1});
    launch.arrive_and_wait();
    bool both_entered = false;
    {
      std::unique_lock lock(worker.mutex);
      both_entered = worker.changed.wait_for(lock, std::chrono::seconds(5), [&] {
        return worker.fetches >= 2 || completed.load() == 2;
      }) && worker.fetches == 2;
      worker.released = true;
      worker.changed.notify_all();
    }
    first.join();
    second.join();

    auto report = Json::object();
    report.set("probe", "independent-direct-baseline-v1");
    report.set("expected_baseline_commit", "346048b4ae329e750d15f1e35952aab4513e3912");
    report.set("classification", "baseline_missing_capability_observation_not_product_regression");
    report.set("same_owner", "alice");
    report.set("url", target);
    report.set("freshness", "refresh");
    report.set("both_fetches_entered_before_release", both_entered);
    report.set("physical_fetches", static_cast<std::int64_t>(worker.fetches));
    report.set("peak_simultaneous_fetches", static_cast<std::int64_t>(worker.peak_active));
    report.set("profile_calls", static_cast<std::int64_t>(worker.profiles));
    report.set("worker_watchdog_fired", worker.worker_watchdog_fired);
    report.set("worker_reported_external_requests_per_fetch", std::int64_t{1});
    report.set("worker_reported_content_bytes_per_fetch", static_cast<std::int64_t>(body.size()));
    auto calls = Json::array();
    for (const auto& item : worker.parameters) calls.push(Json::parse(item));
    report.set("worker_calls", calls);
    auto results = Json::array();
    bool both_succeeded = true, both_missing_usage = true, both_correct_body = true;
    for (std::size_t i = 0; i < replies.size(); ++i) {
      auto item = Json::object();
      item.set("caller_index", static_cast<std::int64_t>(i));
      item.set("error", errors[i]);
      item.set("response", replies[i]);
      const bool success = errors[i].empty() && replies[i].is_object();
      const bool usage_absent = success && !replies[i].has("usage");
      const bool content_matches = success && replies[i].at("content").at("text").str() == body;
      item.set("usage_absent_despite_worker_receipt", usage_absent);
      item.set("body_matches", content_matches);
      both_succeeded = both_succeeded && success;
      both_missing_usage = both_missing_usage && usage_absent;
      both_correct_body = both_correct_body && content_matches;
      results.push(item);
    }
    report.set("callers", results);
    report.set("store_stats", store.stats("alice"));
    const bool expected = both_entered && worker.fetches == 2 && worker.peak_active == 2 &&
                          !worker.worker_watchdog_fired && both_succeeded &&
                          both_missing_usage && both_correct_body;
    report.set("observation_matches_expected_baseline", expected);
    report.set("known_limits", "Offline synchronized Worker and old public C++ APIs only; no HTTP/MCP claim, no new product acceptance, no crash or network-exactly-once claim.");
    std::cout << report.dump(true) << '\n';
    return expected ? 0 : 2;
  } catch (const std::exception& error) {
    std::cerr << "independent baseline probe setup failed: " << error.what() << '\n';
    return 3;
  }
}
