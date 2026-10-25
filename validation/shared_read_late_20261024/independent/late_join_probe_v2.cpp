#include "probe_support.hpp"
using namespace independent;
namespace {
const std::string host="https://independent-shared.example/";
void flag(Store& store,const std::string& id,std::int64_t expected) {
  if(scalar(store.raw_handle(),"PRAGMA user_version")>=15)
    VERIFY(scalar(store.raw_handle(),"SELECT sharing_closed FROM read_job_items WHERE job_id=?",id)==expected);
}
void singleton(const std::filesystem::path& db) {
  Worker worker;Store a(db,[&]{return worker.now;}),b(db,[&]{return worker.now;});WebService first(a,worker,{}),second(b,worker,{});
  const auto payer=first.submit_reads("alice",parameters(host+"singleton","payer")).at("job_id").str();
  const auto lease=a.lease_next_read();VERIFY(lease);Json late,replay;
  worker.response=[&](const Json& request) {
    late=second.submit_reads("alice",parameters(lease->url,"late"));
    replay=second.submit_reads("alice",parameters(lease->url,"late"));return Worker::ok(request);
  };
  first.execute_read(*lease);VERIFY(late.at("state").str()=="running");VERIFY(replay.at("reused").flag());
  VERIFY(late.at("job_id").str()==replay.at("job_id").str());
  const auto member=late.at("job_id").str();VERIFY(worker.requests.size()==1);
  VERIFY(item(a,payer).at("receipt").at("snapshot_id").str()==item(b,member).at("receipt").at("snapshot_id").str());
  VERIFY(item(b,member).at("shared_read").at("members").num()==2);
  VERIFY(a.job_get("alice",payer).at("budget").at("charged_requests").num()==1);
  VERIFY(b.job_get("alice",member).at("budget").at("charged_requests").num()==0);
  VERIFY(second.submit_reads("alice",parameters(lease->url,"late")).at("reused").flag());VERIFY(!a.lease_next_read());
}
void clipped_timeout(const std::filesystem::path& db) {
  Worker worker;Store a(db,[&]{return worker.now;}),b(db,[&]{return worker.now;});WebService first(a,worker,{}),second(b,worker,{});
  const auto payer=first.submit_reads("alice",parameters(host+"clipped","payer",10000,20000)).at("job_id").str();
  const auto lease=a.lease_next_read();VERIFY(lease);VERIFY(lease->timeout_ms==10000);Json late;
  worker.response=[&](const Json& request) {
    worker.now+=1000;
    late=second.submit_reads("alice",parameters(lease->url,"late",20000,10000));return Worker::ok(request);
  };
  first.execute_read(*lease);VERIFY(late.at("state").str()=="running");
  VERIFY(worker.requests.size()==1&&worker.requests.front().at("timeout_ms").num()==10000);
  VERIFY(item(b,late.at("job_id").str()).at("state").str()=="succeeded");
  VERIFY(a.job_get("alice",payer).at("usage").at("external_requests").num()==1);
}
void cancelled_payer_remains_anchor(const std::filesystem::path& db) {
  Worker worker;Store a(db,[&]{return worker.now;}),b(db,[&]{return worker.now;});WebService first(a,worker,{}),second(b,worker,{});
  const auto payer=first.submit_reads("alice",parameters(host+"cancelled-payer","payer")).at("job_id").str();
  const auto original=first.submit_reads("alice",parameters(host+"cancelled-payer","original")).at("job_id").str();
  const auto lease=a.lease_next_read();VERIFY(lease);Json late;
  worker.response=[&](const Json& request) {
    stop(a,first,payer);late=second.submit_reads("alice",parameters(lease->url,"late"));return Worker::ok(request);
  };
  first.execute_read(*lease);VERIFY(late.at("state").str()=="running");VERIFY(worker.cancellations.empty());
  VERIFY(a.job_get("alice",payer).at("state").str()=="cancelled");VERIFY(!item(a,payer).has("receipt"));
  VERIFY(item(a,original).at("receipt").at("snapshot_id").str()==item(b,late.at("job_id").str()).at("receipt").at("snapshot_id").str());
  VERIFY(item(b,late.at("job_id").str()).at("shared_read").at("payer_job_id").str()==payer);
  VERIFY(item(b,late.at("job_id").str()).at("shared_read").at("members").num()==3);
  VERIFY(a.job_get("alice",payer).at("budget").at("charged_requests").num()==1);
  VERIFY(b.job_get("alice",late.at("job_id").str()).at("budget").at("charged_requests").num()==0);
}
void closed_after_clock_rollback(const std::filesystem::path& db) {
  Worker worker;Store a(db,[&]{return worker.now;}),b(db,[&]{return worker.now;});WebService first(a,worker,{}),second(b,worker,{});
  const auto payer=first.submit_reads("alice",parameters(host+"closed","payer")).at("job_id").str();
  const auto member=first.submit_reads("alice",parameters(host+"closed","member")).at("job_id").str();
  const auto lease=a.lease_next_read();VERIFY(lease);
  const auto until=a.job_get("alice",payer).at("lease_until_ms").num();Json late;bool observed_dead=false;std::string cancelled;
  worker.response=[&](const Json& request) {
    const auto original_now=worker.now;worker.now=until+1;
    observed_dead=!a.read_lease_live(*lease);cancelled=a.active_read_attempt("alice",payer);first.cancel_read_attempt(cancelled);flag(a,payer,1);
    worker.now=original_now;late=second.submit_reads("alice",parameters(lease->url,"late"));return Worker::ok(request);
  };
  first.execute_read(*lease);VERIFY(observed_dead);VERIFY(cancelled==lease->attempt_id);VERIFY(worker.cancellations.size()==1);
  VERIFY(late.at("state").str()=="queued");VERIFY(!item(b,late.at("job_id").str()).has("shared_read"));
  // Admission closure itself does not alter old lease/accounting semantics.
  VERIFY(item(a,payer).at("state").str()=="succeeded");VERIFY(item(a,member).at("state").str()=="succeeded");
  VERIFY(a.job_get("alice",payer).at("budget").at("charged_requests").num()==1);VERIFY(worker.requests.size()==1);
}
void stale_lease_cannot_close_retry(const std::filesystem::path& db) {
  Worker worker;Store a(db,[&]{return worker.now;}),b(db,[&]{return worker.now;});WebService first(a,worker,{}),second(b,worker,{});
  const auto payer=first.submit_reads("alice",parameters(host+"retry","payer")).at("job_id").str();
  const auto old=a.lease_next_read();VERIFY(old);
  worker.response=[](const Json& request){auto response=Worker::ok(request);response.set("status","http_error");return response;};
  first.execute_read(*old);VERIFY(a.job_get("alice",payer).at("state").str()=="failed");
  a.job_control("alice",payer,"retry_failed");const auto next=a.lease_next_read();VERIFY(next);VERIFY(next->attempt_id!=old->attempt_id);
  VERIFY(!a.read_lease_live(*old));flag(a,payer,0);Json late;
  worker.response=[&](const Json& request){late=second.submit_reads("alice",parameters(next->url,"late"));return Worker::ok(request);};
  first.execute_read(*next);VERIFY(late.at("state").str()=="running");
  VERIFY(item(b,late.at("job_id").str()).at("shared_read").at("attempt_id").str()==next->attempt_id);
  VERIFY(worker.requests.size()==2);VERIFY(a.job_get("alice",payer).at("budget").at("charged_requests").num()==2);
  VERIFY(b.job_get("alice",late.at("job_id").str()).at("budget").at("charged_requests").num()==0);
}
void incompatible_inputs_remain_queued(const std::filesystem::path& directory) {
  for(const std::string difference:{"owner","uri","timeout","requests","bytes","order","snapshot","publication"}) {
    Worker worker;Store a(directory.string()+"-"+difference,[&]{return worker.now;}),b(directory.string()+"-"+difference,[&]{return worker.now;});
    WebService first(a,worker,{}),second(b,worker,{});const auto url=host+"boundary";
    first.submit_reads("alice",parameters(url,"payer"));const auto lease=a.lease_next_read();VERIFY(lease);Json late;
    worker.response=[&](const Json& request) {
      auto params=parameters(url,"late");
      if(difference=="uri")params=parameters(host+"%62oundary","late");
      if(difference=="timeout")params.set("per_page_timeout_ms",std::int64_t{1000});
      if(difference=="requests")params.set("max_requests",std::int64_t{1});
      if(difference=="bytes")params.set("max_bytes",std::int64_t{100});
      if(difference=="order") {auto values=Json::array();values.push(Json::string(host+"first"));values.push(Json::string(url));params.set("urls",values);}
      if(difference=="snapshot")a.ingest("alice",{url,"Concurrent explicit import.","text/plain","New snapshot"});
      if(difference=="publication") {
        const auto denied=Json::parse(R"({"version":"bzs-publication-1","index_allowed":false,"snippet_allowed":false,"follow_allowed":true,"snippet_text":"","snippet_title":"","directives":["noindex"],"diagnostics":[]})");
        const auto fence=a.publication_fence("alice",url);a.observe_web_publication("alice",url,url,denied,fence);
        VERIFY(a.publication_fence("alice",url).at("publication_sequence").num()>fence.at("publication_sequence").num());
      }
      late=second.submit_reads(difference=="owner"?"bob":"alice",params);return Worker::ok(request);
    };
    first.execute_read(*lease);VERIFY(late.at("state").str()=="queued");
    VERIFY(!item(b,late.at("job_id").str(),difference=="owner"?"bob":"alice").has("shared_read"));
    VERIFY(worker.requests.size()==1);
  }
}
}
int main(int argc,char** argv) {
  if(argc!=2)return 2;const std::filesystem::path root=argv[1];std::filesystem::create_directories(root);
  std::size_t failures=0;
  const std::vector<std::pair<std::string,std::function<void(const std::filesystem::path&)>>> cases={
    {"singleton_late_join_and_idempotency",singleton},{"captured_clipped_timeout_positive",clipped_timeout},
    {"cancelled_payer_with_live_member_admits_late",cancelled_payer_remains_anchor},
    {"expiry_close_survives_clock_rollback",closed_after_clock_rollback},
    {"stale_lease_cas_cannot_close_new_attempt",stale_lease_cannot_close_retry},
    {"eight_incompatible_input_boundaries",incompatible_inputs_remain_queued}};
  for(const auto& [name,run]:cases) {
    try {run(root/(name+".sqlite"));std::cout<<"PASS "<<name<<std::endl;}
    catch(const std::exception& error){++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<std::endl;}
  }
  std::cout<<"INDEPENDENT_CASES "<<cases.size()<<" CHECKS "<<checks<<" FAILURES "<<failures<<std::endl;
  return static_cast<int>(failures);
}
