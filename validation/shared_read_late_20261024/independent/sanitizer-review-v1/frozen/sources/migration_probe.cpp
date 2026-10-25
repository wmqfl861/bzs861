// Creates all original business state through the real schema14 Core API.
#include "probe_support.hpp"
using namespace independent;
namespace {
const std::string host="https://independent-shared.example/";
void create(const std::filesystem::path& db,const std::filesystem::path& backup) {
  Worker worker;Store store(db,[&]{return worker.now;});WebService web(store,worker,{});
  VERIFY(scalar(store.raw_handle(),"PRAGMA user_version")==14);
  auto receipt=Json::object(),jobs=Json::object(),leases=Json::object();
  const auto document=store.ingest("alice",{host+"retained","Independent retained body.","text/plain","Retained"});
  receipt.set("document",document);
  receipt.set("evidence",store.evidence("alice",document.at("snapshot_id").str(),0,11));
  const auto tombstone=store.ingest("alice",{host+"tombstone","Deleted old body.","text/plain","Deleted"});
  store.erase("alice",tombstone.at("document_id").str());receipt.set("tombstone",tombstone);
  store.ingest("bob",{host+"retained","Other owner retained body.","text/plain","Other"});
  const auto submit=[&](const std::string& label,const std::string& url,const std::string& owner="alice") {
    const auto task=web.submit_reads(owner,parameters(url,label));jobs.set(label,task.at("job_id"));return task.at("job_id").str();
  };
  submit("completed-payer",host+"completed");submit("completed-member",host+"completed");
  auto complete=store.lease_next_read();VERIFY(complete);web.execute_read(*complete);
  VERIFY(item(store,jobs.at("completed-member").str()).at("state").str()=="succeeded");
  const auto paused=submit("paused",host+"paused");store.job_control("alice",paused,"pause");
  const auto cancelled=submit("cancelled",host+"cancelled");store.job_control("alice",cancelled,"cancel");
  submit("revoked",host+"revoked","revoked-owner");store.revoke_principal("independent-admin","revoked-owner");
  submit("single",host+"single");auto single=store.lease_next_read();VERIFY(single);leases.set("single",encode(*single));
  submit("group-payer",host+"group");submit("group-member",host+"group");
  auto group=store.lease_next_read();VERIFY(group);leases.set("group",encode(*group));
  submit("cancelled-payer",host+"payer-cancelled");submit("surviving-member",host+"payer-cancelled");
  auto stopped=store.lease_next_read();VERIFY(stopped);leases.set("cancelled-payer",encode(*stopped));
  store.job_control("alice",stopped->job_id,"cancel");VERIFY(store.read_lease_live(*stopped));
  submit("other-owner",host+"single","bob");auto other=store.lease_next_read();VERIFY(other);VERIFY(other->owner=="bob");leases.set("other-owner",encode(*other));
  submit("pending",host+"pending");
  receipt.set("jobs",jobs);receipt.set("leases",leases);receipt.set("clock_ms",worker.now);
  receipt.set("known_fixture_worker_calls",static_cast<std::int64_t>(worker.requests.size()));
  receipt.set("external_network_requests",std::int64_t{0});receipt.set("schema",std::int64_t{14});
  receipt.set("backup",store.backup(backup));receipt.set("checks",static_cast<std::int64_t>(checks));
  std::cout<<receipt.canonical()<<std::endl;
}
void verify_legacy(const std::filesystem::path& db,const std::filesystem::path& saved_path) {
  const auto saved=load_json(saved_path);Worker worker;worker.now=saved.at("clock_ms").num();
  Store store(db,[&]{return worker.now;});WebService web(store,worker,{});
  VERIFY(scalar(store.raw_handle(),"PRAGMA user_version")==15);
  VERIFY(scalar(store.raw_handle(),"SELECT count(*) FROM read_job_items WHERE dispatch_timeout_ms<>0 OR sharing_closed<>1")==0);
  VERIFY(store.snapshot("alice",saved.at("document").at("snapshot_id").str()).at("canonical_text").str()=="Independent retained body.");
  VERIFY(store.evidence_get("alice",saved.at("evidence").at("evidence_id").str()).at("quote").str()=="Independent");
  VERIFY(store.job_get("alice",saved.at("jobs").at("completed-payer").str()).at("usage").at("external_requests").num()==1);
  VERIFY(store.job_get("alice",saved.at("jobs").at("completed-member").str()).at("usage").at("external_requests").num()==0);
  expect([&]{store.job_get("revoked-owner",saved.at("jobs").at("revoked").str());},"E_PRINCIPAL_REVOKED");
  for(const auto& name:{"single","group","cancelled-payer","other-owner"})VERIFY(store.read_lease_live(decode(saved.at("leases").at(name))));
  auto replay=web.submit_reads("alice",parameters(host+"single","single"));VERIFY(replay.at("reused").flag());
  VERIFY(replay.at("job_id").str()==saved.at("jobs").at("single").str());
  VERIFY(worker.requests.empty());std::cout<<"VERIFY_LEGACY_PASS checks="<<checks<<std::endl;
}
void settle_legacy(const std::filesystem::path& db,const std::filesystem::path& saved_path) {
  const auto saved=load_json(saved_path);Worker worker;worker.now=saved.at("clock_ms").num();
  Store store(db,[&]{return worker.now;});Store other(db,[&]{return worker.now;});WebService web(store,worker,{}),other_web(other,worker,{});
  VERIFY(scalar(store.raw_handle(),"PRAGMA user_version")==15);
  auto late=Json::array();
  for(const auto& name:{"single","group","cancelled-payer"}) {
    const auto lease=decode(saved.at("leases").at(name));
    const auto joined=other_web.submit_reads("alice",parameters(lease.url,std::string("legacy-late-")+name));
    VERIFY(joined.at("state").str()=="queued");late.push(joined.at("job_id"));
    VERIFY(scalar(store.raw_handle(),"SELECT sharing_closed FROM read_job_items WHERE job_id=?",lease.job_id)==1);
  }
  for(const auto& name:{"single","group","cancelled-payer","other-owner"})web.execute_read(decode(saved.at("leases").at(name)));
  VERIFY(worker.requests.size()==4);
  for(const auto& name:{"single","group-payer","group-member","surviving-member"})
    VERIFY(item(store,saved.at("jobs").at(name).str()).at("state").str()=="succeeded");
  VERIFY(store.job_get("alice",saved.at("jobs").at("cancelled-payer").str()).at("state").str()=="cancelled");
  VERIFY(!item(store,saved.at("jobs").at("cancelled-payer").str()).has("receipt"));
  VERIFY(store.job_get("alice",saved.at("jobs").at("cancelled-payer").str()).at("usage").at("external_requests").num()==1);
  VERIFY(store.job_get("alice",saved.at("jobs").at("surviving-member").str()).at("usage").at("external_requests").num()==0);
  for(std::size_t i=0;i<late.size();++i)VERIFY(store.job_get("alice",late.at(i).str()).at("state").str()=="queued");
  const auto fresh=store.lease_next_read();VERIFY(fresh);VERIFY(fresh->job_id==saved.at("jobs").at("pending").str());
  VERIFY(scalar(store.raw_handle(),"SELECT dispatch_timeout_ms FROM read_job_items WHERE job_id=?",fresh->job_id)==fresh->timeout_ms);
  VERIFY(scalar(store.raw_handle(),"SELECT sharing_closed FROM read_job_items WHERE job_id=?",fresh->job_id)==0);
  const auto member=other_web.submit_reads("alice",parameters(fresh->url,"new-attempt-member"));
  VERIFY(member.at("state").str()=="running");VERIFY(item(other,member.at("job_id").str()).at("shared_read").at("attempt_id").str()==fresh->attempt_id);
  web.execute_read(*fresh);VERIFY(worker.requests.size()==5);
  std::cout<<"SETTLE_LEGACY_AND_NEW_OPEN_PASS checks="<<checks<<std::endl;
}
}
int main(int argc,char** argv) {
  try {
    VERIFY(argc>=3);const std::string mode=argv[1];const std::filesystem::path db=argv[2];
    if(mode=="create") { VERIFY(argc==4);create(db,argv[3]); }
    else if(mode=="open") { Store store(db,[]{return std::int64_t{1000000};});std::cout<<"OPEN_PASS version="<<scalar(store.raw_handle(),"PRAGMA user_version")<<std::endl; }
    else if(mode=="expect-error") { VERIFY(argc==4);expect([&]{Store store(db,[]{return std::int64_t{1000000};});},argv[3]);std::cout<<"EXPECTED_ERROR "<<argv[3]<<std::endl; }
    else if(mode=="verify") { VERIFY(argc==4);verify_legacy(db,argv[3]); }
    else if(mode=="settle") { VERIFY(argc==4);settle_legacy(db,argv[3]); }
    else throw std::runtime_error("unknown mode");
    return 0;
  } catch(const std::exception& error) { std::cerr<<"PROBE_FAILURE "<<error.what()<<std::endl;return 1; }
}
