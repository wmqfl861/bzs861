#include "bzs/service.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <utility>
#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <algorithm>
#include <atomic>
#include <charconv>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
namespace net=boost::asio;
namespace beast=boost::beast;
namespace http=beast::http;
using tcp=net::ip::tcp;
using namespace bzs;
namespace {
  Json envelope(const std::string& id, const Json& result) {
    Json j=Json::object();
    j.set("request_id", id);
    j.set("api_version", "0.0.1");
    j.set("status", result.has("partial")&&result.at("partial").flag()?"partial":"success");
    j.set("result", result);
    j.set("errors", Json::array());
    Json u=Json::object();
    u.set("external_requests", static_cast<std::int64_t>(0));
    u.set("model_calls", static_cast<std::int64_t>(0));
    j.set("usage", result.has("usage")&&result.at("usage").is_object()?result.at("usage"):u);
    return j;
  }
  Json failure(const std::string& id, const Error& error) {
    auto out=Json::object(), errors=Json::array(), item=Json::object();
    out.set("request_id",id);out.set("status","failed");out.set("api_version","0.0.1");
    item.set("code",error.code);item.set("message",error.what());errors.push(item);out.set("errors",errors);
    return out;
  }
  // At most one admitted request per business thread. There is no additional
  // waiting queue: a saturated executor rejects before Service can reserve any
  // work. Socket handlers, health checks and transport validation stay on IO.
  class RequestExecutor {
    std::mutex mutex_;
    std::condition_variable wake_;
    std::deque<std::function<void()>> pending_;
    std::vector<std::jthread> workers_;
    const unsigned capacity_;
    unsigned admitted_=0;
    bool stopped_=false;
    void run() {
      for(;;) {
        std::function<void()> task;
        {
          std::unique_lock lock(mutex_);
          wake_.wait(lock,[&] {return stopped_||!pending_.empty();});
          if(stopped_)return;
          task=std::move(pending_.front());pending_.pop_front();
        }
        // The request handler produces its own error envelope. An allocation
        // or executor failure must still release admission, never retry work.
        try {task();} catch(...) {std::clog<<"{\"event\":\"request_executor_failure\"}\n";}
        {std::lock_guard lock(mutex_);--admitted_;}
      }
    }
    public:
    explicit RequestExecutor(unsigned count):capacity_(count) {
      try {for(unsigned i=0;i<count;++i)workers_.emplace_back([this] {run();});}
      catch(...) {stop();join();throw;}
    }
    ~RequestExecutor() {stop();join();}
    bool submit(std::function<void()> task) {
      std::lock_guard lock(mutex_);
      if(stopped_||admitted_>=capacity_)return false;
      pending_.push_back(std::move(task));++admitted_;wake_.notify_one();return true;
    }
    void stop() {
      std::lock_guard lock(mutex_);
      stopped_=true;
      admitted_-=static_cast<unsigned>(pending_.size());
      pending_.clear();wake_.notify_all();
    }
    void join() {for(auto& worker:workers_)if(worker.joinable())worker.join();}
  };
  // Worker RPC over loopback HTTP. Each call owns a short-lived io_context so a slow
  // worker only blocks the request thread that is waiting for it.
  class HttpWorker final:public WorkerPort {
    std::string host_;
    unsigned short port_;
    std::string token_;
    public: HttpWorker(std::string host, unsigned short port, std::string token):host_(std::move(host)), port_(port), token_(std::move(token)) {
    }
    Json call(const std::string& method, const Json& params, int timeout_ms) override {
      net::io_context io;
      beast::tcp_stream stream(io);
      beast::flat_buffer buffer;
      http::response_parser<http::string_body> parser;
      parser.body_limit(24*1024*1024);
      Json body=Json::object();
      body.set("method", method);
      body.set("params", params);
      http::request<http::string_body> request(http::verb::post, "/rpc", 11);
      request.set(http::field::host, host_+":"+std::to_string(port_));
      request.set(http::field::authorization, "Bearer "+token_);
      request.set(http::field::content_type, "application/json");
      request.keep_alive(false);
      request.body()=body.dump();
      request.prepare_payload();
      beast::error_code error;
      unsigned status=0;
      std::string output;
      stream.expires_after(std::chrono::milliseconds(timeout_ms));
      stream.async_connect(tcp::endpoint(net::ip::make_address(host_), port_), [&](beast::error_code ec) {
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
      if(error==beast::error::timeout)throw Error("E_WORKER_TIMEOUT", "Worker did not answer in time", 504);
      require(!error, "E_WORKER_UNAVAILABLE", "Worker is not reachable; start it with `bzs start`", 503);
      require(status==200, "E_WORKER", "Worker returned HTTP "+std::to_string(status), 502);
      auto reply=Json::parse(output, 24*1024*1024);
      if(reply.has("error")&&reply.at("error").is_object()) {
        auto e=reply.at("error");
        throw Error("E_WORKER", "Worker error: "+(e.has("message")&&e.at("message").is_string()?utf8_prefix(e.at("message").str(), 300):std::string("unknown")), 502);
      }
      return reply.at("result");
    }
  };
  std::pair<std::string, unsigned short> loopback_url(const std::string& url) {
    require(url.starts_with("http://"), "E_ARGUMENT", "--worker must be http://127.0.0.1:PORT");
    auto rest=url.substr(7);
    while(!rest.empty()&&rest.back()=='/')rest.pop_back();
    auto colon=rest.rfind(':');
    require(colon!=std::string::npos, "E_ARGUMENT", "--worker needs a port");
    auto host=rest.substr(0, colon);
    require(host=="127.0.0.1", "E_ARGUMENT", "--worker must use 127.0.0.1 (worker RPC is loopback-only)");
    unsigned n=0;
    auto port=rest.substr(colon+1);
    auto [p, e]=std::from_chars(port.data(), port.data()+port.size(), n);
    require(e==std::errc {
    }
    &&p==port.data()+port.size()&&n>=1&&n<=65535, "E_ARGUMENT", "Invalid worker port");
    return {
      host, static_cast<unsigned short>(n)
    };
  }
  class Session:public std::enable_shared_from_this<Session> {
    beast::tcp_stream stream_;
    beast::flat_buffer buffer_ {
      16384
    };
    http::request_parser<http::string_body> parser_;
    http::response<http::string_body> response_;
    Service& service_;
    RequestExecutor& requests_;
    const Auth&auth_;
    unsigned short port_;
    std::shared_ptr<std::atomic<unsigned>> active_;
    static void log_request(const std::string& rid,int status,std::int64_t started) {
      // No query, URL, body, principal or token is logged.
      auto log=Json::object();log.set("event","request_completed");log.set("request_id",rid);
      log.set("http_status",static_cast<std::int64_t>(status));log.set("elapsed_ms",now_ms()-started);
      std::clog<<log.dump()<<'\n';
    }
    void dispatch(Principal principal,std::string body,std::string rid,std::int64_t started,
                  net::any_io_executor executor) {
      int status=200;
      std::string output;
      try {
        auto result=service_.dispatch(principal,Json::parse(body));
        output=envelope(rid,result).dump();
      }catch(const Error& error) {
        status=error.http_status;output=failure(rid,error).dump();
      }catch(const std::exception&) {
        status=500;output="{\"status\":\"failed\",\"errors\":[{\"code\":\"E_INTERNAL\"}]}";
      }
      log_request(rid,status,started);
      auto self=shared_from_this();
      // The socket and response object are used only on this session's strand.
      net::post(executor,[self,status,output=std::move(output)] {
        self->respond(static_cast<http::status>(status),output);
      });
    }
    void respond(http::status code, const std::string& body) {
      response_=http::response<http::string_body> {
        code, 11
      };
      response_.set(http::field::content_type, "application/json; charset=utf-8");
      response_.set(http::field::cache_control, "no-store");
      response_.set("X-Content-Type-Options", "nosniff");
      response_.set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'");
      response_.keep_alive(false);
      response_.body()=body;
      response_.prepare_payload();
      stream_.expires_after(std::chrono::seconds(5));
      auto self=shared_from_this();
      http::async_write(stream_, response_, [self](beast::error_code, std::size_t) {
        beast::error_code ec;
        self->stream_.socket().shutdown(tcp::socket::shutdown_both, ec);
        self->stream_.socket().close(ec);
      });
    }
    void received(beast::error_code ec, std::size_t) {
      if(ec) {
        if(ec==http::error::body_limit||ec==http::error::header_limit)respond(http::status::payload_too_large, "{\"status\":\"failed\",\"errors\":[{\"code\":\"E_REQUEST_TOO_LARGE\"}]}");
        return;
      }
      std::string rid=random_id();
      auto started=now_ms();
      int status=200;
      try {
        auto req=parser_.release();
        auto host=std::string(req[http::field::host]);
        require(host=="127.0.0.1:"+std::to_string(port_)||host=="localhost:"+std::to_string(port_), "E_HOST", "Unexpected Host header", 403);
        auto origin=std::string(req[http::field::origin]);
        require(origin.empty(), "E_ORIGIN", "Browser-origin requests are disabled until a same-origin UI is implemented", 403);
        require(req.count(http::field::authorization)<=1, "E_AUTH", "Duplicate authorization header", 401);
        if(req.method()==http::verb::get&&req.target()=="/healthz") {
          Json health=Json::object();
          health.set("ready", true);
          health.set("profile", "offline_bootstrap");
          respond(http::status::ok, envelope(rid, health).dump());
          return;
        }
        require(req.method()==http::verb::post&&req.target()=="/api/v0/call", "E_ROUTE", "Use POST /api/v0/call", 404);
        auto principal=auth_.authenticate(std::string(req[http::field::authorization]));
        auto ct=ascii_lower(std::string(req[http::field::content_type]));
        require(ct=="application/json"||ct=="application/json; charset=utf-8", "E_CONTENT_TYPE", "Expected application/json", 415);
        auto self=shared_from_this();
        auto executor=stream_.get_executor();
        require(requests_.submit([self,principal=std::move(principal),body=std::move(req.body()),rid,started,executor]() mutable {
          self->dispatch(std::move(principal),std::move(body),std::move(rid),started,executor);
        }),"E_REQUEST_BACKPRESSURE","Request execution capacity is full; try again later",503);
        return;
      }catch(const Error&e) {
        status=e.http_status;
        respond(static_cast<http::status>(status), failure(rid,e).dump());
      }catch(const std::exception&) {
        status=500;
        respond(http::status::internal_server_error, "{\"status\":\"failed\",\"errors\":[{\"code\":\"E_INTERNAL\"}]}");
      }
      log_request(rid,status,started);
    }
    public: Session(tcp::socket socket, Service&s, RequestExecutor& requests,const Auth&a, unsigned short port, std::shared_ptr<std::atomic<unsigned>> active) :stream_(std::move(socket)), service_(s), requests_(requests), auth_(a), port_(port), active_(std::move(active)) {
      ++*active_;
      parser_.body_limit(2*1024*1024);
      parser_.header_limit(8192);
    }
    ~Session() {
      --*active_;
    }
    void run() {
      stream_.expires_after(std::chrono::seconds(5));
      auto self=shared_from_this();
      http::async_read(stream_, buffer_, parser_, [self](beast::error_code ec, std::size_t n) {
        self->received(ec, n);
      });
    }
  };
  class Listener {
    net::io_context&io_;
    tcp::acceptor acceptor_;
    Service& service_;
    RequestExecutor& requests_;
    const Auth&auth_;
    std::shared_ptr<std::atomic<unsigned>> active_=std::make_shared<std::atomic<unsigned>>(0);
    public: Listener(net::io_context&io, unsigned short port, Service&s, RequestExecutor& requests,const Auth&a):io_(io), acceptor_(io), service_(s), requests_(requests),auth_(a) {
      tcp::endpoint ep(net::ip::make_address("127.0.0.1"), port);
      acceptor_.open(ep.protocol());
      acceptor_.set_option(net::socket_base::reuse_address(true));
      acceptor_.bind(ep);
      acceptor_.listen(64);
    }
    unsigned short port()const {
      return acceptor_.local_endpoint().port();
    }
    void run() {
      acceptor_.async_accept(net::make_strand(io_), [this](beast::error_code ec, tcp::socket socket) {
        if(!ec) {
          if(active_->load()<64)std::make_shared<Session>(std::move(socket), service_, requests_,auth_, port(), active_)->run();
          else {
            beast::error_code ignored;
            socket.close(ignored);
          }
        }
        if(acceptor_.is_open())run();
      });
    }
  };
}
int main(int argc, char**argv) {
  try {
    std::filesystem::path state=".local/state", fixtures="tests/fixtures", authfile, sources_file, sites_file, worker_token_file;
    std::string worker_url;
    unsigned short port=8610;
    unsigned threads=8;
    unsigned request_threads=0;
    bool worker=true;
    for(int i=1; i<argc; ++i) {
      std::string a=argv[i];
      if(a=="--help") {
        std::cout<<"searchd --auth FILE [--state DIR] [--fixtures DIR] [--port 0..65535] [--threads 1..64] [--no-worker]\n"
                   "        [--request-threads 1..32]\n"
                   "        [--sites FILE] [--worker http://127.0.0.1:PORT --worker-token-file FILE --sources FILE]\n"
                   "Listens on 127.0.0.1 only. Without --worker all external capabilities are disabled.\n"
                   "--threads controls IO; request threads default to min(threads,32). Excess business requests receive 503.\n"
                   "Explicit --sites also governs retained web publication while offline; omission keeps the stored policy.\n";
        return 0;
      }
      if(a=="--version") {
        std::cout<<BZS_VERSION<<'\n';
        return 0;
      }
      if(a=="--no-worker") {
        worker=false;
        continue;
      }
      require(i+1<argc, "E_ARGUMENT", "Missing argument value");
      std::string v=argv[++i];
      if(a=="--state")state=v;
      else if(a=="--fixtures")fixtures=v;
      else if(a=="--auth")authfile=v;
      else if(a=="--worker")worker_url=v;
      else if(a=="--worker-token-file")worker_token_file=v;
      else if(a=="--sources")sources_file=v;
      else if(a=="--sites")sites_file=v;
      else if(a=="--request-threads") {
        unsigned n=0;
        auto[p,e]=std::from_chars(v.data(),v.data()+v.size(),n);
        require(e==std::errc{}&&p==v.data()+v.size()&&n>=1&&n<=32,"E_ARGUMENT","Invalid request thread count");
        request_threads=n;
      }
      else if(a=="--threads") {
        unsigned n=0;
        auto[p, e]=std::from_chars(v.data(), v.data()+v.size(), n);
        require(e==std::errc {
        }
        &&p==v.data()+v.size()&&n>=1&&n<=64, "E_ARGUMENT", "Invalid thread count");
        threads=n;
      }
      else if(a=="--port") {
        unsigned n=0;
        auto[p, e]=std::from_chars(v.data(), v.data()+v.size(), n);
        require(e==std::errc {
        }
        &&p==v.data()+v.size()&&n<=65535, "E_ARGUMENT", "Invalid port");
        port=static_cast<unsigned short>(n);
      }else throw Error("E_ARGUMENT", "Unknown argument: "+a);
    }
    require(!authfile.empty(), "E_AUTH_CONFIG", "--auth FILE is required; use tools/create_auth.py");
    std::filesystem::create_directories(state);
#ifndef _WIN32
    std::filesystem::permissions(state, std::filesystem::perms::owner_all, std::filesystem::perm_options::replace);
#endif
    Auth auth(authfile);
    FixtureCatalog catalog(fixtures);
    Store store(state/"metadata.sqlite");
    auto sites=sites_file.empty()?SiteRegistry{}:SiteRegistry::load(sites_file);
    // An explicit site configuration also governs retained local publication
    // when this process starts without external worker capabilities.
    // Omission keeps the durable policy; an explicit empty registry withdraws
    // previously registered scopes.
    if(!sites_file.empty())store.activate_site_publication(sites);
    std::unique_ptr<HttpWorker> rpc;
    std::unique_ptr<WebService> web;
    if(!worker_url.empty()) {
      require(!worker_token_file.empty()&&!sources_file.empty(), "E_ARGUMENT", "--worker requires --worker-token-file and --sources");
      auto [host, wport]=loopback_url(worker_url);
      auto token=read_file(worker_token_file, 4096);
      while(!token.empty()&&(token.back()=='\n'||token.back()=='\r'||token.back()==' '))token.pop_back();
      require(token.size()>=32, "E_ARGUMENT", "Worker token file must contain a token of at least 32 characters");
      rpc=std::make_unique<HttpWorker>(host, wport, token);
      web=std::make_unique<WebService>(store, *rpc, SourceRegistry::load(sources_file),std::move(sites));
    }
    Service service(store, catalog, state/"backups", web.get());
    if(request_threads==0)request_threads=std::min(threads,32u);
    net::io_context io;
    // Declared after IO so request workers are joined before their response
    // executor is destroyed, including exceptional startup/unwind paths.
    RequestExecutor requests(request_threads);
    Listener listener(io, port, service, requests,auth);
    std::jthread worker_thread;
    if(worker)worker_thread=start_worker(store, catalog, web.get());
    net::signal_set signals(io, SIGINT, SIGTERM);
    signals.async_wait([&](beast::error_code, int) {
      worker_thread.request_stop();
      requests.stop();
      io.stop();
    });
    listener.run();
    Json ready=Json::object();
    ready.set("event", "ready");
    ready.set("host", "127.0.0.1");
    ready.set("port", static_cast<std::int64_t>(listener.port()));
    ready.set("version", BZS_VERSION);
    ready.set("network_mode", web?"worker_mediated":"offline");
    ready.set("threads", static_cast<std::int64_t>(threads));
    ready.set("request_threads",static_cast<std::int64_t>(request_threads));
    std::cout<<ready.dump()<<std::endl;
    std::vector<std::thread> pool;
    for(unsigned t=1; t<threads; ++t)pool.emplace_back([&io] {
      io.run();
    });
    io.run();
    for(auto& t:pool)t.join();
    worker_thread.request_stop();
    requests.stop();requests.join();
    return 0;
  }catch(const Error&e) {
    std::cerr<<e.code<<": "<<e.what()<<'\n';
    return 2;
  }catch(const std::exception&e) {
    std::cerr<<"E_STARTUP: "<<e.what()<<'\n';
    return 2;
  }
}
