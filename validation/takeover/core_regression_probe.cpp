#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include <iostream>
using namespace bzs;
void probe(const char* label, const std::string& query, const std::string& body) {
  try {
    Store s(":memory:");
    s.ingest("alice", {"https://example.com/x",body,"text/plain","Neutral"});
    auto q=parse_query(Json::string(query));
    auto f=fts_candidates(q);
    auto result=s.search("alice",q);
    std::cout << label << " matches=" << matches(q,"Neutral",body,"https://example.com/x","example.com","text/plain")
      << " frozen_count=" << result.at("frozen_count").num() << " partial=" << result.at("partial").flag()
      << " fts=" << (f?*f:"null") << " ast=" << q.json().dump() << '\n';
  } catch (const Error& e) { std::cout << label << " error=" << e.code << ':' << e.what() << '\n'; }
}
int main() {
  probe("quoted-word-substring", "\"app\"", "apple");
  probe("mixed-ascii-cjk", "test", "test中文");
  probe("mixed-ascii-combining", "test", "test\u0301x");
  probe("underscore-only", "_", "_");
  probe("literal-site-quoted", "\"site:example.com\"", "site:example.com");
  probe("literal-field-quoted", "\"intitle:guide\"", "intitle:guide");
  for (auto html : {"<html><body><aside>before-noise</aside><main>only-main</main><aside>after-noise</aside></body></html>",
                   "<html><body><div hidden><main>hidden-canary</main></div><article>visible</article></body></html>",
                   "<html><head><meta name='robots' content=' none '></head><body>private-text</body></html>",
                   "<html><head><meta name='robots' content='none, follow'></head><body>private-text</body></html>"}) {
    auto doc=parse_document(html,"text/html");
    std::cout << "HTML text=" << doc.text << " index_allowed=" << doc.index_allowed << '\n';
  }
  try {
    auto doc=parse_document("<html><head><title>metadata-only</title></head><body hidden>hidden-canary</body></html>","text/html");
    std::cout << "hidden-body text=" << doc.text << '\n';
  } catch (const Error& e) { std::cout << "hidden-body error=" << e.code << '\n'; }

}
