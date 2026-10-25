#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

// Independent reviewer probe. The production test hook only pauses delivery;
// the test-access member reader observes admission under its real mutex.
// All database mutations use the public Store API through a second connection.
namespace bzs {
struct WebReadTestAccess {
  static std::size_t members(WebService& web) {
    std::lock_guard lock(web.direct_reads_mutex_);
    std::size_t count = 0;
    for (const auto& [key, flight] : web.direct_reads_) {
      (void)key;
      count += flight->members;
    }
    return count;
  }
  static std::size_t groups(WebService& web) {
    std::lock_guard lock(web.direct_reads_mutex_);
    return web.direct_reads_.size();
  }
  static void delivery(WebService& web, std::function<void(const std::string&)> callback) {
    std::lock_guard lock(web.direct_reads_mutex_);
    web.direct_read_window_for_test_ = std::move(callback);
  }
};
}

namespace {
using namespace bzs;
using namespace std::chrono_literals;
std::int64_t checks = 0;
Json diagnostic;
#define CHECK(value) do { ++checks; if (!(value)) throw std::runtime_error(std::string("check failed: ") + #value + " at " + std::to_string(__LINE__)); } while (false)
const std::string target = "https://independent.example/%70age";
const std::string body = "Independent shared response αβ🙂 original bytes.";
const std::string title = "Independent shared artifact";
const std::string profile = sha256("independent-direct-profile-v1");
const std::string other_profile = sha256("independent-direct-profile-v2");

Json args(const std::string& url = target, std::int64_t start = 0) {
  auto out = Json::object();
  out.set("url", url); out.set("freshness", "refresh");
  out.set("timeout_ms", std::int64_t{20000}); out.set("start_byte", start);
  return out;
}

Json policy(bool deny = false) {
  auto out = Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":[],"diagnostics":[]})");
  out.set("index_allowed", !deny); out.set("snippet_allowed", !deny);
  if (deny) { auto values = Json::array(); values.push(Json::string("noindex")); out.set("directives", values); }
  else { out.set("snippet_text", body); out.set("snippet_title", title); }
  return out;
}

Json cache_record(const std::string& url, const std::string& selected_profile = profile) {
  auto out = Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"independent-stable-etag\"","last_modified":"Tue, 14 Nov 2023 22:13:20 GMT","cache_control":"max-age=60","vary":"","date":"","age":"","expires":""})");
  out.set("url", url); out.set("request_profile", selected_profile); return out;
}

Json freshness_record(const std::string& url, std::int64_t now,
                      const std::string& control = "max-age=60", const std::string& selected_profile = profile) {
  auto out = Json::parse(R"({"version":"bzs-http-freshness-1","cache_control":"","vary":"","date":"","age":"","expires":"","pragma":"","response_delay_ms":0,"invalid":false})");
  out.set("url", url); out.set("request_profile", selected_profile);
  out.set("cache_control", control); out.set("received_at_ms", now); return out;
}

