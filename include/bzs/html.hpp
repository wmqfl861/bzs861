#pragma once
#include "bzs/json.hpp"
#include <string>
#include <string_view>
namespace bzs {
  struct ParsedDocument {
    std::string title, text, content_type, parser_version="bootstrap-html/2";
    Json links=Json::array();
    bool index_allowed=true;
  };
  ParsedDocument parse_document(std::string_view input, const std::string& content_type, const std::string& fallback_title="");
}
