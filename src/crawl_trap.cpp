#include "bzs/crawl_trap.hpp"
#include "bzs/error.hpp"
#include "bzs/sites.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <chrono>
#include <map>
#include <set>
#include <utility>
#include <vector>

namespace bzs {
namespace {
  constexpr std::int64_t date_limit=16,date_run_limit=6,query_key_limit=8,query_pair_limit=16;
  constexpr std::int64_t permutation_limit=2,combination_limit=8,body_repeat_limit=3,body_min_bytes=256;
  constexpr std::int64_t job_state_limit=2000,global_state_limit=70000;
  void checked(int rc) {
    if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
    const auto primary=rc&0xff;
    if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
    if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
    throw Error("E_STORAGE","Crawl trap storage operation failed",500);
  }
  struct Sql {
    sqlite3_stmt* p=nullptr;
    Sql(sqlite3* db,const char* query) { checked(sqlite3_prepare_v2(db,query,-1,&p,nullptr)); }
    ~Sql() { sqlite3_finalize(p); }
    Sql(const Sql&)=delete;Sql& operator=(const Sql&)=delete;
    void bind(int i,const std::string& value) { checked(sqlite3_bind_text(p,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
    void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(p,i,value)); }
    bool step() { const auto rc=sqlite3_step(p);checked(rc);return rc==SQLITE_ROW; }
    std::int64_t num(int i)const { return sqlite3_column_int64(p,i); }
    std::string text(int i)const { const auto v=sqlite3_column_text(p,i);return v?std::string(reinterpret_cast<const char*>(v),static_cast<std::size_t>(sqlite3_column_bytes(p,i))):""; }
  };
  Json config() {
    auto out=Json::object();out.set("max_distinct_dates",date_limit);out.set("max_consecutive_date_steps",date_run_limit);
    out.set("max_filter_keys",query_key_limit);out.set("max_query_pairs",query_pair_limit);
    out.set("max_query_permutations",permutation_limit);out.set("max_filter_combinations",combination_limit);
    out.set("repeated_body_urls",body_repeat_limit);out.set("min_body_bytes",body_min_bytes);
    out.set("max_url_observations",job_state_limit);out.set("max_global_url_observations",global_state_limit);
    return out;
  }
  CrawlTrapDecision decision(const std::string& reason,const std::string& rule,const std::string& family,
                             std::int64_t observed,std::int64_t limit) {
    auto detail=Json::object();detail.set("version",crawl_trap_version);detail.set("rule",rule);detail.set("reason",reason);
    detail.set("family_sha256",family.empty()?Json():Json::string(family));detail.set("observed",observed);detail.set("limit",limit);
    return {reason,detail};
  }
  std::string key(const std::string& kind,const std::vector<std::string>& components) {
    auto value=Json::array();value.push(Json::string(kind));for(const auto& part:components)value.push(Json::string(part));
    return sha256(value.canonical());
  }
  int hex(char c) {
    if(c>='0'&&c<='9')return c-'0';
    if(c>='a'&&c<='f')return c-'a'+10;
    if(c>='A'&&c<='F')return c-'A'+10;
    return -1;
  }
  bool unreserved(char c) {
    return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='-'||c=='.'||c=='_'||c=='~';
  }
  std::string comparison_component(std::string_view input) {
    static constexpr char digits[]="0123456789ABCDEF";
    std::string out;out.reserve(input.size());
    for(std::size_t i=0;i<input.size();++i) {
      if(input[i]=='%'&&i+2<input.size()&&hex(input[i+1])>=0&&hex(input[i+2])>=0) {
        const auto value=static_cast<char>(hex(input[i+1])*16+hex(input[i+2]));
        if(unreserved(value))out.push_back(value);
        else { out.push_back('%');out.push_back(digits[hex(input[i+1])]);out.push_back(digits[hex(input[i+2])]); }
        i+=2;
      } else out.push_back(input[i]);
    }
    return out;
  }
  std::vector<std::string> split(const std::string& value,char separator) {
    std::vector<std::string> parts;std::size_t start=0;
    for(;;) {
      const auto end=value.find(separator,start);parts.push_back(value.substr(start,end==std::string::npos?end:end-start));
      if(end==std::string::npos)break;
      start=end+1;
    }
    return parts;
  }
  std::string join(const std::vector<std::string>& parts) {
    std::string out;for(std::size_t i=0;i<parts.size();++i) { if(i)out.push_back('/');out+=parts[i]; }return out;
  }
  bool decimal(std::string_view value,std::size_t length=0) {
    return !value.empty()&&(!length||value.size()==length)&&std::all_of(value.begin(),value.end(),[](char c){return c>='0'&&c<='9';});
  }
  int number(std::string_view value) { int out=0;for(char c:value)out=out*10+c-'0';return out; }
  bool valid_day(int year,unsigned month,unsigned day,std::int64_t& serial) {
    if(year<1||year>9999)return false;
    const std::chrono::year_month_day date{std::chrono::year{year},std::chrono::month{month},std::chrono::day{day}};
    if(!date.ok())return false;
    serial=std::chrono::sys_days{date}.time_since_epoch().count();return true;
  }
  struct Shape {
    std::string date_family,query_family,query_signature,ordering_signature,page_family;
    std::int64_t date_value=0,query_pairs=0,filter_keys=0;
  };
  using Pair=std::pair<std::string,std::string>;
  std::string query_key(std::vector<Pair> pairs) {
    // Only reorder distinct names. Repeated-name value order can change a
    // server's first/last-value semantics and is deliberately retained.
    std::stable_sort(pairs.begin(),pairs.end(),[](const auto& a,const auto& b){return a.first<b.first;});
    auto encoded=Json::array();for(const auto& [name,value]:pairs) { auto pair=Json::array();pair.push(Json::string(name));pair.push(Json::string(value));encoded.push(pair); }
    return encoded.canonical();
  }
  Shape classify(const std::string& url) {
    Shape out;
    const auto origin=map_origin(url),path=comparison_component(map_path(url));auto parts=split(path,'/');
    const bool calendar=std::any_of(parts.begin(),parts.end(),[](const auto& part) {
      const auto lower=ascii_lower(part);return lower=="calendar"||lower=="calendars"||lower=="archive"||lower=="archives"||lower=="events"||lower=="agenda";
    });
    for(std::size_t i=0;i<parts.size();++i) {
      const auto& part=parts[i];std::size_t count=0;std::string granularity;std::int64_t serial=0;
      if(part.size()==10&&(part[4]=='-'||part[4]=='_')&&part[7]==part[4]&&decimal(std::string_view(part).substr(0,4),4)&&decimal(std::string_view(part).substr(5,2),2)&&decimal(std::string_view(part).substr(8,2),2)) {
        if(valid_day(number(std::string_view(part).substr(0,4)),static_cast<unsigned>(number(std::string_view(part).substr(5,2))),static_cast<unsigned>(number(std::string_view(part).substr(8,2))),serial)) { count=1;granularity="day"; }
      } else if(decimal(part,4)&&i+2<parts.size()&&decimal(parts[i+1],2)&&decimal(parts[i+2],2)) {
        if(valid_day(number(part),static_cast<unsigned>(number(parts[i+1])),static_cast<unsigned>(number(parts[i+2])),serial)) { count=3;granularity="day"; }
      }
      if(count==0&&calendar) {
        int year=0,month=0;
        if(part.size()==7&&(part[4]=='-'||part[4]=='_')&&decimal(std::string_view(part).substr(0,4),4)&&decimal(std::string_view(part).substr(5,2),2)) {
          year=number(std::string_view(part).substr(0,4));month=number(std::string_view(part).substr(5,2));count=1;
        } else if(decimal(part,4)&&i+1<parts.size()&&decimal(parts[i+1],2)&&(i+2==parts.size()||!decimal(parts[i+2],2))) {
          year=number(part);month=number(parts[i+1]);count=2;
        }
        if(count&&year>=1&&month>=1&&month<=12) { serial=static_cast<std::int64_t>(year)*12+month-1;granularity="month"; }
        else count=0;
      }
      if(count) {
        auto family=parts;family.erase(family.begin()+static_cast<std::ptrdiff_t>(i),family.begin()+static_cast<std::ptrdiff_t>(i+count));
        family.insert(family.begin()+static_cast<std::ptrdiff_t>(i),"{date}");
        out.date_family=key("date",{origin,granularity,join(family)});out.date_value=serial;break;
      }
    }
    auto page_parts=parts;bool paginated=false;
    for(std::size_t i=1;i<page_parts.size();++i)if(page_parts[i-1]=="page"&&decimal(page_parts[i])&&page_parts[i].size()<=20) {
      page_parts[i]="{page}";paginated=true;
    }
    std::vector<Pair> pairs,filters;std::set<std::string> filter_names;
    const auto query=url.find('?');
    if(query!=std::string::npos&&query+1<url.size())for(const auto& pair:split(url.substr(query+1),'&')) {
      const auto equals=pair.find('=');
      const auto name=comparison_component(pair.substr(0,equals));
      // Missing '=' stays distinct from an explicitly empty value.
      const auto value=equals==std::string::npos?std::string("absent:"):"value:"+comparison_component(pair.substr(equals+1));
      pairs.emplace_back(name,value);
      if(name=="page"||name=="offset"||name=="start"||name=="cursor")paginated=true;
      else { filters.emplace_back(name,value);filter_names.insert(name); }
    }
    out.query_pairs=static_cast<std::int64_t>(pairs.size());out.filter_keys=static_cast<std::int64_t>(filter_names.size());
    if(pairs.size()>=2)out.ordering_signature=key("query-order",{origin,path,query_key(pairs)});
    if(filters.size()>=2) {
      out.query_family=key("query-combinations",{origin,join(page_parts)});
      out.query_signature=key("query-filters",{query_key(filters)});
    }
    if(paginated)out.page_family=key("pagination-body",{origin,join(page_parts),query_key(filters)});
    return out;
  }
  std::int64_t next_date_run(sqlite3* db,const std::string& job,const Shape& shape,const std::string& parent) {
    if(shape.date_family.empty()||parent.empty())return 0;
    Sql previous(db,"SELECT date_value,date_run FROM crawl_trap_urls WHERE job_id=? AND url=? AND date_family=?");
    previous.bind(1,job);previous.bind(2,parent);previous.bind(3,shape.date_family);
    if(previous.step()&&(shape.date_value==previous.num(0)+1||shape.date_value==previous.num(0)-1))return std::min(previous.num(1)+1,date_run_limit+1);
    return 0;
  }
  CrawlTrapDecision capacity(sqlite3* db,const std::string& job) {
    Sql counts(db,"SELECT (SELECT COUNT(*) FROM crawl_trap_urls WHERE job_id=?),(SELECT COUNT(*) FROM crawl_trap_urls)");counts.bind(1,job);counts.step();
    if(counts.num(0)>=job_state_limit)return decision("E_CRAWL_TRAP_STATE_LIMIT","retained_url_observations","",counts.num(0)+1,job_state_limit);
    if(counts.num(1)>=global_state_limit)return decision("E_CRAWL_TRAP_STATE_LIMIT","global_url_observations","",counts.num(1)+1,global_state_limit);
    return {};
  }
  std::int64_t repeated(sqlite3* db,const std::string& job,const std::string& family) {
    if(family.empty())return 0;
    Sql count(db,"SELECT COALESCE(MAX(n),0) FROM (SELECT COUNT(*) AS n FROM crawl_trap_urls WHERE job_id=? AND page_family=? AND body_hash!='' GROUP BY body_hash)");
    count.bind(1,job);count.bind(2,family);count.step();return count.num(0);
  }
  void partial(sqlite3* db,const std::string& job,const std::string& reason) {
    Sql update(db,"UPDATE map_jobs SET partial=1,stop_reason=CASE WHEN stop_reason='' THEN ? ELSE stop_reason END WHERE job_id=?");
    update.bind(1,reason);update.bind(2,job);update.step();
  }
}

void crawl_trap_admit(sqlite3* db,const std::string& job,const std::string& url,const std::string& parent,bool explicit_seed) {
  Sql existing(db,"SELECT 1 FROM crawl_trap_urls WHERE job_id=? AND url=?");existing.bind(1,job);existing.bind(2,url);
  if(existing.step()) {
    if(explicit_seed) { Sql seed(db,"UPDATE crawl_trap_urls SET explicit_seed=1 WHERE job_id=? AND url=?");seed.bind(1,job);seed.bind(2,url);seed.step(); }
    return;
  }
  const auto full=capacity(db,job);require(!full,"E_CRAWL_TRAP_STATE_LIMIT","Crawl trap observation quota reached",429);
  const auto shape=classify(url);const auto run=next_date_run(db,job,shape,parent);
  Sql insert(db,"INSERT INTO crawl_trap_urls(job_id,url,explicit_seed,parent,date_family,date_value,date_run,query_family,query_signature,ordering_signature,page_family) VALUES(?,?,?,?,?,?,?,?,?,?,?)");
  insert.bind(1,job);insert.bind(2,url);insert.bind(3,explicit_seed?1:0);insert.bind(4,parent);insert.bind(5,shape.date_family);
  insert.bind(6,shape.date_value);insert.bind(7,run);insert.bind(8,shape.query_family);insert.bind(9,shape.query_signature);
  insert.bind(10,shape.ordering_signature);insert.bind(11,shape.page_family);insert.step();
}

void crawl_trap_ensure(sqlite3* db,const std::string& job) {
  Sql old(db,"SELECT 1 FROM crawl_trap_policy WHERE job_id=?");old.bind(1,job);if(old.step())return;
  Sql insert(db,"INSERT INTO crawl_trap_policy(job_id,version,config_json) VALUES(?,?,?)");insert.bind(1,job);insert.bind(2,std::string(crawl_trap_version));insert.bind(3,config().canonical());insert.step();
  // Preserve admissions (including queued pages) and physical redirect hops
  // when an older live job first enters the new scheduler. No HTTP is issued.
  Sql resources(db,"SELECT r.position,r.url,r.fetch_url,r.parent,r.source,r.kind,r.state,s.canonical_text,s.id FROM map_resources r LEFT JOIN snapshots s ON s.id=r.snapshot_id WHERE r.job_id=? AND r.kind!='robots' ORDER BY r.position");
  resources.bind(1,job);bool incomplete=false;
  try { while(resources.step()) {
    const bool seed=resources.text(4)=="seed"&&resources.text(5)=="page";
    crawl_trap_admit(db,job,resources.text(1),resources.text(3),seed);
    std::string previous=resources.text(1);
    Sql hops(db,"SELECT url FROM map_hops WHERE job_id=? AND resource_position=? AND kind!='robots' ORDER BY position");hops.bind(1,job);hops.bind(2,resources.num(0));
    while(hops.step()) { const auto url=hops.text(0);crawl_trap_admit(db,job,url,previous,seed&&url==resources.text(1));previous=url; }
    if(resources.text(2)!=previous)crawl_trap_admit(db,job,resources.text(2),previous,false);
    if(resources.text(5)=="page"&&resources.text(6)=="succeeded") {
      if(resources.text(8).empty())incomplete=true;
      else (void)crawl_trap_observe_body(db,job,resources.text(2),resources.text(7));
    }
  } } catch(const Error& error) {
    if(error.code!="E_CRAWL_TRAP_STATE_LIMIT")throw;
    // An oversized legacy retention state must settle once. Rolling back
    // this bootstrap and retrying it would starve unrelated queued jobs.
    const auto blocked=capacity(db,job);auto receipt=Json::object();receipt.set("trap",blocked.detail);
    Sql edges(db,"UPDATE crawl_edges SET followed=0,reason=? WHERE job_id=? AND url IN (SELECT url FROM map_resources WHERE job_id=? AND state='pending')");
    edges.bind(1,blocked.reason);edges.bind(2,job);edges.bind(3,job);edges.step();
    Sql details(db,"INSERT OR REPLACE INTO crawl_trap_edges(job_id,position,decision_json) SELECT job_id,position,? FROM crawl_edges WHERE job_id=? AND url IN (SELECT url FROM map_resources WHERE job_id=? AND state='pending')");
    details.bind(1,blocked.detail.canonical());details.bind(2,job);details.bind(3,job);details.step();
    Sql skipped(db,"UPDATE map_resources SET state='skipped',error_code=?,receipt_json=? WHERE job_id=? AND state='pending'");
    skipped.bind(1,blocked.reason);skipped.bind(2,receipt.canonical());skipped.bind(3,job);skipped.step();partial(db,job,blocked.reason);return;
  }
  // Redirect parents can be inserted after children in the legacy resource
  // order. A capped monotone pass reconstructs the small consecutive run.
  for(std::int64_t pass=0;pass<=date_run_limit;++pass) {
    Sql runs(db,R"SQL(UPDATE crawl_trap_urls AS child SET date_run=MAX(date_run,COALESCE((SELECT MIN(parent.date_run+1,7) FROM crawl_trap_urls parent
      WHERE parent.job_id=child.job_id AND parent.url=child.parent AND parent.date_family=child.date_family AND ABS(parent.date_value-child.date_value)=1),0))
      WHERE child.job_id=? AND child.date_family!='')SQL");runs.bind(1,job);runs.step();
  }
  if(incomplete)partial(db,job,"E_CRAWL_TRAP_HISTORY_INCOMPLETE");
}