struct Worker final : WorkerPort {
  std::atomic<std::int64_t>& clock;
  std::mutex mutex; std::condition_variable changed;
  std::size_t fetches = 0, profiles = 0;
  bool gated = false, released = true, watchdog = false;
  std::function<Json(const Json&)> respond;
  std::vector<std::string> requests;
  explicit Worker(std::atomic<std::int64_t>& time) : clock(time) {
    respond = [this](const Json& params) { return page(params); };
  }
  Json page(const Json& params, bool validator = false, const std::string& final_url = "",
            const Json& redirects = Json::array()) const {
    const auto final = final_url.empty() ? params.at("url").str() : final_url;
    auto out = Json::parse(R"({"status":"ok","http_status":200,"content_type":"text/plain","extractor":"independent/1","extract_status":"ok","index_allowed":true,"links":[],"warnings":[],"metadata":{"byline":"Independent fixture","nested":{"unchanged":[1,"original",true]}}})");
    out.set("final_url", final); out.set("redirects", redirects); out.set("html", body);
    out.set("text", body); out.set("title", title); out.set("publication", policy());
    out.set("http_freshness", freshness_record(final, clock.load()));
    auto usage = Json::object(); usage.set("external_requests", static_cast<std::int64_t>(redirects.size() + 1));
    usage.set("content_bytes", static_cast<std::int64_t>(body.size())); out.set("usage", usage);
    if (validator) out.set("http_cache", cache_record(final));
    return out;
  }
  Json validator_response(const Json& params) const {
    if (!params.has("conditional")) return page(params, true);
    auto out = Json::object(); out.set("status", "not_modified");
    out.set("http_status", std::int64_t{304}); out.set("final_url", params.at("url"));
    out.set("redirects", Json::array()); out.set("binding_id", params.at("conditional").at("binding_id"));
    out.set("http_cache", cache_record(params.at("url").str()));
    out.set("http_freshness", freshness_record(params.at("url").str(), clock.load()));
    out.set("usage", Json::parse(R"({"external_requests":1,"content_bytes":0})")); return out;
  }
  void arm() { std::lock_guard lock(mutex); gated = true; released = false; }
  void release() { std::lock_guard lock(mutex); released = true; changed.notify_all(); }
  std::size_t count() { std::lock_guard lock(mutex); return fetches; }
  bool await_count(std::size_t expected) {
    std::unique_lock lock(mutex);
    return changed.wait_for(lock, 5s, [&] { return fetches >= expected; });
  }
  Json call(const std::string& method, const Json& params, int) override {
    if (method == "fetch.profile") {
      std::lock_guard lock(mutex); ++profiles; changed.notify_all();
      auto out = Json::object(); out.set("version", "bzs-http-freshness-1"); out.set("request_profile", profile); return out;
    }
    require(method == "fetch.extract", "E_TEST_FIXTURE", "Unexpected Worker method");
    {
      std::unique_lock lock(mutex); ++fetches; requests.push_back(params.dump()); changed.notify_all();
      if (gated && !changed.wait_for(lock, 10s, [&] { return released; })) {
        watchdog = true; released = true; changed.notify_all();
      }
    }
    return respond(params);
  }
};

struct Observation {
  Json result, details;
  std::string code, message;
  Json json() const {
    auto out = Json::object(); out.set("result", result); out.set("code", code);
    out.set("message", message); out.set("details", details); return out;
  }
};

Observation invoke(WebService& web, const Json& arguments) {
  Observation out;
  try { out.result = web.read("alice", arguments); }
  catch (const Error& error) { out.code = error.code; out.message = error.what(); out.details = error.details; }
  catch (const std::exception& error) { out.code = "STD_EXCEPTION"; out.message = error.what(); }
  return out;
}

struct Call {
  std::mutex mutex; std::condition_variable changed; bool done = false;
  Observation outcome; std::thread thread;
  Call(WebService& web, Json arguments) : thread([&, arguments] {
    auto value = invoke(web, arguments);
    { std::lock_guard lock(mutex); outcome = std::move(value); done = true; }
    changed.notify_all();
  }) {}
  bool await_done() { std::unique_lock lock(mutex); return changed.wait_for(lock, 5s, [&] { return done; }); }
  void join() { if (thread.joinable()) thread.join(); }
  ~Call() { join(); }
};

struct DeliveryGate {
  std::mutex mutex; std::condition_variable changed;
  bool entered = false, released = false, watchdog = false;
  void callback(const std::string& role) {
    if (role != "subscriber") return;
    std::unique_lock lock(mutex); entered = true; changed.notify_all();
    if (!changed.wait_for(lock, 10s, [&] { return released; })) watchdog = true;
  }
  bool await_entered() { std::unique_lock lock(mutex); return changed.wait_for(lock, 5s, [&] { return entered; }); }
  void release() { std::lock_guard lock(mutex); released = true; changed.notify_all(); }
  ~DeliveryGate() { release(); }
};

struct Harness {
  std::atomic<std::int64_t> clock{1800000000000};
  Store store, other;
  Worker worker;
  WebService web;
  explicit Harness(const std::filesystem::path& root)
      : store(root / "state.sqlite", [&] { return clock.load(); }),
        other(root / "state.sqlite", [&] { return clock.load(); }), worker(clock), web(store, worker, {}) {}
};

