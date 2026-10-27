#pragma once
#include "bzs/json.hpp"
#include <optional>
#include <string>
#include <utility>
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
  // Exact match and the existing fallback score, sharing lazy ASCII folding.
  // nullopt is a non-match; zero is a matching query with no positive leaves.
  std::optional<int> match_score(const Query& q, std::string_view title, std::string_view body, std::string_view url, std::string_view host, std::string_view type);
  // Safe optional word-FTS candidate query. Other shapes use substring candidates,
  // never a lossy approximation which could silently drop matching documents.
  std::optional<std::string> fts_candidates(const Query& q);
  // Optional token-aligned priority tier for raw-substring leaves. NOT a
  // superset: always combine with a separately budgeted original fallback.
  std::optional<std::string> priority_fts_candidates(const Query& q);
  // Supplementary FTS5/ascii/detail=none candidate expression over indexed_grams.
  // These are necessary substring grams, not replacements for exact matching.
  // nullopt denotes an unconstrained candidate set; callers retain a safe word
  // anchor or the bounded scan. Every emitted leaf includes its overflow marker.
  std::optional<std::string> substring_candidates(const Query& q);
  // Legacy schema-2 ASCII projection. Schema 3 indexes immutable text with
  // the bzs tokenizer; document text and evidence never use this projection.
  std::string fts_index_text(std::string_view text);
  int score_match(const QueryNode& q, std::string_view title, std::string_view body);
  // Exact match spans [start,end) of one leaf in an ASCII-lowered haystack.
  std::vector<std::pair<std::size_t, std::size_t>> leaf_matches(std::string_view hay_lower, const QueryNode& leaf, std::size_t limit);
  // Leaves that must or may match (excludes everything below NOT).
  std::vector<const QueryNode*> positive_leaves(const QueryNode& root);
}
