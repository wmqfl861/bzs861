// Independent P2-1 field-boundary and public read-cache probe. Offline Worker only.
#include "bzs/web.hpp"
#include "bzs/http_cache.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <iostream>
#include <stdexcept>
#include <string>
using namespace bzs;
int checks=0;
#define CHECK(x) do{++checks;if(!(x))throw std::runtime_error(std::string("failed ")+#x+" at "+std::to_string(__LINE__));}while(false)
template<class F>void rejects(F fn,const std::string& code){try{fn();}catch(const Error& e){CHECK(e.code==code);return;}throw std::runtime_error("expected "+code);}
const std::string url="https://independent-vary.example/page";
constexpr std::int64_t epoch=1800000000000LL;
std::string default_profile(){return sha256("fixed actual Accept-Encoding");}
Json cache(const std::string& vary="Accept-Encoding",const std::string& profile=default_profile(),const std::string& control="max-age=60"){
 auto r=Json::parse(R"({"version":"bzs-http-revalidation-1","url":"","request_profile":"","etag":"\"bound-body\"","last_modified":"","cache_control":"","vary":"","date":"","age":"","expires":""})");
 r.set("url",url);r.set("request_profile",profile);r.set("vary",vary);r.set("cache_control",control);return r;
}
Json freshness(const std::string& vary="Accept-Encoding",const std::string& profile=default_profile(),const std::string& control="max-age=60",std::int64_t now=epoch){
 auto r=Json::parse(R"({"version":"bzs-http-freshness-1","url":"","request_profile":"","cache_control":"","vary":"","date":"","age":"","expires":"","pragma":"","received_at_ms":0,"response_delay_ms":0,"invalid":false})");
 r.set("url",url);r.set("request_profile",profile);r.set("vary",vary);r.set("cache_control",control);r.set("received_at_ms",now);return r;
}
struct Worker:WorkerPort {
 std::int64_t now=epoch;int calls=0,profiles=0;std::string profile=default_profile(),vary="Accept-Encoding",control="max-age=60";bool omit_vary_304=true;
 Json call(const std::string& method,const Json& params,int)override{
  if(method=="fetch.profile"){++profiles;auto r=Json::object();r.set("version","bzs-http-freshness-1");r.set("request_profile",profile);return r;}
  CHECK(method=="fetch.extract");++calls;
  const bool conditional=params.has("conditional")&&params.at("conditional").at("request_profile").str()==profile;
  const auto observed=conditional&&omit_vary_304?std::string{}:vary;
  auto r=conditional?Json::parse(R"({"status":"not_modified","http_status":304,"redirects":[],"usage":{"external_requests":1,"content_bytes":0}})"):
    Json::parse(R"({"status":"ok","http_status":200,"html":"independent retained body","text":"independent retained body","title":"independent fixture","content_type":"text/plain","extractor":"independent/1","extract_status":"ok","index_allowed":true,"links":[],"redirects":[],"warnings":[],"metadata":{},"usage":{"external_requests":1,"content_bytes":25}})");
  r.set("final_url",params.at("url"));r.set("http_cache",cache(observed,profile,control));r.set("http_freshness",freshness(observed,profile,control,now));
  if(conditional)r.set("binding_id",params.at("conditional").at("binding_id"));return r;
 }
};
Json args(const char* mode){auto r=Json::object();r.set("url",url);r.set("freshness",mode);return r;}
int main(int argc,char** argv){try{
 CHECK(argc==2);const auto cases=Json::parse(read_file(argv[1],1024*1024));
 for(std::size_t i=0;i<cases.size();++i){const auto row=cases.at(i);const auto vary=row.at("value").str();bool validator=false,fresh=false;
  try{(void)validate_http_cache(cache(vary),url);validator=true;}catch(const Error& e){CHECK(e.code=="E_WORKER_PROTOCOL");}
  try{fresh=http_freshness(freshness(vary),epoch+1,86400).at("fresh").flag();}catch(const Error& e){CHECK(e.code=="E_WORKER_PROTOCOL");}
  CHECK(validator==row.at("accepted").flag());CHECK(fresh==row.at("accepted").flag());
 }
 auto base=cache(),update=cache("");CHECK(merge_http_cache(base,update).at("vary").str()=="Accept-Encoding");
 CHECK(merge_http_freshness(freshness(),freshness("")).at("vary").str()=="Accept-Encoding");
 for(const auto* name:{"url","request_profile"}){auto changed=cache();changed.set(name,std::string(name)=="url"?url+"/other":sha256("changed"));rejects([&]{merge_http_cache(base,changed);},"E_WORKER_PROTOCOL");}
 for(const auto* control:{"no-store","no-cache","max-age=0","private, max-age=60"})rejects([&]{validate_http_cache(cache("Accept-Encoding",default_profile(),control),url);},"E_WORKER_PROTOCOL");
 CHECK(!http_freshness(freshness("Accept-Encoding",default_profile(),"no-store"),epoch,86400).at("store_allowed").flag());
 CHECK(!http_freshness(freshness("Accept-Encoding",default_profile(),"no-cache"),epoch,86400).at("fresh").flag());
 CHECK(!http_freshness(freshness("Accept-Encoding",default_profile(),"max-age=0"),epoch,86400).at("fresh").flag());
 Worker w;Store store(":memory:",[&]{return w.now;});WebService web(store,w,{});
 const auto first=web.read("alice",args("cache_first"));CHECK(w.calls==1);CHECK(!first.at("from_cache").flag());const auto sid=first.at("snapshot_id").str();
 CHECK(web.read("alice",args("cache_only")).at("from_cache").flag());CHECK(w.calls==1);
 rejects([&]{web.read("bob",args("cache_only"));},"E_NOT_CACHED");CHECK(w.calls==1);
 w.profile=sha256("changed actual Accept-Encoding");rejects([&]{web.read("alice",args("cache_only"));},"E_CACHE_STALE");CHECK(w.calls==1);
 ++w.now;const auto replacement=web.read("alice",args("refresh"));CHECK(w.calls==2);CHECK(replacement.at("snapshot_id").str()==sid);
 ++w.now;const auto renewed=web.read("alice",args("refresh"));CHECK(w.calls==3);CHECK(renewed.at("snapshot_id").str()==sid);CHECK(renewed.at("fetch").at("http_status").num()==304);
 CHECK(store.cached("alice",url)->at("meta").at("http_cache").at("vary").str()=="Accept-Encoding");
 CHECK(store.cached("alice",url)->at("meta").at("http_freshness").at("vary").str()=="Accept-Encoding");
 const auto body=store.snapshot("alice",sid).at("canonical_text").str();w.omit_vary_304=false;w.vary="Accept-Encoding, Cookie";++w.now;
 rejects([&]{web.read("alice",args("refresh"));},"E_WORKER_PROTOCOL");CHECK(w.calls==4);CHECK(store.snapshot("alice",sid).at("canonical_text").str()==body);
 rejects([&]{web.read("alice",args("cache_only"));},"E_CACHE_STALE");CHECK(w.calls==4);
 auto report=Json::object();report.set("checks",static_cast<std::int64_t>(checks));report.set("vary_cases",static_cast<std::int64_t>(cases.size()));report.set("public_cache_same_owner_and_profile",true);report.set("omitted_304_vary_inherited",true);report.set("mixed_vary_invalidates_reuse_without_retry",true);report.set("offline_worker_responses",static_cast<std::int64_t>(w.calls));report.set("external_network_requests",std::int64_t{0});std::cout<<report.dump(true)<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"independent P2-1 probe after "<<checks<<" checks: "<<e.what()<<'\n';return 2;}}
