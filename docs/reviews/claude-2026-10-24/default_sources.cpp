// Repro: with the shipped config/sources.json, web search cannot run at all.
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include <cstdio>
using namespace bzs;

struct NoWorker : WorkerPort {
  Json call(const std::string& method, const Json&, int) override {
    std::printf("unexpected worker call: %s\n", method.c_str());
    return Json::object();
  }
};

int main(int argc, char** argv) {
  const char* manifest = argc > 1 ? argv[1] : "config/sources.json";
  auto registry = SourceRegistry::load(manifest);
  std::size_t enabled = 0;
  for (const auto& s : registry.all()) enabled += s.enabled ? 1 : 0;
  std::printf("sources=%zu enabled=%zu\n", registry.all().size(), enabled);
  Store store(":memory:");
  NoWorker worker;
  WebService web(store, worker, std::move(registry));
  for (const char* category : {"web", "all"}) {
    auto args = Json::object();
    args.set("query", "sqlite fts5 tokenizer");
    args.set("category", category);
    try { web.search("alice", args); std::puts("search ran"); }
    catch (const Error& e) { std::printf("web.search category=%s -> %s: %s\n", category, e.code.c_str(), e.what()); }
  }
  return 0;
}
