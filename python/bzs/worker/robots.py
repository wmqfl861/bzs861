"""Versioned, bounded Robots Exclusion Protocol parsing and request matching.

This module does not fetch, grant site permission, or bypass NetPolicy. The
durable core owns retrieval/caching; the worker rechecks the resulting policy at
the actual HTTP dispatch boundary. Invalid encodings and unrepresentable rules
fail closed instead of turning a damaged Disallow into an empty allow policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from email.utils import parsedate_to_datetime
from typing import Any

from yarl import URL

from .fetch import FetchAdmissionDenied
from .sources.base import retry_after_ms

POLICY_VERSION = "bzs-robots-1"
PRODUCT_TOKEN = "BzsBot"
CRAWLER_USER_AGENT = "BzsBot/0.1.3 (bounded site discovery; https://github.com/wmqfl861/bzs861)"
MAX_ROBOTS_BYTES = 512 * 1024
MAX_RULES = 32768
MAX_PATTERN_BYTES = 4096
MAX_CACHE_AGE_MS = 24 * 60 * 60 * 1000
MAX_RECORD_BYTES = 768 * 1024
_PRODUCT = re.compile(r"[A-Za-z_-]+\Z")
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEX = frozenset("0123456789abcdefABCDEF")


def _normalize(value: str, *, pattern: bool) -> str:
    """Compare ASCII representations; encoded reserved bytes remain encoded.

    Raw '*' and a terminal '$' are controls only in a rule. A percent-encoded
    literal %2A/%24 never becomes such a control. Unicode is compared as its
    percent-encoded UTF-8 bytes, and unreserved percent escapes are decoded.
    """
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%" and index + 2 < len(value) and all(c in _HEX for c in value[index + 1:index + 3]):
            octet = int(value[index + 1:index + 3], 16)
            out.append(chr(octet) if octet in _UNRESERVED else f"%{octet:02X}")
            index += 3
            continue
        if char == "%":
            out.append("%25")
        elif ord(char) > 127:
            out.extend(f"%{octet:02X}" for octet in char.encode("utf-8"))
        elif char == "*" and not pattern:
            out.append("%2A")
        elif char == "$" and (not pattern or index != len(value) - 1):
            out.append("%24")
        elif ord(char) <= 32 or ord(char) == 127:
            out.append(f"%{ord(char):02X}")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _specificity(pattern: str) -> int:
    # The versioned policy counts literal comparison octets, not wildcard or
    # terminal-anchor controls. Percent escapes are literal comparison bytes.
    return len(pattern.removesuffix("$").replace("*", "").encode("ascii"))


def _matches(pattern: str, target: str) -> bool:
    """Glob matching without a backtracking regex or a recursive matcher."""
    anchored = pattern.endswith("$")
    value = pattern[:-1] if anchored else pattern
    parts = value.split("*")
    if len(parts) == 1:
        return target == value if anchored else target.startswith(value)
    first = parts[0]
    if not target.startswith(first):
        return False
    position = len(first)
    for part in parts[1:-1]:
        if not part:
            continue
        found = target.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    last = parts[-1]
    if anchored:
        return len(target) - len(last) >= position and target.endswith(last)
    return not last or target.find(last, position) >= 0


def parse_robots(body: bytes, product_token: str = PRODUCT_TOKEN) -> dict[str, Any]:
    """Select and merge the applicable groups from at most 512 KiB of UTF-8.

    Unknown records do not end a group. Empty Allow/Disallow records delimit the
    preceding group from a later User-agent, but add no path restriction.
    """
    if not isinstance(body, bytes) or not _PRODUCT.fullmatch(product_token):
        raise ValueError("robots body must be bytes and product token must contain ASCII letters, '_' or '-'")
    result: dict[str, Any] = {
        "version": POLICY_VERSION, "product_token": product_token,
        "body_sha256": hashlib.sha256(body).hexdigest(), "state": "rules",
        "rules": [], "sitemaps": [], "diagnostics": [],
    }

    def deny(reason: str) -> dict[str, Any]:
        result.update(state="deny_all", rules=[], diagnostics=[reason])
        return result

    if len(body) > MAX_ROBOTS_BYTES:
        return deny("robots_size_limit")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return deny("robots_invalid_utf8")
    if any(ord(c) < 32 and c not in "\r\n\t" or ord(c) == 127 for c in text):
        return deny("robots_control_character")
    groups: list[tuple[list[str], list[dict[str, Any]], bool]] = []
    agents: list[str] = []
    rules: list[dict[str, Any]] = []
    invalid = False
    saw_rule = False
    count = 0
    malformed = 0
    sitemaps: list[str] = []
    for line in re.split(r"\r\n|\r|\n", text):
        line = line.split("#", 1)[0].strip(" \t")
        if not line:
            continue
        if ":" not in line:
            malformed += 1
            # A recognizable damaged policy directive must not vanish silently.
            if re.match(r"(?i)^(?:allow|disallow|user-agent)(?:\s|$)", line):
                return deny("robots_malformed_policy_directive")
            continue
        field, value = (part.strip(" \t") for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if value != "*" and not _PRODUCT.fullmatch(value):
                return deny("robots_invalid_product_token")
            if saw_rule:
                groups.append((agents, rules, invalid))
                agents, rules, invalid, saw_rule = [], [], False, False
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            if not agents:
                continue
            saw_rule = True
            if not value:
                continue
            count += 1
            if (not value.startswith(("/", "*")) or any(c in " \t" for c in value)
                    or len(value.encode("utf-8")) > MAX_PATTERN_BYTES or count > MAX_RULES):
                invalid = True
                continue
            normalized = _normalize(value, pattern=True)
            if len(normalized) > 3 * MAX_PATTERN_BYTES:
                invalid = True
                continue
            rules.append({"pattern": normalized, "allow": field == "allow"})
        elif field == "sitemap":
            if len(value) <= 4096 and value.startswith(("http://", "https://")) and value not in sitemaps:
                if len(sitemaps) < 64:
                    sitemaps.append(value)
                elif "sitemap_limit" not in result["diagnostics"]:
                    result["diagnostics"].append("sitemap_limit")
    if agents:
        groups.append((agents, rules, invalid))
    wanted = product_token.lower()
    selected = [group for group in groups if wanted in group[0]]
    if not selected:
        selected = [group for group in groups if "*" in group[0]]
    if any(group[2] for group in selected):
        return deny("robots_unrepresentable_applicable_rule")
    seen: set[tuple[str, bool]] = set()
    for _, group_rules, _ in selected:
        for rule in group_rules:
            key = rule["pattern"], rule["allow"]
            if key not in seen:
                seen.add(key)
                result["rules"].append(rule)
    result["sitemaps"] = sitemaps
    if malformed:
        result["diagnostics"].append("ignored_unknown_lines")
    return result


def rule_decision(record: dict[str, Any], target: URL) -> dict[str, Any]:
    """Evaluate already validated selected rules against the actual request URI."""
    state = record["state"]
    if state != "rules":
        return {"allowed": state == "allow_all", "matched_rule": None,
                "reason": "robots_negative_cache" if state == "allow_all" else "robots_deny_all"}
    path = _normalize(target.raw_path_qs, pattern=False)
    # Fetching this origin's metadata is implicit REP permission; site review,
    # scope of metadata access, NetPolicy and budgets remain independent checks.
    if target.raw_path == "/robots.txt" and not target.raw_query_string:
        return {"allowed": True, "matched_rule": None, "reason": "robots_metadata"}
    winner: dict[str, Any] | None = None
    weight = -1
    for rule in record["rules"]:
        pattern = rule["pattern"]
        if _matches(pattern, path):
            current = _specificity(pattern)
            if current > weight or current == weight and rule["allow"]:
                winner, weight = rule, current
    return {"allowed": winner is None or winner["allow"], "matched_rule": winner,
            "reason": "robots_no_matching_rule" if winner is None else "robots_rule"}


def response_record(*, origin: str, status: str, http_status: int | None,
                    headers: dict[str, str], body: bytes, truncated: bool,
                    now_ms: int, response_delay_ms: int = 0) -> tuple[dict[str, Any], int]:
    """Classify one completed metadata chain into a bounded cache record.

    The record is private core/worker data, not a client-supplied grant. The
    body hash describes the available response representation; an unreachable
    response has the empty-body hash and an explicit diagnostic. A deny record
    contains no fetched body or copied Disallow list.
    """
    retry = max((retry_after_ms({"retry-after": value}, now_ms / 1000)
                 for value in headers.get("retry-after", "").split("\n")), default=0)
    parsed = parse_robots(b"")
    parsed["body_sha256"] = hashlib.sha256(body).hexdigest()
    ttl = 5 * 60 * 1000
    if http_status == 429:
        parsed.update(state="deny_all", diagnostics=["robots_rate_limited"])
        retry = max(retry, 10 * 60 * 1000)
        ttl = min(retry, MAX_CACHE_AGE_MS)
    elif http_status in (401, 403):
        parsed.update(state="deny_all", diagnostics=["robots_access_denied"])
        ttl = 60 * 60 * 1000
    elif http_status in (404, 410) and status in ("ok", "http_error"):
        parsed.update(state="allow_all", diagnostics=["robots_unavailable_allow_policy"])
        ttl = MAX_CACHE_AGE_MS
    elif status == "ok" and http_status is not None and 200 <= http_status < 300 and http_status != 206 and not truncated:
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in ("", "text/plain") or body[:512].lstrip().lower().startswith((b"<html", b"<!doctype")):
            parsed.update(state="deny_all", diagnostics=["robots_invalid_content_type"])
        else:
            parsed = parse_robots(body)
            if parsed["state"] == "rules":
                ttl = MAX_CACHE_AGE_MS
    else:
        parsed.update(state="deny_all", diagnostics=["robots_unreachable_or_incomplete"])
    # This first durable cache does not implement per-request conditional
    # revalidation. It refuses to cache a permitting response that forbids reuse.
    if parsed["state"] != "deny_all":
        freshness = _remaining_freshness(headers, now_ms, ttl, response_delay_ms)
        if freshness <= 0:
            parsed.update(state="deny_all", rules=[], sitemaps=[], diagnostics=["robots_cache_revalidation_required"])
            ttl = 5 * 60 * 1000
        else:
            ttl = min(ttl, freshness)
    parsed.update(origin=origin, fetched_at_ms=now_ms, expires_at_ms=now_ms + min(ttl, MAX_CACHE_AGE_MS))
    if len(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RECORD_BYTES:
        parsed.update(state="deny_all", rules=[], sitemaps=[], diagnostics=["robots_record_size_limit"],
                      expires_at_ms=now_ms + 5 * 60 * 1000)
    return parsed, retry


def _remaining_freshness(headers: dict[str, str], now_ms: int, default_ms: int, delay_ms: int) -> int:
    """Conservatively bound fresh reuse, including upstream response age.

    Invalid explicit cache metadata is never replaced with a longer heuristic.
    Request-to-body-completion delay overestimates RFC response delay, which can
    only shorten the resulting admission lifetime.
    """
    if type(delay_ms) is not int or delay_ms < 0:
        return 0
    directives = [value.strip() for value in headers.get("cache-control", "").lower().split(",")]
    if any(value.split("=", 1)[0].strip() in ("no-store", "no-cache") for value in directives):
        return 0
    vary = {value.strip().lower() for value in headers.get("vary", "").split(",") if value.strip()}
    # Metadata always uses the fixed crawler UA and text/plain Accept value.
    # Other Vary dimensions are not part of the persisted record identity.
    if vary - {"user-agent", "accept"}:
        return 0
    max_ages = []
    for value in directives:
        if value.split("=", 1)[0].strip() not in ("max-age", "s-maxage"):
            continue
        found = re.fullmatch(r'(?:s-maxage|max-age)\s*=\s*(?:"(\d{1,12})"|(\d{1,12}))', value)
        if not found:
            return 0
        max_ages.append(int(found.group(1) or found.group(2)) * 1000)
    lifetime = min([default_ms, *max_ages])

    def stamp(value: str) -> int | None:
        if not value or "\n" in value or "\r" in value:
            return None
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return math.floor(date.timestamp() * 1000)
        except (ValueError, TypeError, OverflowError):
            return None

    age = 0
    if "age" in headers:
        value = headers["age"].strip()
        if not re.fullmatch(r"[0-9]{1,12}", value):
            return 0
        age = int(value) * 1000
    date_ms = None
    if "date" in headers:
        date_ms = stamp(headers["date"])
        if date_ms is None or date_ms > now_ms + 1000:
            return 0
    current_age = max(age + delay_ms, max(0, now_ms - date_ms) if date_ms is not None else 0)
    if "expires" in headers:
        expires = stamp(headers["expires"])
        if expires is None:
            return 0
        lifetime = min(lifetime, expires - (date_ms if date_ms is not None else now_ms))
        # An explicit absolute expiry may never become fresh merely because a
        # server clock or a malformed intermediate cache reports a smaller age.
        lifetime = min(lifetime, expires - now_ms + current_age)
    return max(0, lifetime - current_age)


def require_record(record: Any, target: URL, *, now_ms: int) -> dict[str, Any]:
    """Validate every private-record field and decide for the actual target.

    Site manifest identity and review are checked separately. Only the core may
    supply these records over the authenticated private RPC; they are never
    accepted in the public map submission parameters.
    """
    required = {"version", "product_token", "body_sha256", "state", "rules", "sitemaps", "diagnostics",
                "origin", "fetched_at_ms", "expires_at_ms"}

    def invalid(message: str):
        raise FetchAdmissionDenied("E_ROBOTS_RECORD", message)

    if not isinstance(record, dict) or set(record) != required:
        invalid("robots record fields are invalid")
    if record["version"] != POLICY_VERSION or record["product_token"] != PRODUCT_TOKEN:
        invalid("robots policy version or crawler identity differs")
    if record["origin"] != str(target.origin()):
        invalid("robots record belongs to another origin")
    fetched, expires = record["fetched_at_ms"], record["expires_at_ms"]
    if (type(fetched) is not int or type(expires) is not int or not 0 <= fetched <= now_ms < expires or
            expires - fetched > MAX_CACHE_AGE_MS):
        invalid("robots record is expired, from the future, or exceeds the cache lifetime")
    if not isinstance(record["body_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["body_sha256"]):
        invalid("robots body identity is invalid")
    if record["state"] not in ("rules", "allow_all", "deny_all"):
        invalid("robots state is invalid")
    rules = record["rules"]
    if not isinstance(rules, list) or len(rules) > MAX_RULES or record["state"] != "rules" and rules:
        invalid("robots rules are invalid")
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {"pattern", "allow"} or type(rule["allow"]) is not bool or
                not isinstance(rule["pattern"], str) or not rule["pattern"].isascii() or
                not rule["pattern"].startswith(("/", "*")) or len(rule["pattern"]) > 3 * MAX_PATTERN_BYTES or
                _normalize(rule["pattern"], pattern=True) != rule["pattern"]):
            invalid("robots rule encoding or type is invalid")
    for key, maximum, string_limit in (("sitemaps", 64, 4096), ("diagnostics", 32, 128)):
        if (not isinstance(record[key], list) or len(record[key]) > maximum or
                any(not isinstance(value, str) or len(value) > string_limit for value in record[key])):
            invalid("robots metadata limits are invalid")
    if len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RECORD_BYTES:
        invalid("robots record exceeds its size limit")
    decision = rule_decision(record, target)
    if not decision["allowed"]:
        raise FetchAdmissionDenied("E_ROBOTS_DISALLOWED", "robots policy does not permit this request")
    return decision
