#include "bzs/query.hpp"
#include "bzs/error.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <cctype>
#include <set>
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
    bool ascii_alpha(unsigned char c) {
      return c>='a'&&c<='z';
    }
    bool all_word(std::string_view v) {
      return !v.empty()&&std::all_of(v.begin(), v.end(), [](unsigned char c) {
        return word_char(c);
      });
    }
    // Boundary rules on the ASCII-lowered haystack. CJK text has no word delimiters,
    // so boundaries are only enforced next to ASCII word characters.
    bool boundary_ok(std::string_view h, std::size_t pos, std::size_t len, const QueryNode& n, std::string_view v) {
      // Quoted phrases retain the published raw-substring semantics, including
      // a one-word phrase inside a longer word. Only ASCII case is folded.
      if(n.op=="phrase")return true;
      auto e=pos+len;
      unsigned char prev=pos>0?static_cast<unsigned char>(h[pos-1]):static_cast<unsigned char>(0);
      unsigned char next=e<h.size()?static_cast<unsigned char>(h[e]):static_cast<unsigned char>(0);
      auto first=static_cast<unsigned char>(v.front()), last=static_cast<unsigned char>(v.back());
      if(n.op=="term") {
        if(!all_word(v))return true;
        return !(prev&&word_char(prev))&&!(next&&word_char(next));
      }
      if(word_char(first)&&prev&&word_char(prev))return false;
      // literal: exact identifiers, versions and language names (B7).
      if(!next)return true;
      if(word_char(last)) {
        if(word_char(next))return false;
        if((next=='.'||next==':')&&e+1<h.size()&&word_char(static_cast<unsigned char>(h[e+1])))return false;
        return true;
      }
      if(last=='+'||last=='#')return !(ascii_alpha(next)||next=='_'||next=='+'||next=='#');
      return true;
    }
    // Each field is ASCII-lowered at most once per document, on first use.
    // Previously every leaf re-lowered the whole body (up to 1 MiB) per visit.
    struct LoweredFields {
      std::string_view title, body, url;
      mutable std::optional<std::string> lower_title, lower_body, lower_url;
      static std::string_view get(std::string_view raw, std::optional<std::string>& cache) {
        if(!cache)cache=ascii_lower(raw);
        return *cache;
      }
    };
    bool eval(const QueryNode& n, const LoweredFields& f) {
      if(n.op=="and")return std::all_of(n.children.begin(), n.children.end(), [&](const auto& c) {
        return eval(c, f);
      });
      if(n.op=="or")return std::any_of(n.children.begin(), n.children.end(), [&](const auto& c) {
        return eval(c, f);
      });
      if(n.op=="not")return !eval(n.children.at(0), f);
      auto in=[&](std::string_view hay_lower) {
        return !leaf_matches(hay_lower, n, 1).empty();
      };
      const auto title=[&] { return LoweredFields::get(f.title, f.lower_title); };
      const auto body=[&] { return LoweredFields::get(f.body, f.lower_body); };
      if(n.field=="title")return in(title());
      if(n.field=="body")return in(body());
      if(n.field=="url")return in(LoweredFields::get(f.url, f.lower_url));
      return in(title())||in(body());
    }
    bool filters_match(const Query& q,std::string_view host,std::string_view type) {
      for(const auto& h:q.hosts)if(host!=h&&!(host.size()>h.size()&&host.ends_with("."+h)))return false;
      for(const auto& t:q.types)if(type!=t)return false;
      return true;
    }
    int score_fields(const QueryNode& q,const LoweredFields& fields) {
      int score=0;
      // Preserve legacy ranking: every positive leaf scores both fields even
      // when its matching scope is title/body/URL; NOT subtrees add nothing.
      for(const auto* leaf:positive_leaves(q)) {
        score+=4*static_cast<int>(leaf_matches(LoweredFields::get(fields.title,fields.lower_title),*leaf,5).size());
        score+=static_cast<int>(leaf_matches(LoweredFields::get(fields.body,fields.lower_body),*leaf,10).size());
      }
      return score;
    }
    std::string fts_quote(std::string_view v) {
      std::string s="\"";
      for(char c:v) {
        if(c=='"')s+="\"\"";
        else s+=c;
      }
      return s+"\"";
    }
    // Returns an FTS5 expression whose matches are a superset of the exact matches,
    // or nothing when that cannot be guaranteed. Exact semantics are re-checked later.
    std::optional<std::string> fts(const QueryNode& n) {
      if(n.op=="not")return std::nullopt;
      if(n.op=="and") {
        std::vector<std::string> parts;
        for(const auto& c:n.children)if(auto q=fts(c))parts.push_back("("+*q+")");
        if(parts.empty())return std::nullopt;
        std::string s;
        for(std::size_t i=0; i<parts.size(); ++i)s+=(i?" AND ":"")+parts[i];
        return s;
      }
      if(n.op=="or") {
        std::string s;
        for(std::size_t i=0; i<n.children.size(); ++i) {
          auto q=fts(n.children[i]);
          if(!q)return std::nullopt;
          s+=(i?" OR ":"")+("("+*q+")");
        }
        return s;
      }
      auto value=ascii_lower(n.value);
      if(n.field=="url")return std::nullopt;
      // The bzs/2 tokenizer splits ASCII words on every non-[a-z0-9_]
      // character and gives every CJK character a position. These are the
      // only leaf shapes whose exact matches are guaranteed FTS candidates.
      // General Unicode substrings, phrases and symbol literals use the supplementary index.
      const auto prefix=n.field=="title"?"title : ":(n.field=="body"?"body : ":"");
      if((n.op=="term"&&all_word(value))||is_cjk_text(value))return std::string(prefix)+fts_quote(value);
      // Mixed leaves cannot require an ASCII word boundary (CPython教程 also
      // contains Python教程). Contiguous CJK runs do have exact FTS positions.
      // Requiring a bounded subset is a safe superset; grams handle the rest.
      const auto runs=query_cjk_runs(value);
      if(runs.empty())return std::nullopt;
      std::string expression;
      for(const auto& run:runs) {if(!expression.empty())expression+=" AND ";expression+=fts_quote(run);}
      return std::string(prefix)+"("+expression+")";
    }
    // This is a PRIORITY hint, not a necessary filter. Token-aligned matches
    // are usually more selective than grams in long documents. Raw substrings
    // inside words (and unsupported OR branches) must still use the fallback.
    std::optional<std::string> priority_fts(const QueryNode& n, bool& specialized) {
      if(n.op=="not")return std::nullopt;
      if(n.op=="and"||n.op=="or") {
        std::string out;
        for(const auto& child:n.children) {
          auto part=priority_fts(child,specialized);
          if(!part)continue;
          const auto separator=out.empty()?"":n.op=="and"?" AND ":" OR ";
          if(out.size()+part->size()+std::string_view(separator).size()+2>32768)continue;
          out+=separator;out+='(';out+=*part;out+=')';
        }
        return out.empty()?std::nullopt:std::optional<std::string>(out);
      }
      if(n.field=="url")return std::nullopt; // content_fts has no URL column
      const auto value=ascii_lower(n.value);
      if((n.op=="term"&&all_word(value))||is_cjk_text(value))return fts(n);
      // ASCII fragments are enough for cafe-with-accent, identifiers and
      // punctuated phrases; quoting delegates token positions to bzs/2.
      if(!std::any_of(value.begin(),value.end(),[](unsigned char c) {return word_char(c);}))return fts(n);
      specialized=true;
      const auto prefix=n.field=="title"?"title : ":n.field=="body"?"body : ":"";
      return std::string(prefix)+fts_quote(value);
    }
    constexpr std::size_t substring_expression_limit=32768;
    std::optional<std::string> substring_fts(const QueryNode& n) {
      // Negating a superset can exclude a true match. Keep NOT unconstrained.
      if(n.op=="not")return std::nullopt;
      if(n.op=="and"||n.op=="or") {
        const bool conjunction=n.op=="and";
        const auto separator=conjunction?" AND ":" OR ";
        std::string result;
        for(const auto& child:n.children) {
          auto candidate=substring_fts(child);
          if(!candidate) {
            if(!conjunction)return std::nullopt;
            continue;
          }
          const auto part="("+*candidate+")";
          const auto separator_size=result.empty()?std::size_t{0}:std::string_view(separator).size();
          if(result.size()+separator_size+part.size()>substring_expression_limit) {
            // Omitting an AND constraint widens safely. Omitting an OR branch
            // would lose its exact matches, so the whole OR must be unconstrained.
            if(!conjunction)return std::nullopt;
            continue;
          }
          if(!result.empty())result+=separator;
          result+=part;
        }
        if(result.empty())return std::nullopt;
        return result;
      }
      if(n.op!="term"&&n.op!="phrase"&&n.op!="literal")return std::nullopt;
      const auto grams=query_substring_grams(n.value);
      if(grams.empty())return std::nullopt;
      const auto in_field=[&](char field) {
        std::string result="((";
        for(std::size_t i=0;i<grams.size();++i) {
          if(i)result+=" AND ";
          result+=fts_quote(std::string(1,field)+grams[i]);
        }
        result+=") OR "+fts_quote(std::string(1,field)+"overflow")+")";
        return result;
      };
      if(n.field=="title")return in_field('t');
      if(n.field=="body")return in_field('b');
      if(n.field=="url")return in_field('r');
      if(n.field!="any")return std::nullopt;
      // A leaf must match entirely in title or entirely in body. URL is not
      // included in the published `any` field semantics.
      return "("+in_field('t')+" OR "+in_field('b')+")";
    }
    void collect_leaves(const QueryNode& n, std::vector<const QueryNode*>& out) {
      if(n.op=="not")return;
      if(n.op=="and"||n.op=="or") {
        for(const auto& c:n.children)collect_leaves(c, out);
        return;
      }
      out.push_back(&n);
    }
  }
  std::vector<std::pair<std::size_t, std::size_t>> leaf_matches(std::string_view hay_lower, const QueryNode& n, std::size_t limit) {
    std::vector<std::pair<std::size_t, std::size_t>> out;
    auto v=ascii_lower(n.value);
    if(v.empty()||limit==0)return out;
    std::size_t pos=0;
    while(out.size()<limit&&(pos=hay_lower.find(v, pos))!=std::string_view::npos) {
      if(boundary_ok(hay_lower, pos, v.size(), n, v)) {
        out.emplace_back(pos, pos+v.size());
        pos+=v.size();
      } else ++pos;
    }
    return out;
  }
  std::vector<const QueryNode*> positive_leaves(const QueryNode& root) {
    std::vector<const QueryNode*> out;
    collect_leaves(root, out);
    return out;
  }
  bool is_refused_filter_operator_name(const std::string& name) {
    static const std::set<std::string> refused{
      "after","before","since","until","lang","language","region","loc",
      "inurl","intext","inanchor","allintitle","allinbody","allinurl","allintext",
      "ext","containsexact","source"};
    return refused.count(name)>0;
  }
  void require_no_unmodelled_filter_operators(const std::string& query) {
    // Reuse the lexer's own tokens so quoting, escapes, the leading-NOT sign
    // and parenthesis grouping are classified exactly as the parser sees them.
    for(const auto& t:lex(query)) {
      const auto colon=t.text.find(':');
      if(colon==std::string::npos||colon==0||colon>16)continue;
      if(!is_refused_filter_operator_name(ascii_lower(t.text.substr(0,colon))))continue;
      // Only an entirely unquoted "name:" prefix is an operator invocation;
      // a fully quoted token ("after:x") is a literal search.
      if(t.has_unquoted_prefix(t.text.substr(0,colon+1)))
        throw Error("E_QUERY_SYNTAX","unmodelled filter operator in query is refused for web search (modelled: site:, filetype:, intitle:, inbody:)",400);
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
    if(!filters_match(q,host,type))return false;
    const LoweredFields fields{title, body, url, std::nullopt, std::nullopt, std::nullopt};
    return eval(q.root, fields);
  }
  std::optional<int> match_score(const Query& q,std::string_view title,std::string_view body,std::string_view url,std::string_view host,std::string_view type) {
    if(!filters_match(q,host,type))return std::nullopt;
    const LoweredFields fields{title,body,url,std::nullopt,std::nullopt,std::nullopt};
    if(!eval(q.root,fields))return std::nullopt;
    return score_fields(q.root,fields);
  }
  std::optional<std::string> fts_candidates(const Query& q) {
    return fts(q.root);
  }
  std::optional<std::string> priority_fts_candidates(const Query& q) {
    bool specialized=false;
    auto hint=priority_fts(q.root,specialized);
    return specialized?hint:std::nullopt;
  }
  std::optional<std::string> substring_candidates(const Query& q) {
    return substring_fts(q.root);
  }
  std::string fts_index_text(std::string_view text) {
    auto out=ascii_lower(text);
    for(char&c:out)if(!word_char(static_cast<unsigned char>(c)))c=' ';
    return out;
  }
  int score_match(const QueryNode& q, std::string_view title, std::string_view body) {
    // Used by substring candidates and fallback scans: title hits weigh more, body hits are capped.
    const LoweredFields fields{title,body,{},std::nullopt,std::nullopt,std::nullopt};
    return score_fields(q,fields);
  }
}
