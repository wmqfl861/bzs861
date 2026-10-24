"""Additive independent contract probe after the original full-suite failure.

The old frozen probe expected final-only publication for routed 200. Its source
and results remain unchanged, but that expectation is not the public Worker
contract. This probe requires the original aggregate publication and a distinct
versioned final-only record whenever the two policies differ. A complete proof
may omit the new record when its final policy equals the aggregate. No existing
formal test is imported/edited.
Only the completed-fetch boundary is stubbed; no socket or DNS request occurs.
"""
import asyncio
import copy
import importlib.util
import json
import tempfile
from pathlib import Path


PREVIOUS = Path("/tmp/bzs-p2-redirect-review-revocation-v1/worker_publication_scope_probe.py")
SPEC = importlib.util.spec_from_file_location("frozen_scope_fixture", PREVIOUS)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
RECORD_VERSION = "bzs-http-representation-publication-1"


def publication(denied=False, empty=False):
    return {"version": "bzs-publication-1", "index_allowed": not denied,
            "snippet_allowed": not denied, "follow_allowed": True,
            "snippet_text": "" if denied or empty else "scopeprobe final text",
            "snippet_title": "" if denied or empty else "scopeprobe",
            "directives": ["noindex"] if denied else [], "diagnostics": []}


async def run_case(directory, spec):
    name, prefix, expected_aggregate, expected_final = spec["name"], spec["prefix"], spec.get("aggregate"), spec.get("final")
    direct, proof = spec.get("direct", False), spec.get("proof", True)
    status, empty, truncated = spec.get("status", 200), spec.get("empty", False), spec.get("truncated", False)
    denied = spec.get("denied", False)
    sources, sites = directory / (name + "-sources.json"), directory / (name + "-sites.json")
    sources.write_text('{"version":1,"sources":[]}', encoding="utf-8")
    sites.write_text(json.dumps(FIXTURE.site_manifest(prefix)), encoding="utf-8")
    worker = FIXTURE.CompletedResponseWorker(str(sources), FIXTURE.NetPolicy(), sites_path=str(sites))
    worker.completed_calls = 0
    profile = FIXTURE.request_profile(worker.fetcher.request_headers())
    original = FIXTURE.FINAL if direct else FIXTURE.INITIAL
    targets = [] if direct else ([FIXTURE.ORIGIN + "/middle/hop", FIXTURE.FINAL] if spec.get("intermediate") else [FIXTURE.FINAL])
    cache = {"version": "bzs-http-revalidation-1", "url": FIXTURE.FINAL, "request_profile": profile,
             "etag": '"scope-v1"', "last_modified": "", "cache_control": "max-age=60",
             "vary": "", "date": "", "age": "", "expires": ""}
    response = {"status": "not_modified" if status == 304 else "empty_content" if empty else "ok",
                "http_status": status, "final_url": FIXTURE.FINAL, "redirects": targets,
                "usage": {"external_requests": len(targets) + 1, "content_bytes": 0 if status == 304 else 30},
                "http_freshness": FIXTURE.freshness(FIXTURE.FINAL, profile)}
    if status in (200, 304):
        response["http_cache"] = cache
    if status != 304:
        response.update(html="scopeprobe final text", text="" if empty else "scopeprobe final text",
                        title="scopeprobe", content_type="text/plain", extractor="review/1", extract_status="empty" if empty else "ok",
                        index_allowed=not denied, links=[], metadata={}, warnings=["download_truncated"] if truncated else [])
        response["publication"] = publication(denied, empty)
    if denied:
        response["publication_observation"] = publication(True)
    if not direct and proof:
        hops, observed, previous = [], [], original
        for target in targets:
            hop = {"url": previous, "target_url": target, "http_status": 301, "content_bytes": 0,
                   "http_freshness": FIXTURE.freshness(previous, profile)}
            hops.append(hop)
            observed.append({key: hop[key] for key in ("url", "http_status", "http_freshness")})
            previous = target
        response["http_redirect_chain"] = {"version": "bzs-http-redirect-chain-1", "request_url": original,
                                           "final_url": FIXTURE.FINAL, "hops": hops}
        response["http_redirect_observations"] = observed
    params = {"url": original, "timeout_ms": 2000}
    if status == 304:
        conditional = {key: cache[key] for key in ("version", "url", "request_profile", "etag", "last_modified")}
        conditional["binding_id"] = "b" * 64
        response["binding_id"] = conditional["binding_id"]
        if direct:
            params["conditional"] = conditional
        else:
            params["redirect_conditional"] = {"version": "bzs-http-redirect-conditional-1", "request_url": original,
                                               "redirects": targets, "conditional": conditional}
            response["redirect_revalidation"] = {"version": "bzs-http-redirect-conditional-1", "request_url": original,
                                                   "final_url": FIXTURE.FINAL, "binding_id": conditional["binding_id"]}
    worker.completed_response = copy.deepcopy(response)
    result = await worker.fetch_extract(params)
    failures = []
    if worker.completed_calls != 1:
        failures.append("unexpected additional completed fetch")
    wrapper = result.get("representation_publication")
    if status == 304:
        if "publication" in result or wrapper is not None:
            failures.append("304 carried a body publication grant")
        observed = result.get("publication_observation")
        final_restricted = observed is not None and not observed["snippet_allowed"]
        if final_restricted != expected_final:
            failures.append("304 final observation scope changed")
        aggregate = None
    else:
        aggregate = result["publication"]
        if (not aggregate["snippet_allowed"]) != expected_aggregate:
            failures.append("public publication lost its original aggregate site ceiling")
        if expected_aggregate and (aggregate["snippet_text"] or aggregate["snippet_title"]):
            failures.append("aggregate restriction exposed snippet text/title")
        eligible = proof and not direct and status == 200 and not empty and not truncated
        if eligible:
            if wrapper is None:
                final_policy = aggregate
            elif (not isinstance(wrapper, dict) or set(wrapper) != {"version", "url", "publication"} or
                    wrapper.get("version") != RECORD_VERSION or wrapper.get("url") != FIXTURE.FINAL):
                failures.append("missing or malformed exact-final private publication identity")
                final_policy = None
            else:
                final_policy = wrapper["publication"]
            if final_policy is not None:
                if (not final_policy["snippet_allowed"]) != expected_final:
                    failures.append("private final policy inherited an unrelated site ceiling or lost its own")
                if final_policy["index_allowed"] != (not denied):
                    failures.append("private final policy lost actual final noindex")
                if expected_final and (final_policy["snippet_text"] or final_policy["snippet_title"]):
                    failures.append("restricted final record exposed snippet text/title")
                if not expected_final and (final_policy["snippet_text"] != "scopeprobe final text" or final_policy["snippet_title"] != "scopeprobe"):
                    failures.append("allowed independent final record lost its safe text/title")
        elif wrapper is not None:
            failures.append("ineligible/direct/legacy/incomplete response received a new final publication grant")
    return {"name": name, "passed": not failures, "failures": failures, "status": result["status"],
            "public_aggregate": aggregate, "private_final": wrapper,
            "final_observation": result.get("publication_observation"), "completed_fetch_calls": worker.completed_calls}


