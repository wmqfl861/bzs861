#include "bzs/util.hpp"
#include "bzs/error.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <array>
#include <chrono>
#include <fstream>
#include <memory>
namespace bzs {
  bool valid_utf8(std::string_view s) {
    for(std::size_t i=0; i<s.size();) {
      auto c=static_cast<unsigned char>(s[i++]);
      if(c<0x80)continue;
      unsigned n=0, cp=0, min=0;
      if(c>=0xc2&&c<=0xdf) {
        n=1;
        cp=c&31u;
        min=0x80;
      } else if(c>=0xe0&&c<=0xef) {
        n=2;
        cp=c&15u;
        min=0x800;
      } else if(c>=0xf0&&c<=0xf4) {
        n=3;
        cp=c&7u;
        min=0x10000;
      } else return false;
      if(i+n>s.size())return false;
      for(unsigned j=0; j<n; ++j) {
        auto d=static_cast<unsigned char>(s[i++]);
        if((d&0xc0u)!=0x80u)return false;
        cp=(cp<<6u)|(d&63u);
      }
      if(cp<min||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff))return false;
    }
    return true;
  }
  bool utf8_boundary(std::string_view s, std::size_t p) {
    return p<=s.size()&&(p==s.size()||(static_cast<unsigned char>(s[p])&0xc0u)!=0x80u);
  }
  std::string ascii_lower(std::string_view s) {
    std::string o(s);
    for(char&c:o)if(c>='A'&&c<='Z')c=static_cast<char>(c-'A'+'a');
    return o;
  }
  std::string sha256(std::string_view s) {
    unsigned char hash[EVP_MAX_MD_SIZE];
    unsigned n=0;
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    require(ctx&&EVP_DigestInit_ex(ctx.get(), EVP_sha256(), nullptr)==1&&EVP_DigestUpdate(ctx.get(), s.data(), s.size())==1&&EVP_DigestFinal_ex(ctx.get(), hash, &n)==1, "E_CRYPTO", "Digest failed", 500);
    constexpr char hex[]="0123456789abcdef";
    std::string out;
    out.reserve(n*2);
    for(unsigned i=0; i<n; ++i) {
      out+=hex[hash[i]>>4];
      out+=hex[hash[i]&15];
    }
    return out;
  }
  std::string random_id() {
    std::array<unsigned char, 24> b {
    };
    require(RAND_bytes(b.data(), static_cast<int>(b.size()))==1, "E_CRYPTO", "Random generator failed", 500);
    constexpr char h[]="0123456789abcdef";
    std::string s;
    for(auto x:b) {
      s+=h[x>>4];
      s+=h[x&15];
    }
    return s;
  }
  std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
  }
  std::string read_file(const std::filesystem::path& p, std::size_t max) {
    std::ifstream f(p, std::ios::binary);
    require(f.good(), "E_FILE", "Cannot open input file");
    std::string s;
    std::array<char, 8192>b {
    };
    while(f) {
      f.read(b.data(), static_cast<std::streamsize>(b.size()));
      auto n=f.gcount();
      if(n>0) {
        require(s.size()+static_cast<std::size_t>(n)<=max, "E_CONTENT_TOO_LARGE", "Input exceeds size limit", 413);
        s.append(b.data(), static_cast<std::size_t>(n));
      }
    }
    require(f.eof(), "E_FILE", "Input read failed");
    return s;
  }
  std::string metadata_host(std::string_view url) {
    require(url.size()<=4096&&url.find('\0')==std::string_view::npos, "E_URL", "Invalid metadata URL");
    for(unsigned char c:url)require(c>32&&c<127, "E_URL", "URL must be ASCII without whitespace; encode non-ASCII characters");
    std::size_t start=0;
    if(url.starts_with("https://"))start=8;
    else if(url.starts_with("http://"))start=7;
    else throw Error("E_URL", "Only http/https metadata URLs are accepted; no network fetch occurs");
    auto end=url.find_first_of("/?#", start);
    auto authority=url.substr(start, end==std::string_view::npos?url.size()-start:end-start);
    require(!authority.empty()&&authority.find_first_of("@\\%[]:")==std::string_view::npos, "E_URL", "Credentials, encoded hosts, IPv6 and ports are not accepted by bootstrap metadata parser");
    auto host=ascii_lower(authority);
    require(host.size()<=253&&host.front()!='.'&&host.back()!='.', "E_URL", "Invalid hostname");
    std::size_t len=0;
    char prev='.';
    for(char c:host) {
      require((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-'||c=='.', "E_URL", "Invalid hostname");
      if(c=='.') {
        require(len>0&&len<=63&&prev!='-', "E_URL", "Invalid DNS label");
        len=0;
      }else {
        require(!(len==0&&c=='-'), "E_URL", "Invalid DNS label");
        ++len;
      }
      prev=c;
    }
    require(len>0&&len<=63&&prev!='-', "E_URL", "Invalid DNS label");
    return host;
  }
  std::string utf8_prefix(std::string_view s, std::size_t max) {
    auto n=std::min(s.size(), max);
    while(!utf8_boundary(s, n))--n;
    return std::string(s.substr(0, n));
  }
  void require_identifier(std::string_view s) {
    require(!s.empty()&&s.size()<=128, "E_SCHEMA", "Invalid identifier length");
    for(char c:s)require((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='-'||c=='_'||c=='.', "E_SCHEMA", "Invalid identifier characters");
  }
}
