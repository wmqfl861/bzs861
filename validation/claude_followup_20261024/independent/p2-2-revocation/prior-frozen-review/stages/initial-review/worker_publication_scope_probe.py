"""Independent coordinator-boundary probe: no socket, DNS, or real origin call.

Only _fetch_extract is replaced with a completed protocol response. The public
Worker.fetch_extract boundary and real SiteRegistry publication projection run.
The same source is intended for frozen v1 and integrated fixed-code comparison.
"""
import asyncio
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from bzs.worker.netguard import NetPolicy
from bzs.worker.revalidation import request_profile
from bzs.worker.server import Worker


ORIGIN = "https://scope-review.example"
INITIAL = ORIGIN + "/limited/start"
FINAL = ORIGIN + "/open/page"


def site_manifest(prefix):
    return {"version": 1, "sites": [{
        "id": "scope-review", "enabled": True, "origin": ORIGIN,
        "path_prefixes": [prefix],
        "review": {"status": "approved", "payment_scheme": "no_payment",
                   "automated_access": "approved", "checked_at": "1970-01-01T00:00:00Z",
                   "expires_at": "2999-01-01T00:00:00Z", "terms_url": ORIGIN + "/terms",
                   "cache_policy": "allowed_with_limits", "redistribution_policy": "links_only",
                   "approved_by": "independent-scope-probe"},
        "limits": {"max_concurrency": 1, "min_interval_ms": 0,
                   "request_quota": 1000, "quota_window_seconds": 86400},
    }]}


def freshness(target, profile):
    return {"version": "bzs-http-freshness-1", "url": target, "request_profile": profile,
            "cache_control": "max-age=60", "vary": "", "date": "", "age": "",
            "expires": "", "pragma": "", "received_at_ms": 1700000000000,
            "response_delay_ms": 0, "invalid": False}


class CompletedResponseWorker(Worker):
    async def _fetch_extract(self, params, budget, conditional=None, redirect_conditional=None):
        assert budget is None
        self.completed_calls += 1
        return copy.deepcopy(self.completed_response)


async def case(root, name, prefix, *, status=304, direct=False, proof=True, expected_restricted=False):
    sources = root / (name + "-sources.json")
    sites = root / (name + "-sites.json")
    sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
    sites.write_text(json.dumps(site_manifest(prefix)), encoding="utf-8")
    worker = CompletedResponseWorker(str(sources), NetPolicy(), sites_path=str(sites))
    worker.completed_calls = 0
    profile = request_profile(worker.fetcher.request_headers())
    original = FINAL if direct else INITIAL
    cache = {"version": "bzs-http-revalidation-1", "url": FINAL, "request_profile": profile,
             "etag": '"scope-v1"', "last_modified": "", "cache_control": "max-age=60",
             "vary": "", "date": "", "age": "", "expires": ""}
    response = {"status": "not_modified" if status == 304 else "ok", "http_status": status,
                "final_url": FINAL, "redirects": [] if direct else [FINAL],
                "usage": {"external_requests": 1 if direct else 2, "content_bytes": 0 if status == 304 else 30},
                "http_cache": cache, "http_freshness": freshness(FINAL, profile)}
    if status == 200:
        response["publication"] = {"version": "bzs-publication-1", "index_allowed": True,
                                   "snippet_allowed": True, "follow_allowed": True,
                                   "snippet_text": "scopeprobe final text", "snippet_title": "scopeprobe",
                                   "directives": [], "diagnostics": []}
    if not direct and proof:
        hop = {"url": original, "target_url": FINAL, "http_status": 301, "content_bytes": 0,
               "http_freshness": freshness(original, profile)}
        response["http_redirect_chain"] = {"version": "bzs-http-redirect-chain-1",
                                           "request_url": original, "final_url": FINAL, "hops": [hop]}
        response["http_redirect_observations"] = [
            {key: hop[key] for key in ("url", "http_status", "http_freshness")}]
    params = {"url": original, "timeout_ms": 2000}
    if status == 304:
        conditional = {key: cache[key] for key in ("version", "url", "request_profile", "etag", "last_modified")}
        conditional["binding_id"] = hashlib.sha256(b"review-bound-body").hexdigest()
        response["binding_id"] = conditional["binding_id"]
        if direct:
            params["conditional"] = conditional
        else:
            params["redirect_conditional"] = {"version": "bzs-http-redirect-conditional-1",
                                               "request_url": original, "redirects": [FINAL],
                                               "conditional": conditional}
            response["redirect_revalidation"] = {"version": "bzs-http-redirect-conditional-1",
                                                   "request_url": original, "final_url": FINAL,
                                                   "binding_id": conditional["binding_id"]}
    worker.completed_response = response
    result = await worker.fetch_extract(params)
    observed = result.get("publication_observation") if status == 304 else result["publication"]
    restricted = observed is not None and not observed["snippet_allowed"]
    passed = restricted == expected_restricted and worker.completed_calls == 1
    return {"name": name, "passed": passed, "status": result["status"],
            "expected_final_restricted": expected_restricted, "actual_final_restricted": restricted,
            "final_publication_record": observed, "completed_response_calls": worker.completed_calls}


async def main():
    with tempfile.TemporaryDirectory(prefix="bzs-worker-scope-unit-") as temporary:
        root = Path(temporary)
        rows = [
            await case(root, "routed304-prefix-only", "/limited"),
            await case(root, "routed304-final-only", "/open", expected_restricted=True),
            await case(root, "direct304-final-only", "/open", direct=True, expected_restricted=True),
            await case(root, "routed200-prefix-only", "/limited", status=200),
            await case(root, "routed200-final-only", "/open", status=200, expected_restricted=True),
            await case(root, "legacy200-prefix-only", "/limited", status=200, proof=False, expected_restricted=True),
        ]
    print(json.dumps({"scope": "isolated coordinator unit; completed-response stub; no real HTTP",
                      "passed": sum(row["passed"] for row in rows), "total": len(rows), "cases": rows}, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
