#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <sqlite3.h>
#include <atomic>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <thread>
#include <vector>
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
  void restore_schema_one_index(const std::filesystem::path&path) {
    // Reproduce the shipped v1 schema/index without using the new projection.
    database_exec(path, R"SQL(
      BEGIN IMMEDIATE;
      DROP TABLE content_fts;
      CREATE VIRTUAL TABLE content_fts USING fts5(document_id UNINDEXED,owner UNINDEXED,title,body,tokenize='unicode61');
      INSERT INTO content_fts(document_id,owner,title,body)
        SELECT d.id,d.owner,s.title,s.canonical_text FROM documents d JOIN snapshots s ON s.id=d.current_snapshot
        WHERE d.deleted=0 AND s.index_allowed=1;
      PRAGMA user_version=1;
      COMMIT;
    )SQL");
  }
  TEST(json_roundtrip) {
    auto a=Json::parse(R"({"b":2,"a":"中文😀","n":null,"f":false})");
    CHECK(a.at("a").str()=="中文😀");
    CHECK(a.at("n").is_null());
    CHECK(!a.at("f").flag());
    CHECK(a.canonical()==R"({"a":"中文😀","b":2,"f":false,"n":null})");
    CHECK(Json::parse(a.dump()).canonical()==a.canonical());
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
    CHECK(Json::parse(R"("\ud83d\ude00")").str()=="😀");
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
      "file:///etc/passwd", "https://user:pass@example.com/", "https://example.com\\evil", "https://example..com/", "https://example.com:80/"
    })rejects([&] {
      metadata_host(s);
    }, "E_URL");
  }
  TEST(query_symbols) {
    auto x=q("std::expected C++");
    CHECK(x.root.children.at(0).value=="std::expected");
    CHECK(matches(x, "", "use std::expected in C++", "", "", ""));
    CHECK(!matches(x, "", "use std::expected in C#", "", "", ""));
    CHECK(!fts_candidates(x));
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
    CHECK(!fts_candidates(x));
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
    CHECK(p.parser_version=="bootstrap-html/2");
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
    CHECK(a.at("retrieval").str()=="fts5_candidates_verified");
    CHECK(s.search("alice", q("C++")).at("frozen_count").num()==1);
    CHECK(s.search("alice", q("搜索")).at("frozen_count").num()==1);
  }
  TEST(store_candidate_predicate_agreement) {
    Temp t;
    Store s(t.path/"db");
    std::vector<SourceDocument> docs;
    for(auto body: {"apple", "test中文", "中文test", "test\u0301x", "testé", "_", "foo_bar", "test_test"}) {
      docs.push_back(text("https://x.example/"+std::to_string(docs.size()), body, "neutral"));
    }
    docs.push_back(text("https://x.example/title", "body", "label\u0301suffix"));
    for(const auto&doc:docs)s.ingest("alice", doc);
    for(auto expression: {"\"app\"", "test", "_", "foo_bar", "test_test", "intitle:label", "\"_\"", "test OR foo_bar"}) {
      auto query=q(expression);
      std::int64_t expected=0;
      for(const auto&doc:docs)if(matches(query, doc.title, doc.raw, doc.url, "x.example", doc.content_type))++expected;
      auto result=s.search("alice", query);
      CHECK(result.at("frozen_count").num()==expected);
      CHECK(!result.at("partial").flag());
    }
    auto phrase=s.search("alice", q("\"app\""));
    CHECK(phrase.at("frozen_count").num()==1);
    CHECK(phrase.at("retrieval").str()=="bounded_literal_scan");
    auto mixed=s.search("alice", q("test"));
    CHECK(mixed.at("frozen_count").num()==4);
    CHECK(mixed.at("retrieval").str()=="fts5_candidates_verified");
    CHECK(s.search("alice", q("_ ")).at("frozen_count").num()==1);
    auto partial=s.search("alice", q("test"), 1, 10);
    CHECK(partial.at("scanned_candidates").num()==1&&partial.at("partial").flag());
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
    rejects([&] {
      s.page("a", se, "", 10);
    }, "E_SESSION_INVALIDATED");
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
    CHECK(database_scalar(path, "PRAGMA user_version")==2);
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
    database_exec(future, "PRAGMA user_version=3;");
    rejects([&] {
      Store blocked(future);
    }, "E_SCHEMA_VERSION");
    CHECK(database_scalar(future, "PRAGMA user_version")==3);
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
  TEST(job_auth_epoch) {
    Temp t;
    Store s(t.path/"db");
    auto d=s.ingest("a", text("https://x.example/a", "delete me"));
    auto id=s.job_create("a", "developer", "key").at("job_id").str();
    auto l=s.lease_next();
    s.erase("a", d.at("document_id").str());
    auto doc=text("https://x.example/a", "do not resurrect");
    CHECK(!s.complete(*l, doc, parse_document(doc.raw, doc.content_type)));
    s.lease_next();
    CHECK(s.job_get("a", id).at("error_code").str()=="E_POLICY_CHANGED");
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
    CHECK(Service::capabilities().at("live_web_sources").num()==0);
    CHECK(!Service::capabilities().at("production_ready").flag());
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
