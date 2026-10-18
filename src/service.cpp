#include "bzs/service.hpp"
#include "bzs/contracts.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <openssl/crypto.h>
#include <algorithm>
#include <chrono>
#include <set>
#ifndef _WIN32
#include <sys/stat.h>
#endif
namespace bzs {
  Auth::Auth(const std::filesystem::path& path) {
#ifndef _WIN32
    struct stat st {
    };
    require(::stat(path.c_str(), &st)==0, "E_AUTH_CONFIG", "Cannot inspect authentication file", 500);
    require((st.st_mode&0077)==0, "E_AUTH_CONFIG", "Authentication file must not be group/world accessible (chmod 600)", 500);
#endif
    auto config=Json::parse(read_file(path, 65536));
    config.allowed( {
      "tokens"
    });
    auto tokens=config.at("tokens");
    require(tokens.is_array()&&tokens.size()>=1&&tokens.size()<=16, "E_AUTH_CONFIG", "Require 1..16 token entries", 500);
    std::set<std::string> used;
    for(std::size_t i=0; i<tokens.size(); ++i) {
      auto e=tokens.at(i);
      e.allowed( {
        "sha256", "subject", "admin"
      });
      auto digest=required_string(e, "sha256", 64);
      auto subject=required_string(e, "subject", 128);
      require_identifier(subject);
      require(digest.size()==64&&std::all_of(digest.begin(), digest.end(), [](char c) {
        return (c>='a'&&c<='f')||(c>='0'&&c<='9');
      })&&used.insert(digest).second, "E_AUTH_CONFIG", "Invalid or duplicate token digest", 500);
      entries_.push_back( {
        digest, {
          subject, e.has("admin")?e.at("admin").flag():false
        }
      });
    }
  }
  Principal Auth::authenticate(std::string_view auth)const {
    require(auth.starts_with("Bearer ")&&auth.size()>=39&&auth.size()<=519, "E_AUTH", "Bearer token required", 401);
    auto token=auth.substr(7);
    for(unsigned char c:token)require(c>32&&c<127, "E_AUTH", "Invalid bearer token", 401);
    auto digest=sha256(token);
    std::optional<Principal> result;
    for(const auto&e:entries_)if(CRYPTO_memcmp(digest.data(), e.digest.data(), 64)==0)result=e.principal;
    require(result.has_value(), "E_AUTH", "Invalid bearer token", 401);
    return *result;
  }
  FixtureCatalog::FixtureCatalog(const std::filesystem::path& root) {
    auto base=std::filesystem::canonical(root);
    auto manifest=Json::parse(read_file(base/"manifest.json", 65536));
    manifest.allowed( {
      "source_id", "kind", "fixtures"
    });
    require(required_string(manifest, "kind", 16)=="fixture", "E_FIXTURE", "Only fixture manifests may be loaded");
    auto entries=manifest.at("fixtures");
    require(entries.is_array()&&entries.size()<=64, "E_FIXTURE", "Fixture count exceeded");
    std::size_t bytes=0;
    for(std::size_t i=0; i<entries.size(); ++i) {
      auto v=entries.at(i);
      v.allowed( {
        "id", "file", "url", "content_type", "title", "snippet"
      });
      auto id=required_string(v, "id", 128);
      require_identifier(id);
      auto rel=std::filesystem::path(required_string(v, "file", 256));
      require(!rel.is_absolute(), "E_FIXTURE", "Fixture path must be relative");
      for(const auto&part:rel)require(part!="..", "E_FIXTURE", "Fixture path traversal blocked");
      auto full=std::filesystem::canonical(base/rel);
      auto back=std::filesystem::relative(full, base);
      require(!back.empty()&&!back.is_absolute()&&*back.begin()!="..", "E_FIXTURE", "Fixture escapes approved directory");
      auto raw=read_file(full);
      bytes+=raw.size();
      require(bytes<=8*1024*1024, "E_QUOTA", "Fixture corpus exceeds 8 MiB");
      SourceDocument doc {
        required_string(v, "url", 4096), std::move(raw), required_string(v, "content_type", 64), required_string(v, "title", 1024)
      };
      metadata_host(doc.url);
      require(doc.content_type=="text/plain"||doc.content_type=="text/html", "E_FIXTURE", "Unsupported fixture type");
      require(fixtures_.emplace(id, Fixture {
        std::move(doc), id, required_string(v, "snippet", 2048)
      }).second, "E_FIXTURE", "Duplicate fixture identifier");
    }
  }
  const SourceDocument& FixtureCatalog::get(const std::string& id)const {
    auto it=fixtures_.find(id);
    require(it!=fixtures_.end(), "E_NOT_FOUND", "Fixture not found", 404);
    return it->second.document;
  }
  Json FixtureCatalog::list()const {
    Json a=Json::array();
    for(const auto&[id, f]:fixtures_) {
      Json j=Json::object();
      j.set("fixture_id", id);
      j.set("url", f.document.url);
      j.set("title", f.document.title);
      a.push(j);
    }
    Json out=Json::object();
    out.set("source_id", "builtin-fixtures");
    out.set("kind", "fixture");
    out.set("review_status", "test_only");
    out.set("is_live_web_source", false);
    out.set("fixtures", a);
    return out;
  }
  Json FixtureCatalog::search(const Query& q)const {
    Json hits=Json::array();
    for(const auto&[id, f]:fixtures_)if(matches(q, f.document.title, f.snippet, f.document.url, metadata_host(f.document.url), f.document.content_type)) {
      Json j=Json::object();
      j.set("fixture_id", id);
      j.set("url", f.document.url);
      j.set("title", f.document.title);
      j.set("snippet", f.snippet);
      j.set("snippet_kind", "fixture_search_snippet");
      j.set("body_read", false);
      hits.push(j);
    }
    Json out=Json::object();
    out.set("hits", hits);
    out.set("coverage", "registered_fixture_metadata_only");
    out.set("external_requests", static_cast<std::int64_t>(0));
    return out;
  }
  Json Service::capabilities() const {
    Json j=Json::object();
    j.set("version", BZS_VERSION);
    j.set("build_profile", web_?"s1_web":"offline_bootstrap");
    j.set("external_network", web_?"worker_mediated":"disabled");
    j.set("live_web_sources", static_cast<std::int64_t>(web_?web_->enabled_sources():0));
    j.set("production_ready", false);
    Json a=Json::array();
    for(const auto*op: {
      "capabilities", "contract.validate", "fixtures.list", "fixtures.search", "fixture.ingest", "query.compile", "document.ingest", "document.read", "document.range", "document.delete", "search", "search.page", "find", "evidence.create", "evidence.read", "stats", "job.create", "job.get", "job.control", "backup.create"
    })a.push(Json::string(op));
    if(web_)for(const auto*op: {
      "web.search", "web.read", "web.sources"
    })a.push(Json::string(op));
    j.set("operations", a);
    Json missing=Json::array();
    if(!web_)for(const auto*x: {
      "live_web_search", "url_fetch"
    })missing.push(Json::string(x));
    for(const auto*x: {
      "browser", "recursive_crawl", "pdf", "models", "semantic_search", "remote_mcp", "media", "windows_validation"
    })missing.push(Json::string(x));
    j.set("not_implemented", missing);
    return j;
  }
  Json Service::dispatch(const Principal& p, const Json& request) {
    request.allowed( {
      "operation", "params"
    });
    auto op=required_string(request, "operation", 64);
    auto args=request.has("params")?request.at("params"):Json::object();
    require(args.is_object(), "E_SCHEMA", "params must be an object");
    if(op=="capabilities") {
      args.allowed( {
      });
      return capabilities();
    }
    if(op=="contract.validate") {
      args.allowed( {
        "kind", "config"
      });
      auto kind=required_string(args, "kind", 64);
      auto effective=validate_contract(kind, args.at("config"), now_ms(), ContractLimits{});
      Json out=Json::object();
      out.set("kind", kind);
      out.set("validated", true);
      out.set("applied", false);
      out.set("effective_config", effective);
      return out;
    }
    if(op=="fixtures.list") {
      args.allowed( {
      });
      return fixtures_.list();
    }
    if(op=="query.compile") {
      args.allowed( {
        "query"
      });
      return parse_query(args.at("query")).json();
    }
    if(op=="fixtures.search") {
      args.allowed( {
        "query"
      });
      return fixtures_.search(parse_query(args.at("query")));
    }
    if(op=="fixture.ingest") {
      args.allowed( {
        "fixture_id"
      });
      return store_.ingest(p.subject, fixtures_.get(required_string(args, "fixture_id", 128)));
    }
    if(op=="document.ingest") {
      args.allowed( {
        "url", "content", "content_type", "title"
      });
      return store_.ingest(p.subject, {
        required_string(args, "url", 4096), required_string(args, "content", 1024*1024), required_string(args, "content_type", 64), args.has("title")?required_string(args, "title", 1024):""
      });
    }
    if(op=="document.read") {
      args.allowed( {
        "snapshot_id"
      });
      return store_.snapshot(p.subject, required_string(args, "snapshot_id", 64));
    }
    if(op=="document.range") {
      args.allowed( {
        "snapshot_id", "start_byte", "max_bytes"
      });
      return store_.range(p.subject, required_string(args, "snapshot_id", 64), int_option(args, "start_byte", 0, 0, 1LL<<40), int_option(args, "max_bytes", 20000, 1, 262144));
    }
    if(web_&&op=="web.search")return web_->search(p.subject, args);
    if(web_&&op=="web.read")return web_->read(p.subject, args);
    if(web_&&op=="web.sources") {
      args.allowed( {
      });
      return web_->sources();
    }
    if(op=="document.delete") {
      args.allowed( {
        "document_id"
      });
      return store_.erase(p.subject, required_string(args, "document_id", 64));
    }
    if(op=="search") {
      args.allowed( {
        "query", "max_candidates", "limit", "page_size"
      });
      auto q=parse_query(args.at("query"));
      auto max_candidates=int_option(args, "max_candidates", 2000, 1, 2000);
      auto limit=int_option(args, "limit", 200, 1, 500);
      auto page_size=int_option(args, "page_size", 20, 1, 100);
      // Validate all request options before creating a persistent search session.
      auto s=store_.search(p.subject, q, max_candidates, limit);
      auto page=store_.page(p.subject, s.at("session_id").str(), "", page_size);
      s.set("page", page);
      return s;
    }
    if(op=="search.page") {
      args.allowed( {
        "session_id", "cursor", "page_size"
      });
      return store_.page(p.subject, required_string(args, "session_id", 64), args.has("cursor")?required_string(args, "cursor", 64):"", int_option(args, "page_size", 20, 1, 100));
    }
    if(op=="find") {
      args.allowed( {
        "snapshot_id", "text", "limit"
      });
      return store_.find(p.subject, required_string(args, "snapshot_id", 64), required_string(args, "text", 4096), int_option(args, "limit", 16, 1, 64));
    }
    if(op=="evidence.create") {
      args.allowed( {
        "snapshot_id", "start_byte", "end_byte"
      });
      return store_.evidence(p.subject, required_string(args, "snapshot_id", 64), args.at("start_byte").num(), args.at("end_byte").num());
    }
    if(op=="evidence.read") {
      args.allowed( {
        "evidence_id"
      });
      return store_.evidence_get(p.subject, required_string(args, "evidence_id", 64));
    }
    if(op=="stats") {
      args.allowed( {
      });
      return store_.stats(p.subject);
    }
    if(op=="job.create") {
      args.allowed( {
        "fixture_id", "idempotency_key", "max_attempts", "timeout_ms"
      });
      auto fid=required_string(args, "fixture_id", 128);
      fixtures_.get(fid);
      return store_.job_create(p.subject, fid, required_string(args, "idempotency_key", 128), int_option(args, "max_attempts", 3, 1, 5), int_option(args, "timeout_ms", 60000, 100, 3600000));
    }
    if(op=="job.get") {
      args.allowed( {
        "job_id"
      });
      return store_.job_get(p.subject, required_string(args, "job_id", 64));
    }
    if(op=="job.control") {
      args.allowed( {
        "job_id", "action"
      });
      return store_.job_control(p.subject, required_string(args, "job_id", 64), required_string(args, "action", 16));
    }
    if(op=="backup.create") {
      args.allowed( {
      });
      require(p.admin, "E_FORBIDDEN", "Backup requires an administrator token", 403);
      std::size_t count=0;
      std::filesystem::create_directories(backup_root_);
      for(const auto&e:std::filesystem::directory_iterator(backup_root_))if(e.is_regular_file())++count;
      require(count<8, "E_QUOTA", "Backup retention limit reached; archive/remove backups before creating more", 507);
      return store_.backup(backup_root_/(random_id()+".sqlite"));
    }
    throw Error("E_CAPABILITY_UNAVAILABLE", "Operation is unavailable in this build; inspect capabilities", 422);
  }
  std::jthread start_worker(Store& store, const FixtureCatalog& fixtures) {
    return std::jthread([&](std::stop_token stop) {
      while(!stop.stop_requested()) {
        std::optional<Lease> l;
        try {
          l=store.lease_next();
          if(l) {
            const auto&doc=fixtures.get(l->fixture_id);
            auto parsed=parse_document(doc.raw, doc.content_type, doc.title);
            if(!stop.stop_requested())store.complete(*l, doc, parsed);
          }
        }catch(const Error&e) {
          if(l)try {
            store.fail(*l, e.code);
          }catch(...) {
          }
        } catch(...) {
          if(l)try {
            store.fail(*l, "E_INTERNAL");
          }catch(...) {
          }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
      }
    });
  }
}
