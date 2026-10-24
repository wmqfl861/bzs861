#include "bzs/store.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
using namespace bzs;
namespace {
constexpr std::int64_t day=86400000, year=365*day, t0=1800000000000;
int checks=0, scenarios=0;
#define CHECK(x) do{++checks;if(!(x))throw std::runtime_error(#x);}while(false)
void sql(Store& store,const std::string& query) {
 char* error=nullptr;const auto rc=sqlite3_exec(store.raw_handle(),query.c_str(),nullptr,nullptr,&error);
 if(rc!=SQLITE_OK){std::string message=error?error:"sqlite error";sqlite3_free(error);throw std::runtime_error(message);}
}
std::int64_t scalar(Store& store,const std::string& query) {
 sqlite3_stmt* q=nullptr;CHECK(sqlite3_prepare_v2(store.raw_handle(),query.c_str(),-1,&q,nullptr)==SQLITE_OK);
 CHECK(sqlite3_step(q)==SQLITE_ROW);const auto value=sqlite3_column_int64(q,0);sqlite3_finalize(q);return value;
}
Json proof(Store& store) {
 sqlite3_stmt* q=nullptr;CHECK(sqlite3_prepare_v2(store.raw_handle(),"SELECT value FROM settings WHERE key='source_cooldown_proof/shared'",-1,&q,nullptr)==SQLITE_OK);
 CHECK(sqlite3_step(q)==SQLITE_ROW);const auto value=Json::parse(reinterpret_cast<const char*>(sqlite3_column_text(q,0)));sqlite3_finalize(q);return value;
}
std::int64_t deadline(Store& store){return scalar(store,"SELECT cooldown_until FROM source_state WHERE source_id='shared'");}
struct Database {
 std::filesystem::path path=std::filesystem::temp_directory_path()/("independent-proof-"+random_id()+".sqlite");
 ~Database(){for(const auto suffix:{"","-wal","-shm"}){std::error_code ec;std::filesystem::remove(path.string()+suffix,ec);}}
};
}
int main() {
 try {
  // Proof must protect a bounded response even when the old request timestamp
  // and a later success both precede the new wall clock by over a year.
  // This specifically distinguishes proof from the one-year legacy heuristic.
  for(const auto route:{"states","admit","report"})for(bool reopen:{false,true})for(bool late_success:{false,true}) {
   Database db;std::int64_t now=t0;
   auto primary=std::make_unique<Store>(db.path,[&]{return now;});auto actor=std::make_unique<Store>(db.path,[&]{return now;});
   CHECK(primary->source_admit("shared",0,100).empty());now=t0+2*year;
   primary->source_report("shared","parse_error","bounded response after a delayed report",3*day);
   const auto original=deadline(*primary);CHECK(proof(*primary).at("verified_until_ms").num()==original);
   now=t0+60000;if(late_success)primary->source_report("shared","ok","late success after rollback",0);
   if(reopen){actor.reset();primary.reset();actor=std::make_unique<Store>(db.path,[&]{return now;});}
   if(std::string(route)=="states")actor->source_states();
   else if(std::string(route)=="admit")CHECK(actor->source_admit("shared",0,100)=="cooldown");
   else actor->source_report("shared","rate_limited","short late report after rollback",120000);
   CHECK(deadline(*actor)==original);CHECK(proof(*actor).at("verified_until_ms").num()==original);++scenarios;
  }
  // Model unknown pre-proof rows directly. These are boundary fixtures, not
  // evidence of old-version production; the separate native producer is used
  // to establish actual old-version behavior.
  for(const auto wait:{3*day+1,200*day,year,year+1,31*year})for(bool future_event:{false,true}) {
   Database db;std::int64_t now=t0;Store store(db.path,[&]{return now;});CHECK(store.source_admit("shared",0,100).empty());
   const auto original=t0+wait;
   sql(store,"UPDATE source_state SET last_status='rate_limited',cooldown_until="+std::to_string(original)+",last_request_at="+std::to_string(t0+(future_event?1:0))+" WHERE source_id='shared'");
   const auto states=store.source_states();const bool repair=!future_event&&wait>year;
   CHECK(deadline(store)==(repair?t0+day:original));CHECK(states.at("shared").has("cooldown_last_repair")==repair);
   if(repair){const auto audit=states.at("shared").at("cooldown_last_repair");CHECK(audit.at("original_until_ms").num()==original);CHECK(audit.at("anchor_ms").num()==t0);CHECK(audit.at("reason").str()=="unproven_legacy_backoff_over_one_year");}
   ++scenarios;
  }
  {
   Database db;std::int64_t now=t0;Store store(db.path,[&]{return now;});CHECK(store.source_admit("shared",0,100).empty());
   const auto unknown=t0+200*day;
   sql(store,"UPDATE source_state SET last_status='rate_limited',cooldown_until="+std::to_string(unknown)+" WHERE source_id='shared'");
   store.source_report("shared","rate_limited","short new report cannot bless unknown old MAX",120000);
   CHECK(deadline(store)==unknown);CHECK(proof(store).at("verified_until_ms").num()==t0+120000);
   store.source_report("shared","ok","success cannot clear evidence",0);
   CHECK(deadline(store)==unknown);CHECK(proof(store).at("verified_until_ms").num()==t0+120000);++scenarios;
  }
  {
   Database db;std::int64_t now=t0;auto store=std::make_unique<Store>(db.path,[&]{return now;});CHECK(store->source_admit("shared",0,100).empty());
   const auto original=t0+31*year;
   sql(*store,"UPDATE source_state SET last_status='rate_limited',last_error='old',cooldown_until="+std::to_string(original)+" WHERE source_id='shared'");
   auto initial=store->source_states().at("shared");const auto audit=initial.at("cooldown_last_repair").dump();
   const auto fixed=deadline(*store);CHECK(fixed==t0+day);CHECK(initial.at("last_error").str().starts_with("E_SOURCE_COOLDOWN_CLAMPED:"));
   now+=3600000;store->source_report("shared","ok","late success",0);
   CHECK(store->source_states().at("shared").at("cooldown_last_repair").dump()==audit);CHECK(deadline(*store)==fixed);
   store.reset();store=std::make_unique<Store>(db.path,[&]{return now;});
   CHECK(store->source_states().at("shared").at("cooldown_last_repair").dump()==audit);CHECK(deadline(*store)==fixed);
   CHECK(scalar(*store,"PRAGMA user_version")==13);++scenarios;
  }
  std::cout<<"PASS "<<scenarios<<" independent proof/legacy scenarios, "<<checks<<" checks\n";return 0;
 }catch(const std::exception& error){std::cerr<<"FAIL scenario="<<scenarios<<" after "<<checks<<" checks: "<<error.what()<<'\n';return 1;}
}
