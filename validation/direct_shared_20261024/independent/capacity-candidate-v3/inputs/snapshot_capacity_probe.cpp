#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <array>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

// One source for the released and candidate libraries. Calls only the original
// public Store/WebService/WorkerPort APIs. Both versions must deliver the same
// legal bounded body; a candidate error remains a failed product expectation.
namespace {
using namespace bzs;
using namespace std::chrono_literals;
constexpr std::int64_t now = 1800000000000;
const std::string url = "https://independent.example/snapshot-capacity";
const std::string body(700U * 1024U, '\x01');
const std::string profile = sha256("independent-capacity-profile");
std::int64_t checks = 0;
#define CHECK(value) do { ++checks; if (!(value)) throw std::runtime_error(std::string("check failed: ") + #value + " at " + std::to_string(__LINE__)); } while (false)

struct Worker final : WorkerPort {
  bool proven;
  std::mutex mutex;
  std::condition_variable changed;
  std::size_t fetches = 0, profiles = 0;
  bool gated = false, released = true, watchdog = false;
  explicit Worker(bool value) : proven(value) {}
  Json call(const std::string& method, const Json& params, int) override {
    if (method == "fetch.profile") {
      std::lock_guard lock(mutex); ++profiles; changed.notify_all();
      if (!proven) throw Error("E_WORKER", "Legacy fixture has no profile proof", 502);
      auto out = Json::object(); out.set("version", "bzs-http-freshness-1");
      out.set("request_profile", profile); return out;
    }
    require(method == "fetch.extract", "E_TEST_FIXTURE", "Unexpected worker method");
    {
      std::unique_lock lock(mutex); ++fetches; changed.notify_all();
      if (gated && !changed.wait_for(lock, 15s, [&] { return released; })) {
        watchdog = true; released = true; changed.notify_all();
      }
    }
    auto out = Json::parse(R"({"status":"ok","http_status":200,"title":"capacity","content_type":"text/plain","extractor":"plain-text","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{}})");
    out.set("final_url", params.at("url")); out.set("html", ""); out.set("text", body);
    auto usage = Json::object(); usage.set("external_requests", std::int64_t{1});
    usage.set("content_bytes", static_cast<std::int64_t>(body.size())); out.set("usage", usage);
    if (proven) {
      auto record = Json::parse(R"({"version":"bzs-http-freshness-1","cache_control":"max-age=60","vary":"","date":"","age":"","expires":"","pragma":"","response_delay_ms":0,"invalid":false})");
      record.set("url", params.at("url")); record.set("request_profile", profile);
      record.set("received_at_ms", now); out.set("http_freshness", record);
    }
    return out;
  }
};

Json arguments() {
  auto out = Json::object(); out.set("url", url); out.set("freshness", "refresh");
  out.set("timeout_ms", std::int64_t{20000}); out.set("max_bytes", std::int64_t{64}); return out;
}

struct Result {
  Json value;
  std::string code, message;
  void invoke(WebService& web) {
    try { value = web.read("alice", arguments()); }
    catch (const Error& error) { code = error.code; message = error.what(); }
    catch (const std::exception& error) { code = "STD_EXCEPTION"; message = error.what(); }
  }
  Json report() const {
    auto out = Json::object(); out.set("code", code); out.set("message", message);
    if (value.is_object()) {
      const auto content = value.at("content");
      out.set("window_bytes", static_cast<std::int64_t>(content.at("text").str().size()));
      out.set("window_sha256", sha256(content.at("text").str()));
      out.set("total_bytes", content.at("total_bytes")); out.set("snapshot_id", value.at("snapshot_id"));
      if (value.has("shared_read")) out.set("shared_read", value.at("shared_read"));
    }
    return out;
  }
};

