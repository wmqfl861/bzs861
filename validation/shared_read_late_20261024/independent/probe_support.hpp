#pragma once
#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>
using namespace bzs;
namespace independent {
inline std::size_t checks=0;
#define VERIFY(x) do { ++independent::checks; if(!(x)) throw std::runtime_error(std::string("check failed: ")+ #x +" line "+std::to_string(__LINE__)); } while(false)
template<class F> void expect(F function,const std::string& code) {
  ++checks;
  try { function(); } catch(const Error& error) { if(error.code==code)return;throw; }
  throw std::runtime_error("expected "+code);
}
inline std::int64_t scalar(sqlite3* db,const std::string& query,const std::string& value="") {
  sqlite3_stmt* raw=nullptr;
  VERIFY(sqlite3_prepare_v2(db,query.c_str(),-1,&raw,nullptr)==SQLITE_OK);
  std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> stmt(raw,sqlite3_finalize);
  if(sqlite3_bind_parameter_count(raw))VERIFY(sqlite3_bind_text(raw,1,value.c_str(),-1,SQLITE_TRANSIENT)==SQLITE_OK);
  VERIFY(sqlite3_step(raw)==SQLITE_ROW);return sqlite3_column_int64(raw,0);
}
inline Json load_json(const std::filesystem::path& path) {
  std::ifstream stream(path);std::ostringstream text;text<<stream.rdbuf();
  VERIFY(stream.good()||stream.eof());return Json::parse(text.str(),8*1024*1024);
}
inline Json parameters(const std::string& url,const std::string& key,std::int64_t timeout=600000,std::int64_t page_timeout=20000) {
  auto out=Json::object(),urls=Json::array();urls.push(Json::string(url));out.set("urls",urls);
  out.set("idempotency_key",key);out.set("timeout_ms",timeout);out.set("per_page_timeout_ms",page_timeout);
  out.set("max_attempts",std::int64_t{2});return out;
}
struct Worker : WorkerPort {
  std::int64_t now=1000000;
  std::vector<Json> requests;
  std::vector<std::string> cancellations;
  std::function<Json(const Json&)> response;
  static Json ok(const Json& params) {
    auto result=Json::object();result.set("status","ok");
    result.set("html","<main>Independent shared read body.</main>");
    result.set("text","Independent shared read body.");result.set("title","Independent batch fixture");
    result.set("content_type","text/html");result.set("extractor","independent-shared/1");
    result.set("index_allowed",true);result.set("final_url",params.at("url"));
    result.set("redirects",Json::array());result.set("links",Json::array());
    result.set("usage",Json::parse(R"({"external_requests":1,"content_bytes":100})"));return result;
  }
  Json call(const std::string& method,const Json& params,int timeout) override {
    VERIFY(timeout>0);
    if(method=="fetch.cancel") { cancellations.push_back(params.at("attempt_id").str());return Json::object(); }
    VERIFY(method=="fetch.extract");VERIFY(params.at("max_requests").num()>0);
    VERIFY(params.at("max_content_bytes").num()>0);VERIFY(params.at("attempt_id").str().size()==48);
    requests.push_back(params);return response?response(params):ok(params);
  }
};
inline Json item(Store& store,const std::string& id,const std::string& owner="alice") {
  return store.job_results(owner,id).at("items").at(0);
}
inline Json encode(const ReadLease& lease) {
  auto out=Json::object();out.set("job_id",lease.job_id);out.set("owner",lease.owner);out.set("url",lease.url);out.set("attempt_id",lease.attempt_id);
  out.set("epoch",lease.epoch);out.set("input_index",lease.input_index);out.set("deletion_generation",lease.deletion_generation);
  out.set("request_grant",lease.request_grant);out.set("byte_grant",lease.byte_grant);out.set("timeout_ms",lease.timeout_ms);
  out.set("deadline_ms",lease.deadline_ms);out.set("publication_sequence",lease.publication_sequence);return out;
}
inline ReadLease decode(const Json& value) {
  return {value.at("job_id").str(),value.at("owner").str(),value.at("url").str(),value.at("attempt_id").str(),
          value.at("epoch").num(),value.at("input_index").num(),value.at("deletion_generation").num(),
          value.at("request_grant").num(),value.at("byte_grant").num(),value.at("timeout_ms").num(),
          value.at("deadline_ms").num(),value.at("publication_sequence").num()};
}
inline void stop(Store& store,WebService& web,const std::string& id,const std::string& action="cancel") {
  store.job_control("alice",id,action);web.cancel_read_attempt(store.active_read_attempt("alice",id));
}
inline void drain(Store& store,WebService& web) {
  std::size_t attempts=0;while(auto lease=store.lease_next_read()) { VERIFY(++attempts<100);web.execute_read(*lease); }
}
}