bool await_members(WebService& web, std::size_t expected) {
  // The observation itself is protected by the production mutex. Time is only
  // a deadlock watchdog; success requires the exact admitted member count.
  const auto deadline = std::chrono::steady_clock::now() + 5s;
  do {
    if (WebReadTestAccess::members(web) == expected) return true;
    std::this_thread::sleep_for(1ms);
  } while (std::chrono::steady_clock::now() < deadline);
  return false;
}

std::array<Observation, 2> pair(Harness& h, const Json& first_args = args(), const Json& second_args = args(),
                                DeliveryGate* delivery = nullptr, const std::function<void(Call&, Call&)>& after = {}) {
  const auto before = h.worker.count();
  h.worker.arm();
  Call first(h.web, first_args);
  const bool entered = h.worker.await_count(before + 1);
  Call second(h.web, second_args);
  const bool joined = await_members(h.web, 2);
  h.worker.release();
  std::exception_ptr action_error;
  try { if (entered && joined && after) after(first, second); }
  catch (...) { action_error = std::current_exception(); }
  if (delivery) delivery->release();
  first.join(); second.join();
  if (action_error) std::rethrow_exception(action_error);
  CHECK(entered); CHECK(joined); CHECK(!h.worker.watchdog);
  CHECK(h.worker.count() >= before + 1);
  CHECK(WebReadTestAccess::groups(h.web) == 0);
  return {first.outcome, second.outcome};
}

Json receipt(const Observation& out) { return out.code.empty() ? out.result : out.details; }

void check_receipts(const std::array<Observation, 2>& results, std::int64_t physical_requests,
                    std::int64_t physical_bytes, bool known = true) {
  std::string attempt;
  for (std::size_t i = 0; i < results.size(); ++i) {
    const auto value = receipt(results[i]);
    CHECK(value.is_object()); CHECK(value.has("usage")); CHECK(value.has("shared_read"));
    const auto usage = value.at("usage"), shared = value.at("shared_read"), physical = shared.at("physical_usage");
    CHECK(shared.at("role").str() == (i == 0 ? "payer" : "subscriber"));
    CHECK(shared.at("members").num() == 2); CHECK(shared.at("scope").str() == "process");
    CHECK(shared.at("attempt_id").str().size() == 48);
    if (i == 0) attempt = shared.at("attempt_id").str();
    else CHECK(shared.at("attempt_id").str() == attempt);
    CHECK(physical.at("known").flag() == known);
    if (known) {
      CHECK(physical.at("external_requests").num() == physical_requests);
      CHECK(physical.at("content_bytes").num() == physical_bytes);
    } else {
      CHECK(physical.at("external_requests").is_null()); CHECK(physical.at("content_bytes").is_null());
    }
    CHECK(usage.at("known").flag() == (known || i == 1));
    if (known || i == 1) {
      CHECK(usage.at("external_requests").num() == (i == 0 ? physical_requests : 0));
      CHECK(usage.at("content_bytes").num() == (i == 0 ? physical_bytes : 0));
    } else { CHECK(usage.at("external_requests").is_null()); CHECK(usage.at("content_bytes").is_null()); }
    CHECK(usage.at("model_calls").num() == 0);
  }
}

Json outcomes(const std::array<Observation, 2>& values) {
  auto out = Json::array(); for (const auto& value : values) out.push(value.json()); return out;
}

