// Additive review after the original Worker aggregate-contract regression.
// Real Store/WebService; completed Worker protocol responses, no HTTP sockets.
// The original independently frozen scope probe remains byte-for-byte intact.
#define main frozen_publication_scope_main
#include "/tmp/bzs-p2-redirect-review-revocation-v1/redirect_publication_scope_probe.cpp"
#undef main

namespace {
Json cloned(const Json& value) { return Json::parse(value.canonical(),8*1024*1024); }
Json excluding(const Json& value,const std::string& key) {
  auto out=Json::object();for(const auto& field:value.keys())if(field!=key)out.set(field,value.at(field));return out;
}
Json final_record(const std::string& target,const Json& policy) {
  auto record=Json::object();record.set("version","bzs-http-representation-publication-1");
  record.set("url",target);record.set("publication",cloned(policy));return record;
}
Json links_only(const Json& policy) {
  auto out=cloned(policy);out.set("snippet_allowed",false);out.set("snippet_text","");out.set("snippet_title","");
  auto diagnostic=Json::array();diagnostic.push(Json::string("site_links_only"));out.set("diagnostics",diagnostic);return out;
}
struct AggregateWorker:Worker {
  Store* store=nullptr;
  std::string mode="cacheable",corruption;
  bool distinct_policy=false,inflight_a=false,final_denial=false;
  Json last_routed_response=Json::object(),last_params=Json::object();
  std::string target() const {
    const std::string prefix="https://route-review.example/";
    return mode=="metadata-omission"?prefix+std::string(3500-prefix.size(),'x'):final_url;
  }
  Json call(const std::string& method,const Json& parameters,int timeout) override {
    auto response=Worker::call(method,parameters,timeout);
    if(method=="fetch.profile")return response;
    last_params=cloned(parameters);
    const bool routed=parameters.at("url").str()==original;
    const auto destination=target();
    response.set("final_url",destination);
    response.at("http_freshness").set("url",destination);
    response.at("http_cache").set("url",destination);
    if(routed) {
      auto redirects=Json::array();redirects.push(Json::string(destination));response.set("redirects",redirects);
      response.at("http_redirect_chain").set("final_url",destination);
      response.at("http_redirect_chain").at("hops").at(std::size_t{0}).set("target_url",destination);
      if(mode=="no-cache"||mode=="ttl-zero") {
        auto hop_policy=policy(original);hop_policy.set("cache_control",mode=="no-cache"?"no-cache, max-age=60":"max-age=0");
        response.at("http_redirect_chain").at("hops").at(std::size_t{0}).set("http_freshness",hop_policy);
        response.at("http_redirect_observations").at(std::size_t{0}).set("http_freshness",hop_policy);
      }
    }
    if(mode=="metadata-omission") {
      response.at("http_cache").set("etag",'"'+std::string(1022,'v')+'"');
      const auto control=std::string("max-age=60")+std::string(200,' ');
      response.at("http_cache").set("cache_control",control);
      response.at("http_freshness").set("cache_control",control);
    }
    if(routed&&response.at("status").str()=="ok") {
      if(distinct_policy) {
        const auto final_policy=cloned(response.at("publication"));
        response.set("publication",links_only(final_policy));
        response.set("representation_publication",final_record(destination,final_policy));
      }
      if(final_denial) {
        response.set("publication",denied());response.set("index_allowed",false);
        response.set("publication_observation",denied());
        response.set("representation_publication",final_record(destination,denied()));
      }
    }
    if(!corruption.empty()) {
      auto allowing=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"scopeprobe retained body","snippet_title":"scopeprobe","directives":[],"diagnostics":[]})");
      if(!response.has("representation_publication"))response.set("representation_publication",final_record(destination,allowing));
      auto record=response.at("representation_publication");
      if(corruption=="wrong-version")record.set("version","unknown-final-version");
      else if(corruption=="wrong-url")record.set("url",original);
      else if(corruption=="extra-field")record.set("extra",true);
      else if(corruption=="missing-field")response.set("representation_publication",excluding(record,"url"));
      else if(corruption=="wrong-type")response.set("representation_publication",Json::string("not-an-object"));
      else if(corruption=="nested-version")record.at("publication").set("version","unknown-publication-version");
      else if(corruption=="without-proof")response=excluding(response,"http_redirect_chain");
      else if(corruption=="without-receipt")response=excluding(response,"http_redirect_observations");
      else if(corruption=="wrong-usage")response.at("usage").set("external_requests",std::int64_t{1});
      else if(corruption=="status203") {response.set("http_status",std::int64_t{203});response=excluding(response,"http_cache");}
      else if(corruption=="missing-aggregate")response=excluding(response,"publication");
      else if(corruption=="aggregate-relaxes-final") {
        response.set("publication",allowing);response.set("index_allowed",true);record.set("publication",denied());
      } else if(corruption=="final-relaxes-observation") {
        response.set("publication",denied());response.set("index_allowed",false);
        response.set("publication_observation",denied());record.set("publication",allowing);
      } else require(corruption=="on304"||corruption=="direct","E_REVIEW","Unknown corruption case");
    }
    if(routed&&inflight_a) {
      require(store!=nullptr,"E_REVIEW","Missing Store for in-flight policy change");inflight_a=false;
      store->observe_web_publication(owner,original,original,denied(),store->publication_fence(owner,original));
    }
    if(routed)last_routed_response=cloned(response);
    return response;
  }
};
void assert_c_visible(Store& store,const Json& session,const std::string& target) {
  const auto hits=store.page(owner,session.at("session_id").str(),"",20).at("hits");
  require(hits.size()==1&&hits.at(std::size_t{0}).at("url").str()==target,
          "E_REVIEW","A's policy history changed independent C result membership");
  require(!hits.at(std::size_t{0}).at("snippet").str().empty()&&hits.at(std::size_t{0}).at("title").str()=="scopeprobe",
          "E_REVIEW","A's aggregate site ceiling suppressed independent C snippet/title");
}
void scope_case(const std::filesystem::path& path,const std::string& mode,bool distinct) {
  Store store(path,[]{return now;});AggregateWorker worker;worker.store=&store;worker.mode=mode;
  WebService web(store,worker,{});const auto target=worker.target();
  const auto c=web.read(owner,parameters(target));const auto frozen=freeze(store);
  worker.distinct_policy=distinct;worker.inflight_a=true;
  const auto a=web.read(owner,parameters(original));
  require(!a.at("indexed").flag(),"E_REVIEW","In-flight A noindex did not seal A");
  assert_c_visible(store,freeze(store),target);assert_c_visible(store,frozen,target);
  require(web.read(owner,parameters(target,"cache_only")).at("indexed").flag(),"E_REVIEW","A history tightened independent C cache");
  const auto meta=store.cached(owner,original)->at("meta");
  const auto response=worker.last_routed_response;
  if(mode=="cacheable") {
    require(meta.has("http_redirect_chain"),"E_REVIEW","Ordinary cacheable proof disappeared");
    require(!web.read(owner,parameters(original,"cache_only")).at("indexed").flag(),"E_REVIEW","Cache hit bypassed current A noindex");
  } else {
    require(!meta.has("http_redirect_chain")&&!store.revalidation_base(owner,original).has_value(),
            "E_REVIEW","Publication-only identity granted cache/conditional eligibility");
    std::string code;try {(void)web.read(owner,parameters(original,"cache_only"));}catch(const Error& error){code=error.code;}
    require(code=="E_CACHE_STALE","E_REVIEW","Uncacheable route unexpectedly satisfied cache_only");
  }
  if(mode=="metadata-omission") {
    auto accounted=Json::object();for(const auto* key:{"http_cache","http_freshness","http_redirect_chain"})accounted.set(key,response.at(key));
    require(response.at("http_redirect_chain").canonical().size()<=8192&&accounted.canonical().size()>16384,
            "E_REVIEW","Probe did not reach a valid proof omitted only by aggregate metadata allowance");
    std::cout<<"BOUNDARY proof="<<response.at("http_redirect_chain").canonical().size()<<" combined="<<accounted.canonical().size()<<'\n';
  }
  store.observe_web_publication(owner,target,target,denied(),store.publication_fence(owner,target));
  require(urls(store,freeze(store)).empty()&&urls(store,frozen).empty(),"E_REVIEW","C's own final restriction was ignored");
  require(!web.read(owner,parameters(target,"cache_only")).at("indexed").flag(),"E_REVIEW","C's own cache projection failed to tighten");
  require(store.snapshot(owner,a.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body"&&
          store.snapshot(owner,c.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body",
          "E_REVIEW","Policy scope change removed historical evidence");
  require(worker.reads==2,"E_REVIEW","Scope proof caused extra fetch or retry");
}
void reject_case(const std::filesystem::path& path,const std::string& corruption) {
  Store store(path,[]{return now;});AggregateWorker worker;WebService web(store,worker,{});
  const auto target=corruption=="direct"?final_url:original;
  const auto before=web.read(owner,parameters(target));
  worker.corruption=corruption;worker.distinct_policy=corruption!="direct";worker.refresh=corruption=="on304";
  std::string code;try {(void)web.read(owner,parameters(target));}catch(const Error& error){code=error.code;}
  require(code=="E_WORKER_PROTOCOL"||code=="E_SCHEMA","E_REVIEW","Malformed final publication field was accepted or failed for an unrelated reason: "+code);
  require(store.cached(owner,target)->at("snapshot_id").str()==before.at("snapshot_id").str(),
          "E_REVIEW","Malformed response committed a replacement snapshot");
  require(store.snapshot(owner,before.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body",
          "E_REVIEW","Malformed response removed retained evidence");
  require(worker.reads==2,"E_REVIEW","Malformed response triggered an extra retry");
  std::cout<<"REJECT "<<corruption<<" code="<<code<<'\n';
}
void final_denial_case(const std::filesystem::path& path) {
  Store store(path,[]{return now;});AggregateWorker worker;WebService web(store,worker,{});
  const auto c=web.read(owner,parameters(final_url));const auto frozen=freeze(store);
  worker.final_denial=true;const auto a=web.read(owner,parameters(original));
  require(!a.at("indexed").flag()&&urls(store,freeze(store)).empty()&&urls(store,frozen).empty(),
          "E_REVIEW","Explicit actual-final noindex failed to restrict both A and independent C");
  require(!web.read(owner,parameters(final_url,"cache_only")).at("indexed").flag(),"E_REVIEW","Actual-final noindex did not project into C cache");
  require(store.snapshot(owner,c.at("snapshot_id").str()).at("canonical_text").str()=="scopeprobe retained body"&&worker.reads==2,
          "E_REVIEW","Final restriction changed evidence or origin-call accounting");
}
}
int main() {
  const auto root=std::filesystem::temp_directory_path()/("bzs-aggregate-final-independent-"+random_id());
  int total=0,failed=0;
  const auto run=[&](const std::string& name,const auto& action) {
    ++total;try {action(root/name);std::cout<<"PASS "<<name<<'\n';}
    catch(const std::exception& error){++failed;std::cout<<"FAIL "<<name<<": "<<error.what()<<'\n';}
  };
  for(const std::string mode:{"cacheable","no-cache","ttl-zero","metadata-omission"})for(bool distinct:{false,true}) {
    const auto name=mode+(distinct?"-explicit-final":"-equal-without-wrapper");
    run(name,[&](const auto& path){scope_case(path,mode,distinct);});
  }
  for(const std::string corruption:{"wrong-version","wrong-url","extra-field","missing-field","wrong-type","nested-version",
        "without-proof","without-receipt","wrong-usage","status203","missing-aggregate","aggregate-relaxes-final",
        "final-relaxes-observation","on304","direct"})run("reject-"+corruption,[&](const auto& path){reject_case(path,corruption);});
  run("actual-final-denial",final_denial_case);
  std::filesystem::remove_all(root);
  std::cout<<"SUMMARY "<<(total-failed)<<'/'<<total<<" passed\n";return failed?1:0;
}
