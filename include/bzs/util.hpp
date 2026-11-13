#pragma once
#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>
namespace bzs {
  bool valid_utf8(std::string_view s);
  bool utf8_boundary(std::string_view s, std::size_t p);
  std::string ascii_lower(std::string_view s);
  std::string sha256(std::string_view s);
  std::string random_id();
  std::int64_t now_ms();
  std::string read_file(const std::filesystem::path& p, std::size_t max=1024*1024);
  // SQLite open functions take narrow filenames in UTF-8 on Windows; the
  // native narrow encoding there is the ANSI code page, so u8string() must be
  // used for non-ASCII paths. POSIX narrow paths already are bytes.
  std::string utf8_path(const std::filesystem::path& p);
  std::string metadata_host(std::string_view url);
  // A deletion comparison key, not a fetch URL, publication key or admission rule.
  // Invalid legacy metadata URLs retain their exact identity.
  std::string deletion_url_key(std::string_view url);
  std::string utf8_prefix(std::string_view text, std::size_t max_bytes);
  void require_identifier(std::string_view value);
}
