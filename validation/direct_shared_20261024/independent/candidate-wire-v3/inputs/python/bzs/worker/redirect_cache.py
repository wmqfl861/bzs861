"""Bounded private HTTP redirect cache receipts and exact-URI conditions.

This module does not decide cache freshness, ownership or crawl admission. A
condition may only be selected after the original GET has traversed the exact
recorded HTTP prefix. It never provides a shortcut to the final URL.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from .revalidation import validate_conditional

CHAIN_VERSION = "bzs-http-redirect-chain-1"
CONDITIONAL_VERSION = "bzs-http-redirect-conditional-1"
MAX_HOPS = 5
MAX_CHAIN_BYTES = 8192
MAX_OBSERVATIONS = 20
MAX_OBSERVATION_BYTES = 524288


def encoded_size(value: Any) -> int:
    # Core Json::canonical uses json-c's default scalar encoding, which escapes
    # every slash in string keys/values. Measure that same private-protocol form
    # without changing the actual JSON transport or the global serializer.
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(encoded.replace("/", "\\/").encode("utf-8"))


def _uri(value: Any) -> bool:
    if (not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 4096 or
            any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None and not parsed.fragment)
    except ValueError:
        return False


def validate_redirect_conditional(value: Any, request_url: str) -> dict:
    """Validate before attempt retention; NetPolicy still checks every dispatch."""
    if (not isinstance(value, dict) or
            set(value) != {"version", "request_url", "redirects", "conditional"} or
            value["version"] != CONDITIONAL_VERSION or not _uri(value["request_url"]) or
            value["request_url"] != request_url or encoded_size(value) > MAX_CHAIN_BYTES):
        raise ValueError("invalid redirect conditional identity or bounds")
    route = value["redirects"]
    if (not isinstance(route, list) or not 1 <= len(route) <= MAX_HOPS or
            any(not _uri(url) for url in route) or
            len(set([request_url, *route])) != len(route) + 1):
        raise ValueError("invalid redirect conditional route")
    conditional = validate_conditional(value["conditional"])
    if conditional["url"] != route[-1]:
        raise ValueError("redirect conditional must identify the exact final URI")
    return {"version": CONDITIONAL_VERSION, "request_url": request_url,
            "redirects": list(route), "conditional": conditional}


def make_chain(request_url: str, final_url: str, hops: list[dict], profile: str) -> dict | None:
    """A missing/oversized receipt grants no redirect cache reuse.

    Policy observations are transported separately even when this receipt does
    not fit. Omitting a reuse grant must not discard a no-store observation.
    """
    if not 1 <= len(hops) <= MAX_HOPS or any(hop["http_freshness"]["request_profile"] != profile for hop in hops):
        return None
    urls = [request_url, *(hop["target_url"] for hop in hops)]
    if (any(not _uri(url) for url in urls) or len(set(urls)) != len(urls) or
            urls[-1] != final_url or any(hop["url"] != urls[i] for i, hop in enumerate(hops))):
        return None
    result = {"version": CHAIN_VERSION, "request_url": request_url,
              "final_url": final_url, "hops": hops}
    return result if encoded_size(result) <= MAX_CHAIN_BYTES else None
