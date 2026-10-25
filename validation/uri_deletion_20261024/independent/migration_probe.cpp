// Independent schema13 -> 14 probe. The frozen historical redirect helper is
// included unchanged; fixture state is created through the real baseline API.
#define main historical_redirect_probe_main
#include "original_redirect_probe.cpp"
#undef main
#include <sqlite3.h>
#include <fstream>
#include <sstream>

namespace {
std::int64_t scalar(sqlite3* db,const std::string& sql) {
  sqlite3_stmt* stmt=nullptr;
  if(sqlite3_prepare_v2(db,sql.c_str(),-1,&stmt,nullptr)!=SQLITE_OK)
    throw std::runtime_error(sqlite3_errmsg(db));
  struct Close { sqlite3_stmt* s;~Close(){sqlite3_finalize(s);} } close{stmt};
  VERIFY(sqlite3_step(stmt)==SQLITE_ROW);return sqlite3_column_int64(stmt,0);
}
std::string sql_text(sqlite3* db,const std::string& sql,const std::string& value) {
  sqlite3_stmt* stmt=nullptr;
  if(sqlite3_prepare_v2(db,sql.c_str(),-1,&stmt,nullptr)!=SQLITE_OK)
    throw std::runtime_error(sqlite3_errmsg(db));
  struct Close { sqlite3_stmt* s;~Close(){sqlite3_finalize(s);} } close{stmt};
  VERIFY(sqlite3_bind_text(stmt,1,value.c_str(),-1,SQLITE_TRANSIENT)==SQLITE_OK);
  VERIFY(sqlite3_step(stmt)==SQLITE_ROW);const auto* bytes=sqlite3_column_text(stmt,0);
  return bytes?reinterpret_cast<const char*>(bytes):"";
}
Json read_json(const std::filesystem::path& path) {
  std::ifstream file(path);std::ostringstream data;data<<file.rdbuf();
  VERIFY(file.good()||file.eof());return Json::parse(data.str(),8*1024*1024);
}
const std::string direct="https://independent-chain.example/direct";
const std::string encoded_direct="https://independent-chain.example/%64irect";
void create_fixture(const std::filesystem::path& path,const std::filesystem::path& backup) {
  DeterministicWorker worker;Store store(path,[&]{return worker.now;});
  VERIFY(scalar(store.raw_handle(),"PRAGMA user_version")==13);
  WebService web(store,worker,{});auto out=Json::object(),docs=Json::array();
  const std::vector<std::string> uris={
    "https://independent-chain.example/%6Diddle",encoded_direct,
    "https://INDEPENDENT-CHAIN.EXAMPLE:443/%66inal#fragment",
    "https://independent-chain.example/a%2fb", "https://independent-chain.example/a/b",
    "https://independent-chain.example/a%252fb", "https://independent-chain.example/query?x=%41&y=%2f",
    "https://independent-chain.example/query?y=%2f&x=%41", "https://independent-chain.example/bad%XZ",
    "https://independent-chain.example/%E4%B8%AD", "https://independent-chain.example/%e4%b8%ad",
    "http://127.0.0.1/legacy-import"};
  for(const auto& uri:uris)docs.push(import_alias(store,uri));
  out.set("documents",docs);
  const auto other=store.ingest("other-owner",{uris.front(),"Other owner immutable body.","text/plain","Other owner"});
  out.set("other_document",other);
  const auto routed=web.read(owner,request());out.set("routed",routed);
  const auto direct_result=web.read(owner,request(direct));out.set("direct",direct_result);
  out.set("evidence",store.evidence(owner,routed.at("snapshot_id").str(),0,11));
  out.set("session",store.search(owner,parse_query(Json::string("Independent"))));
  const auto success=store.job_create(owner,"fixture-success","fixture-success");
  const auto lease=store.lease_next();VERIFY(lease.has_value());
  const SourceDocument job_doc{"https://independent-chain.example/job","Independent job body.","text/plain","Job"};
  VERIFY(store.complete(*lease,job_doc,parse_document(job_doc.raw,job_doc.content_type,job_doc.title)));
  out.set("successful_job",store.job_get(owner,success.at("job_id").str()));
  out.set("queued_job",store.job_create(owner,"fixture-queued","fixture-queued"));
  out.set("read_job",store.read_many_submit(owner,{original,direct},"read-queued",{}));
  store.source_report("independent-source","ok","",0);
  store.ingest("revoked-owner",{"https://independent-chain.example/revoked","Revoked data.","text/plain","Revoked"});
  out.set("revocation",store.revoke_principal("independent-admin","revoked-owner"));
  store.erase(owner,docs.at(0).at("document_id").str());
  store.erase(owner,docs.at(1).at("document_id").str());
  // These are the retained historical gap: genuine old code reuses both.
  VERIFY(web.read(owner,request(original,"cache_only")).at("from_cache").flag());
  VERIFY(web.read(owner,request(direct,"cache_only")).at("from_cache").flag());
  VERIFY(worker.fetches==2);out.set("backup",store.backup(backup));
  out.set("checks",checks);out.set("real_baseline_schema",std::int64_t{13});
  std::cout<<out.canonical()<<std::endl;
}
void verify_fixture(const std::filesystem::path& path,const std::filesystem::path& receipt) {
  DeterministicWorker worker;Store store(path,[&]{return worker.now;});WebService web(store,worker,{});
  VERIFY(scalar(store.raw_handle(),"PRAGMA user_version")==14);const auto saved=read_json(receipt);
  preserved_body(store,saved.at("routed").at("snapshot_id").str());
  preserved_body(store,saved.at("direct").at("snapshot_id").str());
  VERIFY(store.evidence_get(owner,saved.at("evidence").at("evidence_id").str()).at("quote").str()==body.substr(0,11));
  VERIFY(store.job_get(owner,saved.at("successful_job").at("job_id").str()).at("state").str()=="succeeded");
  VERIFY(store.job_get(owner,saved.at("queued_job").at("job_id").str()).at("state").str()=="queued");
  VERIFY(store.job_get(owner,saved.at("read_job").at("job_id").str()).at("state").str()=="queued");
  expect([&]{store.stats("revoked-owner");},"E_PRINCIPAL_REVOKED");
  expect([&]{web.read(owner,request(original,"cache_only"));},"E_CACHE_STALE");
  expect([&]{web.read(owner,request(direct,"cache_only"));},"E_CACHE_STALE");
  VERIFY(!store.revalidation_base(owner,original));VERIFY(!store.revalidation_base(owner,direct));
  VERIFY(worker.fetches==0);
  VERIFY(scalar(store.raw_handle(),"SELECT count(*) FROM documents WHERE deletion_key='' ")==0);
  VERIFY(scalar(store.raw_handle(),"SELECT count(*) FROM documents WHERE deleted=1")==2);
  VERIFY(sql_text(store.raw_handle(),"SELECT deletion_key FROM documents WHERE url=? LIMIT 1","https://independent-chain.example/%6Diddle")==middle);
  VERIFY(sql_text(store.raw_handle(),"SELECT deletion_key FROM documents WHERE url=? LIMIT 1","https://independent-chain.example/bad%XZ")=="https://independent-chain.example/bad%XZ");
  std::cout<<"VERIFY_UPGRADED_PASS checks="<<checks<<" external_network_requests=0"<<std::endl;
}
void new_writes_and_cross_handle(const std::filesystem::path& path) {
  DeterministicWorker worker;Store first(path,[&]{return worker.now;});Store second(path,[&]{return worker.now;});
  const std::string live="https://independent-chain.example/handle",encoded="https://INDEPENDENT-CHAIN.EXAMPLE:443/%68andle";
  const auto marker=second.ingest(owner,{encoded,"Independent marker.","text/plain","Marker"});
  VERIFY(sql_text(first.raw_handle(),"SELECT deletion_key FROM documents WHERE url=?",encoded)==live);
  WebService web(first,worker,{});const auto initial=web.read(owner,request(live));VERIFY(worker.fetches==1);
  VERIFY(web.read(owner,request(live,"cache_only")).at("from_cache").flag());
  second.erase(owner,marker.at("document_id").str());
  expect([&]{web.read(owner,request(live,"cache_only"));},"E_CACHE_STALE");
  const auto revived=second.ingest(owner,{encoded,"Independent marker revised.","text/plain","Marker"});
  VERIFY(revived.at("document_id").str()==marker.at("document_id").str());
  expect([&]{web.read(owner,request(live,"cache_only"));},"E_CACHE_STALE");
  VERIFY(!first.revalidation_base(owner,live));VERIFY(worker.fetches==1);
  preserved_body(first,initial.at("snapshot_id").str());
  std::cout<<"NEW_WRITES_AND_CROSS_HANDLE_PASS checks="<<checks<<" external_network_requests=0"<<std::endl;
}
}
int main(int argc,char** argv) {
  try {
    if(argc<3)throw std::runtime_error("usage: mode database [argument]");
    const std::string mode=argv[1];const std::filesystem::path path=argv[2];
    if(mode=="create")create_fixture(path,argv[3]);
    else if(mode=="verify")verify_fixture(path,argv[3]);
    else if(mode=="open") {Store s(path,[]{return std::int64_t{1700000000000};});std::cout<<"OPEN_PASS version="<<scalar(s.raw_handle(),"PRAGMA user_version")<<std::endl;}
    else if(mode=="writes")new_writes_and_cross_handle(path);
    else if(mode=="expect-error") {expect([&]{Store s(path,[]{return std::int64_t{1700000000000};});},argv[3]);std::cout<<"EXPECTED_ERROR_PASS "<<argv[3]<<std::endl;}
    else throw std::runtime_error("unknown mode");
    return 0;
  }catch(const Error& e) {std::cerr<<"FAIL "<<e.code<<" "<<e.what()<<std::endl;return 2;}
  catch(const std::exception& e) {std::cerr<<"FAIL "<<e.what()<<std::endl;return 2;}
}
