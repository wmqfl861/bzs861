#include "bzs/http_cache.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cctype>
#include <set>
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
  protocol(field(record,"vary",4096).empty()&&cache_control(field(record,"cache_control",4096)),"Unsupported HTTP cache policy");
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
}
