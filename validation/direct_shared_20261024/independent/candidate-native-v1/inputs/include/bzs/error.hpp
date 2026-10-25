#pragma once
#include "bzs/json.hpp"
#include <stdexcept>
#include <string>
#include <utility>
namespace bzs {
  class Error final : public std::runtime_error {
    public: std::string code;
    int http_status;
    // Optional Core-owned execution receipt. Transport and thin adapters may
    // preserve it; they must not infer zero usage from a failed response.
    Json details;
    Error(std::string c, std::string message, int status=400, Json context=Json()) : std::runtime_error(std::move(message)), code(std::move(c)), http_status(status), details(std::move(context)) {
    }
  };
  inline void require(bool condition, const std::string& code, const std::string& message, int status=400) {
    if (!condition) throw Error(code, message, status);
  }
}
