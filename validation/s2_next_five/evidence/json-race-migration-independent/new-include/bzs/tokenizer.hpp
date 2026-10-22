#pragma once
#include <string>
#include <string_view>
#include <vector>
struct sqlite3;
namespace bzs {
  // Version persisted with the index; changing token semantics requires a rebuild.
  inline constexpr std::string_view fts_tokenizer_version="bzs-ascii-cjk/2";
  // Registers "bzs" on a connection. ASCII word boundaries agree with the
  // exact query predicate, including underscores and adjacent Unicode marks.
  // CJK runs use per-character positions and colocated bigrams. Other Unicode
  // text is a boundary; its substring queries use the bounded exact scan.
  // Must be called before touching a table which uses this tokenizer.
  void register_fts_tokenizer(sqlite3* db);
  bool is_cjk(char32_t cp);
  bool is_cjk_text(std::string_view text);
  // Test helper: the tokens FTS5 would see (document or query mode).
  std::vector<std::string> debug_tokenize(sqlite3* db, std::string_view text, bool query);
}
