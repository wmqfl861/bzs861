#pragma once
#include <stdexcept>
#include <string>
#include <utility>
namespace bzs {
  class Error final : public std::runtime_error {
    public: std::string code;
    int http_status;
    Error(std::string c, std::string message, int status=400) : std::runtime_error(std::move(message)), code(std::move(c)), http_status(status) {
    }
  };
  inline void require(bool condition, const std::string& code, const std::string& message, int status=400) {
    if (!condition) throw Error(code, message, status);
  }
}
