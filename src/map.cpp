#include "bzs/store.hpp"
#include "bzs/sites.hpp"
#include "bzs/publication.hpp"
#include "bzs/crawl_trap.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <limits>
#include <set>
#include <tuple>

namespace bzs {
namespace {
  void checked(int rc) {
    if(rc==SQLITE_OK||rc==SQLITE_ROW||rc==SQLITE_DONE)return;
    auto primary=rc&0xff;
    if(primary==SQLITE_FULL)throw Error("E_STORAGE_FULL","Storage capacity exhausted",507);
    if(primary==SQLITE_BUSY||primary==SQLITE_LOCKED)throw Error("E_STORAGE_BUSY","Storage is busy",503);
    throw Error("E_STORAGE","Map storage operation failed",500);
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
  struct Tx {
    sqlite3* db;bool done=false;
    explicit Tx(sqlite3* db_):db(db_) { checked(sqlite3_exec(db,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr)); }
    ~Tx() { if(!done)sqlite3_exec(db,"ROLLBACK",nullptr,nullptr,nullptr); }
    void commit() { checked(sqlite3_exec(db,"COMMIT",nullptr,nullptr,nullptr));done=true; }
  };
  std::int64_t count(sqlite3* db,const char* query) { Sql s(db,query);s.step();return s.num(0); }
  std::int64_t path_depth(const std::string& url) {
    auto path=map_path(url);std::int64_t depth=0;bool component=false;
    for(char c:path) { if(c=='/')component=false;else if(!component) { ++depth;component=true; } }
    return depth;
  }
  std::int64_t plus_saturated(std::int64_t now,std::int64_t delta) {
    return now>std::numeric_limits<std::int64_t>::max()-delta?std::numeric_limits<std::int64_t>::max():now+delta;
  }
  Json cached_robots(sqlite3* db,const std::string& owner,const std::string& origin,const std::string& digest,
                     std::int64_t now,std::string* id=nullptr) {
    Sql row(db,"SELECT id,record_json FROM robots_cache WHERE owner=? AND origin=? AND manifest_sha256=? AND fetched_at<=? AND expires_at>? ORDER BY fetched_at DESC,rowid DESC LIMIT 1");
    row.bind(1,owner);row.bind(2,origin);row.bind(3,digest);row.bind(4,now);row.bind(5,now);
    if(!row.step())return Json();
    if(id)*id=row.text(0);
    return Json::parse(row.text(1));
  }
  // Compare one shared URL set, so a sitemap index cannot multiply the queue
  // independently of the public candidate budget.
  bool room(sqlite3* db,const std::string& id,const std::string& url,std::int64_t max_urls) {
    Sql exists(db,"SELECT 1 FROM map_resources WHERE job_id=? AND url=? AND kind!='robots' UNION ALL SELECT 1 FROM map_candidates WHERE job_id=? AND url=? LIMIT 1");
    exists.bind(1,id);exists.bind(2,url);exists.bind(3,id);exists.bind(4,url);if(exists.step())return true;
    Sql total(db,"SELECT COUNT(*) FROM (SELECT url FROM map_resources WHERE job_id=? AND kind!='robots' UNION SELECT url FROM map_candidates WHERE job_id=?)");
    total.bind(1,id);total.bind(2,id);total.step();return total.num(0)<max_urls;
  }
  bool variant_room(sqlite3* db,const std::string& id,const std::string& url) {
    const auto base=url.substr(0,url.find('?'));
    Sql rows(db,"SELECT url FROM map_resources WHERE job_id=? AND kind!='robots' UNION SELECT url FROM map_candidates WHERE job_id=?");
    rows.bind(1,id);rows.bind(2,id);std::int64_t variants=0;
    while(rows.step()) { auto existing=rows.text(0);if(existing==url)return true;if(existing.substr(0,existing.find('?'))==base)++variants; }
    return variants<4;
  }
}

void Store::map_partial_locked(const std::string& id,const std::string& reason) {
  Sql update(db_,"UPDATE map_jobs SET partial=1,stop_reason=CASE WHEN stop_reason='' THEN ? ELSE stop_reason END WHERE job_id=?");
  update.bind(1,reason);update.bind(2,id);update.step();
}
bool Store::map_candidate_locked(const std::string& id,const std::string& owner,const Json& candidate) {
  const auto url=candidate.at("url").str();
  Sql job(db_,"SELECT j.created_delete_sequence,m.max_urls FROM jobs j JOIN map_jobs m ON m.job_id=j.id WHERE j.id=? AND j.owner=?");
  job.bind(1,id);job.bind(2,owner);require(job.step(),"E_NOT_FOUND","Map job not found",404);
  Sql deleted(db_,"SELECT deleted,delete_generation FROM documents WHERE owner=? AND url=?");deleted.bind(1,owner);deleted.bind(2,url);
  if(deleted.step()&&(deleted.num(0)!=0||deleted.num(1)>job.num(0))) { map_partial_locked(id,"E_TARGET_DELETED");return false; }
  Sql existing(db_,"SELECT 1 FROM map_candidates WHERE job_id=? AND url=?");existing.bind(1,id);existing.bind(2,url);if(existing.step())return true;
  if(!room(db_,id,url,job.num(1))) { map_partial_locked(id,"E_MAP_URL_LIMIT");return false; }
  if(!variant_room(db_,id,url)) { map_partial_locked(id,"E_MAP_VARIANT_LIMIT");return false; }
  if(count(db_,"SELECT COUNT(*) FROM map_candidates")>=20000) { map_partial_locked(id,"E_QUOTA");return false; }
  const auto history_id=candidate.has("history_document_id")?candidate.at("history_document_id").str():"";
  const auto history_generation=candidate.has("history_generation")?candidate.at("history_generation").num():0;
  if(!history_id.empty()) {
    Sql visible(db_,"SELECT 1 FROM documents WHERE id=? AND owner=? AND deleted=0 AND delete_generation=?");
    visible.bind(1,history_id);visible.bind(2,owner);visible.bind(3,history_generation);if(!visible.step())return false;
  }
  Sql insert(db_,"INSERT INTO map_candidates(job_id,position,url,parent,source,depth,path_depth,deletion_generation,history_document_id,history_generation) SELECT ?,COALESCE(MAX(position)+1,0),?,?,?,?,?,?,?,? FROM map_candidates WHERE job_id=?");
  insert.bind(1,id);insert.bind(2,url);insert.bind(3,candidate.at("parent").str());insert.bind(4,candidate.at("source").str());
  insert.bind(5,candidate.at("depth").num());insert.bind(6,path_depth(url));insert.bind(7,deletion_generation_locked(owner,url));
  insert.bind(8,history_id);insert.bind(9,history_generation);insert.bind(10,id);insert.step();return true;
}
bool Store::map_resource_locked(const std::string& id,const std::string& owner,const std::string& url,
                               const std::string& kind,std::int64_t depth,const std::string& parent,const std::string& source) {
  Sql old(db_,"SELECT 1 FROM map_resources WHERE job_id=? AND url=?");old.bind(1,id);old.bind(2,url);if(old.step())return true;
  Sql job(db_,"SELECT j.created_delete_sequence,m.max_urls,m.max_depth,j.kind FROM jobs j JOIN map_jobs m ON m.job_id=j.id WHERE j.id=?");job.bind(1,id);job.step();
  const bool crawl=job.text(3)=="web.crawl"&&kind!="robots";
  const bool seed=kind=="page"&&source=="seed"&&parent.empty()&&depth==0;
  if(depth>job.num(2)) { map_partial_locked(id,"E_MAP_DEPTH_LIMIT");return false; }
  if(kind!="robots") {
    try { (void)alias_fences_locked(owner,url,url,job.num(0)); }
    catch(const Error& error) { if(error.code!="E_TARGET_DELETED")throw;map_partial_locked(id,error.code);return false; }
    Sql deleted(db_,"SELECT deleted,delete_generation FROM documents WHERE owner=? AND url=?");deleted.bind(1,owner);deleted.bind(2,url);
    if(deleted.step()&&(deleted.num(0)!=0||deleted.num(1)>job.num(0))) { map_partial_locked(id,"E_TARGET_DELETED");return false; }
    if(!room(db_,id,url,job.num(1))) { map_partial_locked(id,"E_MAP_URL_LIMIT");return false; }
    if(!variant_room(db_,id,url)) { map_partial_locked(id,"E_MAP_VARIANT_LIMIT");return false; }
  }
  if(count(db_,"SELECT COUNT(*) FROM map_resources")>=20000) { map_partial_locked(id,"E_QUOTA");return false; }
  if(crawl) {
    crawl_trap_ensure(db_,id);
    const auto blocked=crawl_trap_decide(db_,id,url,parent,seed);
    if(blocked) { map_partial_locked(id,blocked.reason);return false; }
  }
  Sql insert(db_,"INSERT INTO map_resources(job_id,position,url,fetch_url,kind,depth,deletion_generation,parent,source) SELECT ?,COALESCE(MAX(position)+1,0),?,?,?,?,?,?,? FROM map_resources WHERE job_id=?");
  insert.bind(1,id);insert.bind(2,url);insert.bind(3,url);insert.bind(4,kind);insert.bind(5,depth);
  insert.bind(6,deletion_generation_locked(owner,url));insert.bind(7,parent);insert.bind(8,source);insert.bind(9,id);insert.step();
  if(crawl)crawl_trap_admit(db_,id,url,parent,seed);
  return true;
}
Json Store::map_submit(const std::string& owner,const Json& args,const SiteRegistry& sites) { return frontier_submit(owner,args,sites,false); }
Json Store::crawl_submit(const std::string& owner,const Json& args,const SiteRegistry& sites) { return frontier_submit(owner,args,sites,true); }
Json Store::frontier_submit(const std::string& owner,const Json& args,const SiteRegistry& sites,bool crawl) {
  if(crawl)args.allowed({"site_id","seeds","idempotency_key","scope_path","timeout_ms","per_request_timeout_ms","max_requests","max_bytes","max_urls","max_depth","max_pages"});
  else args.allowed({"site_id","seeds","idempotency_key","scope_path","timeout_ms","per_request_timeout_ms","max_requests","max_bytes","max_urls","max_depth"});
  const std::string job_kind=crawl?"web.crawl":"web.map";
  require_identifier(owner);auto key=required_string(args,"idempotency_key",128);require_identifier(key);
  auto site_id=required_string(args,"site_id",64);const auto* site=sites.find(site_id);
  require(site,"E_SITE_REVIEW","Site is not registered",403);site->require_review(clock_());
  auto scope=args.has("scope_path")?map_scope_path(required_string(args,"scope_path",2048)):"";
  if(!scope.empty())require(std::any_of(site->path_prefixes.begin(),site->path_prefixes.end(),[&](const auto& path){return path_within(scope,path);}),
                          "E_SCOPE","Requested scope must narrow an approved site path",403);
  auto seeds=args.at("seeds");require(seeds.is_array()&&seeds.size()>=1&&seeds.size()<=20,"E_SCHEMA","Map requires 1..20 explicit seeds");
  auto timeout=int_option(args,"timeout_ms",600000,1,3600000),per_request=int_option(args,"per_request_timeout_ms",20000,1,55000);
  auto max_requests=int_option(args,"max_requests",100,1,1000),max_bytes=int_option(args,"max_bytes",16777216,1,67108864);
  auto max_urls=int_option(args,"max_urls",1000,1,1000),max_depth=int_option(args,"max_depth",3,0,8);
  const auto max_pages=crawl?int_option(args,"max_pages",std::min<std::int64_t>(100,max_urls),1,max_urls):0;
  require(static_cast<std::int64_t>(seeds.size())<=max_urls,"E_SCHEMA","URL budget cannot be smaller than the explicit seed list");
  auto parameters=Json::object(),urls=Json::array();std::set<std::string> unique;
  for(std::size_t i=0;i<seeds.size();++i) {
    auto url=map_url(seeds.at(i).str());site->require_scope(url,scope);
    require(unique.insert(url).second,"E_SCHEMA","Duplicate map seed");urls.push(Json::string(url));
  }
  parameters.set("kind",job_kind);if(crawl)parameters.set("max_pages",max_pages);parameters.set("site_id",site_id);parameters.set("manifest_sha256",sites.manifest_digest());
  parameters.set("seeds",urls);parameters.set("scope_path",scope);parameters.set("timeout_ms",timeout);parameters.set("per_request_timeout_ms",per_request);
  parameters.set("max_requests",max_requests);parameters.set("max_bytes",max_bytes);parameters.set("max_urls",max_urls);parameters.set("max_depth",max_depth);
  const auto fingerprint=sha256(parameters.canonical());
  std::lock_guard lock(mutex_);Tx tx(db_);const auto now=clock_();site->require_review(now);const auto auth=epoch_locked(owner);
  Sql old(db_,"SELECT id,fingerprint,kind FROM jobs WHERE owner=? AND idempotency_key=?");old.bind(1,owner);old.bind(2,key);
  if(old.step()) { require(old.text(1)==fingerprint&&old.text(2)==job_kind,"E_IDEMPOTENCY_CONFLICT","Idempotency key reused with different parameters",409);
    auto out=map_job_locked(owner,old.text(0));out.set("reused",true);tx.commit();return out; }
  require(count(db_,"SELECT COUNT(*) FROM map_jobs")<512&&count(db_,"SELECT COUNT(*) FROM map_resources")+static_cast<std::int64_t>(seeds.size())+1<=20000,
          "E_QUOTA","Map retention quota reached",429);
  Sql retained(db_,"SELECT COUNT(*) FROM jobs WHERE owner=?");retained.bind(1,owner);retained.step();require(retained.num(0)<10000,"E_QUOTA","Job retention quota reached",429);
  require(now<=std::numeric_limits<std::int64_t>::max()-timeout,"E_SCHEMA","Clock exceeds deadline range");
  const auto id=random_id();
  Sql job(db_,"INSERT INTO jobs(id,owner,fixture_id,idempotency_key,fingerprint,state,auth_epoch,max_attempts,deadline,created_at,created_delete_sequence,kind)VALUES(?,?,'',?,?,'queued',?,1,?,?,?,?)");
  job.bind(1,id);job.bind(2,owner);job.bind(3,key);job.bind(4,fingerprint);job.bind(5,auth);job.bind(6,now+timeout);job.bind(7,now);job.bind(8,delete_sequence_locked());job.bind(9,job_kind);job.step();
  Sql config(db_,"INSERT INTO map_jobs(job_id,site_id,manifest_sha256,origin,scope_path,max_urls,max_depth,max_requests,max_bytes,per_request_timeout_ms,max_pages)VALUES(?,?,?,?,?,?,?,?,?,?,?)");
  config.bind(1,id);config.bind(2,site_id);config.bind(3,sites.manifest_digest());config.bind(4,site->origin);config.bind(5,scope);
  config.bind(6,max_urls);config.bind(7,max_depth);config.bind(8,max_requests);config.bind(9,max_bytes);config.bind(10,per_request);config.bind(11,max_pages);config.step();
  // The server's pinned policy is separate from the client's canonical
  // fingerprint, so an upgraded live job still reuses its original key.
  if(crawl)crawl_trap_ensure(db_,id);
  require(map_resource_locked(id,owner,site->origin+"/robots.txt","robots",0),"E_QUOTA","Cannot retain robots resource",429);
  for(std::size_t i=0;i<urls.size();++i)require(map_resource_locked(id,owner,urls.at(i).str(),crawl?"page":"auto",0),"E_SCHEMA","Seed exceeds deletion or map variation policy");
  // History is metadata from this owner only. It remains a candidate: this does
  // not read any saved body or create a snapshot, read receipt, or evidence.
  if(!crawl) {
  Sql history(db_,"SELECT id,url,delete_generation FROM documents WHERE owner=? AND deleted=0 AND current_snapshot IS NOT NULL ORDER BY id LIMIT 10001");history.bind(1,owner);
  std::int64_t scanned=0;
  while(history.step()) {
    if(++scanned>10000) { map_partial_locked(id,"E_HISTORY_SCAN_LIMIT");break; }
    try {
      auto url=map_url(history.text(1));if(!site->allows(url,scope))continue;
      auto candidate=Json::object();candidate.set("url",url);candidate.set("parent","");candidate.set("source","history");candidate.set("depth",std::int64_t{0});
      candidate.set("history_document_id",history.text(0));candidate.set("history_generation",history.num(2));
      map_candidate_locked(id,owner,candidate);
    } catch(const Error& error) { if(error.code=="E_STORAGE"||error.code=="E_STORAGE_FULL"||error.code=="E_STORAGE_BUSY")throw; }
  }
  }
  auto out=map_job_locked(owner,id);out.set("reused",false);tx.commit();return out;
}
Json Store::map_job_locked(const std::string& owner,const std::string& id) {
  Sql j(db_,"SELECT j.state,j.epoch,j.attempts,j.lease_until,j.deadline,j.error_code,m.site_id,m.manifest_sha256,m.origin,m.scope_path,m.max_urls,m.max_depth,m.max_requests,m.max_bytes,m.per_request_timeout_ms,m.charged_requests,m.charged_bytes,m.external_requests,m.content_bytes,m.unknown_requests,m.unknown_bytes,m.partial,m.stop_reason,j.kind,m.max_pages,m.used_pages FROM jobs j JOIN map_jobs m ON m.job_id=j.id WHERE j.id=? AND j.owner=? AND j.kind IN ('web.map','web.crawl')");
  j.bind(1,id);j.bind(2,owner);require(j.step(),"E_NOT_FOUND","Map job not found",404);
  auto out=Json::object();out.set("job_id",id);out.set("kind",j.text(23));out.set("state",j.text(0));out.set("lease_epoch",j.num(1));out.set("dispatches",j.num(2));
  out.set("lease_until_ms",j.num(3));out.set("deadline_ms",j.num(4));out.set("error_code",j.text(5).empty()?Json():Json::string(j.text(5)));
  out.set("site_id",j.text(6));out.set("manifest_sha256",j.text(7));out.set("origin",j.text(8));out.set("scope_path",j.text(9).empty()?Json():Json::string(j.text(9)));
  out.set("max_urls",j.num(10));out.set("max_depth",j.num(11));out.set("max_query_variants",std::int64_t{4});out.set("body_read",false);
  out.set("partial",j.num(21)!=0);out.set("stop_reason",j.text(22).empty()?Json():Json::string(j.text(22)));
  Sql filtered(db_,"SELECT filtered_urls FROM map_jobs WHERE job_id=?");filtered.bind(1,id);filtered.step();out.set("filtered_urls",filtered.num(0));
  auto budget=Json::object();budget.set("max_requests",j.num(12));budget.set("max_bytes",j.num(13));budget.set("per_request_timeout_ms",j.num(14));
  budget.set("charged_requests",j.num(15));budget.set("charged_bytes",j.num(16));budget.set("remaining_requests",j.num(12)-j.num(15));budget.set("remaining_bytes",j.num(13)-j.num(16));out.set("budget",budget);
  auto usage=Json::object();usage.set("external_requests",j.num(17));usage.set("content_bytes",j.num(18));usage.set("unknown_requests_charged",j.num(19));usage.set("unknown_bytes_charged",j.num(20));
  usage.set("reserved_requests",j.num(15)-j.num(17)-j.num(19));usage.set("reserved_bytes",j.num(16)-j.num(18)-j.num(20));
  usage.set("scope","job_lifetime");usage.set("model_calls",std::int64_t{0});usage.set("accounting","known_usage_plus_conservative_grants");out.set("usage",usage);out.set("external_requests",j.num(17));
  Sql r(db_,"SELECT COUNT(*),COALESCE(SUM(state='succeeded'),0),COALESCE(SUM(state='failed'),0),COALESCE(SUM(state='pending'),0),COALESCE(SUM(state='running'),0) FROM map_resources WHERE job_id=? AND kind!='robots'");r.bind(1,id);r.step();
  out.set("discovery_documents",r.num(0));out.set("succeeded_documents",r.num(1));out.set("failed_documents",r.num(2));out.set("pending_documents",r.num(3));out.set("running_documents",r.num(4));
  Sql c(db_,"SELECT COUNT(*) FROM map_candidates WHERE job_id=?");c.bind(1,id);c.step();out.set("candidate_count",c.num(0));out.set("used_pages",j.num(25));
  if(j.text(23)=="web.crawl") {
    out.set("max_pages",j.num(24));out.set("remaining_pages",j.num(24)-j.num(25));
    Sql pages(db_,"SELECT COUNT(*),COALESCE(SUM(state='succeeded'),0),COALESCE(SUM(state='failed'),0),COALESCE(SUM(state='skipped'),0) FROM map_resources WHERE job_id=? AND kind='page'");pages.bind(1,id);pages.step();
    out.set("page_count",pages.num(0));out.set("total_items",pages.num(0));out.set("body_read",pages.num(1)>0);out.set("succeeded_pages",pages.num(1));out.set("failed_pages",pages.num(2));out.set("skipped_pages",pages.num(3));
    Sql edges(db_,"SELECT COUNT(*) FROM crawl_edges WHERE job_id=?");edges.bind(1,id);edges.step();out.set("edge_count",edges.num(0));
    out.set("trap_policy",crawl_trap_summary(db_,id));
  }
  return out;
}
void Store::settle_map_job_locked(const std::string& id) {
  Sql job(db_,"SELECT state FROM jobs WHERE id=? AND kind IN ('web.map','web.crawl')");job.bind(1,id);if(!job.step())return;
  Sql totals(db_,"SELECT COALESCE(SUM(state='pending'),0),COALESCE(SUM(state='running'),0),COALESCE(SUM(state='failed'),0),COALESCE(SUM(state='succeeded' AND kind!='robots'),0) FROM map_resources WHERE job_id=?");totals.bind(1,id);totals.step();
  if(totals.num(1)>0)return;
  auto current=job.text(0);std::string state;
  if(current=="pausing"||current=="paused")state="paused";
  else if(current=="cancelling"||current=="cancelled")state="cancelled";
  else if(totals.num(0)>0)state="queued";
  else state=totals.num(2)==0&&totals.num(3)>0?"succeeded":"failed";
  Sql update(db_,"UPDATE jobs SET state=?,lease_until=0,error_code=CASE WHEN ?='failed' THEN (SELECT CASE WHEN stop_reason='' THEN 'E_MAP_FAILED' ELSE stop_reason END FROM map_jobs WHERE job_id=?) ELSE NULL END WHERE id=?");
  update.bind(1,state);update.bind(2,state);update.bind(3,id);update.bind(4,id);update.step();
}
bool Store::owns_map_lease_locked(const MapLease& lease) {
  Sql h(db_,"SELECT 1 FROM map_hops h JOIN jobs j ON j.id=h.job_id WHERE h.job_id=? AND h.position=? AND h.resource_position=? AND h.attempt_id=? AND h.lease_epoch=? AND h.url=? AND h.state='running' AND j.owner=? AND j.kind IN ('web.map','web.crawl')");
  h.bind(1,lease.job_id);h.bind(2,lease.position);h.bind(3,lease.resource_position);h.bind(4,lease.attempt_id);h.bind(5,lease.epoch);h.bind(6,lease.url);h.bind(7,lease.owner);return h.step();
}
bool Store::live_map_lease_locked(const MapLease& lease) {
  if(!owns_map_lease_locked(lease))return false;
  Sql j(db_,"SELECT state,epoch,lease_until,deadline,auth_epoch FROM jobs WHERE id=? AND owner=?");j.bind(1,lease.job_id);j.bind(2,lease.owner);
  if(!j.step()||j.text(0)!="running"||j.num(1)!=lease.epoch||j.num(2)<=clock_()||j.num(3)<=clock_()||lease.deadline_ms<=clock_())return false;
  Sql hop(db_,"SELECT admission_json FROM map_hops WHERE job_id=? AND position=?");hop.bind(1,lease.job_id);hop.bind(2,lease.position);if(!hop.step())return false;
  const auto target_generation=Json::parse(hop.text(0)).at("target_generation").num();
  const auto admission=Json::parse(hop.text(0));
  return j.num(4)==epoch_locked(lease.owner)&&deletion_generation_locked(lease.owner,lease.logical_url)==lease.deletion_generation&&
         deletion_generation_locked(lease.owner,lease.url)==target_generation&&
         (!admission.has("alias_fences")||alias_fences_live_locked(admission.at("alias_fences")));
}
bool Store::map_lease_live(const MapLease& lease) { std::lock_guard lock(mutex_);return live_map_lease_locked(lease); }
void Store::reconcile_map_usage_locked(const MapLease& lease,std::int64_t requests,std::int64_t bytes) {
  Sql row(db_,"SELECT request_grant,byte_grant FROM map_hops WHERE job_id=? AND position=? AND attempt_id=? AND state='running'");
  row.bind(1,lease.job_id);row.bind(2,lease.position);row.bind(3,lease.attempt_id);require(row.step()&&row.num(0)==lease.request_grant&&row.num(1)==lease.byte_grant,"E_LEASE_EXPIRED","Map grant is no longer current",409);
  const bool known=requests>=0&&bytes>=0;
  require(known?requests<=lease.request_grant&&bytes<=lease.byte_grant&&(requests!=0||bytes==0):requests==-1&&bytes==-1,"E_WORKER_PROTOCOL","Map usage exceeds durable grant",502);
  Sql budget(db_,"UPDATE map_jobs SET charged_requests=charged_requests-?,charged_bytes=charged_bytes-?,external_requests=external_requests+?,content_bytes=content_bytes+?,unknown_requests=unknown_requests+?,unknown_bytes=unknown_bytes+? WHERE job_id=?");
  budget.bind(1,known?lease.request_grant-requests:0);budget.bind(2,known?lease.byte_grant-bytes:0);budget.bind(3,known?requests:0);budget.bind(4,known?bytes:0);
  budget.bind(5,known?0:lease.request_grant);budget.bind(6,known?0:lease.byte_grant);budget.bind(7,lease.job_id);budget.step();
  Sql hop(db_,"UPDATE map_hops SET external_requests=?,content_bytes=?,usage_unknown=?,request_grant=0,byte_grant=0 WHERE job_id=? AND position=?");
  hop.bind(1,known?requests:0);hop.bind(2,known?bytes:0);hop.bind(3,known?0:1);hop.bind(4,lease.job_id);hop.bind(5,lease.position);hop.step();
  if(known&&requests==0&&lease.kind=="page") {
    Sql started(db_,"SELECT 1 FROM map_hops WHERE job_id=? AND resource_position=? AND (external_requests>0 OR usage_unknown!=0) LIMIT 1");
    started.bind(1,lease.job_id);started.bind(2,lease.resource_position);
    if(!started.step()) {
      Sql refund(db_,"UPDATE map_jobs SET used_pages=used_pages-(SELECT page_charged FROM map_resources WHERE job_id=? AND position=?) WHERE job_id=?");
      refund.bind(1,lease.job_id);refund.bind(2,lease.resource_position);refund.bind(3,lease.job_id);refund.step();
      Sql clear(db_,"UPDATE map_resources SET page_charged=0 WHERE job_id=? AND position=?");clear.bind(1,lease.job_id);clear.bind(2,lease.resource_position);clear.step();
    }
  }
  // An undispatched denial refunds the site request reservation too. Unknown
  // outcomes keep it. Retired RPC attempts return their cumulative usage.
  Sql site(db_,"UPDATE site_request_state SET requests=requests-?,active_attempt='',active_until=0 WHERE origin=? AND active_attempt=?");
  site.bind(1,known?lease.request_grant-requests:0);site.bind(2,lease.origin);site.bind(3,lease.attempt_id);site.step();
}
void Store::settle_map_jobs_locked() {
  Sql rows(db_,"SELECT j.id,j.owner,m.site_id,m.manifest_sha256,m.origin,h.url,r.url,h.kind,h.attempt_id,h.lease_epoch,h.position,r.position,r.depth,r.redirect_hop,r.deletion_generation,h.request_grant,h.byte_grant FROM map_hops h JOIN jobs j ON j.id=h.job_id JOIN map_jobs m ON m.job_id=j.id JOIN map_resources r ON r.job_id=h.job_id AND r.position=h.resource_position WHERE h.state='running' AND j.lease_until<=?");
  rows.bind(1,clock_());std::vector<MapLease> stale;
  while(rows.step())stale.push_back({rows.text(0),rows.text(1),rows.text(2),rows.text(3),rows.text(4),rows.text(5),rows.text(6),rows.text(7),rows.text(8),rows.num(9),rows.num(10),rows.num(11),rows.num(12),rows.num(13),rows.num(14),rows.num(15),rows.num(16),0,0,{}});
  for(const auto& lease:stale) {
    reconcile_map_usage_locked(lease,-1,-1);
    Sql hop(db_,"UPDATE map_hops SET state='failed',error_code='E_EXECUTION_UNKNOWN' WHERE job_id=? AND position=?");hop.bind(1,lease.job_id);hop.bind(2,lease.position);hop.step();
    Sql resource(db_,"UPDATE map_resources SET state='failed',error_code='E_EXECUTION_UNKNOWN' WHERE job_id=? AND position=?");resource.bind(1,lease.job_id);resource.bind(2,lease.resource_position);resource.step();
    map_partial_locked(lease.job_id,"E_EXECUTION_UNKNOWN");settle_map_job_locked(lease.job_id);
  }
}

std::optional<MapLease> Store::lease_next_map(const SiteRegistry& sites) {
  std::lock_guard lock(mutex_);Tx tx(db_);settle_map_jobs_locked();
  Sql pending(db_,"SELECT j.id FROM jobs j JOIN map_jobs m ON m.job_id=j.id WHERE j.kind IN ('web.map','web.crawl') AND j.state='queued' ORDER BY m.last_dispatch_sequence,j.created_at,j.id");
  std::vector<std::string> ids;while(pending.step())ids.push_back(pending.text(0));
  for(const auto& id:ids)for(;;) {
    Sql job(db_,"SELECT j.owner,j.epoch,j.deadline,j.auth_epoch,j.created_delete_sequence,m.site_id,m.manifest_sha256,m.origin,m.scope_path,m.max_requests-m.charged_requests,m.max_bytes-m.charged_bytes,m.per_request_timeout_ms,m.max_pages,m.used_pages,j.kind FROM jobs j JOIN map_jobs m ON m.job_id=j.id WHERE j.id=? AND j.state='queued'");
    job.bind(1,id);if(!job.step())break;
    const auto owner=job.text(0),origin=job.text(7);const auto now=clock_();const auto* site=sites.find(job.text(5));
    std::string stop;
    if(job.num(3)!=epoch_locked(owner))stop="E_POLICY_CHANGED";
    else if(job.text(6)!=sites.manifest_digest())stop="E_SITE_MANIFEST_MISMATCH";
    else if(!site||!site->reviewed(now))stop="E_SITE_REVIEW";
    else if(job.num(2)<=now)stop="E_DEADLINE";
    Sql active_sites(db_,"SELECT manifest_sha256 FROM site_publication_state WHERE singleton=1");active_sites.step();
    if(!active_sites.text(0).empty()&&active_sites.text(0)!=sites.manifest_digest())stop="E_SITE_MANIFEST_MISMATCH";
    if(!stop.empty()) {
      Sql all(db_,"UPDATE map_resources SET state='failed',error_code=? WHERE job_id=? AND state='pending'");all.bind(1,stop);all.bind(2,id);all.step();
      map_partial_locked(id,stop);settle_map_job_locked(id);break;
    }
    const bool crawl=job.text(14)=="web.crawl";
    if(crawl)crawl_trap_ensure(db_,id);
    Sql resource(db_,"SELECT position,url,fetch_url,kind,depth,redirect_hop,deletion_generation,page_charged,parent,source,publication_sequence FROM map_resources WHERE job_id=? AND state='pending' ORDER BY CASE WHEN kind='robots' THEN 0 ELSE 1 END,depth,position LIMIT 1");resource.bind(1,id);
    if(!resource.step()) { settle_map_job_locked(id);break; }
    const auto kind=resource.text(3),url=resource.text(2),logical=resource.text(1);
    if(kind=="page"&&resource.num(7)==0&&job.num(13)>=job.num(12)) {
      Sql stop_pages(db_,"UPDATE map_resources SET state='skipped',error_code='E_PAGE_BUDGET' WHERE job_id=? AND state='pending'");stop_pages.bind(1,id);stop_pages.step();
      map_partial_locked(id,"E_PAGE_BUDGET");settle_map_job_locked(id);break;
    }
    std::string robots_id;auto robots=cached_robots(db_,owner,origin,sites.manifest_digest(),now,&robots_id);
    if(kind=="robots"&&!robots.is_null()) {
      Sql skip(db_,"UPDATE map_resources SET state='cached' WHERE job_id=? AND position=?");skip.bind(1,id);skip.bind(2,resource.num(0));skip.step();settle_map_job_locked(id);continue;
    }
    if(kind!="robots"&&robots.is_null()) {
      Sql metadata(db_,"SELECT position,state FROM map_resources WHERE job_id=? AND kind='robots'");metadata.bind(1,id);require(metadata.step(),"E_STORAGE","Map robots phase is missing",500);
      if(metadata.text(1)=="failed") {
        Sql denied(db_,"UPDATE map_resources SET state='failed',error_code='E_ROBOTS_UNAVAILABLE' WHERE job_id=? AND position=?");denied.bind(1,id);denied.bind(2,resource.num(0));denied.step();
        map_partial_locked(id,"E_ROBOTS_UNAVAILABLE");settle_map_job_locked(id);continue;
      }
      Sql refresh(db_,"UPDATE map_resources SET state='pending',fetch_url=url,redirect_hop=0 WHERE job_id=? AND position=?");refresh.bind(1,id);refresh.bind(2,metadata.num(0));refresh.step();continue;
    }
    if(kind!="robots"&&robots.at("state").str()=="deny_all") {
      Sql denied(db_,"UPDATE map_resources SET state='failed',error_code='E_ROBOTS_DISALLOWED' WHERE job_id=? AND position=?");
      denied.bind(1,id);denied.bind(2,resource.num(0));denied.step();map_partial_locked(id,"E_ROBOTS_DISALLOWED");settle_map_job_locked(id);continue;
    }
    Json aliases=Json::array();
    if(kind!="robots") {
      try { aliases=alias_fences_locked(owner,logical,url,job.num(4)); }
      catch(const Error& error) { if(error.code!="E_TARGET_DELETED")throw;stop=error.code; }
    }
    if(kind=="robots") {
      if(map_origin(url)!=origin||resource.num(5)>5)stop="E_SCOPE";
    } else if(!site->allows(url,job.text(8))||!site->allows(logical,job.text(8)))stop="E_SCOPE";
    Sql deleted(db_,"SELECT deleted,delete_generation FROM documents WHERE owner=? AND url=?");deleted.bind(1,owner);deleted.bind(2,url);
    if(deletion_generation_locked(owner,logical)!=resource.num(6)||
        (deleted.step()&&(deleted.num(0)!=0||deleted.num(1)>job.num(4))))stop="E_TARGET_DELETED";
    if(!stop.empty()) {
      Sql denied(db_,"UPDATE map_resources SET state='failed',error_code=? WHERE job_id=? AND position=?");denied.bind(1,stop);denied.bind(2,id);denied.bind(3,resource.num(0));denied.step();
      map_partial_locked(id,stop);settle_map_job_locked(id);continue;
    }
    if(crawl&&kind!="robots") {
      const bool seed=kind=="page"&&url==logical&&resource.num(5)==0&&resource.text(9)=="seed"&&resource.text(8).empty()&&resource.num(4)==0;
      const auto blocked=crawl_trap_decide(db_,id,url,resource.text(8),seed,true);
      if(blocked) {
        auto receipt=Json::object();receipt.set("trap",blocked.detail);
        Sql denied(db_,"UPDATE map_resources SET state='skipped',error_code=?,receipt_json=? WHERE job_id=? AND position=?");
        denied.bind(1,blocked.reason);denied.bind(2,receipt.canonical());denied.bind(3,id);denied.bind(4,resource.num(0));denied.step();
        crawl_trap_skip_edges(db_,id,logical,blocked);map_partial_locked(id,blocked.reason);settle_map_job_locked(id);continue;
      }
    }
    if(job.num(9)<=0)stop="E_REQUEST_BUDGET";else if(job.num(10)<=0)stop="E_BYTE_BUDGET";
    else if(count(db_,"SELECT COUNT(*) FROM map_hops")>=50000)stop="E_QUOTA";
    else if(kind=="robots"&&count(db_,"SELECT COUNT(*) FROM robots_cache")>=512)stop="E_QUOTA";
    if(!stop.empty()) {
      Sql denied(db_,"UPDATE map_resources SET state='failed',error_code=? WHERE job_id=? AND state='pending'");denied.bind(1,stop);denied.bind(2,id);denied.step();
      map_partial_locked(id,stop);settle_map_job_locked(id);break;
    }
    Sql ensure(db_,"INSERT OR IGNORE INTO site_request_state(origin,window_started)VALUES(?,?)");ensure.bind(1,origin);ensure.bind(2,now);ensure.step();
    Sql lane(db_,"SELECT window_started,requests,next_start,cooldown_until,active_until FROM site_request_state WHERE origin=?");lane.bind(1,origin);lane.step();
    // Never transfer an unknown old request into a fresh quota window before its
    // active grant expires. The actual worker independently enforces start pace.
    if(lane.num(4)>now||lane.num(2)>now||lane.num(3)>now)break;
    auto window=lane.num(0),used=lane.num(1);
    if(now>=window&&now-window>=86400000) { window=now;used=0; }
    if(used>=site->request_quota) {
      Sql denied(db_,"UPDATE map_resources SET state='failed',error_code='E_SITE_QUOTA' WHERE job_id=? AND state='pending'");denied.bind(1,id);denied.step();
      map_partial_locked(id,"E_SITE_QUOTA");settle_map_job_locked(id);break;
    }
    require(job.num(1)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Lease generation exhausted",500);
    Sql sequence(db_,"SELECT COALESCE(MAX(position)+1,0) FROM map_hops WHERE job_id=?");sequence.bind(1,id);sequence.step();
    const auto timeout=std::min(job.num(11),job.num(2)-now);
    const auto lease_until=now+std::min(job.num(2)-now,timeout+5000);
    MapLease lease{id,owner,site->id,sites.manifest_digest(),origin,url,logical,kind,random_id(),job.num(1)+1,sequence.num(0),resource.num(0),resource.num(4),resource.num(5),resource.num(6),1,
        std::min<std::int64_t>(job.num(10),kind=="robots"?512*1024:2*1024*1024),timeout,now+timeout,kind=="robots"?Json():robots};
    lease.publication_sequence=resource.num(10)<0?publication_sequence_locked():resource.num(10);
    if(kind=="page"&&resource.num(10)<0) {
      Sql current(db_,"SELECT current_snapshot FROM documents WHERE owner=? AND url=?");current.bind(1,owner);current.bind(2,logical);
      const auto snapshot=current.step()?current.text(0):"";
      Sql capture(db_,"UPDATE map_resources SET publication_sequence=?,publication_snapshot_id=? WHERE job_id=? AND position=?");
      capture.bind(1,lease.publication_sequence);capture.bind(2,snapshot);capture.bind(3,id);capture.bind(4,lease.resource_position);capture.step();
    }
    auto admission=Json::object();admission.set("site_id",site->id);admission.set("manifest_sha256",sites.manifest_digest());admission.set("origin",origin);admission.set("url",url);
    admission.set("logical_url",logical);admission.set("robots_cache_id",robots_id.empty()?Json():Json::string(robots_id));
    admission.set("robots_sha256",robots.is_null()?Json():Json::string(sha256(robots.canonical())));
    admission.set("alias_fences",aliases);admission.set("deadline_ms",lease.deadline_ms);admission.set("target_generation",deletion_generation_locked(owner,url));
    Sql current_page(db_,"SELECT current_snapshot FROM documents WHERE owner=? AND url=?");current_page.bind(1,owner);current_page.bind(2,url);
    admission.set("publication_snapshot_id",current_page.step()?current_page.text(0):"");
    admission.set("publication_sequence",lease.publication_sequence);
    Sql hop(db_,"INSERT INTO map_hops(job_id,position,resource_position,url,kind,attempt_id,lease_epoch,state,byte_grant,request_grant,admission_json)VALUES(?,?,?,?,?,?,?,'running',?,1,?)");
    hop.bind(1,id);hop.bind(2,lease.position);hop.bind(3,lease.resource_position);hop.bind(4,url);hop.bind(5,kind);hop.bind(6,lease.attempt_id);hop.bind(7,lease.epoch);hop.bind(8,lease.byte_grant);hop.bind(9,admission.dump());hop.step();
    Sql sequence_value(db_,"SELECT CAST(value AS INTEGER) FROM settings WHERE key='map_dispatch_sequence'");sequence_value.step();
    require(sequence_value.num(0)<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Dispatch sequence exhausted",500);
    const auto dispatch_sequence=sequence_value.num(0)+1;
    Sql sequence_update(db_,"UPDATE settings SET value=? WHERE key='map_dispatch_sequence'");sequence_update.bind(1,dispatch_sequence);sequence_update.step();
    Sql budget(db_,"UPDATE map_jobs SET charged_requests=charged_requests+1,charged_bytes=charged_bytes+?,used_pages=used_pages+?,last_dispatch_sequence=? WHERE job_id=?");
    budget.bind(1,lease.byte_grant);budget.bind(2,kind=="page"&&resource.num(7)==0?1:0);budget.bind(3,dispatch_sequence);budget.bind(4,id);budget.step();
    if(kind=="page") { Sql charged(db_,"UPDATE map_resources SET page_charged=1 WHERE job_id=? AND position=?");charged.bind(1,id);charged.bind(2,lease.resource_position);charged.step(); }
    Sql reserve(db_,"UPDATE site_request_state SET window_started=?,requests=?,next_start=?,active_attempt=?,active_until=? WHERE origin=?");
    reserve.bind(1,window);reserve.bind(2,used+1);reserve.bind(3,plus_saturated(now,site->min_interval_ms));reserve.bind(4,lease.attempt_id);reserve.bind(5,lease_until);reserve.bind(6,origin);reserve.step();
    Sql claim(db_,"UPDATE map_resources SET state='running' WHERE job_id=? AND position=?");claim.bind(1,id);claim.bind(2,lease.resource_position);claim.step();
    Sql parent(db_,"UPDATE jobs SET state='running',epoch=?,attempts=attempts+1,lease_until=? WHERE id=?");parent.bind(1,lease.epoch);parent.bind(2,lease_until);parent.bind(3,id);parent.step();
    tx.commit();return lease;
  }
  tx.commit();return std::nullopt;
}

void Store::fail_map(const MapLease& lease,const std::string& error,std::int64_t requests,std::int64_t bytes,const Json* observation) {
  std::lock_guard lock(mutex_);Tx tx(db_);if(!owns_map_lease_locked(lease)) { tx.commit();return; }
  if(observation&&requests==1&&live_map_lease_locked(lease)) {
    Sql created(db_,"SELECT created_delete_sequence FROM jobs WHERE id=?");created.bind(1,lease.job_id);created.step();
    bool live=true;try { (void)alias_fences_locked(lease.owner,lease.logical_url,lease.url,created.num(0)); } catch(const Error& e) { if(e.code!="E_TARGET_DELETED")throw;live=false; }
    if(live) {
      observe_policy_locked(lease.owner,lease.logical_url,lease.url,*observation);
      auto receipt=Json::object(),policy=Json::object();for(const auto& key:observation->keys())if(key!="snippet_text"&&key!="snippet_title")policy.set(key,observation->at(key));
      receipt.set("publication_observation",policy);receipt.set("status","error");receipt.set("error_code",error);
      Sql saved(db_,"UPDATE map_hops SET response_json=? WHERE job_id=? AND position=?");saved.bind(1,receipt.dump());saved.bind(2,lease.job_id);saved.bind(3,lease.position);saved.step();
    }
  }
  reconcile_map_usage_locked(lease,requests,bytes);
  Sql hop(db_,"UPDATE map_hops SET state='failed',error_code=? WHERE job_id=? AND position=?");hop.bind(1,error);hop.bind(2,lease.job_id);hop.bind(3,lease.position);hop.step();
  Sql resource(db_,"UPDATE map_resources SET state='failed',error_code=? WHERE job_id=? AND position=?");resource.bind(1,error);resource.bind(2,lease.job_id);resource.bind(3,lease.resource_position);resource.step();
  map_partial_locked(lease.job_id,error);settle_map_job_locked(lease.job_id);tx.commit();
}
bool Store::complete_map(const MapLease& lease,const Json& response,const SiteRegistry& sites,
                         std::int64_t requests,std::int64_t bytes,const SourceDocument* page,const ParsedDocument* parsed,const Json* meta,const Json* observation) {
  std::lock_guard lock(mutex_);Tx tx(db_);if(!owns_map_lease_locked(lease)) { tx.commit();return false; }
  std::string error;const auto now=clock_();const auto* site=sites.find(lease.site_id);
  if(!live_map_lease_locked(lease))error=deletion_generation_locked(lease.owner,lease.logical_url)!=lease.deletion_generation?"E_TARGET_DELETED":"E_LEASE_EXPIRED";
  else if(sites.manifest_digest()!=lease.manifest_sha256)error="E_SITE_MANIFEST_MISMATCH";
  else if(!site||!site->reviewed(now))error="E_SITE_REVIEW";
  Sql active_sites(db_,"SELECT manifest_sha256 FROM site_publication_state WHERE singleton=1");active_sites.step();
  if(!active_sites.text(0).empty()&&active_sites.text(0)!=sites.manifest_digest())error="E_SITE_MANIFEST_MISMATCH";
  Sql config(db_,"SELECT m.scope_path,m.max_urls,m.max_depth,j.kind,j.created_delete_sequence FROM map_jobs m JOIN jobs j ON j.id=m.job_id WHERE m.job_id=?");config.bind(1,lease.job_id);require(config.step(),"E_STORAGE","Map configuration missing",500);
  const auto scope=config.text(0);const bool crawl=config.text(3)=="web.crawl";
  if(error.empty()&&crawl)crawl_trap_ensure(db_,lease.job_id);
  reconcile_map_usage_locked(lease,requests,bytes);
  const auto status=response.at("status").str();std::string next="succeeded";
  auto receipt=Json::object();receipt.set("status",status);receipt.set("http_status",response.at("http_status"));
  if(response.has("robots_decision"))receipt.set("robots_decision",response.at("robots_decision"));
  receipt.set("url",lease.url);receipt.set("external_requests",requests);receipt.set("content_bytes",bytes);
  if(error.empty()&&response.has("retry_after_ms")) {
    const auto delay=response.at("retry_after_ms").num();
    Sql cooldown(db_,"UPDATE site_request_state SET cooldown_until=MAX(cooldown_until,?) WHERE origin=?");cooldown.bind(1,plus_saturated(now,delay));cooldown.bind(2,lease.origin);cooldown.step();
  }
  if(error.empty()&&observation&&lease.kind=="page"&&requests==1) {
    try {
      (void)alias_fences_locked(lease.owner,lease.logical_url,lease.url,config.num(4));
      if(status!="ok"||!page||!parsed)observe_policy_locked(lease.owner,lease.logical_url,lease.url,*observation);
      auto recorded=Json::object();for(const auto& key:observation->keys())if(key!="snippet_text"&&key!="snippet_title")recorded.set(key,observation->at(key));
      receipt.set("publication_observation",recorded);
      if(status=="redirect") {
        Sql current(db_,"SELECT publication_ceiling_json FROM map_resources WHERE job_id=? AND position=?");current.bind(1,lease.job_id);current.bind(2,lease.resource_position);current.step();
        auto ceiling=meet_publication(*observation,Json::parse(current.text(0)));
        Sql saved(db_,"UPDATE map_resources SET publication_ceiling_json=? WHERE job_id=? AND position=?");saved.bind(1,ceiling.canonical());saved.bind(2,lease.job_id);saved.bind(3,lease.resource_position);saved.step();
      }
    } catch(const Error& e) { if(e.code!="E_TARGET_DELETED")throw;error=e.code; }
  }
  if(error.empty()&&status=="redirect") {
    auto target=map_url(response.at("redirect_url").str());
    if(lease.redirect_hop>=5)error="E_REDIRECT_LIMIT";
    else if(map_origin(target)!=lease.origin||(lease.kind!="robots"&&!site->allows(target,scope)))error="E_SCOPE";
    else {
      Sql seen(db_,"SELECT 1 FROM map_hops WHERE job_id=? AND resource_position=? AND url=?");seen.bind(1,lease.job_id);seen.bind(2,lease.resource_position);seen.bind(3,target);
      if(seen.step())error="E_REDIRECT_LOOP";
      else {
        if(crawl&&lease.kind!="robots") {
          // A new Location is automatic navigation even when the logical
          // page was an explicit seed. Only a target itself in the original
          // seed set has its own durable admission exemption.
          const auto blocked=crawl_trap_decide(db_,lease.job_id,target,lease.url);
          if(blocked) {
            error=blocked.reason;receipt.set("trap",blocked.detail);receipt.set("redirect_url",target);
            auto candidate=Json::object();candidate.set("url",target);candidate.set("parent",lease.url);candidate.set("source","redirect");candidate.set("depth",lease.depth);
            if(crawl_edge_locked(lease.job_id,lease.owner,candidate,false,blocked.reason))crawl_trap_record_edge(db_,lease.job_id,candidate,blocked);
            crawl_trap_skip_edges(db_,lease.job_id,lease.logical_url,blocked);
            auto saved_receipt=Json::object();saved_receipt.set("trap",blocked.detail);
            Sql saved(db_,"UPDATE map_resources SET receipt_json=? WHERE job_id=? AND position=?");saved.bind(1,saved_receipt.canonical());saved.bind(2,lease.job_id);saved.bind(3,lease.resource_position);saved.step();
          } else crawl_trap_admit(db_,lease.job_id,target,lease.url);
        }
        if(error.empty()) {
        Sql redirect(db_,"UPDATE map_resources SET fetch_url=?,redirect_hop=redirect_hop+1 WHERE job_id=? AND position=?");redirect.bind(1,target);redirect.bind(2,lease.job_id);redirect.bind(3,lease.resource_position);redirect.step();
        next="pending";receipt.set("redirect_url",target);
        }
      }
    }
  } else if(error.empty()&&lease.kind=="robots"&&response.has("robots")) {
    auto record=response.at("robots");const auto encoded=record.dump();
    if(count(db_,"SELECT COUNT(*) FROM robots_cache")>=512)error="E_QUOTA";
    else {
      auto cache_id=random_id();Sql save(db_,"INSERT INTO robots_cache(id,owner,origin,manifest_sha256,record_json,fetched_at,expires_at)VALUES(?,?,?,?,?,?,?)");
      save.bind(1,cache_id);save.bind(2,lease.owner);save.bind(3,lease.origin);save.bind(4,lease.manifest_sha256);save.bind(5,encoded);save.bind(6,record.at("fetched_at_ms").num());save.bind(7,record.at("expires_at_ms").num());save.step();
      receipt.set("robots_cache_id",cache_id);receipt.set("robots_sha256",sha256(record.canonical()));
      // Sitemap declarations are discovery documents, unlike ordinary links.
      auto maps=record.at("sitemaps");
      for(std::size_t i=0;i<maps.size();++i) {
        std::string url;
        try { url=map_url(maps.at(i).str()); } catch(const Error&) {}
        if(url.empty()||!site->allows(url,scope)) {
          Sql filtered(db_,"UPDATE map_jobs SET filtered_urls=filtered_urls+1 WHERE job_id=?");filtered.bind(1,lease.job_id);filtered.step();continue;
        }
        map_resource_locked(lease.job_id,lease.owner,url,"sitemap",0);
      }
    }
    if(status!="ok"&&error.empty())error=response.at("error_code").str();
  } else if(error.empty()&&status=="ok"&&lease.kind!="robots") {
    auto discovery=response.at("discovery");
    if(discovery.at("parse_status").str()!="ok")error="E_DISCOVERY_PARSE";
    else {
      if(lease.kind=="page") {
        require(page&&parsed&&meta&&crawl,"E_WORKER_PROTOCOL","Crawl page artifact is missing",502);
        require(page->url==lease.url&&parsed->publication.has("version"),"E_WORKER_PROTOCOL","Crawl publication identity is invalid",502);
        // Final and logical aliases are both fenced; an explicit later import
        // cannot revive the in-flight task that observed the older generation.
        (void)alias_fences_locked(lease.owner,lease.logical_url,lease.url,config.num(4));
        Sql bound(db_,"SELECT admission_json FROM map_hops WHERE job_id=? AND position=?");bound.bind(1,lease.job_id);bound.bind(2,lease.position);bound.step();
        const auto admission=Json::parse(bound.text(0));const auto generation=admission.at("target_generation").num();
        Sql capture(db_,"SELECT publication_sequence,publication_snapshot_id FROM map_resources WHERE job_id=? AND position=?");capture.bind(1,lease.job_id);capture.bind(2,lease.resource_position);capture.step();
        checked(sqlite3_exec(db_,"SAVEPOINT crawl_body",nullptr,nullptr,nullptr));
        Json snapshot,logical_history;
        try {
          auto page_meta=Json::parse(meta->dump()),route=Json::array();std::set<std::string> route_urls;
          Sql hops(db_,"SELECT url FROM map_hops WHERE job_id=? AND resource_position=? AND kind='page' AND (state='succeeded' OR position=?) ORDER BY position LIMIT 6");
          hops.bind(1,lease.job_id);hops.bind(2,lease.resource_position);hops.bind(3,lease.position);
          while(hops.step())if(route_urls.insert(hops.text(0)).second)route.push(Json::string(hops.text(0)));
          for(const auto& url:{lease.logical_url,lease.url})if(route_urls.insert(url).second)route.push(Json::string(url));
          page_meta.set("crawl_publication_urls",route);
          logical_history=publication_history_locked(lease.owner,route,capture.num(0));
          page_meta.set("page_publication_sequence",capture.num(0));
          auto fence=Json::object();fence.set("epoch",epoch_locked(lease.owner));fence.set("sequence",config.num(4));fence.set("url",lease.logical_url);fence.set("generation",lease.deletion_generation);
          if(admission.has("publication_snapshot_id"))fence.set("snapshot_id",admission.at("publication_snapshot_id"));
          page_meta.set("publication_fence",fence);
          snapshot=ingest_web_locked(lease.owner,*page,*parsed,page_meta,generation,true);
          checked(sqlite3_exec(db_,"RELEASE crawl_body",nullptr,nullptr,nullptr));
        } catch(const Error& failure) {
          checked(sqlite3_exec(db_,"ROLLBACK TO crawl_body",nullptr,nullptr,nullptr));checked(sqlite3_exec(db_,"RELEASE crawl_body",nullptr,nullptr,nullptr));
          if(failure.code!="E_QUOTA")throw;
          error=failure.code;
          if(restrictive_publication(parsed->publication)) {
            observe_policy_locked(lease.owner,lease.logical_url,lease.url,parsed->publication);
            auto recorded=Json::object();for(const auto& key:parsed->publication.keys())if(key!="snippet_text"&&key!="snippet_title")recorded.set(key,parsed->publication.at(key));
            receipt.set("publication_observation",recorded);
          }
        }
        if(error.empty()) {
        if(lease.logical_url!=lease.url) {
          Sql current(db_,"SELECT publication_ceiling_json FROM map_resources WHERE job_id=? AND position=?");current.bind(1,lease.job_id);current.bind(2,lease.resource_position);current.step();
          const auto logical_policy=meet_publication(parsed->publication,Json::parse(current.text(0)));
          const bool stale=logical_history.at("index_generation").num()>0||logical_history.at("snippet_generation").num()>0;
          Sql logical(db_,"SELECT d.id,d.current_snapshot,s.site_bindings_json,COALESCE(json_extract(s.meta_json,'$.page_publication_sequence'),0) FROM documents d LEFT JOIN snapshots s ON s.id=d.current_snapshot WHERE d.owner=? AND d.url=?");logical.bind(1,lease.owner);logical.bind(2,lease.logical_url);
          const bool exists=logical.step();
          if(stale&&exists)seal_frozen_publication_locked(lease.owner,logical.text(0),logical_history);
          bool matching=!exists;
          if(exists) {
            const auto bindings=Json::parse(logical.text(2));const auto restrictions=logical_history.at("restrictions");
            if(!bindings.has("urls"))matching=true;
            else for(std::size_t i=0;i<restrictions.size();++i)for(std::size_t j=0;j<bindings.at("urls").size();++j)
              matching=matching||restrictions.at(i).at("url").str()==bindings.at("urls").at(j).str();
          }
          if(!stale)publish_policy_locked(lease.owner,lease.logical_url,snapshot.at("snapshot_id").str(),logical_policy);
          else if((!exists||logical.text(1)==capture.text(1)||(capture.text(1)=="@legacy"&&logical.num(3)<std::max(logical_history.at("index_generation").num(),logical_history.at("snippet_generation").num())))&&matching)
            publish_policy_locked(lease.owner,lease.logical_url,snapshot.at("snapshot_id").str(),seal_publication_locked(logical_policy,logical_history));
        }
        auto page_receipt=Json::object();page_receipt.set("snapshot_id",snapshot.at("snapshot_id"));page_receipt.set("document_id",snapshot.at("document_id"));
        page_receipt.set("final_url",lease.url);page_receipt.set("revision",snapshot.at("revision"));page_receipt.set("body_read",true);
        auto policy=Json::object();const auto stored_policy=snapshot.at("publication");for(const auto& key:stored_policy.keys())if(key!="snippet_text"&&key!="snippet_title")policy.set(key,stored_policy.at(key));
        page_receipt.set("publication",policy);
        const auto trap=crawl_trap_observe_body(db_,lease.job_id,lease.url,parsed->text);
        if(!trap.is_null())page_receipt.set("trap",trap);
        Sql saved(db_,"UPDATE map_resources SET snapshot_id=?,receipt_json=? WHERE job_id=? AND position=?");saved.bind(1,snapshot.at("snapshot_id").str());saved.bind(2,page_receipt.dump());saved.bind(3,lease.job_id);saved.bind(4,lease.resource_position);saved.step();
        receipt.set("page",page_receipt);
        }
      }
      auto candidates=discovery.at("candidates");
      if(discovery.at("truncated").flag())map_partial_locked(lease.job_id,"E_MAP_PARSE_LIMIT");
      for(std::size_t i=0;error.empty()&&i<candidates.size();++i) {
        auto candidate=Json::parse(candidates.at(i).dump());std::string url;
        try { url=map_url(candidate.at("url").str()); } catch(const Error&) {}
        if(url.empty()||!site->allows(url,scope)) {
          Sql filtered(db_,"UPDATE map_jobs SET filtered_urls=filtered_urls+1 WHERE job_id=?");filtered.bind(1,lease.job_id);filtered.step();continue;
        }
        candidate.set("url",url);
        std::string blocked;
        CrawlTrapDecision trap;
        if(candidate.at("depth").num()>config.num(2))blocked="E_MAP_DEPTH_LIMIT";
        if(crawl&&lease.kind=="page"&&!parsed->publication.at("follow_allowed").flag())blocked="E_NOFOLLOW";
        if(crawl&&blocked.empty()) {
          trap=crawl_trap_decide(db_,lease.job_id,url,candidate.at("parent").str());
          if(trap)blocked=trap.reason;
        }
        if(!blocked.empty()) {
          if(blocked!="E_NOFOLLOW")map_partial_locked(lease.job_id,blocked);
          if(crawl&&crawl_edge_locked(lease.job_id,lease.owner,candidate,false,blocked)&&trap)crawl_trap_record_edge(db_,lease.job_id,candidate,trap);
          continue;
        }
        if(crawl&&!crawl_edge_locked(lease.job_id,lease.owner,candidate,false,"E_FRONTIER_LIMIT"))continue;
        const bool retained=map_candidate_locked(lease.job_id,lease.owner,candidate);
        bool followed=false;
        if(retained&&(crawl||!candidate.at("document").is_null()))
          followed=map_resource_locked(lease.job_id,lease.owner,url,candidate.at("document").is_null()?"page":"sitemap",candidate.at("depth").num(),candidate.at("parent").str(),candidate.at("source").str());
        if(crawl)crawl_edge_locked(lease.job_id,lease.owner,candidate,followed,followed?"":"E_FRONTIER_LIMIT");
      }
      receipt.set("document_type",discovery.at("document_type"));receipt.set("candidate_count",static_cast<std::int64_t>(candidates.size()));
      receipt.set("diagnostics",discovery.at("diagnostics"));
    }
  } else if(error.empty())error=response.has("error_code")?response.at("error_code").str():"E_MAP_FETCH";
  if(!error.empty()) { next="failed";map_partial_locked(lease.job_id,error); }
  Sql finish(db_,"UPDATE map_resources SET state=?,error_code=? WHERE job_id=? AND position=?");finish.bind(1,next);finish.bind(2,error);finish.bind(3,lease.job_id);finish.bind(4,lease.resource_position);finish.step();
  Sql hop(db_,"UPDATE map_hops SET state=?,error_code=?,response_json=? WHERE job_id=? AND position=?");hop.bind(1,error.empty()?std::string("succeeded"):std::string("failed"));hop.bind(2,error);hop.bind(3,receipt.dump());hop.bind(4,lease.job_id);hop.bind(5,lease.position);hop.step();
  settle_map_job_locked(lease.job_id);tx.commit();return error.empty();
}
Json Store::control_map_locked(const std::string& owner,const std::string& id,const std::string& action) {
  auto old=map_job_locked(owner,id);auto state=old.at("state").str();std::string next;
  if(action=="pause") {
    if(state=="queued")next="paused";else if(state=="running")next="pausing";else if(state=="paused"||state=="pausing")next=state;
  } else if(action=="resume"&&state=="paused")next="queued";
  else if(action=="cancel") {
    if(state=="queued"||state=="paused")next="cancelled";else if(state=="running"||state=="pausing")next="cancelling";else if(state=="cancelled"||state=="cancelling")next=state;
  }
  require(!next.empty(),"E_JOB_STATE","Action is not valid for this map job state",409);
  if(action=="cancel") {
    Sql cancel(db_,"UPDATE map_resources SET state='cancelled',error_code='E_CANCELLED' WHERE job_id=? AND state='pending'");cancel.bind(1,id);cancel.step();map_partial_locked(id,"E_CANCELLED");
  }
  if(next!=state) {
    require(old.at("lease_epoch").num()<std::numeric_limits<std::int64_t>::max(),"E_STORAGE","Lease generation exhausted",500);
    Sql change(db_,"UPDATE jobs SET state=?,epoch=epoch+1 WHERE id=?");change.bind(1,next);change.bind(2,id);change.step();
  }
  settle_map_job_locked(id);return map_job_locked(owner,id);
}
Json Store::map_results_locked(const std::string& owner,const std::string& id,std::int64_t offset,std::int64_t limit) {
  auto job=map_job_locked(owner,id);require(offset>=0&&offset<=1000&&limit>=1&&limit<=1000,"E_SCHEMA","Invalid map result window");
  Sql rows(db_,"SELECT c.position,c.url,c.parent,c.source,c.depth,c.path_depth FROM map_candidates c WHERE c.job_id=? AND c.position>=? AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.owner=? AND d.url=c.url AND (d.deleted!=0 OR d.delete_generation!=c.deletion_generation)) AND (c.history_document_id='' OR EXISTS(SELECT 1 FROM documents hd WHERE hd.id=c.history_document_id AND hd.owner=(SELECT owner FROM jobs WHERE id=c.job_id) AND hd.deleted=0 AND hd.delete_generation=c.history_generation)) ORDER BY c.position LIMIT ?");
  rows.bind(1,id);rows.bind(2,offset);rows.bind(3,owner);rows.bind(4,limit);auto items=Json::array();auto next=offset;
  while(rows.step()) {
    auto item=Json::object();item.set("type","candidate");item.set("url",rows.text(1));item.set("parent",rows.text(2));item.set("source",rows.text(3));item.set("depth",rows.num(4));item.set("path_depth",rows.num(5));item.set("body_read",false);items.push(item);next=rows.num(0)+1;
  }
  Sql more(db_,"SELECT 1 FROM map_candidates c WHERE c.job_id=? AND c.position>=? AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.owner=? AND d.url=c.url AND (d.deleted!=0 OR d.delete_generation!=c.deletion_generation)) AND (c.history_document_id='' OR EXISTS(SELECT 1 FROM documents hd WHERE hd.id=c.history_document_id AND hd.owner=(SELECT owner FROM jobs WHERE id=c.job_id) AND hd.deleted=0 AND hd.delete_generation=c.history_generation)) LIMIT 1");more.bind(1,id);more.bind(2,next);more.bind(3,owner);const bool has_more=more.step();
  Sql visible(db_,"SELECT COUNT(*) FROM map_candidates c WHERE c.job_id=? AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.owner=? AND d.url=c.url AND (d.deleted!=0 OR d.delete_generation!=c.deletion_generation)) AND (c.history_document_id='' OR EXISTS(SELECT 1 FROM documents hd WHERE hd.id=c.history_document_id AND hd.owner=(SELECT owner FROM jobs WHERE id=c.job_id) AND hd.deleted=0 AND hd.delete_generation=c.history_generation))");visible.bind(1,id);visible.bind(2,owner);visible.step();
  auto out=Json::object();out.set("job_id",id);out.set("kind","web.map");out.set("state",job.at("state"));out.set("partial",job.at("partial"));out.set("stop_reason",job.at("stop_reason"));out.set("offset",offset);out.set("limit",limit);
  out.set("items",items);out.set("total",visible.num(0));out.set("total_items",visible.num(0));out.set("has_more",has_more);out.set("next_offset",has_more?Json::integer(next):Json());return out;
}
std::string Store::active_map_attempt(const std::string& owner,const std::string& id) {
  std::lock_guard lock(mutex_);Sql item(db_,"SELECT h.attempt_id FROM map_hops h JOIN jobs j ON j.id=h.job_id WHERE j.id=? AND j.owner=? AND h.state='running'");item.bind(1,id);item.bind(2,owner);return item.step()?item.text(0):"";
}
}
