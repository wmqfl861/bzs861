#include "bzs/http_cache.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cctype>
#include <set>
#include <optional>
#include <vector>
#include <string_view>

namespace bzs {
namespace {
void protocol(bool value,const char* message) { require(value,"E_WORKER_PROTOCOL",message,502); }
bool hex(const std::string& value) {
  return value.size()==64&&std::all_of(value.begin(),value.end(),[](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');});
}
std::string field(const Json& value,const char* key,std::size_t limit) {
  protocol(value.has(key)&&value.at(key).is_string(),"Invalid HTTP cache field");
  auto text=value.at(key).str();
  protocol(text.size()<=limit&&std::all_of(text.begin(),text.end(),[](unsigned char c){return c>=32&&c<127;}),"Invalid HTTP cache field bytes");
  return text;
}
std::string_view opaque(std::string_view tag) { return tag.starts_with("W/")?tag.substr(2):tag; }
bool etag(const std::string& value) {
  if(value.empty())return true;
  auto tag=opaque(value);
  return tag.size()>=2&&tag.front()=='"'&&tag.back()=='"'&&
    std::all_of(tag.begin()+1,tag.end()-1,[](unsigned char c){return c==33||(c>=35&&c<=126);});
}
bool date(const std::string& value) {
  if(value.empty())return true;
  if(value.size()!=29||value.substr(3,2)!=", "||value[7]!=' '||value[11]!=' '||value[16]!=' '||
     value[19]!=':'||value[22]!=':'||value.substr(25)!=" GMT")return false;
  const auto number=[&](std::size_t start,std::size_t length)->int {
    int out=0;for(std::size_t i=start;i<start+length;++i) { if(value[i]<'0'||value[i]>'9')return -1;out=out*10+(value[i]-'0'); }return out;
  };
  constexpr std::array<std::string_view,12> months={"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
  constexpr std::array<std::string_view,7> days={"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
  const auto found=std::find(months.begin(),months.end(),std::string_view(value).substr(8,3));
  const auto year=number(12,4),day=number(5,2),hour=number(17,2),minute=number(20,2),second=number(23,2);
  if(found==months.end()||year<1601||day<1||hour<0||hour>23||minute<0||minute>59||second<0||second>59)return false;
  const std::chrono::year_month_day calendar{std::chrono::year{year},std::chrono::month{static_cast<unsigned>(found-months.begin()+1)},std::chrono::day{static_cast<unsigned>(day)}};
  return calendar.ok()&&std::string_view(value).substr(0,3)==days[std::chrono::weekday{std::chrono::sys_days{calendar}}.c_encoding()];
}
std::string trim(std::string value) {
  const auto first=value.find_first_not_of(' ');if(first==std::string::npos)return {};
  return value.substr(first,value.find_last_not_of(' ')-first+1);
}
// Every supported selection is already part of the exact request profile.
// Empty metadata means the response omitted Vary; a nonempty list may name
// only Accept-Encoding. Keep the same bounded ASCII contract as wire fields.
bool supported_vary(std::string_view value) {
  if(value.size()>4096||!std::all_of(value.begin(),value.end(),[](unsigned char c){return c>=32&&c<127;}))return false;
  if(value.empty())return true;
  constexpr std::string_view supported="accept-encoding";
  for(;;) {
    const auto end=value.find(',');auto item=value.substr(0,end);
    while(!item.empty()&&item.front()==' ')item.remove_prefix(1);
    while(!item.empty()&&item.back()==' ')item.remove_suffix(1);
    if(item.size()!=supported.size())return false;
    for(std::size_t i=0;i<item.size();++i) {
      const auto c=item[i];const auto folded=c>='A'&&c<='Z'?static_cast<char>(c-'A'+'a'):c;
      if(folded!=supported[i])return false;
    }
    if(end==std::string_view::npos)return true;
    value.remove_prefix(end+1);
  }
}
bool cache_control(const std::string& value) {
  if(value.empty())return true;
  const auto token=[](const std::string& word) {
    return !word.empty()&&std::all_of(word.begin(),word.end(),[](unsigned char c){
      return std::isalnum(c)||std::string_view("!#$%&'*+-.^_`|~").find(static_cast<char>(c))!=std::string_view::npos;
    });
  };
  std::set<std::string> seen;std::size_t start=0;
  while(start<=value.size()) {
    const auto end=value.find(',',start);const auto part=trim(value.substr(start,end==std::string::npos?end:end-start));
    const auto split=part.find('=');auto name=trim(part.substr(0,split));if(!token(name))return false;
    std::string argument;
    if(split!=std::string::npos) {
      argument=trim(part.substr(split+1));
      const bool quoted=argument.size()>=2&&argument.front()=='"'&&argument.back()=='"'&&
        std::all_of(argument.begin()+1,argument.end()-1,[](unsigned char c){return c==32||c==33||(c>=35&&c<=91)||(c>=93&&c<=126);});
      if(!token(argument)&&!quoted)return false;
    }
    std::transform(name.begin(),name.end(),name.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    if(name=="no-store"||name=="no-cache"||name=="private")return false;
    if(name=="max-age"||name=="s-maxage") {
      if(!seen.insert(name).second||split==std::string::npos)return false;
      auto age=argument;if(age.size()>=2&&age.front()=='"'&&age.back()=='"')age=age.substr(1,age.size()-2);
      if(age.empty()||age.size()>12||!std::all_of(age.begin(),age.end(),[](char c){return c>='0'&&c<='9';}))return false;
      std::int64_t seconds=0;const auto result=std::from_chars(age.data(),age.data()+age.size(),seconds);
      if(result.ec!=std::errc{}||seconds==0)return false;
    }
    if(end==std::string::npos)break;
    start=end+1;
  }
  return true;
}
}
Json validate_http_cache(const Json& record,const std::string& url) {
  record.allowed({"version","url","request_profile","etag","last_modified","cache_control","vary","date","age","expires"});
  protocol(record.size()==10,"Incomplete HTTP cache record");
  protocol(field(record,"version",64)=="bzs-http-revalidation-1"&&field(record,"url",4096)==url&&!url.empty(),"HTTP cache identity changed");
  protocol(hex(field(record,"request_profile",64)),"Invalid HTTP request profile");
  const auto tag=field(record,"etag",1024),modified=field(record,"last_modified",29);
  protocol(etag(tag)&&date(modified)&&(!tag.empty()||!modified.empty()),"Invalid or missing HTTP validator");
  protocol(supported_vary(field(record,"vary",4096))&&cache_control(field(record,"cache_control",4096)),"Unsupported HTTP cache policy");
  protocol(date(field(record,"date",29))&&date(field(record,"expires",29)),"Invalid HTTP cache date");
  const auto age=field(record,"age",12);protocol(std::all_of(age.begin(),age.end(),[](char c){return c>='0'&&c<='9';}),"Invalid HTTP cache age");
  return Json::parse(record.canonical());
}
Json http_conditional(const Json& cache,const std::string& binding) {
  protocol(hex(binding),"Invalid HTTP revalidation binding");
  auto out=Json::object();for(const auto* key:{"version","url","request_profile","etag","last_modified"})out.set(key,cache.at(key));
  out.set("binding_id",binding);return out;
}
Json merge_http_cache(const Json& base,const Json& update) {
  const auto url=base.at("url").str();auto old=validate_http_cache(base,url),fresh=validate_http_cache(update,url);
  protocol(old.at("request_profile").str()==fresh.at("request_profile").str(),"HTTP request profile changed during revalidation");
  const auto tag=old.at("etag").str(),modified=old.at("last_modified").str();
  protocol(tag.empty()||opaque(tag)==opaque(fresh.at("etag").str()),"304 validator does not identify the stored body");
  protocol(modified.empty()||modified==fresh.at("last_modified").str(),"304 last-modified does not identify the stored body");
  for(const auto& key:fresh.keys())if(!fresh.at(key).str().empty())old.set(key,fresh.at(key));
  return validate_http_cache(old,url);
}

namespace {
std::int64_t stamp(const std::string& value) {
  constexpr std::array<std::string_view,12> months={"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
  const auto month=std::find(months.begin(),months.end(),std::string_view(value).substr(8,3))-months.begin()+1;
  const auto number=[&](std::size_t start,std::size_t count) { std::int64_t n=0;for(std::size_t i=start;i<start+count;++i)n=n*10+(value[i]-'0');return n; };
  const std::chrono::year_month_day day{std::chrono::year{static_cast<int>(number(12,4))},std::chrono::month{static_cast<unsigned>(month)},std::chrono::day{static_cast<unsigned>(number(5,2))}};
  return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::sys_days{day}.time_since_epoch()).count()+number(17,2)*3600000+number(20,2)*60000+number(23,2)*1000;
}
struct Controls {
  bool valid=true,no_store=false,no_cache=false;
  std::optional<std::int64_t> max_age;
};
Controls controls(const std::string& value) {
  Controls out;std::set<std::string> seen;std::size_t start=0;
  if(value.empty())return out;
  // Quoted field-name lists are permitted; commas inside quotes are not separators.
  bool quote=false,escape=false;std::vector<std::string> parts;
  for(std::size_t i=0;i<=value.size();++i) {
    if(i==value.size()||(!quote&&value[i]==',')) { parts.push_back(trim(value.substr(start,i-start)));start=i+1;continue; }
    if(escape)escape=false;
    else if(quote&&value[i]=='\\')escape=true;
    else if(value[i]=='"')quote=!quote;
  }
  if(quote||escape)out.valid=false;
  for(const auto& part:parts) {
    const auto equal=part.find('=');auto name=trim(part.substr(0,equal));
    std::transform(name.begin(),name.end(),name.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    const auto token=[](const std::string& text) { return !text.empty()&&std::all_of(text.begin(),text.end(),[](unsigned char c){return std::isalnum(c)||std::string_view("!#$%&'*+-.^_`|~").find(static_cast<char>(c))!=std::string_view::npos;}); };
    if(!token(name))out.valid=false;
    auto arg=equal==std::string::npos?std::string{}:trim(part.substr(equal+1));
    if(equal!=std::string::npos&&!token(arg)) {
      if(arg.size()<2||arg.front()!='"'||arg.back()!='"')out.valid=false;
      else {bool escaped=false;for(std::size_t i=1;i+1<arg.size();++i) {if(escaped)escaped=false;else if(arg[i]=='\\')escaped=true;else if(arg[i]=='"')out.valid=false;}if(escaped)out.valid=false;}
    }
    if(name=="no-store")out.no_store=true;
    if(name=="no-cache"||name=="must-understand")out.no_cache=true;
    if(name=="max-age"||name=="s-maxage") {
      if(!seen.insert(name).second||equal==std::string::npos)out.valid=false;
      if(arg.size()>=2&&arg.front()=='"'&&arg.back()=='"')arg=arg.substr(1,arg.size()-2);
      if(arg.empty()||arg.size()>12||!std::all_of(arg.begin(),arg.end(),[](char c){return c>='0'&&c<='9';})) { out.valid=false;continue; }
      std::int64_t seconds=0;const auto parsed=std::from_chars(arg.data(),arg.data()+arg.size(),seconds);
      if(parsed.ec!=std::errc{}) { out.valid=false;continue; }
      // Even private owner caches honor the tighter shared-cache ceiling.
      const auto ms=std::min<std::int64_t>(seconds,365LL*86400)*1000;
      out.max_age=out.max_age?std::min(*out.max_age,ms):ms;
    }
  }
  return out;
}
}
Json validate_http_freshness(const Json& record,const std::string& url) {
  record.allowed({"version","url","request_profile","cache_control","vary","date","age","expires","pragma","received_at_ms","response_delay_ms","invalid"});
  protocol(record.size()==12,"Incomplete HTTP freshness record");
  protocol(field(record,"version",64)=="bzs-http-freshness-1"&&field(record,"url",4096)==url&&!url.empty(),"HTTP freshness identity changed");
  protocol(hex(field(record,"request_profile",64)),"Invalid HTTP freshness profile");
  for(const auto* key:{"cache_control","vary","date","age","expires","pragma"})(void)field(record,key,4096);
  protocol(record.at("received_at_ms").is_integer()&&record.at("received_at_ms").num()>=0&&record.at("received_at_ms").num()<=253402300799999LL,"Invalid HTTP response time");
  protocol(record.at("response_delay_ms").is_integer()&&record.at("response_delay_ms").num()>=0&&record.at("response_delay_ms").num()<=3600000,"Invalid HTTP response delay");
  protocol(record.at("invalid").is_bool(),"Invalid HTTP freshness validity flag");
  return Json::parse(record.canonical());
}
Json merge_http_freshness(const Json& base,const Json& update) {
  const auto url=base.at("url").str();auto old=validate_http_freshness(base,url),fresh=validate_http_freshness(update,url);
  protocol(old.at("request_profile").str()==fresh.at("request_profile").str(),"HTTP freshness request profile changed");
  for(const auto* key:{"cache_control","vary","date","age","expires","pragma"})if(fresh.at(key).str().empty())fresh.set(key,old.at(key));
  return fresh;
}
Json http_freshness(const Json& input,std::int64_t now,std::int64_t max_age_s) {
  auto out=Json::object();out.set("fresh",false);out.set("store_allowed",true);out.set("reason","missing_policy");
  out.set("current_age_ms",std::int64_t{0});out.set("freshness_lifetime_ms",std::int64_t{0});
  if(input.is_null()||!input.has("version"))return out;
  const auto r=validate_http_freshness(input,input.at("url").str());const auto cc=controls(r.at("cache_control").str());
  if(cc.no_store||!cc.valid||r.at("invalid").flag()) {out.set("store_allowed",false);out.set("reason",cc.no_store?"no_store":"invalid_cache_fields");return out;}
  if(cc.no_cache) {out.set("reason","requires_validation");return out;}
  if(!supported_vary(r.at("vary").str())) {out.set("reason","unsupported_vary");return out;}
  if(r.at("cache_control").str().empty()) {const auto pragma=controls(r.at("pragma").str());if(!pragma.valid||pragma.no_cache) {out.set("reason","pragma_no_cache");return out;}}
  const auto received=r.at("received_at_ms").num();
  if(now<received) {out.set("reason","clock_rollback");return out;}
  auto lifetime=std::min<std::int64_t>(max_age_s,365LL*86400)*1000;
  if(cc.max_age)lifetime=std::min(lifetime,*cc.max_age);
  const auto date_value=r.at("date").str(),expiry=r.at("expires").str(),age_value=r.at("age").str();
  if((!date_value.empty()&&!date(date_value))||(!expiry.empty()&&!date(expiry))) {out.set("reason","invalid_date");return out;}
  std::int64_t age=0;
  if(!age_value.empty()) {
    if(age_value.size()>12||!std::all_of(age_value.begin(),age_value.end(),[](char c){return c>='0'&&c<='9';})) {out.set("reason","invalid_age");return out;}
    const auto parsed=std::from_chars(age_value.data(),age_value.data()+age_value.size(),age);
    if(parsed.ec!=std::errc{}) {out.set("reason","invalid_age");return out;}age*=1000;
  }
  const auto date_ms=date_value.empty()?received:stamp(date_value);
  if(date_ms>received+1000) {out.set("reason","future_date");return out;}
  const auto current_age=std::max(age+r.at("response_delay_ms").num(),std::max<std::int64_t>(0,received-date_ms))+(now-received);
  if(!expiry.empty()) {
    const auto expiry_ms=stamp(expiry);
    lifetime=std::min(lifetime,std::max<std::int64_t>(0,expiry_ms-date_ms));
    if(now>=expiry_ms) {out.set("reason","expired");out.set("current_age_ms",current_age);out.set("freshness_lifetime_ms",lifetime);return out;}
  }
  const bool fresh=current_age<lifetime;
  out.set("fresh",fresh);out.set("reason",fresh?"fresh":"expired");out.set("current_age_ms",current_age);out.set("freshness_lifetime_ms",lifetime);return out;
}

bool http_response_restricts_cache(const Json& response,std::int64_t now) {
  if(!response.has("http_freshness"))return false;
  protocol(response.has("final_url")&&response.at("final_url").is_string(),"HTTP cache response lacks identity");
  const auto record=validate_http_freshness(response.at("http_freshness"),response.at("final_url").str());
  // A failed extraction or unsupported 304 must not preserve an older, more
  // permissive freshness decision. This ceiling is independent of the caller's
  // max_age_s, which may deliberately request a conditional refresh at age zero.
  return !http_freshness(record,now,7*86400).at("fresh").flag();
}

}
