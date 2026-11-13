// CL-18: URL identity lattice property tests.
//
// The repository has four URL-identity functions (see docs/URI_DELETION_IDENTITY.md,
// section "URL 身份函数的格"):
//   dedup_key               (src/web.cpp, search-result fusion groups)
//   url_key = map_url       (src/sites.cpp, automatic site admission document key)
//   read_identity           (src/web.cpp; exposed as publication_url_identity,
//                            publication-binding and HTTP cache route identity)
//   deletion_url_key        (src/util.cpp, deletion barrier aliases)
//
// Contract under test (R-01 and later): identity equality is checked on each
// implication's admitted domain:
//   dedup_key(a)==dedup_key(b) => map_url(a)==map_url(b), when both map_url calls succeed
//   map_url(a)==map_url(b) => publication_url_identity(a)==publication_url_identity(b),
//                            when both map_url and both publication calls succeed
//   dedup_key(a)==dedup_key(b) => publication_url_identity(a)==publication_url_identity(b),
//                               when both publication calls succeed, even if map_url rejects
// Only search-entry-admitted members participate in fusion and share one
// publication-check identity; equal raw dedup keys do not imply equal admission.
// dedup_key returns "" as a "no fusion identity" sentinel (the fusion path skips
// such URLs); pairs with an empty key are outside the dedup implications.
// The random corpus below is deterministic (fixed splitmix64 seed); it mixes
// sibling mutations (host case, default ports, fragments, trailing slash, query
// order, percent-escape case, scheme case/scheme, empty path) with independent
// pairs over schemes, hosts (case, punycode, UTF-8, IPv6, blocked), ports
// (default, :080, non-default, non-numeric), paths (dot segments, percent
// escapes, trailing slash, empty), queries (order, duplicates, escapes, empty)
// and fragments.
#include "bzs/error.hpp"
#include "bzs/sites.hpp"
#include "bzs/util.hpp"
#include "bzs/web.hpp"
#include <cctype>
#include <cstdint>
#include <functional>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>
using namespace bzs;
namespace {
using Test = std::pair<std::string, std::function<void()>>;
std::vector<Test> &tests() {
  static std::vector<Test> values;
  return values;
}
struct Add {
  Add(const char *name, std::function<void()> fn) {
    tests().emplace_back(name, std::move(fn));
  }
};
std::size_t assertions = 0;
#define TEST(n)                                                                                    \
  void n();                                                                                        \
  Add reg_##n(#n, n);                                                                              \
  void n()
#define CHECK(c)                                                                                   \
  do {                                                                                             \
    ++assertions;                                                                                  \
    if (!(c))                                                                                      \
      throw std::runtime_error(std::string("check failed: ") + #c + " line " +                     \
                               std::to_string(__LINE__));                                          \
  } while (false)
template <class F> void rejects(F fn, const std::string &code) {
  ++assertions;
  try {
    fn();
  } catch (const Error &error) {
    if (error.code == code)
      return;
    throw std::runtime_error("expected " + code + ", got " + error.code);
  }
  throw std::runtime_error("expected " + code);
}
// Deterministic splitmix64; fixed seed so every run probes the same corpus.
struct Rng {
  std::uint64_t state;
  explicit Rng(std::uint64_t seed) : state(seed) {}
  std::uint64_t next() {
    state += 0x9E3779B97F4A7C15ULL;
    std::uint64_t z = state;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
  }
  std::size_t pick(std::size_t n) {
    return static_cast<std::size_t>(next() % n);
  }
};
// Lowercase http/https is double-weighted: it is the only scheme family any
// of the three identities admits, so the in-domain corpus stays dense while
// uppercase-scheme and non-http forms keep probing the out-of-domain edges.
const char *schemes[] = {"http", "https", "http", "https", "HTTP", "HTTPS", "ftp"};
const char *hosts[] = {"example.com",
                       "EXAMPLE.com",
                       "ExAmPlE.CoM",
                       "www.example.com",
                       "xn--bcher-kva.example",
                       "sub.domain.example",
                       "93.184.216.34",
                       "localhost",
                       "internal.corp",
                       "[::1]",
                       "b\xc3\xbc"
                       "cher.example",
                       "under_score.example"};
const char *ports[] = {"", ":80", ":443", ":8080", ":8443", ":080", ":44300", ":abc"};
const char *paths[] = {"/",       "",       "/a",     "/a/",     "/a/b", "/a/b/", "/%6Diddle",
                       "/middle", "/a%2Fb", "/a%2fb", "/a/../b", "/./b", "/a b",  "/caf%C3%A9"};
const char *queries[] = {"",       "?",    "?a=1",   "?a=1&b=2", "?b=2&a=1", "?a=1&a=2",
                         "?q=%41", "?q=A", "?q=%2f", "?q=%2F",   "?q=a+b",   "?x="};
const char *fragments[] = {"", "#", "#top", "#/route/a"};
struct Url {
  std::string scheme, host, port, path, query, fragment;
  std::string str() const {
    return scheme + "://" + host + port + path + query + fragment;
  }
};
Url random_url(Rng &rng) {
  Url u;
  u.scheme = schemes[rng.pick(sizeof(schemes) / sizeof(schemes[0]))];
  u.host = hosts[rng.pick(sizeof(hosts) / sizeof(hosts[0]))];
  u.port = ports[rng.pick(sizeof(ports) / sizeof(ports[0]))];
  u.path = paths[rng.pick(sizeof(paths) / sizeof(paths[0]))];
  u.query = queries[rng.pick(sizeof(queries) / sizeof(queries[0]))];
  u.fragment = fragments[rng.pick(sizeof(fragments) / sizeof(fragments[0]))];
  return u;
}
std::string upper_host(std::string s) {
  for (char &c : s)
    if (c >= 'a' && c <= 'z')
      c = static_cast<char>(c - 'a' + 'A');
  return s;
}
std::string flip_escape_case(std::string s) {
  auto at = s.rfind('%');
  if (at == std::string::npos || at + 2 >= s.size())
    return s;
  s[at + 1] = static_cast<char>(std::toupper(static_cast<unsigned char>(s[at + 1])));
  s[at + 2] = static_cast<char>(std::toupper(static_cast<unsigned char>(s[at + 2])));
  return s;
}
std::string decode_unreserved(std::string s) {
  auto at = s.rfind("%41");
  if (at == std::string::npos)
    return s;
  return s.substr(0, at) + "A" + s.substr(at + 3);
}
std::string encode_unreserved(std::string s) {
  auto at = s.rfind("q=A");
  if (at == std::string::npos)
    return s;
  return s.substr(0, at + 2) + "%41" + s.substr(at + 3);
}
// One sibling mutation per pair; every mutation stays within normal URL edits
// so the corpus contains both same-key and different-key pairs on each level.
Url mutate(Url u, Rng &rng, std::size_t choice) {
  switch (choice) {
  case 0:
    break; // identical pair
  case 1:
    u.host = ascii_lower(u.host);
    break; // host case
  case 2:
    u.host = upper_host(u.host);
    break; // host case
  case 3:
    if (u.scheme == "http" && u.port.empty())
      u.port = ":80";
    else if (u.scheme == "http" && u.port == ":80")
      u.port = "";
    else if (u.scheme == "https" && u.port.empty())
      u.port = ":443";
    else if (u.scheme == "https" && u.port == ":443")
      u.port = "";
    break; // default port
  case 4:
    if (u.scheme == "http")
      u.scheme = "HTTP";
    else if (u.scheme == "HTTP")
      u.scheme = "http";
    else if (u.scheme == "https")
      u.scheme = "HTTPS";
    else if (u.scheme == "HTTPS")
      u.scheme = "https";
    break; // scheme case
  case 5:
    if (u.scheme == "http" || u.scheme == "HTTP")
      u.scheme = u.scheme == "http" ? "https" : "HTTPS";
    else if (u.scheme == "https" || u.scheme == "HTTPS")
      u.scheme = u.scheme == "https" ? "http" : "HTTP";
    break; // scheme swap
  case 6:
    u.fragment = u.fragment.empty() ? "#top" : "";
    break; // fragment
  case 7:
    u.query = flip_escape_case(u.query);
    break; // escape hex case
  case 8:
    u.query = decode_unreserved(u.query);
    break; // unreserved decode
  case 9:
    u.query = encode_unreserved(u.query);
    break; // unreserved encode
  case 10:
    if (u.path.size() > 1 && u.path.back() == '/')
      u.path.pop_back();
    else if (!u.path.empty() && u.path.back() != '/')
      u.path += '/';
    break; // trailing slash
  case 11:
    u.path = u.path.empty() ? "/" : "";
    break; // empty path vs "/"
  case 12:
    u.query = u.query == "?a=1&b=2" ? "?b=2&a=1" : u.query;
    break; // query order
  case 13:
    u.query += u.query.empty() ? "?utm_source=x" : "&utm_source=x";
    break; // tracking param
  default:
    u = random_url(rng);
    break; // independent pair
  }
  return u;
}
struct Keys {
  std::string dedup; // never throws; "" is the no-identity sentinel
  bool url_ok = false;
  std::string url;
  bool read_ok = false;
  std::string read;
};
Keys evaluate(const std::string &url) {
  Keys k;
  k.dedup = dedup_key(url);
  try {
    k.url = map_url(url);
    k.url_ok = true;
  } catch (const std::exception &) {
  }
  try {
    k.read = publication_url_identity(url);
    k.read_ok = true;
  } catch (const std::exception &) {
  }
  return k;
}
[[noreturn]] void violation(const char *stage, const std::string &a, const std::string &b,
                            const Keys &ka, const Keys &kb) {
  throw std::runtime_error(std::string(stage) + " lattice violation\n  a = " + a + "\n  b = " + b +
                           "\n  dedup(a) = " + (ka.dedup.empty() ? "<empty>" : ka.dedup) +
                           "\n  dedup(b) = " + (kb.dedup.empty() ? "<empty>" : kb.dedup) +
                           "\n  url_key(a) = " + (ka.url_ok ? ka.url : "<rejected>") +
                           "\n  url_key(b) = " + (kb.url_ok ? kb.url : "<rejected>") +
                           "\n  read_identity(a) = " + (ka.read_ok ? ka.read : "<rejected>") +
                           "\n  read_identity(b) = " + (kb.read_ok ? kb.read : "<rejected>"));
}
struct LatticeCounts {
  std::size_t dedup_equal = 0, chain_dedup_url = 0, chain_url_read = 0, direct_dedup_read = 0;
  std::size_t url_equal_dedup_distinct = 0, read_equal_url_distinct = 0;
  std::size_t both_dedup_nonempty = 0, both_url_ok = 0, both_read_ok = 0;
};
void check_lattice_pair(const std::string &a, const std::string &b, LatticeCounts &counts) {
  auto &[dedup_equal, chain_dedup_url, chain_url_read, direct_dedup_read, url_equal_dedup_distinct,
         read_equal_url_distinct, both_dedup_nonempty, both_url_ok, both_read_ok] = counts;
  const auto ka = evaluate(a), kb = evaluate(b);
  if (!ka.dedup.empty())
    ++both_dedup_nonempty; // counts a-side; b tracked below
  const bool dedup_nonempty = !ka.dedup.empty() && !kb.dedup.empty();
  const bool dedup_same = dedup_nonempty && ka.dedup == kb.dedup;
  // Equal nonempty raw dedup keys need not have equal admission (see the
  // length-boundary regression). Check each implication on its own domain.
  if (dedup_same) {
    ++dedup_equal;
    if (ka.url_ok && kb.url_ok) {
      ++chain_dedup_url;
      if (ka.url != kb.url)
        violation("dedup=>url_key", a, b, ka, kb);
    }
    // Independent of map_url admission: read also admits dot-segment paths.
    if (ka.read_ok && kb.read_ok) {
      ++direct_dedup_read;
      if (ka.read != kb.read)
        violation("dedup=>read_identity", a, b, ka, kb);
    }
  }
  if (ka.url_ok && kb.url_ok)
    ++both_url_ok;
  if (ka.read_ok && kb.read_ok)
    ++both_read_ok;
  if (ka.url_ok && kb.url_ok && ka.url == kb.url) {
    // Preserve read-admission symmetry for equal map_url-admitted keys only;
    // the raw length-boundary pair is outside this both-url-admitted domain.
    if (ka.read_ok != kb.read_ok)
      violation("url_key=>read_identity admission", a, b, ka, kb);
    if (ka.read_ok && kb.read_ok) {
      ++chain_url_read;
      if (ka.read != kb.read)
        violation("url_key=>read_identity", a, b, ka, kb);
      if (ka.dedup != kb.dedup)
        ++url_equal_dedup_distinct;
    }
  }
  if (ka.url_ok && kb.url_ok && ka.read_ok && kb.read_ok && ka.read == kb.read && ka.url != kb.url)
    ++read_equal_url_distinct;
}
TEST(url_identity_lattice_raw_length_boundary) {
  const auto p = std::string(4076, 'a');
  const auto a = "https://example.com/" + p;
  const auto b = "https://example.com:443/" + p;
  CHECK(a.size() == 4096);
  CHECK(b.size() == 4100);
  const auto da = dedup_key(a), db = dedup_key(b);
  CHECK(!da.empty() && !db.empty());
  CHECK(da == a);
  CHECK(db == a);
  CHECK(map_url(a) == a);
  CHECK(publication_url_identity(a) == a);
  rejects([&] { map_url(b); }, "E_URL");
  rejects([&] { publication_url_identity(b); }, "E_URL");
  // Characterize pre-fusion search-entry admission, not the end-to-end fuse
  // path or its contribution set: only search-entry-admitted members fuse.
  CHECK(normalize_iri(a) == a);
  check_public_url(a);
  rejects([&] { normalize_iri(b); }, "E_URL");
  // Exercise the same predicate as the random corpus without adding this
  // directed boundary pair to its counts or admission floors.
  LatticeCounts boundary_counts;
  check_lattice_pair(a, b, boundary_counts);
}
TEST(url_identity_lattice_chain_targeted_pairs) {
  // Same fusion group (host case and default ports only) shares every identity.
  for (const auto &[a, b] : std::vector<std::pair<std::string, std::string>>{
           {"https://EXAMPLE.com/a?q=1#f", "https://example.com/a?q=1#f"},
           {"http://example.com:80/a", "http://example.com/a"},
           {"https://Example.COM:443/x", "https://example.com/x"},
       }) {
    const auto ka = evaluate(a), kb = evaluate(b);
    CHECK(ka.dedup == kb.dedup && !ka.dedup.empty());
    CHECK(ka.url_ok && kb.url_ok && ka.url == kb.url);
    CHECK(ka.read_ok && kb.read_ok && ka.read == kb.read);
  }
  // http vs https stay distinct resources on every level (R-01 semantic note).
  {
    const auto ka = evaluate("https://example.com/a"), kb = evaluate("http://example.com/a");
    CHECK(ka.dedup != kb.dedup);
    CHECK(ka.url != kb.url);
    CHECK(ka.read != kb.read);
  }
  // url_key is strictly coarser than dedup_key: these pairs differ for dedup
  // (fragment, empty path, trailing "?", leading-zero port) but merge for
  // url_key and read_identity.
  for (const auto &[a, b] : std::vector<std::pair<std::string, std::string>>{
           {"http://example.com", "http://example.com/"},
           {"http://example.com/a#f", "http://example.com/a"},
           {"http://example.com/a?", "http://example.com/a"},
           {"http://example.com:080/", "http://example.com/"},
       }) {
    const auto ka = evaluate(a), kb = evaluate(b);
    CHECK(ka.dedup != kb.dedup);
    CHECK(ka.url_ok && kb.url_ok && ka.url == kb.url);
    CHECK(ka.read_ok && kb.read_ok && ka.read == kb.read);
  }
  // read_identity is strictly coarser than url_key: query escape hex case and
  // unreserved escapes stay distinct for url_key but merge for read_identity.
  for (const auto &[a, b] : std::vector<std::pair<std::string, std::string>>{
           {"https://example.com/?q=%2f", "https://example.com/?q=%2F"},
           {"https://example.com/?q=%41", "https://example.com/?q=A"},
       }) {
    const auto ka = evaluate(a), kb = evaluate(b);
    CHECK(ka.dedup != kb.dedup);
    CHECK(ka.url_ok && kb.url_ok && ka.url != kb.url);
    CHECK(ka.read_ok && kb.read_ok && ka.read == kb.read);
  }
}
TEST(url_identity_lattice_sentinels_and_domains) {
  // dedup_key: "" means "no fusion identity"; fusion skips such URLs.
  CHECK(dedup_key("ftp://example.com/").empty());
  CHECK(dedup_key("HTTPS://example.com/a").empty()); // scheme case is not folded
  CHECK(dedup_key("http://user@example.com/").empty());
  CHECK(!dedup_key("https://example.com/a").empty());
  // Non-lowercase-scheme and non-http URLs are outside map_url/read_identity
  // admission as well, so the empty sentinel never hides an in-domain split.
  rejects([&] { map_url("HTTPS://example.com/a"); }, "E_URL");
  rejects([&] { publication_url_identity("HTTPS://example.com/a"); }, "E_URL");
  rejects([&] { map_url("ftp://example.com/"); }, "E_URL");
  // map_url admits no dot segments; read_identity resolves them, so both URLs
  // below stay outside the url_key domain while sharing a read identity.
  rejects([&] { map_url("https://example.com/a/../b"); }, "E_SCOPE");
  CHECK(publication_url_identity("https://example.com/a/../b") == "https://example.com/b");
  CHECK(dedup_key("https://example.com/a/../b") != dedup_key("https://example.com/b"));
}
TEST(url_identity_deletion_key_position) {
  // deletion_url_key decodes unreserved escapes and uppercases reserved ones
  // (like read_identity) instead of keeping them verbatim (unlike url_key).
  CHECK(deletion_url_key("https://example.com/?q=%2f") ==
        deletion_url_key("https://example.com/?q=%2F"));
  CHECK(deletion_url_key("https://example.com/%6Diddle") ==
        deletion_url_key("https://example.com/middle"));
  // It keeps dot segments distinct (finer than read_identity there)...
  CHECK(deletion_url_key("https://example.com/a/../b") !=
        deletion_url_key("https://example.com/b"));
  CHECK(publication_url_identity("https://example.com/a/../b") ==
        publication_url_identity("https://example.com/b"));
  // ...and http/https stay distinct resources, like every other identity.
  CHECK(deletion_url_key("http://example.com/a") != deletion_url_key("https://example.com/a"));
  // Hosts outside metadata_host's grammar fall back to the exact raw string,
  // so a dedup-equal pair can carry different deletion keys there.
  CHECK(dedup_key("http://under_score.example/a") == dedup_key("http://UNDER_SCORE.example/a"));
  CHECK(deletion_url_key("http://under_score.example/a") !=
        deletion_url_key("http://UNDER_SCORE.example/a"));
  rejects([&] { map_url("http://under_score.example/a"); }, "E_URL");
  rejects([&] { publication_url_identity("http://under_score.example/a"); }, "E_URL");
  // Same fusion group (case + default port) shares the deletion key whenever
  // the URL is inside the metadata grammar.
  CHECK(deletion_url_key("https://EXAMPLE.com:443/a?q=1") ==
        deletion_url_key("https://example.com/a?q=1"));
}
TEST(url_identity_lattice_random_property) {
  Rng rng{0xC0FFEE18C0DE2026ULL};
  constexpr std::size_t pairs = 12000;
  static_assert(pairs >= 10000, "CL-18 requires at least 10000 random pairs");
  LatticeCounts counts;
  auto &[dedup_equal, chain_dedup_url, chain_url_read, direct_dedup_read, url_equal_dedup_distinct,
         read_equal_url_distinct, both_dedup_nonempty, both_url_ok, both_read_ok] = counts;
  for (std::size_t i = 0; i < pairs; ++i) {
    const auto first = random_url(rng);
    const auto second = mutate(first, rng, rng.pick(15)); // 0..14; 14 = independent
    const auto a = first.str(), b = second.str();
    check_lattice_pair(a, b, counts);
  }
  std::cout << "url_identity_lattice corpus: " << pairs << " pairs, " << both_dedup_nonempty
            << " non-empty dedup keys (a side), " << both_url_ok << " pairs url_key-admitted, "
            << both_read_ok << " pairs read-admitted, " << dedup_equal << " dedup-equal, "
            << chain_dedup_url << " checked dedup=>url_key, " << chain_url_read
            << " checked url_key=>read, " << direct_dedup_read << " checked dedup=>read, "
            << url_equal_dedup_distinct << " url_key-equal/dedup-distinct, "
            << read_equal_url_distinct << " read-equal/url_key-distinct\n";
  // The corpus must actually exercise every level of the lattice; a generator
  // degenerate enough to never produce equal keys would make this test hollow.
  CHECK(dedup_equal > 0);
  CHECK(chain_dedup_url > 0);
  CHECK(chain_url_read > 0);
  CHECK(direct_dedup_read > 0);
  CHECK(url_equal_dedup_distinct > 0);
  CHECK(read_equal_url_distinct > 0);
  // Floors sit below the fixed-seed corpus' exact counts (2519 url-admitted,
  // 3592 read-admitted) so a generator or admission change that hollows out
  // the corpus fails here instead of silently testing nothing.
  CHECK(both_url_ok >= 1500);
  CHECK(both_read_ok >= 2500);
}
} // namespace
int main() {
  std::size_t failures = 0;
  for (const auto &[name, test] : tests()) {
    try {
      test();
      std::cout << "PASS " << name << '\n';
    } catch (const std::exception &error) {
      ++failures;
      std::cerr << "FAIL " << name << ": " << error.what() << '\n';
    }
  }
  std::cout << tests().size() << " url identity lattice tests, " << assertions << " assertions, "
            << failures << " failures\n";
  return failures ? 1 : 0;
}
