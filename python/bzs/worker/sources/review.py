"""Execution-time subset of the retained source-manifest contract.

The C++ validator owns the complete contract. The worker independently checks the
policy and identity fields before connecting; a manifest digest also binds both
processes to the same configuration. Candidate probes are explicit and never
change approval metadata.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any
from urllib.parse import urlsplit


class SourceReviewError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise SourceReviewError(message)


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _stamp(value: Any) -> float:
    _need(isinstance(value, str) and bool(re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value)),
        "review timestamp must be an RFC3339 timestamp")
    if value[-1] != "Z":
        _need(int(value[-5:-3]) < 24 and int(value[-2:]) < 60, "invalid review timezone offset")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError) as exc:
        raise SourceReviewError("invalid review timestamp") from exc


def _http_url(value: Any, *, https_only: bool = False) -> None:
    _need(isinstance(value, str) and bool(value), "source URL must be a nonempty string")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise SourceReviewError("invalid source URL") from exc
    _need(parts.scheme in (("https",) if https_only else ("http", "https")) and bool(parts.hostname) and
          parts.username is None and parts.password is None and (port is None or 1 <= port <= 65535),
          "invalid source URL or credentials in authority")


def validate_spec(spec: dict[str, Any]) -> None:
    _need(isinstance(spec, dict), "source must be an object")
    for key in ("id", "provider", "index_family", "capability", "kind"):
        _need(isinstance(spec.get(key), str) and bool(spec[key]), f"missing source {key}")
    _need(bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", spec["id"])), "invalid source id")
    _need(type(spec.get("enabled")) is bool and type(spec.get("default", False)) is bool,
          "source enabled/default must be booleans")
    _need(spec["kind"] in ("api", "scrape"), "invalid source kind")
    params, contract = spec.get("params"), spec.get("contract")
    _need(isinstance(params, dict) and isinstance(contract, dict), "source requires params and contract objects")
    capability = {"encyclopedia": "web", "papers": "scholar"}.get(spec["capability"], spec["capability"])
    for key, value in {"id": spec["id"], "provider_id": spec["provider"], "index_family": spec["index_family"],
                       "capability": capability, "enabled": spec["enabled"],
                       "adapter_id": params.get("adapter"), "endpoint": params.get("base_url")}.items():
        _need(contract.get(key) == value and value is not None, f"source contract identity mismatch: {key}")
    _need(type(contract.get("enabled")) is bool, "contract enabled must be a boolean")
    _http_url(contract["endpoint"])
    _need(isinstance(contract.get("adapter_id"), str) and bool(contract["adapter_id"]), "missing adapter identity")
    limits = contract.get("limits")
    _need(isinstance(limits, dict), "source contract requires limits")
    _need(_integer(limits.get("max_concurrency"), 1, 100), "invalid source concurrency")
    _need(_integer(spec.get("min_interval_ms", 1000), 0, 3600000), "invalid source interval")
    _need(_integer(spec.get("daily_cap", 500), 0, 1000000), "invalid daily source quota")
    _need(limits.get("min_interval_ms") == spec.get("min_interval_ms", 1000) and
          type(limits.get("min_interval_ms")) is int, "source interval differs from contract")
    _need(limits.get("request_quota") == spec.get("daily_cap", 500) and
          type(limits.get("request_quota")) is int and type(limits.get("quota_window_seconds")) is int and
          limits["quota_window_seconds"] == 86400, "S1 source quota must match its daily contract")
    _need(isinstance(limits.get("shared_budget_key"), str) and
          bool(re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", limits["shared_budget_key"])), "invalid shared budget key")
    review = contract.get("review")
    _need(isinstance(review, dict), "source contract requires review metadata")
    _need(review.get("status") in ("candidate", "approved", "rejected", "expired"), "invalid review status")
    _need(review.get("payment_scheme") in ("unverified", "no_payment", "paid_or_trial"), "invalid payment review")
    _need(review.get("automated_access") in ("unverified", "approved", "restricted", "denied"), "invalid access review")
    for name in ("checked_at", "expires_at"):
        if review.get(name) is not None:
            _stamp(review[name])
    if review.get("checked_at") is not None and review.get("expires_at") is not None:
        _need(_stamp(review["checked_at"]) < _stamp(review["expires_at"]), "review expiry precedes its check")


def require_review(spec: dict[str, Any], now: float | None = None) -> None:
    validate_spec(spec)
    contract, now = spec["contract"], time.time() if now is None else now
    review = contract["review"]
    _need(spec["enabled"], "source is disabled")
    _need(review.get("status") == "approved" and review.get("payment_scheme") == "no_payment" and
          review.get("automated_access") == "approved", "source lacks approved free automated access review")
    _need(isinstance(contract.get("logical_service_id"), str) and bool(contract["logical_service_id"]),
          "source lacks logical service identity")
    _need(isinstance(review.get("approved_by"), str) and bool(review["approved_by"]), "review approver is missing")
    _http_url(review.get("terms_url"), https_only=True)
    _need(review.get("cache_policy") == "allowed_with_limits" and
          review.get("redistribution_policy") in ("links_only", "allowed_with_limits"),
          "source needs explicit cache and redistribution review")
    _need(_stamp(review.get("checked_at")) <= now < _stamp(review.get("expires_at")),
          "source review is from the future or has expired")
