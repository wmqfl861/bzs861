// A valid stored proof can be smaller than its optional validator grant.
// Reuse the frozen protocol fixture without modifying its publication probe.
#define main frozen_publication_scope_main
#include "/tmp/bzs-p2-redirect-review-revocation-v1/redirect_publication_scope_probe.cpp"
#undef main

struct LongValidatorWorker:Worker {
  const std::string target=std::string("https://route-review.example/")+std::string(3500-std::string("https://route-review.example/").size(),'x');
  Json call(const std::string& method,const Json& p,int timeout) override {
    auto response=Worker::call(method,p,timeout);
    if(method=="fetch.profile")return response;
    require(!p.has("conditional")&&!p.has("redirect_conditional"),"E_REVIEW","Oversized optional condition was dispatched instead of falling back to a full GET");
    response.set("final_url",target);response.set("http_freshness",policy(target));
    auto cache=validators();cache.set("url",target);cache.set("etag",'"'+std::string(1022,'v')+'"');response.set("http_cache",cache);
    auto route=Json::array();route.push(Json::string(target));response.set("redirects",route);
    response.at("http_redirect_chain").set("final_url",target);
    response.at("http_redirect_chain").at("hops").at(std::size_t{0}).set("target_url",target);
    return response;
  }
};

int main() {
  const auto root=std::filesystem::temp_directory_path()/("bzs-condition-size-"+random_id());int result=1;
  try {
    Store store(root,[]{return now;});LongValidatorWorker worker;WebService web(store,worker,{});
    web.read(owner,parameters(original));
    const auto meta=store.cached(owner,original)->at("meta");
    require(meta.has("http_redirect_chain"),"E_REVIEW","Initial valid proof was unexpectedly discarded");
    auto accounted=Json::object();for(const auto* key:{"http_cache","http_freshness","http_redirect_chain"})accounted.set(key,meta.at(key));
    const auto base=store.revalidation_base(owner,original);
    auto conditional=Json::object();for(const auto* key:{"version","url","request_profile","etag","last_modified"})conditional.set(key,meta.at("http_cache").at(key));
    conditional.set("binding_id",sha256("independent-size-review"));
    auto route=Json::array();route.push(Json::string(worker.target));auto grant=Json::object();
    grant.set("version","bzs-http-redirect-conditional-1");grant.set("request_url",original);grant.set("redirects",route);grant.set("conditional",conditional);
    std::cout<<"proof_bytes="<<meta.at("http_redirect_chain").canonical().size()<<" metadata_bytes="<<accounted.canonical().size()<<" conditional_bytes="<<grant.canonical().size()<<" base_supplied="<<base.has_value()<<'\n';
    require(meta.at("http_redirect_chain").canonical().size()<=8192&&accounted.canonical().size()<=16384&&grant.canonical().size()>8192,
            "E_REVIEW","Probe did not reach the intended accepted-proof/oversized-grant boundary");
    bool completed=false;
    try {const auto refreshed=web.read(owner,parameters(original));completed=!refreshed.at("from_cache").flag();}
    catch(const Error& error) {std::cout<<"refresh="<<error.code<<" message="<<error.what()<<'\n';}
    std::cout<<"origin_calls="<<worker.reads<<'\n';
    require(completed&&worker.reads==2,"E_REVIEW","Optional oversized conditional blocked an otherwise authorized full refresh");
    std::cout<<"PASS oversized optional route grant fell back to one full authorized GET\n";result=0;
  } catch(const std::exception& error) {std::cout<<"FAIL "<<error.what()<<'\n';}
  std::filesystem::remove_all(root);return result;
}
