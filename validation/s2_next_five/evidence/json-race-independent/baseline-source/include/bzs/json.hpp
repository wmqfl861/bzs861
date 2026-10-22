#pragma once
#include <cstdint>
#include <initializer_list>
#include <string>
#include <string_view>
#include <vector>
struct json_object;
namespace bzs {
  // Owned reference to json-c data. No implicit scalar conversions or missing-key defaults.
  class Json {
    json_object* p_=nullptr;
    public: Json()=default;
    explicit Json(json_object* p):p_(p) {
    }
    Json(const Json& other);
    Json(Json&& other) noexcept;
    Json& operator=(Json other) noexcept;
    ~Json();
    static Json object();
    static Json array();
    static Json string(std::string_view s);
    static Json integer(std::int64_t n);
    static Json boolean(bool b);
    // max_bytes: 2 MiB for client requests; trusted internal channels may allow more.
    static Json parse(std::string_view text, std::size_t max_bytes=2*1024*1024);
    std::string dump(bool pretty=false) const;
    std::string canonical() const;
    bool is_object() const;
    bool is_array() const;
    bool is_string() const;
    bool is_integer() const;
    bool is_bool() const;
    bool is_null() const {
      return p_==nullptr;
    }
    bool has(const std::string& key) const;
    Json at(const std::string& key) const;
    Json at(std::size_t index) const;
    std::string str() const;
    std::int64_t num() const;
    bool flag() const;
    std::size_t size() const;
    std::vector<std::string> keys() const;
    void set(const std::string& key, const Json& value);
    void set(const std::string& key, const std::string& value) {
      set(key, string(value));
    }
    void set(const std::string& key, const char* value) {
      set(key, string(value));
    }
    void set(const std::string& key, std::int64_t value) {
      set(key, integer(value));
    }
    void set(const std::string& key, bool value) {
      set(key, boolean(value));
    }
    void push(const Json& value);
    void allowed(std::initializer_list<std::string_view> fields) const;
  };
  std::string required_string(const Json& object, const std::string& key, std::size_t max=8192);
  std::int64_t int_option(const Json& object, const std::string& key, std::int64_t fallback, std::int64_t low, std::int64_t high);
}
