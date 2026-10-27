#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include "bzs/web.hpp"
#include "bzs/publication.hpp"
#include <set>
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <limits>
#include <tuple>
#include <algorithm>
#include <map>

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
  constexpr std::size_t max_site_scopes=4096,max_site_binding_urls=24;
  Json site_manifest(const SiteRegistry& sites) {
    auto manifest=Json::object(),entries=Json::array();manifest.set("version",std::int64_t{1});
    for(const auto& site:sites.all()) {
      auto entry=Json::object(),paths=Json::array(),limits=Json::object();
      entry.set("id",site.id);entry.set("origin",site.origin);entry.set("enabled",site.enabled);entry.set("review",site.review);
      for(const auto& path:site.path_prefixes)paths.push(Json::string(path));
      entry.set("path_prefixes",paths);
      limits.set("max_concurrency",std::int64_t{1});limits.set("min_interval_ms",site.min_interval_ms);
      limits.set("request_quota",site.request_quota);limits.set("quota_window_seconds",site.quota_window_seconds);entry.set("limits",limits);entries.push(entry);
    }
    manifest.set("sites",entries);return manifest;
  }
  int deployment_mode(const SiteSpec& site,std::int64_t now) {
    if(!site.reviewed(now))return 2;
    return site.review.at("redistribution_policy").str()=="links_only"?1:0;
  }
  std::pair<std::string,std::string> origin_path(const std::string& identity) {
    const auto from=identity.find("://")+3,start=identity.find('/',from);
    require(start!=std::string::npos,"E_STORAGE","Invalid stored web publication identity",500);
    return {identity.substr(0,start),identity.substr(start,identity.find('?',start)-start)};
  }
}

