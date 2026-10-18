"""Independent site-review and path-scope checks for automatic discovery.

Source adapters and site discovery have separate manifests. An approved search
adapter never grants automatic access to every result URL it returns.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yarl import URL

from .fetch import FetchAdmissionDenied
from .netguard import NetPolicy, PolicyError
from .sources.review import SourceReviewError, _http_url, _stamp

EMPTY_MANIFEST = b'{"version":1,"sites":[]}'
MAX_MANIFEST_BYTES = 1024 * 1024
_SITE_KEYS = frozenset(("id", "enabled", "origin", "path_prefixes", "review", "limits"))
_REVIEW_KEYS = frozenset(("status", "payment_scheme", "automated_access", "checked_at", "expires_at",
                          "terms_url", "cache_policy", "redistribution_policy", "approved_by"))
_LIMIT_KEYS = frozenset(("max_concurrency", "min_interval_ms", "request_quota", "quota_window_seconds"))
_AMBIGUOUS_PATH = re.compile(r"%(?:2e|2f|5c|25)", re.I)
_SITE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}\Z")


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate site manifest key: " + key)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite number in site manifest")


def canonical_map_url(value: str, policy: NetPolicy) -> URL:
    """Reject ambiguous paths before yarl can remove dot segments or re-encode."""
    if not isinstance(value, str):
        raise PolicyError("map URL must be a string")
    if "\\" in value:
        raise PolicyError("backslashes are not allowed in map URLs")
    try:
        parts = urlsplit(value)
        path = parts.path
    except ValueError as exc:
        raise PolicyError("invalid map URL") from exc
    if any(part in (".", "..") for part in path.split("/")) or _AMBIGUOUS_PATH.search(path):
        raise PolicyError("ambiguous encoded or dot-segment map path")
    checked = policy.prepare_url(value)
    rest = (path or "/") + (("?" + parts.query) if parts.query else "")
    if any(char in rest for char in '[]<>"`{}|^') or re.search(r"%(?![0-9A-Fa-f]{2})", rest):
        raise PolicyError("invalid character or percent escape in map URL")
    # Core uses the same simple UTF-8/space URI conversion. Preserve every
    # already encoded octet and query ordering; yarl's default constructor would
    # otherwise turn /%61?x=%62 into /a?x=b and change durable item identity.
    rest = "".join("".join(f"%{octet:02X}" for octet in char.encode("utf-8"))
                   if ord(char) > 127 or char == " " else char for char in rest)
    target = URL(str(checked.origin()) + rest, encoded=True)
    if (target.scheme, target.raw_host, target.port) != (checked.scheme, checked.raw_host, checked.port):
        raise PolicyError("encoded map URL changed its validated authority")
    if len(str(target)) > 4096:
        raise PolicyError("canonical map URL is too long")
    return target


def in_path_scope(target: URL, prefixes: list[str]) -> bool:
    path = target.raw_path
    return any(prefix == "/" or path == prefix.rstrip("/") or
               path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


class SiteRegistry:
    def __init__(self, path: str | Path | None, policy: NetPolicy):
        self.policy = policy
        if path is None:
            raw = EMPTY_MANIFEST
        else:
            with Path(path).open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
        _need(len(raw) <= MAX_MANIFEST_BYTES, "site manifest exceeds 1 MiB")
        try:
            data = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
        except (UnicodeError, RecursionError) as exc:
            raise ValueError("invalid site manifest JSON") from exc
        _need(isinstance(data, dict) and set(data) == {"version", "sites"} and
              type(data["version"]) is int and data["version"] == 1, "unsupported site manifest version or fields")
        _need(isinstance(data["sites"], list) and len(data["sites"]) <= 100,
              "site manifest must contain at most 100 sites")
        self.digest = hashlib.sha256(raw).hexdigest()
        self.sites: dict[str, dict[str, Any]] = {}
        origins: set[str] = set()
        for spec in data["sites"]:
            self._validate(spec)
            _need(spec["id"] not in self.sites, "duplicate site id")
            canonical_origin = str(policy.prepare_url(spec["origin"]).origin())
            _need(canonical_origin not in origins, "duplicate canonical site origin")
            origins.add(canonical_origin)
            self.sites[spec["id"]] = {**spec, "origin": canonical_origin,
                                       "path_prefixes": [prefix.rstrip("/") or "/" for prefix in spec["path_prefixes"]]}
        # One actual request per site lane in this release. Core persists the
        # same one-active-hop constraint; this additionally orders worker starts.
        self.lanes = {site_id: asyncio.Semaphore(1) for site_id in self.sites}
        self.last_dispatch: dict[str, float] = {}
        self.cooldown_until: dict[str, float] = {}

    def _validate(self, spec: Any) -> None:
        _need(isinstance(spec, dict) and set(spec) == _SITE_KEYS, "invalid site fields")
        _need(isinstance(spec["id"], str) and bool(_SITE_ID.fullmatch(spec["id"])), "invalid site id")
        _need(type(spec["enabled"]) is bool, "site enabled must be a boolean")
        origin = spec["origin"]
        _need(isinstance(origin, str) and origin and not origin.endswith("/"), "site origin must not end with a slash")
        canonical = canonical_map_url(origin, self.policy)
        parts = urlsplit(origin)
        _need(not parts.path and not parts.query and not parts.fragment, "site origin cannot contain path, query or fragment")
        _need(canonical.raw_path == "/", "invalid site origin")
        prefixes = spec["path_prefixes"]
        _need(isinstance(prefixes, list) and 1 <= len(prefixes) <= 32, "site needs 1 to 32 path prefixes")
        for prefix in prefixes:
            _need(isinstance(prefix, str) and 1 <= len(prefix) <= 2048 and prefix.startswith("/") and
                  all(ord(c) > 32 and ord(c) < 127 for c in prefix) and
                  not any(c in prefix for c in ("%", "\\", "?", "#")) and
                  not any(part in (".", "..") for part in prefix.split("/")), "invalid site path prefix")
        _need(len({prefix.rstrip("/") or "/" for prefix in prefixes}) == len(prefixes), "duplicate site path prefix")
        limits = spec["limits"]
        _need(isinstance(limits, dict) and set(limits) == _LIMIT_KEYS, "invalid site limits")
        _need(type(limits["max_concurrency"]) is int and limits["max_concurrency"] == 1,
              "site max_concurrency must be 1 in this version")
        _need(_integer(limits["min_interval_ms"], 0, 3600000), "invalid site minimum interval")
        _need(_integer(limits["request_quota"], 1, 1000000), "invalid site request quota")
        _need(type(limits["quota_window_seconds"]) is int and limits["quota_window_seconds"] == 86400,
              "site quota window must be 86400 seconds")
        review = spec["review"]
        _need(isinstance(review, dict) and set(review) <= _REVIEW_KEYS and
              {"status", "payment_scheme", "automated_access", "checked_at", "terms_url"} <= set(review), "invalid site review fields")
        _need(review["status"] in ("candidate", "approved", "rejected", "expired"), "invalid site review status")
        _need(review["payment_scheme"] in ("unverified", "no_payment", "paid_or_trial"), "invalid site payment review")
        _need(review["automated_access"] in ("unverified", "approved", "restricted", "denied"), "invalid automated access review")
        if "cache_policy" in review:
            _need(review["cache_policy"] in ("unverified", "disallow", "allowed_with_limits"), "invalid site cache policy")
        if "redistribution_policy" in review:
            _need(review["redistribution_policy"] in ("unverified", "links_only", "allowed_with_limits"), "invalid site redistribution policy")
        if review.get("approved_by") is not None:
            _need(isinstance(review["approved_by"], str) and bool(review["approved_by"]), "invalid site review approver")
        if review["terms_url"] is not None:
            _http_url(review["terms_url"], https_only=True)
        for key in ("checked_at", "expires_at"):
            if review.get(key) is not None:
                _stamp(review[key])
        if review.get("checked_at") is not None and review.get("expires_at") is not None:
            _need(_stamp(review["checked_at"]) < _stamp(review["expires_at"]), "site review expires before its check")

    def require(self, site_id: str, digest: str, target: URL, *, metadata: bool = False,
                now: float | None = None) -> dict[str, Any]:
        """Check immutable identity plus review freshness for this exact dispatch."""
        now = time.time() if now is None else now
        if not isinstance(digest, str) or not hmac.compare_digest(digest, self.digest):
            raise FetchAdmissionDenied("E_SITE_MANIFEST_MISMATCH", "site manifest mismatch between core and worker")
        spec = self.sites.get(site_id)
        if spec is None:
            raise FetchAdmissionDenied("E_SITE_REVIEW", "site is not registered")
        review = spec["review"]
        try:
            _need(spec["enabled"] and review["status"] == "approved" and
                  review["payment_scheme"] == "no_payment" and review["automated_access"] == "approved",
                  "site lacks enabled, approved free automated access")
            _need(isinstance(review.get("approved_by"), str) and 0 < len(review["approved_by"]) <= 256,
                  "site review approver is missing")
            _http_url(review.get("terms_url"), https_only=True)
            _need(review.get("cache_policy") == "allowed_with_limits" and
                  review.get("redistribution_policy") in ("links_only", "allowed_with_limits"),
                  "site lacks cache and link redistribution approval")
            _need(_stamp(review.get("checked_at")) <= now < _stamp(review.get("expires_at")),
                  "site review is not currently valid")
        except (ValueError, SourceReviewError) as exc:
            raise FetchAdmissionDenied("E_SITE_REVIEW", str(exc)) from exc
        if str(target.origin()) != spec["origin"]:
            raise FetchAdmissionDenied("E_SITE_SCOPE", "target origin differs from the reviewed site")
        if not metadata and not in_path_scope(target, spec["path_prefixes"]):
            raise FetchAdmissionDenied("E_SITE_SCOPE", "target path is outside the reviewed site scope")
        if self.cooldown_until.get(site_id, 0) > time.monotonic():
            raise FetchAdmissionDenied("E_SITE_COOLDOWN", "site is cooling down")
        return spec
