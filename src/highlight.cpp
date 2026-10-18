#include "bzs/highlight.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <set>
#include <tuple>
namespace bzs {
  namespace {
    struct Passage {
      std::size_t start=0, end=0;
      int score=0;
      std::size_t first_match=std::string::npos;
      std::set<std::size_t> terms;
    };
    bool sentence_end(std::string_view t, std::size_t i) {
      auto c=static_cast<unsigned char>(t[i]);
      if(c=='.'||c=='!'||c=='?'||c==';')return i+1>=t.size()||t[i+1]==' ';
      // 。！？；  (E3 80 82, EF BC 81, EF BC 9F, EF BC 9B)
      if(i>=2) {
        auto a=static_cast<unsigned char>(t[i-2]), b=static_cast<unsigned char>(t[i-1]);
        if(a==0xE3&&b==0x80&&c==0x82)return true;
        if(a==0xEF&&b==0xBC&&(c==0x81||c==0x9F||c==0x9B))return true;
      }
      return false;
    }
    std::vector<std::pair<std::size_t, std::size_t>> segments(std::string_view t, std::size_t max_bytes) {
      std::vector<std::pair<std::size_t, std::size_t>> out;
      std::size_t start=0;
      auto flush=[&](std::size_t end) {
        // Trim surrounding whitespace without breaking UTF-8.
        while(start<end&&(t[start]==' '||t[start]=='\n'||t[start]=='\t'))++start;
        auto e=end;
        while(e>start&&(t[e-1]==' '||t[e-1]=='\n'||t[e-1]=='\t'))--e;
        if(e>start)out.emplace_back(start, e);
        start=end;
      };
      for(std::size_t i=0; i<t.size(); ++i) {
        if(t[i]=='\n') {
          flush(i+1);
          continue;
        }
        if(i+1-start>=2*max_bytes&&sentence_end(t, i))flush(i+1);
        else if(i+1-start>=4*max_bytes&&utf8_boundary(t, i+1))flush(i+1);
      }
      flush(t.size());
      return out;
    }
    std::size_t back_to_boundary(std::string_view t, std::size_t p) {
      while(p>0&&!utf8_boundary(t, p))--p;
      return p;
    }
  }
  Json highlight(std::string_view text, const QueryNode& query, std::size_t max_passages, std::size_t max_bytes) {
    require(max_passages>=1&&max_passages<=16&&max_bytes>=4&&max_bytes<=65536,
            "E_SCHEMA","Invalid highlight limits");
    Json out=Json::object();
    Json list=Json::array();
    auto leaves=positive_leaves(query);
    auto lowered=ascii_lower(text);
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t>> matches;  // start, end, leaf
    for(std::size_t li=0; li<leaves.size(); ++li)
      for(auto [s, e]:leaf_matches(lowered, *leaves[li], 200))matches.emplace_back(s, e, li);
    std::sort(matches.begin(), matches.end());
    out.set("match_count", static_cast<std::int64_t>(matches.size()));
    if(matches.empty()) {
      auto prefix=utf8_prefix(text, max_bytes);
      out.set("snippet", prefix);
      out.set("snippet_kind", "document_prefix");
      out.set("highlights", list);
      return out;
    }
    std::vector<Passage> passages;
    for(auto [s, e]:segments(text, max_bytes)) {
      Passage p;
      p.start=s;
      p.end=e;
      passages.push_back(std::move(p));
    }
    std::size_t mi=0;
    for(auto& p:passages) {
      while(mi<matches.size()&&std::get<0>(matches[mi])<p.start)++mi;
      for(auto k=mi; k<matches.size()&&std::get<0>(matches[k])<p.end; ++k) {
        if(std::get<1>(matches[k])>p.end)continue;
        if(p.first_match==std::string::npos)p.first_match=std::get<0>(matches[k]);
        p.terms.insert(std::get<2>(matches[k]));
        ++p.score;
      }
      p.score=static_cast<int>(p.terms.size())*10+std::min(p.score, 9);
    }
    std::vector<Passage*> ranked;
    for(auto& p:passages)if(p.score>0)ranked.push_back(&p);
    if(ranked.empty()) {
      // A phrase can cross a newline or a segment boundary. Keep a window
      // around the actual match instead of returning an empty snippet.
      Passage p;
      p.start=std::get<0>(matches.front());
      p.end=std::get<1>(matches.front());
      p.first_match=p.start;
      p.score=1;
      passages.push_back(std::move(p));
      ranked.push_back(&passages.back());
    }
    std::stable_sort(ranked.begin(), ranked.end(), [](const Passage* a, const Passage* b) {
      return a->score>b->score;
    });
    if(ranked.size()>max_passages)ranked.resize(max_passages);
    std::sort(ranked.begin(), ranked.end(), [](const Passage* a, const Passage* b) {
      return a->start<b->start;
    });
    std::string snippet;
    for(auto* p:ranked) {
      auto s=p->start, e=p->end;
      if(e-s>max_bytes) {
        auto lead=std::min<std::size_t>(max_bytes/3, p->first_match-s);
        s=back_to_boundary(text, p->first_match-lead);
        e=back_to_boundary(text, std::min(p->end, s+max_bytes));
      }
      Json h=Json::object();
      h.set("start_byte", static_cast<std::int64_t>(s));
      h.set("end_byte", static_cast<std::int64_t>(e));
      h.set("text", std::string(text.substr(s, e-s)));
      Json terms=Json::array();
      std::set<std::size_t> visible_terms;
      for(const auto& [start,end,li]:matches)if(start>=s&&end<=e)visible_terms.insert(li);
      for(auto li:visible_terms)terms.push(Json::string(leaves[li]->value));
      h.set("terms", terms);
      h.set("match_truncated",visible_terms.empty());
      list.push(h);
      if(!snippet.empty())snippet+=" … ";
      snippet+=std::string(text.substr(s, e-s));
    }
    out.set("snippet", snippet);
    out.set("snippet_kind", "query_highlights");
    out.set("highlights", list);
    return out;
  }
}
