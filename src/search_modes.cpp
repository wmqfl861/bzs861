#include "bzs/search_modes.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <array>

namespace bzs {
namespace {
struct Preset { const char* name;std::int64_t sources,limit,per_source,timeout,deadline; };
constexpr std::array<Preset,3> presets={{{"fast",2,10,5,4000,20000},{"balanced",4,20,10,8000,60000},{"deep",8,50,20,15000,180000}}};
}
Json SearchMode::budget(std::int64_t selected_sources)const {
  auto value=Json::object();value.set("version","bzs-search-mode-1");value.set("scope","search_only");
  value.set("max_sources",max_sources);if(selected_sources>=0)value.set("selected_sources",selected_sources);value.set("max_source_attempts",selected_sources>=0?selected_sources:max_sources);
  value.set("limit",limit);value.set("per_source",per_source);value.set("timeout_ms",timeout_ms);value.set("deadline_ms",deadline_ms);
  value.set("max_reads",std::int64_t{0});value.set("max_model_calls",std::int64_t{0});value.set("max_browser_calls",std::int64_t{0});return value;
}
SearchMode search_mode(const Json& request,bool persistent) {
  SearchMode mode;mode.limit=persistent?20:10;
  std::int64_t max_sources=20,max_limit=50,max_per_source=30,max_timeout=30000,max_deadline=600000;
  if(request.has("mode")) {
    mode.name=required_string(request,"mode",16);mode.explicit_mode=true;bool found=false;
    for(const auto& preset:presets)if(mode.name==preset.name) {
      found=true;mode.max_sources=max_sources=preset.sources;mode.limit=max_limit=preset.limit;mode.per_source=max_per_source=preset.per_source;
      mode.timeout_ms=max_timeout=preset.timeout;mode.deadline_ms=max_deadline=preset.deadline;
    }
    require(found,"E_SCHEMA","mode must be fast, balanced or deep");
  }
  mode.explicit_source_limit=request.has("max_sources");
  mode.max_sources=int_option(request,"max_sources",mode.max_sources,1,max_sources);
  mode.limit=int_option(request,"limit",mode.limit,1,max_limit);mode.per_source=int_option(request,"per_source",mode.per_source,1,max_per_source);
  mode.timeout_ms=int_option(request,"timeout_ms",mode.timeout_ms,1000,max_timeout);mode.deadline_ms=int_option(request,"deadline_ms",mode.deadline_ms,1000,max_deadline);
  return mode;
}
Json search_modes() {
  auto out=Json::object(),modes=Json::array();out.set("version","bzs-search-mode-1");out.set("scope","search_only");out.set("default_mode","legacy");
  out.set("overrides","tighten_only");out.set("quality_guarantee",false);out.set("automatic_reading",false);
  for(const auto& preset:presets) { auto request=Json::object();request.set("mode",preset.name);const auto policy=search_mode(request);auto value=Json::object();value.set("mode",preset.name);value.set("budget",policy.budget(-1));modes.push(value); }
  out.set("modes",modes);auto legacy=Json::object();legacy.set("synchronous",search_mode(Json::object(),false).budget(-1));legacy.set("persistent",search_mode(Json::object(),true).budget(-1));out.set("legacy_defaults",legacy);
  out.set("note","Presets bound source search only; source approval, shared quotas, publication restrictions and cancellation still apply. Deep does not read pages or run models.");return out;
}
}
