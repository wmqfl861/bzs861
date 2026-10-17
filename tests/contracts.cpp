#include "bzs/contracts.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <chrono>
#include <functional>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

using namespace bzs;
namespace {
  constexpr auto fixture_now=std::chrono::duration_cast<std::chrono::milliseconds>(
    (std::chrono::sys_days{std::chrono::year{2026}/std::chrono::month{10}/std::chrono::day{16}}+
     std::chrono::hours{12}).time_since_epoch()).count();
  std::size_t assertions=0;
  using Test=std::pair<std::string,std::function<void()>>;
  std::vector<Test>& tests() { static std::vector<Test> entries;return entries; }
  struct Add { Add(const char* name,std::function<void()> f) { tests().emplace_back(name,std::move(f)); } };
#define TEST(name) void name();Add register_##name(#name,name);void name()
#define CHECK(condition) do { ++assertions;if(!(condition))throw std::runtime_error("Check failed at line "+std::to_string(__LINE__)+": " #condition); } while(false)
  template<class F> void rejects(F action,const std::string& code={}) {
    ++assertions;
    try { action(); }
    catch(const Error& e) {
      if(e.http_status>=500)throw;
      if(!code.empty()&&e.code!=code)throw std::runtime_error("Expected "+code+", got "+e.code);
      return;
    }
    throw std::runtime_error("Expected contract rejection");
  }
  Json clone(const Json& value) { return Json::parse(value.dump()); }
  Json without(const Json& input,const std::string& omitted) {
    auto out=Json::object();
    for(const auto& key:input.keys())if(key!=omitted)out.set(key,clone(input.at(key)));
    return out;
  }
  Json model() {
    return Json::parse(R"({
      "policy":{"allow_reference_backends":false,"external_nonmodel_services":"reviewed_free_only",
                "allow_provider_hosted_search":false,"allow_unconfigured_fallback":false},
      "default_text_profile":"text","profiles":{"text":{"kind":"generation","protocol_adapter":"openai_responses",
        "base_url":"https://models.invalid/v1","model":"fixture-model","credential_ref":null,"capabilities":["text"]}},
      "roles":{}})");
  }
  Json source(bool enabled=false) {
    auto value=Json::parse(R"({"id":"fixture.source","provider_id":"fixture","index_family":"unknown",
      "capability":"web","endpoint":"https://source.invalid/search","adapter_id":"fixture_v1",
      "review":{"status":"candidate","payment_scheme":"unverified","automated_access":"unverified",
        "checked_at":null,"terms_url":null},"limits":{"max_concurrency":1,"min_interval_ms":1000,"shared_budget_key":"fixture"},"enabled":false})");
    if(enabled) {
      value.set("enabled",true);value.set("logical_service_id","fixture-service");
      auto review=value.at("review");
      review.set("status","approved");review.set("payment_scheme","no_payment");review.set("automated_access","approved");
      review.set("checked_at","2026-10-16T10:00:00Z");review.set("expires_at","2026-10-17T12:00:00Z");
      review.set("terms_url","https://source.invalid/terms#automated-access");review.set("approved_by","fixture-reviewer");
    }
    return value;
  }
  Json request() {
    return Json::parse(R"({"query":"C++ 搜索","scope":{"corpus":"local"},"mode":"balanced",
      "constraint_mode":"strict","budget":{"max_source_requests":0,"max_reads":0,"max_model_calls":0,"timeout_ms":1000}})");
  }
  Json validate(std::string_view kind,const Json& input,const ContractLimits& limits={}) {
    return validate_contract(kind,input,fixture_now,limits);
  }
  void bad(std::string_view kind,const Json& input,const std::string& code={}) {
    rejects([&]{(void)validate(kind,input);},code);
  }
  Json date_filter(const char* op,const char* value,const char* field="published_at") {
    auto out=Json::object();out.set("field",field);out.set("operator",op);out.set("value",value);return out;
  }
  Json leaf(const char* text="x") {
    auto out=Json::object();out.set("op","term");out.set("value",text);return out;
  }
  Json conjunction(const Json& children) {
    auto out=Json::object();out.set("op","and");out.set("children",children);return out;
  }

  TEST(normalization_is_pure_and_defaults_are_explicit) {
    auto input=model();auto before=input.canonical();
    auto out=validate("model-config",input);
    CHECK(input.canonical()==before);
    CHECK(out.at("effective_roles").at("planner").at("profile").str()=="text");
    CHECK(out.at("effective_roles").at("writer").at("limits").at("timeout_ms").num()==90000);
    CHECK(out.at("profiles").at("text").at("limits").at("retry_limit").num()==0);
    CHECK(out.at("profiles").at("text").at("reasoning").at("mode").str()=="provider_default");
    CHECK(out.at("text_fallback_profiles").size()==0);
    for(const auto* role:{"embedder","reranker","vision","asr"})CHECK(out.at("effective_roles").at(role).is_null());
    out.at("profiles").at("text").set("model","changed-output");
    CHECK(input.canonical()==before);
  }
  TEST(no_model_and_explicit_disabled_roles) {
    auto input=model();input.set("profiles",Json::object());input.set("default_text_profile",Json{});
    auto out=validate("model-config",input);
    for(const auto& role:out.at("effective_roles").keys())CHECK(out.at("effective_roles").at(role).is_null());
    input=model();input.at("roles").set("writer",Json{});input.at("roles").set("vision",Json::object());
    out=validate("model-config",input);
    CHECK(out.at("effective_roles").at("writer").is_null());
    CHECK(out.at("effective_roles").at("vision").is_null());
    CHECK(!out.at("effective_roles").at("planner").is_null());
  }
  TEST(role_inheritance_does_not_mutate_sibling_limits) {
    auto input=model();input.at("roles").set("writer",Json::parse(R"({"overrides":{"max_concurrency":3,"retry_limit":1}})"));
    auto out=validate("model-config",input);
    CHECK(out.at("effective_roles").at("writer").at("limits").at("max_concurrency").num()==3);
    CHECK(out.at("effective_roles").at("planner").at("limits").at("max_concurrency").num()==1);
    CHECK(out.at("profiles").at("text").at("limits").at("max_concurrency").num()==1);
  }
  TEST(model_structure_and_policy_are_strict) {
    for(const auto* key:{"policy","default_text_profile","profiles","roles"})bad("model-config",without(model(),key));
    for(const auto* key:{"kind","protocol_adapter","base_url","model","credential_ref","capabilities"}) {
      auto input=model();input.at("profiles").set("text",without(input.at("profiles").at("text"),key));bad("model-config",input);
    }
    auto input=model();input.set("deployment_limits",Json::object());bad("model-config",input,"E_SCHEMA");
    input=model();input.at("policy").set("allow_provider_hosted_search",true);bad("model-config",input);
    input=model();input.at("policy").set("allow_reference_backends",static_cast<std::int64_t>(0));bad("model-config",input);
    input=model();input.at("profiles").at("text").set("api_key","must-use-a-reference");bad("model-config",input);
    input=model();input.at("roles").set("unknown-role",Json{});bad("model-config",input);
    input=model();input.at("roles").set("writer",Json::parse(R"({"profile":null})"));bad("model-config",input);
    input=model();input.at("profiles").at("text").set("capabilities",Json::parse(R"(["text","text"])"));bad("model-config",input);
    input=model();input.at("profiles").at("text").set("capabilities",Json::parse(R"(["hosted_search"])"));bad("model-config",input);
  }
  TEST(profile_and_role_limits_use_trusted_ceilings) {
    auto input=model();ContractLimits limits;
    limits.model_timeout_ms=1000;limits.model_output_tokens=8;limits.model_concurrency=1;limits.model_retry_limit=0;
    auto out=validate("model-config",input,limits);
    CHECK(out.at("effective_roles").at("writer").at("limits").at("timeout_ms").num()==1000);
    CHECK(out.at("effective_roles").at("writer").at("limits").at("max_output_tokens").num()==8);
    input.at("roles").set("writer",Json::parse(R"({"overrides":{"max_concurrency":2}})"));
    rejects([&]{(void)validate("model-config",input,limits);},"E_CONTRACT_LIMIT");
    input=model();input.at("profiles").at("text").set("limits",Json::parse(R"({"max_concurrency":2})"));
    input.at("roles").set("writer",Json::parse(R"({"overrides":{"max_concurrency":1}})"));
    rejects([&]{(void)validate("model-config",input,limits);},"E_CONTRACT_LIMIT");
    for(const auto* json:{R"({"timeout_ms":99})",R"({"max_concurrency":0})",R"({"retry_limit":11})",R"({"max_output_tokens":false})"}) {
      input=model();input.at("profiles").at("text").set("limits",Json::parse(json));bad("model-config",input);
    }
  }
  TEST(unused_profiles_still_validate_reasoning_and_references) {
    auto input=model();auto spare=clone(input.at("profiles").at("text"));
    spare.set("reasoning",Json::parse(R"({"mode":"explicit","provider_parameters":{}})"));
    input.at("profiles").set("unused",spare);bad("model-config",input,"E_CONTRACT");
    input=model();input.set("default_text_profile","missing");bad("model-config",input);
    input=model();input.set("text_fallback_profiles",Json::parse(R"(["missing"])"));bad("model-config",input);
    input=model();input.set("text_fallback_profiles",Json::parse(R"(["text","text"])"));bad("model-config",input);
    input=model();input.at("roles").set("writer",Json::parse(R"({"profile":"missing"})"));bad("model-config",input);
    input=model();input.at("roles").set("vision",Json::parse(R"({"overrides":{}})"));bad("model-config",input);
  }
  TEST(specialized_roles_require_explicit_suitable_profiles) {
    for(const auto* role:{"embedder","vision","asr"}) {
      auto input=model();input.at("roles").set(role,Json::parse(R"({"profile":"text"})"));bad("model-config",input);
    }
    auto input=model();input.at("roles").set("reranker",Json::parse(R"({"profile":"text"})"));
    CHECK(!validate("model-config",input).at("effective_roles").at("reranker").is_null());
    auto multimedia=clone(input.at("profiles").at("text"));multimedia.set("kind","multimodal");
    multimedia.set("capabilities",Json::parse(R"(["embeddings","image_input","asr"])"));input.at("profiles").set("multi",multimedia);
    for(const auto* role:{"embedder","vision","asr"})input.at("roles").set(role,Json::parse(R"({"profile":"multi"})"));
    auto out=validate("model-config",input);
    for(const auto* role:{"embedder","vision","asr"})CHECK(out.at("effective_roles").at(role).at("profile").str()=="multi");
  }
  TEST(endpoint_structure_is_checked_without_network) {
    for(const auto* url:{"https:","https://","file:///tmp/model","https://user:secret@source.invalid/", "https://source.invalid:0/",
        "https://source.invalid:65536/","https://source.invalid:/","https://source.invalid:-1/","https://source.invalid\\@other.invalid/",
        "https://source.invalid/ a","https://source.invalid/%zz","https://source.invalid/#fragment","https://127.1/",
        "https://01.2.3.4/","https://[:::1]/","https://[v1.example]/","https://[]/","https://source..invalid/","https://-bad.invalid/",
        "https://source.invalid../","https://source.invalid/path[0]","https://source.invalid/?query[]=value"}) {
      auto input=model();input.at("profiles").at("text").set("base_url",url);bad("model-config",input);
      auto manifest=source();manifest.set("endpoint",url);bad("source-manifest",manifest);
    }
    for(const auto* url:{"http://localhost:8080/v1","https://127.0.0.1:443/v1","http://[::1]:8080/v1",
        "https://[2001:db8::1]/v1","https://[::ffff:192.0.2.1]/v1","HTTPS://Source.Invalid./v1?q=%E4%B8%AD%E6%96%87",
        "https://source.invalid/path%5B0%5D?query%5B%5D=value"}) {
      auto input=model();input.at("profiles").at("text").set("base_url",url);
      CHECK(validate("model-config",input).at("profiles").at("text").at("base_url").str()==url);
    }
    for(const auto* url:{"https://source.invalid/#one#two","https://source.invalid/#fragment[0]"}) {
      auto input=source(true);input.at("review").set("terms_url",url);bad("source-manifest",input);
    }
  }
  TEST(source_validation_does_not_invent_review_or_activation) {
    auto input=source();auto before=input.canonical();auto out=validate("source-manifest",input);
    CHECK(out.canonical()==before);
    CHECK(out.at("review").at("status").str()=="candidate");CHECK(!out.at("enabled").flag());
    out=validate("source-manifest",source(true));CHECK(out.at("enabled").flag());
    CHECK(out.at("review").at("approved_by").str()=="fixture-reviewer");
  }
  TEST(source_structural_and_conditional_fields_are_checked) {
    for(const auto* key:{"id","provider_id","index_family","capability","endpoint","adapter_id","review","limits","enabled"})
      bad("source-manifest",without(source(),key));
    for(const auto* key:{"logical_service_id"})bad("source-manifest",without(source(true),key));
    for(const auto* key:{"expires_at","approved_by","terms_url"}) {
      auto input=source(true);input.set("review",without(input.at("review"),key));bad("source-manifest",input);
    }
    for(const auto* id:{"a","UPPER","bad/name","_invalid"}) { auto input=source();input.set("id",id);bad("source-manifest",input); }
    auto input=source();input.at("review").set("extra",true);bad("source-manifest",input);
    input=source();input.set("native_filters",Json::parse(R"(["host","host"])"));bad("source-manifest",input);
    input=source(true);input.at("review").set("payment_scheme","paid_or_trial");bad("source-manifest",input);
    input=source(true);input.at("review").set("automated_access","restricted");bad("source-manifest",input);
  }
  TEST(source_quotas_and_deployment_ceilings_are_enforced) {
    auto input=source();input.at("limits").set("request_quota",static_cast<std::int64_t>(0));bad("source-manifest",input);
    input.at("limits").set("quota_window_seconds",static_cast<std::int64_t>(60));CHECK(!validate("source-manifest",input).at("enabled").flag());
    input.at("limits").set("quota_window_seconds",static_cast<std::int64_t>(0));bad("source-manifest",input);
    input=source();input.at("limits").set("max_concurrency",static_cast<std::int64_t>(2));ContractLimits limits;limits.source_concurrency=1;
    rejects([&]{(void)validate("source-manifest",input,limits);},"E_CONTRACT_LIMIT");
    input=source();input.at("limits").set("min_interval_ms",static_cast<std::int64_t>(-1));bad("source-manifest",input);
  }
  TEST(review_times_use_the_trusted_clock_and_fractional_precision) {
    auto input=source(true);input.at("review").set("checked_at","2026-10-16T20:00:00+08:00");
    CHECK(validate("source-manifest",input).at("enabled").flag());
    input.at("review").set("checked_at","2026-10-16T20:00:00.0001+08:00");bad("source-manifest",input);
    input=source(true);input.at("review").set("expires_at","2026-10-16T12:00:00Z");bad("source-manifest",input);
    input.at("review").set("expires_at","2026-10-16T12:00:00.0001Z");CHECK(validate("source-manifest",input).at("enabled").flag());
    input=source(true);input.at("review").set("checked_at","2024-02-29T10:00:00Z");CHECK(validate("source-manifest",input).at("enabled").flag());
    for(const auto* value:{"2026-02-29T10:00:00Z","2026-10-16","2026-10-16 10:00:00Z","2026-10-16T24:00:00Z",
        "2026-10-16T10:00:60Z","2026-10-16T10:00:00","2026-10-16T10:00:00+24:00","2026-10-16T10:00:00.Z"}) {
      input=source(true);input.at("review").set("checked_at",value);bad("source-manifest",input);
    }
    input=source(true);input.at("review").set("expires_at","2026-10-16T09:00:00Z");input.set("enabled",false);bad("source-manifest",input);
  }
  TEST(search_defaults_preserve_query_and_filters) {
    auto input=request();input.set("hard_filters",Json::parse(R"([{"field":"identifier","operator":"eq","value":"std::expected","match":"literal"}])"));
    auto before=input.canonical();auto out=validate("search-request",input);
    CHECK(!out.at("allow_unverified").flag());CHECK(out.at("query").str()==input.at("query").str());
    CHECK(out.at("hard_filters").canonical()==input.at("hard_filters").canonical());CHECK(input.canonical()==before);
    auto node=leaf();node.set("field","author");input.set("query",node);CHECK(validate("search-request",input).at("query").at("field").str()=="author");
  }
  TEST(search_schema_rejects_unknown_fields_wrong_types_and_enums) {
    for(const auto* key:{"query","scope","mode","constraint_mode","budget"})bad("search-request",without(request(),key));
    auto input=request();input.set("user_limits",Json::object());bad("search-request",input);
    input=request();input.at("scope").set("source_ids",Json::parse(R"(["same","same"])"));bad("search-request",input);
    input=request();input.set("privacy",Json::parse(R"({"save_history":1})"));bad("search-request",input);
    input=request();input.set("allow_unverified",static_cast<std::int64_t>(0));bad("search-request",input);
    input=request();input.set("mode","unbounded");bad("search-request",input);
    input=request();input.at("budget").set("max_model_calls",Json::parse("1.5"));bad("search-request",input);
    input=request();input.set("protected_literals",Json::parse(R"([""])"));bad("search-request",input);
    input=request();input.set("model_profile_ref","");bad("search-request",input);
  }
  TEST(ast_structure_and_complexity_have_independent_limits) {
    auto input=request();auto node=leaf();
    for(int i=0;i<15;++i) { auto parent=Json::object();parent.set("op","not");parent.set("child",node);node=parent; }
    input.set("query",node);CHECK(validate("search-request",input).at("query").is_object());
    auto parent=Json::object();parent.set("op","not");parent.set("child",node);input.set("query",parent);bad("search-request",input,"E_CONTRACT_LIMIT");
    auto top=Json::array();
    for(int i=0;i<8;++i) {
      auto children=Json::array();for(int j=0;j<(i==0?62:63);++j)children.push(leaf());top.push(conjunction(children));
    }
    input.set("query",conjunction(top));CHECK(validate("search-request",input).at("query").is_object()); // 512 nodes.
    top.at(static_cast<std::size_t>(0)).at("children").push(leaf());bad("search-request",input,"E_CONTRACT_LIMIT");
    for(const auto* json:{R"({"op":"and","children":[{"op":"term","value":"x"}]})",R"({"op":"not","children":[]})",
        R"({"op":"term","value":"x","child":{}})",R"({"op":"term","value":""})",R"({"op":"term","value":"x","field":"missing"})"}) {
      input=request();input.set("query",Json::parse(json));bad("search-request",input);
    }
  }
  TEST(unicode_schema_lengths_are_characters_not_bytes) {
    auto input=request();std::string text;for(int i=0;i<8192;++i)text+="中";input.set("query",text);
    CHECK(validate("search-request",input).at("query").str()==text);
    input.set("query",text+"中");bad("search-request",input,"E_SCHEMA");
    input=request();input.set("query",std::string(70000,'x'));bad("search-request",input,"E_CONTRACT_LIMIT");
    ContractLimits limits;limits.max_request_bytes=20;rejects([&]{(void)validate("search-request",request(),limits);},"E_CONTRACT_LIMIT");
  }
  TEST(repeated_date_bounds_are_conjunctive_in_every_order) {
    const std::vector<Json> contradictory={date_filter("after","2026-11-01"),date_filter("after","2026-01-01"),date_filter("before","2026-10-01")};
    std::vector<unsigned> order{0,1,2};
    do {
      auto input=request();auto values=Json::array();for(auto i:order)values.push(contradictory[i]);input.set("hard_filters",values);bad("search-request",input,"E_CONTRACT");
    } while(std::next_permutation(order.begin(),order.end()));
    auto input=request();auto values=Json::array();values.push(date_filter("after","2026-01-01"));values.push(date_filter("after","2026-02-01"));
    values.push(date_filter("before","2026-11-01"));values.push(date_filter("before","2026-10-01"));values.push(date_filter("eq","2026-07-01"));
    input.set("hard_filters",values);CHECK(validate("search-request",input).at("hard_filters").size()==5);
  }
  TEST(date_equalities_respect_timezone_fraction_and_open_bounds) {
    auto input=request();auto values=Json::array();values.push(date_filter("eq","2026-10-16T12:00:00Z"));values.push(date_filter("eq","2026-10-16T20:00:00+08:00"));
    input.set("hard_filters",values);CHECK(validate("search-request",input).at("hard_filters").size()==2);
    values.push(date_filter("eq","2026-10-16T12:00:00.0001Z"));bad("search-request",input);
    for(const auto* op:{"before","after"}) {
      input=request();values=Json::array();values.push(date_filter("eq","2026-10-16"));values.push(date_filter(op,"2026-10-16"));input.set("hard_filters",values);bad("search-request",input);
    }
    input=request();values=Json::array();values.push(date_filter("after","2026-11-01"));values.push(date_filter("before","2026-01-01","updated_at"));
    input.set("hard_filters",values);CHECK(validate("search-request",input).at("hard_filters").size()==2);
  }
  TEST(filter_field_operator_value_and_match_must_agree) {
    for(const auto* json:{R"([{"field":"host","operator":"after","value":"2026-10-01"}])",
        R"([{"field":"identifier","operator":"contains","value":"id"}])",R"([{"field":"title","operator":"eq","value":"x","match":"exact_host"}])",
        R"([{"field":"host","operator":"eq","value":["example.invalid"]}])",R"([{"field":"host","operator":"in","value":[]}])",
        R"([{"field":"host","operator":"in","value":["example.invalid","example.invalid"]}])",R"([{"field":"host","operator":"eq","value":"https://example.invalid"}])",
        R"([{"field":"published_at","operator":"eq","value":"2026-02-29"}])"}) {
      auto input=request();input.set("hard_filters",Json::parse(json));bad("search-request",input);
    }
    auto input=request();auto values=Json::array();for(int i=0;i<64;++i)values.push(date_filter("eq","2026-10-16"));input.set("hard_filters",values);
    CHECK(validate("search-request",input).at("hard_filters").size()==64);values.push(date_filter("eq","2026-10-16"));bad("search-request",input);
  }
  TEST(search_budget_and_offline_conflicts_cannot_expand_scope) {
    auto input=request();input.at("scope").set("corpus","both");input.set("network_mode","offline");bad("search-request",input);
    input.at("scope").set("corpus","local");CHECK(validate("search-request",input).at("network_mode").str()=="offline");
    input=request();input.at("budget").set("max_reads",static_cast<std::int64_t>(2));ContractLimits limits;limits.search_reads=1;
    rejects([&]{(void)validate("search-request",input,limits);},"E_CONTRACT_LIMIT");
    input=request();input.at("budget").set("max_bytes",static_cast<std::int64_t>(1073741825));bad("search-request",input,"E_CONTRACT_LIMIT");
    input=request();input.at("budget").set("max_source_requests",static_cast<std::int64_t>(1001));bad("search-request",input,"E_SCHEMA");
    input=request();input.at("budget").set("max_model_calls",static_cast<std::int64_t>(-1));bad("search-request",input,"E_SCHEMA");
  }
  TEST(unknown_kind_and_non_object_inputs_are_rejected) {
    bad("unknown",request(),"E_SCHEMA");bad("search-request",Json::array(),"E_SCHEMA");bad("source-manifest",Json{},"E_SCHEMA");
  }
}

