#pragma once
#include "bzs/store.hpp"
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
    public: Service(Store& store, const FixtureCatalog& fixtures, std::filesystem::path backup_root) :store_(store), fixtures_(fixtures), backup_root_(std::move(backup_root)) {
    }
    Json dispatch(const Principal& principal, const Json& request);
    static Json capabilities();
  };
  std::jthread start_worker(Store& store, const FixtureCatalog& fixtures);
}