CrawlTrapDecision crawl_trap_decide(sqlite3* db,const std::string& job,const std::string& url,const std::string& parent,bool explicit_seed,bool dispatch) {
  Sql policy(db,"SELECT version,config_json FROM crawl_trap_policy WHERE job_id=?");policy.bind(1,job);
  if(!policy.step()||policy.text(0)!=crawl_trap_version||policy.text(1)!=config().canonical())return decision("E_CRAWL_TRAP_POLICY","unsupported_policy","",0,0);
  Sql prior(db,"SELECT explicit_seed FROM crawl_trap_urls WHERE job_id=? AND url=?");prior.bind(1,job);prior.bind(2,url);const bool exists=prior.step();
  if(!exists) { const auto full=capacity(db,job);if(full)return full; }
  if(explicit_seed||(exists&&prior.num(0)!=0))return {};
  if(exists&&!dispatch) {
    Sql denied(db,"SELECT error_code,receipt_json FROM map_resources WHERE job_id=? AND url=? AND state IN ('skipped','failed') AND error_code LIKE 'E_CRAWL_TRAP_%'");
    denied.bind(1,job);denied.bind(2,url);
    if(denied.step()&&!denied.text(1).empty()) {
      const auto receipt=Json::parse(denied.text(1));
      if(receipt.has("trap"))return {denied.text(0),receipt.at("trap")};
    }
  }
  const auto shape=classify(url);
  // A page already retained by the frontier is not a new expansion. The
  // dispatch check still retracts a queued page after later body evidence.
  if(!exists||dispatch) {
    const auto duplicates=repeated(db,job,shape.page_family);
    if(duplicates>=body_repeat_limit)return decision("E_CRAWL_TRAP_DUPLICATE_PAGINATION","repeated_pagination_body",shape.page_family,duplicates,body_repeat_limit);
  }
  if(exists)return {};
  if(!shape.date_family.empty()) {
    const auto run=next_date_run(db,job,shape,parent);
    if(run>date_run_limit)return decision("E_CRAWL_TRAP_DATE_SEQUENCE","consecutive_date_steps",shape.date_family,run,date_run_limit);
    Sql dates(db,"SELECT COUNT(DISTINCT date_value),COALESCE(MAX(date_value=?),0) FROM crawl_trap_urls WHERE job_id=? AND date_family=?");
    dates.bind(1,shape.date_value);dates.bind(2,job);dates.bind(3,shape.date_family);dates.step();
    if(dates.num(1)==0&&dates.num(0)>=date_limit)return decision("E_CRAWL_TRAP_DATE_FAMILY","distinct_calendar_dates",shape.date_family,dates.num(0)+1,date_limit);
  }
  if(shape.filter_keys>query_key_limit)return decision("E_CRAWL_TRAP_QUERY_DIMENSIONS","filter_key_count",shape.query_family,shape.filter_keys,query_key_limit);
  if(shape.query_pairs>query_pair_limit)return decision("E_CRAWL_TRAP_QUERY_DIMENSIONS","query_pair_count",shape.query_family,shape.query_pairs,query_pair_limit);
  if(!shape.ordering_signature.empty()) {
    Sql variants(db,"SELECT COUNT(*) FROM crawl_trap_urls WHERE job_id=? AND ordering_signature=?");variants.bind(1,job);variants.bind(2,shape.ordering_signature);variants.step();
    if(variants.num(0)>=permutation_limit)return decision("E_CRAWL_TRAP_QUERY_PERMUTATIONS","equivalent_query_permutations",shape.ordering_signature,variants.num(0)+1,permutation_limit);
  }
  if(!shape.query_family.empty()) {
    Sql combos(db,"SELECT COUNT(DISTINCT query_signature),COALESCE(MAX(query_signature=?),0) FROM crawl_trap_urls WHERE job_id=? AND query_family=?");
    combos.bind(1,shape.query_signature);combos.bind(2,job);combos.bind(3,shape.query_family);combos.step();
    if(combos.num(1)==0&&combos.num(0)>=combination_limit)return decision("E_CRAWL_TRAP_QUERY_COMBINATIONS","filter_combinations",shape.query_family,combos.num(0)+1,combination_limit);
  }
  return {};
}

