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
  std::string metadata_host(std::string_view url);
  std::string utf8_prefix(std::string_view text, std::size_t max_bytes);
  void require_identifier(std::string_view value);
}
