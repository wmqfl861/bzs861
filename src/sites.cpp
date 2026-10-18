#include "bzs/sites.hpp"
#include "bzs/contracts.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <set>

namespace bzs {
namespace {
  bool hex(char c) { return (c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F'); }
  void safe_path(std::string_view path,bool scope) {
    require(!path.empty()&&path.front()=='/'&&path.size()<=4096,"E_SCOPE","An absolute site path is required",403);
    require(path.find_first_of("\\?#") == std::string_view::npos,"E_SCOPE","Invalid site path",403);
    for(std::size_t i=0;i<path.size();++i) {
      const auto c=static_cast<unsigned char>(path[i]);
      require(c>32&&c<127,"E_SCOPE","Site paths must use ASCII URI encoding",403);
      if(path[i]=='%') {
        require(!scope&&i+2<path.size()&&hex(path[i+1])&&hex(path[i+2]),"E_SCOPE","Invalid or encoded scope path",403);
        auto code=ascii_lower(path.substr(i+1,2));
        require(code!="2e"&&code!="2f"&&code!="5c"&&code!="25","E_SCOPE","Encoded path separators, dots and nested encodings are not admitted",403);
        i+=2;
      }
    }
    std::size_t from=1;
    while(from<=path.size()) {
      auto end=path.find('/',from);if(end==std::string_view::npos)end=path.size();
      auto part=path.substr(from,end-from);
      require(part!="."&&part!="..","E_SCOPE","Dot segments are not admitted",403);
      from=end+1;
    }
  }
}
std::string map_url(std::string_view input) {
  auto uri=normalize_iri(input);
  check_public_url(uri);
  require(uri.find('\\')==std::string::npos,"E_SCOPE","Backslashes are not admitted",403);
  auto authority_start=uri.find("://")+3;
  auto authority_end=uri.find_first_of("/?#",authority_start);
  if(authority_end==std::string::npos)authority_end=uri.size();
  auto authority=ascii_lower(std::string_view(uri).substr(authority_start,authority_end-authority_start));
  if(auto colon=authority.find(':');colon!=std::string::npos) {
    auto number=std::stoll(authority.substr(colon+1));
    auto scheme=uri.substr(0,authority_start);
    authority=authority.substr(0,colon)+(((scheme=="https://"&&number==443)||(scheme=="http://"&&number==80))?"":":"+std::to_string(number));
  }
  auto end=uri.find('#',authority_end);if(end==std::string::npos)end=uri.size();
  auto rest=uri.substr(authority_end,end-authority_end);
  if(rest.empty()||rest.front()=='?')rest='/'+rest;
  if(!rest.empty()&&rest.back()=='?'&&rest.find('?')==rest.size()-1)rest.pop_back();
  auto q=rest.find('?');
  safe_path(std::string_view(rest).substr(0,q),false);
  for(std::size_t i=0;i<rest.size();++i) {
    const char c=rest[i];
    require(c!='['&&c!=']'&&c!='<'&&c!='>'&&c!='"'&&c!='`'&&c!='{'&&c!='}'&&c!='|'&&c!='^',"E_URL","Invalid URL character");
    if(c=='%') {
      require(i+2<rest.size()&&hex(rest[i+1])&&hex(rest[i+2]),"E_URL","Malformed percent escape");
      i+=2;
    }
  }
  return uri.substr(0,authority_start)+authority+rest;
}
std::string map_origin(std::string_view input) {
  auto uri=map_url(input);
  return uri.substr(0,uri.find('/',uri.find("://")+3));
}
std::string map_path(std::string_view input) {
  auto uri=map_url(input);auto start=uri.find('/',uri.find("://")+3);
  return uri.substr(start,uri.find('?',start)-start);
}
std::string map_scope_path(std::string_view input) {
  require(input.size()<=2048,"E_SCOPE","Site scope path is too long",403);
  safe_path(input,true);
  auto result=std::string(input);
  while(result.size()>1&&result.back()=='/')result.pop_back();
  return result;
}
bool path_within(std::string_view path,std::string_view prefix) {
  if(prefix=="/")return !path.empty()&&path.front()=='/';
  while(prefix.size()>1&&prefix.back()=='/')prefix.remove_suffix(1);
  return path==prefix||(path.size()>prefix.size()&&path.starts_with(prefix)&&path[prefix.size()]=='/');
}
void SiteSpec::require_review(std::int64_t now) const {
  require(enabled,"E_SITE_REVIEW","Site is disabled",403);
  try {
    validate_access_review(review,true,now);
    require(review.at("approved_by").str().size()<=256,"E_SITE_REVIEW","Site approver metadata is too long",403);
    require(review.at("cache_policy").str()=="allowed_with_limits"&&
        (review.at("redistribution_policy").str()=="links_only"||review.at("redistribution_policy").str()=="allowed_with_limits"),
        "E_SITE_REVIEW","Site requires explicit cache and redistribution review",403);
  } catch(const Error& error) { throw Error("E_SITE_REVIEW",error.what(),403); }
}
bool SiteSpec::reviewed(std::int64_t now)const { try { require_review(now);return true; } catch(const Error&) { return false; } }
bool SiteSpec::allows(std::string_view input,std::string_view scope)const {
  try {
    auto uri=map_url(input);
    if(map_origin(uri)!=origin)return false;
    auto path=map_path(uri);
    return (scope.empty()||path_within(path,scope))&&std::any_of(path_prefixes.begin(),path_prefixes.end(),[&](const auto& p){return path_within(path,p);});
  } catch(const Error&) { return false; }
}
void SiteSpec::require_scope(std::string_view input,std::string_view scope)const {
  require(allows(input,scope),"E_SCOPE","URL is outside the admitted site path scope",403);
}
SiteRegistry::SiteRegistry():digest_(sha256("{\"version\":1,\"sites\":[]}")) {}
SiteRegistry::SiteRegistry(const Json& manifest) {
  try {
    manifest.allowed({"version","sites"});
    require(manifest.at("version").num()==1,"E_CONFIG","Unsupported site registry version",500);
    auto entries=manifest.at("sites");require(entries.is_array()&&entries.size()<=100,"E_CONFIG","sites must have at most 100 entries",500);
    std::set<std::string> ids,origins;
    for(std::size_t i=0;i<entries.size();++i) {
      auto entry=entries.at(i);entry.allowed({"id","enabled","origin","path_prefixes","review","limits"});
      SiteSpec site;site.id=required_string(entry,"id",64);
      require(site.id.size()>=2&&std::all_of(site.id.begin(),site.id.end(),[](char c){return(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='.'||c=='_'||c=='-';})&&
          ((site.id.front()>='a'&&site.id.front()<='z')||(site.id.front()>='0'&&site.id.front()<='9'))&&ids.insert(site.id).second,
          "E_CONFIG","Invalid or duplicate site id",500);
      site.enabled=entry.at("enabled").flag();
      auto original=required_string(entry,"origin",4096);
      require(original.find_first_of("/?#",original.find("://")+3)==std::string::npos,"E_CONFIG","Site origin cannot have a path, query or fragment",500);
      site.origin=map_origin(original);require(origins.insert(site.origin).second,"E_CONFIG","Duplicate site origin would multiply its quota",500);
      auto prefixes=entry.at("path_prefixes");require(prefixes.is_array()&&prefixes.size()>=1&&prefixes.size()<=32,"E_CONFIG","Site needs 1..32 path prefixes",500);
      std::set<std::string> unique;
      for(std::size_t p=0;p<prefixes.size();++p) {
        auto prefix=map_scope_path(prefixes.at(p).str());require(unique.insert(prefix).second,"E_CONFIG","Duplicate site path prefix",500);
        site.path_prefixes.push_back(std::move(prefix));
      }
      site.review=Json::parse(entry.at("review").dump());validate_access_review(site.review,false,now_ms());
      auto limits=entry.at("limits");limits.allowed({"max_concurrency","min_interval_ms","request_quota","quota_window_seconds"});
      require(limits.at("max_concurrency").num()==1,"E_CONFIG","This map version requires site max_concurrency=1",500);
      site.min_interval_ms=int_option(limits,"min_interval_ms",-1,0,3600000);
      site.request_quota=int_option(limits,"request_quota",0,1,1000000);
      site.quota_window_seconds=int_option(limits,"quota_window_seconds",0,86400,86400);
      specs_.push_back(std::move(site));
    }
    digest_=sha256(manifest.dump());
  } catch(const Error& error) { throw Error("E_CONFIG",error.what(),500); }
}
SiteRegistry SiteRegistry::load(const std::filesystem::path& path) {
  auto raw=read_file(path,1024*1024);auto out=SiteRegistry(Json::parse(raw));out.digest_=sha256(raw);return out;
}
const SiteSpec* SiteRegistry::find(const std::string& id)const {
  for(const auto& site:specs_)if(site.id==id)return &site;
  return nullptr;
}
Json SiteRegistry::describe(std::int64_t now)const {
  auto result=Json::object(),items=Json::array();
  for(const auto& site:specs_) {
    auto item=Json::object(),paths=Json::array();for(const auto& path:site.path_prefixes)paths.push(Json::string(path));
    item.set("id",site.id);item.set("enabled",site.enabled);item.set("origin",site.origin);item.set("path_prefixes",paths);
    item.set("review_valid",site.reviewed(now));
    auto limits=Json::object();limits.set("max_concurrency",std::int64_t{1});limits.set("min_interval_ms",site.min_interval_ms);
    limits.set("request_quota",site.request_quota);limits.set("quota_window_seconds",site.quota_window_seconds);item.set("limits",limits);
    items.push(item);
  }
  result.set("sites",items);result.set("manifest_sha256",digest_);return result;
}
}
