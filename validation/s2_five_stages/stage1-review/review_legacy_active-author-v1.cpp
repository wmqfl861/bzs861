#define main original_review_unused_main
#include "review_refresh_order-baseline.cpp"
#undef main
#include <fstream>
#include <sqlite3.h>

namespace {
Json encode_lease(const ReadLease& lease){auto j=Json::object();
  j.set("job_id",lease.job_id);j.set("owner",lease.owner);j.set("url",lease.url);j.set("attempt_id",lease.attempt_id);
  j.set("epoch",lease.epoch);j.set("input_index",lease.input_index);j.set("deletion_generation",lease.deletion_generation);j.set("request_grant",lease.request_grant);j.set("byte_grant",lease.byte_grant);j.set("timeout_ms",lease.timeout_ms);j.set("deadline_ms",lease.deadline_ms);return j;}
ReadLease decode_lease(const Json& j){return {j.at("job_id").str(),j.at("owner").str(),j.at("url").str(),j.at("attempt_id").str(),j.at("epoch").num(),j.at("input_index").num(),j.at("deletion_generation").num(),j.at("request_grant").num(),j.at("byte_grant").num(),j.at("timeout_ms").num(),j.at("deadline_ms").num()};}
int schema_version(Store& s){sqlite3_stmt* p=nullptr;check(sqlite3_prepare_v2(s.raw_handle(),"PRAGMA user_version",-1,&p,nullptr)==SQLITE_OK,"schema read failed");check(sqlite3_step(p)==SQLITE_ROW,"schema row failed");int v=sqlite3_column_int(p,0);sqlite3_finalize(p);return v;}
}
int main(int argc,char** argv){try{
  check(argc==5,"usage: review_legacy_active create|upgrade noindex|nosnippet db metadata");bool snippet=std::string(argv[2])=="nosnippet";
  Store store(argv[3],[]{return std::int64_t{1700000000000};});Reader old_reader,new_reader;old_reader.final_url=b;old_reader.redirects.push(Json::string(b));WebService old_web(store,old_reader,{}),new_web(store,new_reader,{});
  if(std::string(argv[1])=="create") {
    check(schema_version(store)==7,"generator is not actual published schema 7");auto original=old_web.read("alice",args(a));auto original_session=freeze(store);auto evidence=store.evidence("alice",original.at("snapshot_id").str(),0,6);
    auto job=store.read_many_submit("alice",{a},"legacy-active",{});auto lease=store.lease_next_read();check(lease&&lease->job_id==job.at("job_id").str(),"old lease not reserved");new_reader.index=snippet;new_reader.snippets=false;new_web.read("alice",args(b));
    auto meta=Json::object();meta.set("lease",encode_lease(*lease));meta.set("original_snapshot",original.at("snapshot_id"));meta.set("session",original_session);meta.set("evidence_id",evidence.at("evidence_id"));std::ofstream(argv[4])<<meta.dump();
    std::cout<<"PASS schema7 generated real active read lease before "<<(snippet?"nosnippet":"noindex")<<" on B; no completion yet\n";
  } else {
    check(std::string(argv[1])=="upgrade","unknown mode");check(schema_version(store)==8,"candidate did not apply stage1 migration");auto meta=Json::parse(bzs::read_file(argv[4]));auto lease=decode_lease(meta.at("lease"));old_web.execute_read(lease);auto result=store.job_results("alice",lease.job_id).at("items").at(0);check(result.at("state").str()=="succeeded","live legacy completion failed");
    new_web.read("alice",args(b));auto current=find_url(hits(store,freeze(store)),a);
    if(snippet)check(!current.is_null()&&current.at("snippet").str().empty(),"legacy missing capture preserved current A canonical snippet after B allow");else check(current.is_null(),"legacy missing capture preserved current A index after B allow");
    auto previous=find_url(hits(store,meta.at("session")),a);if(snippet)check(!previous.is_null()&&previous.at("snippet").str().empty(),"legacy frozen snippet resurrected");else check(previous.is_null(),"legacy frozen row resurrected");
    check(store.evidence_get("alice",meta.at("evidence_id").str()).at("quote").str()=="marker","legacy owner evidence changed");check(!find_url(hits(store,freeze(store)),b).at("snippet").str().empty(),"legacy completion suppressed independent fresh B");
    check(store.snapshot("alice",result.at("snapshot_id").str()).at("canonical_text").str()=="marker secret body","legacy completion private body lost");
    std::cout<<"PASS genuine schema7 active lease upgraded; late A permanently sealed across independent fresh B allow; private evidence preserved ("<<(snippet?"nosnippet":"noindex")<<")\n";
  }return 0;
}catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}}
