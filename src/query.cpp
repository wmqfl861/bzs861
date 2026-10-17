#include "bzs/query.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <cctype>
namespace bzs {
  Json QueryNode::json()const {
    Json j=Json::object();
    j.set("op", op);
    if(op=="and"||op=="or") {
      Json a=Json::array();
      for(const auto&n:children)a.push(n.json());
      j.set("children", a);
    } else if(op=="not") j.set("child", children.at(0).json());
    else {
      j.set("field", field);
      j.set("value", value);
    }
    return j;
  }
  Json Query::json()const {
    Json j=Json::object();
    j.set("original", original);
    j.set("ast", root.json());
    Json h=Json::array();
    for(const auto&s:hosts)h.push(Json::string(s));
    j.set("hosts", h);
    Json t=Json::array();
    for(const auto&s:types)t.push(Json::string(s));
    j.set("filetypes", t);
    return j;
  }
  namespace {
    struct Token {
      std::string text, kind;
      bool quoted=false;
      std::size_t unquoted_prefix=0;
      bool has_unquoted_prefix(std::string_view prefix)const {
        return unquoted_prefix>=prefix.size()&&text.starts_with(prefix);
      }
    };
    std::vector<Token> lex(std::string_view text) {
      std::vector<Token> out;
      std::size_t i=0;
      while(i<text.size()) {
        if(std::isspace(static_cast<unsigned char>(text[i]))) {
          ++i;
          continue;
        }
        require(out.size()<256, "E_QUERY_LIMIT", "Too many query tokens");
        if(text[i]=='('||text[i]==')') {
          out.push_back( {
            std::string(1, text[i]), std::string(1, text[i]), false
          });
          ++i;
          continue;
        }
        if(text[i]=='-') {
          out.push_back( {
            "NOT", "NOT", false
          });
          ++i;
          continue;
        }
        std::string word;
        bool quoted=false, in_quote=false;
        std::size_t unquoted_prefix=0;
        while(i<text.size()) {
          char c=text[i];
          if(!in_quote&&(std::isspace(static_cast<unsigned char>(c))||c=='('||c==')'))break;
          ++i;
          if(c=='"') {
            quoted=true;
            in_quote=!in_quote;
            continue;
          }
          if(in_quote&&c=='\\') {
            require(i<text.size(), "E_QUERY_SYNTAX", "Unclosed escape");
            require(text[i]=='"'||text[i]=='\\', "E_QUERY_SYNTAX", "Only quote and backslash may be escaped inside phrases");
            word+=text[i++];
            continue;
          }
          word+=c;
          if(!quoted)++unquoted_prefix;
        }
        require(!in_quote&&!word.empty(), "E_QUERY_SYNTAX", "Unclosed or empty phrase");
        require(word.size()<=4096, "E_QUERY_LIMIT", "Query term too long");
        auto kind=(!quoted&&(word=="AND"||word=="OR"||word=="NOT"))?word:"term";
        out.push_back( {
          word, kind, quoted, unquoted_prefix
        });
      }
      require(!out.empty(), "E_QUERY_SYNTAX", "Empty query");
      return out;
    }
    class Parser {
      const std::vector<Token>& ts;
      std::size_t p=0, nodes=0;
      QueryNode count(QueryNode n) {
        require(++nodes<=256, "E_QUERY_LIMIT", "Too many AST nodes");
        return n;
      }
      QueryNode atom(int depth) {
        require(depth<=16, "E_QUERY_LIMIT", "Query nesting too deep");
        require(p<ts.size(), "E_QUERY_SYNTAX", "Missing expression");
        if(ts[p].kind=="NOT") {
          ++p;
          return count( {
            "not", "any", "", {
              atom(depth+1)
            }
          });
        }
        if(ts[p].kind=="(") {
          ++p;
          auto n=or_expr(depth+1);
          require(p<ts.size()&&ts[p].kind==")", "E_QUERY_SYNTAX", "Unclosed group");
          ++p;
          return n;
        }
        require(ts[p].kind=="term", "E_QUERY_SYNTAX", "Unexpected operator");
        auto t=ts[p++];
        QueryNode n;
        n.value=t.text;
        n.op=t.quoted?"phrase":"term";
        if(t.has_unquoted_prefix("intitle:")) {
          n.field="title";
          n.value.erase(0, 8);
        } else if(t.has_unquoted_prefix("inbody:")) {
          n.field="body";
          n.value.erase(0, 7);
        }
        require(!n.value.empty(), "E_QUERY_SYNTAX", "Empty field term");
        if(!t.quoted && n.value.find_first_of("+#:./\\")!=std::string::npos)n.op="literal";
        return count(std::move(n));
      }
      QueryNode and_expr(int depth) {
        std::vector<QueryNode> children;
        children.push_back(atom(depth));
        while(p<ts.size()&&ts[p].kind!="OR"&&ts[p].kind!=")") {
          if(ts[p].kind=="AND")++p;
          require(children.size()<64, "E_QUERY_LIMIT", "Too many conjunction terms");
          children.push_back(atom(depth));
        }
        if(children.size()==1)return std::move(children[0]);
        return count( {
          "and", "any", "", std::move(children)
        });
      }
      QueryNode or_expr(int depth) {
        std::vector<QueryNode> children;
        children.push_back(and_expr(depth));
        while(p<ts.size()&&ts[p].kind=="OR") {
          ++p;
          require(children.size()<64, "E_QUERY_LIMIT", "Too many disjunction branches");
          children.push_back(and_expr(depth));
        }
        if(children.size()==1)return std::move(children[0]);
        return count( {
          "or", "any", "", std::move(children)
        });
      }
      public: explicit Parser(const std::vector<Token>& tokens):ts(tokens) {
      }
      QueryNode run() {
        auto r=or_expr(0);
        require(p==ts.size(), "E_QUERY_SYNTAX", "Unexpected closing group");
        return r;
      }
    };
    QueryNode from_json(const Json& j, int depth, int& nodes) {
      require(depth<=16&&++nodes<=256, "E_QUERY_LIMIT", "AST complexity limit exceeded");
      auto op=required_string(j, "op", 16);
      QueryNode n;
      n.op=op;
      if(op=="and"||op=="or") {
        j.allowed( {
          "op", "children"
        });
        auto a=j.at("children");
        require(a.is_array()&&a.size()>=2&&a.size()<=64, "E_SCHEMA", "AND/OR require 2..64 children");
        for(std::size_t i=0; i<a.size(); ++i)n.children.push_back(from_json(a.at(i), depth+1, nodes));
      }else if(op=="not") {
        j.allowed( {
          "op", "child"
        });
        n.children.push_back(from_json(j.at("child"), depth+1, nodes));
      } else {
        require(op=="term"||op=="phrase"||op=="literal", "E_QUERY_SYNTAX", "Unknown query operator");
        j.allowed( {
          "op", "field", "value"
        });
        n.value=required_string(j, "value", 4096);
        n.field=j.has("field")?j.at("field").str():"any";
        require(n.field=="any"||n.field=="title"||n.field=="body"||n.field=="url", "E_CAPABILITY_UNAVAILABLE", "Unsupported field in bootstrap query", 422);
      }
      return n;
    }
    bool word_char(unsigned char c) {
      return (c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_';
    }
    bool contains(std::string_view hay, const QueryNode& n) {
      auto h=ascii_lower(hay), v=ascii_lower(n.value);
      std::size_t pos=0;
      bool word=n.op=="term"&&std::all_of(v.begin(), v.end(), [](unsigned char c) {
        return word_char(c);
      });
      while((pos=h.find(v, pos))!=std::string::npos) {
        if(!word||(pos==0||!word_char(static_cast<unsigned char>(h[pos-1])))) {
          auto e=pos+v.size();
          if(!word||e==h.size()||!word_char(static_cast<unsigned char>(h[e])))return true;
        }
        ++pos;
      }
      return false;
    }
    bool eval(const QueryNode& n, std::string_view title, std::string_view body, std::string_view url) {
      if(n.op=="and")return std::all_of(n.children.begin(), n.children.end(), [&](const auto& c) {
        return eval(c, title, body, url);
      });
      if(n.op=="or")return std::any_of(n.children.begin(), n.children.end(), [&](const auto& c) {
        return eval(c, title, body, url);
      });
      if(n.op=="not")return !eval(n.children.at(0), title, body, url);
      if(n.field=="title")return contains(title, n);
      if(n.field=="body")return contains(body, n);
      if(n.field=="url")return contains(url, n);
      return contains(title, n)||contains(body, n);
    }
    std::optional<std::string> fts(const QueryNode& n) {
      if(n.op=="not"||n.field=="url")return std::nullopt;
      if(n.op=="and"||n.op=="or") {
        std::string s="(";
        bool first=true;
        for(const auto& c:n.children) {
          auto q=fts(c);
          if(!q)return std::nullopt;
          if(!first)s+=n.op=="and"?" AND ":" OR ";
          first=false;
          s+=*q;
        }
        return s+")";
      }
      auto value=ascii_lower(n.value);
      if(n.op!="term"||!std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return word_char(c);
      }))return std::nullopt;
      // Phrases use substring semantics even when they contain only one word.
      // Term candidates require the matching ASCII-only index projection.
      auto prefix=n.field=="title"?"title : ":(n.field=="body"?"body : ":"");
      return std::string(prefix)+"\""+value+"\"";
    }
  }
  Query parse_query(const Json& input) {
    Query q;
    if(input.is_object()) {
      int nodes=0;
      q.original=input.canonical();
      q.root=from_json(input, 0, nodes);
      return q;
    }
    require(input.is_string(), "E_SCHEMA", "Query must be a string or AST object");
    q.original=input.str();
    require(q.original.size()<=8192&&q.original.find('\0')==std::string::npos, "E_QUERY_LIMIT", "Invalid query length/content");
    auto ts=lex(q.original);
    bool directives=false;
    for(const auto&t:ts)if(t.has_unquoted_prefix("site:")||t.has_unquoted_prefix("filetype:"))directives=true;
    if(directives) {
      require(std::none_of(ts.begin(), ts.end(), [](const Token&t) {
        return t.kind=="OR"||t.kind=="("||t.kind==")"||t.kind=="NOT";
      }), "E_QUERY_SYNTAX", "site/filetype directives currently require a top-level positive conjunction");
      Parser(ts).run();
      // Validate conjunction syntax before removing global directives.
      std::vector<Token> kept;
      for(const auto&t:ts) {
        if(t.has_unquoted_prefix("site:")) {
          auto h=metadata_host("https://"+t.text.substr(5));
          q.hosts.push_back(h);
        } else if(t.has_unquoted_prefix("filetype:")) {
          auto v=ascii_lower(t.text.substr(9));
          require(v=="html"||v=="txt", "E_CAPABILITY_UNAVAILABLE", "Only html/txt are indexed in this build", 422);
          q.types.push_back(v=="html"?"text/html":"text/plain");
        } else if(t.kind!="AND") kept.push_back(t);
      }
      require(!kept.empty(), "E_QUERY_SYNTAX", "A text expression is required in addition to filters");
      ts=std::move(kept);
    }
    q.root=Parser(ts).run();
    return q;
  }
  bool matches(const Query& q, std::string_view title, std::string_view body, std::string_view url, std::string_view host, std::string_view type) {
    for(const auto& h:q.hosts)if(host!=h && !(host.size()>h.size()&&host.ends_with("."+h)))return false;
    for(const auto&t:q.types)if(type!=t)return false;
    return eval(q.root, title, body, url);
  }
  std::optional<std::string> fts_candidates(const Query& q) {
    return fts(q.root);
  }
  std::string fts_index_text(std::string_view text) {
    auto out=ascii_lower(text);
    for(char&c:out)if(!word_char(static_cast<unsigned char>(c)))c=' ';
    return out;
  }
  int score_match(const QueryNode& q, std::string_view title, std::string_view body) {
    if(q.op=="not")return 0;
    if(q.op=="and"||q.op=="or") {
      int n=0;
      for(const auto&c:q.children)n+=score_match(c, title, body);
      return n;
    }
    return (contains(title, q)?5:0)+(contains(body, q)?1:0);
  }
}
