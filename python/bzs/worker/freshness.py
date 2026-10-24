"""Bounded response cache fields, independent of optional HTTP validators.

The core interprets these fields. This transport record never grants storage,
publication, or cross-owner reuse. Invalid explicit fields remain conservative.
"""
from __future__ import annotations

import time

VERSION = "bzs-http-freshness-1"
_FIELDS = {"cache_control": "cache-control", "vary": "vary", "date": "date",
           "age": "age", "expires": "expires", "pragma": "pragma"}


def response_freshness(response) -> dict:
    return raw_response_freshness(response.headers, response.final_url, response.request_profile,
                                  int(time.time() * 1000), response.elapsed_ms)


def raw_response_freshness(headers: dict, url: str, profile: str,
                           received_at_ms: int, response_delay_ms: int) -> dict:
    # Body completeness is separate from header validity. The core may retain
    # an explicitly non-publishing bounded prefix, but never reuse it as fresh.
    invalid = False
    values: dict[str, str] = {}
    for key, header in _FIELDS.items():
        value = headers.get(header, "")
        if (not isinstance(value, str) or len(value) > 4096 or not value.isascii() or
                any(ord(char) < 32 or ord(char) == 127 for char in value) or
                header in headers and not value):
            invalid = True
            value = ""
        values[key] = value
    return {"version": VERSION, "url": url,
            "request_profile": profile, **values,
            "received_at_ms": received_at_ms,
            "response_delay_ms": max(0, min(response_delay_ms, 3600000)),
            "invalid": invalid}
