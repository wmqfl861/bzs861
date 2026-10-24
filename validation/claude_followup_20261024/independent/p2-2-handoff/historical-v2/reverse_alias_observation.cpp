// Observation, not an acceptance test: distinguish existing exact/map_url
// deletion identities from a newly proposed broader percent equivalence.
#define main prepared_probe_main
#include "redirect_independent_probe.cpp"
#undef main
int main() {
  try {
    const std::vector<std::string> aliases={middle,"https://INDEPENDENT-CHAIN.EXAMPLE:443/middle","https://independent-chain.example/%6Diddle","https://INDEPENDENT-CHAIN.EXAMPLE:443/%66inal"};
    for(const auto& alias:aliases) {
      Temporary temp;DeterministicWorker worker;worker.force_full=true;
      Store store(temp.path/"db",[&]{return worker.now;});WebService web(store,worker,{});
      const auto marker=import_alias(store,alias);web.read(owner,request());
      const auto id=store.read_many_submit(owner,{original},"full200-alias",{}).at("job_id").str();
      const auto lease=store.lease_next_read();VERIFY(lease.has_value());
      worker.on_fetch=[&]{store.erase(owner,marker.at("document_id").str());};web.execute_read(*lease);
      const auto job=store.job_get(owner,id),item=store.job_results(owner,id).at("items").at(0);
      Json observed=Json::object();observed.set("deleted_uri",alias);observed.set("actual_chain_middle",middle);observed.set("actual_chain_final",final_uri);
      observed.set("forced_final_status",std::int64_t{200});observed.set("job_state",job.at("state"));observed.set("item_error",item.at("error_code"));
      observed.set("known_requests",item.at("external_requests"));observed.set("known_bytes",item.at("content_bytes"));observed.set("usage_unknown",item.at("usage_unknown"));
      observed.set("had_routed_grant",worker.calls.back().has("redirect_conditional"));std::cout<<observed.canonical()<<std::endl;
      VERIFY(!item.at("usage_unknown").flag());VERIFY(item.at("external_requests").num()==3);
      if(alias.find('%')==std::string::npos) {VERIFY(job.at("state").str()=="failed");VERIFY(item.at("error_code").str()=="E_TARGET_DELETED");}
    }
    std::cout<<"OBSERVATION_COMPLETE cases=4 checks="<<checks<<" external_network_requests=0; percent outcomes are observations, not acceptance assertions"<<std::endl;
    return 0;
  } catch(const std::exception& error) {std::cout<<"OBSERVATION_ERROR "<<error.what()<<std::endl;return 2;}
}