Json crawl_trap_observe_body(sqlite3* db,const std::string& job,const std::string& url,std::string_view text) {
  if(text.size()<static_cast<std::size_t>(body_min_bytes))return Json();
  Sql row(db,"SELECT page_family,body_hash FROM crawl_trap_urls WHERE job_id=? AND url=?");row.bind(1,job);row.bind(2,url);if(!row.step()||row.text(0).empty())return Json();
  const auto family=row.text(0);
  if(row.text(1).empty()) {
    Sql body(db,"UPDATE crawl_trap_urls SET body_hash=?,body_bytes=? WHERE job_id=? AND url=? AND body_hash=''");
    body.bind(1,sha256(text));body.bind(2,static_cast<std::int64_t>(text.size()));body.bind(3,job);body.bind(4,url);body.step();
  }
  const auto count=repeated(db,job,family);auto out=Json::object();out.set("version",crawl_trap_version);out.set("rule","repeated_pagination_body");
  out.set("family_sha256",family);out.set("max_identical_bodies",count);out.set("limit",body_repeat_limit);out.set("expansion_limited",count>=body_repeat_limit);
  // Reaching the evidence threshold alone is not partial coverage. An actual
  // denied outgoing edge or queued dispatch records the partial stop reason.
  return out;
}

void crawl_trap_record_edge(sqlite3* db,const std::string& job,const Json& candidate,const CrawlTrapDecision& blocked) {
  if(!blocked)return;
  Sql insert(db,"INSERT OR REPLACE INTO crawl_trap_edges(job_id,position,decision_json) SELECT job_id,position,? FROM crawl_edges WHERE job_id=? AND parent=? AND url=? AND source=? AND followed=0");
  insert.bind(1,blocked.detail.canonical());insert.bind(2,job);insert.bind(3,candidate.at("parent").str());insert.bind(4,candidate.at("url").str());insert.bind(5,candidate.at("source").str());insert.step();
}