async def main():
    cases = [
        {"name": "200-original-only", "prefix": "/limited", "aggregate": True, "final": False},
        {"name": "200-final-only", "prefix": "/open", "aggregate": True, "final": True},
        {"name": "200-intermediate-only", "prefix": "/middle", "intermediate": True, "aggregate": True, "final": False},
        {"name": "200-all-route", "prefix": "/", "aggregate": True, "final": True},
        {"name": "200-unrelated-scope", "prefix": "/unrelated", "aggregate": False, "final": False},
        {"name": "200-final-noindex", "prefix": "/limited", "denied": True, "aggregate": True, "final": True},
        {"name": "304-original-only", "prefix": "/limited", "status": 304, "final": False},
        {"name": "304-final-only", "prefix": "/open", "status": 304, "final": True},
        {"name": "direct304-final-only", "prefix": "/open", "status": 304, "direct": True, "final": True},
        {"name": "legacy200-original-only", "prefix": "/limited", "proof": False, "aggregate": True, "final": None},
        {"name": "direct200-final-only", "prefix": "/open", "direct": True, "aggregate": True, "final": None},
        {"name": "203-original-only", "prefix": "/limited", "status": 203, "aggregate": True, "final": None},
        {"name": "empty200-original-only", "prefix": "/limited", "empty": True, "aggregate": True, "final": None},
        {"name": "truncated200-original-only", "prefix": "/limited", "truncated": True, "aggregate": True, "final": None},
    ]
    with tempfile.TemporaryDirectory(prefix="bzs-aggregate-contract-") as temporary:
        rows = [await run_case(Path(temporary), spec) for spec in cases]
    print(json.dumps({"scope": "independent additive public-aggregate/private-final contract; completed-fetch stub, no HTTP",
                      "passed": sum(row["passed"] for row in rows), "total": len(rows), "cases": rows}, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
