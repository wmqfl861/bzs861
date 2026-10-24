#include "bzs/http_cache.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
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

namespace {
constexpr std::size_t redirect_hops=5,redirect_bytes=8192;
bool redirect_status(std::int64_t status) { return status==301||status==302||status==303||status==307||status==308; }
Json redirect_observations(const Json& response,const std::string& url) {
  auto output=Json::array();if(!response.has("http_redirect_observations"))return output;
  const auto records=response.at("http_redirect_observations");
  protocol(!url.empty()&&records.is_array()&&records.size()<=20&&records.canonical().size()<=524288,"Invalid redirect cache observation bounds");
  protocol(response.has("redirects")&&response.at("redirects").is_array()&&response.at("redirects").size()<=20&&
           response.has("final_url")&&response.at("final_url").is_string(),"Redirect cache observations lack a response route");
  std::vector<std::string> route={publication_url_identity(url)};
  const auto redirects=response.at("redirects");
  for(std::size_t i=0;i<redirects.size();++i) {
    try { route.push_back(publication_url_identity(redirects.at(i).str())); }catch(const Error&) { break; }
  }
  const auto final=publication_url_identity(response.at("final_url").str());
  std::size_t reached=route.size();for(std::size_t i=0;i<route.size();++i)if(route[i]==final)reached=i;
  protocol(reached<route.size(),"Redirect cache observations do not reach the actual response");
  const std::set<std::string> observed(route.begin(),route.begin()+static_cast<std::ptrdiff_t>(reached+1));
  protocol(response.has("usage")&&response.at("usage").has("external_requests")&&
           response.at("usage").at("external_requests").is_integer()&&
           response.at("usage").at("external_requests").num()>=static_cast<std::int64_t>(records.size()),"Redirect cache observations exceed actual HTTP usage");
  for(std::size_t i=0;i<records.size();++i) {
    const auto record=records.at(i);record.allowed({"url","http_status","http_freshness"});
    protocol(record.size()==3&&record.at("http_status").is_integer()&&redirect_status(record.at("http_status").num()),"Invalid redirect cache observation status");
    const auto target=field(record,"url",4096);
    protocol(observed.contains(publication_url_identity(target)),"Redirect cache observation is outside the actual response prefix");
    output.push(validate_http_freshness(record.at("http_freshness"),target));
  }
  return output;
}
}
Json validate_http_redirect_chain(const Json& record,const std::string& url,const std::string& final_url,const Json& redirects) {
  record.allowed({"version","request_url","final_url","hops"});
  protocol(record.size()==4&&record.canonical().size()<=redirect_bytes&&
           field(record,"version",64)=="bzs-http-redirect-chain-1"&&field(record,"request_url",4096)==url&&
           field(record,"final_url",4096)==final_url,"Invalid HTTP redirect proof identity or encoded bounds");
  const auto hops=record.at("hops");
  protocol(hops.is_array()&&hops.size()>=1&&hops.size()<=redirect_hops&&redirects.is_array()&&redirects.size()==hops.size(),"Incomplete HTTP redirect proof");
  std::set<std::string> visited={url};std::string prior=url,profile;std::int64_t bytes=0;
  check_public_url(url);
  for(std::size_t i=0;i<hops.size();++i) {
    const auto hop=hops.at(i);hop.allowed({"url","target_url","http_status","content_bytes","http_freshness"});
    protocol(hop.size()==5&&field(hop,"url",4096)==prior&&hop.at("http_status").is_integer()&&redirect_status(hop.at("http_status").num()),"Broken HTTP redirect proof prefix");
    const auto target=field(hop,"target_url",4096);check_public_url(target);
    protocol(redirects.at(i).is_string()&&redirects.at(i).str()==target&&visited.insert(target).second,"HTTP redirect proof is cyclic or differs from the actual route");
    protocol(hop.at("content_bytes").is_integer()&&hop.at("content_bytes").num()>=0&&hop.at("content_bytes").num()<=8*1024*1024,"Invalid redirect body accounting");
    bytes+=hop.at("content_bytes").num();protocol(bytes<=8*1024*1024,"Redirect bodies exceed the read grant bound");
    const auto freshness=validate_http_freshness(hop.at("http_freshness"),prior);
    if(profile.empty())profile=freshness.at("request_profile").str();
    protocol(freshness.at("request_profile").str()==profile,"v1 redirect proof changed its selected request profile");
    prior=target;
  }
  protocol(prior==final_url,"HTTP redirect proof does not reach the final representation");
  return Json::parse(record.canonical());
}
Json validate_http_redirect_receipt(const Json& response,const std::string& url,bool not_modified) {
  protocol(response.has("http_redirect_chain")&&response.has("http_freshness")&&response.has("usage"),"Redirect proof lacks its final receipt");
  const auto proof=validate_http_redirect_chain(response.at("http_redirect_chain"),url,response.at("final_url").str(),response.at("redirects"));
  const auto hops=proof.at("hops"),observed=redirect_observations(response,url);
  protocol(observed.size()==hops.size(),"Redirect proof omits an observed cache policy");
  std::int64_t bytes=0;
  for(std::size_t i=0;i<hops.size();++i) {
    const auto hop=hops.at(i),receipt=response.at("http_redirect_observations").at(i);
    protocol(receipt.at("url").str()==hop.at("url").str()&&receipt.at("http_status").num()==hop.at("http_status").num()&&
             observed.at(i).canonical()==hop.at("http_freshness").canonical(),"Redirect proof differs from its HTTP observations");
    bytes+=hop.at("content_bytes").num();
  }
  const auto policy=validate_http_freshness(response.at("http_freshness"),proof.at("final_url").str());
  protocol(policy.at("request_profile").str()==hops.at(std::size_t{0}).at("http_freshness").at("request_profile").str(),"Redirect final profile differs from its prefix");
  const auto usage=response.at("usage");
  protocol(usage.has("external_requests")&&usage.at("external_requests").is_integer()&&usage.at("external_requests").num()==static_cast<std::int64_t>(hops.size()+1)&&
           usage.has("content_bytes")&&usage.at("content_bytes").is_integer()&&usage.at("content_bytes").num()>=bytes&&usage.at("content_bytes").num()<=8*1024*1024&&
           (!not_modified||usage.at("content_bytes").num()==bytes),"Redirect receipt does not account for its exact HTTP prefix and final response");
  return proof;
}
bool http_redirect_chain_cacheable(const Json& record) {
  const auto hops=record.at("hops");
  for(std::size_t i=0;i<hops.size();++i) {
    const auto policy=hops.at(i).at("http_freshness");const auto cc=controls(policy.at("cache_control").str());
    // v1 never invents a default TTL for a redirect response.
    if((!cc.max_age&&policy.at("expires").str().empty())||
       !http_freshness(policy,policy.at("received_at_ms").num(),365LL*86400).at("fresh").flag())return false;
  }
  return true;
}
Json http_redirect_conditional(const Json& record,const Json& conditional) {
  protocol(conditional.at("url").str()==record.at("final_url").str(),"Redirect condition does not name its final URI");
  auto result=Json::object(),route=Json::array();const auto hops=record.at("hops");
  for(std::size_t i=0;i<hops.size();++i)route.push(hops.at(i).at("target_url"));
  result.set("version","bzs-http-redirect-conditional-1");result.set("request_url",record.at("request_url"));
  result.set("redirects",route);result.set("conditional",conditional);
  protocol(result.canonical().size()<=redirect_bytes,"Redirect condition exceeds its encoded bound");return result;
}
Json http_read_freshness(const Json& meta,const std::string& url,std::int64_t now,std::int64_t max_age_s) {
  auto result=http_freshness(meta.has("http_freshness")?meta.at("http_freshness"):Json(),now,max_age_s);
  const bool direct=meta.has("final_url")&&meta.at("final_url").str()==url&&meta.has("redirects")&&meta.at("redirects").is_array()&&meta.at("redirects").size()==0;
  if(direct)return result;
  if(!meta.has("http_redirect_chain")||!meta.has("http_freshness")) {
    result.set("fresh",false);result.set("reason","redirect_chain_not_cached");return result;
  }
  const auto proof=validate_http_redirect_chain(meta.at("http_redirect_chain"),url,meta.at("final_url").str(),meta.at("redirects"));
  const auto hops=proof.at("hops");
  protocol(hops.at(std::size_t{0}).at("http_freshness").at("request_profile").str()==meta.at("http_freshness").at("request_profile").str(),"Final redirect profile differs from its prefix");
  bool fresh=result.at("fresh").flag(),store=result.at("store_allowed").flag();
  std::int64_t remaining=result.at("freshness_lifetime_ms").num()-result.at("current_age_ms").num();
  for(std::size_t i=0;i<hops.size();++i) {
    const auto status=http_freshness(hops.at(i).at("http_freshness"),now,max_age_s);
    remaining=std::min(remaining,status.at("freshness_lifetime_ms").num()-status.at("current_age_ms").num());
    store=store&&status.at("store_allowed").flag();
    if(!status.at("fresh").flag()&&fresh)result.set("reason","redirect_"+status.at("reason").str());
    fresh=fresh&&status.at("fresh").flag();
  }
  if(!http_redirect_chain_cacheable(proof)) {fresh=false;result.set("reason","redirect_explicit_freshness_required");}
  result.set("fresh",fresh);result.set("store_allowed",store);result.set("redirect_hops",static_cast<std::int64_t>(hops.size()));
  // Express the intersected lifetime against the final response's age anchor.
  result.set("freshness_lifetime_ms",std::max<std::int64_t>(0,result.at("current_age_ms").num()+remaining));
  return result;
}
bool http_redirect_storage_allowed(const Json& response,const std::string& url,std::int64_t now) {
  const auto records=redirect_observations(response,url);
  for(std::size_t i=0;i<records.size();++i)if(!http_freshness(records.at(i),now,7*86400).at("store_allowed").flag())return false;
  return true;
}
bool http_response_restricts_cache(const Json& response,std::int64_t now,const std::string& url) {
  const auto observed=redirect_observations(response,url);
  for(std::size_t i=0;i<observed.size();++i) {
    const auto policy=observed.at(i);const auto cc=controls(policy.at("cache_control").str());
    if((!cc.max_age&&policy.at("expires").str().empty())||!http_freshness(policy,now,7*86400).at("fresh").flag())return true;
  }
  if(!response.has("http_freshness"))return false;
  protocol(response.has("final_url")&&response.at("final_url").is_string(),"HTTP cache response lacks identity");
  const auto record=validate_http_freshness(response.at("http_freshness"),response.at("final_url").str());
  return !http_freshness(record,now,7*86400).at("fresh").flag();
}

}
