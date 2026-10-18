#include "bzs/json.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <json-c/json.h>
#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <memory>
#include <set>
#include <utility>
namespace bzs {
  Json::Json(const Json& o):p_(json_object_get(o.p_)) {
  }
  Json::Json(Json&& o) noexcept:p_(std::exchange(o.p_, nullptr)) {
  }
  Json& Json::operator=(Json o) noexcept {
    std::swap(p_, o.p_);
    return *this;
  }
  Json::~Json() {
    json_object_put(p_);
  }
  Json Json::object() {
    return Json(json_object_new_object());
  }
  Json Json::array() {
    return Json(json_object_new_array());
  }
  Json Json::string(std::string_view s) {
    return Json(json_object_new_string_len(s.data(), static_cast<int>(s.size())));
  }
  Json Json::integer(std::int64_t v) {
    return Json(json_object_new_int64(v));
  }
  Json Json::boolean(bool v) {
    return Json(json_object_new_boolean(v));
  }
  namespace {
    // Reject duplicate keys before json-c's last-key-wins parser can discard them.
    // Also bounds nesting and node count independently of request byte size.
    class ShapeGuard {
      std::string_view s;
      std::size_t i=0, nodes=0;
      void ws() {
        while(i<s.size() && (s[i]==' '||s[i]=='\n'||s[i]=='\r'||s[i]=='\t')) ++i;
      }
      unsigned hex4() {
        require(i+4<=s.size(), "E_JSON", "Incomplete Unicode escape");
        unsigned cp=0;
        for(int k=0; k<4; ++k) {
          char c=s[i++];
          unsigned v=0;
          if(c>='0'&&c<='9')v=static_cast<unsigned>(c-'0');
          else if(c>='a'&&c<='f')v=static_cast<unsigned>(c-'a'+10);
          else if(c>='A'&&c<='F')v=static_cast<unsigned>(c-'A'+10);
          else throw Error("E_JSON", "Invalid Unicode escape");
          cp=(cp<<4)|v;
        }
        return cp;
      }
      std::string quoted() {
        require(i<s.size() && s[i]=='"', "E_JSON", "JSON string expected");
        auto start=i++;
        while(i<s.size()) {
          unsigned char c=static_cast<unsigned char>(s[i++]);
          if(c=='"') {
            auto raw=std::string(s.substr(start, i-start));
            std::unique_ptr<json_tokener, decltype(&json_tokener_free)> tok(json_tokener_new(), json_tokener_free);
            // UTF-8 is validated by our scalar-value validator, not a locale-dependent library flag.
            json_tokener_set_flags(tok.get(), 0);
            // ShapeGuard enforces RFC JSON grammar before decoding.
            Json value(json_tokener_parse_ex(tok.get(), raw.c_str(), static_cast<int>(raw.size()+1)));
            require(json_tokener_get_error(tok.get())==json_tokener_success && value.is_string(), "E_JSON", "Invalid JSON string");
            require(valid_utf8(value.str()), "E_UTF8", "Decoded string is not UTF-8");
            return value.str();
          }
          require(c>=32, "E_JSON", "Unescaped control character");
          if(c=='\\') {
            require(i<s.size(), "E_JSON", "Incomplete escape");
            char e=s[i++];
            if(e=='u') {
              auto cp=hex4();
              if(cp>=0xd800&&cp<=0xdbff) {
                require(i+2<=s.size()&&s.substr(i, 2)=="\\u", "E_JSON", "Unpaired high surrogate");
                i+=2;
                auto low=hex4();
                require(low>=0xdc00&&low<=0xdfff, "E_JSON", "Invalid low surrogate");
              } else require(cp<0xdc00||cp>0xdfff, "E_JSON", "Unpaired low surrogate");
            }else require(std::string_view("\"\\/bfnrt").find(e)!=std::string_view::npos, "E_JSON", "Unknown escape");
          }
        }
        throw Error("E_JSON", "Unclosed JSON string");
      }
      void value(int depth) {
        require(depth<=64 && ++nodes<=20000, "E_JSON_LIMIT", "JSON structure limit exceeded");
        ws();
        require(i<s.size(), "E_JSON", "Missing JSON value");
        char c=s[i];
        if(c=='"') {
          quoted();
          return;
        }
        if(c=='{'||c=='[') {
          bool obj=c=='{';
          char close=obj?'}':']';
          ++i;
          ws();
          std::set<std::string> keys;
          if(i<s.size()&&s[i]==close) {
            ++i;
            return;
          }
          while(true) {
            if(obj) {
              ws();
              auto key=quoted();
              require(key.find('\0')==std::string::npos, "E_JSON", "NUL in object key");
              require(keys.insert(key).second, "E_JSON_DUPLICATE", "Duplicate JSON key");
              ws();
              require(i<s.size()&&s[i++]==':', "E_JSON", "Missing colon");
            }
            value(depth+1);
            ws();
            require(i<s.size(), "E_JSON", "Unclosed JSON container");
            if(s[i]==close) {
              ++i;
              return;
            }
            require(s[i++]==',', "E_JSON", "Missing comma");
          }
        }
        auto start=i;
        while(i<s.size() && s[i]!=',' && s[i]!=']' && s[i]!='}' && !std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        require(start<i, "E_JSON", "Invalid JSON value");
        auto token=s.substr(start, i-start);
        if(token=="true"||token=="false"||token=="null")return;
        std::size_t p=0;
        if(p<token.size()&&token[p]=='-')++p;
        require(p<token.size(), "E_JSON", "Invalid number");
        if(token[p]=='0')++p;
        else {
          require(token[p]>='1'&&token[p]<='9', "E_JSON", "Invalid number");
          while(p<token.size()&&token[p]>='0'&&token[p]<='9')++p;
        }
        if(p<token.size()&&token[p]=='.') {
          auto begin=++p;
          while(p<token.size()&&token[p]>='0'&&token[p]<='9')++p;
          require(p>begin, "E_JSON", "Invalid fraction");
        }
        if(p<token.size()&&(token[p]=='e'||token[p]=='E')) {
          ++p;
          if(p<token.size()&&(token[p]=='+'||token[p]=='-'))++p;
          auto begin=p;
          while(p<token.size()&&token[p]>='0'&&token[p]<='9')++p;
          require(p>begin, "E_JSON", "Invalid exponent");
        }
        require(p==token.size(), "E_JSON", "Invalid numeric value");
        require(token.size()<=128, "E_JSON_LIMIT", "Number token too long");
        if(token.find_first_of(".eE")==std::string_view::npos) {
          std::int64_t n=0;
          auto parsed=std::from_chars(token.data(), token.data()+token.size(), n);
          require(parsed.ec==std::errc {
          }, "E_JSON_RANGE", "Integer exceeds signed 64-bit range");
        } else {
          double n=0;
          auto parsed=std::from_chars(token.data(), token.data()+token.size(), n);
          require(parsed.ec==std::errc {
          }
          &&std::isfinite(n), "E_JSON_RANGE", "Non-finite or out-of-range number");
        }
      }
      public: explicit ShapeGuard(std::string_view text):s(text) {
      }
      void run() {
        value(0);
        ws();
        require(i==s.size(), "E_JSON", "Trailing data");
      }
    };
  }
  Json Json::parse(std::string_view text, std::size_t max_bytes) {
    require(!text.empty() && text.size()<=max_bytes, "E_JSON_LIMIT", "JSON size outside supported limits");
    require(valid_utf8(text), "E_UTF8", "Invalid UTF-8 input");
    ShapeGuard(text).run();
    std::string owned(text);
    std::unique_ptr<json_tokener, decltype(&json_tokener_free)> tok(json_tokener_new_ex(72), json_tokener_free);
    require(static_cast<bool>(tok), "E_MEMORY", "JSON allocation failed", 500);
    json_tokener_set_flags(tok.get(), 0);
    // ShapeGuard enforces RFC JSON grammar before decoding.
    Json out(json_tokener_parse_ex(tok.get(), owned.c_str(), static_cast<int>(owned.size()+1)));
    require(json_tokener_get_error(tok.get())==json_tokener_success, "E_JSON", "Invalid JSON syntax");
    return out;
  }
  std::string Json::dump(bool pretty) const {
    if(!p_) return "null";
    return json_object_to_json_string_ext(p_, pretty?JSON_C_TO_STRING_PRETTY:JSON_C_TO_STRING_PLAIN);
  }
  std::string Json::canonical() const {
    if(is_object()) {
      auto k=keys();
      std::sort(k.begin(), k.end());
      std::string s="{";
      bool first=true;
      for(const auto& key:k) {
        if(!first)s+=',';
        first=false;
        s+=string(key).dump()+":"+at(key).canonical();
      }
      return s+"}";
    }
    if(is_array()) {
      std::string s="[";
      for(std::size_t i=0; i<size(); ++i) {
        if(i)s+=',';
        s+=at(i).canonical();
      }
      return s+"]";
    }
    return dump();
  }
  bool Json::is_object()const {
    return json_object_is_type(p_, json_type_object);
  }
  bool Json::is_array()const {
    return json_object_is_type(p_, json_type_array);
  }
  bool Json::is_string()const {
    return json_object_is_type(p_, json_type_string);
  }
  bool Json::is_integer()const {
    return json_object_is_type(p_, json_type_int);
  }
  bool Json::is_bool()const {
    return json_object_is_type(p_, json_type_boolean);
  }
  bool Json::has(const std::string& key)const {
    json_object* x=nullptr;
    return is_object()&&json_object_object_get_ex(p_, key.c_str(), &x);
  }
  Json Json::at(const std::string& key)const {
    require(is_object(), "E_SCHEMA", "Object required");
    json_object* v=nullptr;
    require(json_object_object_get_ex(p_, key.c_str(), &v), "E_SCHEMA", "Missing field: "+key);
    return Json(json_object_get(v));
  }
  Json Json::at(std::size_t i)const {
    require(is_array()&&i<size(), "E_SCHEMA", "Invalid array index");
    return Json(json_object_get(json_object_array_get_idx(p_, i)));
  }
  std::string Json::str()const {
    require(is_string(), "E_SCHEMA", "String required");
    return {
      json_object_get_string(p_), static_cast<std::size_t>(json_object_get_string_len(p_))
    };
  }
  std::int64_t Json::num()const {
    require(is_integer(), "E_SCHEMA", "Integer required");
    return json_object_get_int64(p_);
  }
  bool Json::flag()const {
    require(is_bool(), "E_SCHEMA", "Boolean required");
    return json_object_get_boolean(p_)!=0;
  }
  std::size_t Json::size()const {
    if(is_array())return json_object_array_length(p_);
    if(is_object())return static_cast<std::size_t>(json_object_object_length(p_));
    throw Error("E_SCHEMA", "Container required");
  }
  std::vector<std::string> Json::keys()const {
    require(is_object(), "E_SCHEMA", "Object required");
    std::vector<std::string> out;
    json_object_object_foreach(p_, key, value) {
      (void)value;
      out.emplace_back(key);
    }
    return out;
  }
  void Json::set(const std::string& k, const Json& v) {
    require(is_object(), "E_INTERNAL", "Not an object", 500);
    json_object_object_add(p_, k.c_str(), json_object_get(v.p_));
  }
  void Json::push(const Json& v) {
    require(is_array(), "E_INTERNAL", "Not an array", 500);
    json_object_array_add(p_, json_object_get(v.p_));
  }
  void Json::allowed(std::initializer_list<std::string_view> fields)const {
    for(const auto& key:keys())require(std::find(fields.begin(), fields.end(), key)!=fields.end(), "E_SCHEMA", "Unknown field: "+key);
  }
  std::string required_string(const Json& j, const std::string& k, std::size_t max) {
    auto s=j.at(k).str();
    require(!s.empty()&&s.size()<=max&&s.find('\0')==std::string::npos, "E_SCHEMA", "Invalid string length/content: "+k);
    return s;
  }
  std::int64_t int_option(const Json& j, const std::string& k, std::int64_t fallback, std::int64_t low, std::int64_t high) {
    auto n=j.has(k)?j.at(k).num():fallback;
    require(n>=low&&n<=high, "E_SCHEMA", "Integer out of range: "+k);
    return n;
  }
}
