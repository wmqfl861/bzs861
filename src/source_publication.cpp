#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include "bzs/contracts.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <limits>
#include <map>
#include <vector>

namespace bzs {
namespace {
void checked(int rc) {
  if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
  if((rc&255)==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
  if((rc&255)==SQLITE_BUSY||(rc&255)==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
  throw Error("E_STORAGE","Source publication storage operation failed",500);
}
void sql(sqlite3* db,const char* query) { checked(sqlite3_exec(db,query,nullptr,nullptr,nullptr)); }
struct Statement {
  sqlite3_stmt* p=nullptr;
  Statement(sqlite3* db,const char* query) { checked(sqlite3_prepare_v2(db,query,-1,&p,nullptr)); }
  ~Statement() { sqlite3_finalize(p); }
  Statement(const Statement&)=delete;Statement& operator=(const Statement&)=delete;
  void bind(int i,const std::string& value) { checked(sqlite3_bind_text(p,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
  void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(p,i,value)); }
  bool step() { const auto rc=sqlite3_step(p);checked(rc);return rc==SQLITE_ROW; }
  std::int64_t num(int i)const { return sqlite3_column_int64(p,i); }
  std::string text(int i)const { const auto* value=sqlite3_column_text(p,i);return value?std::string(reinterpret_cast<const char*>(value),static_cast<std::size_t>(sqlite3_column_bytes(p,i))):""; }
};
struct Transaction {
  sqlite3* db;bool done=false;
  explicit Transaction(sqlite3* handle):db(handle) { sql(db,"BEGIN IMMEDIATE"); }
  ~Transaction() { if(!done)sqlite3_exec(db,"ROLLBACK",nullptr,nullptr,nullptr); }
  void commit() { sql(db,"COMMIT");done=true; }
};
constexpr std::size_t max_retained_sources=4096;
std::string source_identity(const Json& contract) {
  auto identity=Json::object();
  for(const auto* field:{"id","provider_id","index_family","logical_service_id","capability","endpoint","adapter_id"})identity.set(field,contract.at(field));
  return identity.canonical();
}
int contract_mode(const Json& contract,std::int64_t now) {
  try {
    if(!contract.at("enabled").flag())return 2;
    (void)validate_contract("source-manifest",contract,now);
    const auto review=contract.at("review");
    if(review.at("cache_policy").str()!="allowed_with_limits")return 2;
    const auto policy=review.at("redistribution_policy").str();
    return policy=="allowed_with_limits"?0:policy=="links_only"?1:2;
  }catch(const Error&) { return 2; }
}
std::string policy_digest(const SourceRegistry& registry) {
  const auto manifest=registry.publication_manifest().canonical();
  require(manifest.size()<=1024*1024,"E_SOURCE_POLICY_CAPACITY","Active source publication manifest exceeds the storage bound",507);
  return sha256(manifest);
}
}

Json SourceRegistry::publication_manifest()const {
  std::map<std::string,Json> sorted;
  for(const auto& source:specs_)sorted.emplace(source.id,source.contract);
  auto result=Json::array();for(const auto& [id,contract]:sorted) { (void)id;result.push(contract); }
  return result;
}
void Store::migrate_source_publication_locked() {
  sql(db_,R"SQL(
CREATE TABLE IF NOT EXISTS source_publication_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  generation INTEGER NOT NULL,manifest_generation INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL);
INSERT OR IGNORE INTO source_publication_state VALUES(1,0,0,'');
CREATE TABLE IF NOT EXISTS source_publication_rules(source_id TEXT PRIMARY KEY,mode INTEGER NOT NULL,
  snippet_generation INTEGER NOT NULL,withdraw_generation INTEGER NOT NULL,contract_json TEXT NOT NULL);
)SQL");
  bool exists=false;Statement columns(db_,"PRAGMA table_info(search_streams)");while(columns.step())if(columns.text(1)=="source_generation")exists=true;
  if(!exists)sql(db_,"ALTER TABLE search_streams ADD COLUMN source_generation INTEGER NOT NULL DEFAULT 0");
}
void Store::refresh_source_publication_locked() {
  const bool own_transaction=sqlite3_get_autocommit(db_)!=0;
  if(own_transaction)sql(db_,"BEGIN IMMEDIATE");
  try {
    std::vector<std::string> expired;
    Statement rules(db_,"SELECT source_id,contract_json FROM source_publication_rules WHERE mode<2 ORDER BY source_id");
    while(rules.step()) {
      require(expired.size()<max_retained_sources,"E_STORAGE","Invalid source publication capacity",500);
      if(contract_mode(Json::parse(rules.text(1),128*1024),clock_())==2)expired.push_back(rules.text(0));
    }
    if(!expired.empty()) {
      Statement state(db_,"SELECT generation FROM source_publication_state WHERE singleton=1");require(state.step(),"E_STORAGE","Missing source publication state",500);
      require(state.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Source publication generation exhausted",500);const auto generation=state.num(0)+1;
      for(const auto& id:expired) { Statement update(db_,"UPDATE source_publication_rules SET mode=2,snippet_generation=?,withdraw_generation=? WHERE source_id=?");update.bind(1,generation);update.bind(2,generation);update.bind(3,id);update.step(); }
      Statement update(db_,"UPDATE source_publication_state SET generation=? WHERE singleton=1");update.bind(1,generation);update.step();
    }
    if(own_transaction)sql(db_,"COMMIT");
  }catch(...) { if(own_transaction)sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);throw; }
}
std::int64_t Store::activate_source_publication(const SourceRegistry& registry) {
  require(registry.configured(),"E_SOURCE_CONFIG","Source activation requires an explicit registry",500);
  std::lock_guard lock(mutex_);refresh_source_publication_locked();Transaction tx(db_);
  Statement state(db_,"SELECT generation,manifest_generation,manifest_sha256 FROM source_publication_state WHERE singleton=1");require(state.step(),"E_STORAGE","Missing source publication state",500);
  auto generation=state.num(0),manifest_generation=state.num(1);const auto digest=policy_digest(registry);
  if(state.text(2)!=digest) {
    require(manifest_generation==source_publication_manifest_generation_,"E_SOURCE_POLICY_STALE","A newer source manifest is active; reopen before explicit activation",409);
    require(generation<std::numeric_limits<std::int64_t>::max()&&manifest_generation<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Source publication generation exhausted",500);
    ++generation;++manifest_generation;
    struct Rule { int mode=0;std::int64_t snippet=0,withdraw=0;Json contract=Json::object(); };
    std::map<std::string,Rule> rules;Statement old(db_,"SELECT source_id,mode,snippet_generation,withdraw_generation,contract_json FROM source_publication_rules");
    while(old.step())rules.emplace(old.text(0),Rule{static_cast<int>(old.num(1)),old.num(2),old.num(3),Json::parse(old.text(4),128*1024)});
    for(const auto& source:registry.all())rules.try_emplace(source.id);
    require(rules.size()<=max_retained_sources,"E_SOURCE_POLICY_CAPACITY","Retained source publication identities exceed the storage bound",507);
    for(auto& [id,rule]:rules) {
      const auto* source=registry.find(id);const auto mode=source?contract_mode(source->contract,clock_()):2;
      if(source&&rule.contract.size()>0&&source_identity(rule.contract)!=source_identity(source->contract))rule.withdraw=std::max(rule.withdraw,generation-1);
      if(mode>rule.mode) { if(mode>=1)rule.snippet=generation;if(mode>=2)rule.withdraw=generation; }
      if(rule.mode>=1&&mode<1)rule.snippet=std::max(rule.snippet,generation-1);
      if(rule.mode>=2&&mode<2)rule.withdraw=std::max(rule.withdraw,generation-1);
      rule.mode=mode;if(source)rule.contract=source->contract;
      Statement save(db_,"INSERT INTO source_publication_rules VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET mode=excluded.mode,snippet_generation=excluded.snippet_generation,withdraw_generation=excluded.withdraw_generation,contract_json=excluded.contract_json");
      save.bind(1,id);save.bind(2,static_cast<std::int64_t>(mode));save.bind(3,rule.snippet);save.bind(4,rule.withdraw);save.bind(5,rule.contract.canonical());save.step();
    }
    Statement update(db_,"UPDATE source_publication_state SET generation=?,manifest_generation=?,manifest_sha256=? WHERE singleton=1");update.bind(1,generation);update.bind(2,manifest_generation);update.bind(3,digest);update.step();
  }
  tx.commit();source_publication_manifest_generation_=manifest_generation;return generation;
}
int Store::source_publication_mode_locked(const std::string& id,std::int64_t generation,const Json* original) {
  Statement rule(db_,"SELECT mode,snippet_generation,withdraw_generation,contract_json FROM source_publication_rules WHERE source_id=?");rule.bind(1,id);
  if(!rule.step())return 2;
  if(original&&source_identity(*original)!=source_identity(Json::parse(rule.text(3),128*1024)))return 2;
  const auto mode=rule.num(0);
  require(mode>=0&&mode<=2&&generation>=0,"E_STORAGE","Invalid source publication binding",500);
  if(mode>=2||(rule.num(2)>0&&generation<=rule.num(2)))return 2;
  return mode>=1||(rule.num(1)>0&&generation<=rule.num(1))?1:0;
}
std::int64_t Store::source_capture_locked(const SourceRegistry& registry,const Json& ids) {
  Statement state(db_,"SELECT generation,manifest_sha256 FROM source_publication_state WHERE singleton=1");require(state.step(),"E_STORAGE","Missing source publication state",500);
  require(state.text(1)==policy_digest(registry),"E_SOURCE_POLICY_STALE","Search registry is no longer the active reviewed source manifest",409);
  require(ids.is_array()&&ids.size()<=20,"E_SCHEMA","Invalid selected source identities");
  for(std::size_t i=0;i<ids.size();++i)require(source_publication_mode_locked(ids.at(i).str(),state.num(0))<2,"E_SOURCE_REVIEW","Source publication permission was withdrawn",409);
  return state.num(0);
}
std::int64_t Store::source_publication_capture(const SourceRegistry& registry,const Json& ids) {
  std::lock_guard lock(mutex_);
  // Persist timed withdrawal even when the subsequent request is rejected.
  refresh_source_publication_locked();Transaction tx(db_);const auto generation=source_capture_locked(registry,ids);tx.commit();return generation;
}
int Store::source_hit_mode_locked(const Json& hit,std::int64_t generation) {
  if(!hit.has("sources"))return 2;
  const auto sources=hit.at("sources");if(!sources.is_array()||sources.size()==0||sources.size()>600)return 2;
  int mode=0;for(std::size_t i=0;i<sources.size()&&mode<2;++i)mode=std::max(mode,source_publication_mode_locked(sources.at(i).at("source_id").str(),generation));return mode;
}
Json Store::project_source_search(const Json& response,std::int64_t generation) {
  std::lock_guard lock(mutex_);refresh_source_publication_locked();Transaction tx(db_);
  auto projected=Json::parse(response.dump(),24*1024*1024);const auto reports=projected.at("sources");bool withdrawn=false;
  for(std::size_t i=0;i<reports.size();++i)withdrawn=withdrawn||source_publication_mode_locked(reports.at(i).at("source_id").str(),generation)>=2;
  auto results=projected.at("results");
  for(std::size_t i=0;i<results.size();++i) {
    auto hit=results.at(i);const auto mode=source_hit_mode_locked(hit,generation);withdrawn=withdrawn||mode>=2;
    if(mode>=1) { hit.set("title",hit.at("url"));hit.set("snippet","");hit.set("snippet_kind","publication_suppressed"); }
  }
  tx.commit();require(!withdrawn,"E_SOURCE_POLICY_CHANGED","Source publication permission changed during this search; repeat under current approved sources",409);return projected;
}
}
