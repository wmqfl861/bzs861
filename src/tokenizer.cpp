#include "bzs/tokenizer.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <new>
#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <unordered_set>
namespace bzs {
  bool is_cjk(char32_t c) {
    return (c>=0x3040&&c<=0x30FF)||(c>=0x3100&&c<=0x312F)||(c>=0x31A0&&c<=0x31BF)||
           (c>=0x3400&&c<=0x4DBF)||(c>=0x4E00&&c<=0x9FFF)||(c>=0xAC00&&c<=0xD7AF)||
           (c>=0x1100&&c<=0x11FF)||(c>=0x3130&&c<=0x318F)||(c>=0xF900&&c<=0xFAFF)||
           (c>=0xFF66&&c<=0xFF9F)||(c>=0x20000&&c<=0x2FA1F)||(c>=0x30000&&c<=0x3134F);
  }
  namespace {
    // Callers validate UTF-8 once before decoding. Positions always refer to
    // original bytes, never a normalized or stripped copy of the document.
    char32_t decode(std::string_view text, std::size_t& i) {
      auto c=static_cast<unsigned char>(text[i++]);
      if(c<0x80)return c;
      unsigned remaining=c<0xe0?1U:c<0xf0?2U:3U;
      char32_t cp=c&(remaining==1?0x1fU:remaining==2?0x0fU:0x07U);
      while(remaining--)cp=(cp<<6)|(static_cast<unsigned char>(text[i++])&0x3fU);
      return cp;
    }
    char32_t ascii_fold_codepoint(char32_t value) {
      return value>=U'A'&&value<=U'Z'?value+(U'a'-U'A'):value;
    }
    std::string encoded_gram(char width, std::uint64_t value) {
      // Three 21-bit Unicode codepoints fit in 63 bits. The width prefix makes
      // leading-zero codepoints unambiguous without changing their values.
      std::array<char,16> digits{};
      const auto converted=std::to_chars(digits.data(),digits.data()+digits.size(),value,16);
      require(converted.ec==std::errc{},"E_INTERNAL","Cannot encode substring gram",500);
      std::string result(1,width);
      result.append(digits.data(),static_cast<std::size_t>(converted.ptr-digits.data()));
      return result;
    }
    bool word_char(unsigned char c) {
      return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_';
    }
    bool alpha(char c) { return (c>='a'&&c<='z')||(c>='A'&&c<='Z'); }
    struct Tokenizer {};
    struct Ctx {
      void* pctx;
      int (*emit)(void*, int, const char*, int, int, int);
      std::string_view text;
      bool query;
      int token(std::size_t start, std::size_t end, int flags=0) const {
        return emit(pctx, flags, text.data()+start, static_cast<int>(end-start),
                    static_cast<int>(start), static_cast<int>(end));
      }
    };
    int emit_cjk_run(const Ctx& c, const std::vector<std::size_t>& starts, std::size_t end) {
      auto n=starts.size();
      auto at=[&](std::size_t i) { return i+1<n?starts[i+1]:end; };
      int rc=SQLITE_OK;
      if(c.query) {
        if(n==1)return c.token(starts[0], end);
        // Bigrams plus the final character occupy exactly the same positions
        // as a matching substring of a longer CJK document run.
        for(std::size_t i=0;i+1<n&&rc==SQLITE_OK;++i)rc=c.token(starts[i],at(i+1));
        if(rc==SQLITE_OK)rc=c.token(starts.back(),end);
      } else {
        for(std::size_t i=0;i<n&&rc==SQLITE_OK;++i) {
          rc=c.token(starts[i],at(i));
          if(rc==SQLITE_OK&&i+1<n)rc=c.token(starts[i],at(i+1),FTS5_TOKEN_COLOCATED);
        }
      }
      return rc;
    }
    int tokenize(const Ctx& c) {
      auto text=c.text;
      std::size_t i=0;
      while(i<text.size()) {
        if(word_char(static_cast<unsigned char>(text[i]))) {
          auto start=i;
          bool letters=true;
          while(i<text.size()&&word_char(static_cast<unsigned char>(text[i]))) {
            letters=letters&&alpha(text[i]);
            ++i;
          }
          auto token=ascii_lower(text.substr(start,i-start));
          std::string suffix;
          if(letters&&i<text.size()) {
            if(i+1<text.size()&&text[i]=='+'&&text[i+1]=='+')suffix="++";
            else if(text[i]=='#')suffix="#";
          }
          int rc=SQLITE_OK;
          if(suffix.empty()||!c.query)
            rc=c.emit(c.pctx,0,token.data(),static_cast<int>(token.size()),static_cast<int>(start),static_cast<int>(i));
          if(rc!=SQLITE_OK)return rc;
          if(!suffix.empty()) {
            token+=suffix;
            rc=c.emit(c.pctx,c.query?0:FTS5_TOKEN_COLOCATED,token.data(),static_cast<int>(token.size()),
                      static_cast<int>(start),static_cast<int>(i+suffix.size()));
          }
          if(rc!=SQLITE_OK)return rc;
          continue;
        }
        auto start=i;
        auto cp=decode(text,i);
        if(!is_cjk(cp))continue;
        std::vector<std::size_t> starts{start};
        while(i<text.size()) {
          auto next=i;
          if(!is_cjk(decode(text,next)))break;
          starts.push_back(i);
          i=next;
        }
        auto rc=emit_cjk_run(c,starts,i);
        if(rc!=SQLITE_OK)return rc;
      }
      return SQLITE_OK;
    }
    int x_create(void*, const char**, int nArg, Fts5Tokenizer** out) {
      if(nArg!=0)return SQLITE_ERROR;
      auto* p=new(std::nothrow) Tokenizer;
      if(!p)return SQLITE_NOMEM;
      *out=reinterpret_cast<Fts5Tokenizer*>(p);
      return SQLITE_OK;
    }
    void x_delete(Fts5Tokenizer* p) { delete reinterpret_cast<Tokenizer*>(p); }
    int x_tokenize(Fts5Tokenizer*, void* pctx, int flags, const char* text, int n,
                   int (*emit)(void*, int, const char*, int, int, int)) {
      if(n<0||(!text&&n))return SQLITE_ERROR;
      try {
        std::string_view input(text?text:"",static_cast<std::size_t>(n));
        if(!valid_utf8(input))return SQLITE_ERROR;
        return tokenize(Ctx{pctx,emit,input,(flags&FTS5_TOKENIZE_QUERY)!=0});
      } catch(const std::bad_alloc&) {
        return SQLITE_NOMEM;
      } catch(...) {
        // Never unwind a C++ exception across SQLite's C tokenizer API.
        return SQLITE_ERROR;
      }
    }
    fts5_api* api_for(sqlite3* db) {
      fts5_api* api=nullptr;
      sqlite3_stmt* st=nullptr;
      if(sqlite3_prepare_v2(db,"SELECT fts5(?1)",-1,&st,nullptr)==SQLITE_OK) {
        sqlite3_bind_pointer(st,1,static_cast<void*>(&api),"fts5_api_ptr",nullptr);
        sqlite3_step(st);
      }
      sqlite3_finalize(st);
      return api;
    }
  }
  bool is_cjk_text(std::string_view text) {
    if(text.empty()||!valid_utf8(text))return false;
    for(std::size_t i=0;i<text.size();)if(!is_cjk(decode(text,i)))return false;
    return true;
  }
  std::string indexed_grams(std::string_view text, char field) {
    require(field=='t'||field=='b'||field=='r',"E_INTERNAL","Invalid substring index field",500);
    require(valid_utf8(text),"E_UTF8","Substring index input must be UTF-8");
    std::array<std::unordered_set<std::uint64_t>,3> grams;
    grams[0].reserve(std::min(text.size(),std::size_t{256}));
    grams[1].reserve(std::min(text.size(),std::size_t{2048}));
    grams[2].reserve(std::min(text.size(),std::size_t{4096}));
    std::size_t distinct=0,seen=0;
    std::uint64_t previous=0,before_previous=0;
    const auto add=[&](std::size_t width,std::uint64_t value) {
      if(grams[width].insert(value).second)++distinct;
      return distinct<=substring_max_field_grams;
    };
    for(std::size_t offset=0;offset<text.size();) {
      const auto current=static_cast<std::uint64_t>(ascii_fold_codepoint(decode(text,offset)));
      // Casts precede shifts: char32_t itself may only be 32 bits.
      if(!add(0,current)||
         (seen>=1&&!add(1,(previous<<21)|current))||
         (seen>=2&&!add(2,(before_previous<<42)|(previous<<21)|current)))
        return std::string(1,field)+"overflow";
      before_previous=previous;previous=current;if(seen<2)++seen;
    }
    // Only the bounded set of distinct grams reaches the FTS table. In the
    // worst case each token takes 19 bytes including its field and separator.
    std::string out;out.reserve(distinct*19);
    constexpr std::array<char,3> widths{'u','b','t'};
    for(std::size_t width=0;width<grams.size();++width) {
      std::vector<std::uint64_t> ordered(grams[width].begin(),grams[width].end());
      std::sort(ordered.begin(),ordered.end());
      for(const auto value:ordered) {
        if(!out.empty())out+=' ';
        out+=field;out+=encoded_gram(widths[width],value);
      }
    }
    return out;
  }
  std::vector<std::string> query_substring_grams(std::string_view text, std::size_t max_grams) {
    require(text.size()<=4096&&valid_utf8(text),"E_QUERY_LIMIT","Invalid substring query leaf");
    require(max_grams>=1&&max_grams<=32,"E_QUERY_LIMIT","Substring candidate gram budget out of range");
    std::vector<char32_t> points;points.reserve(text.size());
    for(std::size_t offset=0;offset<text.size();)points.push_back(ascii_fold_codepoint(decode(text,offset)));
    if(points.empty())return {};
    const auto width=std::min(points.size(),std::size_t{3});
    std::vector<std::uint64_t> grams;grams.reserve(points.size()-width+1);
    for(std::size_t offset=0;offset+width<=points.size();++offset) {
      std::uint64_t value=0;
      for(std::size_t i=0;i<width;++i)value=(value<<21)|static_cast<std::uint64_t>(points[offset+i]);
      grams.push_back(value);
    }
    std::sort(grams.begin(),grams.end());grams.erase(std::unique(grams.begin(),grams.end()),grams.end());
    const auto count=std::min(grams.size(),max_grams);
    constexpr std::array<char,3> widths{'u','b','t'};
    std::vector<std::string> out;out.reserve(count);
    for(std::size_t i=0;i<count;++i) {
      // Spread selection across distinct gram values instead of retaining only
      // a possibly repetitive prefix. Any subset is still a necessary filter.
      const auto position=count==1?std::size_t{0}:i*(grams.size()-1)/(count-1);
      out.push_back(encoded_gram(widths[width-1],grams[position]));
    }
    return out;
  }
  void register_fts_tokenizer(sqlite3* db) {
    auto* api=api_for(db);
    require(api&&api->iVersion>=2,"E_STORAGE","SQLite FTS5 is unavailable",500);
    fts5_tokenizer t{x_create,x_delete,x_tokenize};
    require(api->xCreateTokenizer(api,"bzs",nullptr,&t,nullptr)==SQLITE_OK,
            "E_STORAGE","Cannot register FTS tokenizer",500);
  }
  std::vector<std::string> debug_tokenize(sqlite3* db, std::string_view text, bool query) {
    auto* api=api_for(db);
    require(api!=nullptr,"E_STORAGE","SQLite FTS5 is unavailable",500);
    void* ud=nullptr;
    fts5_tokenizer t{};
    require(api->xFindTokenizer(api,"bzs",&ud,&t)==SQLITE_OK,"E_STORAGE","bzs tokenizer not registered",500);
    Fts5Tokenizer* tok=nullptr;
    require(t.xCreate(ud,nullptr,0,&tok)==SQLITE_OK,"E_STORAGE","Cannot create tokenizer",500);
    std::vector<std::string> out;
    auto collect=[](void* p,int flags,const char* s,int n,int,int)->int {
      static_cast<std::vector<std::string>*>(p)->push_back(
        std::string((flags&FTS5_TOKEN_COLOCATED)?"+":"")+std::string(s,static_cast<std::size_t>(n)));
      return SQLITE_OK;
    };
    int rc=t.xTokenize(tok,&out,query?FTS5_TOKENIZE_QUERY:FTS5_TOKENIZE_DOCUMENT,
                       text.data(),static_cast<int>(text.size()),collect);
    t.xDelete(tok);
    require(rc==SQLITE_OK,"E_STORAGE","Tokenizer failed",500);
    return out;
  }
}
