// Diagnostic for the residual failure in the frozen publication-scope probe.
// Reuse the frozen direct-response fixture; do not change the original probe.
#define main frozen_direct_route_failure_main
#include "/tmp/bzs-p2-redirect-review-revocation-v1/direct_route_failure_probe.cpp"
#undef main
#include "bzs/query.hpp"

int main() {
  const auto path=std::filesystem::temp_directory_path()/("bzs-direct-projection-"+random_id());
  int result=2;
  try {
    Store store(path,[]{return now;});Worker worker;WebService web(store,worker,{});
    const auto first=web.read("review-owner",parameters("cache_first"));
    auto deny=Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex"],"diagnostics":[]})");
    store.observe_web_publication("review-owner",original,original,deny,store.publication_fence("review-owner",original));
    const auto search=store.search("review-owner",parse_query(Json::string("retained")));
    const auto cached=web.read("review-owner",parameters("cache_only"));
    const auto snap=store.snapshot("review-owner",first.at("snapshot_id").str());
    auto report=Json::object();report.set("current_search_count",search.at("frozen_count"));
    report.set("cache_only_indexed",cached.at("indexed"));report.set("snapshot_indexed",snap.at("indexed"));
    report.set("origin_requests",std::int64_t{worker.reads});report.set("history_retained",snap.at("canonical_text").str()=="retained direct body");
    std::cout<<report.canonical()<<'\n';
    require(search.at("frozen_count").num()==0&&worker.reads==1,"E_REVIEW","Unexpected publication/search or request behavior");
    result=cached.at("indexed").flag()?1:0;
  } catch(const std::exception& error) {std::cout<<"ERROR "<<error.what()<<'\n';}
  std::filesystem::remove_all(path);return result;
}