Json profile_restriction_case(const std::filesystem::path& root, const std::string& kind) {
  Harness h(root); const auto old = h.web.read("alice", args());
  CHECK(old.at("indexed").flag());
  const auto id = old.at("snapshot_id").str();
  h.worker.respond = [&](const Json& params) {
    auto out = h.worker.page(params);
    out.set("http_freshness", freshness_record(target, h.clock.load(), kind == "body" ? "no-cache" : "no-store", other_profile));
    out.set("publication", policy(true)); out.set("index_allowed", false);
    if (kind != "body") out.set("publication_observation", policy(true));
    if (kind == "failed") { out.set("status", "network_error"); out.set("http_status", std::int64_t{503}); out.set("error", "independent known-response failure"); }
    return out;
  };
  auto result = pair(h);
  const auto expected = kind == "body" ? "E_READ_PROFILE_CHANGED" : kind == "no_store" ? "E_CACHE_POLICY" : "E_FETCH_NETWORK";
  CHECK(result[0].code == expected); CHECK(result[1].code == expected);
  CHECK(h.worker.count() == 2); check_receipts(result, 1, static_cast<std::int64_t>(body.size()));
  const auto retained = h.store.snapshot("alice", id);
  CHECK(retained.at("canonical_text").str() == body); CHECK(!retained.at("indexed").flag());
  const auto cached = h.store.cached("alice", target); CHECK(cached.has_value());
  CHECK(!cached->at("meta").has("http_freshness")); CHECK(!cached->at("meta").has("http_cache"));
  auto cache_only = args(); cache_only.set("freshness", "cache_only");
  CHECK(invoke(h.web, cache_only).code == "E_CACHE_STALE"); CHECK(h.worker.count() == 2);
  auto report = Json::object(); report.set("kind", kind); report.set("callers", outcomes(result));
  report.set("physical_fetches_including_seed", std::int64_t{2});
  report.set("old_body_retained_index_restricted_cache_invalidated", true); return report;
}

Json metadata_304_case(const std::filesystem::path& root, bool race) {
  Harness h(root); h.worker.respond = [&](const Json& params) { return h.worker.validator_response(params); };
  const auto old = h.web.read("alice", args()); const auto id = old.at("snapshot_id").str();
  const auto evidence = h.store.evidence("alice", id, 0, 11);
  h.clock += 1000;
  DeliveryGate delivery;
  if (race) WebReadTestAccess::delivery(h.web, [&](const std::string& role) { delivery.callback(role); });
  std::string before_hash, after_hash;
  const auto action = [&](Call& payer, Call&) {
    CHECK(delivery.await_entered()); CHECK(payer.await_done()); CHECK(payer.outcome.code.empty());
    const auto before = h.store.snapshot("alice", id); before_hash = sha256(before.at("meta").canonical());
    h.clock += 1000;
    const auto later = h.web.read("alice", args()); CHECK(later.at("snapshot_id").str() == id);
    CHECK(later.at("fetch").at("http_status").num() == 304);
    const auto updated = h.store.snapshot("alice", id); after_hash = sha256(updated.at("meta").canonical());
    CHECK(before_hash != after_hash);
    CHECK(before.at("meta").at("http_cache").canonical() == updated.at("meta").at("http_cache").canonical());
  };
  const auto result = pair(h, args(), args(), race ? &delivery : nullptr, race ? action : std::function<void(Call&, Call&)>{});
  CHECK(result[0].code.empty()); CHECK(result[0].result.at("snapshot_id").str() == id);
  CHECK(result[0].result.at("fetch").at("http_status").num() == 304);
  if (race) { CHECK(result[1].code == "E_REVALIDATION_STALE"); CHECK(!delivery.watchdog); CHECK(h.worker.count() == 3); }
  else { CHECK(result[1].code.empty()); CHECK(result[1].result.at("snapshot_id").str() == id); CHECK(h.worker.count() == 2); }
  check_receipts(result, 1, 0);
  CHECK(h.store.snapshot("alice", id).at("canonical_text").str() == body);
  CHECK(h.store.evidence_get("alice", evidence.at("evidence_id").str()).at("quote").str() == evidence.at("quote").str());
  auto report = Json::object(); report.set("concurrent_metadata_refresh", race); report.set("callers", outcomes(result));
  report.set("original_evidence_retained", true); report.set("before_metadata_sha256", before_hash);
  report.set("after_metadata_sha256", after_hash); return report;
}

