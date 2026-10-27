#pragma once
#include <cstddef>
#include <string>
#include <string_view>
#include <vector>
struct sqlite3;
namespace bzs {
  // Version persisted with the index; changing token semantics requires a rebuild.
  inline constexpr std::string_view fts_tokenizer_version="bzs-ascii-cjk/2";
  // The word tokenizer above stays byte-for-byte unchanged. Store migration
  // must atomically build the supplementary index and persist this compatibility
  // marker in the existing fts_tokenizer_version setting before using it.
  // Old binaries reject the new marker instead of writing only the word index.
  inline constexpr std::string_view fts_legacy_gram_index_format_version="bzs-ascii-cjk/2+codepoint-grams/1";
  inline constexpr std::string_view fts_index_format_version="bzs-ascii-cjk/2+codepoint-grams/2";
  inline constexpr std::size_t substring_max_field_grams=65536;
  // Pure ASCII alphanumeric tokens for one field: t=title, b=body, r=url.
  // CJK windows are omitted (word FTS supplies their necessary constraints).
  // Every distinct non-CJK unigram/bigram/trigram is retained, or the field is
  // represented by its explicit overflow token. Never silently truncate grams.
  std::string indexed_grams(std::string_view text, char field);
  // Necessary grams of the complete leaf, without a field prefix. Selection is
  // bounded and deterministic; all selected grams occur in every exact match.
  std::vector<std::string> query_substring_grams(std::string_view text, std::size_t max_grams=8);
  // Bounded necessary CJK runs for a raw-substring leaf; offsets/bytes unchanged.
  std::vector<std::string> query_cjk_runs(std::string_view text);
  // Registers "bzs" on a connection. ASCII word boundaries agree with the
  // exact query predicate, including underscores and adjacent Unicode marks.
  // CJK runs use per-character positions and colocated bigrams. Other Unicode
  // text is a boundary; substring queries use the supplementary gram candidates.
  // Must be called before touching a table which uses this tokenizer.
  void register_fts_tokenizer(sqlite3* db);
  bool is_cjk(char32_t cp);
  bool is_cjk_text(std::string_view text);
  // Test helper: the tokens FTS5 would see (document or query mode).
  std::vector<std::string> debug_tokenize(sqlite3* db, std::string_view text, bool query);
}
