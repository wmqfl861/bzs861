#include "bzs/store.hpp"
#include "bzs/error.hpp"
#include "bzs/publication.hpp"
#include "bzs/crawl_trap.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>

namespace bzs {
namespace {
  void checked(int rc) {
    if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
    const auto primary=rc&0xff;
    if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
    if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
    throw Error("E_STORAGE","Crawl storage operation failed",500);
  }
  struct Sql {
    sqlite3_stmt* p=nullptr;
    Sql(sqlite3* db,const char* query) { checked(sqlite3_prepare_v2(db,query,-1,&p,nullptr)); }
    ~Sql() { sqlite3_finalize(p); }
    Sql(const Sql&)=delete;Sql& operator=(const Sql&)=delete;
    void bind(int i,const std::string& value) { checked(sqlite3_bind_text(p,i,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)); }
    void bind(int i,std::int64_t value) { checked(sqlite3_bind_int64(p,i,value)); }
    bool step() { auto rc=sqlite3_step(p);checked(rc);return rc==SQLITE_ROW; }
    std::int64_t num(int i)const { return sqlite3_column_int64(p,i); }
    std::string text(int i)const { auto v=sqlite3_column_text(p,i);return v?std::string(reinterpret_cast<const char*>(v),static_cast<std::size_t>(sqlite3_column_bytes(p,i))):""; }
  };
}

bool Store::crawl_edge_locked(const std::string& id,const std::string& owner,const Json& candidate,bool followed,const std::string& reason) {
  Sql existing(db_,"SELECT 1 FROM crawl_edges WHERE job_id=? AND parent=? AND url=? AND source=?");
  existing.bind(1,id);existing.bind(2,candidate.at("parent").str());existing.bind(3,candidate.at("url").str());existing.bind(4,candidate.at("source").str());
  if(existing.step()) {
    Sql update(db_,"UPDATE crawl_edges SET followed=MAX(followed,?),reason=CASE WHEN followed!=0 OR ?!=0 THEN '' ELSE ? END WHERE job_id=? AND parent=? AND url=? AND source=?");
    update.bind(1,followed?1:0);update.bind(2,followed?1:0);update.bind(3,reason);update.bind(4,id);update.bind(5,candidate.at("parent").str());update.bind(6,candidate.at("url").str());update.bind(7,candidate.at("source").str());update.step();return true;
  }
  Sql count(db_,"SELECT (SELECT COUNT(*) FROM crawl_edges WHERE job_id=?),(SELECT COUNT(*) FROM crawl_edges)");count.bind(1,id);count.step();
  if(count.num(0)>=10000||count.num(1)>=50000) { map_partial_locked(id,"E_CRAWL_EDGE_LIMIT");return false; }
  Sql insert(db_,"INSERT INTO crawl_edges(job_id,position,parent,url,source,depth,followed,reason,parent_generation,target_generation) SELECT ?,COALESCE(MAX(position)+1,0),?,?,?,?,?,?,?,? FROM crawl_edges WHERE job_id=?");
  insert.bind(1,id);insert.bind(2,candidate.at("parent").str());insert.bind(3,candidate.at("url").str());insert.bind(4,candidate.at("source").str());insert.bind(5,candidate.at("depth").num());insert.bind(6,followed?1:0);insert.bind(7,reason);
  insert.bind(8,deletion_generation_locked(owner,candidate.at("parent").str()));insert.bind(9,deletion_generation_locked(owner,candidate.at("url").str()));insert.bind(10,id);insert.step();return true;
}

Json Store::crawl_results_locked(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit) {
  // job_results already performs the one bounded sweep in this transaction.
  refresh_site_publication_locked(false);
  const auto job=map_job_locked(owner,id);require(job.at("kind").str()=="web.crawl","E_JOB_KIND","Crawl results require a crawl job",409);
  require(offset>=0&&offset<=1000&&limit>=1&&limit<=1000,"E_SCHEMA","Invalid crawl result window");
  Sql rows(db_,R"SQL(SELECT r.position,r.url,r.fetch_url,r.parent,r.source,r.depth,r.state,r.snapshot_id,r.receipt_json,r.error_code,
 s.document_id,d.deleted,p.record_json,lp.record_json,rp.record_json,s.index_allowed,
 bzs_site_publication(s.source_kind,s.site_bindings_json,s.site_publication_generation,d.url,s.meta_json,r.url) AS site_mode FROM map_resources r LEFT JOIN snapshots s ON s.id=r.snapshot_id
 LEFT JOIN documents d ON d.id=s.document_id AND d.owner=? LEFT JOIN document_publication p ON p.owner=d.owner AND p.url_key=d.url_key
 LEFT JOIN document_publication lp ON lp.owner=d.owner AND lp.url_key=r.url
 LEFT JOIN document_publication rp ON rp.owner=d.owner AND rp.url_key=d.representation_key
 WHERE r.job_id=? AND r.kind='page' AND r.position>=? ORDER BY r.position LIMIT ?)SQL");
  rows.bind(1,owner);rows.bind(2,id);rows.bind(3,offset);rows.bind(4,limit+1);
  auto items=Json::array();auto next=offset;bool more=false;
  while(rows.step()) {
    if(items.size()==static_cast<std::size_t>(limit)) { more=true;break; }
    auto item=Json::object();item.set("type","page");item.set("position",rows.num(0));item.set("url",rows.text(1));item.set("final_url",rows.text(2));item.set("parent",rows.text(3));item.set("source",rows.text(4));item.set("depth",rows.num(5));item.set("state",rows.text(6));
    const bool available=!rows.text(7).empty()&&!rows.text(10).empty()&&rows.num(11)==0;
    item.set("available",available);item.set("body_read",available&&rows.text(6)=="succeeded");item.set("snapshot_id",available?Json::string(rows.text(7)):Json());item.set("document_id",available?Json::string(rows.text(10)):Json());
    item.set("error_code",rows.text(9).empty()?Json():Json::string(rows.text(9)));
    if(!rows.text(8).empty()) { const auto receipt=Json::parse(rows.text(8));if(receipt.has("trap"))item.set("trap",receipt.at("trap")); }
    Json policy;
    if(available)for(int column:{12,13,14})if(!rows.text(column).empty()) {
      const auto current=Json::parse(rows.text(column));policy=policy.is_null()?current:meet_publication(policy,current);
    }
    if(available&&rows.num(16)>0&&!policy.is_null()) {
      policy.set("snippet_allowed",false);
      if(rows.num(16)>=2) { policy.set("index_allowed",false);policy.set("follow_allowed",false); }
      auto diagnostics=policy.at("diagnostics");diagnostics.push(Json::string(rows.num(16)>=2?"site_publication_withdrawn":"site_links_only"));policy.set("diagnostics",diagnostics);
    }
    auto summary=Json::object();if(!policy.is_null())for(const auto& field:policy.keys())if(field!="snippet_text"&&field!="snippet_title")summary.set(field,policy.at(field));
    item.set("publication",policy.is_null()?Json():summary);
    const bool indexed=available&&rows.num(15)!=0&&rows.num(16)<2&&(policy.is_null()||policy.at("index_allowed").flag());item.set("indexed",indexed);
    if(available&&!rows.text(8).empty()) {
      auto receipt=Json::parse(rows.text(8));receipt.set("publication",item.at("publication"));receipt.set("indexed",indexed);item.set("receipt",receipt);
    }
    items.push(item);next=rows.num(0)+1;
  }
  auto out=Json::object();out.set("job_id",id);out.set("kind","web.crawl");out.set("state",job.at("state"));out.set("partial",job.at("partial"));out.set("stop_reason",job.at("stop_reason"));out.set("offset",offset);out.set("limit",limit);out.set("items",items);out.set("total",job.at("page_count"));out.set("total_items",job.at("page_count"));out.set("has_more",more);out.set("next_offset",more?Json::integer(next):Json());return out;
}

Json Store::crawl_edges(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit) {
  require(offset>=0&&offset<=10000&&limit>=1&&limit<=1000,"E_SCHEMA","Invalid crawl edge window");
  std::lock_guard lock(mutex_);const auto job=map_job_locked(owner,id);require(job.at("kind").str()=="web.crawl","E_JOB_KIND","Edge results require a crawl job",409);
  // Deletion tombstones retract both ends, including original URL aliases.
  const auto predicate=" AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.owner=? AND ((d.url_key=e.url AND (d.deleted!=0 OR d.delete_generation>j.created_delete_sequence)) OR (d.url_key=e.parent AND (d.deleted!=0 OR d.delete_generation>j.created_delete_sequence))))";
  const auto query=std::string("SELECT e.position,e.parent,e.url,e.source,e.depth,e.followed,e.reason,t.decision_json FROM crawl_edges e JOIN jobs j ON j.id=e.job_id LEFT JOIN crawl_trap_edges t ON t.job_id=e.job_id AND t.position=e.position WHERE e.job_id=? AND e.position>=?")+predicate+" ORDER BY e.position LIMIT ?";
  Sql rows(db_,query.c_str());rows.bind(1,id);rows.bind(2,offset);rows.bind(3,owner);rows.bind(4,limit+1);
  auto items=Json::array();auto next=offset;bool more=false;
  while(rows.step()) {
    if(items.size()==static_cast<std::size_t>(limit)) { more=true;break; }
    auto item=Json::object();item.set("position",rows.num(0));item.set("parent",rows.text(1));item.set("url",rows.text(2));item.set("source",rows.text(3));item.set("depth",rows.num(4));item.set("followed",rows.num(5)!=0);item.set("reason",rows.text(6).empty()?Json():Json::string(rows.text(6)));
    if(!rows.text(7).empty()&&rows.num(5)==0)item.set("trap",Json::parse(rows.text(7)));
    items.push(item);next=rows.num(0)+1;
  }
  const auto total_query=std::string("SELECT COUNT(*) FROM crawl_edges e JOIN jobs j ON j.id=e.job_id WHERE e.job_id=?")+predicate;
  Sql total(db_,total_query.c_str());total.bind(1,id);total.bind(2,owner);total.step();
  auto out=Json::object();out.set("job_id",id);out.set("items",items);out.set("offset",offset);out.set("limit",limit);out.set("total",total.num(0));out.set("has_more",more);out.set("next_offset",more?Json::integer(next):Json());return out;
}
}