Json alias_delivery_case(const std::filesystem::path& root, const std::string& which) {
  Harness h(root);
  const auto final = which == "original" ? target : "https://independent.example/%66inal";
  const auto alias = which == "original" ? "https://independent.example/page" :
                     which == "final" ? "https://independent.example/final" : "https://independent.example/hop";
  auto redirects = Json::array();
  if (which == "intermediate") redirects.push(Json::string("https://independent.example/%68op"));
  if (which != "original") redirects.push(Json::string(final));
  const auto alias_doc = h.other.ingest("alice", {alias, "alias old evidence", "text/plain", "Alias"});
  h.worker.respond = [&](const Json& params) { return h.worker.page(params, false, final, redirects); };
  DeliveryGate delivery;
  WebReadTestAccess::delivery(h.web, [&](const std::string& role) { delivery.callback(role); });
  const auto result = pair(h, args(), args(), &delivery, [&](Call& payer, Call&) {
    CHECK(delivery.await_entered()); CHECK(payer.await_done()); CHECK(payer.outcome.code.empty());
    h.other.erase("alice", alias_doc.at("document_id").str());
    h.other.ingest("alice", {alias, "alias revived evidence", "text/plain", "Alias revived"});
  });
  CHECK(result[0].code.empty()); CHECK(result[1].code == "E_TARGET_DELETED");
  CHECK(!delivery.watchdog); CHECK(h.worker.count() == 1);
  check_receipts(result, static_cast<std::int64_t>(redirects.size() + 1), static_cast<std::int64_t>(body.size()));
  CHECK(h.store.snapshot("alice", result[0].result.at("snapshot_id").str()).at("canonical_text").str() == body);
  auto report = Json::object(); report.set("alias_position", which); report.set("callers", outcomes(result));
  report.set("scope", "Payer returned before deletion; held subscriber cannot publish after alias deletion and revival. Historical snapshot access remains distinct.");
  return report;
}

Json revoke_delivery_case(const std::filesystem::path& root) {
  Harness h(root); DeliveryGate delivery;
  WebReadTestAccess::delivery(h.web, [&](const std::string& role) { delivery.callback(role); });
  const auto result = pair(h, args(), args(), &delivery, [&](Call& payer, Call&) {
    CHECK(delivery.await_entered()); CHECK(payer.await_done()); CHECK(payer.outcome.code.empty());
    h.other.revoke_principal("admin", "alice");
  });
  CHECK(result[0].code.empty()); CHECK(result[1].code == "E_PRINCIPAL_REVOKED");
  CHECK(!delivery.watchdog); CHECK(h.worker.count() == 1);
  check_receipts(result, 1, static_cast<std::int64_t>(body.size()));
  auto report = Json::object(); report.set("callers", outcomes(result));
  report.set("scope", "Payer returned before revocation; held subscriber must recheck current principal before delivery."); return report;
}

Json window_error_case(const std::filesystem::path& root, bool payer_bad) {
  Harness h(root);
  const auto result = pair(h, args(target, payer_bad ? 1000000 : 0), args(target, payer_bad ? 0 : 1000000));
  CHECK(result[payer_bad ? 0 : 1].code == "E_EVIDENCE_RANGE");
  CHECK(result[payer_bad ? 1 : 0].code.empty()); CHECK(h.worker.count() == 1);
  check_receipts(result, 1, static_cast<std::int64_t>(body.size()));
  auto report = Json::object(); report.set("payer_bad_window", payer_bad); report.set("callers", outcomes(result)); return report;
}

Json unknown_failure_case(const std::filesystem::path& root) {
  Harness h(root);
  h.worker.respond = [](const Json&) -> Json { throw Error("E_WORKER_TIMEOUT", "Independent unknown execution", 504); };
  const auto result = pair(h); CHECK(result[0].code == "E_WORKER_TIMEOUT"); CHECK(result[1].code == "E_WORKER_TIMEOUT");
  CHECK(h.worker.count() == 1); check_receipts(result, 0, 0, false);
  CHECK(h.store.stats("alice").at("snapshots").num() == 0);
  auto report = Json::object(); report.set("callers", outcomes(result)); report.set("automatic_retries", std::int64_t{0}); return report;
}

