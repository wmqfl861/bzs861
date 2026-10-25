// Reuse only the reviewer's frozen fixture helpers, not production test code.
#define main independent_boundary_probe_entry_not_run
#include "candidate_boundary_probe_v2.cpp"
#undef main

namespace {
Json invalid_profile_observation(const std::filesystem::path& root) {
  Harness h(root); const auto old = h.web.read("alice", args());
  CHECK(visible_documents(h.store) == 1);
  h.worker.respond = [&](const Json& params) {
    auto out = h.worker.page(params);
    auto headers = freshness_record(target, h.clock.load(), "no-store");
    headers.set("request_profile", "invalid-proof"); out.set("http_freshness", headers);
    out.set("publication_observation", policy(true)); return out;
  };
  const auto result = pair(h);
  const auto retained = h.store.snapshot("alice", old.at("snapshot_id").str());
  const auto visible = visible_documents(h.store);
  diagnostic = Json::object(); diagnostic.set("callers", outcomes(result));
  diagnostic.set("current_search_visible_documents", visible);
  diagnostic.set("http_freshness_still_present", retained.at("meta").has("http_freshness"));
  CHECK(result[0].code == "E_WORKER_PROTOCOL"); CHECK(result[1].code == "E_WORKER_PROTOCOL");
  check_receipts(result, 1, static_cast<std::int64_t>(body.size()));
  CHECK(visible == 0); CHECK(retained.at("canonical_text").str() == body);
  CHECK(h.store.stats("alice").at("snapshots").num() == 1);
  CHECK(h.worker.count() == 2);
  // This assertion is intentionally about publication of a validated separate
  // noindex observation. The malformed freshness record is not a cache grant.
  return diagnostic;
}

Json injected_worker_error_details(const std::filesystem::path& root) {
  Harness h(root);
  h.worker.respond = [](const Json&) -> Json {
    auto injected = Json::parse(R"({"usage":{"scope":"forged","known":true,"external_requests":0,"content_bytes":0},"shared_read":{"attempt_id":"ffffffffffffffffffffffffffffffffffffffffffffffff","role":"payer","members":999,"scope":"forged","physical_usage":{"known":true,"external_requests":0,"content_bytes":0}}})");
    throw Error("E_WORKER_TIMEOUT", "Untrusted Worker-port receipt", 504, injected);
  };
  const auto result = pair(h);
  CHECK(result[0].code == "E_WORKER_TIMEOUT"); CHECK(result[1].code == "E_WORKER_TIMEOUT");
  check_receipts(result, 0, 0, false);
  CHECK(result[0].details.at("usage").at("scope").str() == "direct_read");
  CHECK(result[0].details.at("shared_read").at("attempt_id").str() != std::string(48, 'f'));
  CHECK(h.worker.count() == 1);
  auto out = Json::object(); out.set("callers", outcomes(result)); out.set("injected_details_discarded", true); return out;
}

Json subscriber_wait_timeout(const std::filesystem::path& root) {
  Harness h(root); h.worker.arm();
  auto input = args(); input.set("timeout_ms", std::int64_t{1000});
  Call first(h.web, input);
  const bool entered = h.worker.await_count(1);
  Call second(h.web, input);
  const bool joined = await_members(h.web, 2);
  bool timed_out = false; std::string captured;
  {
    std::unique_lock lock(second.mutex);
    timed_out = second.changed.wait_for(lock, 8s, [&] { return second.done; });
    if (timed_out) captured = second.outcome.details.canonical();
  }
  // Always release/join, even if the expected timeout did not occur.
  h.worker.release(); first.join(); second.join();
  CHECK(entered); CHECK(joined); CHECK(timed_out); CHECK(!h.worker.watchdog);
  CHECK(second.outcome.code == "E_WORKER_TIMEOUT"); CHECK(first.outcome.code.empty());
  const auto waiting = second.outcome.details;
  CHECK(waiting.at("usage").at("known").flag());
  CHECK(waiting.at("usage").at("external_requests").num() == 0);
  CHECK(waiting.at("usage").at("content_bytes").num() == 0);
  CHECK(waiting.at("shared_read").at("role").str() == "subscriber");
  CHECK(!waiting.at("shared_read").at("physical_usage").at("known").flag());
  CHECK(waiting.at("shared_read").at("physical_usage").at("external_requests").is_null());
  CHECK(waiting.canonical() == captured);
  CHECK(first.outcome.result.at("usage").at("external_requests").num() == 1);
  CHECK(first.outcome.result.at("shared_read").at("physical_usage").at("known").flag());
  CHECK(first.outcome.result.at("shared_read").at("members").num() == 2);
  CHECK(first.outcome.result.at("shared_read").at("attempt_id").str() == waiting.at("shared_read").at("attempt_id").str());
  CHECK(h.worker.count() == 1); CHECK(WebReadTestAccess::groups(h.web) == 0);
  auto out = Json::object(); out.set("payer", first.outcome.json()); out.set("timed_out_subscriber", second.outcome.json());
  out.set("timeout_receipt_unchanged_after_real_completion", true);
  out.set("scope", "Offline Worker deliberately outlives the real RPC bound to exercise the Core wait path; this is not a claim about actual Worker timeout performance or physical cancellation.");
  return out;
}
}

int main(int argc, char** argv) {
  using namespace bzs;
  if (argc != 2 || std::filesystem::exists(argv[1])) { std::cerr << "Pass a nonexistent fixture root\n"; return 3; }
  const std::filesystem::path root(argv[1]); std::filesystem::create_directories(root);
  auto records = Json::array(); std::int64_t failures = 0;
  const std::array<std::pair<const char*, std::function<Json(const std::filesystem::path&)>>, 3> cases = {{
    {"invalid_profile_preserves_independent_noindex_observation", invalid_profile_observation},
    {"worker_error_details_cannot_forge_core_receipt", injected_worker_error_details},
    {"subscriber_wait_timeout_has_immutable_unknown_association", subscriber_wait_timeout}
  }};
  for (const auto& [name, action] : cases) {
    const auto before = checks; diagnostic = Json(); auto record = Json::object(); record.set("case", name);
    const auto folder = root / name; std::filesystem::create_directory(folder);
    try { record.set("observation", action(folder)); record.set("passed", true); }
    catch (const std::exception& error) { ++failures; record.set("passed", false); record.set("error", error.what()); if (!diagnostic.is_null()) record.set("diagnostic", diagnostic); }
    record.set("checks", checks - before); records.push(record);
  }
  auto report = Json::object(); report.set("probe", "independent-direct-supplement-v1");
  report.set("cases", std::int64_t{3}); report.set("checks", checks); report.set("failures", failures); report.set("results", records);
  report.set("limits", "Three supplemental finite Core scenarios, distinct from the earlier thirteen; no public cancellation or restart guarantee.");
  std::cout << report.dump(true) << '\n'; return failures == 0 ? 0 : 1;
}