std::int64_t Store::site_publication_generation() const {
  std::lock_guard lock(mutex_);return site_publication_bound_generation_;
}
std::int64_t Store::activate_site_publication(const SiteRegistry& sites) {
  std::lock_guard lock(mutex_);checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr));
  try {
    refresh_site_publication_locked(false);
    Sql state(db_,"SELECT manifest_sha256,manifest_generation,generation,legacy_crawl_unverified FROM site_publication_state WHERE singleton=1");
    require(state.step(),"E_STORAGE","Missing deployment publication state",500);
    const auto previous_manifest=state.num(1);auto generation=state.num(2);
    if(state.text(0)!=sites.manifest_digest()) {
      require(previous_manifest==site_publication_manifest_generation_,"E_SITE_POLICY_STALE","A newer deployment publication manifest is already active; reopen before explicit activation",409);
      require(generation<std::numeric_limits<std::int64_t>::max()&&previous_manifest<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Deployment publication generation exhausted",500);
      ++generation;
      auto rules=site_publication_rules_;
      for(const auto& site:sites.all())for(const auto& path:site.path_prefixes) {
        if(std::any_of(rules.begin(),rules.end(),[&](const auto& rule){return rule.origin==site.origin&&rule.path==path;}))continue;
        SitePublicationRule rule;rule.origin=site.origin;rule.path=path;
        // A new narrower allow cannot erase an earlier ancestor's withdrawal.
        const SitePublicationRule* ancestor=nullptr;
        for(const auto& old:site_publication_rules_)if(old.origin==rule.origin&&path_within(path,old.path)&&(!ancestor||old.path.size()>ancestor->path.size()))ancestor=&old;
        if(ancestor) { rule.mode=ancestor->mode;rule.snippet_generation=ancestor->snippet_generation;rule.index_generation=ancestor->index_generation; }
        rules.push_back(std::move(rule));
        require(rules.size()<=max_site_scopes,"E_SITE_POLICY_CAPACITY","Retained deployment publication scopes exceed the configured bound",507);
      }
      const auto now=clock_();
      for(auto& rule:rules) {
        int mode=2;
        for(const auto& site:sites.all())if(site.origin==rule.origin&&std::any_of(site.path_prefixes.begin(),site.path_prefixes.end(),[&](const auto& path){return path_within(rule.path,path);})) { mode=deployment_mode(site,now);break; }
        if(mode>rule.mode) {
          if(mode>=1)rule.snippet_generation=generation;
          if(mode>=2)rule.index_generation=generation;
        }
        // A response dispatched while a restriction remained active may commit
        // after this activation. Seal every older generation when releasing
        // the restriction, including generations changed only by metadata.
        if(rule.mode>=1&&mode<1)rule.snippet_generation=std::max(rule.snippet_generation,generation-1);
        if(rule.mode>=2&&mode<2)rule.index_generation=std::max(rule.index_generation,generation-1);
        rule.mode=mode;
        Sql save(db_,"INSERT INTO site_publication_rules(origin,path_prefix,mode,snippet_generation,index_generation)VALUES(?,?,?,?,?) ON CONFLICT(origin,path_prefix) DO UPDATE SET mode=excluded.mode,snippet_generation=excluded.snippet_generation,index_generation=excluded.index_generation");
        save.bind(1,rule.origin);save.bind(2,rule.path);save.bind(3,static_cast<std::int64_t>(rule.mode));save.bind(4,rule.snippet_generation);save.bind(5,rule.index_generation);save.step();
      }
      const auto manifest=site_manifest(sites).canonical();require(manifest.size()<=1024*1024,"E_SITE_POLICY_CAPACITY","Deployment publication manifest exceeds the storage bound",507);
      Sql update(db_,"UPDATE site_publication_state SET manifest_sha256=?,manifest_json=?,manifest_generation=?,generation=?,legacy_frozen_withdrawn=MAX(legacy_frozen_withdrawn,?),sweep_after=0,sweep_complete=0 WHERE singleton=1");
      update.bind(1,sites.manifest_digest());update.bind(2,manifest);update.bind(3,previous_manifest+1);update.bind(4,generation);update.bind(5,rules.empty()&&state.num(3)==0?0:1);update.step();
    }
    refresh_site_publication_locked();
    Sql current(db_,"SELECT generation,manifest_generation FROM site_publication_state WHERE singleton=1");current.step();
    const auto bound=current.num(0),manifest_generation=current.num(1);
    checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));
    site_publication_bound_generation_=bound;site_publication_manifest_generation_=manifest_generation;return bound;
  } catch(...) { sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);site_publication_cached_generation_=-1;throw; }
}
void Store::refresh_site_publication_locked(bool sweep) {
  const bool transaction=sqlite3_get_autocommit(db_)!=0;
  if(transaction)checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr));
  try {
    const auto reload=[&] {
      Sql state(db_,"SELECT version,generation,manifest_json,legacy_frozen_withdrawn FROM site_publication_state WHERE singleton=1");
      require(state.step()&&state.text(0)=="bzs-site-publication-1","E_SCHEMA_VERSION","Unknown deployment publication policy version",409);
      if(state.num(1)!=site_publication_cached_generation_) {
        std::vector<SitePublicationRule> rules;
        Sql rows(db_,"SELECT origin,path_prefix,mode,snippet_generation,index_generation FROM site_publication_rules ORDER BY origin,path_prefix");
        while(rows.step()) {
          require(rules.size()<max_site_scopes&&rows.num(2)>=0&&rows.num(2)<=2&&rows.num(3)>=0&&rows.num(4)>=0,"E_STORAGE","Invalid deployment publication state",500);
          rules.push_back({rows.text(0),rows.text(1),static_cast<int>(rows.num(2)),rows.num(3),rows.num(4)});
        }
        site_publication_registry_=std::make_shared<SiteRegistry>(Json::parse(state.text(2),1024*1024));
        site_publication_rules_=std::move(rules);site_publication_cached_generation_=state.num(1);
      }
      site_publication_legacy_withdrawn_=state.num(3)!=0;
    };
    reload();
    std::set<std::string> expired;
    for(const auto& site:site_publication_registry_->all())if(!site.reviewed(clock_()))
      for(const auto& rule:site_publication_rules_)if(rule.origin==site.origin&&rule.mode<2) { expired.insert(site.origin);break; }
    if(!expired.empty()) {
      require(site_publication_cached_generation_<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Deployment publication generation exhausted",500);
      const auto generation=site_publication_cached_generation_+1;
      for(const auto& origin:expired) {
        Sql restrict(db_,"UPDATE site_publication_rules SET mode=2,snippet_generation=?,index_generation=? WHERE origin=? AND mode<2");
        restrict.bind(1,generation);restrict.bind(2,generation);restrict.bind(3,origin);restrict.step();
      }
      Sql update(db_,"UPDATE site_publication_state SET generation=?,sweep_after=0,sweep_complete=0 WHERE singleton=1");update.bind(1,generation);update.step();reload();
    }
    if(sweep)sweep_site_publication_locked();
    if(transaction)checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));
  } catch(...) { if(transaction)sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);site_publication_cached_generation_=-1;throw; }
}
Json Store::site_bindings_locked(const std::string& url,const Json& meta) {
  std::set<std::string> identities;
  const auto add=[&](const std::string& value) { identities.insert(publication_url_identity(value));require(identities.size()<=max_site_binding_urls,"E_WORKER_PROTOCOL","Too many stored publication URL bindings",502); };
  add(url);if(meta.has("final_url"))add(meta.at("final_url").str());
  for(const auto* field:{"redirects","crawl_publication_urls"})if(meta.has(field)) {
    const auto urls=meta.at(field);require(urls.is_array()&&urls.size()<=max_site_binding_urls,"E_WORKER_PROTOCOL","Invalid stored publication URL chain",502);
    for(std::size_t i=0;i<urls.size();++i)add(urls.at(i).str());
  }
  auto result=Json::object(),urls=Json::array();result.set("version","bzs-site-bindings-1");
  for(const auto& identity:identities)urls.push(Json::string(identity));
  result.set("urls",urls);return result;
}
int Store::site_publication_mode_locked(const std::string& source_kind,const std::string& encoded,std::int64_t generation,
                                      const std::string& url,const std::string& meta,const std::string& frozen_representation) {
  if(source_kind!="web")return 0;
  auto bindings=Json::parse(encoded,128*1024);
  int mode=bindings.has("captured_mode")?static_cast<int>(bindings.at("captured_mode").num()):0;
  require(mode>=0&&mode<=2,"E_STORAGE","Invalid captured deployment publication ceiling",500);
  if(bindings.has("legacy_crawl_unverified")&&bindings.at("legacy_crawl_unverified").flag())return site_publication_legacy_withdrawn_?2:mode;
  if(site_publication_rules_.empty())return mode;
  if(!bindings.has("version"))bindings=site_bindings_locked(url,Json::parse(meta,8*1024*1024));
  require(bindings.at("version").str()=="bzs-site-bindings-1","E_SCHEMA_VERSION","Unknown stored publication binding version",409);
  auto urls=bindings.at("urls");require(urls.is_array()&&urls.size()<=max_site_binding_urls,"E_STORAGE","Invalid stored publication bindings",500);
  if(!frozen_representation.empty())urls.push(Json::string(publication_url_identity(frozen_representation)));
  for(std::size_t i=0;i<urls.size();++i) {
    const auto [origin,path]=origin_path(urls.at(i).str());const auto lower=ascii_lower(path);
    const bool ambiguous=lower.find("%2f")!=std::string::npos||lower.find("%5c")!=std::string::npos||lower.find("%25")!=std::string::npos;
    const SitePublicationRule* selected=nullptr;
    auto rule=std::lower_bound(site_publication_rules_.begin(),site_publication_rules_.end(),origin,[](const auto& value,const std::string& key){return value.origin<key;});
    for(;rule!=site_publication_rules_.end()&&rule->origin==origin;++rule) {
      if(ambiguous) {
        mode=std::max(mode,rule->mode);
        if(rule->index_generation>0&&generation<=rule->index_generation)mode=2;
        else if(rule->snippet_generation>0&&generation<=rule->snippet_generation)mode=std::max(mode,1);
      } else if(path_within(path,rule->path)&&(!selected||rule->path.size()>selected->path.size()))selected=&*rule;
    }
    if(selected) {
      mode=std::max(mode,selected->mode);
      if(selected->index_generation>0&&generation<=selected->index_generation)mode=2;
      else if(selected->snippet_generation>0&&generation<=selected->snippet_generation)mode=std::max(mode,1);
    }
    if(mode==2)break;
  }
  return mode;
}
void Store::site_publication_sql(sqlite3_context* context,int argc,sqlite3_value** values) {
  if(argc!=6) { sqlite3_result_int(context,2);return; }
  try {
    const auto text=[](sqlite3_value* value) { const auto* bytes=sqlite3_value_text(value);return bytes?std::string(reinterpret_cast<const char*>(bytes),static_cast<std::size_t>(sqlite3_value_bytes(value))):std::string(); };
    auto* store=static_cast<Store*>(sqlite3_user_data(context));
    sqlite3_result_int(context,store->site_publication_mode_locked(text(values[0]),text(values[1]),sqlite3_value_int64(values[2]),text(values[3]),text(values[4]),text(values[5])));
  } catch(...) { sqlite3_result_int(context,2); }
}
void Store::sweep_site_publication_locked() {
  Sql state(db_,"SELECT sweep_after,sweep_complete FROM site_publication_state WHERE singleton=1");state.step();if(state.num(1))return;
  Sql rows(db_,"SELECT d.rowid,d.id,d.url,s.source_kind,s.site_bindings_json,s.site_publication_generation,s.meta_json FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.rowid>? ORDER BY d.rowid LIMIT 128");
  rows.bind(1,state.num(0));auto last=state.num(0);std::int64_t count=0;
  while(rows.step()) {
    ++count;last=rows.num(0);
    if(site_publication_mode_locked(rows.text(3),rows.text(4),rows.num(5),rows.text(2),rows.text(6))>=2) {
      remove_document_index_locked(rows.text(1));
    }
  }
  Sql update(db_,"UPDATE site_publication_state SET sweep_after=?,sweep_complete=? WHERE singleton=1");update.bind(1,last);update.bind(2,count<128?1:0);update.step();
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
  const std::set<std::string> keys={deletion_url_key(logical),deletion_url_key(target)};
  for(const auto& key:keys) {
    // One indexed range at a time avoids sorting/scanning all permanent alias
    // history before discovering the existing 1000-row admission limit.
    Sql rows(db_,"SELECT id,delete_generation,deleted FROM documents WHERE owner=? AND deletion_key=? ORDER BY id LIMIT 1001");
    rows.bind(1,owner);rows.bind(2,key);
    while(rows.step()) {
      require(rows.num(2)==0&&rows.num(1)<=created_sequence,"E_TARGET_DELETED","A URL alias was deleted after this job was created",409);
      auto fence=Json::object();fence.set("document_id",rows.text(0));fence.set("generation",rows.num(1));fences.push(fence);
      require(fences.size()<=1000,"E_QUOTA","Too many retained aliases for one URL",429);
    }
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

std::int64_t Store::publication_sequence_locked() {
  Sql value(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'");
  require(value.step(),"E_STORAGE","Missing publication sequence",500);return value.num(0);
}
Json Store::publication_history_locked(const std::string& owner,const Json& urls,std::int64_t sequence) {
  require(urls.is_array()&&urls.size()<=24&&sequence>=0,"E_WORKER_PROTOCOL","Invalid publication fence",502);
  std::int64_t index=0,snippet=0;std::set<std::string> keys;auto restrictions=Json::array();
  for(std::size_t i=0;i<urls.size();++i) {
    const auto url=urls.at(i).str();keys.insert(key_for(url));keys.insert(key_for(publication_url_identity(url)));
  }
  for(const auto& key:keys) {
    Sql row(db_,"SELECT index_generation,snippet_generation FROM document_publication WHERE owner=? AND url_key=?");
    row.bind(1,owner);row.bind(2,key);if(!row.step())continue;
    if(row.num(0)>sequence)index=std::max(index,row.num(0));
    if(row.num(1)>sequence)snippet=std::max(snippet,row.num(1));
    if(row.num(0)>sequence||row.num(1)>sequence) {
      auto restriction=Json::object();restriction.set("url",publication_url_identity(key));
      restriction.set("index_generation",row.num(0)>sequence?row.num(0):0);restriction.set("snippet_generation",row.num(1)>sequence?row.num(1):0);restrictions.push(restriction);
    }
  }
  auto result=Json::object();result.set("index_generation",index);result.set("snippet_generation",snippet);result.set("restrictions",restrictions);return result;
}
Json Store::seal_publication_locked(const Json& input,const Json& history) {
  auto policy=Json::parse(input.dump(),8*1024*1024);
  if(!policy.has("version")||(history.at("index_generation").num()==0&&history.at("snippet_generation").num()==0))return policy;
  if(history.at("index_generation").num()>0)policy.set("index_allowed",false);
  policy.set("snippet_allowed",false);policy.set("snippet_text","");policy.set("snippet_title","");
  auto diagnostics=policy.at("diagnostics");bool present=false;
  for(std::size_t i=0;i<diagnostics.size();++i)present=present||diagnostics.at(i).str()=="stale_publication_response";
  if(!present)diagnostics.push(Json::string("stale_publication_response"));
  return policy;
}
void Store::seal_frozen_publication_locked(const std::string& owner,const std::string& document_id,const Json& history) {
  const auto restrictions=history.at("restrictions");
  for(std::size_t i=0;i<restrictions.size();++i) {
    const auto restriction=restrictions.at(i);const auto index=restriction.at("index_generation").num(),snippet=restriction.at("snippet_generation").num();
    // Immutable site bindings contain the same actual logical/final/redirect
    // identities. A later A-to-C refresh must not change an old A-to-B row's
    // binding, nor should a B observation suppress an unrelated frozen C row.
    if(index>0) {
      Sql remove(db_,"DELETE FROM session_rows WHERE document_id=? AND session_id IN (SELECT id FROM sessions WHERE owner=?) AND snapshot_id IN (SELECT id FROM snapshots WHERE source_kind='web' AND COALESCE(json_extract(meta_json,'$.page_publication_sequence'),0)<? AND (EXISTS(SELECT 1 FROM json_each(site_bindings_json,'$.urls') WHERE value=?) OR site_bindings_json='{}'))");
      remove.bind(1,document_id);remove.bind(2,owner);remove.bind(3,index);remove.bind(4,restriction.at("url").str());remove.step();
    }
    if(snippet>0) {
      Sql suppress(db_,"UPDATE session_rows SET snippet_suppressed=1 WHERE document_id=? AND session_id IN (SELECT id FROM sessions WHERE owner=?) AND snapshot_id IN (SELECT id FROM snapshots WHERE source_kind='web' AND COALESCE(json_extract(meta_json,'$.page_publication_sequence'),0)<? AND (EXISTS(SELECT 1 FROM json_each(site_bindings_json,'$.urls') WHERE value=?) OR site_bindings_json='{}'))");
      suppress.bind(1,document_id);suppress.bind(2,owner);suppress.bind(3,snippet);suppress.bind(4,restriction.at("url").str());suppress.step();
    }
  }
}
void Store::prepare_web_publication_locked(const std::string& owner,const std::string& url,ParsedDocument& parsed,Json& meta) {
  if(!parsed.publication.has("version")) {
    // Compatibility read artifacts still enter the same persistent fence.
    // Once restricted they must never fall back to canonical snippet text.
    auto policy=Json::object();policy.set("version","bzs-publication-1");policy.set("index_allowed",parsed.index_allowed);
    policy.set("snippet_allowed",parsed.index_allowed);policy.set("follow_allowed",true);
    policy.set("snippet_text",parsed.index_allowed?utf8_prefix(parsed.text,1024*1024):"");policy.set("snippet_title",parsed.index_allowed?parsed.title:"");
    policy.set("directives",Json::array());policy.set("diagnostics",Json::parse(R"(["legacy_read_publication"])"));parsed.publication=policy;
  }
  // A complete HTTP chain has two publication identities. Preserve the final
  // representation before sealing the aggregate with original/intermediate history.
  const bool validated_scope=meta.has("validated_final_publication_scope");
  if(validated_scope) {
    const auto scope=meta.at("validated_final_publication_scope");scope.allowed({"version","url"});
    require(scope.size()==2&&scope.at("version").str()=="bzs-final-publication-scope-1"&&
            scope.at("url").str().size()<=4096&&meta.has("final_url")&&scope.at("url").str()==meta.at("final_url").str()&&
            scope.at("url").str()!=url&&meta.has("http_status")&&meta.at("http_status").num()==200&&
            meta.has("redirects")&&meta.at("redirects").is_array()&&meta.at("redirects").size()>0&&
            normalize_iri(meta.at("redirects").at(meta.at("redirects").size()-1).str())==scope.at("url").str(),
            "E_WORKER_PROTOCOL","Invalid internal final publication scope",502);
  }
  const bool separate_final=meta.has("http_redirect_chain")||validated_scope;
  if(separate_final&&!parsed.representation_publication.has("version"))parsed.representation_publication=parsed.publication;
  const auto sequence=meta.has("page_publication_sequence")?meta.at("page_publication_sequence").num():publication_sequence_locked();
  require(sequence>=0&&sequence<=publication_sequence_locked(),"E_WORKER_PROTOCOL","Invalid read publication sequence",502);
  auto urls=Json::array();std::set<std::string> known;
  const auto add=[&](const std::string& value) { if(known.insert(value).second)urls.push(Json::string(value));require(urls.size()<=24,"E_WORKER_PROTOCOL","Publication chain exceeds its bound",502); };
  add(url);if(meta.has("final_url"))add(meta.at("final_url").str());
  for(const auto* key:{"redirects","crawl_publication_urls"})if(meta.has(key)) {
    const auto chain=meta.at(key);require(chain.is_array()&&chain.size()<=24,"E_WORKER_PROTOCOL","Invalid publication chain",502);
    for(std::size_t i=0;i<chain.size();++i)add(chain.at(i).str());
  }
  if(meta.has("publication_fence")) {
    const auto fence=meta.at("publication_fence");const auto logical=fence.has("url")?fence.at("url").str():url;
    require(epoch_locked(owner)==fence.at("epoch").num(),"E_POLICY_CHANGED","Owner policy changed during this read",409);
    require(deletion_generation_locked(owner,logical)==fence.at("generation").num(),"E_TARGET_DELETED","Document was deleted after this read started",409);
    for(std::size_t i=0;i<urls.size();++i) {
      (void)alias_fences_locked(owner,logical,urls.at(i).str(),fence.at("sequence").num());
    }
  }
  // Crawl stores the physical final page independently of its logical
  // redirect resource; complete_map fences that logical resource separately.
  auto identities=urls;if(meta.has("crawl_publication_urls")) { identities=Json::array();identities.push(Json::string(url)); }
  const auto history=publication_history_locked(owner,identities,sequence);
  const bool stale=history.at("index_generation").num()>0||history.at("snippet_generation").num()>0;
  meta.set("page_publication_sequence",sequence);meta.set("page_publication_history",history);meta.set("page_publication_stale",stale);
  if(stale) {
    // Only restrictions actually observed in this response update the current
    // resource policy. Inherited history seals this immutable artifact alone.
    if(restrictive_publication(parsed.publication))observe_policy_locked(owner,url,url,parsed.publication);
    const auto& final=parsed.representation_publication.has("version")?parsed.representation_publication:parsed.publication;
    if(meta.has("final_url")&&restrictive_publication(final))observe_policy_locked(owner,meta.at("final_url").str(),meta.at("final_url").str(),final);
    parsed.publication=seal_publication_locked(parsed.publication,history);
    if(parsed.representation_publication.has("version")) {
      auto final_history=history;
      if(separate_final) {
        auto final_urls=Json::array();final_urls.push(meta.at("final_url"));
        final_history=publication_history_locked(owner,final_urls,sequence);
      }
      parsed.representation_publication=seal_publication_locked(parsed.representation_publication,final_history);
    }
    if(history.at("index_generation").num()>0)parsed.index_allowed=false;
  }
  // Evaluate history before applying this response's own redirect headers.
  if(meta.has("redirect_publication_observations")) {
    const auto observations=meta.at("redirect_publication_observations");
    require(observations.is_array()&&observations.size()<=24,"E_WORKER_PROTOCOL","Invalid redirect publication observations",502);
    for(std::size_t i=0;i<observations.size();++i) {
      const auto item=observations.at(i);const auto target=item.at("url").str();
      observe_policy_locked(owner,target,target,item.at("publication"));
    }
  }
  // Retain the existing compact encoding and unchanged-304 snapshot identity.
  // Compact only after every history/response restriction has been applied.
  if(separate_final&&parsed.representation_publication.canonical()==parsed.publication.canonical())
    parsed.representation_publication=Json::object();
  if(validated_scope) {
    // The committed final policy already carries its ordinary publication byte
    // charge. Do not persist a second grant or expand HTTP cache metadata.
    auto retained=Json::object();for(const auto& key:meta.keys())if(key!="validated_final_publication_scope")retained.set(key,meta.at(key));
    meta=std::move(retained);
  }
}
void Store::publish_policy_locked(const std::string& owner,const std::string& url,const std::string& snapshot_id,const Json& policy) {
  refresh_site_publication_locked(false);
  const auto key=key_for(url);
  auto record=Json::object();
  for(const auto& field:policy.keys())if(field!="snippet_text"&&field!="snippet_title")record.set(field,policy.at(field));
  const bool index=policy.at("index_allowed").flag(),snippet=policy.at("snippet_allowed").flag();
  bool restricted=!index||!snippet;
  const auto directives=policy.at("directives");
  for(std::size_t i=0;i<directives.size();++i)if(directives.at(i).str()=="data-nosnippet")restricted=true;
  const auto diagnostics=policy.at("diagnostics");
  for(std::size_t i=0;i<diagnostics.size();++i)if(diagnostics.at(i).str()=="site_links_only")restricted=true;
  std::int64_t generation=0,index_generation=0,snippet_generation=0;
  Sql old(db_,"SELECT restriction_generation,index_generation,snippet_generation FROM document_publication WHERE owner=? AND url_key=?");
  old.bind(1,owner);old.bind(2,key);
  if(old.step()) { generation=old.num(0);index_generation=old.num(1);snippet_generation=old.num(2); }
  const auto encoded=record.canonical();
  // A second real response with the same header still fences allowing reads
  // which started after the first response. Lease ownership prevents replayed
  // completions; policy equality cannot stand in for observation identity.
  if(restricted) {
    Sql sequence(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='publication_restriction_sequence'");require(sequence.step(),"E_STORAGE","Missing publication sequence",500);
    require(sequence.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Publication generation exhausted",500);generation=sequence.num(0)+1;
    Sql update(db_,"UPDATE settings SET value=? WHERE key='publication_restriction_sequence'");update.bind(1,generation);update.step();
    if(!index)index_generation=generation;
    snippet_generation=generation;
  }
  Sql save(db_,R"SQL(INSERT INTO document_publication(owner,url_key,snapshot_id,record_json,index_allowed,snippet_allowed,restricted,restriction_generation,observed_at,index_generation,snippet_generation)
 VALUES(?,?,NULLIF(?,''),?,?,?,?,?,?,?,?) ON CONFLICT(owner,url_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,record_json=excluded.record_json,
 index_allowed=excluded.index_allowed,snippet_allowed=excluded.snippet_allowed,restricted=excluded.restricted,
 restriction_generation=excluded.restriction_generation,observed_at=excluded.observed_at,
 index_generation=excluded.index_generation,snippet_generation=excluded.snippet_generation)SQL");
  save.bind(1,owner);save.bind(2,key);save.bind(3,snapshot_id);save.bind(4,encoded);save.bind(5,index?1:0);save.bind(6,snippet?1:0);save.bind(7,restricted?1:0);save.bind(8,generation);save.bind(9,clock_());save.bind(10,index_generation);save.bind(11,snippet_generation);save.step();
  // The current policy is keyed by exact normalized retrieval URL, so historical
  // host/default-port/fragment aliases cannot retain a public index entry.
  // Use the same transactional removal path for both indices and accounting.
  // Freeze IDs before mutating the indices under this publication observation.
  std::vector<std::string> indexed_ids;
  std::set<std::string> existing_grams;
  {
    Sql documents(db_,"SELECT id,EXISTS(SELECT 1 FROM substring_fts f WHERE f.rowid=d.rowid) FROM documents d WHERE owner=? AND (url_key=? OR representation_key=?)");
    documents.bind(1,owner);documents.bind(2,key);documents.bind(3,key);
    while(documents.step()) {
      indexed_ids.push_back(documents.text(0));
      if(documents.num(1))existing_grams.insert(documents.text(0));
    }
  }
  for(const auto& id:indexed_ids)remove_document_index_locked(id);
  if(!index) {
    // Once hidden by noindex, a frozen row cannot reappear after a later allow.
    // Explicit immutable snapshots and evidence remain available to their owner.
    Sql retract(db_,"DELETE FROM session_rows WHERE document_id IN (SELECT id FROM documents WHERE owner=? AND (url_key=? OR representation_key=?)) OR (representation_key=? AND session_id IN (SELECT id FROM sessions WHERE owner=?))");
    retract.bind(1,owner);retract.bind(2,key);retract.bind(3,key);retract.bind(4,key);retract.bind(5,owner);retract.step();
  } else {
    Sql index_rows(db_,"SELECT d.id,s.title,s.canonical_text,d.url FROM documents d JOIN snapshots s ON s.id=d.current_snapshot LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key WHERE d.owner=? AND (d.url_key=? OR d.representation_key=?) AND d.deleted=0 AND s.index_allowed=1 AND COALESCE(p.index_allowed,1)=1 AND COALESCE(rp.index_allowed,1)=1 AND bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,'')<2");
    index_rows.bind(1,owner);index_rows.bind(2,key);index_rows.bind(3,key);
    while(index_rows.step()) {
      // Current snapshot/bytes cannot change inside this publication transaction.
      // Reinstating its prior row consumes no new budget, even after a legacy
      // rebuild exceeded a newly reduced limit. Never let a quota error undo a
      // tightening restriction. Newly exposed rows still require fresh quota.
      index_document_locked(owner,index_rows.text(0),index_rows.text(1),index_rows.text(2),index_rows.text(3),
                            !existing_grams.contains(index_rows.text(0)));
    }
  }
}
Json Store::current_publication_ceiling_locked(const std::string& owner,const std::string& url,const Json& policy) {
  Sql current(db_,"SELECT record_json FROM document_publication WHERE owner=? AND url_key=?");
  current.bind(1,owner);current.bind(2,key_for(url));
  return current.step()?meet_publication(policy,Json::parse(current.text(0),8*1024*1024)):policy;
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
  std::lock_guard lock(mutex_);require_principal_active_locked(owner);auto fence=Json::object();fence.set("epoch",epoch_locked(owner));fence.set("sequence",delete_sequence_locked());fence.set("generation",deletion_generation_locked(owner,url));fence.set("publication_sequence",publication_sequence_locked());
  Sql current(db_,"SELECT current_snapshot FROM documents WHERE owner=? AND url=?");current.bind(1,owner);current.bind(2,url);fence.set("snapshot_id",current.step()?current.text(0):"");return fence;
}
void Store::observe_web_publication(const std::string& owner,const std::string& url,const std::string& final_url,const Json& policy,const Json& fence) {
  std::lock_guard lock(mutex_);checked(sqlite3_exec(db_,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr));
  try {
    require_principal_active_locked(owner);
    if(epoch_locked(owner)==fence.at("epoch").num()&&deletion_generation_locked(owner,url)==fence.at("generation").num()) {
      bool live=true;try { (void)alias_fences_locked(owner,url,final_url,fence.at("sequence").num()); } catch(const Error& error) { if(error.code!="E_TARGET_DELETED")throw;live=false; }
      if(live)observe_policy_locked(owner,url,final_url,policy);
    }
    checked(sqlite3_exec(db_,"COMMIT",nullptr,nullptr,nullptr));
  } catch(...) { sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);throw; }
}

}
