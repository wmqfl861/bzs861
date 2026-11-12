#pragma once
#include <cstdint>
#include <exception>
namespace bzs {
  // CL-14: process-wide structured diagnostics for swallowed internal
  // errors. Counting is lock-free; logging is best-effort and never throws.
  // Events carry only a static where-string, the exception type and a
  // truncated what() - never URLs, queries, bodies, principals or tokens.
  // Exception messages must obey docs/SECURITY.md; truncation is not redaction.
  std::int64_t internal_error_count() noexcept;
  void log_internal(const char* where, const std::exception* exception) noexcept;
  // Call only inside a catch block with an active exception.
  void log_current_internal(const char* where) noexcept;
}
