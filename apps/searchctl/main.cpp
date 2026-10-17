#include "bzs/json.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <utility>
#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <charconv>
#include <cstdlib>
#include <iostream>
namespace net=boost::asio;
namespace beast=boost::beast;
namespace http=beast::http;
using tcp=net::ip::tcp;
using namespace bzs;
namespace {
  std::pair<unsigned, std::string> call(unsigned short port, const Json& body, bool health) {
    net::io_context io;
    beast::tcp_stream stream(io);
    beast::flat_buffer buffer;
    http::response_parser<http::string_body> parser;
    parser.body_limit(4*1024*1024);
    http::request<http::string_body> request(health?http::verb::get:http::verb::post, health?"/healthz":"/api/v0/call", 11);
    request.set(http::field::host, "127.0.0.1:"+std::to_string(port));
    request.keep_alive(false);
    if(!health) {
      const char*token=std::getenv("SEARCH_API_TOKEN");
      require(token&&std::string_view(token).size()>=32&&std::string_view(token).size()<=512, "E_AUTH", "Set SEARCH_API_TOKEN in the environment");
      for(unsigned char c:std::string_view(token))require(c>32&&c<127, "E_AUTH", "Invalid token characters");
      request.set(http::field::authorization, std::string("Bearer ")+token);
      request.set(http::field::content_type, "application/json");
      request.body()=body.dump();
      request.prepare_payload();
    }
    beast::error_code error;
    unsigned status=0;
    std::string output;
    stream.expires_after(std::chrono::seconds(10));
    stream.async_connect(tcp::endpoint(net::ip::make_address("127.0.0.1"), port), [&](beast::error_code ec) {
      if(ec) {
        error=ec;
        return;
      }
      http::async_write(stream, request, [&](beast::error_code ec2, std::size_t) {
        if(ec2) {
          error=ec2;
          return;
        }
        http::async_read(stream, buffer, parser, [&](beast::error_code ec3, std::size_t) {
          if(ec3) {
            error=ec3;
            return;
          }
          auto result=parser.release();
          status=result.result_int();
          output=std::move(result.body());
        });
      });
    });
    io.run();
    require(!error, "E_TRANSPORT", "Local API request failed: "+error.message(), 503);
    return {
      status, output
    };
  }
}
int main(int argc, char**argv) {
  try {
    unsigned short port=8610;
    int i=1;
    if(i<argc&&std::string(argv[i])=="--port") {
      require(i+1<argc, "E_ARGUMENT", "Missing port");
      std::string v=argv[i+1];
      unsigned n=0;
      auto[p, e]=std::from_chars(v.data(), v.data()+v.size(), n);
      require(e==std::errc {
      }
      &&p==v.data()+v.size()&&n>=1&&n<=65535, "E_ARGUMENT", "Invalid port");
      port=static_cast<unsigned short>(n);
      i+=2;
    }
    if(i>=argc||std::string(argv[i])=="--help") {
      std::cout<<"searchctl [--port 8610] COMMAND [ARGS]\n\nCommands:\n  health | capabilities | fixtures | stats | backup\n  fixture-search QUERY | fixture-ingest FIXTURE_ID\n  ingest FILE URL [text/html|text/plain]\n  query QUERY | search QUERY\n  read SNAPSHOT_ID | delete DOCUMENT_ID\n  find SNAPSHOT_ID TEXT\n  page SESSION_ID [CURSOR]\n  validate KIND CONFIG_JSON_FILE\n  call REQUEST_JSON_FILE\n\nValidation kinds: model-config | source-manifest | search-request.\nValidation checks configuration without applying it.\nSEARCH_API_TOKEN supplies the bearer token; only loopback connections are allowed.\n";
      return 0;
    }
    auto command=std::string(argv[i++]);
    if(command=="--version") {
      std::cout<<BZS_VERSION<<'\n';
      return 0;
    }
    auto arg=[&]() {
      require(i<argc, "E_ARGUMENT", "Missing command argument");
      return std::string(argv[i++]);
    };
    Json params=Json::object(), request=Json::object();
    std::string op;
    if(command=="health") {
    } else if(command=="capabilities"||command=="stats")op=command;
    else if(command=="fixtures")op="fixtures.list";
    else if(command=="backup")op="backup.create";
    else if(command=="fixture-ingest") {
      op="fixture.ingest";
      params.set("fixture_id", arg());
    } else if(command=="read") {
      op="document.read";
      params.set("snapshot_id", arg());
    } else if(command=="delete") {
      op="document.delete";
      params.set("document_id", arg());
    } else if(command=="find") {
      op="find";
      params.set("snapshot_id", arg());
      params.set("text", arg());
    } else if(command=="page") {
      op="search.page";
      params.set("session_id", arg());
      if(i<argc)params.set("cursor", arg());
    } else if(command=="ingest") {
      op="document.ingest";
      auto path=arg();
      params.set("url", arg());
      auto type=i<argc?arg():(std::filesystem::path(path).extension()==".txt"?"text/plain":"text/html");
      params.set("content_type", type);
      params.set("content", read_file(path));
      params.set("title", std::filesystem::path(path).filename().string());
    } else if(command=="query"||command=="search"||command=="fixture-search") {
      op=command=="query"?"query.compile":command=="fixture-search"?"fixtures.search":"search";
      std::string q=arg();
      while(i<argc)q+=" "+arg();
      params.set("query", q);
    } else if(command=="validate") {
      op="contract.validate";
      params.set("kind", arg());
      params.set("config", Json::parse(read_file(arg(), 65536)));
    } else if(command=="call") {
      request=Json::parse(read_file(arg(), 2*1024*1024));
    } else throw Error("E_ARGUMENT", "Unknown command");
    require(i==argc, "E_ARGUMENT", "Unexpected arguments");
    if(command!="call") {
      request.set("operation", op);
      request.set("params", params);
    }
    auto[status, response]=call(port, request, command=="health");
    std::cout<<Json::parse(response).dump(true)<<'\n';
    return status>=400?1:0;
  }catch(const Error&e) {
    std::cerr<<e.code<<": "<<e.what()<<'\n';
    return 2;
  }catch(const std::exception&e) {
    std::cerr<<"E_CLIENT: "<<e.what()<<'\n';
    return 2;
  }
}
