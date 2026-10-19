#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include "bzs/publication.hpp"
#include <set>
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <limits>
#include <tuple>

namespace bzs {
namespace {
  void checked(int rc) {
    if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
    const auto primary=rc&0xff;
    if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
    if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
    throw Error("E_STORAGE","Publication storage operation failed",500);
  }
  struct Sql {
    sqlite3_stmt* p=nullptr;
    Sql(sqlite3* db,const char* query) { checked(sqlite3_prepare_v2(db,query,-1,&p,nullptr)); }
    ~Sql() { sqlite3_finalize(p); }
    Sql(const Sql&)=delete;Sql& operator=(const Sql&)=delete;
    void bind(int i,const std::string& value) { checked(sqlite3_bind_text(p,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
    void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(p,i,value)); }
    bool step() { const auto rc=sqlite3_step(p);checked(rc);return rc==SQLITE_ROW; }
    std::int64_t num(int i)const { return sqlite3_column_int64(p,i); }
    std::string text(int i)const { const auto v=sqlite3_column_text(p,i);return v?std::string(reinterpret_cast<const char*>(v),static_cast<std::size_t>(sqlite3_column_bytes(p,i))):""; }
  };
  std::string key_for(const std::string& url) { try { return map_url(url); } catch(const Error&) { return url; } }
}

Json validate_publication(const Json& record) {
  auto policy=Json::parse(record.dump(),8*1024*1024);
  policy.allowed({"version","index_allowed","snippet_allowed","follow_allowed","snippet_text","snippet_title","directives","diagnostics"});
  require(required_string(policy,"version",64)=="bzs-publication-1","E_WORKER_PROTOCOL","Unknown publication version",502);
  const bool index=policy.at("index_allowed").flag(),snippet=policy.at("snippet_allowed").flag();(void)policy.at("follow_allowed").flag();
  for(const auto* key:{"snippet_text","snippet_title"})require(policy.at(key).is_string()&&valid_utf8(policy.at(key).str())&&policy.at(key).str().find('\0')==std::string::npos&&
      policy.at(key).str().size()<=(std::string_view(key)=="snippet_text"?1024U*1024U:1024U),"E_WORKER_PROTOCOL","Invalid publication text",502);
  require((snippet||policy.at("snippet_text").str().empty())&&(index||(!snippet&&policy.at("snippet_title").str().empty())),"E_WORKER_PROTOCOL","Restricted publication contains forbidden text",502);
  for(const auto* key:{"directives","diagnostics"}) {
    const auto values=policy.at(key);require(values.is_array()&&values.size()<=32,"E_WORKER_PROTOCOL","Invalid publication metadata",502);
    for(std::size_t i=0;i<values.size();++i)require(values.at(i).is_string()&&values.at(i).str().size()<=128&&valid_utf8(values.at(i).str())&&values.at(i).str().find('\0')==std::string::npos,"E_WORKER_PROTOCOL","Invalid publication directive",502);
  }
  return policy;
}
Json meet_publication(const Json& input,const Json& ceiling) {
  auto policy=Json::parse(input.dump(),8*1024*1024);
  if(!ceiling.has("version"))return policy;
  for(const auto* field:{"index_allowed","snippet_allowed","follow_allowed"})policy.set(field,policy.at(field).flag()&&ceiling.at(field).flag());
  for(const auto* field:{"directives","diagnostics"}) {
    std::set<std::string> unique;for(const auto& record:{policy,ceiling}) { const auto values=record.at(field);for(std::size_t i=0;i<values.size();++i)unique.insert(values.at(i).str()); }
    auto values=Json::array();for(const auto& value:unique)values.push(Json::string(value));policy.set(field,values);
  }
  if(!policy.at("index_allowed").flag()) { policy.set("snippet_allowed",false);policy.set("snippet_title",""); }
  if(!policy.at("snippet_allowed").flag())policy.set("snippet_text","");
  const auto diagnostics=policy.at("diagnostics");for(std::size_t i=0;i<diagnostics.size();++i)if(diagnostics.at(i).str()=="site_links_only")policy.set("snippet_title","");
  return policy;
}
bool publication_covers(const Json& policy,const Json& observation) {
  for(const auto* flag:{"index_allowed","snippet_allowed","follow_allowed"})if(!observation.at(flag).flag()&&policy.at(flag).flag())return false;
  return true;
}
bool restrictive_publication(const Json& policy) {
  if(!policy.is_object()||!policy.has("version"))return false;
  if(!policy.at("index_allowed").flag()||!policy.at("snippet_allowed").flag()||!policy.at("follow_allowed").flag())return true;
  auto rules=policy.at("directives");for(std::size_t i=0;i<rules.size();++i)if(rules.at(i).str()=="data-nosnippet")return true;
  return false;
}

Json Store::alias_fences_locked(const std::string& owner,const std::string& logical,const std::string& target,std::int64_t created_sequence) {
  auto fences=Json::array();
  Sql rows(db_,"SELECT id,delete_generation,deleted FROM documents WHERE owner=? AND url_key IN (?,?) ORDER BY id");
  rows.bind(1,owner);rows.bind(2,key_for(logical));rows.bind(3,key_for(target));
  while(rows.step()) {
    require(rows.num(2)==0&&rows.num(1)<=created_sequence,"E_TARGET_DELETED","A URL alias was deleted after this job was created",409);
    auto fence=Json::object();fence.set("document_id",rows.text(0));fence.set("generation",rows.num(1));fences.push(fence);
    require(fences.size()<=1000,"E_QUOTA","Too many retained aliases for one URL",429);
  }
  return fences;
}
bool Store::alias_fences_live_locked(const Json& fences) {
  for(std::size_t i=0;i<fences.size();++i) {
    const auto fence=fences.at(i);Sql row(db_,"SELECT 1 FROM documents WHERE id=? AND deleted=0 AND delete_generation=?");
    row.bind(1,fence.at("document_id").str());row.bind(2,fence.at("generation").num());if(!row.step())return false;
  }
  return true;
}

void Store::publish_policy_locked(const std::string& owner,const std::string& url,const std::string& snapshot_id,const Json& policy) {
  const auto key=key_for(url);
  auto record=Json::object();
  for(const auto& field:policy.keys())if(field!="snippet_text"&&field!="snippet_title")record.set(field,policy.at(field));
  const bool index=policy.at("index_allowed").flag(),snippet=policy.at("snippet_allowed").flag();
  bool restricted=!index||!snippet;
  const auto directives=policy.at("directives");
  for(std::size_t i=0;i<directives.size();++i)if(directives.at(i).str()=="data-nosnippet")restricted=true;
  const auto diagnostics=policy.at("diagnostics");
  for(std::size_t i=0;i<diagnostics.size();++i)if(diagnostics.at(i).str()=="site_links_only")restricted=true;
  std::int64_t generation=0;std::string previous,previous_snapshot;
  Sql old(db_,"SELECT restriction_generation,record_json,snapshot_id FROM document_publication WHERE owner=? AND url_key=?");
  old.bind(1,owner);old.bind(2,key);
  if(old.step()) { generation=old.num(0);previous=old.text(1);previous_snapshot=old.text(2); }
  const auto encoded=record.canonical();
  if(restricted&&(previous!=encoded||previous_snapshot!=snapshot_id)) {
    Sql sequence(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'");require(sequence.step(),"E_STORAGE","Missing publication sequence",500);
    require(sequence.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Publication generation exhausted",500);generation=sequence.num(0)+1;
    Sql update(db_,"UPDATE settings SET value=? WHERE key='publication_restriction_sequence'");update.bind(1,generation);update.step();
  }
  Sql save(db_,R"SQL(INSERT INTO document_publication(owner,url_key,snapshot_id,record_json,index_allowed,snippet_allowed,restricted,restriction_generation,observed_at)
 VALUES(?,?,NULLIF(?,''),?,?,?,?,?,?) ON CONFLICT(owner,url_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,record_json=excluded.record_json,
 index_allowed=excluded.index_allowed,snippet_allowed=excluded.snippet_allowed,restricted=excluded.restricted,
 restriction_generation=excluded.restriction_generation,observed_at=excluded.observed_at)SQL");
  save.bind(1,owner);save.bind(2,key);save.bind(3,snapshot_id);save.bind(4,encoded);save.bind(5,index?1:0);save.bind(6,snippet?1:0);save.bind(7,restricted?1:0);save.bind(8,generation);save.bind(9,clock_());save.step();
  // The current policy is keyed by exact normalized retrieval URL, so historical
  // host/default-port/fragment aliases cannot retain a public index entry.
  Sql remove(db_,"DELETE FROM content_fts WHERE owner=? AND document_id IN (SELECT id FROM documents WHERE owner=? AND (url_key=? OR representation_key=?))");
  remove.bind(1,owner);remove.bind(2,owner);remove.bind(3,key);remove.bind(4,key);remove.step();
  if(!index) {
    // Once hidden by noindex, a frozen row cannot reappear after a later allow.
    // Explicit immutable snapshots and evidence remain available to their owner.
    Sql retract(db_,"DELETE FROM session_rows WHERE document_id IN (SELECT id FROM documents WHERE owner=? AND (url_key=? OR representation_key=?)) OR (representation_key=? AND session_id IN (SELECT id FROM sessions WHERE owner=?))");
    retract.bind(1,owner);retract.bind(2,key);retract.bind(3,key);retract.bind(4,key);retract.bind(5,owner);retract.step();
  } else {
    Sql index_rows(db_,"SELECT d.id,s.title,s.canonical_text FROM documents d JOIN snapshots s ON s.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key WHERE d.owner=? AND (d.url_key=? OR d.representation_key=?) AND d.deleted=0 AND s.index_allowed=1 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1");
    index_rows.bind(1,owner);index_rows.bind(2,key);index_rows.bind(3,key);
    while(index_rows.step()) {
      Sql insert(db_,"INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)");
      insert.bind(1,index_rows.text(0));insert.bind(2,owner);insert.bind(3,index_rows.text(1));insert.bind(4,index_rows.text(2));insert.step();
    }
  }
}
void Store::observe_policy_locked(const std::string& owner,const std::string& url,const std::string& final_url,const Json& input) {
  if(!restrictive_publication(input))return;
  const std::set<std::string> keys={key_for(url),key_for(final_url)};
  for(const auto& key:keys) {
    auto policy=Json::parse(input.dump(),8*1024*1024);policy.set("snippet_text","");policy.set("snippet_title","");
    Sql previous(db_,"SELECT record_json FROM document_publication WHERE owner=? AND url_key=?");previous.bind(1,owner);previous.bind(2,key);
    if(previous.step()) {
      const auto old=Json::parse(previous.text(0));
      for(const auto* field:{"index_allowed","snippet_allowed","follow_allowed"})policy.set(field,policy.at(field).flag()&&old.at(field).flag());
      for(const auto* field:{"directives","diagnostics"}) {
        std::set<std::string> unique;for(const auto& record:{old,policy}) { const auto values=record.at(field);for(std::size_t i=0;i<values.size();++i)unique.insert(values.at(i).str()); }
        auto values=Json::array();for(const auto& value:unique)values.push(Json::string(value));policy.set(field,values);
      }
    }
    auto diagnostics=policy.at("diagnostics");bool tagged=false;for(std::size_t i=0;i<diagnostics.size();++i)tagged=tagged||diagnostics.at(i).str()=="restrictive_observation";
    if(!tagged)diagnostics.push(Json::string("restrictive_observation"));
    publish_policy_locked(owner,key,"",policy);
  }
}
Json Store::publication_fence(const std::string& owner,const std::string& url) {
  std::lock_guard lock(mutex_);auto fence=Json::object();fence.set("epoch",epoch_locked(owner));fence.set("sequence",delete_sequence_locked());fence.set("generation",deletion_generation_locked(owner,url));return fence;
}
void Store::observe_web_publication(const std::string& owner,const std::string& url,const std::string& final_url,const Json& policy,const Json& fence) {
  std::lock_guard lock(mutex_);checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr));
  try {
    if(epoch_locked(owner)==fence.at("epoch").num()&&deletion_generation_locked(owner,url)==fence.at("generation").num()) {
      bool live=true;try { (void)alias_fences_locked(owner,url,final_url,fence.at("sequence").num()); } catch(const Error& error) { if(error.code!="E_TARGET_DELETED")throw;live=false; }
      if(live)observe_policy_locked(owner,url,final_url,policy);
    }
    checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));
  } catch(...) { sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);throw; }
}

}
