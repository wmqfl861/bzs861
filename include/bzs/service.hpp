#pragma once
#include "bzs/store.hpp"
#include "bzs/web.hpp"
#include <map>
#include <thread>
#include <vector>
namespace bzs {
  struct Principal {
    std::string subject;
    bool admin=false;
  };
  class Auth {
    struct Entry {
      std::string digest;
      Principal principal;
    };
    std::vector<Entry> entries_;
    public: explicit Auth(const std::filesystem::path& config);
    Principal authenticate(std::string_view authorization) const;
  };
  class FixtureCatalog {
    struct Fixture {
      SourceDocument document;
      std::string id, snippet;
    };
    std::map<std::string, Fixture> fixtures_;
    public: explicit FixtureCatalog(const std::filesystem::path& root);
    const SourceDocument& get(const std::string& id) const;
    Json list() const;
    Json search(const Query& query) const;
  };
  class Service {
    Store& store_;
    const FixtureCatalog& fixtures_;
    std::filesystem::path backup_root_;
    WebService* web_=nullptr;
    Json dispatch_active(const Principal& principal,const Json& request);
    public: Service(Store& store, const FixtureCatalog& fixtures, std::filesystem::path backup_root, WebService* web=nullptr) :store_(store), fixtures_(fixtures), backup_root_(std::move(backup_root)), web_(web) {
    }
    Json dispatch(const Principal& principal, const Json& request);
    Json capabilities() const;
  };
  struct BackgroundOptions { unsigned reads=4, maps=2, searches=4; };
  // Bounded pools: one fixture lane plus configurable read/map/search lanes.
  // A slow RPC occupies one worker, not its entire operation class. Destruction stops every lane before
  // joining any one of them; Store and WebService must outlive this handle.
  class BackgroundWorker {
    std::vector<std::jthread> lanes_;
    public:
    BackgroundWorker()=default;
    BackgroundWorker(Store& store,const FixtureCatalog& fixtures,WebService* web,BackgroundOptions options={});
    ~BackgroundWorker();
    BackgroundWorker(const BackgroundWorker&)=delete;
    BackgroundWorker& operator=(const BackgroundWorker&)=delete;
    BackgroundWorker(BackgroundWorker&&) noexcept=default;
    BackgroundWorker& operator=(BackgroundWorker&& other) noexcept;
    bool request_stop() noexcept;
    bool joinable() const noexcept;
    void join();
  };
  BackgroundWorker start_worker(Store& store, const FixtureCatalog& fixtures, WebService* web=nullptr,BackgroundOptions options={});
}
