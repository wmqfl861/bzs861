#pragma once
#include "bzs/json.hpp"
#include <string>
#include <string_view>
namespace bzs {
  struct ParsedDocument {
    std::string title, text, content_type, parser_version="bootstrap-html/3";
    Json links=Json::array();
    bool index_allowed=true;
    // Versioned publication policy; canonical text remains an explicit owner read artifact.
    Json publication=Json::object();
    // A redirected read can have stricter logical-URL rules than its final
    // representation. An empty record means both share publication above.
    Json representation_publication=Json::object();
  };
  ParsedDocument parse_document(std::string_view input, const std::string& content_type, const std::string& fallback_title="");
}
