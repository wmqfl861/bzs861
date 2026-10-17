#include "bzs/contracts.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <algorithm>
#include <chrono>
#include <initializer_list>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace bzs {
  namespace {
    constexpr auto unlimited=std::numeric_limits<std::int64_t>::max();
    using Words=std::initializer_list<std::string_view>;

    bool one_of(std::string_view value, Words values) {
      return std::find(values.begin(), values.end(), value)!=values.end();
    }
    void object(const Json& value, Words allowed, Words required={}) {
      require(value.is_object(), "E_SCHEMA", "Contract object required");
      value.allowed(allowed);
      for(const auto key:required)require(value.has(std::string(key)), "E_SCHEMA", "Missing contract field: "+std::string(key));
    }
    std::string string(const Json& value, std::size_t low=0, std::size_t high=65536) {
      const auto s=value.str();
      require(valid_utf8(s), "E_SCHEMA", "Contract string must be UTF-8");
      require(s.find('\0')==std::string::npos, "E_CONTRACT", "NUL is not allowed in configuration strings");
      std::size_t characters=0;
      for(const unsigned char c:s)if((c&0xc0U)!=0x80U)++characters;
      require(characters>=low&&characters<=high, "E_SCHEMA", "Contract string length out of range");
      return s;
    }
    std::string choice(const Json& value, Words values) {
      auto s=string(value);
      require(one_of(s, values), "E_SCHEMA", "Unknown contract enum value");
      return s;
    }
    std::int64_t integer(const Json& value, std::int64_t low, std::int64_t high=unlimited) {
      auto n=value.num();
      require(n>=low&&n<=high, "E_SCHEMA", "Contract integer out of range");
      return n;
    }
    void ceiling(std::int64_t value, std::int64_t high) {
      require(value<=high, "E_CONTRACT_LIMIT", "Value exceeds a trusted deployment ceiling");
    }
    void nullable_string(const Json& value, std::size_t low=0, std::size_t high=65536) {
      if(!value.is_null())(void)string(value, low, high);
    }
    void strings(const Json& value, std::size_t low=0, std::size_t high=65536,
                 std::size_t item_low=0, Words values={}) {
      require(value.is_array()&&value.size()>=low&&value.size()<=high, "E_SCHEMA", "Contract array length out of range");
      std::set<std::string> seen;
      for(std::size_t i=0;i<value.size();++i) {
        auto s=string(value.at(i), item_low);
        require(values.size()==0||one_of(s, values), "E_SCHEMA", "Unknown contract array value");
        require(seen.insert(s).second, "E_SCHEMA", "Contract array values must be unique");
      }
    }
    void guard_shape(const Json& input, const ContractLimits& limits) {
      require(input.is_object(), "E_SCHEMA", "Contract input must be an object");
      std::vector<std::pair<Json,std::size_t>> pending{{input,1}};
      std::size_t visited=0;
      while(!pending.empty()) {
        auto [node,depth]=std::move(pending.back());
        pending.pop_back();
        require(++visited<=65536&&depth<=64, "E_CONTRACT_LIMIT", "Contract JSON complexity exceeded");
        if(node.is_object())for(const auto& key:node.keys())pending.emplace_back(node.at(key), depth+1);
        else if(node.is_array())for(std::size_t i=0;i<node.size();++i)pending.emplace_back(node.at(i), depth+1);
      }
      require(input.dump().size()<=std::min<std::size_t>(limits.max_request_bytes,65536),
              "E_CONTRACT_LIMIT", "Contract input exceeds its byte budget");
    }
    void trusted_limits(const ContractLimits& l) {
      require(l.max_request_bytes>0&&l.max_ast_depth>0&&l.max_ast_nodes>0&&
              l.model_timeout_ms>=100&&l.model_concurrency>=1&&l.model_output_tokens>=1&&
              l.model_retry_limit>=0&&l.source_concurrency>=1&&l.search_source_requests>=0&&
              l.search_reads>=0&&l.search_browser_seconds>=0&&l.search_model_calls>=0&&
              l.search_timeout_ms>=100&&l.search_bytes>=0&&l.search_tokens_estimated>=0,
              "E_INTERNAL", "Invalid trusted contract limits", 500);
    }

    bool digit(char c) { return c>='0'&&c<='9'; }
    bool hex(char c) { return digit(c)||(c>='a'&&c<='f')||(c>='A'&&c<='F'); }
    int decimal(std::string_view s) {
      require(!s.empty()&&s.size()<=5, "E_CONTRACT", "Malformed numeric URL/date component");
      int result=0;
      for(char c:s) {
        require(digit(c), "E_CONTRACT", "Malformed numeric URL/date component");
        result=result*10+(c-'0');
      }
      return result;
    }
    bool ipv4(std::string_view s) {
      unsigned groups=0;
      std::size_t offset=0;
      while(offset<s.size()) {
        auto end=s.find('.',offset);
        if(end==std::string_view::npos)end=s.size();
        auto part=s.substr(offset,end-offset);
        if(part.empty()||part.size()>3||(part.size()>1&&part.front()=='0'))return false;
        int n=0;
        for(char c:part) { if(!digit(c))return false; n=n*10+(c-'0'); }
        if(n>255)return false;
        ++groups;
        if(end==s.size())return groups==4;
        offset=end+1;
      }
      return false;
    }
    bool ipv6(std::string_view s) {
      const auto compression=s.find("::");
      if(compression!=std::string_view::npos&&s.find("::",compression+2)!=std::string_view::npos)return false;
      auto count=[](std::string_view part, bool ipv4_tail)->std::optional<unsigned> {
        if(part.empty())return 0;
        unsigned result=0;
        std::size_t offset=0;
        while(offset<part.size()) {
          auto end=part.find(':',offset);
          if(end==std::string_view::npos)end=part.size();
          auto group=part.substr(offset,end-offset);
          if(group.find('.')!=std::string_view::npos) {
            if(!ipv4_tail||end!=part.size()||!ipv4(group))return std::nullopt;
            result+=2;
          } else {
            if(group.empty()||group.size()>4||!std::all_of(group.begin(),group.end(),hex))return std::nullopt;
            ++result;
          }
          if(end==part.size())return result;
          offset=end+1;
        }
        return std::nullopt;
      };
      if(compression==std::string_view::npos) {
        auto n=count(s,true);
        return n&&*n==8;
      }
      auto left=count(s.substr(0,compression),false);
      auto right=count(s.substr(compression+2),true);
      return left&&right&&*left+*right<8;
    }
    void host(std::string_view s) {
      require(!s.empty()&&s.size()<=254, "E_CONTRACT", "Invalid endpoint/filter host");
      if(s.front()=='[') {
        require(s.back()==']'&&ipv6(s.substr(1,s.size()-2)), "E_CONTRACT", "Invalid bracketed IPv6 host");
        return;
      }
      if(s.back()=='.')s.remove_suffix(1);
      require(!s.empty()&&s.back()!='.'&&s.size()<=253, "E_CONTRACT", "Invalid endpoint/filter host");
      const bool numeric=std::all_of(s.begin(),s.end(),[](char c){return digit(c)||c=='.';});
      if(numeric) {
        require(ipv4(s), "E_CONTRACT", "Ambiguous or invalid IPv4 host");
        return;
      }
      std::size_t start=0;
      while(start<s.size()) {
        auto end=s.find('.',start);
        if(end==std::string_view::npos)end=s.size();
        auto label=s.substr(start,end-start);
        require(!label.empty()&&label.size()<=63&&label.front()!='-'&&label.back()!='-',
                "E_CONTRACT", "Invalid DNS host label");
        for(char c:label)require(digit(c)||(c>='a'&&c<='z')||(c>='A'&&c<='Z')||c=='-',
                                  "E_CONTRACT", "Invalid DNS host character");
        start=end+1;
      }
    }
    void http_url(const Json& value, bool allow_fragment=false) {
      auto s=string(value,1);
      for(std::size_t i=0;i<s.size();++i) {
        const auto c=static_cast<unsigned char>(s[i]);
        require(c>0x20U&&c<0x7fU&&s[i]!='\\'&&s[i]!='"'&&s[i]!='<'&&s[i]!='>'&&
                s[i]!='{'&&s[i]!='}'&&s[i]!='|'&&s[i]!='^'&&s[i]!='`',
                "E_CONTRACT", "Endpoint must be an unambiguous ASCII HTTP(S) URI");
        if(s[i]=='%') {
          require(i+2<s.size()&&hex(s[i+1])&&hex(s[i+2]), "E_CONTRACT", "Invalid URI percent escape");
          i+=2;
        }
      }
      auto scheme_end=s.find("://");
      require(scheme_end!=std::string::npos&&one_of(ascii_lower(s.substr(0,scheme_end)),{"http","https"}),
              "E_CONTRACT", "Only absolute HTTP(S) endpoints are supported");
      auto authority_start=scheme_end+3;
      auto authority_end=s.find_first_of("/?#",authority_start);
      if(authority_end==std::string::npos)authority_end=s.size();
      auto authority=std::string_view(s).substr(authority_start,authority_end-authority_start);
      require(!authority.empty()&&authority.find('@')==std::string_view::npos,
              "E_CONTRACT", "Endpoint host is required and userinfo is forbidden");
      require(allow_fragment||s.find('#')==std::string::npos, "E_CONTRACT", "Execution endpoints cannot contain fragments");
      std::string_view hostname=authority,port;
      bool has_port=false;
      if(authority.front()=='[') {
        auto end=authority.find(']');
        require(end!=std::string_view::npos, "E_CONTRACT", "Unclosed IPv6 host");
        hostname=authority.substr(0,end+1);
        if(end+1<authority.size()) {
          require(authority[end+1]==':', "E_CONTRACT", "Invalid IPv6 authority suffix");
          port=authority.substr(end+2);has_port=true;
        }
      } else if(auto colon=authority.find(':');colon!=std::string_view::npos) {
        require(authority.find(':',colon+1)==std::string_view::npos, "E_CONTRACT", "IPv6 hosts require brackets");
        hostname=authority.substr(0,colon);port=authority.substr(colon+1);has_port=true;
      }
      host(hostname);
      if(has_port)require(decimal(port)>=1&&decimal(port)<=65535, "E_CONTRACT", "Endpoint port out of range");
      // RFC URI path/query/fragment characters differ from the authority:
      // brackets are reserved for IP literals and must be escaped elsewhere.
      enum class Component { path,query,fragment } component=Component::path;
      for(std::size_t i=authority_end;i<s.size();++i) {
        const char c=s[i];
        if(c=='%') { i+=2;continue; } // Escape syntax was checked above.
        if(c=='#') {
          require(allow_fragment&&component!=Component::fragment, "E_CONTRACT", "Invalid URI fragment delimiter");
          component=Component::fragment;continue;
        }
        if(c=='?'&&component==Component::path) { component=Component::query;continue; }
        const bool unreserved=digit(c)||(c>='a'&&c<='z')||(c>='A'&&c<='Z')||
          std::string_view("-._~").find(c)!=std::string_view::npos;
        const bool sub_delim=std::string_view("!$&'()*+,;=").find(c)!=std::string_view::npos;
        require(unreserved||sub_delim||c==':'||c=='@'||c=='/'||(c=='?'&&component!=Component::path),
                "E_CONTRACT", "URI path/query/fragment contains an unescaped reserved character");
      }
    }

    // Keep arbitrary fractional precision: truncating to milliseconds can turn
    // two conflicting eq filters, or an expired review, into apparent equality.
    struct Timestamp { std::int64_t seconds=0; std::string fraction; };
    int compare(const Timestamp& a,const Timestamp& b) {
      if(a.seconds!=b.seconds)return a.seconds<b.seconds?-1:1;
      for(std::size_t i=0;i<std::max(a.fraction.size(),b.fraction.size());++i) {
        auto x=i<a.fraction.size()?a.fraction[i]:'0';
        auto y=i<b.fraction.size()?b.fraction[i]:'0';
        if(x!=y)return x<y?-1:1;
      }
      return 0;
    }
    Timestamp timestamp(const Json& value, bool allow_date=false) {
      auto s=string(value,10);
      require(s.size()>=10&&s[4]=='-'&&s[7]=='-', "E_CONTRACT", "Invalid ISO date");
      const int y=decimal(std::string_view(s).substr(0,4));
      const int m=decimal(std::string_view(s).substr(5,2));
      const int d=decimal(std::string_view(s).substr(8,2));
      auto day=std::chrono::year{y}/std::chrono::month{static_cast<unsigned>(m)}/std::chrono::day{static_cast<unsigned>(d)};
      require(y>=1&&day.ok(), "E_CONTRACT", "Invalid calendar date");
      Timestamp result;
      // chrono::days::rep is 32-bit on MSVC; widen before converting days to seconds.
      result.seconds=static_cast<std::int64_t>(std::chrono::sys_days{day}.time_since_epoch().count())*86400;
      if(s.size()==10) {
        require(allow_date, "E_CONTRACT", "Timestamp needs time and timezone");
        return result;
      }
      require(s.size()>=20&&(s[10]=='T'||s[10]=='t')&&s[13]==':'&&s[16]==':',
              "E_CONTRACT", "Timestamp must be RFC3339 with a timezone");
      const int hour=decimal(std::string_view(s).substr(11,2));
      const int minute=decimal(std::string_view(s).substr(14,2));
      const int second=decimal(std::string_view(s).substr(17,2));
      require(hour<24&&minute<60&&second<60, "E_CONTRACT", "Invalid timestamp time");
      result.seconds+=hour*3600+minute*60+second;
      std::size_t pos=19;
      if(s[pos]=='.') {
        auto start=++pos;
        while(pos<s.size()&&digit(s[pos]))++pos;
        require(pos>start, "E_CONTRACT", "Empty timestamp fraction");
        result.fraction=s.substr(start,pos-start);
        while(!result.fraction.empty()&&result.fraction.back()=='0')result.fraction.pop_back();
      }
      require(pos<s.size(), "E_CONTRACT", "Timestamp needs a timezone");
      if(s[pos]=='Z'||s[pos]=='z') {
        require(pos+1==s.size(), "E_CONTRACT", "Trailing timestamp content");
      } else {
        require((s[pos]=='+'||s[pos]=='-')&&pos+6==s.size()&&s[pos+3]==':',
                "E_CONTRACT", "Invalid timestamp timezone");
        const int hours=decimal(std::string_view(s).substr(pos+1,2));
        const int minutes=decimal(std::string_view(s).substr(pos+4,2));
        require(hours<24&&minutes<60, "E_CONTRACT", "Invalid timezone offset");
        result.seconds+=(s[pos]=='+'?-1:1)*(hours*3600+minutes*60);
      }
      return result;
    }
    Timestamp from_ms(std::int64_t ms) {
      auto seconds=ms/1000;
      auto remainder=ms%1000;
      if(remainder<0) { --seconds;remainder+=1000; }
      return {seconds,std::to_string(1000+remainder).substr(1)};
    }

    Json reasoning(const Json& value) {
      object(value,{"mode","provider_parameters"},{"mode"});
      auto mode=choice(value.at("mode"),{"provider_default","explicit"});
      if(value.has("provider_parameters"))require(value.at("provider_parameters").is_object(), "E_SCHEMA", "Reasoning parameters must be an object");
      require(mode!="explicit"||(value.has("provider_parameters")&&value.at("provider_parameters").size()>0),
              "E_CONTRACT", "Explicit reasoning requires provider parameters");
      return value;
    }
    Json default_reasoning() {
      auto result=Json::object();result.set("mode","provider_default");return result;
    }
    Json model_limits(const Json& value,const ContractLimits& l,bool override_values) {
      object(value,override_values?Words{"reasoning","timeout_ms","max_concurrency","max_output_tokens","retry_limit"}:
                                     Words{"timeout_ms","max_concurrency","max_output_tokens","retry_limit"});
      const std::pair<std::string,std::pair<std::int64_t,std::int64_t>> fields[]={
        {"timeout_ms",{100,l.model_timeout_ms}},{"max_concurrency",{1,l.model_concurrency}},
        {"max_output_tokens",{1,l.model_output_tokens}},{"retry_limit",{0,l.model_retry_limit}}
      };
      for(const auto& [key,bounds]:fields)if(value.has(key)) {
        const auto schema_high=key=="retry_limit"?10:(override_values?(key=="timeout_ms"?86400000:(key=="max_concurrency"?1024:10000000)):unlimited);
        ceiling(integer(value.at(key),bounds.first,schema_high),bounds.second);
      }
      if(override_values&&value.has("reasoning"))(void)reasoning(value.at("reasoning"));
      return value;
    }
    Json base_model_limits(const ContractLimits& l) {
      auto out=Json::object();
      out.set("timeout_ms",std::min<std::int64_t>(90000,l.model_timeout_ms));
      out.set("max_concurrency",static_cast<std::int64_t>(1));
      out.set("max_output_tokens",std::min<std::int64_t>(4096,l.model_output_tokens));
      out.set("retry_limit",static_cast<std::int64_t>(0));
      return out;
    }
    bool has_capability(const Json& profile,Words needed) {
      auto capabilities=profile.at("capabilities");
      for(std::size_t i=0;i<capabilities.size();++i)if(one_of(capabilities.at(i).str(),needed))return true;
      return false;
    }
    Json models(Json out,const ContractLimits& limits) {
      object(out,{"policy","default_text_profile","profiles","roles","text_fallback_profiles"},
                 {"policy","default_text_profile","profiles","roles"});
      auto policy=out.at("policy");
      object(policy,{"allow_reference_backends","external_nonmodel_services","allow_provider_hosted_search","allow_unconfigured_fallback"},
                    {"allow_reference_backends","external_nonmodel_services","allow_provider_hosted_search","allow_unconfigured_fallback"});
      for(const auto* key:{"allow_reference_backends","allow_provider_hosted_search","allow_unconfigured_fallback"})
        require(!policy.at(key).flag(), "E_SCHEMA", "Disallowed service/fallback policy");
      (void)choice(policy.at("external_nonmodel_services"),{"reviewed_free_only"});
      nullable_string(out.at("default_text_profile"),1);
      auto profiles=out.at("profiles");
      require(profiles.is_object(), "E_SCHEMA", "Profiles must be an object");
      for(const auto& id:profiles.keys()) {
        auto p=profiles.at(id);
        object(p,{"kind","protocol_adapter","base_url","model","credential_ref","reasoning","capabilities","limits"},
                 {"kind","protocol_adapter","base_url","model","credential_ref","capabilities"});
        (void)choice(p.at("kind"),{"generation","embedding","reranking","vision","asr","multimodal"});
        (void)choice(p.at("protocol_adapter"),{"openai_chat","openai_responses","anthropic_messages","gemini_generate_content","custom"});
        http_url(p.at("base_url"));
        (void)string(p.at("model"),1);
        nullable_string(p.at("credential_ref"));
        strings(p.at("capabilities"),0,65536,0,{"text","image_input","audio_input","asr","embeddings","reranking","stream","json_schema","tool_call"});
        auto normalized_limits=base_model_limits(limits);
        if(p.has("limits")) {
          auto configured=model_limits(p.at("limits"),limits,false);
          for(const auto& key:configured.keys())normalized_limits.set(key,configured.at(key));
        }
        p.set("limits",normalized_limits);
        p.set("reasoning",p.has("reasoning")?reasoning(p.at("reasoning")):default_reasoning());
      }
      auto default_id=out.at("default_text_profile");
      if(!default_id.is_null()) {
        require(profiles.has(default_id.str()), "E_CONTRACT", "Default model profile does not exist");
        require(has_capability(profiles.at(default_id.str()),{"text"}), "E_CONTRACT", "Default model profile lacks text capability");
      }
      auto roles=out.at("roles");
      object(roles,{"planner","extractor","verifier","writer","embedder","reranker","vision","asr"});
      auto effective=Json::object();
      for(const auto* name:{"planner","extractor","verifier","writer","embedder","reranker","vision","asr"}) {
        const bool text_role=one_of(name,{"planner","extractor","verifier","writer"});
        auto role=roles.has(name)?roles.at(name):(text_role?Json::object():Json{});
        if(role.is_null()) { effective.set(name,Json{});continue; }
        object(role,{"profile","overrides"});
        if(role.has("profile"))(void)string(role.at("profile"),1);
        auto overrides=role.has("overrides")?model_limits(role.at("overrides"),limits,true):Json::object();
        auto profile_id=role.has("profile")?role.at("profile"):(text_role?default_id:Json{});
        if(profile_id.is_null()) {
          require(role.size()==0, "E_CONTRACT", "Role overrides require a resolvable model profile");
          effective.set(name,Json{});continue;
        }
        require(profiles.has(profile_id.str()), "E_CONTRACT", "Role model profile does not exist");
        auto p=profiles.at(profile_id.str());
        const bool suitable=text_role?has_capability(p,{"text"}):
          (std::string_view(name)=="embedder"?has_capability(p,{"embeddings"}):
           (std::string_view(name)=="reranker"?has_capability(p,{"reranking","text"}):
            (std::string_view(name)=="vision"?has_capability(p,{"image_input"}):has_capability(p,{"asr"}))));
        require(suitable, "E_CONTRACT", "Model profile capability does not match its role");
        auto resolved=Json::object();resolved.set("profile",profile_id);
        auto role_limits=Json::parse(p.at("limits").dump());
        for(const auto& key:overrides.keys())if(key!="reasoning")role_limits.set(key,overrides.at(key));
        resolved.set("limits",role_limits);
        auto resolved_reasoning=overrides.has("reasoning")?overrides.at("reasoning"):p.at("reasoning");
        resolved.set("reasoning",Json::parse(resolved_reasoning.dump()));
        effective.set(name,resolved);
      }
      auto fallbacks=out.has("text_fallback_profiles")?out.at("text_fallback_profiles"):Json::array();
      strings(fallbacks);
      for(std::size_t i=0;i<fallbacks.size();++i) {
        auto id=fallbacks.at(i).str();
        require(profiles.has(id)&&has_capability(profiles.at(id),{"text"}), "E_CONTRACT", "Invalid text fallback profile");
      }
      out.set("text_fallback_profiles",fallbacks);
      out.set("effective_roles",effective);
      return out;
    }

    Json source(Json out,std::int64_t now,const ContractLimits& limits) {
      object(out,{"id","provider_id","index_family","capability","endpoint","adapter_id","credential_ref","review","native_filters","limits","enabled","logical_service_id"},
                 {"id","provider_id","index_family","capability","endpoint","adapter_id","review","limits","enabled"});
      auto id=string(out.at("id"),2,64);
      require((digit(id.front())||(id.front()>='a'&&id.front()<='z'))&&
              std::all_of(id.begin(),id.end(),[](char c){return digit(c)||(c>='a'&&c<='z')||c=='.'||c=='_'||c=='-';}),
              "E_SCHEMA", "Source id does not match its schema pattern");
      for(const auto* key:{"provider_id","index_family","adapter_id"})(void)string(out.at(key),1);
      (void)choice(out.at("capability"),{"web","news","code","scholar","image","reverse_image","video","audio","catalogue"});
      http_url(out.at("endpoint"));
      if(out.has("credential_ref"))nullable_string(out.at("credential_ref"));
      if(out.has("native_filters"))strings(out.at("native_filters"));
      if(out.has("logical_service_id"))(void)string(out.at("logical_service_id"),1,128);
      const bool enabled=out.at("enabled").flag();
      auto review=out.at("review");
      object(review,{"status","payment_scheme","automated_access","checked_at","terms_url","cache_policy","redistribution_policy","expires_at","approved_by"},
                    {"status","payment_scheme","automated_access","checked_at","terms_url"});
      auto status=choice(review.at("status"),{"candidate","approved","rejected","expired"});
      auto payment=choice(review.at("payment_scheme"),{"unverified","no_payment","paid_or_trial"});
      auto access=choice(review.at("automated_access"),{"unverified","approved","restricted","denied"});
      if(review.has("cache_policy"))(void)choice(review.at("cache_policy"),{"unverified","disallow","allowed_with_limits"});
      if(review.has("redistribution_policy"))(void)choice(review.at("redistribution_policy"),{"unverified","links_only","allowed_with_limits"});
      if(review.has("approved_by"))nullable_string(review.at("approved_by"),1);
      std::optional<Timestamp> checked,expires;
      if(!review.at("checked_at").is_null())checked=timestamp(review.at("checked_at"));
      if(review.has("expires_at")&&!review.at("expires_at").is_null())expires=timestamp(review.at("expires_at"));
      if(!review.at("terms_url").is_null())http_url(review.at("terms_url"),true);
      if(checked&&expires)require(compare(*expires,*checked)>0, "E_CONTRACT", "Review expiry must follow its check time");
      if(enabled) {
        require(status=="approved"&&payment=="no_payment"&&access=="approved", "E_CONTRACT", "Enabled source requires approved free automated access metadata");
        require(out.has("logical_service_id")&&checked&&expires&&!review.at("terms_url").is_null()&&
                review.has("approved_by")&&!review.at("approved_by").is_null(),
                "E_CONTRACT", "Enabled source lacks required review/service metadata");
        const auto current=from_ms(now);
        require(compare(*checked,current)<=0&&compare(*expires,current)>0, "E_CONTRACT", "Source review is from the future or expired");
      }
      auto configured=out.at("limits");
      object(configured,{"max_concurrency","min_interval_ms","shared_budget_key","request_quota","quota_window_seconds"},
                        {"max_concurrency","min_interval_ms","shared_budget_key"});
      ceiling(integer(configured.at("max_concurrency"),1,100),limits.source_concurrency);
      (void)integer(configured.at("min_interval_ms"),0);
      (void)string(configured.at("shared_budget_key"),1);
      if(configured.has("quota_window_seconds")&&!configured.at("quota_window_seconds").is_null())
        (void)integer(configured.at("quota_window_seconds"),1);
      if(configured.has("request_quota")&&!configured.at("request_quota").is_null()) {
        (void)integer(configured.at("request_quota"),0);
        require(configured.has("quota_window_seconds")&&!configured.at("quota_window_seconds").is_null(),
                "E_CONTRACT", "A request quota requires a quota window");
      }
      return out;
    }

    void ast(const Json& input,const ContractLimits& limits) {
      std::vector<std::pair<Json,std::size_t>> pending{{input,1}};
      std::size_t visited=0;
      while(!pending.empty()) {
        auto [node,depth]=std::move(pending.back());pending.pop_back();
        require(++visited<=std::min<std::size_t>(512,limits.max_ast_nodes)&&depth<=std::min<std::size_t>(16,limits.max_ast_depth),
                "E_CONTRACT_LIMIT", "Query AST complexity exceeded");
        require(node.is_object(), "E_SCHEMA", "AST node must be an object");
        auto op=choice(node.at("op"),{"term","phrase","literal","and","or","not"});
        if(one_of(op,{"term","phrase","literal"})) {
          object(node,{"op","value","field"},{"op","value"});
          (void)string(node.at("value"),1,4096);
          if(node.has("field"))(void)choice(node.at("field"),{"any","title","body","code","url","author","identifier"});
        } else if(op=="not") {
          object(node,{"op","child"},{"op","child"});
          pending.emplace_back(node.at("child"),depth+1);
        } else {
          object(node,{"op","children"},{"op","children"});
          auto children=node.at("children");
          require(children.is_array()&&children.size()>=2&&children.size()<=64, "E_SCHEMA", "Boolean AST needs 2..64 children");
          for(std::size_t i=0;i<children.size();++i)pending.emplace_back(children.at(i),depth+1);
        }
      }
    }
    struct DateRange { std::optional<Timestamp> after,before,equal; };
    void filters(const Json& input) {
      require(input.is_array()&&input.size()<=64, "E_SCHEMA", "hard_filters must have at most 64 entries");
      std::map<std::string,DateRange> ranges;
      for(std::size_t i=0;i<input.size();++i) {
        auto f=input.at(i);
        object(f,{"field","operator","value","match"},{"field","operator","value"});
        auto field=choice(f.at("field"),{"host","path","content_type","language","published_at","updated_at","title","body","code","identifier"});
        auto op=choice(f.at("operator"),{"eq","neq","contains","not_contains","before","after","in"});
        if(op=="in")strings(f.at("value"),1,128,1);
        else (void)string(f.at("value"),1);
        if(f.has("match")) {
          auto match=choice(f.at("match"),{"literal","normalized","exact_host","host_and_subdomains"});
          if(one_of(match,{"exact_host","host_and_subdomains"}))require(field=="host"&&one_of(op,{"eq","neq","in"}),
                                                                          "E_CONTRACT", "Host matching requires a compatible host filter");
        }
        const bool date=one_of(field,{"published_at","updated_at"});
        const bool simple=one_of(field,{"host","content_type","language","identifier"});
        require(date?one_of(op,{"before","after","eq"}):(simple?one_of(op,{"eq","neq","in"}):one_of(op,{"eq","neq","contains","not_contains","in"})),
                "E_CONTRACT", "Filter field and operator are incompatible");
        if(field=="host") {
          if(op=="in")for(std::size_t j=0;j<f.at("value").size();++j)host(f.at("value").at(j).str());
          else host(f.at("value").str());
        }
        if(date) {
          auto dt=timestamp(f.at("value"),true);
          auto& range=ranges[field];
          if(op=="after") { if(!range.after||compare(dt,*range.after)>0)range.after=dt; }
          else if(op=="before") { if(!range.before||compare(dt,*range.before)<0)range.before=dt; }
          else {
            require(!range.equal||compare(dt,*range.equal)==0, "E_CONTRACT", "Conflicting date equalities");
            range.equal=dt;
          }
        }
      }
      for(const auto& [field,range]:ranges) {
        (void)field;
        require(!range.after||!range.before||compare(*range.after,*range.before)<0, "E_CONTRACT", "Empty conjunctive date range");
        require(!range.equal||((!range.after||compare(*range.equal,*range.after)>0)&&(!range.before||compare(*range.equal,*range.before)<0)),
                "E_CONTRACT", "Date equality violates a conjunctive bound");
      }
    }
    Json search(Json out,const ContractLimits& limits) {
      object(out,{"query","scope","hard_filters","protected_literals","mode","constraint_mode","allow_unverified","budget","model_profile_ref","network_mode","stream","privacy"},
                 {"query","scope","mode","constraint_mode","budget"});
      if(out.at("query").is_string())(void)string(out.at("query"),1,8192);
      else ast(out.at("query"),limits);
      auto scope=out.at("scope");
      object(scope,{"corpus","source_ids","collection_ids","languages","category"},{"corpus"});
      auto corpus=choice(scope.at("corpus"),{"external","local","both"});
      for(const auto* key:{"source_ids","collection_ids","languages"})if(scope.has(key))strings(scope.at(key));
      if(scope.has("category"))(void)choice(scope.at("category"),{"web","news","code","scholar","image","video","audio","mixed"});
      if(out.has("hard_filters"))filters(out.at("hard_filters"));
      if(out.has("protected_literals"))strings(out.at("protected_literals"),0,65536,1);
      (void)choice(out.at("mode"),{"fast","balanced","deep"});
      (void)choice(out.at("constraint_mode"),{"strict","exploratory"});
      if(out.has("allow_unverified"))(void)out.at("allow_unverified").flag();
      else out.set("allow_unverified",false);
      if(out.has("model_profile_ref"))nullable_string(out.at("model_profile_ref"),1);
      if(out.has("network_mode")) {
        auto network=choice(out.at("network_mode"),{"normal","offline"});
        require(network!="offline"||corpus=="local", "E_CONTRACT", "Offline search requires a local corpus");
      }
      if(out.has("stream"))(void)out.at("stream").flag();
      if(out.has("privacy")) {
        auto privacy=out.at("privacy");
        object(privacy,{"save_history","allow_external_suggestions","allow_private_query_egress"});
        for(const auto& key:privacy.keys())(void)privacy.at(key).flag();
      }
      auto budget=out.at("budget");
      object(budget,{"max_source_requests","max_reads","max_browser_seconds","max_model_calls","timeout_ms","max_bytes","max_tokens_estimated"},
                    {"max_source_requests","max_reads","max_model_calls","timeout_ms"});
      ceiling(integer(budget.at("max_source_requests"),0,1000),limits.search_source_requests);
      ceiling(integer(budget.at("max_reads"),0,10000),limits.search_reads);
      ceiling(integer(budget.at("max_model_calls"),0,1000),limits.search_model_calls);
      ceiling(integer(budget.at("timeout_ms"),100,86400000),limits.search_timeout_ms);
      if(budget.has("max_browser_seconds"))ceiling(integer(budget.at("max_browser_seconds"),0,86400),limits.search_browser_seconds);
      if(budget.has("max_bytes"))ceiling(integer(budget.at("max_bytes"),0),limits.search_bytes);
      if(budget.has("max_tokens_estimated"))ceiling(integer(budget.at("max_tokens_estimated"),0),limits.search_tokens_estimated);
      return out;
    }
  }

  Json validate_contract(std::string_view kind,const Json& input,std::int64_t validation_now_ms,const ContractLimits& limits) {
    require(one_of(kind,{"model-config","source-manifest","search-request"}), "E_SCHEMA", "Unknown contract kind");
    trusted_limits(limits);
    guard_shape(input,limits);
    // Json copies share json-c objects; reparsing prevents normalization from
    // modifying the caller's nested objects or an earlier validated result.
    auto out=Json::parse(input.dump());
    if(kind=="model-config")return models(std::move(out),limits);
    if(kind=="source-manifest")return source(std::move(out),validation_now_ms,limits);
    return search(std::move(out),limits);
  }
}