void crawl_trap_skip_edges(sqlite3* db,const std::string& job,const std::string& logical_url,const CrawlTrapDecision& blocked) {
  if(!blocked)return;
  // Called only for a pending logical resource before its next physical hop.
  // All retained parents receive the same reason; successful body receipts
  // and edges to any already-read logical resource are left untouched.
  Sql update(db,"UPDATE crawl_edges SET followed=0,reason=? WHERE job_id=? AND url=?");update.bind(1,blocked.reason);update.bind(2,job);update.bind(3,logical_url);update.step();
  Sql detail(db,"INSERT OR REPLACE INTO crawl_trap_edges(job_id,position,decision_json) SELECT job_id,position,? FROM crawl_edges WHERE job_id=? AND url=?");
  detail.bind(1,blocked.detail.canonical());detail.bind(2,job);detail.bind(3,logical_url);detail.step();
}

Json crawl_trap_summary(sqlite3* db,const std::string& job) {
  Sql policy(db,"SELECT version,config_json FROM crawl_trap_policy WHERE job_id=?");policy.bind(1,job);if(!policy.step())return Json();
  auto out=Json::object();out.set("version",policy.text(0));out.set("limits",Json::parse(policy.text(1)));
  out.set("scope","job_lifetime");out.set("classification","finite_heuristics");out.set("explicit_seeds","read_with_existing_scope_robots_and_budgets");
  Sql counts(db,"SELECT COUNT(*),COALESCE(SUM(body_hash!=''),0),COUNT(DISTINCT NULLIF(date_family,'')),COUNT(DISTINCT NULLIF(query_family,'')) FROM crawl_trap_urls WHERE job_id=?");counts.bind(1,job);counts.step();
  out.set("url_observations",counts.num(0));out.set("body_observations",counts.num(1));out.set("date_families",counts.num(2));out.set("query_families",counts.num(3));
  Sql closed(db,"SELECT COUNT(DISTINCT page_family) FROM (SELECT page_family FROM crawl_trap_urls WHERE job_id=? AND page_family!='' AND body_hash!='' GROUP BY page_family,body_hash HAVING COUNT(*)>=3)");closed.bind(1,job);closed.step();out.set("limited_pagination_families",closed.num(0));
  Sql reasons(db,"SELECT reason,COUNT(*) FROM crawl_edges WHERE job_id=? AND reason LIKE 'E_CRAWL_TRAP_%' GROUP BY reason ORDER BY reason");reasons.bind(1,job);
  auto stops=Json::array();while(reasons.step()) { auto stop=Json::object();stop.set("reason",reasons.text(0));stop.set("edge_count",reasons.num(1));stops.push(stop); }
  out.set("stops",stops);return out;
}
}
