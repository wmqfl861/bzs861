"""Shared, non-secret evidence fields for probe recording and offline replay."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

SCHEMA_VERSION = 2
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_HITS = 10

_PARSER_FILES = (
    "worker/sources/__init__.py", "worker/sources/base.py", "worker/sources/apis.py", "worker/sources/engines.py",
    "worker/extract.py", "worker/fetch.py",
)
_DEPENDENCIES = ("aiohttp", "yarl", "idna", "lxml", "trafilatura", "cssselect")
_RESPONSE_HEADERS = frozenset((
    "content-type", "content-language", "content-encoding", "retry-after", "date",
    "x-ratelimit-remaining", "x-ratelimit-reset", "x-robots-tag", "etag", "last-modified",
))
_PARSER_PARAMS = frozenset(("adapter", "base_url", "lang", "region", "site", "site_url"))


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def parser_identity() -> dict:
    root = Path(__file__).resolve().parent
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _PARSER_FILES}
    dependencies = {"python": platform.python_version()}
    for name in _DEPENDENCIES:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "not-installed"
    return {"code_sha256": hashlib.sha256(canonical_json(files)).hexdigest(),
            "files": files, "dependencies": dependencies}


def source_metadata(spec: dict) -> dict:
    """Persist only parser inputs and declared source review; never credential env values."""
    contract = spec["contract"]
    return {"id": spec["id"], "provider": spec["provider"], "index_family": spec["index_family"],
            "capability": spec["capability"], "kind": spec["kind"],
            "adapter_id": spec["params"]["adapter"],
            "params": {key: value for key, value in spec["params"].items() if key in _PARSER_PARAMS},
            "review": {key: value for key, value in contract["review"].items() if key in (
                "status", "payment_scheme", "automated_access", "checked_at", "expires_at",
                "terms_url", "cache_policy", "redistribution_policy", "approved_by")},
            "enabled": spec["enabled"], "budget_key": contract["limits"]["shared_budget_key"],
            "limits": {key: value for key, value in contract["limits"].items() if key in (
                "max_concurrency", "min_interval_ms", "shared_budget_key", "request_quota", "quota_window_seconds")}}


def safe_response_metadata(response) -> dict:
    """Metadata needed to reconstruct FetchResult. Artifact body fields are added by probe."""
    return {"status": response.status, "url": response.url, "final_url": response.final_url,
            "http_status": response.http_status,
            "headers": {key.lower(): value for key, value in response.headers.items()
                        if key.lower() in _RESPONSE_HEADERS},
            "truncated": response.truncated, "redirects": list(response.redirects),
            "elapsed_ms": response.elapsed_ms,
            "external_requests": getattr(response, "external_requests", 0),
            "content_bytes": getattr(response, "content_bytes", len(response.body))}
