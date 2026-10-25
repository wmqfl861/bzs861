#include "bzs/service.hpp"
#include "bzs/highlight.hpp"
#include "bzs/tokenizer.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <atomic>
#include <barrier>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <set>
#include <thread>
#include <vector>
namespace bzs {
  struct StoreSearchTestAccess {
    static void set(Store& store,std::function<void()> callback) {
      std::lock_guard lock(store.mutex_);store.search_unlocked_for_test_=std::move(callback);
    }
  };
}
using namespace bzs;
namespace {
  using Test=std::pair<std::string, std::function<void()>>;
  std::vector<Test>&tests() {
    static std::vector<Test>a;
    return a;
  }
  struct Add {
    Add(const char*n, std::function<void()>f) {
      tests().emplace_back(n, std::move(f));
    }
  };
  std::size_t assertions=0;
#define TEST(n) void n();Add reg_##n(#n,n);void n()
#define CHECK(c) do{++assertions;if(!(c))throw std::runtime_error(std::string("check failed: ")+#c+" line "+std::to_string(__LINE__));}while(false)
  template<class F>void rejects(F f, const std::string&code) {
    ++assertions;
    try {
      f();
    }catch(const Error&e) {
      if(e.code==code)return;
      throw std::runtime_error("expected "+code+", got "+e.code);
    }
    throw std::runtime_error("expected "+code);
  }
  struct Temp {
    std::filesystem::path path=std::filesystem::temp_directory_path()/("bzs-test-"+random_id());
    Temp() {
      std::filesystem::create_directories(path);
    }
    ~Temp() {
      std::error_code ec;
      std::filesystem::remove_all(path, ec);
    }
  };
  SourceDocument text(const std::string&url, const std::string&body, const std::string&title="Test") {
    return {
      url, body, "text/plain", title
    };
  }
  Query q(const std::string&s) {
    return parse_query(Json::string(s));
  }
  // A handshaked barrier establishes that another Store operation completes
  // while matching is paused. The timeout is only a deadlock watchdog, not a
  // query-performance threshold; both threads are released/joined on failure.
  Json search_while(Store& store,bool freeze,const std::function<void()>& action,const std::string& query="NOT absent") {
    std::mutex mutex;std::condition_variable changed;
    bool entered=false,released=false,finished=false,action_finished=false,transaction_free=false;
    Json response;std::exception_ptr search_error,action_error;
    StoreSearchTestAccess::set(store,[&] {
      std::unique_lock lock(mutex);if(entered)return;
      transaction_free=sqlite3_get_autocommit(store.raw_handle())!=0;entered=true;changed.notify_all();
      changed.wait(lock,[&]{return released;});
    });
    std::thread searcher([&] {
      try {response=freeze?store.search("alice",q(query),2000,100):store.search_once("alice",q(query),2000,100);}
      catch(...) {search_error=std::current_exception();}
      {std::lock_guard lock(mutex);finished=true;}changed.notify_all();
    });
    bool reached=false,progressed=false;
    {
      std::unique_lock lock(mutex);
      reached=changed.wait_for(lock,std::chrono::seconds(5),[&]{return entered||finished;})&&entered;
    }
    std::thread actor;
    if(reached) {
      actor=std::thread([&] {
        try {action();}catch(...) {action_error=std::current_exception();}
        {std::lock_guard lock(mutex);action_finished=true;}changed.notify_all();
      });
      std::unique_lock lock(mutex);
      progressed=changed.wait_for(lock,std::chrono::seconds(5),[&]{return action_finished;});
    }
    {std::lock_guard lock(mutex);released=true;}changed.notify_all();
    searcher.join();if(actor.joinable())actor.join();StoreSearchTestAccess::set(store,{});
    CHECK(reached);CHECK(progressed);CHECK(transaction_free);
    if(action_error)std::rethrow_exception(action_error);
    if(search_error)std::rethrow_exception(search_error);
    return response;
  }
  Json search_hits(Store& store,bool freeze,const Json& result) {
    return freeze?store.page("alice",result.at("session_id").str(),"",100).at("hits"):result.at("hits");
  }
  Json req(const std::string&op, const Json&params) {
    auto j=Json::object();
    j.set("operation", op);
    j.set("params", params);
    return j;
  }
  using SqliteHandle=std::unique_ptr<sqlite3, decltype(&sqlite3_close)>;
  SqliteHandle open_database(const std::filesystem::path&path) {
    sqlite3*raw=nullptr;
    auto rc=sqlite3_open_v2(path.string().c_str(), &raw, SQLITE_OPEN_READWRITE, nullptr);
    SqliteHandle db(raw, sqlite3_close);
    CHECK(rc==SQLITE_OK);
    register_fts_tokenizer(raw);
    return db;
  }
  void database_exec(const std::filesystem::path&path, const char*sql) {
    auto db=open_database(path);
    CHECK(sqlite3_exec(db.get(), sql, nullptr, nullptr, nullptr)==SQLITE_OK);
  }
  std::int64_t database_scalar(const std::filesystem::path&path, const char*sql) {
    auto db=open_database(path);
    sqlite3_stmt*raw=nullptr;
    CHECK(sqlite3_prepare_v2(db.get(), sql, -1, &raw, nullptr)==SQLITE_OK);
    std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)> stmt(raw, sqlite3_finalize);
    CHECK(sqlite3_step(stmt.get())==SQLITE_ROW);
    return sqlite3_column_int64(stmt.get(), 0);
  }
  void restore_legacy_schema(const std::filesystem::path& path,int version,bool claude_s1=false) {
    auto db=open_database(path);
    auto exec=[&](const std::string& sql) {
      CHECK(sqlite3_exec(db.get(),sql.c_str(),nullptr,nullptr,nullptr)==SQLITE_OK);
    };
    exec("BEGIN IMMEDIATE;");
    exec("DROP TABLE map_hops; DROP TABLE map_candidates; DROP TABLE map_resources; DROP TABLE map_jobs; DROP TABLE robots_cache; DROP TABLE site_request_state; DROP TABLE read_job_items; DROP TABLE read_job_budgets; ALTER TABLE jobs DROP COLUMN kind;");
    exec("DROP TABLE content_fts; ALTER TABLE documents DROP COLUMN delete_generation;"
         "ALTER TABLE jobs DROP COLUMN created_delete_sequence;"
         "DELETE FROM settings WHERE key IN ('deletion_sequence','fts_tokenizer_version');");
    if(!claude_s1) {
      exec("ALTER TABLE documents DROP COLUMN deleted_at; ALTER TABLE snapshots DROP COLUMN meta_json;"
           "ALTER TABLE snapshots DROP COLUMN source_kind; DROP TABLE usage; DROP TABLE source_state;"
           "DROP INDEX evidence_snapshot; DROP INDEX session_rows_snapshot;");
    }
    if(claude_s1)exec("CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='bzs');");
    else if(version==2)exec("CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize=\"unicode61 remove_diacritics 0 tokenchars '_'\");");
    else exec("CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='unicode61');");
    sqlite3_stmt* rows=nullptr;
    sqlite3_stmt* insert=nullptr;
    CHECK(sqlite3_prepare_v2(db.get(),"SELECT d.id,d.owner,s.title,s.canonical_text FROM documents d JOIN snapshots s ON s.id=d.current_snapshot WHERE d.deleted=0 AND s.index_allowed=1",-1,&rows,nullptr)==SQLITE_OK);
    CHECK(sqlite3_prepare_v2(db.get(),"INSERT INTO content_fts(document_id,owner,title,body)VALUES(?,?,?,?)",-1,&insert,nullptr)==SQLITE_OK);
    int rc=SQLITE_OK;
    while((rc=sqlite3_step(rows))==SQLITE_ROW) {
      for(int i=0;i<4;++i) {
        auto raw=reinterpret_cast<const char*>(sqlite3_column_text(rows,i));
        auto value=std::string(raw,static_cast<std::size_t>(sqlite3_column_bytes(rows,i)));
        if(!claude_s1&&version==2&&i>=2)value=fts_index_text(value);
        CHECK(sqlite3_bind_text(insert,i+1,value.data(),static_cast<int>(value.size()),SQLITE_TRANSIENT)==SQLITE_OK);
      }
      CHECK(sqlite3_step(insert)==SQLITE_DONE);
      CHECK(sqlite3_reset(insert)==SQLITE_OK);
    }
    CHECK(rc==SQLITE_DONE);
    sqlite3_finalize(insert);
    sqlite3_finalize(rows);
    exec("PRAGMA user_version="+std::to_string(version)+"; COMMIT;");
  }
  void restore_schema_one_index(const std::filesystem::path& path) { restore_legacy_schema(path,1); }
  Json reviewed_sources(Json manifest) {
    auto sources=manifest.at("sources");
    for(std::size_t i=0;i<sources.size();++i) {
      auto source=sources.at(i);
      auto id=source.at("id").str();
      if(!source.has("min_interval_ms"))source.set("min_interval_ms",static_cast<std::int64_t>(0));
      if(!source.has("daily_cap"))source.set("daily_cap",static_cast<std::int64_t>(500));
      Json params=source.has("params")?source.at("params"):Json::object();
      params.set("adapter","offline_fixture");
      params.set("base_url","https://example.com/search");
      source.set("params",params);
      Json contract=Json::object();
      contract.set("id",id);
      contract.set("provider_id",source.at("provider"));
      contract.set("index_family",source.at("index_family"));
      contract.set("adapter_id","offline_fixture");
      contract.set("endpoint","https://example.com/search");
      auto capability=source.at("capability").str();
      if(capability=="encyclopedia")capability="web";
      else if(capability=="papers")capability="scholar";
      contract.set("capability",capability);
      contract.set("enabled",source.has("enabled")&&source.at("enabled").flag());
      contract.set("logical_service_id",id);
      contract.set("review",Json::parse(R"({"status":"approved","payment_scheme":"no_payment","automated_access":"approved",
        "checked_at":"1970-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://example.com/terms",
        "cache_policy":"allowed_with_limits","redistribution_policy":"links_only","approved_by":"offline-fixture"})"));
      Json limits=Json::object();
      limits.set("max_concurrency",static_cast<std::int64_t>(1));
      limits.set("min_interval_ms",source.at("min_interval_ms"));
      limits.set("shared_budget_key",id);
      limits.set("request_quota",source.at("daily_cap"));
      limits.set("quota_window_seconds",static_cast<std::int64_t>(86400));
      contract.set("limits",limits);
      source.set("contract",contract);
    }
    return manifest;
  }
  TEST(json_roundtrip) {
    auto a=Json::parse(R"({"b":2,"a":"中文😀","n":null,"f":false})");
    CHECK(a.at("a").str()=="中文😀");
    CHECK(a.at("n").is_null());
    CHECK(!a.at("f").flag());
    CHECK(a.canonical()==R"({"a":"中文😀","b":2,"f":false,"n":null})");
    CHECK(Json::parse(a.dump()).canonical()==a.canonical());
  }
  TEST(json_shared_readers_preserve_serialization_and_alias_lifetimes) {
    const auto shared=Json::parse(R"({"name":"immutable shared tree","items":[{"text":"nested value","enabled":true},42,null],"payload":{"unicode":"中文","escaped":"quote\" and slash\\","values":[1,2,3]}})");
    const auto expected=shared.canonical();
    constexpr std::size_t count=4;
    std::barrier start(static_cast<std::ptrdiff_t>(count));
    std::vector<std::exception_ptr> errors(count);
    std::vector<std::thread> workers;
    for(std::size_t t=0;t<count;++t)workers.emplace_back([&,t] {
      for(int round=0;round<100;++round) {
        start.arrive_and_wait();
        try {
          auto alias=shared;
          auto child=alias.at("payload");
          if(Json::parse(alias.dump(round%2==0)).canonical()!=expected)
            throw std::runtime_error("Concurrent serialization changed the shared tree");
          auto holder=Json::object(),list=Json::array();
          holder.set("root",alias);holder.set("child",child);
          list.push(child);list.push(alias.at("items").at(0));holder.set("list",list);
          // Replacing a private holder's entry releases a shared child reference.
          holder.set("child",Json::object());
          const auto copy=Json::parse(holder.dump());
          if(copy.at("root").canonical()!=expected||
             copy.at("list").at(0).at("unicode").str()!="中文"||
             !copy.at("list").at(1).at("enabled").flag())
            throw std::runtime_error("Concurrent alias lifetime changed a retained child");
        } catch(...) { if(!errors[t])errors[t]=std::current_exception(); }
      }
    });
    for(auto& worker:workers)worker.join();
    for(const auto& error:errors)if(error)std::rethrow_exception(error);
    CHECK(shared.canonical()==expected);
  }
  TEST(json_duplicate_keys) {
    rejects([] {
      Json::parse(R"({"x":1,"x":2})");
    }, "E_JSON_DUPLICATE");
    rejects([] {
      Json::parse(R"({"a":1,"\u0061":2})");
    }, "E_JSON_DUPLICATE");
    rejects([] {
      Json::parse(R"({"x":{"a":1,"a":2}})");
    }, "E_JSON_DUPLICATE");
  }
  TEST(json_malformed) {
    for(auto s: {
      "{", "[1,]", "{\"a\":01}", "{\"a\":NaN}", "{} trailing", "{\"a\":1}{}", "{\"a\":true/*hi*/}"
    }) {
      bool error=false;
      try {
        Json::parse(s);
      }catch(const Error&) {
        error=true;
      }
      CHECK(error);
    }
  }
  TEST(json_strict_types) {
    auto j=Json::parse(R"({"number":"2","flag":1})");
    rejects([&] {
      j.at("number").num();
    }, "E_SCHEMA");
    rejects([&] {
      j.at("flag").flag();
    }, "E_SCHEMA");
    rejects([&] {
      j.at("missing");
    }, "E_SCHEMA");
    rejects([&] {
      j.allowed( {
        "number"
      });
    }, "E_SCHEMA");
  }
  TEST(json_depth_limit) {
    auto s=std::string(70, '[')+"0"+std::string(70, ']');
    rejects([&] {
      Json::parse(s);
    }, "E_JSON_LIMIT");
  }
  TEST(json_scalar_unicode_and_ranges) {
    CHECK(Json::parse(R"("\u4e2d\u6587")").str()=="中文");
    // Keep surrogate escapes outside CHECK's stringification on MSVC's preprocessor.
    auto supplementary=Json::parse(R"("\ud83d\ude00")");
    CHECK(supplementary.str()=="😀");
    rejects([] {
      Json::parse(R"("\ud800")");
    }, "E_JSON");
    rejects([] {
      Json::parse(R"({"x\u0000y":1})");
    }, "E_JSON");
    rejects([] {
      Json::parse("9223372036854775808");
    }, "E_JSON_RANGE");
    rejects([] {
      Json::parse("1e999");
    }, "E_JSON_RANGE");
  }
  TEST(utf8_boundaries) {
    CHECK(valid_utf8("中文😀"));
    CHECK(!valid_utf8(std::string("\xc0\xaf", 2)));
    CHECK(!valid_utf8(std::string("\xed\xa0\x80", 3)));
    CHECK(!valid_utf8(std::string("\xf4\x90\x80\x80", 4)));
    CHECK(utf8_boundary("中a", 3));
    CHECK(!utf8_boundary("中a", 1));
    CHECK(utf8_prefix("中a", 2).empty());
  }
  TEST(hash_and_random) {
    CHECK(sha256("abc")=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    auto a=random_id(), b=random_id();
    CHECK(a.size()==48&&a!=b);
  }
  TEST(metadata_urls) {
    CHECK(metadata_host("https://Docs.Example/path")=="docs.example");
    for(auto s: {
      "file:///etc/passwd", "https://user:pass@example.com/", "https://example.com\\evil", "https://example..com/",
      "https://example.com:0/", "https://example.com:99999/", "https://example.com:ab/", "https://[::1]/"
    })rejects([&] {
      metadata_host(s);
    }, "E_URL");
    CHECK(metadata_host("https://example.com:8443/x")=="example.com");
  }
  TEST(query_symbols) {
    auto x=q("std::expected C++");
    CHECK(x.root.children.at(0).value=="std::expected");
    CHECK(matches(x, "", "use std::expected in C++", "", "", ""));
    CHECK(!matches(x, "", "use std::expected in C#", "", "", ""));
    CHECK(!fts_candidates(x).has_value());
  }
  TEST(query_boolean) {
    auto x=q("alpha OR beta gamma");
    CHECK(matches(x, "", "alpha", "", "", ""));
    CHECK(!matches(x, "", "beta", "", "", ""));
    CHECK(matches(x, "", "beta gamma", "", "", ""));
    auto y=q("(alpha OR beta) NOT gamma");
    CHECK(matches(y, "", "alpha", "", "", ""));
    CHECK(!matches(y, "", "beta gamma", "", "", ""));
  }
  TEST(query_phrases) {
    CHECK(matches(q("\"exact phrase\""), "", "an exact phrase!", "", "", ""));
    CHECK(!matches(q("\"exact phrase\""), "", "exact and phrase", "", "", ""));
    CHECK(!matches(q("app"), "", "apple", "", "", ""));
    CHECK(matches(q("app"), "", "app works", "", "", ""));
  }
  TEST(query_fields) {
    CHECK(matches(q("intitle:\"C++ guide\""), "C++ guide", "", "", "", ""));
    CHECK(!matches(q("intitle:guide"), "other", "guide", "", "", ""));
    auto x=q("site:example.com guide");
    CHECK(matches(x, "guide", "", "", "docs.example.com", "text/html"));
    CHECK(!matches(x, "guide", "", "", "notexample.com", "text/html"));
  }
  TEST(query_quoted_directives) {
    for(auto value: {"intitle:guide", "inbody:guide", "site:example.com", "filetype:txt"}) {
      auto query=q("\""+std::string(value)+"\"");
      CHECK(query.root.op=="phrase");
      CHECK(query.root.field=="any");
      CHECK(query.root.value==value);
      CHECK(query.hosts.empty()&&query.types.empty());
      CHECK(matches(query, "neutral", value, "", "", ""));
    }
    CHECK(matches(q("\"site:example.com\" OR alpha"), "", "alpha", "", "", ""));
    CHECK(q("si\"te:example.com\"").hosts.empty());
    auto field=q("intitle:\"C++ guide\"");
    CHECK(field.root.field=="title"&&field.root.value=="C++ guide");
    CHECK(matches(field, "C++ guide", "", "", "", ""));
    CHECK(!matches(field, "neutral", "C++ guide", "", "", ""));
    auto filter=q("site:\"example.com\" AND guide");
    CHECK(filter.hosts.size()==1&&filter.hosts.at(0)=="example.com");
  }
  TEST(query_ast) {
    auto j=Json::parse(R"({"op":"and","children":[{"op":"literal","value":"C++"},{"op":"not","child":{"op":"term","value":"java"}}]})");
    CHECK(matches(parse_query(j), "", "C++ library", "", "", ""));
    rejects([] {
      parse_query(Json::parse(R"({"op":"term","value":"x","field":"author"})"));
    }, "E_CAPABILITY_UNAVAILABLE");
  }
  TEST(query_errors) {
    for(auto s: {
      "", "alpha OR", "(alpha", "alpha)", "\"unclosed", "NOT", "site:example.com", "site:example.com OR x"
    }) {
      bool err=false;
      try {
        q(s);
      }catch(const Error&) {
        err=true;
      }
      CHECK(err);
    }
  }
  TEST(query_chinese) {
    auto x=q("搜索");
    CHECK(fts_candidates(x).has_value());
    CHECK(matches(x, "", "中文搜索工具", "", "", ""));
  }
  TEST(query_flat_conjunction) {
    std::string terms="alpha";
    for(int i=1; i<50; ++i)terms+=" alpha";
    auto query=q(terms);
    CHECK(query.root.children.size()==50);
    CHECK(Json::parse(query.json().dump()).is_object());
    CHECK(matches(q("guide AND site:example.com"), "guide", "", "", "example.com", "text/html"));
  }
  TEST(html_main_code) {
    auto p=parse_document(read_file(std::filesystem::path(BZS_FIXTURE_DIR)/"developer.html"), "text/html");
    CHECK(p.title=="C++ 搜索工具开发指南");
    CHECK(p.text.find("std::expected<int, Error>")!=std::string::npos);
    CHECK(p.text.find("evil_script_content")==std::string::npos);
    CHECK(p.text.find("页脚广告")==std::string::npos);
    CHECK(p.text.find("隐藏密钥")==std::string::npos);
    CHECK(p.links.size()==1);
  }
  TEST(html_noindex) {
    auto p=parse_document("<html><head><meta name='robots' content='noindex'></head><body>text</body></html>", "text/html");
    CHECK(!p.index_allowed);
  }
  TEST(html_selected_subtree) {
    auto p=parse_document("<body><aside>before-noise</aside><main>only-main<a href='/inside'>link</a></main><aside><a href='/outside'>after-noise</a></aside></body>", "text/html");
    CHECK(p.text=="only-mainlink");
    CHECK(p.links.size()==1&&p.links.at(0).str()=="/inside");
    CHECK(p.parser_version=="bootstrap-html/3");
  }
  TEST(html_hidden_ancestors) {
    for(const auto&wrapper:std::vector<std::pair<std::string,std::string>> {
      {"<div hidden>", "</div>"}, {"<div aria-hidden='true'>", "</div>"},
      {"<div style='display: none'>", "</div>"}, {"<nav>", "</nav>"}, {"<template>", "</template>"}
    }) {
      auto html="<body>"+wrapper.first+"<main>hidden-canary</main>"+wrapper.second+"<article>visible-content</article></body>";
      CHECK(parse_document(html, "text/html").text=="visible-content");
    }
    rejects([] {
      parse_document("<html><head><title>metadata-only</title></head><body hidden>hidden-canary</body></html>", "text/html");
    }, "E_PARSE_FAILED");
  }
  TEST(html_robots_tokens) {
    for(auto directives: {"none", " NONE ", "index, none", "NOINDEX, follow", "\tnoindex\n"}) {
      auto html="<head><meta name='robots' content='"+std::string(directives)+"'></head><body>canary</body>";
      CHECK(!parse_document(html, "text/html").index_allowed);
    }
    CHECK(parse_document("<head><meta name='robots' content='notnoindex'></head><body>canary</body>", "text/html").index_allowed);
  }
  TEST(html_guards) {
    rejects([] {
      parse_document("<!ENTITY evil SYSTEM 'file:///etc/passwd'><body>x</body>", "text/html");
    }, "E_ACCESS_RESTRICTED");
    rejects([] {
      parse_document("<body><script>only()</script></body>", "text/html");
    }, "E_PARSE_FAILED");
    rejects([] {
      parse_document("x", "application/pdf");
    }, "E_CAPABILITY_UNAVAILABLE");
  }
  TEST(document_limits) {
    rejects([] {
      parse_document(std::string(1024*1024+1, 'x'), "text/plain");
    }, "E_CONTENT_TOO_LARGE");
    rejects([] {
      parse_document(std::string("\xc0\xaf", 2), "text/plain");
    }, "E_UTF8");
  }
  TEST(store_snapshots) {
    Temp t;
    Store s(t.path/"db");
    auto a=s.ingest("alice", text("https://x.example/a", "hello world"));
    auto b=s.ingest("alice", text("https://x.example/a", "hello world"));
    CHECK(a.at("snapshot_id").str()==b.at("snapshot_id").str());
    CHECK(b.at("reused").flag());
    auto c=s.ingest("alice", text("https://x.example/a", "updated world"));
    CHECK(c.at("revision").num()==2);
    CHECK(s.snapshot("alice", a.at("snapshot_id").str()).at("canonical_text").str()=="hello world");
  }
  TEST(store_owner_isolation) {
    Temp t;
    Store s(t.path/"db");
    auto a=s.ingest("alice", text("https://x.example/a", "secretword"));
    rejects([&] {
      s.snapshot("bob", a.at("snapshot_id").str());
    }, "E_NOT_FOUND");
    CHECK(s.search("bob", q("secretword")).at("frozen_count").num()==0);
    CHECK(s.stats("bob").at("documents").num()==0);
  }
  TEST(store_search) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("alice", text("https://x.example/a", "C++ indexing 中文搜索工具"));
    s.ingest("alice", text("https://x.example/b", "C# indexing"));
    auto a=s.search("alice", q("indexing"));
    CHECK(a.at("frozen_count").num()==2);
    CHECK(a.at("retrieval").str()=="fts5_bm25_verified");
    CHECK(s.search("alice", q("C++")).at("frozen_count").num()==1);
    CHECK(s.search("alice", q("搜索")).at("frozen_count").num()==1);
  }
  TEST(single_response_search_has_no_lifetime_or_active_session_cost) {
    Temp t;const auto path=t.path/"db";std::int64_t now=1800000000000;
    Store store(path,[&]{return now;});const auto query=q("marker");
    const auto empty=store.search_once("alice",query);
    CHECK(empty.at("hits").size()==0);CHECK(empty.at("returned_count").num()==0);
    store.ingest("alice",text("https://x.example/a","marker first"));
    const auto second=store.ingest("alice",text("https://x.example/b","marker second"));
    store.ingest("bob",text("https://x.example/bob","marker private"));
    for(int i=0;i<80;++i) {
      now+=1000;const auto once=store.search_once("alice",query);
      CHECK(once.at("result_mode").str()=="single");CHECK(once.at("returned_count").num()==2);
      CHECK(!once.has("session_id")&&!once.has("result_set_version")&&!once.has("next_cursor"));
      CHECK(!once.at("partial").flag());
    }
    for(const auto* sql:{"SELECT count(*) FROM sessions","SELECT count(*) FROM session_rows",
                        "SELECT count(*) FROM search_stream_receipts","SELECT count(*) FROM search_event_log"})CHECK(database_scalar(path,sql)==0);
    const auto frozen=store.search("alice",query);const auto id=frozen.at("session_id").str();
    const auto page=store.page("alice",id,"",1);CHECK(page.at("has_more").flag());
    const auto cursor=page.at("next_cursor").str(),last=store.page("alice",id,cursor,1).canonical();
    for(int i=1;i<64;++i)store.search("alice",query);
    rejects([&]{store.search("alice",query);},"E_QUOTA");
    for(int i=0;i<80;++i)CHECK(store.search_once("alice",query).at("returned_count").num()==2);
    CHECK(database_scalar(path,"SELECT count(*) FROM sessions")==64);
    CHECK(database_scalar(path,"SELECT count(*) FROM session_rows")==128);
    CHECK(database_scalar(path,"SELECT count(*) FROM search_stream_receipts")==64);
    CHECK(store.page("alice",id,"",1).canonical()==page.canonical());
    CHECK(store.page("alice",id,cursor,1).canonical()==last);
    CHECK(store.search_once("bob",query).at("returned_count").num()==1);
    store.erase("alice",second.at("document_id").str());
    CHECK(store.search_once("alice",query).at("returned_count").num()==1);
    rejects([&]{store.search_once("alice",query,2000,101);},"E_SCHEMA");
    rejects([&]{store.search_once("alice",query,0,1);},"E_SCHEMA");
  }
  TEST(store_candidate_predicate_agreement) {
    Temp t;
    Store s(t.path/"db");
    std::vector<SourceDocument> docs;
    for(auto body: {"apple", "test中文", "中文test", "test\u0301x", "testé", "_", "foo_bar", "test_test", "café中", "prefix😀suffix", "ABC搜索引擎def", "alpha𠀀ending", "中文\u0301x", "한글검색", "検索エンジン"}) {
      docs.push_back(text("https://x.example/"+std::to_string(docs.size()), body, "neutral"));
    }
    docs.push_back(text("https://x.example/title", "body", "label\u0301suffix"));
    for(const auto&doc:docs)s.ingest("alice", doc);
    for(auto expression: {"\"app\"", "test", "_", "foo_bar", "test_test", "intitle:label", "\"_\"", "test OR foo_bar", "café", "fé", "😀", "中", "中文", "搜索引擎", "ABC搜索", "한글", "検索", "test OR \"app\"", "test AND \"test\""}) {
      auto query=q(expression);
      std::int64_t expected=0;
      for(const auto&doc:docs)if(matches(query, doc.title, doc.raw, doc.url, "x.example", doc.content_type))++expected;
      auto result=s.search("alice", query);
      CHECK(result.at("frozen_count").num()==expected);
      CHECK(!result.at("partial").flag());
    }
    auto phrase=s.search("alice", q("\"app\""));
    CHECK(phrase.at("frozen_count").num()==1);
    CHECK(phrase.at("retrieval").str()=="fts5_substring_verified");
    auto mixed=s.search("alice", q("test"));
    CHECK(mixed.at("frozen_count").num()==4);
    CHECK(mixed.at("retrieval").str()=="fts5_bm25_verified");
    CHECK(s.search("alice", q("_ ")).at("frozen_count").num()==1);
    auto partial=s.search("alice", q("test"), 1, 10);
    CHECK(partial.at("scanned_candidates").num()==1&&partial.at("partial").flag());
  }
  TEST(substring_index_finds_old_phrases_symbols_and_multilingual_text_before_budget) {
    std::int64_t now=1800000000000;Store store(":memory:",[&]{return now;});
    const auto target=store.ingest("alice",text("https://old.example/rare/urlneedle", "commonword machine learning c++ node.js café Москва apple sqlite fts5 prefix😀suffix", "Rare title"));
    for(int i=0;i<2100;++i) {
      ++now;store.ingest("alice",text("https://new.example/"+std::to_string(i),"commonword filler unrelated content","ordinary"));
    }
    for(int i=0;i<30;++i) { ++now;store.ingest("bob",text("https://private.example/"+std::to_string(i),"commonword machine learning c++ node.js café Москва apple sqlite fts5 prefix😀suffix","private")); }
    for(const auto* expression:{"\"machine learning\"","c++","node.js","café","Москва","\"app\"","\"achine learn\"","\"sqlite fts\"","fé","😀",
                                "commonword AND \"machine learning\"","\"machine learning\" OR \"unseen phrase\"","\"machine learning\" NOT absent",
                                "site:old.example \"machine learning\"","filetype:txt \"machine learning\""}) {
      const auto once=store.search_once("alice",q(expression),1,100);
      CHECK(once.at("returned_count").num()==1);CHECK(once.at("hits").at(0).at("snapshot_id").str()==target.at("snapshot_id").str());
      CHECK(once.at("scanned_candidates").num()==1);CHECK(!once.at("partial").flag());
    }
    const auto url_query=parse_query(Json::parse(R"({"op":"phrase","field":"url","value":"rare/urlneedle"})"));
    CHECK(store.search_once("alice",url_query,1).at("returned_count").num()==1);
    CHECK(store.search_once("alice",q("\"rare/urlneedle\""),1).at("returned_count").num()==0);
    const auto frozen=store.search("alice",q("\"machine learning\""),1,100);
    CHECK(store.page("alice",frozen.at("session_id").str(),"",100).at("hits").at(0).at("snapshot_id").str()==target.at("snapshot_id").str());
    CHECK(store.search_once("alice",q("commonword"),1,100).at("partial").flag());
    CHECK(store.search_once("alice",q("NOT absent"),1,100).at("partial").flag());
    store.ingest("alice",text("https://old.example/rare/urlneedle","replacement content","replacement"));
    CHECK(store.search_once("alice",q("\"machine learning\"")).at("returned_count").num()==0);
    CHECK(store.page("alice",frozen.at("session_id").str(),"",100).at("hits").at(0).at("snapshot_id").str()==target.at("snapshot_id").str());
    store.erase("alice",target.at("document_id").str());
    CHECK(store.search_once("alice",url_query).at("returned_count").num()==0);
  }
  TEST(substring_candidates_preserve_exact_matching_across_fields_and_boolean_shapes) {
    Store store(":memory:");
    std::vector<SourceDocument> docs={
      text("https://x.example/URLonlyneedle","machine learning sqlite fts5 café prefix😀suffix","apple"),
      text("https://x.example/two","ac++ c++foo node.jsx CAFÉ москва test\u0301x","machine"),
      text("https://x.example/three","C++14 node.js testé Москва e\u0301","learning"),
      text("https://x.example/four","unrelated","café 中文"),
      text("https://x.example/five","xxfoobar _ foo_bar test_test","title")};
    for(const auto& doc:docs)store.ingest("alice",doc);
    std::vector<Query> queries;
    for(const auto* expression:{"\"app\"","\"achine learn\"","\"sqlite fts\"","c++","node.js","café","CAFÉ","Москва","москва","fé","😀","test","e\u0301",
         "\"_\"","foo_bar","\"machine learning\"","\"machine learning\" OR café","c++ AND NOT node.js","NOT \"machine learning\"","c++ OR NOT café",
         "intitle:café","inbody:café","\"URLonlyneedle\""})queries.push_back(q(expression));
    for(const auto* field:{"any","title","body","url"}) { auto ast=Json::object();ast.set("op","phrase");ast.set("field",field);ast.set("value",field==std::string("url")?"URLonlyneedle":"machine learning");queries.push_back(parse_query(ast)); }
    for(const auto& query:queries) {
      std::set<std::string> expected,actual;
      for(const auto& doc:docs)if(matches(query,doc.title,doc.raw,doc.url,"x.example",doc.content_type))expected.insert(doc.url);
      const auto response=store.search_once("alice",query,2000,100);const auto hits=response.at("hits");
      for(std::size_t i=0;i<hits.size();++i)actual.insert(hits.at(i).at("url").str());
      CHECK(actual==expected);CHECK(!response.at("partial").flag());
    }
  }
  TEST(substring_overflow_keeps_exact_matches_and_bounds_derived_fields) {
    std::string high_entropy;
    for(std::uint32_t cp=0x10000;cp<0x10000+23000;++cp) {
      high_entropy+=static_cast<char>(0xf0|(cp>>18));high_entropy+=static_cast<char>(0x80|((cp>>12)&0x3f));
      high_entropy+=static_cast<char>(0x80|((cp>>6)&0x3f));high_entropy+=static_cast<char>(0x80|(cp&0x3f));
    }
    CHECK(indexed_grams(high_entropy,'b')=="boverflow");
    Store store(":memory:");store.ingest("alice",text("https://x.example/entropy",high_entropy,"neutral"));
    auto exact=q(high_entropy.substr(high_entropy.size()-12));
    const auto found=store.search_once("alice",exact,1,100);CHECK(found.at("returned_count").num()==1);CHECK(!found.at("partial").flag());
    const auto absent=store.search_once("alice",q("\"not present\""),1,100);CHECK(absent.at("scanned_candidates").num()==1);CHECK(absent.at("returned_count").num()==0);CHECK(!absent.at("partial").flag());
    auto large=Json::object(),groups=Json::array();large.set("op","or");
    for(int group=0;group<4;++group) {
      auto branch=Json::object(),leaves=Json::array();branch.set("op","or");
      for(int leaf=0;leaf<60;++leaf) { auto item=Json::object();item.set("op","phrase");item.set("value",high_entropy.substr(static_cast<std::size_t>(group*60+leaf)*4,80));leaves.push(item); }
      branch.set("children",leaves);groups.push(branch);
    }
    large.set("children",groups);const auto query=parse_query(large);
    CHECK(!substring_candidates(query));CHECK(store.search_once("alice",query,1,100).at("returned_count").num()==1);
  }
  TEST(known_word_index_upgrade_is_atomic_and_preserves_business_rows) {
    Temp temp;const auto path=temp.path/"db";const auto clock=[] {return std::int64_t{1800000000000};};
    Json snapshot,evidence,session,page,job;
    {
      Store store(path,clock);const auto doc=store.ingest("alice",text("https://x.example/upgrade","machine learning café","old index"));
      snapshot=store.snapshot("alice",doc.at("snapshot_id").str());evidence=store.evidence("alice",doc.at("snapshot_id").str(),0,7);
      session=store.search("alice",q("machine"));page=store.page("alice",session.at("session_id").str(),"",100);
      job=store.job_create("alice","fixture","upgrade-key");
    }
    // The unchanged bzs/2 word tokenizer is the old derived format. The separate
    // recorded compatibility probe also creates this database with real 442 code.
    database_exec(path,"DROP TABLE substring_fts; UPDATE settings SET value='bzs-ascii-cjk/2' WHERE key='fts_tokenizer_version'; CREATE TRIGGER reject_index_marker BEFORE UPDATE ON settings WHEN NEW.key='fts_tokenizer_version' BEGIN SELECT RAISE(ABORT,'migration fixture'); END;");
    rejects([&]{Store blocked(path,clock);},"E_STORAGE");
    CHECK(database_scalar(path,"SELECT COUNT(*) FROM sqlite_master WHERE name='substring_fts'")==0);
    CHECK(database_scalar(path,"SELECT COUNT(*) FROM settings WHERE key='fts_tokenizer_version' AND value='bzs-ascii-cjk/2'")==1);
    database_exec(path,"DROP TRIGGER reject_index_marker;");
    {
      Store upgraded(path,clock);
      CHECK(upgraded.search_once("alice",q("\"achine learn\"")).at("returned_count").num()==1);
      CHECK(upgraded.snapshot("alice",snapshot.at("snapshot_id").str()).canonical()==snapshot.canonical());
      CHECK(upgraded.evidence_get("alice",evidence.at("evidence_id").str()).at("quote").str()=="machine");
      CHECK(upgraded.page("alice",session.at("session_id").str(),"",100).canonical()==page.canonical());
      CHECK(upgraded.job_get("alice",job.at("job_id").str()).at("state").str()=="queued");
      CHECK(upgraded.job_create("alice","fixture","upgrade-key").at("reused").flag());
    }
    CHECK(database_scalar(path,"SELECT COUNT(*) FROM substring_fts")==1);
    database_exec(path,"UPDATE settings SET value='unknown-index/999' WHERE key='fts_tokenizer_version';");
    rejects([&]{Store unknown(path,clock);},"E_SCHEMA_VERSION");
  }
  TEST(store_updates_index) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://x.example/a", "uniquebefore"));
    s.ingest("a", text("https://x.example/a", "uniqueafter"));
    CHECK(s.search("a", q("uniquebefore")).at("frozen_count").num()==0);
    CHECK(s.search("a", q("uniqueafter")).at("frozen_count").num()==1);
  }
  TEST(store_noindex) {
    Temp t;
    Store s(t.path/"db");
    auto a=s.ingest("a", {
      "https://x.example/n", "<head><meta name='robots' content='noindex'></head><body>canary</body>", "text/html", "n"
    });
    CHECK(!a.at("indexed").flag());
    CHECK(s.search("a", q("canary")).at("frozen_count").num()==0);
    CHECK(!s.snapshot("a", a.at("snapshot_id").str()).at("canonical_text").str().empty());
  }
  TEST(session_stable) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://x.example/a", "stable old"));
    s.ingest("a", text("https://x.example/b", "stable two"));
    auto sid=s.search("a", q("stable")).at("session_id").str();
    auto p1=s.page("a", sid, "", 1);
    CHECK(p1.at("has_more").flag());
    auto cursor=p1.at("next_cursor").str();
    s.ingest("a", text("https://x.example/a", "stable new"));
    s.ingest("a", text("https://x.example/c", "stable three"));
    auto p2=s.page("a", sid, cursor, 1);
    CHECK(!p2.at("has_more").flag());
    CHECK(p1.at("hits").at(0).at("document_id").str()!=p2.at("hits").at(0).at("document_id").str());
    CHECK(p1.canonical()==s.page("a", sid, "", 1).canonical());
  }
  TEST(session_cursor_isolation) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://x.example/a", "stable"));
    s.ingest("a", text("https://x.example/b", "stable"));
    auto one=s.search("a", q("stable")).at("session_id").str();
    auto two=s.search("a", q("stable")).at("session_id").str();
    auto cursor=s.page("a", one, "", 1).at("next_cursor").str();
    rejects([&] {
      s.page("a", two, cursor, 1);
    }, "E_CURSOR");
    rejects([&] {
      s.page("b", one, "", 1);
    }, "E_NOT_FOUND");
  }
  TEST(delete_revokes_paths) {
    Temp t;
    Store s(t.path/"db");
    auto a=s.ingest("a", text("https://x.example/a", "中文 evidence"));
    auto sn=a.at("snapshot_id").str();
    auto se=s.search("a", q("evidence")).at("session_id").str();
    auto ev=s.evidence("a", sn, 0, 6);
    CHECK(ev.at("quote").str()=="中文");
    s.erase("a", a.at("document_id").str());
    // The frozen session stays usable but can no longer return the deleted document.
    CHECK(s.page("a", se, "", 10).at("hits").size()==0);
    rejects([&] {
      s.snapshot("a", sn);
    }, "E_NOT_FOUND");
    rejects([&] {
      s.evidence_get("a", ev.at("evidence_id").str());
    }, "E_NOT_FOUND");
    CHECK(s.search("a", q("evidence")).at("frozen_count").num()==0);
  }
  TEST(evidence_boundaries) {
    Temp t;
    Store s(t.path/"db");
    auto sid=s.ingest("a", text("https://x.example/a", "中😀abc中")).at("snapshot_id").str();
    rejects([&] {
      s.evidence("a", sid, 1, 3);
    }, "E_EVIDENCE_RANGE");
    rejects([&] {
      s.evidence("a", sid, 0, 4);
    }, "E_EVIDENCE_RANGE");
    auto e=s.evidence("a", sid, 0, 7);
    CHECK(e.at("quote").str()=="中😀");
    CHECK(s.evidence("a", sid, 0, 7).at("evidence_id").str()==e.at("evidence_id").str());
    CHECK(s.find("a", sid, "中").at("matches").size()==2);
  }
  TEST(search_budget_partial) {
    Temp t;
    Store s(t.path/"db");
    for(int i=0; i<3; ++i)s.ingest("a", text("https://x.example/"+std::to_string(i), "中文全文"));
    auto se=s.search("a", q("中文"), 1, 10);
    CHECK(se.at("partial").flag());
    CHECK(se.at("scanned_candidates").num()==1);
  }
  TEST(reopen_data) {
    Temp t;
    std::string sid;
    {
      Store s(t.path/"db");
      sid=s.ingest("a", text("https://x.example/a", "durable")).at("snapshot_id").str();
    }
    {
      Store s(t.path/"db");
      CHECK(s.snapshot("a", sid).at("canonical_text").str()=="durable");
      CHECK(s.search("a", q("durable")).at("frozen_count").num()==1);
    }
  }
  TEST(migrate_schema_one_preserves_records) {
    Temp t;
    auto path=t.path/"db";
    Json historical, current, noindex, evidence, page, stats, job;
    std::string session;
    {
      Store s(path);
      historical=s.ingest("alice", text("https://x.example/a", "legacy_before 中文", "neutral"));
      evidence=s.evidence("alice", historical.at("snapshot_id").str(), 0, 6);
      current=s.ingest("alice", text("https://x.example/a", "test中文 current", "neutral"));
      noindex=s.ingest("alice", {"https://x.example/noindex", "<head><meta name='robots' content='noindex'></head><body>private-canary</body>", "text/html", "neutral"});
      s.ingest("bob", text("https://x.example/b", "test other-owner", "neutral"));
      auto gone=s.ingest("alice", text("https://x.example/gone", "test deleted-canary", "neutral"));
      s.erase("alice", gone.at("document_id").str());
      session=s.search("alice", q("current")).at("session_id").str();
      page=s.page("alice", session, "", 10);
      job=s.job_create("alice", "developer", "migration-job");
      // Strip the ingest/create response-only reused field before comparison.
      historical=s.snapshot("alice", historical.at("snapshot_id").str());
      current=s.snapshot("alice", current.at("snapshot_id").str());
      noindex=s.snapshot("alice", noindex.at("snapshot_id").str());
      job=s.job_get("alice", job.at("job_id").str());
      stats=s.stats("alice");
    }
    restore_schema_one_index(path);
    CHECK(database_scalar(path, "PRAGMA user_version")==1);
    CHECK(database_scalar(path, "SELECT COUNT(*) FROM content_fts WHERE content_fts MATCH '\"test\"' AND owner='alice'")==0);
    {
      Store s(path);
      CHECK(s.search("alice", q("test")).at("frozen_count").num()==1);
      CHECK(s.search("bob", q("test")).at("frozen_count").num()==1);
      CHECK(s.search("alice", q("legacy_before OR deleted-canary OR private-canary")).at("frozen_count").num()==0);
      CHECK(s.snapshot("alice", historical.at("snapshot_id").str()).canonical()==historical.canonical());
      CHECK(s.snapshot("alice", current.at("snapshot_id").str()).canonical()==current.canonical());
      CHECK(s.snapshot("alice", noindex.at("snapshot_id").str()).canonical()==noindex.canonical());
      CHECK(s.evidence_get("alice", evidence.at("evidence_id").str()).canonical()==evidence.canonical());
      CHECK(s.page("alice", session, "", 10).canonical()==page.canonical());
      CHECK(s.job_get("alice", job.at("job_id").str()).canonical()==job.canonical());
      CHECK(s.stats("alice").canonical()==stats.canonical());
    }
    CHECK(database_scalar(path, "PRAGMA user_version")==14);
    CHECK(database_scalar(path, "SELECT COUNT(*) FROM content_fts")==2);
  }
  TEST(migration_rejects_quarantined_and_newer_databases) {
    Temp t;
    auto path=t.path/"db";
    {
      Store s(path);
      s.ingest("alice", text("https://x.example/a", "test中文", "neutral"));
    }
    restore_schema_one_index(path);
    database_exec(path, "UPDATE settings SET value='1' WHERE key='restore_quarantine';");
    rejects([&] {
      Store blocked(path);
    }, "E_RESTORE_QUARANTINED");
    CHECK(database_scalar(path, "PRAGMA user_version")==1);
    CHECK(database_scalar(path, "SELECT CAST(value AS INTEGER) FROM settings WHERE key='restore_quarantine'")==1);

    auto future=t.path/"future";
    {
      Store s(future);
      s.ingest("alice", text("https://x.example/a", "future-canary", "neutral"));
    }
    database_exec(future, "PRAGMA user_version=15;");
    rejects([&] {
      Store blocked(future);
    }, "E_SCHEMA_VERSION");
    CHECK(database_scalar(future, "PRAGMA user_version")==15);
    CHECK(database_scalar(future, "SELECT COUNT(*) FROM snapshots")==1);
  }
  TEST(job_idempotency) {
    Temp t;
    Store s(t.path/"db");
    auto a=s.job_create("a", "developer", "key");
    auto b=s.job_create("a", "developer", "key");
    CHECK(a.at("job_id").str()==b.at("job_id").str());
    CHECK(b.at("reused").flag());
    rejects([&] {
      s.job_create("a", "other", "key");
    }, "E_IDEMPOTENCY_CONFLICT");
    CHECK(s.job_create("b", "developer", "key").at("job_id").str()!=a.at("job_id").str());
  }
  TEST(job_controls) {
    Temp t;
    Store s(t.path/"db");
    auto id=s.job_create("a", "developer", "key").at("job_id").str();
    CHECK(s.job_control("a", id, "pause").at("state").str()=="paused");
    CHECK(!s.lease_next());
    CHECK(s.job_control("a", id, "resume").at("state").str()=="queued");
    CHECK(s.job_control("a", id, "cancel").at("state").str()=="cancelled");
    CHECK(!s.lease_next());
    rejects([&] {
      s.job_control("a", id, "resume");
    }, "E_JOB_STATE");
  }
  TEST(job_lease_fencing) {
    Temp t;
    std::int64_t now=10000;
    Store s(t.path/"db", [&] {
      return now;
    });
    auto id=s.job_create("a", "developer", "key").at("job_id").str();
    auto old=s.lease_next(100);
    CHECK(old.has_value());
    now+=101;
    auto fresh=s.lease_next(100);
    CHECK(fresh&&fresh->epoch>old->epoch);
    auto doc=text("https://x.example/a", "fencing");
    auto parsed=parse_document(doc.raw, doc.content_type, doc.title);
    CHECK(!s.complete(*old, doc, parsed));
    CHECK(s.stats("a").at("documents").num()==0);
    CHECK(s.complete(*fresh, doc, parsed));
    CHECK(!s.complete(*fresh, doc, parsed));
    CHECK(s.stats("a").at("snapshots").num()==1);
    CHECK(s.job_get("a", id).at("attempts").num()==2);
    CHECK(s.job_get("a", id).at("used_pages").num()==1);
  }
  TEST(job_pause_fencing) {
    Temp t;
    std::int64_t now=10000;
    Store s(t.path/"db", [&] {
      return now;
    });
    auto id=s.job_create("a", "developer", "key").at("job_id").str();
    auto lease=s.lease_next(100);
    s.job_control("a", id, "pause");
    auto doc=text("https://x.example/a", "shouldnotpublish");
    CHECK(!s.complete(*lease, doc, parse_document(doc.raw, doc.content_type)));
    now+=101;
    s.settle_interruptions();
    CHECK(s.job_get("a", id).at("state").str()=="paused");
    CHECK(s.stats("a").at("documents").num()==0);
  }
  TEST(job_budgets) {
    Temp t;
    std::int64_t now=10000;
    Store s(t.path/"db", [&] {
      return now;
    });
    auto id=s.job_create("a", "developer", "k", 1, 1000).at("job_id").str();
    s.lease_next(100);
    now+=101;
    CHECK(!s.lease_next());
    CHECK(s.job_get("a", id).at("error_code").str()=="E_ATTEMPT_BUDGET");
    auto ex=s.job_create("a", "developer", "e", 3, 100).at("job_id").str();
    now+=101;
    CHECK(!s.lease_next());
    CHECK(s.job_get("a", ex).at("error_code").str()=="E_DEADLINE");
  }
  TEST(job_target_tombstone) {
    Temp t;
    std::int64_t now=10000;
    Store s(t.path/"db", [&] {
      return now;
    });
    auto d=s.ingest("a", text("https://x.example/a", "delete me"));
    now+=10;
    auto id=s.job_create("a", "developer", "key").at("job_id").str();
    auto l=s.lease_next();
    now+=10;
    s.erase("a", d.at("document_id").str());
    auto doc=text("https://x.example/a", "do not resurrect");
    CHECK(!s.complete(*l, doc, parse_document(doc.raw, doc.content_type)));
    CHECK(s.job_get("a", id).at("error_code").str()=="E_TARGET_DELETED");
    CHECK(s.stats("a").at("documents").num()==0);
  }
  TEST(job_restart) {
    Temp t;
    std::int64_t now=10000;
    std::string id;
    std::int64_t epoch;
    {
      Store s(t.path/"db", [&] {
        return now;
      });
      id=s.job_create("a", "developer", "key").at("job_id").str();
      epoch=s.lease_next(100)->epoch;
    }
    now+=101;
    {
      Store s(t.path/"db", [&] {
        return now;
      });
      auto l=s.lease_next();
      CHECK(l&&l->id==id&&l->epoch>epoch);
    }
  }
  TEST(job_concurrent_claim) {
    Temp t;
    Store a(t.path/"db"), b(t.path/"db");
    a.job_create("a", "developer", "key");
    std::atomic<int>leases {
      0
    }, errors {
      0
    };
    auto work=[&](Store&s) {
      try {
        if(s.lease_next())++leases;
      }catch(...) {
        ++errors;
      }
    };
    std::thread x(work, std::ref(a)), y(work, std::ref(b));
    x.join();
    y.join();
    CHECK(leases==1);
    CHECK(errors==0);
  }
  TEST(backup_quarantine) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://x.example/a", "backup canary"));
    auto path=t.path/"backup.sqlite";
    auto report=s.backup(path);
    CHECK(report.at("restore_state").str()=="quarantined");
    CHECK(report.at("contains_private_content").flag());
    for(const auto& entry:std::filesystem::directory_iterator(t.path)) {
      CHECK(entry.path().filename().string().find(".partial-")==std::string::npos);
    }
    CHECK(std::filesystem::file_size(path)>0);
    rejects([&] {
      Store blocked(path);
    }, "E_RESTORE_QUARANTINED");
    rejects([&] {
      s.backup(path);
    }, "E_BACKUP_EXISTS");
  }
  TEST(fixture_source_boundary) {
    FixtureCatalog f(BZS_FIXTURE_DIR);
    CHECK(f.list().at("kind").str()=="fixture");
    CHECK(!f.list().at("is_live_web_source").flag());
    auto hits=f.search(q("C++")).at("hits");
    CHECK(hits.size()==1);
    CHECK(!hits.at(0).at("body_read").flag());
    rejects([&] {
      f.get("missing");
    }, "E_NOT_FOUND");
  }
  TEST(service_capabilities) {
    Temp t;
    Store s(t.path/"db");
    FixtureCatalog f(BZS_FIXTURE_DIR);
    Service svc(s, f, t.path/"backup");
    auto p=Json::object();
    p.set("owner", "someone_else");
    rejects([&] {
      svc.dispatch( {
        "a", false
      }, req("stats", p));
    }, "E_SCHEMA");
    rejects([&] {
      svc.dispatch( {
        "a", false
      }, req("web.search", Json::object()));
    }, "E_CAPABILITY_UNAVAILABLE");
    CHECK(svc.capabilities().at("live_web_sources").num()==0);
    CHECK(!svc.capabilities().at("production_ready").flag());
  }
  TEST(admin_backup) {
    Temp t;
    Store s(t.path/"db");
    FixtureCatalog f(BZS_FIXTURE_DIR);
    Service svc(s, f, t.path/"backup");
    rejects([&] {
      svc.dispatch( {
        "a", false
      }, req("backup.create", Json::object()));
    }, "E_FORBIDDEN");
  }
  TEST(auth_validation) {
    Temp t;
    auto p=t.path/"auth.json";
    std::string token(64, 'a');
    {
      std::ofstream f(p);
      f<<"{\"tokens\":[{\"sha256\":\""<<sha256(token)<<"\",\"subject\":\"alice\",\"admin\":true}]}";
    }
    std::filesystem::permissions(p, std::filesystem::perms::owner_read|std::filesystem::perms::owner_write);
    Auth auth(p);
    CHECK(auth.authenticate("Bearer "+token).subject=="alice");
    rejects([&] {
      auth.authenticate("Bearer "+std::string(64, 'b'));
    }, "E_AUTH");
    rejects([&] {
      auth.authenticate("");
    }, "E_AUTH");
  }

  // ---------------------------------------------------------------- S1 additions
  std::vector<std::string> toks(Store& s, const std::string& text, bool query) {
    return debug_tokenize(s.raw_handle(), text, query);
  }
  TEST(tokenizer_cjk_and_symbols) {
    Temp t;
    Store s(t.path/"db");
    CHECK((toks(s, "搜索引擎", false)==std::vector<std::string> {"搜", "+搜索", "索", "+索引", "引", "+引擎", "擎"}));
    CHECK((toks(s, "搜索引擎", true)==std::vector<std::string> {"搜索", "索引", "引擎", "擎"}));
    CHECK((toks(s, "爬", true)==std::vector<std::string> {"爬"}));
    CHECK((toks(s, "C++20 rocks", false)==std::vector<std::string> {"c", "+c++", "20", "rocks"}));
    CHECK((toks(s, "C++", true)==std::vector<std::string> {"c++"}));
    CHECK((toks(s, "C# guide", true)==std::vector<std::string> {"c#", "guide"}));
    CHECK((toks(s, "ABC中文def", false)==std::vector<std::string> {"abc", "中", "+中文", "文", "def"}));
  }
  TEST(fts_chinese_uses_index) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://x.example/zh", "这是一个搜索引擎的开发文档，介绍断点续爬。", "中文资料"));
    s.ingest("a", text("https://x.example/en", "An English page about engines.", "English"));
    for(auto query: {"引擎", "擎", "断点续爬", "搜索引擎 开发", "\"开发文档\""}) {
      auto r=s.search("a", q(query));
      CHECK(r.at("retrieval").str()=="fts5_bm25_verified");
      CHECK(r.at("frozen_count").num()==1);
    }
    CHECK(s.search("a", q("续爬虫")).at("frozen_count").num()==0);
    CHECK(s.search("a", q("开发者")).at("frozen_count").num()==0);
  }
  TEST(bm25_ranks_relevance) {
    Temp t;
    Store s(t.path/"db");
    std::string dense;
    for(int i=0; i<40; ++i)dense+="sqlite fts5 sqlite index sqlite ";
    s.ingest("a", text("https://c.example/mention-1", "A long cooking blog that mentions sqlite once among recipes and travel."));
    s.ingest("a", text("https://c.example/relevant", dense, "SQLite FTS5 guide"));
    s.ingest("a", text("https://c.example/mention-2", "Travel notes. My laptop runs sqlite somewhere. Anyway, Paris is lovely."));
    auto r=s.search("a", q("sqlite"));
    auto hits=s.page("a", r.at("session_id").str(), "").at("hits");
    CHECK(hits.size()==3);
    CHECK(hits.at(0).at("url").str()=="https://c.example/relevant");
  }
  TEST(literal_exactness) {
    CHECK(!matches(q("v1.2"), "", "release v1.2.3 notes", "", "", ""));
    CHECK(matches(q("v1.2"), "", "release v1.2, then v1.3", "", "", ""));
    CHECK(matches(q("C++"), "", "modern C++20 coroutines", "", "", ""));
    CHECK(!matches(q("C++"), "", "plain C and C# only", "", "", ""));
    CHECK(!matches(q("std::expected"), "", "use std::expected_or here", "", "", ""));
    CHECK(matches(q("std::expected"), "", "std::expected<int, E>", "", "", ""));
    CHECK(matches(q("C#"), "", "C#12 features", "", "", ""));
    CHECK(matches(q("\"sqlite fts\""), "", "sqlite fts5 tables", "", "", ""));
  }
  TEST(compound_matching_keeps_fields_and_documents_independent) {
    const auto query=parse_query(Json::parse(R"({"op":"or","children":[{"op":"and","children":[{"op":"phrase","field":"title","value":"alpha beta"},{"op":"literal","field":"body","value":"C++"},{"op":"not","child":{"op":"term","field":"body","value":"java"}}]},{"op":"and","children":[{"op":"phrase","field":"body","value":"café Москва"},{"op":"literal","field":"url","value":"node.js"}]}]})"));
    struct Document {std::string title,body,url;bool expected;};
    const std::vector<Document> documents={
      {"ALPHA BETA guide","Modern C++20 library","https://example.test/plain",true},
      {"ALPHA BETA guide","Modern C++20 JAVA bridge","https://example.test/plain",false},
      {"neutral","alpha beta C++","https://example.test/plain",false},
      {"alpha beta C++","neutral","https://example.test/plain",false},
      {"neutral","A café Москва guide","https://example.test/NODE.JS",true},
      {"café Москва","neutral","https://example.test/node.js",false},
      {"neutral","A CAFÉ МОСКВА guide","https://example.test/node.js",false},
      {"neutral","A café Москва guide","https://example.test/node.jsx",false}
    };
    // Repeat in both directions: no field/document may inherit a previous
    // leaf's lowered text, and non-ASCII case folding remains unsupported.
    for(int pass=0;pass<2;++pass)for(std::size_t i=0;i<documents.size();++i) {
      const auto& doc=documents[pass==0?i:documents.size()-1-i];
      CHECK(matches(query,doc.title,doc.body,doc.url,"example.test","text/plain")==doc.expected);
    }
  }
  TEST(html_main_excludes_siblings) {
    auto p=parse_document("<html><body><main><p>MAIN_CONTENT</p></main><aside>SIDEBAR</aside><div>COMMENTS</div></body></html>", "text/html");
    CHECK(p.text=="MAIN_CONTENT");
    auto a=parse_document("<html><body><article><p>ARTICLE</p><aside>NOTE</aside></article><div>FOOT</div></body></html>", "text/html");
    CHECK(a.text=="ARTICLE");
  }
  TEST(delete_keeps_unrelated_jobs_and_sessions) {
    Temp t;
    Store s(t.path/"db");
    s.ingest("a", text("https://a.example/keep", "sqlite keep"));
    auto junk=s.ingest("a", text("https://a.example/junk", "sqlite junk"));
    auto sess=s.search("a", q("sqlite")).at("session_id").str();
    std::vector<std::string> jobs;
    for(int i=0; i<3; ++i)jobs.push_back(s.job_create("a", "developer", "page-"+std::to_string(i)).at("job_id").str());
    s.erase("a", junk.at("document_id").str());
    auto lease=s.lease_next();
    CHECK(lease.has_value());
    for(const auto& id:jobs)CHECK(s.job_get("a", id).at("error_code").is_null());
    auto page=s.page("a", sess, "", 10).at("hits");
    CHECK(page.size()==1);
    CHECK(page.at(0).at("url").str()=="https://a.example/keep");
  }
  TEST(highlights_are_query_relevant_and_exact) {
    Temp t;
    Store s(t.path/"db");
    std::string filler;
    for(int i=0; i<60; ++i)filler+="filler sentence number "+std::to_string(i)+".\n";
    auto body=filler+"The answer: resumable crawling is supported via checkpoints.\nMore filler.";
    auto snap=s.ingest("a", text("https://b.example/doc", body));
    auto r=s.search("a", q("resumable checkpoints"));
    auto hit=s.page("a", r.at("session_id").str(), "").at("hits").at(0);
    CHECK(hit.at("snippet_kind").str()=="query_highlights");
    CHECK(hit.at("snippet").str().find("resumable crawling")!=std::string::npos);
    auto h=hit.at("highlights").at(0);
    auto canonical=s.snapshot("a", snap.at("snapshot_id").str()).at("canonical_text").str();
    auto start=static_cast<std::size_t>(h.at("start_byte").num()), end=static_cast<std::size_t>(h.at("end_byte").num());
    CHECK(canonical.substr(start, end-start)==h.at("text").str());
    CHECK(s.evidence("a", snap.at("snapshot_id").str(), h.at("start_byte").num(), h.at("end_byte").num()).at("quote").str()==h.at("text").str());
    auto zh=highlight("第一段没有关键词。\n第二段讲断点续爬的实现细节。\n第三段无关。", q("断点续爬").root);
    CHECK(zh.at("highlights").size()==1);
    CHECK(zh.at("highlights").at(0).at("text").str()=="第二段讲断点续爬的实现细节。");
  }
  TEST(revisions_pruned_and_quota_counters) {
    Temp t;
    StoreOptions o;
    o.max_revisions_per_document=3;
    o.max_snapshots_per_owner=500;
    Store s(t.path/"db", {}, o);
    for(int rev=0; rev<20; ++rev)
      for(int u=0; u<50; ++u)s.ingest("a", text("https://e.example/p"+std::to_string(u), "revision "+std::to_string(rev)));
    auto st=s.stats("a");
    CHECK(st.at("documents").num()==50);
    CHECK(st.at("snapshots").num()==150);
    // Evidence pins an old revision so it survives pruning.
    Temp t2;
    Store p(t2.path/"db", {}, o);
    auto first=p.ingest("a", text("https://e.example/pinned", "original wording"));
    p.evidence("a", first.at("snapshot_id").str(), 0, 8);
    for(int rev=0; rev<6; ++rev)p.ingest("a", text("https://e.example/pinned", "changed "+std::to_string(rev)));
    CHECK(p.snapshot("a", first.at("snapshot_id").str()).at("canonical_text").str()=="original wording");
    StoreOptions tiny;
    tiny.max_snapshots_per_owner=2;
    Temp t3;
    Store q3(t3.path/"db", {}, tiny);
    q3.ingest("a", text("https://e.example/1", "one"));
    q3.ingest("a", text("https://e.example/2", "two"));
    rejects([&] {
      q3.ingest("a", text("https://e.example/3", "three"));
    }, "E_QUOTA");
  }
  TEST(range_windows_are_utf8_safe) {
    Temp t;
    Store s(t.path/"db");
    auto snap=s.ingest("a", text("https://x.example/r", "中文abc中文"));
    auto id=snap.at("snapshot_id").str();
    auto w=s.range("a", id, 0, 4);
    CHECK(w.at("text").str()=="中");
    CHECK(w.at("next_start_byte").num()==3);
    auto rest=s.range("a", id, 3, 100);
    CHECK(rest.at("text").str()=="文abc中文");
    CHECK(!rest.at("truncated").flag());
    rejects([&] {
      s.range("a", id, 1, 10);
    }, "E_EVIDENCE_RANGE");
  }
  TEST(migration_from_v1) {
    Temp t;
    auto path=t.path/"db";
    {
      sqlite3* raw=nullptr;
      sqlite3_open(path.string().c_str(), &raw);
      auto ok=sqlite3_exec(raw, R"SQL(
 CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 INSERT INTO settings VALUES('restore_quarantine','0');
 CREATE TABLE subjects(owner TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE documents(id TEXT PRIMARY KEY,owner TEXT NOT NULL,url TEXT NOT NULL,host TEXT NOT NULL,
     current_snapshot TEXT,revision INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,UNIQUE(owner,url));
 CREATE TABLE snapshots(id TEXT PRIMARY KEY,extraction_id TEXT UNIQUE NOT NULL,document_id TEXT NOT NULL REFERENCES documents(id),
     revision INTEGER NOT NULL,title TEXT NOT NULL,canonical_text TEXT NOT NULL,raw_content TEXT NOT NULL,raw_hash TEXT NOT NULL,
     content_type TEXT NOT NULL,parser_version TEXT NOT NULL,created_at INTEGER NOT NULL,index_allowed INTEGER NOT NULL,
     links_json TEXT NOT NULL,UNIQUE(document_id,revision));
 CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='unicode61');
 CREATE TABLE evidence(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
     start_byte INTEGER NOT NULL,end_byte INTEGER NOT NULL,UNIQUE(snapshot_id,start_byte,end_byte));
 CREATE TABLE sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,auth_epoch INTEGER NOT NULL,query_json TEXT NOT NULL,
     created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,scanned INTEGER NOT NULL,partial INTEGER NOT NULL,retrieval TEXT NOT NULL);
 CREATE TABLE session_rows(session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,position INTEGER NOT NULL,
     document_id TEXT NOT NULL,snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,score INTEGER NOT NULL,PRIMARY KEY(session_id,position));
 CREATE TABLE cursors(token TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,position INTEGER NOT NULL,UNIQUE(session_id,position));
 CREATE TABLE jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,fixture_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,fingerprint TEXT NOT NULL,
     state TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,auth_epoch INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL,
     lease_until INTEGER NOT NULL DEFAULT 0,deadline INTEGER NOT NULL,created_at INTEGER NOT NULL,used_pages INTEGER NOT NULL DEFAULT 0,
     result_json TEXT,error_code TEXT,UNIQUE(owner,idempotency_key));
 INSERT INTO subjects VALUES('a',0);
 INSERT INTO documents VALUES('d1','a','https://old.example/zh','old.example','s1',1,0);
 INSERT INTO snapshots VALUES('s1','e1','d1',1,'旧资料','旧版本的搜索引擎文档','raw','h','text/plain','bootstrap-text/1',1,1,'[]');
 INSERT INTO content_fts VALUES('d1','a','旧资料','旧版本的搜索引擎文档');
 PRAGMA user_version=1;
 )SQL", nullptr, nullptr, nullptr);
      sqlite3_close(raw);
      CHECK(ok==SQLITE_OK);
    }
    Store s(path);
    auto r=s.search("a", q("引擎"));
    CHECK(r.at("retrieval").str()=="fts5_bm25_verified");
    CHECK(r.at("frozen_count").num()==1);
    CHECK(s.stats("a").at("snapshots").num()==1);
    CHECK(s.snapshot("a", "s1").at("source_kind").str()=="import");
  }
  TEST(dedup_and_rrf_fusion) {
    CHECK(dedup_key("https://www.Example.com/a/?utm_source=x&b=2&a=1#frag")==dedup_key("http://example.com:80/a?a=1&b=2"));
    CHECK(dedup_key("https://example.com/a")!=dedup_key("https://example.com/b"));
    CHECK(dedup_key("ftp://example.com/").empty());
    auto manifest=Json::parse(R"({"version":1,"sources":[
      {"id":"bing","provider":"microsoft","index_family":"bing","capability":"web","kind":"scrape","enabled":true,"default":true},
      {"id":"yahoo","provider":"yahoo","index_family":"bing","capability":"web","kind":"scrape","enabled":true},
      {"id":"ddg","provider":"duckduckgo","index_family":"ddg","capability":"web","kind":"scrape","enabled":true,"default":true}]})");
    SourceRegistry reg(reviewed_sources(manifest));
    std::vector<SourceHits> in= {
      {"bing", {{"https://a.example/", "A", "from bing", 1}, {"https://b.example/", "B", "", 2}}},
      {"yahoo", {{"http://a.example", "A", "", 1}, {"https://b.example/", "B", "", 1}}},
      {"ddg", {{"https://b.example/", "B", "from ddg", 5}, {"https://c.example/", "C", "", 1}}}};
    auto out=fuse(in, reg, 10);
    CHECK(out.size()==3);
    // b.example appears in two families (bing family counted once, ddg) and wins.
    CHECK(out.at(0).at("url").str()=="https://b.example/");
    CHECK(out.at(0).at("index_families").num()==2);
    CHECK(out.at(0).at("snippet").str()=="from ddg");
    CHECK(out.at(1).at("url").str()=="https://a.example/");
    CHECK(out.at(1).at("sources").size()==2);
  }
  TEST(public_url_policy) {
    for(auto u: {"http://localhost/", "http://127.0.0.1/", "http://10.1.2.3/", "http://169.254.169.254/latest/meta-data",
                 "http://192.168.1.1/", "http://2130706433/", "http://0x7f000001/", "http://1.2.3/", "http://printer.local/", "http://012.0.0.1/"})
      rejects([&] {
        check_public_url(u);
      }, "E_URL_BLOCKED");
    check_public_url("https://example.com/");
    check_public_url("http://8.8.8.8/");
    check_public_url("https://1e100.net/");
    rejects([&] {
      check_public_url("file:///etc/passwd");
    }, "E_URL");
  }
  struct FakeWorker final:WorkerPort {
    std::vector<std::pair<std::string, Json>> calls;
    std::function<Json(const std::string&, const Json&)> handler;
    bool fill_fetch_defaults=true;
    Json call(const std::string& m, const Json& p, int) override {
      calls.emplace_back(m, p);
      auto result=handler(m, p);
      if(fill_fetch_defaults&&m=="fetch.extract"&&result.has("status")&&
         result.at("status").is_string()&&result.at("status").str()=="ok") {
        if(!result.has("html"))result.set("html", "");
        if(!result.has("extractor"))result.set("extractor", "fakeweb/1");
        if(!result.has("final_url"))result.set("final_url", p.at("url"));
        if(!result.has("redirects"))result.set("redirects", Json::array());
      }
      return result;
    }
  };
  Json obj(const std::string& s) {
    return Json::parse(s);
  }
  SourceRegistry test_registry() {
    return SourceRegistry(reviewed_sources(obj(R"({"version":1,"sources":[
      {"id":"bing","provider":"microsoft","index_family":"bing","capability":"web","kind":"scrape","enabled":true,"default":true,"min_interval_ms":0},
      {"id":"ddg","provider":"duckduckgo","index_family":"ddg","capability":"web","kind":"scrape","enabled":true,"default":true,"min_interval_ms":0},
      {"id":"arxiv","provider":"arxiv","index_family":"arxiv","capability":"papers","kind":"api","enabled":true,"min_interval_ms":0},
      {"id":"off","provider":"x","index_family":"x","capability":"web","kind":"api","enabled":false}]})")));
  }
  TEST(web_search_merges_and_cools_down) {
    Temp t;
    Store s(t.path/"db");
    FakeWorker w;
    w.handler=[](const std::string& m, const Json& p) {
      CHECK(m=="search.multi");
      CHECK(p.at("sources").size()==2);
      return obj(R"({"results":{
        "bing":{"status":"ok","elapsed_ms":120,"hits":[{"url":"https://docs.example.com/x","title":"X","snippet":"x doc","rank":1},
                                                     {"url":"https://other.example/y","title":"Y","snippet":"","rank":2}]},
        "ddg":{"status":"blocked","error":"captcha page","hits":[]}}})");
    };
    WebService web(s, w, test_registry());
    auto r=web.search("a", obj(R"({"query":"guide site:example.com"})"));
    CHECK(r.at("partial").flag());
    CHECK(r.at("result_count").num()==1);
    CHECK(r.at("results").at(0).at("url").str()=="https://docs.example.com/x");
    CHECK(r.at("filters").at("removed_unverified").num()==1);
    CHECK(r.at("usage").at("external_requests").num()==2);
    // ddg is now cooling down and is skipped without a request.
    w.handler=[](const std::string&, const Json& p) {
      CHECK(p.at("sources").size()==1);
      return obj(R"({"results":{"bing":{"status":"ok","hits":[]}}})");
    };
    auto r2=web.search("a", obj(R"({"query":"guide"})"));
    bool skipped=false;
    for(std::size_t i=0; i<r2.at("sources").size(); ++i) {
      auto x=r2.at("sources").at(i);
      if(x.at("source_id").str()=="ddg")skipped=x.at("status").str()=="skipped"&&x.at("reason").str()=="cooldown";
    }
    CHECK(skipped);
    CHECK(web.sources().at("sources").size()==4);
    rejects([&] {
      web.search("a", obj(R"({"query":"x","sources":["off"]})"));
    }, "E_SCHEMA");
    w.handler=[](const std::string&, const Json& p) {
      CHECK(p.at("sources").at(0).str()=="arxiv");
      return obj(R"({"results":{"arxiv":{"status":"ok","hits":[{"url":"https://arxiv.org/abs/1","title":"Paper","snippet":"s","rank":1}]}}})");
    };
    CHECK(web.search("a", obj(R"({"query":"attention","category":"papers"})")).at("result_count").num()==1);
  }
  TEST(web_search_cache_spares_sources) {
    Temp t;
    std::int64_t now=1'000'000;
    Store s(t.path/"db", [&] {
      return now;
    });
    FakeWorker w;
    w.handler=[](const std::string&, const Json&) {
      return obj(R"({"results":{"bing":{"status":"ok","hits":[{"url":"https://a.example/1","title":"A","snippet":"","rank":1}]},
                                "ddg":{"status":"ok","hits":[]}}})");
    };
    WebService web(s, w, test_registry());
    auto first=web.search("a", obj(R"({"query":"cache me"})"));
    CHECK(!first.at("from_cache").flag());
    CHECK(w.calls.size()==1);
    auto second=web.search("a", obj(R"({"query":"cache me"})"));
    CHECK(second.at("from_cache").flag());
    CHECK(second.at("usage").at("external_requests").num()==0);
    CHECK(second.at("results").at(0).at("url").str()=="https://a.example/1");
    CHECK(w.calls.size()==1);
    // Different query / limit / explicit refresh all go upstream; the cache expires after 10 minutes.
    web.search("a", obj(R"({"query":"cache me","limit":5})"));
    web.search("a", obj(R"({"query":"cache me","refresh":true})"));
    CHECK(w.calls.size()==3);
    now+=10*60*1000+1;
    CHECK(!web.search("a", obj(R"({"query":"cache me"})")).at("from_cache").flag());
    CHECK(w.calls.size()==4);
    // A search where every source failed is not cached.
    w.handler=[](const std::string&, const Json&) {
      return obj(R"({"results":{"bing":{"status":"timeout","hits":[]},"ddg":{"status":"network_error","hits":[]}}})");
    };
    web.search("a", obj(R"({"query":"all fail","sources":["bing"]})"));
    now+=11*60*1000;  // past any cooldown
    web.search("a", obj(R"({"query":"all fail","sources":["bing"]})"));
    CHECK(w.calls.size()==6);
  }
  TEST(iri_urls_are_encoded_for_reading) {
    CHECK(normalize_iri("https://zh.wikipedia.org/wiki/网络爬虫")=="https://zh.wikipedia.org/wiki/%E7%BD%91%E7%BB%9C%E7%88%AC%E8%99%AB");
    CHECK(normalize_iri("https://x.example/a b?q=中文#片段")=="https://x.example/a%20b?q=%E4%B8%AD%E6%96%87#%E7%89%87%E6%AE%B5");
    CHECK(normalize_iri("https://x.example/already%20encoded")=="https://x.example/already%20encoded");
    CHECK(normalize_iri("https://x.example")=="https://x.example");
    rejects([] {
      normalize_iri("https://例子.测试/");
    }, "E_URL");
    rejects([] {
      normalize_iri("ftp-less-url");
    }, "E_URL");
  }
  TEST(web_read_ingests_and_caches) {
    Temp t;
    std::int64_t now=1000000;
    Store s(t.path/"db", [&] {
      return now;
    });
    FakeWorker w;
    int fetches=0;
    w.handler=[&](const std::string& m, const Json& p) {
      CHECK(m=="fetch.extract");
      ++fetches;
      auto url=p.at("url").str();
      if(url.find("challenge")!=std::string::npos)return obj(R"({"status":"challenge","http_status":403,"error":"bot check"})");
      return obj(R"({"status":"ok","http_status":200,"final_url":"https://site.example/final","content_type":"text/html",
        "html":"<html><body><main>中文正文 resumable crawling</main></body></html>","title":"Page",
        "text":"# Page\n\n中文正文 resumable crawling","links":["https://site.example/next"],"extractor":"trafilatura/2",
        "extract_status":"ok","index_allowed":true,"metadata":{"sitename":"Site"}})");
    };
    WebService web(s, w, test_registry());
    auto r=web.read("a", obj(R"({"url":"https://site.example/start"})"));
    CHECK(!r.at("from_cache").flag());
    CHECK(r.at("final_url").str()=="https://site.example/final");
    CHECK(r.at("content").at("text").str().find("中文正文")!=std::string::npos);
    CHECK(r.at("content_trust").str()=="untrusted_web_content");
    auto again=web.read("a", obj(R"({"url":"https://site.example/start"})"));
    CHECK(!again.at("from_cache").flag());
    CHECK(fetches==2);
    now+=8LL*86400*1000;
    web.read("a", obj(R"({"url":"https://site.example/start"})"));
    CHECK(fetches==3);
    auto local=s.search("a", q("中文正文"));
    CHECK(local.at("frozen_count").num()==1);
    CHECK(s.snapshot("a", r.at("snapshot_id").str()).at("content_origin").str()=="web_fetch_untrusted");
    rejects([&] {
      web.read("a", obj(R"({"url":"https://never.example/","freshness":"cache_only"})"));
    }, "E_NOT_CACHED");
    rejects([&] {
      web.read("a", obj(R"({"url":"https://site.example/challenge"})"));
    }, "E_ACCESS_RESTRICTED");
    rejects([&] {
      web.read("a", obj(R"({"url":"http://169.254.169.254/latest/meta-data"})"));
    }, "E_URL_BLOCKED");
    CHECK(fetches==4);
  }
  TEST(source_admission_limits) {
    Temp t;
    std::int64_t now=5000000;
    Store s(t.path/"db", [&] {
      return now;
    });
    CHECK(s.source_admit("x", 1000, 2)=="");
    CHECK(s.source_admit("x", 1000, 2)=="min_interval");
    now+=1001;
    CHECK(s.source_admit("x", 1000, 2)=="");
    now+=1001;
    CHECK(s.source_admit("x", 1000, 2)=="daily_cap");
    now+=86400000;
    CHECK(s.source_admit("x", 1000, 2)=="");
    s.source_report("x", "rate_limited", "429", 60000);
    now+=2000;
    CHECK(s.source_admit("x", 1000, 2)=="cooldown");
    now+=60000;
    CHECK(s.source_admit("x", 1000, 2)=="");
  }
  TEST(source_retry_after_is_bounded_persistent_and_saturating) {
    Temp t;
    std::int64_t now=5000000;
    {
      Store store(t.path/"db",[&]{return now;});
      CHECK(store.source_admit("shared",0,50).empty());
      store.source_report("shared","rate_limited","429",std::numeric_limits<std::int64_t>::max());
      const auto state=store.source_states().at("shared");
      CHECK(state.at("cooldown_remaining_ms").num()==86400000);
      CHECK(state.at("last_error").str().starts_with("E_RETRY_AFTER_CLAMPED:"));
      CHECK(state.at("consecutive_failures").num()==1);
      CHECK(state.at("requests_today").num()==1);
      store.source_report("shared","ok","",0);
      CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==86400000);
    }
    now+=86400000-1;
    {
      Store store(t.path/"db",[&]{return now;});
      CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==1);
      CHECK(store.source_admit("shared",0,50)=="cooldown");
      ++now;
      CHECK(store.source_admit("shared",0,50).empty());
    }
    now=std::numeric_limits<std::int64_t>::max()-1000;
    Store overflow(t.path/"overflow",[&]{return now;});
    CHECK(overflow.source_admit("fixture",0,50).empty());
    overflow.source_report("fixture","rate_limited","429",std::numeric_limits<std::int64_t>::max());
    CHECK(overflow.source_states().at("fixture").at("cooldown_remaining_ms").num()==1000);
    now+=1000;
    CHECK(overflow.source_states().at("fixture").at("cooldown_remaining_ms").num()==0);
    CHECK(overflow.source_admit("fixture",0,50).empty());
  }
  TEST(source_retry_after_does_not_shorten_prior_parse_error_or_ordinary_waits) {
    Temp t;
    std::int64_t now=5000000;
    Store store(t.path/"db",[&]{return now;});
    CHECK(store.source_admit("shared",0,50).empty());
    store.source_report("shared","parse_error","parse",3LL*86400000);
    store.source_report("shared","rate_limited","429",std::numeric_limits<std::int64_t>::max());
    CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==3LL*86400000);
    store.source_report("shared","ok","",0);
    CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==3LL*86400000);
    now+=86400000;
    CHECK(store.source_admit("shared",0,50)=="cooldown");
    CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==2LL*86400000);
    CHECK(store.source_admit("ordinary",0,50).empty());
    store.source_report("ordinary","rate_limited","429",120000);
    CHECK(store.source_states().at("ordinary").at("cooldown_remaining_ms").num()==120000);
    store.source_report("ordinary","rate_limited","429",-1);
    CHECK(store.source_states().at("ordinary").at("cooldown_remaining_ms").num()==120000);
  }
  TEST(legacy_source_cooldown_repair_has_a_fixed_anchor_and_keeps_accounting) {
    Temp t;
    std::int64_t now=5000000;
    const auto path=t.path/"db";
    {
      Store store(path,[&]{return now;});
      for(const auto& id:{"rate","late","parse","ambiguous"}) {
        CHECK(store.source_admit(id,0,50).empty());
        store.source_report(id,"rate_limited","429",60000);
      }
      store.source_report("late","ok","",0);
      store.source_report("parse","parse_error","parse",3LL*86400000);
      store.source_report("no-attempt","rate_limited","429",60000);
    }
    // On-disk shape and status combinations produced by the old unbounded
    // code, including a late successful response and a missing attempt stamp.
    database_exec(path,"UPDATE source_state SET cooldown_until=999999999000 WHERE source_id IN ('rate','late','no-attempt');"
                       "UPDATE source_state SET cooldown_until=177800000 WHERE source_id='ambiguous';");
    now+=3600000;
    {
      Store store(path,[&]{return now;});
      CHECK(store.source_admit("rate",0,50)=="cooldown");
      const auto states=store.source_states();
      CHECK(states.at("rate").at("cooldown_remaining_ms").num()==86400000-3600000);
      CHECK(states.at("late").at("cooldown_remaining_ms").num()==3LL*86400000-3600000);
      CHECK(states.at("parse").at("cooldown_remaining_ms").num()==3LL*86400000-3600000);
      CHECK(states.at("ambiguous").at("cooldown_remaining_ms").num()==2LL*86400000-3600000);
      CHECK(states.at("no-attempt").at("cooldown_remaining_ms").num()==86400000);
      CHECK(states.at("rate").at("last_error").str().starts_with("E_SOURCE_COOLDOWN_CLAMPED:"));
      CHECK(states.at("rate").at("consecutive_failures").num()==1);
      CHECK(states.at("rate").at("requests_today").num()==1);
      CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='rate'")==5000000+86400000);
      CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='no-attempt'")==now+86400000);
    }
    now+=3600000;
    {
      Store store(path,[&]{return now;});
      const auto states=store.source_states();
      CHECK(states.at("rate").at("cooldown_remaining_ms").num()==86400000-2*3600000);
      CHECK(states.at("no-attempt").at("cooldown_remaining_ms").num()==86400000-3600000);
      CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='rate'")==5000000+86400000);
    }
    now=5000000+86400000;
    Store store(path,[&]{return now;});
    CHECK(store.source_admit("rate",0,50).empty());
    CHECK(store.source_admit("late",0,50)=="cooldown");
  }
  TEST(source_cooldown_proof_preserves_legal_deadlines_across_rollback_and_restart) {
    constexpr std::int64_t started=1800000000000;
    constexpr std::int64_t day=86400000;
    for(const std::int64_t delay:{0,60000})for(const auto& late:{"none","rate_limited","ok"}) {
      Temp t;
      std::int64_t now=started;
      const auto path=t.path/"db";
      const auto deadline=started+delay+3*day;
      {
        Store store(path,[&]{return now;});
        CHECK(store.source_admit("shared",0,50).empty());
        now+=delay;
        store.source_report("shared","parse_error","parse",3*day);
        if(std::string(late)!="none")store.source_report("shared",late,"late",120000);
        // With delayed report this rollback is still AFTER admission. Attempt
        // time alone cannot distinguish this legitimate 72h absolute deadline.
        now=started+(delay?30000:-3600000);
        CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==deadline-now);
        CHECK(store.source_admit("shared",0,50)=="cooldown");
        store.source_report("shared","ok","another late success",0);
        CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='shared'")==deadline);
      }
      {
        Store store(path,[&]{return now;});
        CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==deadline-now);
        // A large rollback still cannot revoke an already proven deadline.
        now=started-2*365*day;
        CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==deadline-now);
        CHECK(store.source_admit("shared",0,50)=="cooldown");
        store.source_report("shared","rate_limited","late429",std::numeric_limits<std::int64_t>::max());
        CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='shared'")==deadline);
        now=deadline-1;
        CHECK(store.source_admit("shared",0,50)=="cooldown");
        ++now;
        CHECK(store.source_admit("shared",0,50).empty());
      }
    }
  }
  TEST(legacy_source_cooldown_repair_retains_ambiguous_and_future_event_state) {
    Temp t;
    constexpr std::int64_t started=1800000000000;
    constexpr std::int64_t day=86400000;
    std::int64_t now=started;
    const auto path=t.path/"db";
    {
      Store store(path,[&]{return now;});
      for(const auto& id:{"delayed-parse","ambiguous","future","late-ok"})CHECK(store.source_admit(id,0,50).empty());
    }
    // Unproven rows have the old schema's missing response time. These are
    // deliberately not certified by a new bounded source_report call.
    database_exec(path,"UPDATE source_state SET last_status='parse_error',cooldown_until=1800259260000 WHERE source_id='delayed-parse';"
                       "UPDATE source_state SET last_status='rate_limited',cooldown_until=1815552000000 WHERE source_id='ambiguous';"
                       "UPDATE source_state SET last_status='rate_limited',last_request_at=1800000060000,cooldown_until=2799999999000 WHERE source_id='future';"
                       "UPDATE source_state SET last_status='ok',last_ok_at=1800000020000,cooldown_until=2799999999000 WHERE source_id='late-ok';");
    now=started+30000;
    {
      Store store(path,[&]{return now;});
      const auto states=store.source_states();
      CHECK(states.at("delayed-parse").at("cooldown_remaining_ms").num()==3*day+30000);
      CHECK(states.at("ambiguous").at("cooldown_remaining_ms").num()==180*day-30000);
      CHECK(states.at("future").at("cooldown_remaining_ms").num()==2799999999000LL-now);
      CHECK(!states.at("future").has("cooldown_last_repair"));
      CHECK(states.at("late-ok").at("cooldown_remaining_ms").num()==3*day-10000);
      const auto audit=states.at("late-ok").at("cooldown_last_repair");
      CHECK(audit.at("original_until_ms").num()==2799999999000LL);
      CHECK(audit.at("repaired_until_ms").num()==started+20000+3*day);
      CHECK(audit.at("anchor_ms").num()==started+20000);
      CHECK(audit.at("reason").str()=="unproven_legacy_backoff_over_one_year");
      CHECK(database_scalar(path,"PRAGMA user_version")==14);
    }
    now=started+61000;
    Store store(path,[&]{return now;});
    const auto states=store.source_states();
    CHECK(states.at("future").at("cooldown_remaining_ms").num()==day-1000);
    CHECK(states.at("future").at("cooldown_last_repair").at("anchor_ms").num()==started+60000);
    CHECK(states.at("late-ok").at("cooldown_last_repair").at("observed_at_ms").num()==started+30000);
  }
  TEST(source_cooldown_proof_never_certifies_unknown_older_max) {
    Temp t;
    constexpr std::int64_t started=1800000000000;
    constexpr std::int64_t unknown=started+100LL*86400000;
    std::int64_t now=started;
    const auto path=t.path/"db";
    {
      Store store(path,[&]{return now;});
      CHECK(store.source_admit("shared",0,50).empty());
      database_exec(path,"UPDATE source_state SET cooldown_until=1808640000000,last_status='rate_limited' WHERE source_id='shared';");
      store.source_report("shared","rate_limited","new bounded response",120000);
      CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='shared'")==unknown);
      CHECK(database_scalar(path,"SELECT json_extract(value,'$.verified_until_ms') FROM settings WHERE key='source_cooldown_proof/shared'")==started+120000);
      store.source_report("shared","ok","late success",0);
    }
    Store store(path,[&]{return now;});
    CHECK(store.source_states().at("shared").at("cooldown_remaining_ms").num()==unknown-now);
    CHECK(database_scalar(path,"SELECT json_extract(value,'$.verified_until_ms') FROM settings WHERE key='source_cooldown_proof/shared'")==started+120000);
  }
  TEST(source_cooldown_proof_and_repair_are_transactional) {
    Temp t;
    std::int64_t now=1800000000000;
    const auto path=t.path/"db";
    Store store(path,[&]{return now;});
    CHECK(store.source_admit("shared",0,50).empty());
    database_exec(path,"CREATE TRIGGER reject_cooldown_proof BEFORE INSERT ON settings WHEN NEW.key LIKE 'source_cooldown_proof/%' BEGIN SELECT RAISE(ABORT,'proof fixture'); END;");
    rejects([&]{store.source_report("shared","rate_limited","429",60000);},"E_STORAGE");
    CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='shared'")==0);
    CHECK(database_scalar(path,"SELECT failures FROM source_state WHERE source_id='shared'")==0);
    CHECK(database_scalar(path,"SELECT COUNT(*) FROM settings WHERE key='source_cooldown_proof/shared'")==0);
    database_exec(path,"DROP TRIGGER reject_cooldown_proof;");
    store.source_report("shared","rate_limited","429",60000);
    database_exec(path,"UPDATE source_state SET cooldown_until=2799999999000 WHERE source_id='shared';"
                       "CREATE TRIGGER reject_cooldown_proof BEFORE UPDATE ON settings WHEN NEW.key LIKE 'source_cooldown_proof/%' BEGIN SELECT RAISE(ABORT,'proof fixture'); END;");
    rejects([&]{store.source_states();},"E_STORAGE");
    CHECK(database_scalar(path,"SELECT cooldown_until FROM source_state WHERE source_id='shared'")==2799999999000LL);
    CHECK(database_scalar(path,"SELECT last_error='429' FROM source_state WHERE source_id='shared'")==1);
    CHECK(database_scalar(path,"SELECT json_extract(value,'$.verified_until_ms') FROM settings WHERE key='source_cooldown_proof/shared'")==now+60000);
    database_exec(path,"DROP TRIGGER reject_cooldown_proof;");
    const auto state=store.source_states().at("shared");
    CHECK(state.at("cooldown_remaining_ms").num()==86400000);
    CHECK(state.at("requests_today").num()==1);
    CHECK(state.at("consecutive_failures").num()==1);
    CHECK(state.at("cooldown_last_repair").at("original_until_ms").num()==2799999999000LL);
  }
  TEST(web_search_bounds_old_worker_retry_after_and_reports_new_worker_clamping) {
    struct Input { std::int64_t retry;bool present,already_clamped; };
    const Input inputs[]={{0,false,false},{-1,true,false},{120000,true,false},{86400000,true,false},
                          {86400001,true,false},{999999999000LL,true,false},
                          {std::numeric_limits<std::int64_t>::max(),true,false},{86400000,true,true}};
    for(const auto& input:inputs) {
      Temp t;
      std::int64_t now=5000000;
      Store store(t.path/"db",[&]{return now;});
      auto manifest=reviewed_sources(obj(R"({"version":1,"sources":[{"id":"fixture","provider":"fixture","index_family":"fixture","capability":"web","kind":"api","enabled":true,"default":true,"min_interval_ms":0,"cooldown_rate_ms":600000}]})"));
      FakeWorker worker;
      worker.handler=[&](const std::string& method,const Json&) {
        CHECK(method=="search.multi");
        auto result=obj(R"({"results":{"fixture":{"status":"rate_limited","error":"HTTP 429","http_status":429,"hits":[]}}})");
        if(input.present)result.at("results").at("fixture").set("retry_after_ms",input.retry);
        if(input.already_clamped)result.at("results").at("fixture").set("retry_after_clamped",true);
        return result;
      };
      WebService web(store,worker,SourceRegistry(manifest));
      const auto result=web.search("alice",obj(R"({"query":"bounded source retry"})"));
      const auto row=result.at("sources").at(0);
      const auto expected=std::min<std::int64_t>(86400000,std::max<std::int64_t>(0,input.retry));
      const bool clamped=input.retry>86400000||input.already_clamped;
      CHECK(row.at("status").str()=="rate_limited");
      CHECK(row.at("retry_after_ms").num()==expected);
      CHECK(row.at("cooldown_ms").num()==std::max<std::int64_t>(600000,expected));
      CHECK(row.at("retry_after_clamped").flag()==clamped);
      if(clamped) {
        CHECK(row.at("retry_after_limit_ms").num()==86400000);
        CHECK(row.at("error").str().starts_with("E_RETRY_AFTER_CLAMPED:"));
      }
      CHECK(result.at("usage").at("external_requests").num()==1);
      const auto source=web.sources().at("sources").at(0);
      CHECK(source.at("enabled").flag()&&source.at("configured_enabled").flag());
      CHECK(source.at("state").at("cooldown_remaining_ms").num()==std::max<std::int64_t>(600000,expected));
      CHECK(web.search("bob",obj(R"({"query":"another owner"})")).at("sources").at(0).at("reason").str()=="cooldown");
      CHECK(worker.calls.size()==1);
    }
  }
  TEST(migrate_both_schema_two_layouts_preserves_records) {
    for(bool claude_s1:{false,true}) {
      Temp t;
      auto path=t.path/"db";
      Json old,current,evidence,page,job,stats;
      std::string session;
      {
        Store store(path);
        old=store.ingest("alice",text("https://x.example/a","historical wording","neutral"));
        evidence=store.evidence("alice",old.at("snapshot_id").str(),0,10);
        auto input=text("https://x.example/a","test\u0301x 中文 current","neutral");
        if(claude_s1) {
          auto parsed=parse_document(input.raw,input.content_type,input.title);
          parsed.parser_version="worker-fixture/1";
          auto meta=Json::parse(R"({"fetched_at_ms":123,"final_url":"https://x.example/final","metadata":{"author":"fixture"}})");
          current=store.ingest_web("alice",input,parsed,meta,store.deletion_generation("alice",input.url));
          CHECK(store.source_admit("preserved-source",0,50).empty());
        } else current=store.ingest("alice",input);
        store.ingest("alice",{"https://x.example/noindex","<head><meta name='robots' content='none, follow'></head><body>hidden-canary</body>","text/html","neutral"});
        store.ingest("bob",text("https://x.example/b","test private-owner","neutral"));
        auto gone=store.ingest("alice",text("https://x.example/gone","test deleted-canary","neutral"));
        store.erase("alice",gone.at("document_id").str());
        session=store.search("alice",q("current")).at("session_id").str();
        page=store.page("alice",session,"",10);
        job=store.job_create("alice","developer","legacy-pending");
        job=store.job_get("alice",job.at("job_id").str());
        old=store.snapshot("alice",old.at("snapshot_id").str());
        current=store.snapshot("alice",current.at("snapshot_id").str());
        stats=store.stats("alice");
      }
      restore_legacy_schema(path,2,claude_s1);
      // Derived index contents are deliberately stale; migration must rebuild
      // only current, publishable rows from the preserved canonical snapshots.
      database_exec(path,"UPDATE content_fts SET title='stale-index',body='stale-index';");
      {
        Store store(path);
        CHECK(store.snapshot("alice",old.at("snapshot_id").str()).canonical()==old.canonical());
        CHECK(store.snapshot("alice",current.at("snapshot_id").str()).canonical()==current.canonical());
        CHECK(store.evidence_get("alice",evidence.at("evidence_id").str()).canonical()==evidence.canonical());
        CHECK(store.page("alice",session,"",10).canonical()==page.canonical());
        CHECK(store.job_get("alice",job.at("job_id").str()).canonical()==job.canonical());
        CHECK(store.stats("alice").canonical()==stats.canonical());
        CHECK(store.search("alice",q("test")).at("frozen_count").num()==1);
        CHECK(store.search("alice",q("中文")).at("frozen_count").num()==1);
        CHECK(store.search("alice",q("historical OR hidden-canary OR deleted-canary OR private-owner OR stale-index")).at("frozen_count").num()==0);
        CHECK(store.search("bob",q("test")).at("frozen_count").num()==1);
        if(claude_s1)CHECK(store.source_states().at("preserved-source").at("requests_today").num()==1);
      }
      CHECK(database_scalar(path,"PRAGMA user_version")==14);
      CHECK(database_scalar(path,"SELECT COUNT(*) FROM content_fts")==2);
      CHECK(database_scalar(path,"SELECT COUNT(*) FROM settings WHERE key='fts_tokenizer_version' AND value='bzs-ascii-cjk/2+codepoint-grams/1'")==1);
    }
  }
  TEST(schema_two_quarantine_is_checked_before_migration) {
    for(bool claude_s1:{false,true}) {
      Temp t;
      auto path=t.path/"db";
      {
        Store store(path);
        store.ingest("alice",text("https://x.example/a","quarantine-canary"));
      }
      restore_legacy_schema(path,2,claude_s1);
      database_exec(path,"UPDATE settings SET value='1' WHERE key='restore_quarantine';");
      rejects([&]{Store blocked(path);},"E_RESTORE_QUARANTINED");
      CHECK(database_scalar(path,"PRAGMA user_version")==2);
      CHECK(database_scalar(path,"SELECT COUNT(*) FROM snapshots")==1);
      CHECK(database_scalar(path,"SELECT COUNT(*) FROM pragma_table_info('documents') WHERE name='delete_generation'")==0);
    }
  }
  TEST(deletion_fences_survive_reimport_restart_and_clock_ties) {
    Temp t;
    auto path=t.path/"db";
    std::int64_t now=1000000,old_fence=0,current_fence=0;
    auto input=text("https://x.example/target","fresh web body","neutral");
    auto parsed=parse_document(input.raw,input.content_type,input.title);
    {
      Store store(path,[&]{return now;});
      auto before=store.ingest("alice",text(input.url,"original body","neutral"));
      old_fence=store.deletion_generation("alice",input.url);
      auto old_job=store.job_create("alice","fixture","started-before-delete");
      auto old_lease=store.lease_next();
      CHECK(old_lease&&old_lease->id==old_job.at("job_id").str());
      store.erase("alice",before.at("document_id").str());
      current_fence=store.deletion_generation("alice",input.url);
      CHECK(current_fence>old_fence);
      auto replacement=store.ingest("alice",text(input.url,"explicit replacement","neutral"));
      CHECK(store.deletion_generation("alice",input.url)==current_fence);
      rejects([&]{store.ingest_web("alice",input,parsed,Json::object(),old_fence);},"E_TARGET_DELETED");
      CHECK(!store.complete(*old_lease,input,parsed));
      CHECK(store.job_get("alice",old_lease->id).at("error_code").str()=="E_TARGET_DELETED");
      CHECK(store.cached("alice",input.url)->at("snapshot_id").str()==replacement.at("snapshot_id").str());
      // A new task after deletion is authorized even in the same millisecond.
      auto fresh_job=store.job_create("alice","fixture","started-after-delete");
      auto fresh_lease=store.lease_next();
      CHECK(fresh_lease&&fresh_lease->id==fresh_job.at("job_id").str());
      CHECK(store.complete(*fresh_lease,input,parsed));
      // Another owner's tombstone must not invalidate this document's fence.
      auto other=store.ingest("bob",text(input.url,"other owner"));
      store.erase("bob",other.at("document_id").str());
      CHECK(store.deletion_generation("alice",input.url)==current_fence);
      CHECK(store.ingest_web("alice",input,parsed,Json::object(),current_fence).at("canonical_text").str()==input.raw);
    }
    {
      Store store(path,[&]{return now;});
      CHECK(store.deletion_generation("alice",input.url)==current_fence);
      rejects([&]{store.ingest_web("alice",input,parsed,Json::object(),old_fence);},"E_TARGET_DELETED");
      auto current=store.cached("alice",input.url);
      store.erase("alice",current->at("document_id").str());
      CHECK(store.deletion_generation("alice",input.url)>current_fence);
      rejects([&]{store.ingest_web("alice",input,parsed,Json::object(),current_fence);},"E_TARGET_DELETED");
      auto allowed=store.deletion_generation("alice",input.url);
      CHECK(store.ingest_web("alice",input,parsed,Json::object(),allowed).at("canonical_text").str()==input.raw);
    }
  }
  TEST(web_noindex_change_is_not_hidden_by_content_reuse) {
    Temp t;
    Store store(t.path/"db");
    SourceDocument input{"https://x.example/noindex-header","<body>header canary</body>","text/html","neutral"};
    auto parsed=parse_document(input.raw,input.content_type,input.title);
    auto fence=store.deletion_generation("alice",input.url);
    auto first=store.ingest_web("alice",input,parsed,Json::object(),fence);
    auto evidence=store.evidence("alice",first.at("snapshot_id").str(),0,6);
    CHECK(store.search("alice",q("canary")).at("frozen_count").num()==1);
    parsed.index_allowed=false; // Same HTTP body, changed X-Robots-Tag.
    auto restricted=store.ingest_web("alice",input,parsed,Json::object(),fence);
    CHECK(restricted.at("snapshot_id").str()!=first.at("snapshot_id").str());
    CHECK(!restricted.at("indexed").flag());
    CHECK(store.search("alice",q("canary")).at("frozen_count").num()==0);
    CHECK(store.snapshot("alice",first.at("snapshot_id").str()).at("indexed").flag());
    CHECK(store.evidence_get("alice",evidence.at("evidence_id").str()).canonical()==evidence.canonical());
    parsed.index_allowed=true;
    auto restored=store.ingest_web("alice",input,parsed,Json::object(),fence);
    CHECK(restored.at("revision").num()==3);
    CHECK(store.search("alice",q("canary")).at("frozen_count").num()==1);
  }
  TEST(retention_reclaims_before_quota_and_ignores_expired_pins) {
    Temp t;
    StoreOptions options;
    options.max_revisions_per_document=1;
    options.max_snapshots_per_owner=1;
    options.max_bytes_per_owner=32;
    std::int64_t now=1000000;
    Store store(t.path/"db",[&]{return now;},options);
    auto first=store.ingest("alice",text("https://x.example/a","first"));
    rejects([&]{store.ingest("alice",text("https://x.example/a",std::string(40,'x')));},"E_QUOTA");
    // Failed writes roll prospective pruning and counters back.
    CHECK(store.snapshot("alice",first.at("snapshot_id").str()).at("canonical_text").str()=="first");
    CHECK(store.stats("alice").at("content_bytes").num()==10);
    auto second=store.ingest("alice",text("https://x.example/a","second"));
    CHECK(store.stats("alice").at("snapshots").num()==1);
    rejects([&]{store.snapshot("alice",first.at("snapshot_id").str());},"E_NOT_FOUND");
    auto session=store.search("alice",q("second"));
    rejects([&]{store.ingest("alice",text("https://x.example/a","third"));},"E_QUOTA");
    now+=3600001;
    auto third=store.ingest("alice",text("https://x.example/a","third"));
    CHECK(store.stats("alice").at("snapshots").num()==1);
    CHECK(store.stats("alice").at("content_bytes").num()==10);
    rejects([&]{store.snapshot("alice",second.at("snapshot_id").str());},"E_NOT_FOUND");
    rejects([&]{store.page("alice",session.at("session_id").str(),"");},"E_SESSION_EXPIRED");
    store.evidence("alice",third.at("snapshot_id").str(),0,5);
    rejects([&]{store.ingest("alice",text("https://x.example/a","fourth"));},"E_QUOTA");
    CHECK(store.snapshot("alice",third.at("snapshot_id").str()).at("canonical_text").str()=="third");
  }
  TEST(highlight_clipping_reports_only_visible_terms) {
    auto query=q("x y");
    auto text=std::string("x ")+std::string(700,'z')+" y.";
    auto h=highlight(text,query.root).at("highlights").at(0);
    CHECK(h.at("terms").size()==1&&h.at("terms").at(0).str()=="x");
    CHECK(h.at("text").str()==text.substr(static_cast<std::size_t>(h.at("start_byte").num()),static_cast<std::size_t>(h.at("end_byte").num()-h.at("start_byte").num())));
    auto cross=highlight("head a\nb tail",q("\"a\nb\"").root);
    CHECK(!cross.at("snippet").str().empty());
    CHECK(cross.at("highlights").at(0).at("text").str()=="a\nb");
    rejects([&]{highlight("中文",q("中").root,1,1);},"E_SCHEMA");
    Temp t;
    Store store(t.path/"db");
    auto snapshot=store.ingest("alice",bzs::SourceDocument{"https://x.example/range","中文","text/plain","neutral"});
    rejects([&]{store.range("alice",snapshot.at("snapshot_id").str(),0,1);},"E_EVIDENCE_RANGE");
    rejects([&]{store.range("alice",snapshot.at("snapshot_id").str(),3,2);},"E_EVIDENCE_RANGE");
    auto eof=store.range("alice",snapshot.at("snapshot_id").str(),6,1);
    CHECK(eof.at("text").str().empty()&&!eof.at("truncated").flag());
  }
  TEST(web_read_does_not_publish_across_target_deletion) {
    for(bool reimport:{false,true}) {
      Temp t;
      Store store(t.path/"db");
      auto original=store.ingest("alice",text("https://example.com/document","original","neutral"));
      FakeWorker worker;
      worker.handler=[&](const std::string& method,const Json&) {
        CHECK(method=="fetch.extract");
        store.erase("alice",original.at("document_id").str());
        if(reimport)store.ingest("alice",text("https://example.com/document","new explicit import","neutral"));
        return obj(R"({"status":"ok","html":"<body>stale fetched body</body>","text":"stale fetched body",
          "title":"neutral","content_type":"text/html","extractor":"offline-fixture/1","index_allowed":true})");
      };
      WebService web(store,worker,SourceRegistry{});
      rejects([&]{web.read("alice",obj(R"({"url":"https://example.com/document","freshness":"refresh"})"));},"E_TARGET_DELETED");
      auto current=store.cached("alice","https://example.com/document");
      CHECK(current.has_value()==reimport);
      if(current)CHECK(current->at("canonical_text").str()=="new explicit import");
    }
  }
  TEST(late_success_does_not_cancel_source_cooldown) {
    Temp t;
    std::int64_t now=1000000;
    Store store(t.path/"db",[&]{return now;});
    CHECK(store.source_admit("fixture",0,50).empty());
    store.source_report("fixture","blocked","captcha",1800000);
    store.source_report("fixture","ok","",0); // Older in-flight response completed late.
    CHECK(store.source_states().at("fixture").at("cooldown_remaining_ms").num()==1800000);
    CHECK(store.source_admit("fixture",0,50)=="cooldown");
    now+=1800001;
    CHECK(store.source_admit("fixture",0,50).empty());
    CHECK(store.source_states().at("fixture").at("cooldown_remaining_ms").num()==0);
  }

  TEST(web_cache_is_scoped_to_owner_and_review_validity) {
    Temp t;
    std::int64_t now=1000;
    Store store(t.path/"db",[&]{return now;});
    auto manifest=reviewed_sources(obj(R"({"version":1,"sources":[
      {"id":"fixture","provider":"fixture","index_family":"fixture","capability":"web","kind":"api","enabled":true,"default":true}]})"));
    manifest.at("sources").at(0).at("contract").at("review").set("expires_at","1970-01-01T00:00:02Z");
    FakeWorker worker;
    worker.handler=[](const std::string&,const Json&) {
      return obj(R"({"results":{"fixture":{"status":"empty","hits":[]}}})");
    };
    WebService web(store,worker,SourceRegistry(manifest));
    auto args=obj(R"({"query":"same"})");
    CHECK(!web.search("alice",args).at("from_cache").flag());
    CHECK(web.search("alice",args).at("from_cache").flag());
    CHECK(!web.search("bob",args).at("from_cache").flag());
    CHECK(worker.calls.size()==2);
    now=2000;
    rejects([&]{web.search("alice",args);},"E_SOURCE_REVIEW");
    CHECK(worker.calls.size()==2);
    CHECK(web.enabled_sources()==0);
    auto inconsistent=Json::parse(manifest.dump());
    inconsistent.at("sources").at(0).at("params").set("base_url","https://different.example/search");
    rejects([&]{SourceRegistry invalid(inconsistent);},"E_SOURCE_CONFIG");
  }
  TEST(rrf_rejects_invalid_ranks_and_preserves_nondefault_ports) {
    CHECK(dedup_key("http://example.com:443/x")!=dedup_key("http://example.com:80/x"));
    CHECK(dedup_key("https://example.com:80/x")!=dedup_key("https://example.com:443/x"));
    std::vector<SourceHits> hits={{"bing",{{"https://x.example/negative","bad","",-60},
      {"https://x.example/zero","bad","",0},{"https://x.example/huge","bad","",1000001},
      {"https://x.example/good","good","",1}}}};
    auto registry=test_registry();
    auto results=fuse(hits,registry,10);
    CHECK(results.size()==1&&results.at(0).at("url").str()=="https://x.example/good");
    rejects([&]{fuse(hits,registry,10,0);},"E_SCHEMA");
  }
  TEST(worker_success_schema_cannot_create_a_snapshot_by_default) {
    Temp t;
    Store store(t.path/"db");
    FakeWorker worker;
    worker.fill_fetch_defaults=false;
    auto valid=obj(R"({"status":"ok","html":"","text":"safe body","content_type":"text/plain",
      "title":"test","extractor":"fixture/1","final_url":"https://example.com/doc","redirects":[],"index_allowed":true})");
    Json response;
    worker.handler=[&](const std::string&,const Json&){return response;};
    WebService web(store,worker,SourceRegistry{});
    auto args=obj(R"({"url":"https://example.com/doc","freshness":"refresh"})");
    response=obj(R"({"status":"ok","html":"","text":"safe body","content_type":"text/plain",
      "title":"test","extractor":"fixture/1","final_url":"https://example.com/doc","redirects":[]})");
    rejects([&]{web.read("alice",args);},"E_WORKER_PROTOCOL");
    response=Json::parse(valid.dump());response.set("index_allowed","true");
    rejects([&]{web.read("alice",args);},"E_WORKER_PROTOCOL");
    response=Json::parse(valid.dump());response.set("final_url","http://127.0.0.1/private");
    rejects([&]{web.read("alice",args);},"E_WORKER_PROTOCOL");
    response=Json::parse(valid.dump());response.set("final_url","http://012.0.0.1/private");
    rejects([&]{web.read("alice",args);},"E_WORKER_PROTOCOL");
    response=Json::parse(valid.dump());response.set("redirects",obj(R"(["http://169.254.169.254/latest/meta-data"] )"));
    rejects([&]{web.read("alice",args);},"E_WORKER_PROTOCOL");
    CHECK(store.stats("alice").at("snapshots").num()==0);
    response=Json::parse(valid.dump());response.set("index_allowed",false);
    CHECK(!web.read("alice",args).at("indexed").flag());
    CHECK(store.search("alice",q("body")).at("frozen_count").num()==0);
  }
  TEST(malformed_source_success_is_partial_and_not_cached) {
    Temp t;
    std::int64_t now=1000;
    Store store(t.path/"db",[&]{return now;});
    FakeWorker worker;
    worker.handler=[](const std::string&,const Json&) {
      return obj(R"({"results":{"bing":{"status":"ok","hits":{}}}})");
    };
    WebService web(store,worker,test_registry());
    auto args=obj(R"({"query":"protocol","sources":["bing"]})");
    auto first=web.search("alice",args);
    CHECK(first.at("partial").flag());
    CHECK(first.at("sources").at(0).at("status").str()=="error");
    now+=6*60*1000;
    CHECK(!web.search("alice",args).at("from_cache").flag());
    CHECK(worker.calls.size()==2);
  }


  TEST(combined_match_score_keeps_legacy_weights_filters_and_field_semantics) {
    struct Case {std::string expression,title,body;std::optional<int> score;};
    const std::vector<Case> cases={
      {"marker","MARKER marker","marker",9},
      {"intitle:marker","MARKER marker","marker",9},
      {"inbody:marker","marker marker marker marker marker marker","marker marker marker marker marker marker marker marker marker marker marker marker",30},
      {"NOT absent","marker","marker",0},
      {"NOT marker","marker","other",std::nullopt},
      {"marker OR absent","marker","marker absent",6},
      {"\"alpha beta\" AND NOT forbidden","Alpha Beta","alpha beta alpha beta",6},
      {"café","CAFÉ","café",1},
      {"Москва","Москва","москва",4},
      {"c++","C++ c++20 ac++","c++ c++foo",9},
      {"site:elsewhere.example marker","marker","marker",std::nullopt},
      {"filetype:html marker","marker","marker",std::nullopt}};
    for(bool reverse:{false,true})for(std::size_t i=0;i<cases.size();++i) {
      const auto& value=cases[reverse?cases.size()-1-i:i];const auto query=q(value.expression);
      const auto score=match_score(query,value.title,value.body,"https://x.example/marker","x.example","text/plain");
      CHECK(score==value.score);CHECK(matches(query,value.title,value.body,"https://x.example/marker","x.example","text/plain")==score.has_value());
      if(score)CHECK(score_match(query.root,value.title,value.body)==*value.score);
    }
    const auto url=parse_query(Json::parse(R"({"op":"phrase","field":"url","value":"marker"})"));
    CHECK(match_score(url,"marker","marker marker","https://x.example/marker","x.example","text/plain")==6);
    CHECK(match_score(url,"neutral","other","https://x.example/marker","x.example","text/plain")==0);
    CHECK(!match_score(url,"marker","marker","https://x.example/other","x.example","text/plain"));
  }
  TEST(search_matching_releases_store_lock_and_sqlite_transaction_for_every_path) {
    for(bool freeze:{false,true})for(const auto* query:{"marker","\"arker\"","NOT absent"}) {
      Store store(":memory:");store.ingest("alice",text("https://x.example/a","marker local content"));
      store.ingest("bob",text("https://x.example/b","marker other owner"));
      const auto result=search_while(store,freeze,[&] {
        CHECK(store.stats("bob").at("documents").num()==1);
        store.ingest("bob",text("https://x.example/new","marker concurrent import"));
        CHECK(store.stats("bob").at("documents").num()==2);
      },query);
      CHECK(search_hits(store,freeze,result).size()==1);CHECK(!result.at("partial").flag());
      CHECK(result.at("scanned_candidates").num()==1);
    }
  }
  TEST(search_matching_rechecks_deleted_and_reimported_candidate_identity) {
    for(bool freeze:{false,true})for(bool reimport:{false,true}) {
      Store store(":memory:");const auto old=store.ingest("alice",text("https://x.example/a","marker old"));
      const auto result=search_while(store,freeze,[&] {
        store.erase("alice",old.at("document_id").str());
        if(reimport)store.ingest("alice",text("https://x.example/a","marker new"));
      });
      CHECK(search_hits(store,freeze,result).size()==0);CHECK(result.at("partial").flag());
      CHECK(result.at("scanned_candidates").num()==1);
      CHECK(store.search_once("alice",q("marker")).at("returned_count").num()==(reimport?1:0));
    }
  }
  TEST(search_matching_preserves_immutable_revision_or_marks_pruning_partial) {
    for(bool freeze:{false,true})for(bool prune:{false,true}) {
      StoreOptions options;options.max_revisions_per_document=prune?1:5;
      Store store(":memory:",{},options);const auto old=store.ingest("alice",text("https://x.example/a","marker original"));
      const auto result=search_while(store,freeze,[&] {store.ingest("alice",text("https://x.example/a","replacement text"));},"marker");
      const auto found=search_hits(store,freeze,result);CHECK(found.size()==(prune?0U:1U));
      CHECK(result.at("partial").flag()==prune);
      if(!prune)CHECK(found.at(0).at("snapshot_id").str()==old.at("snapshot_id").str());
      CHECK(store.search_once("alice",q("marker")).at("returned_count").num()==0);
    }
  }
  TEST(search_matching_rechecks_principal_revocation_before_any_result_or_receipt) {
    for(bool freeze:{false,true})for(bool other_handle:{false,true}) {
      Temp temp;Store store(temp.path/"db");Store other(temp.path/"db");
      store.ingest("alice",text("https://x.example/a","marker private"));
      rejects([&] {search_while(store,freeze,[&] {(other_handle?other:store).revoke_principal("admin","alice");});},"E_PRINCIPAL_REVOKED");
      CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM sessions")==0);
      CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM search_stream_receipts")==0);
      CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM snapshots")==1);
    }
  }
  TEST(search_matching_rechecks_active_session_capacity_at_commit) {
    Temp temp;Store store(temp.path/"db");store.ingest("alice",text("https://x.example/a","marker"));
    for(int i=0;i<63;++i)store.search("alice",q("marker"));
    rejects([&] {search_while(store,true,[&] {
      StoreSearchTestAccess::set(store,{});store.search("alice",q("marker"));
      CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM sessions")==64);
    });},"E_QUOTA");
    CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM sessions")==64);
    CHECK(database_scalar(temp.path/"db","SELECT COUNT(*) FROM search_stream_receipts")==64);
    CHECK(store.search_once("alice",q("marker")).at("returned_count").num()==1);
  }
  TEST(search_source_withdrawal_does_not_change_independent_local_snapshot_ownership) {
    for(bool freeze:{false,true}) {
      Store store(":memory:");store.activate_source_publication(test_registry());
      store.ingest("alice",text("https://x.example/a","marker local import"));
      store.ingest("bob",text("https://x.example/b","marker unrelated owner"));
      const auto result=search_while(store,freeze,[&] {store.activate_source_publication(SourceRegistry(Json::parse(R"({"version":1,"sources":[]})")));});
      const auto found=search_hits(store,freeze,result);CHECK(found.size()==1);
      CHECK(found.at(0).at("url").str()=="https://x.example/a");CHECK(!result.at("partial").flag());
    }
  }
  Json search_site_config(const std::string& mode="allowed") {
    auto manifest=Json::parse(R"({"version":1,"sites":[{"id":"scan-site","enabled":true,"origin":"https://scan.example","path_prefixes":["/"],"review":{"status":"approved","payment_scheme":"no_payment","automated_access":"approved","checked_at":"2020-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","terms_url":"https://scan.example/terms","cache_policy":"allowed_with_limits","redistribution_policy":"allowed_with_limits","approved_by":"offline-fixture"},"limits":{"max_concurrency":1,"min_interval_ms":0,"request_quota":1000,"quota_window_seconds":86400}}]})");
    auto review=manifest.at("sites").at(0).at("review");
    if(mode=="rejected")review.set("status","rejected");
    if(mode=="links_only")review.set("redistribution_policy","links_only");
    return manifest;
  }
  Json search_web_artifact(Store& store,const std::string& url,const std::string& final_url="",Json redirects=Json::array(),const std::string& mode="allowed") {
    ParsedDocument parsed;parsed.title="marker original title";parsed.text="marker original secret";parsed.content_type="text/plain";parsed.parser_version="search-concurrency/1";
    parsed.index_allowed=mode!="noindex";
    parsed.publication=Json::parse(R"({"version":"bzs-publication-1","index_allowed":true,"snippet_allowed":true,"follow_allowed":true,"snippet_text":"marker original secret","snippet_title":"marker original title","directives":[],"diagnostics":[]})");
    if(mode!="allowed") {parsed.publication.set("snippet_allowed",false);parsed.publication.set("snippet_text","");parsed.publication.set("snippet_title","");}
    if(mode=="noindex")parsed.publication.set("index_allowed",false);
    auto meta=Json::object();meta.set("final_url",final_url.empty()?url:final_url);meta.set("redirects",redirects);
    return store.ingest_web("alice",{url,parsed.text,"text/plain",parsed.title},parsed,meta,store.deletion_generation("alice",url));
  }
  TEST(search_matching_rechecks_site_restrictions_even_after_relaxation) {
    for(bool freeze:{false,true})for(const auto* mode:{"rejected","links_only"})for(bool relax:{false,true}) {
      Store store(":memory:");store.activate_site_publication(SiteRegistry(search_site_config()));
      const auto old=search_web_artifact(store,"https://scan.example/a");
      const auto result=search_while(store,freeze,[&] {
        store.activate_site_publication(SiteRegistry(search_site_config(mode)));
        if(relax)store.activate_site_publication(SiteRegistry(search_site_config()));
      });
      const auto found=search_hits(store,freeze,result);
      if(std::string(mode)=="rejected") {CHECK(found.size()==0);CHECK(result.at("partial").flag());}
      else {CHECK(found.size()==1);CHECK(found.at(0).at("snapshot_id").str()==old.at("snapshot_id").str());CHECK(found.at(0).at("snippet").str().empty());CHECK(found.at(0).at("title").str()=="https://scan.example/a");}
    }
  }
  TEST(search_matching_rechecks_page_and_frozen_redirect_history_after_relaxation) {
    for(bool freeze:{false,true})for(const auto* mode:{"noindex","nosnippet"})for(const auto* identity:{"logical","final","intermediate"}) {
      Store store(":memory:");const std::string logical="https://scan.example/a",intermediate="https://middle.example/b",final="https://final.example/c";
      const auto old=search_web_artifact(store,logical,final,Json::parse("[\""+intermediate+"\",\""+final+"\"]"));
      const auto target=std::string(identity)=="logical"?logical:std::string(identity)=="final"?final:intermediate;
      const auto result=search_while(store,freeze,[&] {
        search_web_artifact(store,target,"",Json::array(),mode);
        search_web_artifact(store,target); // Current allow must not revive the captured old snapshot.
      });
      const auto found=search_hits(store,freeze,result);
      if(std::string(mode)=="noindex") {CHECK(found.size()==0);CHECK(result.at("partial").flag());}
      else {CHECK(found.size()==1);CHECK(found.at(0).at("snapshot_id").str()==old.at("snapshot_id").str());CHECK(found.at(0).at("snippet").str().empty());CHECK(found.at(0).at("title").str()==logical);}
      // A genuinely new read remains eligible, proving the barrier is a
      // captured-artifact fence, not an accidental permanent URL blacklist.
      StoreSearchTestAccess::set(store,{});search_web_artifact(store,logical,final,Json::parse("[\""+intermediate+"\",\""+final+"\"]"));
      CHECK(store.search_once("alice",q("marker")).at("returned_count").num()>0);
    }
  }

  TEST(deletion_url_key_folds_only_unreserved_uri_bytes) {
    const std::string safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    constexpr char hex[]="0123456789ABCDEF";
    for(const auto c:safe) {
      std::string escaped="%";escaped+=hex[static_cast<unsigned char>(c)>>4];escaped+=hex[static_cast<unsigned char>(c)&15];
      const auto plain="https://identity.example/pre"+std::string(1,c)+"post?q="+std::string(1,c);
      const auto encoded="https://IDENTITY.EXAMPLE:00443/pre"+escaped+"post?q="+escaped+"#fragment";
      CHECK(deletion_url_key(encoded)==plain);
      CHECK(deletion_url_key(deletion_url_key(encoded))==plain);
      CHECK(deletion_url_key("https://identity.example/pre"+ascii_lower(escaped)+"post?q="+ascii_lower(escaped))==plain);
    }
    CHECK(deletion_url_key("https://IDENTITY.EXAMPLE:443")=="https://identity.example/");
    CHECK(deletion_url_key("http://IDENTITY.EXAMPLE:080/?#fragment")=="http://identity.example/");
    CHECK(deletion_url_key("https://identity.example?%61=1")=="https://identity.example/?a=1");
  }
  TEST(deletion_url_key_preserves_reserved_bytes_and_non_equivalent_urls) {
    for(const auto& pair:std::vector<std::pair<std::string,std::string>>{
      {"/a%2Fb","/a/b"},{"/a%3Fb","/a?b"},{"/a%3Bb","/a;b"},{"/a%252Fb","/a%2Fb"},
      {"/?x=a%26b","/?x=a&b"},{"/?x=a%3Db","/?x=a=b"},{"/?x=a%2Fb","/?x=a/b"},
      {"/?a=1&b=2","/?b=2&a=1"},{"/Case","/case"},{"/a/../b","/b"},
      {"/%2E%2E/b","/b"},{"/a/","/a"}}) {
      CHECK(deletion_url_key("https://identity.example"+pair.first)!=deletion_url_key("https://identity.example"+pair.second));
    }
    CHECK(deletion_url_key("https://identity.example/%2f")==deletion_url_key("https://identity.example/%2F"));
    CHECK(deletion_url_key("http://identity.example/a")!=deletion_url_key("https://identity.example/a"));
    CHECK(deletion_url_key("https://identity.example:444/a")!=deletion_url_key("https://identity.example/a"));
    for(const auto* legacy:{"https://IDENTITY.EXAMPLE:443/%6D%","https://identity.example/%GG","not-a-url"})
      CHECK(deletion_url_key(legacy)==legacy);
  }
}
int main() {
  std::size_t passed=0;
  for(const auto&[name, fn]:tests()) {
    try {
      fn();
      ++passed;
      std::cout<<"PASS "<<name<<'\n';
    }catch(const std::exception&e) {
      std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n';
    }
  }
  Json r=Json::object();
  r.set("tests", static_cast<std::int64_t>(tests().size()));
  r.set("passed", static_cast<std::int64_t>(passed));
  r.set("assertions", static_cast<std::int64_t>(assertions));
  std::cout<<r.dump()<<'\n';
  return passed==tests().size()?0:1;
}