Json scenario(const std::filesystem::path& folder, bool proven, bool candidate) {
  Store store(folder / "state.sqlite", [] { return now; });
  Worker worker(proven); WebService web(store, worker, {});
  std::array<Result, 2> results;
  bool gate_ready = true;
  if (!proven) results[0].invoke(web);
  else {
    { std::lock_guard lock(worker.mutex); worker.gated = true; worker.released = false; }
    std::thread first([&] { results[0].invoke(web); });
    {
      std::unique_lock lock(worker.mutex);
      gate_ready = worker.changed.wait_for(lock, 5s, [&] { return worker.fetches >= 1; });
    }
    std::thread second([&] { results[1].invoke(web); });
    {
      std::unique_lock lock(worker.mutex);
      gate_ready = worker.changed.wait_for(lock, 5s, [&] { return worker.fetches >= 2 || worker.profiles >= 2; }) && gate_ready;
      // Public API has no admission observer. A bounded scheduling grace is
      // followed by an explicit one-fetch/two-caller assertion on candidates.
      if (worker.fetches < 2) worker.changed.wait_for(lock, 300ms, [&] { return worker.fetches >= 2; });
      worker.released = true; worker.changed.notify_all();
    }
    first.join(); second.join();
  }
  auto report = Json::object(); report.set("case", proven ? "profile_concurrent_capacity" : "legacy_single_capacity");
  report.set("source_body_bytes", static_cast<std::int64_t>(body.size()));
  report.set("physical_fetches", static_cast<std::int64_t>(worker.fetches));
  report.set("profile_calls", static_cast<std::int64_t>(worker.profiles));
  report.set("gate_ready", gate_ready); report.set("worker_watchdog", worker.watchdog);
  auto observations = Json::array();
  for (std::size_t i = 0; i < (proven ? 2U : 1U); ++i) observations.push(results[i].report());
  report.set("callers", observations);
  try {
    const auto retained = store.cached("alice", url);
    CHECK(retained.has_value());
    report.set("retained_snapshot_json_bytes", static_cast<std::int64_t>(retained->dump().size()));
    report.set("retained_text_sha256", sha256(retained->at("canonical_text").str()));
    CHECK(retained->at("canonical_text").str() == body);
    CHECK(retained->dump().size() > 8U * 1024U * 1024U);
    CHECK(gate_ready && !worker.watchdog);
    CHECK(worker.fetches == (proven && !candidate ? 2U : 1U));
    for (std::size_t i = 0; i < (proven ? 2U : 1U); ++i) {
      CHECK(results[i].code.empty());
      CHECK(results[i].value.at("content").at("text").str() == body.substr(0, 64));
      CHECK(results[i].value.at("content").at("total_bytes").num() == static_cast<std::int64_t>(body.size()));
      CHECK(!results[i].value.at("from_cache").flag());
      if (candidate) {
        CHECK(results[i].value.at("shared_read").at("members").num() == (proven ? 2 : 1));
        CHECK(results[i].value.at("shared_read").at("physical_usage").at("known").flag());
      }
    }
    if (proven && candidate) CHECK(results[0].value.at("shared_read").at("attempt_id").str() == results[1].value.at("shared_read").at("attempt_id").str());
    report.set("passed", true);
  } catch (const std::exception& error) { report.set("passed", false); report.set("assertion_failure", error.what()); }
  return report;
}
}

int main(int argc, char** argv) {
  using namespace bzs;
  try {
    require(argc == 3, "E_TEST_USAGE", "Pass fresh directory and baseline|candidate");
    const std::filesystem::path folder(argv[1]); const std::string mode(argv[2]);
    require(!std::filesystem::exists(folder) && (mode == "baseline" || mode == "candidate"), "E_TEST_USAGE", "Invalid fixture arguments");
    std::filesystem::create_directories(folder / "legacy"); std::filesystem::create_directories(folder / "profile");
    auto report = Json::object(); auto results = Json::array();
    results.push(scenario(folder / "legacy", false, mode == "candidate"));
    results.push(scenario(folder / "profile", true, mode == "candidate"));
    std::int64_t failures = 0;
    for (std::size_t i = 0; i < results.size(); ++i) if (!results.at(i).at("passed").flag()) ++failures;
    report.set("mode", mode); report.set("cases", std::int64_t{2}); report.set("checks", checks);
    report.set("failures", failures); report.set("results", results);
    report.set("scope", "Same old public C++ interfaces; finite offline capacity probe, not a change to external input limits. Candidate expectations still require successful original body delivery.");
    std::cout << report.dump(true) << '\n'; return failures ? 1 : 0;
  } catch (const std::exception& error) { std::cerr << "capacity fixture failed: " << error.what() << '\n'; return 3; }
}