int main(int argc,char** argv) {
  if(argc!=2) { std::cerr<<"Usage: bzs_contract_tests CONTRACT_CASES_JSON\n";return 2; }
  std::size_t passed=0,failed=0,fixture_count=0;
  try {
    auto cases=Json::parse(read_file(argv[1],1024*1024));
    require(cases.is_array(),"E_SCHEMA","Contract fixtures must be an array");
    fixture_count=cases.size();
    for(std::size_t i=0;i<cases.size();++i) {
      auto test=cases.at(i);const auto name=test.at("name").str();
      try {
        bool valid=true;Json result;
        try { result=validate(test.at("schema").str(),test.at("input")); }
        catch(const Error& e) { if(e.http_status>=500)throw;valid=false; }
        CHECK(valid==test.at("valid").flag());
        if(valid&&test.has("effective_assertion")) {
          auto wanted=test.at("effective_assertion");auto role=result.at("effective_roles").at(wanted.at("role").str());
          auto value=role.is_null()?Json{}:role.at("limits").at(wanted.at("field").str());
          CHECK(value.canonical()==wanted.at("value").canonical());
        }
        ++passed;std::cout<<"PASS fixture "<<name<<'\n';
      } catch(const std::exception& e) { ++failed;std::cerr<<"FAIL fixture "<<name<<": "<<e.what()<<'\n'; }
    }
  } catch(const std::exception& e) { std::cerr<<"Cannot run contract fixtures: "<<e.what()<<'\n';return 2; }
  for(const auto& [name,run]:tests()) {
    try { run();++passed;std::cout<<"PASS "<<name<<'\n'; }
    catch(const std::exception& e) { ++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n'; }
  }
  std::cout<<"Contract tests: "<<passed<<" passed, "<<failed<<" failed; "<<fixture_count
           <<" original design fixtures, "<<tests().size()<<" additional tests, "<<assertions<<" assertions\n";
  return failed==0?0:1;
}
