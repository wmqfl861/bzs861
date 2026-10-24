"""Independent P2-1 Python metadata/header/robots boundaries; no sockets opened."""
from pathlib import Path
import hashlib
import importlib
import json
import sys
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "python"))
from bzs.worker.fetch import Fetcher
from bzs.worker.netguard import NetPolicy
from bzs.worker.revalidation import (VERSION, ConditionalMismatch, merge_cache, request_profile,
                                     response_cache, validate_cache, validate_conditional)
from bzs.worker.freshness import response_freshness
from bzs.worker.robots import (CRAWLER_USER_AGENT, POLICY_VERSION, _remaining_freshness,
                               response_record, rule_decision)
from yarl import URL

checks = 0
def check(value):
    global checks
    checks += 1
    assert value

def rejects(fn, exc=ValueError):
    try:
        fn()
    except exc:
        check(True)
        return
    raise AssertionError("expected " + exc.__name__)

url = "https://independent-vary.example/page"
now = 1_800_000_000_000
fetcher = Fetcher(NetPolicy())
headers = fetcher.request_headers()
check(headers["Accept-Encoding"] == "gzip, deflate")
profile = request_profile(headers)
check(profile == request_profile({key.upper(): value for key, value in headers.items()}))
check(profile == request_profile({**headers, "If-None-Match": '"bound-body"', "If-Modified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"}))
for name, value in (("Accept-Encoding", "identity"), ("Accept", "text/plain"), ("User-Agent", "Changed"), ("Accept-Language", "en")):
    changed = fetcher.request_headers(headers={name.lower(): value})
    check(request_profile(changed) != profile)
    check(len([key for key in changed if key.lower() == name.lower()]) == 1)
base = response_cache({"etag": '"bound-body"', "cache-control": "max-age=60", "vary": "Accept-Encoding"}, url=url, profile=profile)
conditional = validate_conditional({key: base[key] for key in ("version", "url", "request_profile", "etag", "last_modified")} | {"binding_id": "b" * 64})
for row in json.loads(Path(sys.argv[2]).read_text()):
    value = {**base, "vary": row["value"]}
    if row["accepted"]:
        check(validate_cache(value) == value)
    else:
        rejects(lambda: validate_cache(value))
updates = response_cache({}, url=url, profile=profile, conditional=conditional)
check(updates["vary"] == "")
check(merge_cache(base, updates)["vary"] == "Accept-Encoding")
rejects(lambda: response_cache({"vary": ""}, url=url, profile=profile, conditional=conditional))
rejects(lambda: response_cache({}, url=url, profile=request_profile({**headers, "Accept-Encoding": "identity"}), conditional=conditional), ConditionalMismatch)
for control in ("no-store", "no-cache", "max-age=0", "private, max-age=60"):
    rejects(lambda: response_cache({"cache-control": control}, url=url, profile=profile, conditional=conditional))
for value in ("", "Accept-Encoding, Cookie", "*", "Accept-Encoding,", "Accept-Encoding\t"):
    rejects(lambda: response_cache({"vary": value}, url=url, profile=profile, conditional=conditional))
explicit_empty = response_freshness(SimpleNamespace(headers={"cache-control": "max-age=60", "vary": ""}, final_url=url, request_profile=profile, elapsed_ms=0))
check(explicit_empty["invalid"])
absent = response_freshness(SimpleNamespace(headers={"cache-control": "max-age=60"}, final_url=url, request_profile=profile, elapsed_ms=0))
check(not absent["invalid"] and absent["vary"] == "")
robots_headers = fetcher.request_headers(headers={"User-Agent": CRAWLER_USER_AGENT}, accept="text/plain")
check(robots_headers["User-Agent"] == CRAWLER_USER_AGENT)
check(robots_headers["Accept"] == "text/plain" and robots_headers["Accept-Encoding"] == "gzip, deflate")
body = b"User-agent: BzsBot\nDisallow: /private\n"
for vary in (None, "Accept-Encoding", "ACCEPT-ENCODING, accept-encoding", "User-Agent", "Accept", "User-Agent, Accept, Accept-Encoding"):
    wire = {"content-type": "text/plain", "cache-control": "max-age=60"}
    if vary is not None:
        wire["vary"] = vary
    record, _ = response_record(origin="https://independent-vary.example", status="ok", http_status=200,
                                headers=wire, body=body, truncated=False, now_ms=now)
    check(record["version"] == POLICY_VERSION and record["state"] == "rules")
    check(record["expires_at_ms"] - record["fetched_at_ms"] == 60_000)
    check(record["body_sha256"] == hashlib.sha256(body).hexdigest())
    check(not rule_decision(record, URL("https://independent-vary.example/private"))["allowed"])
    check(rule_decision(record, URL("https://independent-vary.example/public"))["allowed"])
    check("request_profile" not in record)
for vary in ("", " ", "*", "Accept-Encoding,Cookie", "Accept-Encoding, Accept-Language", "Accept-Encoding,", "Accept-Encoding\t"):
    record, _ = response_record(origin="https://independent-vary.example", status="ok", http_status=200,
                                headers={"content-type": "text/plain", "cache-control": "max-age=60", "vary": vary},
                                body=body, truncated=False, now_ms=now)
    check(record["state"] == "deny_all")
    check(not rule_decision(record, URL("https://independent-vary.example/public"))["allowed"])
for control in ("no-store", "no-cache", "max-age=0"):
    check(_remaining_freshness({"cache-control": control, "vary": "Accept-Encoding"}, now, 86400000, 0) == 0)
modules = {}
for name in ("bzs.worker.fetch", "bzs.worker.netguard", "bzs.worker.revalidation", "bzs.worker.freshness", "bzs.worker.robots"):
    path = Path(importlib.import_module(name).__file__).resolve()
    modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
print(json.dumps({"checks": checks, "modules": modules, "header_profiles_bound": True,
                  "robots_fixed_header_assumption_explicit": True,
                  "fresh_robots_record_has_profile": False,
                  "external_network_requests": 0}, indent=2))
