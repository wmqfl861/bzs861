#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <set>

namespace bzs {
namespace {
  std::string text_field(const Json& j,const std::string& key,std::size_t bound) {
    const auto value=required_string(j,key,bound);
    require(valid_utf8(value)&&value.find('\0')==std::string::npos,"E_WORKER_PROTOCOL","Invalid map response string",502);return value;
  }
  void protocol(bool value,const char* message) { require(value,"E_WORKER_PROTOCOL",message,502); }
  bool hash(const std::string& value) { return value.size()==64&&std::all_of(value.begin(),value.end(),[](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');}); }
  void robots_record(const Json& record,const MapLease& lease,std::int64_t now) {
    record.allowed({"version","product_token","body_sha256","state","rules","sitemaps","diagnostics","origin","fetched_at_ms","expires_at_ms"});
    protocol(record.dump().size()<=768*1024,"Robots record too large");
    protocol(text_field(record,"version",64)=="bzs-robots-1"&&text_field(record,"product_token",64)=="BzsBot","Unknown robots policy version");
    protocol(text_field(record,"origin",4096)==lease.origin&&hash(text_field(record,"body_sha256",64)),"Invalid robots origin or content hash");
    const auto state=text_field(record,"state",16);protocol(state=="rules"||state=="allow_all"||state=="deny_all","Invalid robots state");
    const auto fetched=record.at("fetched_at_ms").num(),expires=record.at("expires_at_ms").num();
    protocol(fetched>=0&&fetched<=now&&expires>now&&expires>fetched&&expires-fetched<=86400000,"Invalid robots cache lifetime");
    auto rules=record.at("rules");protocol(rules.is_array()&&rules.size()<=32768,"Invalid robots rules");
    for(std::size_t i=0;i<rules.size();++i) {
      auto rule=rules.at(i);rule.allowed({"pattern","allow"});auto pattern=text_field(rule,"pattern",12288);
      protocol(!pattern.empty()&&(pattern.front()=='/'||pattern.front()=='*')&&std::all_of(pattern.begin(),pattern.end(),[](unsigned char c){return c>32&&c<127;}),"Invalid robots pattern");(void)rule.at("allow").flag();
    }
    protocol(state=="rules"||rules.size()==0,"Non-rule robots state has rules");
    auto maps=record.at("sitemaps");protocol(maps.is_array()&&maps.size()<=64,"Invalid robots sitemap list");
    for(std::size_t i=0;i<maps.size();++i)protocol(maps.at(i).is_string()&&maps.at(i).str().size()<=4096&&valid_utf8(maps.at(i).str()),"Invalid robots sitemap URI");
    auto diagnostics=record.at("diagnostics");protocol(diagnostics.is_array()&&diagnostics.size()<=32,"Invalid robots diagnostics");
    for(std::size_t i=0;i<diagnostics.size();++i)protocol(diagnostics.at(i).is_string()&&diagnostics.at(i).str().size()<=128&&valid_utf8(diagnostics.at(i).str()),"Invalid robots diagnostic");
  }
  void discovery_record(const Json& record,const MapLease& lease,std::int64_t max_entries) {
    record.allowed({"document_url","document_type","parse_status","candidates","truncated","diagnostics"});
    protocol(map_url(text_field(record,"document_url",4096))==lease.url,"Discovery document identity changed");
    const auto type=text_field(record,"document_type",32),status=text_field(record,"parse_status",32);
    protocol(status=="ok"||status=="invalid"||status=="unsupported","Invalid discovery parse status");
    const std::set<std::string> types={"sitemap_urlset","sitemap_index","rss","atom","html","unknown"};
    protocol(types.contains(type),"Unknown discovery document type");
    (void)record.at("truncated").flag();protocol(record.at("diagnostics").is_object()&&record.at("diagnostics").dump().size()<=16384,"Invalid discovery diagnostics");
    const auto candidates=record.at("candidates");protocol(candidates.is_array()&&static_cast<std::int64_t>(candidates.size())<=max_entries,"Discovery candidate count exceeds grant");
    for(std::size_t i=0;i<candidates.size();++i) {
      auto candidate=candidates.at(i);candidate.allowed({"url","parent","source","document","depth","path_depth"});
      (void)text_field(candidate,"url",4096);protocol(map_url(text_field(candidate,"parent",4096))==lease.url,"Candidate parent does not match the fetched document");
      const auto source=text_field(candidate,"source",32);protocol(source=="html"||source=="rss"||source=="atom"||source=="sitemap"||source=="sitemap_index","Invalid candidate source");
      protocol(candidate.at("depth").num()==lease.depth+1&&candidate.at("path_depth").num()>=0&&candidate.at("path_depth").num()<=4096,"Invalid discovery depth");
      protocol(candidate.at("document").is_null()||(candidate.at("document").is_string()&&candidate.at("document").str()=="sitemap"&&source=="sitemap_index"&&type=="sitemap_index"),"Only sitemap-index children may become discovery resources");
      protocol((source!="sitemap_index")||(!candidate.at("document").is_null()&&type=="sitemap_index"),"Sitemap-index candidate has invalid purpose");
    }
  }
}
Json WebService::submit_map(const std::string& owner,const Json& args) { return store_.map_submit(owner,args,sites_); }
Json WebService::sites() { return sites_.describe(store_.now()); }
std::optional<MapLease> WebService::lease_map() { return store_.lease_next_map(sites_); }
void WebService::execute_map(const MapLease& lease) {
  std::int64_t requests=-1,bytes=-1;
  try {
    if(!store_.map_lease_live(lease)) { store_.fail_map(lease,"E_LEASE_EXPIRED",0,0);return; }
    const auto* site=sites_.find(lease.site_id);require(site,"E_SITE_REVIEW","Site is no longer registered",403);site->require_review(store_.now());
    const auto remaining=lease.deadline_ms-store_.now();
    if(remaining<=0) { store_.fail_map(lease,"E_DEADLINE",0,0);return; }
    auto parameters=Json::object();parameters.set("url",lease.url);parameters.set("kind",lease.kind);parameters.set("attempt_id",lease.attempt_id);
    parameters.set("site_id",lease.site_id);parameters.set("sites_manifest_sha256",lease.manifest_sha256);parameters.set("deadline_ms",lease.deadline_ms);
    parameters.set("timeout_ms",std::min(lease.timeout_ms,remaining));parameters.set("max_content_bytes",lease.byte_grant);parameters.set("max_requests",std::int64_t{1});
    parameters.set("depth",lease.depth);parameters.set("max_entries",std::int64_t{1000});parameters.set("redirect_hop",lease.redirect_hop);
    if(lease.kind=="robots")parameters.set("robots_origin",lease.origin);else parameters.set("robots",lease.robots_record);
    const auto response=worker_.call("map.fetch",parameters,static_cast<int>(std::min(lease.timeout_ms,remaining)+5000));
    protocol(response.is_object(),"Map worker response must be an object");
    const auto usage=response.at("usage");usage.allowed({"external_requests","content_bytes"});
    const auto measured_requests=usage.at("external_requests").num(),measured_bytes=usage.at("content_bytes").num();
    protocol(measured_requests>=0&&measured_requests<=1&&measured_bytes>=0&&measured_bytes<=lease.byte_grant&&(measured_requests!=0||measured_bytes==0),"Map usage exceeds its durable grant");
    // Parse usage before artifact fields: a malformed result still pays for its
    // known request, while a transport/usage failure retains the entire grant.
    requests=measured_requests;bytes=measured_bytes;
    auto status=text_field(response,"status",32);protocol(status=="ok"||status=="redirect"||status=="error","Invalid map status");
    protocol(text_field(response,"url",4096)==lease.url&&map_url(text_field(response,"final_url",4096))==lease.url,"Single-hop response identity changed");
    const auto http=response.at("http_status").is_null()?std::int64_t{0}:response.at("http_status").num();
    protocol(http>=0&&http<=599&&(status=="error"||http>=100),"Invalid map HTTP status");
    if(status!="error")protocol(requests==1,"Successful map exchange requires one charged request");
    if(status=="redirect") {
      protocol(http==301||http==302||http==303||http==307||http==308,"Invalid redirect HTTP status");(void)map_url(text_field(response,"redirect_url",4096));
    } else if(status=="error") {
      auto error=text_field(response,"error_code",96);protocol(error.starts_with("E_"),"Invalid map error code");
    }
    if(response.has("retry_after_ms"))protocol(response.at("retry_after_ms").num()>=0&&response.at("retry_after_ms").num()<=31536000000LL,"Invalid Retry-After interval");
    if(lease.kind!="robots") {
      if(response.has("robots_decision")) {
        const auto decision=response.at("robots_decision");decision.allowed({"version","product_token","origin","target_url","body_sha256","allowed","reason"});
        protocol(text_field(decision,"version",64)=="bzs-robots-1"&&text_field(decision,"product_token",64)=="BzsBot"&&
                 text_field(decision,"origin",4096)==lease.origin&&text_field(decision,"target_url",4096)==lease.url&&
                 text_field(decision,"body_sha256",64)==lease.robots_record.at("body_sha256").str(),"Robots dispatch decision does not match its grant");
        (void)text_field(decision,"reason",128);
        protocol(decision.at("allowed").flag()||requests==0,"Denied robots decision cannot authorize a request");
        protocol(status=="error"||decision.at("allowed").flag(),"Successful discovery needs robots permission");
      } else protocol(requests==0&&status=="error","Dispatched discovery lacks a bound robots decision");
    }
    if(lease.kind=="robots") {
      if(response.has("robots"))robots_record(response.at("robots"),lease,store_.now());
      else protocol(status!="ok","Successful robots fetch lacks a cache decision");
    } else if(status=="ok")discovery_record(response.at("discovery"),lease,1000);
    store_.complete_map(lease,response,sites_,requests,bytes);
  } catch(const Error& error) {
    store_.fail_map(lease,error.code,requests,bytes);
  } catch(...) { store_.fail_map(lease,"E_INTERNAL",requests,bytes); }
}
}
