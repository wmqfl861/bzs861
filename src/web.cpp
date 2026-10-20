#include "bzs/web.hpp"
#include "bzs/publication.hpp"
#include "bzs/contracts.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <chrono>
#include <map>
#include <thread>
#include <set>
#include <tuple>
namespace bzs {
  namespace {
    bool tracking_param(std::string_view k) {
      static const std::set<std::string, std::less<>> exact= {
        "gclid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "spm", "ved", "ei", "usg"
      };
      return k.starts_with("utm_")||exact.contains(k);
    }
    // Identity comparison for the existing ordinary HTTP client. This is not
    // automatic-site admission: escaped separators remain distinct, while URI
    // requoting and dot resolution can identify its actual response URL.
    std::string read_identity(std::string_view input) {
      auto uri=normalize_iri(input);check_public_url(uri);
      const auto start=uri.find("://")+3;auto end=uri.find_first_of("/?#",start);if(end==std::string::npos)end=uri.size();
      auto authority=ascii_lower(std::string_view(uri).substr(start,end-start));
      if(const auto colon=authority.find(':');colon!=std::string::npos) {
        const auto port=std::stoll(authority.substr(colon+1));
        authority=authority.substr(0,colon)+(((uri.starts_with("https://")&&port==443)||(uri.starts_with("http://")&&port==80))?"":":"+std::to_string(port));
      }
      auto rest=uri.substr(end,uri.find('#',end)-end);if(rest.empty()||rest.front()=='?')rest='/'+rest;
      const auto query=rest.find('?');
      const auto component=[](std::string_view value,bool is_query) {
        static constexpr char hex[]="0123456789ABCDEF";
        const auto digit=[](char c)->int { if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1; };
        std::string out;
        for(std::size_t i=0;i<value.size();++i) {
          if(value[i]!='%') { out+=value[i];continue; }
          require(i+2<value.size()&&digit(value[i+1])>=0&&digit(value[i+2])>=0,"E_URL","Malformed read URL escape");
          const auto c=static_cast<char>(digit(value[i+1])*16+digit(value[i+2]));i+=2;
          const std::string_view safe=is_query?"-._~!$'()*,/:?@":"-._~!$&'()*,=:;@";
          if((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||safe.find(c)!=std::string_view::npos)out+=c;
          else { out+='%';out+=hex[static_cast<unsigned char>(c)>>4];out+=hex[static_cast<unsigned char>(c)&15]; }
        }
        return out;
      };
      auto path=component(std::string_view(rest).substr(0,query),false);std::vector<std::string> parts;
      for(std::size_t from=1;from<=path.size();) {
        auto to=path.find('/',from);if(to==std::string::npos)to=path.size();const auto part=path.substr(from,to-from);
        if(part=="..") { if(!parts.empty())parts.pop_back();if(to==path.size())parts.emplace_back(); }
        else if(part==".") { if(to==path.size())parts.emplace_back(); }
        else parts.push_back(part);
        from=to+1;
      }
      path.clear();for(const auto& part:parts) { path+='/';path+=part; }if(path.empty())path="/";
      auto result=uri.substr(0,start)+authority+path;
      if(query!=std::string::npos&&query+1<rest.size())result+='?'+component(std::string_view(rest).substr(query+1),true);
      return result;
    }
    std::vector<std::string> split(std::string_view s, char sep) {
      std::vector<std::string> out;
      std::size_t start=0;
      while(start<=s.size()) {
        auto p=s.find(sep, start);
        if(p==std::string_view::npos)p=s.size();
        if(p>start)out.emplace_back(s.substr(start, p-start));
        start=p+1;
      }
      return out;
    }
    bool ipv4_literal(std::string_view host, unsigned (&o)[4]) {
      if(host.empty()||host.front()=='.'||host.back()=='.'||host.find("..")!=std::string_view::npos)return false;
      auto parts=split(host, '.');
      if(parts.size()!=4)return false;
      for(std::size_t i=0; i<4; ++i) {
        if(parts[i].empty()||parts[i].size()>3||(parts[i].size()>1&&parts[i].front()=='0')||!std::all_of(parts[i].begin(), parts[i].end(), [](char c) {
          return c>='0'&&c<='9';
        }))return false;
        o[i]=static_cast<unsigned>(std::stoul(parts[i]));
        if(o[i]>255)return false;
      }
      return true;
    }
    std::string status_code(const std::string& status) {
      static const std::map<std::string, std::pair<std::string, int>> m= {
        {"blocked_by_policy", {"E_URL_BLOCKED", 403}}, {"challenge", {"E_ACCESS_RESTRICTED", 403}},
        {"login_required", {"E_ACCESS_RESTRICTED", 403}}, {"http_error", {"E_FETCH_HTTP", 502}},
        {"unsupported_content", {"E_UNSUPPORTED_CONTENT", 415}}, {"too_large", {"E_CONTENT_TOO_LARGE", 413}},
        {"timeout", {"E_FETCH_TIMEOUT", 504}}, {"network_error", {"E_FETCH_NETWORK", 502}},
        {"empty_content", {"E_PARSE_FAILED", 422}}, {"invalid_url", {"E_URL", 400}}
      };
      auto it=m.find(status);
      return it==m.end()?"E_FETCH":it->second.first;
    }
    int status_http(const std::string& status) {
      if(status=="blocked_by_policy"||status=="challenge"||status=="login_required")return 403;
      if(status=="unsupported_content")return 415;
      if(status=="too_large")return 413;
      if(status=="timeout")return 504;
      if(status=="empty_content")return 422;
      if(status=="invalid_url")return 400;
      return 502;
    }
    std::string opt_string(const Json& j, const std::string& k) {
      return j.has(k)&&j.at(k).is_string()?j.at(k).str():"";
    }
    std::int64_t opt_int(const Json& j, const std::string& k, std::int64_t fallback) {
      return j.has(k)&&j.at(k).is_integer()?j.at(k).num():fallback;
    }
  }
  std::string publication_url_identity(std::string_view url) { return read_identity(url); }
  std::string dedup_key(std::string_view url) {
    std::size_t p=0;
    if(url.starts_with("https://"))p=8;
    else if(url.starts_with("http://"))p=7;
    else return {};
    auto end=url.find_first_of("/?#", p);
    auto authority=ascii_lower(url.substr(p, end==std::string_view::npos?url.size()-p:end-p));
    if(authority.empty()||authority.find('@')!=std::string::npos)return {};
    if(p==7&&authority.ends_with(":80"))authority.resize(authority.size()-3);
    else if(p==8&&authority.ends_with(":443"))authority.resize(authority.size()-4);
    if(authority.starts_with("www."))authority.erase(0, 4);
    std::string rest=end==std::string_view::npos?"":std::string(url.substr(end));
    if(auto hash=rest.find('#'); hash!=std::string::npos)rest.resize(hash);
    std::string path=rest, query;
    if(auto q=rest.find('?'); q!=std::string::npos) {
      path=rest.substr(0, q);
      query=rest.substr(q+1);
    }
    while(path.size()>1&&path.back()=='/')path.pop_back();
    if(path=="/")path.clear();
    std::vector<std::string> kept;
    for(auto& kv:split(query, '&')) {
      auto key=kv.substr(0, kv.find('='));
      if(!tracking_param(ascii_lower(key)))kept.push_back(kv);
    }
    std::sort(kept.begin(), kept.end());
    std::string out=authority+path;
    for(std::size_t i=0; i<kept.size(); ++i)out+=(i?"&":"?")+kept[i];
    return out;
  }
  std::string normalize_iri(std::string_view url) {
    require(url.size()<=4096&&valid_utf8(url)&&url.find('\0')==std::string_view::npos, "E_URL", "Invalid URL");
    auto scheme_end=url.find("://");
    require(scheme_end!=std::string_view::npos, "E_URL", "Only http/https URLs can be read");
    auto authority_end=url.find_first_of("/?#", scheme_end+3);
    auto head=url.substr(0, authority_end==std::string_view::npos?url.size():authority_end);
    require(std::all_of(head.begin(), head.end(), [](char c) {
      return static_cast<unsigned char>(c)<0x80;
    }), "E_URL", "Non-ASCII host names must be punycode (xn--...); the MCP tools convert them automatically");
    std::string out(head);
    if(authority_end!=std::string_view::npos) {
      static constexpr char hex[]="0123456789ABCDEF";
      for(auto ch:url.substr(authority_end)) {
        auto b=static_cast<unsigned char>(ch);
        if(b>=0x80||b==' ') {
          out+='%';
          out+=hex[b>>4];
          out+=hex[b&15];
        } else out+=ch;
      }
    }
    require(out.size()<=4096, "E_URL", "URL too long after encoding");
    return out;
  }
  void check_public_url(std::string_view url) {
    auto host=metadata_host(url);
    require(host!="localhost"&&!host.ends_with(".localhost")&&!host.ends_with(".local")&&!host.ends_with(".internal")&&host!="home.arpa"&&!host.ends_with(".home.arpa"), "E_URL_BLOCKED", "Local and internal hostnames are not fetched", 403);
    unsigned o[4];
    if(ipv4_literal(host, o)) {
      bool blocked=o[0]==0||o[0]==10||o[0]==127||o[0]>=224||(o[0]==100&&o[1]>=64&&o[1]<=127)||(o[0]==169&&o[1]==254)||
        (o[0]==172&&o[1]>=16&&o[1]<=31)||(o[0]==192&&o[1]==168)||(o[0]==192&&o[1]==0&&o[2]==0)||(o[0]==198&&(o[1]==18||o[1]==19));
      require(!blocked, "E_URL_BLOCKED", "Private, loopback and link-local addresses are not fetched", 403);
      return;
    }
    // Numeric host forms (2130706433, 0x7f.1, 017700000001) resolve to IPs; refuse them outright.
    bool numeric=std::all_of(host.begin(), host.end(), [](char c) {
      return (c>='0'&&c<='9')||c=='.'||c=='x'||(c>='a'&&c<='f');
    })&&std::any_of(host.begin(), host.end(), [](char c) {
      return c>='0'&&c<='9';
    })&&(host.starts_with("0")||std::all_of(host.begin(), host.end(), [](char c) {
      return (c>='0'&&c<='9')||c=='.';
    }));
    require(!numeric, "E_URL_BLOCKED", "Numeric host forms are not fetched", 403);
  }
  void SourceSpec::require_review(std::int64_t now) const {
    require(enabled, "E_SOURCE_REVIEW", "Source is disabled: "+id, 409);
    try {
      (void)validate_contract("source-manifest", contract, now);
      auto review=contract.at("review");
      require(review.has("cache_policy")&&review.at("cache_policy").str()=="allowed_with_limits"&&
              review.has("redistribution_policy")&&(review.at("redistribution_policy").str()=="links_only"||
              review.at("redistribution_policy").str()=="allowed_with_limits"),
              "E_SOURCE_REVIEW", "Source requires explicit cache and redistribution review", 409);
    } catch(const Error& e) {
      throw Error("E_SOURCE_REVIEW", "Source "+id+": "+e.what(), 409);
    }
  }
  bool SourceSpec::reviewed(std::int64_t now) const {
    try { require_review(now); return true; } catch(const Error&) { return false; }
  }
  SourceRegistry::SourceRegistry(const Json& manifest) {
    try {
    manifest.allowed( {
      "version", "notes", "sources"
    });
    require(manifest.at("version").num()==1, "E_CONFIG", "Unsupported source manifest version", 500);
    auto list=manifest.at("sources");
    require(list.is_array()&&list.size()<=200, "E_CONFIG", "sources must be an array (<=200)", 500);
    std::set<std::string> seen;
    std::map<std::string,std::tuple<std::int64_t,std::int64_t,std::int64_t>> budgets;
    for(std::size_t i=0; i<list.size(); ++i) {
      auto j=list.at(i);
      j.allowed( {
        "id", "provider", "index_family", "capability", "kind", "enabled", "default", "weight", "min_interval_ms", "daily_cap",
        "status", "params", "notes", "languages", "terms_url", "checked_at", "cooldown_blocked_ms", "cooldown_rate_ms", "cooldown_error_ms", "contract"
      });
      SourceSpec s;
      s.id=required_string(j, "id", 64);
      require_identifier(s.id);
      require(seen.insert(s.id).second, "E_CONFIG", "Duplicate source id: "+s.id, 500);
      s.provider=required_string(j, "provider", 64);
      s.family=required_string(j, "index_family", 64);
      s.capability=required_string(j, "capability", 32);
      s.kind=required_string(j, "kind", 16);
      require(s.kind=="scrape"||s.kind=="api", "E_CONFIG", "kind must be scrape or api: "+s.id, 500);
      s.enabled=j.has("enabled")&&j.at("enabled").flag();
      s.by_default=j.has("default")&&j.at("default").flag();
      s.weight=int_option(j, "weight", 100, 1, 1000);
      s.min_interval_ms=int_option(j, "min_interval_ms", 1000, 0, 3600000);
      s.daily_cap=int_option(j, "daily_cap", 500, 0, 1000000);
      s.status=j.has("status")?required_string(j, "status", 32):"unverified";
      s.cooldown_blocked_ms=int_option(j, "cooldown_blocked_ms", s.cooldown_blocked_ms, 0, 86400000);
      s.cooldown_rate_ms=int_option(j, "cooldown_rate_ms", s.cooldown_rate_ms, 0, 86400000);
      s.cooldown_error_ms=int_option(j, "cooldown_error_ms", s.cooldown_error_ms, 0, 86400000);
      s.contract=Json::parse(j.at("contract").dump());
      auto structural=Json::parse(s.contract.dump());
      structural.set("enabled",false);
      (void)validate_contract("source-manifest",structural,now_ms());
      const auto& c=s.contract;
      auto params=j.at("params");
      auto capability=s.capability=="encyclopedia"?std::string("web"):(s.capability=="papers"?std::string("scholar"):s.capability);
      require(c.at("id").str()==s.id&&c.at("provider_id").str()==s.provider&&c.at("index_family").str()==s.family&&
              c.at("capability").str()==capability&&c.at("enabled").flag()==s.enabled&&
              c.at("adapter_id").str()==required_string(params,"adapter",64)&&
              c.at("endpoint").str()==required_string(params,"base_url",4096),
              "E_SOURCE_CONFIG","Source contract identity differs from runtime source: "+s.id,500);
      auto limits=c.at("limits");
      require(limits.at("min_interval_ms").num()==s.min_interval_ms&&limits.at("request_quota").num()==s.daily_cap&&
              limits.at("quota_window_seconds").num()==86400,"E_SOURCE_CONFIG","S1 quota differs from its source contract",500);
      s.budget_key=limits.at("shared_budget_key").str();
      require_identifier(s.budget_key);
      auto shape=std::make_tuple(limits.at("max_concurrency").num(),s.min_interval_ms,s.daily_cap);
      require(!budgets.contains(s.budget_key)||budgets.at(s.budget_key)==shape,"E_SOURCE_CONFIG","Shared budget limits differ",500);
      budgets[s.budget_key]=shape;
      s.status=c.at("review").at("status").str();
      specs_.push_back(s);
    }
    } catch(const Error& e) {
      throw Error("E_SOURCE_CONFIG",e.what(),500);
    }
  }
  SourceRegistry SourceRegistry::load(const std::filesystem::path& file) {
    auto raw=read_file(file,1024*1024);
    auto registry=SourceRegistry(Json::parse(raw));
    registry.manifest_digest_=sha256(raw);
    return registry;
  }
  const SourceSpec* SourceRegistry::find(const std::string& id) const {
    for(const auto& s:specs_)if(s.id==id)return &s;
    return nullptr;
  }
  Json fuse(const std::vector<SourceHits>& results, const SourceRegistry& registry, std::size_t limit, std::int64_t k) {
    require(k>=1&&k<=1000000,"E_SCHEMA","RRF k must be in 1..1000000");
    struct Agg {
      std::string url, title, snippet;
      std::int64_t best_rank=1<<30;
      std::size_t first_seen=0;
      std::map<std::string, std::pair<std::int64_t, std::int64_t>> family;  // family -> (best rank, weight)
      std::vector<std::pair<std::string, std::int64_t>> sources;
    };
    std::map<std::string, Agg> groups;
    std::size_t order=0;
    for(const auto& r:results) {
      const auto* spec=registry.find(r.source_id);
      if(!spec)continue;
      const auto& fam=spec->family;
      auto weight=spec->weight;
      for(const auto& h:r.hits) {
        if(h.rank<1||h.rank>1000000)continue;
        auto key=dedup_key(h.url);
        if(key.empty())continue;
        auto [it, fresh]=groups.try_emplace(key);
        auto& g=it->second;
        if(fresh)g.first_seen=order++;
        bool better=h.rank<g.best_rank;
        bool upgrade=g.url.starts_with("http://")&&h.url.starts_with("https://");
        if(better||g.url.empty()||upgrade)g.url=h.url;
        if(better||g.title.empty()) {
          if(!h.title.empty())g.title=h.title;
        }
        if((better&&!h.snippet.empty())||g.snippet.empty())g.snippet=h.snippet.empty()?g.snippet:h.snippet;
        g.best_rank=std::min(g.best_rank, h.rank);
        auto& f=g.family[fam];
        if(f.first==0||h.rank<f.first)f.first=h.rank;
        f.second=std::max(f.second, weight);
        g.sources.emplace_back(r.source_id, h.rank);
      }
    }
    struct Scored {
      const Agg* g;
      double score;
    };
    std::vector<Scored> ranked;
    for(const auto& [key, g]:groups) {
      double score=0;
      for(const auto& [fam, f]:g.family)score+=(static_cast<double>(f.second)/100.0)/static_cast<double>(k+f.first);
      ranked.push_back( {
        &g, score
      });
    }
    std::sort(ranked.begin(), ranked.end(), [](const Scored& a, const Scored& b) {
      if(a.score!=b.score)return a.score>b.score;
      if(a.g->best_rank!=b.g->best_rank)return a.g->best_rank<b.g->best_rank;
      return a.g->first_seen<b.g->first_seen;
    });
    Json out=Json::array();
    for(std::size_t i=0; i<ranked.size()&&i<limit; ++i) {
      const auto& g=*ranked[i].g;
      Json j=Json::object();
      j.set("rank", static_cast<std::int64_t>(i+1));
      j.set("url", g.url);
      j.set("title", g.title);
      j.set("snippet", g.snippet);
      j.set("snippet_kind", "search_engine_snippet");
      Json src=Json::array();
      auto sorted=g.sources;
      std::sort(sorted.begin(), sorted.end(), [](const auto& a, const auto& b) {
        return a.second<b.second;
      });
      for(const auto& [id, rank]:sorted) {
        Json s=Json::object();
        s.set("source_id", id);
        s.set("rank", rank);
        src.push(s);
      }
      j.set("sources", src);
      j.set("index_families", static_cast<std::int64_t>(g.family.size()));
      j.set("score_micro", static_cast<std::int64_t>(ranked[i].score*1e6));
      out.push(j);
    }
    return out;
  }
  WebService::WebService(Store& store, WorkerPort& worker, SourceRegistry registry,SiteRegistry sites):store_(store), worker_(worker), registry_(std::move(registry)), sites_(std::move(sites)) {
    site_publication_generation_=sites_.all().empty()?store_.site_publication_generation():store_.activate_site_publication(sites_);
  }
  namespace {
    constexpr std::int64_t search_cache_ttl_ms=10*60*1000, partial_cache_ttl_ms=2*60*1000;
    constexpr std::size_t search_cache_entries=256, search_cache_bytes=16*1024*1024, search_cache_entry_bytes=512*1024;
  }
  std::optional<Json> WebService::cache_get(const std::string& key) {
    std::lock_guard lock(cache_mutex_);
    auto it=cache_.find(key);
    if(it==cache_.end())return std::nullopt;
    if(store_.now()<it->second.stored_at||store_.now()-it->second.stored_at>it->second.ttl_ms) {
      cache_.erase(it);
      return std::nullopt;
    }
    auto value=Json::parse(it->second.body, 24*1024*1024);
    value.set("from_cache", true);
    value.set("cached_at_ms", it->second.stored_at);
    Json usage=Json::object();
    usage.set("external_requests", static_cast<std::int64_t>(0));
    usage.set("model_calls", static_cast<std::int64_t>(0));
    value.set("usage", usage);
    return value;
  }
  void WebService::cache_put(const std::string& key, const Json& value, bool partial) {
    std::lock_guard lock(cache_mutex_);
    auto now=store_.now();
    for(auto it=cache_.begin(); it!=cache_.end();)it=now-it->second.stored_at>it->second.ttl_ms?cache_.erase(it):std::next(it);
    auto body=value.dump();
    if(body.size()>search_cache_entry_bytes)return;
    auto bytes=body.size();
    for(const auto& [existing_key,entry]:cache_)bytes+=existing_key.size()+entry.body.size();
    while(!cache_.empty()&&(cache_.size()>=search_cache_entries||bytes+key.size()>search_cache_bytes)) {
      auto oldest=std::min_element(cache_.begin(), cache_.end(), [](const auto& a, const auto& b) {
        return a.second.stored_at<b.second.stored_at;
      });
      bytes-=oldest->first.size()+oldest->second.body.size();
      cache_.erase(oldest);
    }
    cache_[key]= {
      now, partial?partial_cache_ttl_ms:search_cache_ttl_ms, std::move(body)
    };
  }
  std::size_t WebService::enabled_sources() const {
    return static_cast<std::size_t>(std::count_if(registry_.all().begin(), registry_.all().end(), [&](const SourceSpec& s) {
      return s.reviewed(store_.now());
    }));
  }
  Json WebService::sources() {
    auto states=store_.source_states();
    Json list=Json::array();
    for(const auto& s:registry_.all()) {
      Json j=Json::object();
      j.set("source_id", s.id);
      j.set("provider", s.provider);
      j.set("index_family", s.family);
      j.set("capability", s.capability);
      j.set("kind", s.kind);
      j.set("enabled", s.reviewed(store_.now()));
      j.set("configured_enabled", s.enabled);
      j.set("shared_budget_key", s.budget_key);
      j.set("default", s.by_default);
      j.set("verification", s.status);
      j.set("min_interval_ms", s.min_interval_ms);
      j.set("daily_cap", s.daily_cap);
      j.set("state", states.has(s.budget_key)?states.at(s.budget_key):Json::object());
      list.push(j);
    }
    Json out=Json::object();
    out.set("sources", list);
    return out;
  }
  Json WebService::search(const std::string& owner, const Json& args) {
    args.allowed( {
      "query", "sources", "category", "limit", "per_source", "language", "timeout_ms", "refresh"
    });
    auto query=required_string(args, "query", 1000);
    require(valid_utf8(query), "E_UTF8", "Query must be UTF-8");
    auto limit=int_option(args, "limit", 10, 1, 50);
    auto per_source=int_option(args, "per_source", 10, 1, 30);
    auto timeout=int_option(args, "timeout_ms", 8000, 1000, 30000);
    auto category=args.has("category")?required_string(args, "category", 32):std::string("web");
    std::string language;
    if(args.has("language")) {
      language=required_string(args, "language", 16);
      require(std::all_of(language.begin(), language.end(), [](char c) {
        return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||c=='-'||c=='_';
      }), "E_SCHEMA", "Invalid language tag");
    }
    std::vector<const SourceSpec*> selected;
    if(args.has("sources")) {
      auto list=args.at("sources");
      require(list.is_array()&&list.size()>=1&&list.size()<=20, "E_SCHEMA", "sources must be a non-empty array (<=20)");
      for(std::size_t i=0; i<list.size(); ++i) {
        auto id=list.at(i).str();
        const auto* s=registry_.find(id);
        require(s&&s->enabled, "E_SCHEMA", "Unknown or disabled source: "+id);
        if(std::find(selected.begin(), selected.end(), s)==selected.end())selected.push_back(s);
      }
    } else {
      for(const auto& s:registry_.all()) {
        if(!s.enabled)continue;
        bool take=category=="web"?(s.by_default&&(s.capability=="web"||s.capability=="encyclopedia")):
          (category=="all"?s.by_default:s.capability==category);
        if(take)selected.push_back(&s);
      }
      require(!selected.empty(), "E_SCHEMA", "No enabled sources for category: "+category);
    }
    require(selected.size()<=20,"E_SCHEMA","Default source selection exceeds 20 sources");
    for(const auto* source:selected)source->require_review(store_.now());
    std::vector<std::string> selected_ids;
    for(const auto* s:selected)selected_ids.push_back(s->id);
    std::sort(selected_ids.begin(), selected_ids.end());
    Json key_parts=Json::object();
    key_parts.set("owner",owner);
    key_parts.set("query",query);
    key_parts.set("language",language);
    key_parts.set("limit",limit);
    key_parts.set("per_source",per_source);
    Json key_sources=Json::array();
    for(const auto& id:selected_ids)key_sources.push(Json::string(id));
    key_parts.set("sources",key_sources);
    auto cache_key=key_parts.dump();
    bool refresh=args.has("refresh")&&args.at("refresh").flag();
    if(!refresh)if(auto hit=cache_get(cache_key))return *hit;
    Json report=Json::array();
    Json ids=Json::array();
    std::vector<const SourceSpec*> admitted;
    std::vector<std::pair<const SourceSpec*, std::string>> decisions;
    std::int64_t wait=0;
    for(const auto* s:selected) {
      auto reason=store_.source_admit(s->budget_key, s->min_interval_ms, s->daily_cap);
      if(reason=="min_interval"&&s->min_interval_ms<=3000)wait=std::max(wait, s->min_interval_ms);
      decisions.emplace_back(s, reason);
    }
    if(wait>0) {
      // Politeness gaps still open: wait once (bounded) instead of dropping those sources.
      std::this_thread::sleep_for(std::chrono::milliseconds(wait));
      for(auto& [s, reason]:decisions)
        if(reason=="min_interval"&&s->min_interval_ms<=3000)reason=store_.source_admit(s->budget_key, s->min_interval_ms, s->daily_cap);
    }
    for(const auto& [s, reason]:decisions) {
      if(reason.empty()) {
        admitted.push_back(s);
        ids.push(Json::string(s->id));
        continue;
      }
      Json r=Json::object();
      r.set("source_id", s->id);
      r.set("status", "skipped");
      r.set("reason", reason);
      r.set("hits", static_cast<std::int64_t>(0));
      report.push(r);
    }
    std::vector<SourceHits> collected;
    bool partial=report.size()>0;
    if(!admitted.empty()) {
      Json params=Json::object();
      params.set("query", query);
      params.set("limit", per_source);
      params.set("timeout_ms", timeout);
      if(!language.empty())params.set("language", language);
      params.set("sources", ids);
      params.set("manifest_sha256",registry_.manifest_digest());
      auto resp=worker_.call("search.multi", params, static_cast<int>(timeout+4000));
      require(resp.is_object()&&resp.has("results")&&resp.at("results").is_object(),"E_WORKER_PROTOCOL","Worker search response requires results object",502);
      auto results=resp.at("results");
      for(const auto* s:admitted) {
        Json r=Json::object();
        r.set("source_id", s->id);
        r.set("verification", s->status);
        if(!results.has(s->id)) {
          r.set("status", "error");
          r.set("error", "worker returned no result");
          r.set("hits", static_cast<std::int64_t>(0));
          store_.source_report(s->budget_key, "error", "missing", 0);
          partial=true;
          report.push(r);
          continue;
        }
        auto x=results.at(s->id);
        auto status=opt_string(x, "status");
        auto error=opt_string(x, "error");
        if(status.empty())status="error";
        if((status=="ok"||status=="empty")&&(!x.has("hits")||!x.at("hits").is_array()||
           (status=="empty"&&x.at("hits").is_array()&&x.at("hits").size()!=0))) {
          status="error";
          error="E_WORKER_PROTOCOL: successful source response requires a consistent hits array";
        }
        SourceHits sh;
        sh.source_id=s->id;
        if(status=="ok"&&x.has("hits")&&x.at("hits").is_array()) {
          auto hits=x.at("hits");
          for(std::size_t i=0; i<hits.size()&&i<static_cast<std::size_t>(per_source); ++i) {
            auto h=hits.at(i);
            auto url=opt_string(h, "url");
            auto rank=opt_int(h,"rank",0);
            if(url.empty()||rank<1||rank>1000000)continue;
            try { url=normalize_iri(url);check_public_url(url); } catch(const Error&) { continue; }
            if(dedup_key(url).empty())continue;
            auto title=opt_string(h,"title"),snippet=opt_string(h,"snippet");
            if(!valid_utf8(title)||!valid_utf8(snippet))continue;
            sh.hits.push_back({url,utf8_prefix(title,512),utf8_prefix(snippet,1024),rank});
          }
        }
        std::int64_t cooldown=0;
        if(status=="blocked"||status=="challenge")cooldown=s->cooldown_blocked_ms;
        else if(status=="rate_limited")cooldown=std::max(s->cooldown_rate_ms, std::clamp(opt_int(x, "retry_after_ms", 0),std::int64_t{0},(std::int64_t{1}<<62)-1));
        else if(status=="parse_error")cooldown=s->cooldown_error_ms*3;
        else if(status!="ok"&&status!="empty")cooldown=s->cooldown_error_ms;
        store_.source_report(s->budget_key, status.empty()?"error":status, error, cooldown);
        if(status!="ok"&&status!="empty")partial=true;
        r.set("status", status.empty()?"error":status);
        r.set("hits", static_cast<std::int64_t>(sh.hits.size()));
        r.set("elapsed_ms", opt_int(x, "elapsed_ms", 0));
        if(x.has("http_status")&&x.at("http_status").is_integer())r.set("http_status", x.at("http_status").num());
        if(!error.empty())r.set("error", utf8_prefix(error, 300));
        report.push(r);
        collected.push_back(std::move(sh));
      }
    }
    auto merged=fuse(collected, registry_, static_cast<std::size_t>(limit));
    Json filters=Json::object();
    try {
      // Verify site: constraints on result hosts instead of trusting upstream (QRY-04 lite).
      auto parsed=parse_query(Json::string(query));
      if(!parsed.hosts.empty()) {
        Json kept=Json::array();
        std::int64_t removed=0;
        for(std::size_t i=0; i<merged.size(); ++i) {
          auto r=merged.at(i);
          std::string host;
          try {
            host=metadata_host(r.at("url").str());
          }catch(const Error&) {
          }
          bool ok=std::any_of(parsed.hosts.begin(), parsed.hosts.end(), [&](const std::string& h) {
            return host==h||(host.size()>h.size()&&host.ends_with("."+h));
          });
          if(ok) {
            r.set("rank", static_cast<std::int64_t>(kept.size()+1));
            kept.push(r);
          } else ++removed;
        }
        merged=kept;
        Json sites=Json::array();
        for(const auto& h:parsed.hosts)sites.push(Json::string(h));
        filters.set("site", sites);
        filters.set("removed_unverified", removed);
      }
    }catch(const Error&) {
      // Engine syntax our parser does not model; results are passed through unverified.
    }
    Json usage=Json::object();
    usage.set("external_requests", static_cast<std::int64_t>(admitted.size()));
    usage.set("model_calls", static_cast<std::int64_t>(0));
    Json out=Json::object();
    out.set("query", query);
    out.set("results", merged);
    out.set("result_count", static_cast<std::int64_t>(merged.size()));
    out.set("sources", report);
    out.set("filters", filters);
    out.set("coverage", "external_search_snippets_unread");
    out.set("partial", partial);
    out.set("retrieved_at_ms", store_.now());
    out.set("usage", usage);
    out.set("from_cache", false);
    // Cache only answers that reached at least one source; an all-failed search is not worth replaying.
    bool answered=false;
    for(std::size_t i=0; i<report.size(); ++i) {
      auto st=report.at(i).at("status").str();
      answered=answered||st=="ok"||st=="empty";
    }
    if(answered)cache_put(cache_key, out, partial);
    return out;
  }
  Json WebService::window(const std::string& owner, const Json& snap, std::int64_t start, std::int64_t max_bytes, bool from_cache, std::int64_t fetched_at) {
    auto meta=snap.at("meta");
    Json out=Json::object();
    out.set("snapshot_id", snap.at("snapshot_id"));
    out.set("document_id", snap.at("document_id"));
    out.set("url", snap.at("url"));
    out.set("final_url", meta.has("final_url")?meta.at("final_url"):snap.at("url"));
    out.set("title", snap.at("title"));
    out.set("revision", snap.at("revision"));
    out.set("fetched_at_ms", fetched_at);
    out.set("from_cache", from_cache);
    out.set("indexed", snap.at("indexed"));
    out.set("site_publication",snap.at("site_publication"));
    out.set("content", store_.range(owner, snap.at("snapshot_id").str(), start, max_bytes));
    out.set("links_count", static_cast<std::int64_t>(snap.at("links").size()));
    out.set("metadata", meta.has("metadata")?meta.at("metadata"):Json::object());
    Json fetch=Json::object();
    for(const auto* k: {
      "http_status", "content_type", "charset", "extractor", "extract_status", "warnings", "redirects", "text_truncated"
    })if(meta.has(k))fetch.set(k, meta.at(k));
    out.set("fetch", fetch);
    out.set("content_trust", "untrusted_web_content");
    return out;
  }
  Json WebService::read(const std::string& owner, const Json& args) {
    args.allowed( {
      "url", "freshness", "max_age_s", "max_bytes", "start_byte", "timeout_ms"
    });
    auto url=normalize_iri(required_string(args, "url", 4096));
    check_public_url(url);
    auto freshness=args.has("freshness")?required_string(args, "freshness", 16):std::string("cache_first");
    require(freshness=="cache_first"||freshness=="refresh"||freshness=="cache_only", "E_SCHEMA", "freshness must be cache_first, refresh or cache_only");
    auto max_age=int_option(args, "max_age_s", 7*86400, 0, 365LL*86400);
    auto max_bytes=int_option(args, "max_bytes", 20000, 1, 262144);
    auto start=int_option(args, "start_byte", 0, 0, 1LL<<40);
    auto timeout=int_option(args, "timeout_ms", 20000, 1000, 60000);
    const auto publication_fence=store_.publication_fence(owner,url);
    const auto deletion_generation=publication_fence.at("generation").num();
    auto cached=store_.cached(owner, url);
    if(freshness=="cache_only") {
      require(cached.has_value(), "E_NOT_CACHED", "No stored snapshot for this URL; use freshness=cache_first or refresh", 404);
      return window(owner, *cached, start, max_bytes, true, cached->at("fetched_at_ms").num());
    }
    if(freshness=="cache_first"&&cached&&store_.now()-cached->at("fetched_at_ms").num()<=max_age*1000)
      return window(owner, *cached, start, max_bytes, true, cached->at("fetched_at_ms").num());
    Json params=Json::object();
    params.set("url", url);
    params.set("timeout_ms", timeout);
    auto r=worker_.call("fetch.extract", params, static_cast<int>(timeout+5000));
    const auto redirect_observations=decode_redirect_observations(url,r);
    std::optional<Json> observation;
    const auto apply_observations=[&] {
      for(std::size_t i=0;i<redirect_observations.size();++i) {
        const auto observed=redirect_observations.at(i);
        store_.observe_web_publication(owner,url,observed.at("url").str(),observed.at("publication"),publication_fence);
      }
      if(observation)store_.observe_web_publication(owner,url,normalize_iri(r.at("final_url").str()),*observation,publication_fence);
    };
    ReadArtifact artifact;
    try { observation=decode_observation(url,r);artifact=decode_read(url,r); }
    catch(...) { apply_observations();throw; }
    artifact.meta.set("publication_fence",publication_fence);
    artifact.meta.set("page_publication_sequence",publication_fence.at("publication_sequence"));
    const auto policy=artifact.parsed.publication;
    const auto representation_policy=artifact.parsed.representation_publication.has("version")?artifact.parsed.representation_publication:policy;
    try {
      auto snap=store_.ingest_web(owner, artifact.document, std::move(artifact.parsed), artifact.meta, deletion_generation);
      return window(owner, snap, start, max_bytes, false, artifact.meta.at("fetched_at_ms").num());
    } catch(const Error& error) {
      if(error.code=="E_QUOTA") {
        apply_observations();
        if(restrictive_publication(policy))store_.observe_web_publication(owner,url,url,policy,publication_fence);
        if(restrictive_publication(representation_policy))store_.observe_web_publication(owner,url,artifact.meta.at("final_url").str(),representation_policy,publication_fence);
      }
      throw;
    }
  }
  Json WebService::decode_publication(const Json& record,const std::string& url,const std::string& final_url,const Json& redirects) {
    auto policy=validate_publication(record);
      for(const auto& site:sites_.all()) {
        bool matching=site.allows(url)||site.allows(final_url);
        for(std::size_t i=0;i<redirects.size();++i)matching=matching||site.allows(redirects.at(i).str());
        if(matching&&site.review.has("redistribution_policy")&&site.review.at("redistribution_policy").is_string()&&site.review.at("redistribution_policy").str()=="links_only") {
        policy.set("snippet_allowed",false);policy.set("snippet_text","");policy.set("snippet_title","");
        auto diagnostics=policy.at("diagnostics");bool present=false;
        for(std::size_t i=0;i<diagnostics.size();++i)present=present||diagnostics.at(i).str()=="site_links_only";
        if(!present)diagnostics.push(Json::string("site_links_only"));
        }
      }
    return policy;
  }
  std::optional<Json> WebService::decode_observation(const std::string& url,const Json& response) {
    Json record;
    if(response.has("publication_observation"))record=response.at("publication_observation");
    else if(response.has("status")&&response.at("status").str()=="empty_content"&&response.has("publication")) {
      // An empty body can still carry applicable HTML meta directives. Validate
      // the complete artifact before deriving a restrictive observation; keep
      // the original empty-content error and never manufacture a successful read.
      auto empty=decode_read(url,response,true);
      const auto& final_policy=empty.parsed.representation_publication.has("version")?empty.parsed.representation_publication:empty.parsed.publication;
      if(!restrictive_publication(final_policy))return std::nullopt;
      record=Json::parse(final_policy.dump(),8*1024*1024);record.set("snippet_text","");record.set("snippet_title","");
    } else return std::nullopt;
    require(response.has("http_status")&&response.at("http_status").is_integer()&&response.at("http_status").num()>=100&&response.at("http_status").num()<=599,
            "E_WORKER_PROTOCOL","Publication observation lacks a real HTTP response",502);
    const auto final_url=normalize_iri(required_string(response,"final_url",4096));check_public_url(final_url);
    const auto redirects=response.has("redirects")?response.at("redirects"):Json::array();
    require(redirects.is_array()&&redirects.size()<=20,"E_WORKER_PROTOCOL","Invalid observed redirect chain",502);
    for(std::size_t i=0;i<redirects.size();++i)check_public_url(normalize_iri(redirects.at(i).str()));
    // This record describes the actual final HTTP response. Redirect response
    // rules are carried separately and cannot be attributed to its target.
    auto policy=decode_publication(record,final_url,final_url,Json::array());
    require(restrictive_publication(policy)&&policy.at("snippet_text").str().empty()&&policy.at("snippet_title").str().empty(),"E_WORKER_PROTOCOL","Failed fetch cannot publish an allowing policy",502);
    return policy;
  }
  Json WebService::decode_redirect_observations(const std::string& url,const Json& response) {
    auto out=Json::array();
    if(!response.has("redirect_publication_observations"))return out;
    const auto records=response.at("redirect_publication_observations");
    require(records.is_array()&&records.size()<=20,"E_WORKER_PROTOCOL","Invalid redirect publication observations",502);
    if(records.size()==0)return out;
    require(response.has("redirects")&&response.at("redirects").is_array()&&response.at("redirects").size()<=20,"E_WORKER_PROTOCOL","Observed redirects lack a bounded chain",502);
    const auto actual_final=read_identity(required_string(response,"final_url",4096));
    std::vector<std::string> prefix={read_identity(url)};
    const auto redirects=response.at("redirects");
    // The final advertised target may have been refused before HTTP. Restrict
    // policy identity to public hops through the last actual response; an unsafe
    // unvisited tail must not erase an earlier applicable response directive.
    for(std::size_t i=0;i<redirects.size();++i) {
      try { prefix.push_back(read_identity(redirects.at(i).str())); }catch(const Error&) { break; }
    }
    std::size_t reached=prefix.size();
    for(std::size_t i=0;i<prefix.size();++i)if(prefix[i]==actual_final)reached=i;
    require(reached<prefix.size(),"E_WORKER_PROTOCOL","Final response is not bound to the observed redirect chain",502);
    std::set<std::string> chain(prefix.begin(),prefix.begin()+static_cast<std::ptrdiff_t>(reached+1));
    require(records.size()<=redirects.size()+1,"E_WORKER_PROTOCOL","More redirect observations than responses",502);
    require(response.has("usage")&&response.at("usage").at("external_requests").num()>=static_cast<std::int64_t>(records.size()),"E_WORKER_PROTOCOL","Redirect observations exceed known HTTP usage",502);
    for(std::size_t i=0;i<records.size();++i) {
      const auto record=records.at(i);record.allowed({"url","http_status","publication"});
      const auto hop=normalize_iri(required_string(record,"url",4096));
      require(chain.contains(read_identity(hop)),"E_WORKER_PROTOCOL","Redirect observation is not bound to the returned chain",502);
      const auto http=record.at("http_status").num();
      require(http==301||http==302||http==303||http==307||http==308||(http>=200&&http<300&&http!=206),"E_WORKER_PROTOCOL","Invalid observed redirect response status",502);
      const auto policy=decode_publication(record.at("publication"),hop,hop,Json::array());
      require(restrictive_publication(policy)&&policy.at("snippet_text").str().empty()&&policy.at("snippet_title").str().empty(),"E_WORKER_PROTOCOL","Redirect observation must only restrict publication",502);
      auto item=Json::object();item.set("url",hop);item.set("http_status",http);item.set("publication",policy);out.push(item);
    }
    return out;
  }
  WebService::ReadArtifact WebService::decode_read(const std::string& url, const Json& r,bool allow_empty) {
    require(r.is_object(),"E_WORKER_PROTOCOL","Worker read response must be an object",502);
    auto status=opt_string(r, "status");
    if(status!="ok"&&!(allow_empty&&status=="empty_content")) {
      std::string detail=opt_string(r, "error");
      if(r.has("http_status")&&r.at("http_status").is_integer()) {
        auto code="HTTP "+std::to_string(r.at("http_status").num());
        if(detail.find(code)==std::string::npos)detail+=detail.empty()?code:" ("+code+")";
      }
      throw Error(status_code(status), "Fetch "+(status.empty()?std::string("failed"):status)+(detail.empty()?"":": "+utf8_prefix(detail, 300)), status_http(status));
    }
    for(const auto* key:{"html","text","content_type","title","extractor","final_url"})
      require(r.has(key)&&r.at(key).is_string()&&valid_utf8(r.at(key).str())&&r.at(key).str().find('\0')==std::string::npos,"E_WORKER_PROTOCOL",std::string("Invalid worker field: ")+key,502);
    require(r.has("index_allowed")&&r.at("index_allowed").is_bool(),"E_WORKER_PROTOCOL","Worker must explicitly report index_allowed",502);
    require(r.at("html").str().size()<=8*1024*1024&&r.at("text").str().size()<=2*1024*1024&&
            r.at("title").str().size()<=4096&&r.at("content_type").str().size()<=128&&
            !r.at("extractor").str().empty()&&r.at("extractor").str().size()<=128&&
            (r.at("content_type").str()=="text/html"||r.at("content_type").str()=="text/plain"),
            "E_WORKER_PROTOCOL","Worker content exceeds protocol bounds",502);
    // Compatibility responses may omit the versioned publication record, but
    // an explicitly incomplete or unsuccessful HTTP response never qualifies
    // as a complete refresh capable of lifting a stored restriction.
    if(r.has("http_status")||r.has("publication"))
      require(r.has("http_status")&&r.at("http_status").is_integer()&&r.at("http_status").num()>=200&&r.at("http_status").num()<300&&r.at("http_status").num()!=206,"E_WORKER_PROTOCOL","Readable publication needs a complete 2xx response",502);
    bool incomplete=false;
    if(r.has("warnings")) {
      const auto warnings=r.at("warnings");require(warnings.is_array()&&warnings.size()<=64,"E_WORKER_PROTOCOL","Invalid read warnings",502);
      for(std::size_t i=0;i<warnings.size();++i) {
        require(warnings.at(i).is_string()&&warnings.at(i).str().size()<=256,"E_WORKER_PROTOCOL","Invalid read warning",502);
        incomplete=incomplete||warnings.at(i).str()=="download_truncated";
      }
    }
    require(!incomplete||r.has("publication"),"E_WORKER_PROTOCOL","Incomplete HTTP body needs an explicit non-publishing policy",502);
    std::string final_url;
    try {
      final_url=normalize_iri(r.at("final_url").str());
      check_public_url(final_url);
      require(r.has("redirects")&&r.at("redirects").is_array()&&r.at("redirects").size()<=20,
              "E_WORKER_PROTOCOL","Worker redirect chain is invalid",502);
      for(std::size_t i=0;i<r.at("redirects").size();++i)check_public_url(normalize_iri(r.at("redirects").at(i).str()));
    } catch(const Error& e) { throw Error("E_WORKER_PROTOCOL",std::string("Invalid worker URL: ")+e.what(),502); }
    SourceDocument doc {url,r.at("html").str(),r.at("content_type").str(),r.at("title").str()};
    ParsedDocument parsed;
    parsed.title=opt_string(r, "title");
    parsed.text=opt_string(r, "text");
    parsed.content_type=doc.content_type;
    parsed.parser_version=opt_string(r, "extractor").empty()?"worker":opt_string(r, "extractor");
    parsed.index_allowed=r.at("index_allowed").flag();
    if(r.has("publication")) {
      parsed.publication=decode_publication(r.at("publication"),url,final_url,r.at("redirects"));
      require(parsed.publication.at("index_allowed").flag()==parsed.index_allowed,"E_WORKER_PROTOCOL","Publication index decision disagrees with page",502);
      if(status=="ok"&&r.has("publication_observation")) {
        const auto observation=decode_publication(r.at("publication_observation"),url,final_url,r.at("redirects"));
        require(publication_covers(parsed.publication,observation),"E_WORKER_PROTOCOL","Read artifact would relax its response's restrictive observation",502);
      }
      const auto final_policy=decode_publication(r.at("publication"),final_url,final_url,Json::array());
      if(incomplete)require(!final_policy.at("index_allowed").flag()&&!final_policy.at("snippet_allowed").flag()&&!final_policy.at("follow_allowed").flag(),"E_WORKER_PROTOCOL","Incomplete HTTP body cannot publish an allowing policy",502);
      const auto redirects=decode_redirect_observations(url,r);
      for(std::size_t i=0;i<redirects.size();++i)parsed.publication=meet_publication(parsed.publication,redirects.at(i).at("publication"));
      if(parsed.publication.canonical()!=final_policy.canonical())parsed.representation_publication=final_policy;
      parsed.index_allowed=parsed.publication.at("index_allowed").flag();
    }
    if(r.has("links")&&r.at("links").is_array()) {
      auto links=r.at("links");
      for(std::size_t i=0; i<links.size()&&i<512; ++i)if(links.at(i).is_string()&&links.at(i).str().size()<=4096)parsed.links.push(links.at(i));
    }
    Json meta=Json::object();
    auto fetched=store_.now();
    meta.set("fetched_at_ms", fetched);
    meta.set("final_url", final_url);
    meta.set("site_publication_generation",site_publication_generation_);
    for(const auto* k: {
      "http_status", "charset", "extractor", "extract_status", "warnings", "redirects", "metadata"
    })if(r.has(k))meta.set(k, r.at(k));
    meta.set("redirect_publication_observations",decode_redirect_observations(url,r));
    meta.set("content_type", doc.content_type);
    return {std::move(doc),std::move(parsed),std::move(meta)};
  }
  Json WebService::submit_reads(const std::string& owner,const Json& args) {
    args.allowed({"urls","idempotency_key","timeout_ms","per_page_timeout_ms","max_requests","max_bytes","max_attempts"});
    auto list=args.at("urls");
    require(list.is_array()&&list.size()>=1&&list.size()<=20,"E_SCHEMA","urls must contain 1..20 explicit URLs");
    std::vector<std::string> urls;
    for(std::size_t i=0;i<list.size();++i) {
      require(list.at(i).is_string()&&!list.at(i).str().empty()&&list.at(i).str().size()<=4096,
              "E_SCHEMA","Each URL must be a non-empty string of at most 4096 bytes");
      urls.push_back(list.at(i).str());
    }
    ReadManyOptions options;
    options.timeout_ms=int_option(args,"timeout_ms",600000,1000,3600000);
    options.per_page_timeout_ms=int_option(args,"per_page_timeout_ms",20000,1000,55000);
    // Only omission selects defaults: explicit zero or null never enlarges a budget.
    options.max_requests=args.has("max_requests")?int_option(args,"max_requests",0,1,1000):0;
    options.max_bytes=args.has("max_bytes")?int_option(args,"max_bytes",0,1,256LL*1024*1024):0;
    options.max_attempts=int_option(args,"max_attempts",1,1,5);
    return store_.read_many_submit(owner,urls,required_string(args,"idempotency_key",128),options);
  }
  void WebService::execute_read(const ReadLease& lease) {
    std::int64_t requests=-1,bytes=-1;std::optional<Json> observation;std::string observed_url;auto redirect_observations=Json::array();
    try {
      if(!store_.read_lease_live(lease)) {
        store_.fail_read(lease,"E_LEASE_EXPIRED",0,0);
        return;
      }
      const auto now=store_.now();
      if(now>=lease.deadline_ms) {
        store_.fail_read(lease,"E_DEADLINE",0,0);
        return;
      }
      const auto timeout=std::min(lease.timeout_ms,lease.deadline_ms-now);
      Json params=Json::object();
      params.set("url",lease.url);params.set("timeout_ms",timeout);
      params.set("max_requests",lease.request_grant);params.set("max_content_bytes",lease.byte_grant);
      params.set("attempt_id",lease.attempt_id);
      const auto response=worker_.call("fetch.extract",params,static_cast<int>(timeout+5000));
      require(response.is_object()&&response.has("status")&&response.at("status").is_string()&&
              response.has("usage")&&response.at("usage").is_object(),
              "E_WORKER_PROTOCOL","Bounded read response must report status and usage",502);
      auto usage=response.at("usage");
      require(usage.has("external_requests")&&usage.at("external_requests").is_integer()&&
              usage.has("content_bytes")&&usage.at("content_bytes").is_integer(),
              "E_WORKER_PROTOCOL","Read usage must be explicit integers",502);
      const auto used_requests=usage.at("external_requests").num(),used_bytes=usage.at("content_bytes").num();
      require(used_requests>=0&&used_requests<=lease.request_grant&&used_bytes>=0&&used_bytes<=lease.byte_grant&&
              (used_requests!=0||used_bytes==0)&&
              (response.at("status").str()!="ok"||(used_requests>0&&used_bytes>0)),
              "E_WORKER_PROTOCOL","Read usage exceeds the durable grant",502);
      requests=used_requests;bytes=used_bytes;
      redirect_observations=decode_redirect_observations(lease.url,response);
      observation=decode_observation(lease.url,response);if(observation)observed_url=normalize_iri(response.at("final_url").str());
      const auto status=response.at("status").str();
      if(status=="budget_exhausted"||status=="cancelled"||status=="attempt_replayed") {
        const auto code=opt_string(response,"error_code");
        require((status=="budget_exhausted"&&(code=="E_REQUEST_BUDGET"||code=="E_BYTE_BUDGET"||code=="E_CONTENT_LIMIT"))||
                (status=="cancelled"&&code=="E_CANCELLED")||(status=="attempt_replayed"&&code=="E_ATTEMPT_REPLAY"),
                "E_WORKER_PROTOCOL","Invalid bounded read stop reason",502);
        store_.fail_read(lease,code,requests,bytes,observation?&*observation:nullptr,observed_url,&redirect_observations);
        return;
      }
      auto artifact=decode_read(lease.url,response);
      const auto& final_policy=artifact.parsed.representation_publication.has("version")?artifact.parsed.representation_publication:artifact.parsed.publication;
      if(restrictive_publication(final_policy)) { observation=final_policy;observed_url=artifact.meta.at("final_url").str(); }
      if(artifact.parsed.representation_publication.has("version")&&restrictive_publication(artifact.parsed.publication)) {
        auto logical=Json::object();logical.set("url",lease.url);logical.set("publication",artifact.parsed.publication);redirect_observations.push(logical);
      }
      (void)store_.complete_read(lease,artifact.document,std::move(artifact.parsed),artifact.meta,requests,bytes);
    } catch(const Error& error) {
      store_.fail_read(lease,error.code,requests,bytes,observation?&*observation:nullptr,observed_url,&redirect_observations);
    } catch(...) {
      store_.fail_read(lease,"E_INTERNAL",requests,bytes);
    }
  }
  void WebService::cancel_read_attempt(const std::string& attempt_id) noexcept {
    if(attempt_id.empty())return;
    try {
      Json params=Json::object();params.set("attempt_id",attempt_id);
      (void)worker_.call("fetch.cancel",params,2000);
    } catch(...) {
      // Job control has already advanced the durable epoch. Worker cooperation
      // only shortens outstanding work and is never the publication fence.
    }
  }
}