Json malformed_usage_restrictions_case(const std::filesystem::path& root) {
  Harness h(root); const auto old = h.web.read("alice", args());
  CHECK(old.at("indexed").flag()); const auto id = old.at("snapshot_id").str();
  h.worker.respond = [&](const Json& params) {
    auto out = h.worker.page(params);
    out.set("http_freshness", freshness_record(target, h.clock.load(), "no-store"));
    out.set("publication_observation", policy(true));
    out.set("usage", Json::parse(R"({"external_requests":0,"content_bytes":0})"));
    return out;
  };
  const auto result = pair(h);
  const auto retained = h.store.snapshot("alice", id);
  const auto cached = h.store.cached("alice", target);
  diagnostic = Json::object(); diagnostic.set("callers", outcomes(result));
  diagnostic.set("retained_indexed", retained.at("indexed"));
  diagnostic.set("retained_meta", retained.at("meta"));
  diagnostic.set("freshness_still_present", cached && cached->at("meta").has("http_freshness"));
  diagnostic.set("physical_fetches_including_seed", static_cast<std::int64_t>(h.worker.count()));
  CHECK(result[0].code == "E_WORKER_PROTOCOL"); CHECK(result[1].code == "E_WORKER_PROTOCOL");
  check_receipts(result, 0, 0, false);
  CHECK(h.worker.count() == 2); CHECK(retained.at("canonical_text").str() == body);
  // Correct behavior must preserve independently validated negative policy
  // observations even though its usage receipt is unusable. Do not weaken
  // this expectation to match a candidate that skips those restrictions.
  CHECK(!retained.at("indexed").flag());
  CHECK(cached.has_value()); CHECK(!cached->at("meta").has("http_freshness"));
  return diagnostic;
}
}

int main(int argc, char** argv) {
  using namespace bzs;
  if (argc != 2) { std::cerr << "Pass a new, nonexistent fixture root\n"; return 3; }
  const std::filesystem::path root(argv[1]);
  if (std::filesystem::exists(root)) { std::cerr << "Refusing to reuse existing fixtures\n"; return 3; }
  std::filesystem::create_directories(root);
  auto records = Json::array(); std::int64_t failures = 0;
  const auto run = [&](const std::string& name, const std::function<Json(const std::filesystem::path&)>& action) {
    const auto begin = checks; auto record = Json::object(); record.set("case", name);
    diagnostic = Json();
    const auto folder = root / name; std::filesystem::create_directory(folder);
    try { record.set("observation", action(folder)); record.set("passed", true); }
    catch (const std::exception& error) { ++failures; record.set("passed", false); record.set("error", error.what()); if (!diagnostic.is_null()) record.set("diagnostic", diagnostic); }
    record.set("checks", checks - begin); records.push(record);
  };
  for (const auto& kind : {"body", "no_store", "failed"})
    run(std::string("profile_restrictions_") + kind, [&](const auto& path) { return profile_restriction_case(path, kind); });
  run("304_positive_control", [](const auto& path) { return metadata_304_case(path, false); });
  run("304_full_metadata_delivery_race", [](const auto& path) { return metadata_304_case(path, true); });
  for (const auto& which : {"original", "final", "intermediate"})
    run(std::string("alias_delivery_") + which, [&](const auto& path) { return alias_delivery_case(path, which); });
  run("principal_revocation_before_subscriber_delivery", revoke_delivery_case);
  run("payer_window_failure_preserves_subscriber_and_receipt", [](const auto& path) { return window_error_case(path, true); });
  run("subscriber_window_failure_preserves_payer_and_receipt", [](const auto& path) { return window_error_case(path, false); });
  run("unknown_physical_failure_has_no_retry_or_zero_claim", unknown_failure_case);
  run("malformed_usage_does_not_discard_valid_no_store_and_noindex", malformed_usage_restrictions_case);
  auto report = Json::object(); report.set("probe", "independent-direct-boundaries-v1");
  report.set("cases", static_cast<std::int64_t>(records.size())); report.set("checks", checks);
  report.set("failures", failures); report.set("results", records);
  report.set("limits", "Offline Worker, two threads, deterministic member observation and subscriber delivery hook, public second-Store mutations. No new public cancellation API, process-crash, real-site or full B-24 acceptance claim.");
  std::cout << report.dump(true) << '\n'; return failures == 0 ? 0 : 1;
}
