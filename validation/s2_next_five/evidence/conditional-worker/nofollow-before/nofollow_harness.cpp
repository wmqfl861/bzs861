#define main unused_reference_suite_main
#include "fixture_reference.cpp"
#undef main

int main() {
  bool all_safe = true;
  try {
    for (const bool during : {false, true}) {
      Temp temp;
      Worker worker;
      worker.links[url()] = {url("/docs/child")};
      Store store(temp.path / "db", [&] { return worker.now; });
      WebService web(store, worker, {}, SiteRegistry(manifest()));
      web.submit_crawl("alice", crawl_args("original-full-body"));
      drain(web);
      const auto original = store.cached("alice", url());
      CHECK(original.has_value());
      CHECK(original->at("publication").at("follow_allowed").flag());
      CHECK(worker.calls.size() == 3);

      std::string failed_read_code;
      if (!during) {
        worker.alter = [](const Json& request, Json) {
          auto failure = Json::object();
          failure.set("status", "timeout");
          failure.set("http_status", std::int64_t{200});
          failure.set("final_url", request.at("url"));
          failure.set("redirects", Json::array());
          failure.set("error", "fixture body timeout after nofollow headers");
          failure.set("usage", Json::parse(R"({"external_requests":1,"content_bytes":0})"));
          failure.set("publication_observation", restriction("nofollow"));
          return failure;
        };
        try {
          (void)web.read("alice", read_args());
          throw std::runtime_error("failed refresh unexpectedly succeeded");
        } catch (const Error& failure) {
          failed_read_code = failure.code;
        }
        worker.alter = {};
      } else {
        worker.alter = [&](const Json& request, Json response) {
          if (request.at("kind").str() == "page" && request.at("url").str() == url() && request.has("conditional")) {
            store.observe_web_publication("alice", url(), url(), restriction("nofollow"),
                                          store.publication_fence("alice", url()));
          }
          return response;
        };
      }

      const auto before = worker.calls.size();
      const auto job = web.submit_crawl("alice", crawl_args(during ? "during-304" : "after-failed-nofollow"));
      const auto id = job.at("job_id").str();
      drain(web);
      const auto after = store.cached("alice", url());
      const bool first_conditional = worker.calls.at(before).has("conditional");
      const bool followed_child = worker.calls.size() > before + 1;
      const bool stored_follow = after->at("publication").at("follow_allowed").flag();
      const bool safe = first_conditional && !followed_child && !stored_follow;
      all_safe = all_safe && safe;
      auto result = Json::object();
      result.set("scenario", during ? "nofollow_observed_during_304" : "failed_nofollow_before_304");
      result.set("failed_refresh_error", failed_read_code);
      result.set("root_request_had_conditional", first_conditional);
      result.set("new_requests", static_cast<std::int64_t>(worker.calls.size() - before));
      result.set("stored_follow_allowed", stored_follow);
      result.set("unexpected_child_request", followed_child);
      result.set("safe", safe);
      result.set("job", store.job_get("alice", id));
      result.set("edges", store.crawl_edges("alice", id));
      std::cout << result.dump() << '\n';
    }
    std::cout << (all_safe ? "PASS: 304 preserves observed nofollow without expanding old links"
                          : "REPRODUCED: 304 revived observed nofollow or expanded old links") << '\n';
    return all_safe ? 0 : 1;
  } catch (const std::exception& failure) {
    std::cerr << "HARNESS_ERROR: " << failure.what() << '\n';
    return 2;
  }
}
