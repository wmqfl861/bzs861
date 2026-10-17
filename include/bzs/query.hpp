#pragma once
#include "bzs/json.hpp"
#include <optional>
#include <string>
#include <vector>
namespace bzs {
  struct QueryNode {
    std::string op="term", field="any", value;
    std::vector<QueryNode> children;
    Json json() const;
  };
  struct Query {
    std::string original;
    QueryNode root;
    std::vector<std::string> hosts, types;
    Json json() const;
  };
  Query parse_query(const Json& input);
  bool matches(const Query& q, std::string_view title, std::string_view body, std::string_view url, std::string_view host, std::string_view type);
  // Safe optional FTS candidate query. Unsupported expressions use a bounded scan,
  // never a lossy approximation which could silently drop matching documents.
  std::optional<std::string> fts_candidates(const Query& q);
  // FTS-only text: token boundaries must match the ASCII term predicate.
  // The immutable document text and evidence offsets never use this projection.
  std::string fts_index_text(std::string_view text);
  int score_match(const QueryNode& q, std::string_view title, std::string_view body);
}
