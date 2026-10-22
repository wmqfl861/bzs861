"""Private, versioned HTTP validation metadata with a deliberately small scope.

These records never grant ownership, site permission, robots admission or
publication rights. The core binds them to an exact stored representation and
checks that binding again when committing a bodyless 304 response. This module
only validates wire fields and the selected HTTP request/response identity.
"""
from __future__ import annotations

import hashlib
import json
import re
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any


VERSION = "bzs-http-revalidation-1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e]*"\Z')
_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_DIRECTIVE = re.compile(rf'({_TOKEN})(?:\s*=\s*({_TOKEN}|"[\x20\x21\x23-\x5b\x5d-\x7e]*"))?\Z')
_CONDITIONAL_FIELDS = frozenset(("version", "binding_id", "url", "etag", "last_modified", "request_profile"))
CACHE_FIELDS = frozenset(("version", "url", "request_profile", "etag", "last_modified",
                          "cache_control", "vary", "date", "age", "expires"))
_HTTP_FIELDS = {"etag": "etag", "last_modified": "last-modified", "cache_control": "cache-control",
                "vary": "vary", "date": "date", "age": "age", "expires": "expires"}
_CONDITION_HEADERS = frozenset(("if-none-match", "if-modified-since"))


class ConditionalMismatch(Exception):
    """A well-formed grant does not name the request this worker will dispatch."""


def _url(value: Any) -> bool:
    return (isinstance(value, str) and value.isascii() and 1 <= len(value) <= 4096 and
            not any(ord(char) <= 32 or ord(char) == 127 for char in value))


def _etag(value: Any) -> bool:
    # A single quoted opaque tag is accepted. Wildcards, lists, repeated fields,
    # control characters and obs-text are outside this private contract.
    return isinstance(value, str) and len(value) <= 1024 and bool(_ETAG.fullmatch(value))


def _date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 29 or not value.isascii():
        return False
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.year >= 1601 and parsed.tzinfo is not None and format_datetime(parsed, usegmt=True) == value
    except (ValueError, TypeError, OverflowError):
        return False


def _validators(value: dict) -> None:
    etag, modified = value["etag"], value["last_modified"]
    if (not isinstance(etag, str) or not isinstance(modified, str) or
            not (etag or modified) or etag and not _etag(etag) or modified and not _date(modified)):
        raise ValueError("HTTP validators must be one entity tag and/or one canonical HTTP date")


def validate_conditional(value: Any) -> dict[str, str]:
    """Validate raw private RPC fields before retaining or dispatching an attempt."""
    if not isinstance(value, dict) or set(value) != _CONDITIONAL_FIELDS:
        raise ValueError("conditional fields are invalid")
    if (value["version"] != VERSION or not _url(value["url"]) or
            not isinstance(value["binding_id"], str) or not _HEX.fullmatch(value["binding_id"]) or
            not isinstance(value["request_profile"], str) or not _HEX.fullmatch(value["request_profile"])):
        raise ValueError("conditional version or representation identity is invalid")
    _validators(value)
    return dict(value)


def request_profile(headers: dict[str, str]) -> str:
    """Hash the explicit GET header selection, excluding its resource validators.

    Fetcher explicitly fixes Accept-Encoding, uses no cookie jar and disables
    environment proxies. Including every other explicit header also prevents a
    future credential/header addition from silently sharing the old profile.
    """
    selected = {key.lower(): value for key, value in headers.items() if key.lower() not in _CONDITION_HEADERS}
    payload = json.dumps({"version": VERSION, "method": "GET", "headers": selected},
                         ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def require_identity(conditional: dict[str, str], *, url: str, profile: str) -> None:
    if conditional["url"] != url or conditional["request_profile"] != profile:
        raise ConditionalMismatch("conditional URL or request profile differs from this request")


def conditional_headers(conditional: dict[str, str] | None) -> dict[str, str]:
    if conditional is None:
        return {}
    return {name: conditional[key] for key, name in (("etag", "If-None-Match"),
                                                    ("last_modified", "If-Modified-Since")) if conditional[key]}


def _cache_control(value: str) -> bool:
    if len(value) > 4096 or not value.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if not value:
        return True
    seen_ages: set[str] = set()
    for item in value.split(","):
        directive = _DIRECTIVE.fullmatch(item.strip())
        if not directive:
            return False
        name, argument = directive[1].lower(), directive[2]
        # The first protocol version does not retain representations requiring
        # per-use validation or private/shared-cache interpretation. A caller
        # can request a separately budgeted complete read instead.
        if name in ("no-store", "no-cache", "private"):
            return False
        if name in ("max-age", "s-maxage"):
            if name in seen_ages or argument is None:
                return False
            age = argument.strip('"')
            if not re.fullmatch(r"[0-9]{1,12}", age) or int(age) == 0:
                return False
            seen_ages.add(name)
    return True


def validate_cache(value: Any) -> dict[str, str]:
    """Validate a complete metadata object, including records received from core."""
    if not isinstance(value, dict) or set(value) != CACHE_FIELDS or any(not isinstance(item, str) for item in value.values()):
        raise ValueError("http_cache fields are invalid")
    if value["version"] != VERSION or not _url(value["url"]) or not _HEX.fullmatch(value["request_profile"]):
        raise ValueError("http_cache version or request identity is invalid")
    _validators(value)
    if not _cache_control(value["cache_control"]):
        raise ValueError("HTTP cache controls do not permit this revalidation protocol")
    if value["vary"]:
        raise ValueError("Vary selections are outside this revalidation protocol")
    for name in ("date", "expires"):
        if value[name] and not _date(value[name]):
            raise ValueError("HTTP cache date is invalid or repeated")
    if value["age"] and not re.fullmatch(r"[0-9]{1,12}", value["age"]):
        raise ValueError("HTTP cache age is invalid or repeated")
    return dict(value)


def response_cache(headers: dict[str, str], *, url: str, profile: str,
                   conditional: dict[str, str] | None = None) -> dict[str, str]:
    """Extract bounded metadata; callers handle a validation failure explicitly.

    A complete 200 without safe metadata remains a usable full response, but it
    cannot become a conditional base. A 304 failure never requests a fallback.
    Missing 304 fields mean unchanged and must not erase stored restrictions.
    """
    for name in ("etag", "last-modified", "date", "age", "expires", "cache-control"):
        if name in headers and not headers[name]:
            raise ValueError("empty explicit HTTP cache field is invalid")
    result = {"version": VERSION, "url": url, "request_profile": profile}
    result.update((key, headers.get(header, "")) for key, header in _HTTP_FIELDS.items())
    if conditional is not None:
        require_identity(conditional, url=url, profile=profile)
        tag = result["etag"]
        old_tag = conditional["etag"]
        if tag and old_tag and tag.removeprefix("W/") != old_tag.removeprefix("W/"):
            raise ValueError("304 entity tag differs from the dispatched validator")
        modified = result["last_modified"]
        if modified and conditional["last_modified"] and modified != conditional["last_modified"]:
            raise ValueError("304 Last-Modified differs from the dispatched validator")
        result["etag"] = tag or old_tag
        result["last_modified"] = modified or conditional["last_modified"]
    return validate_cache(result)


def merge_cache(base: dict[str, str], updates: dict[str, str]) -> dict[str, str]:
    """Merge only explicitly supplied 304 metadata; omitted restrictions survive."""
    base, updates = validate_cache(base), validate_cache(updates)
    if base["url"] != updates["url"] or base["request_profile"] != updates["request_profile"]:
        raise ConditionalMismatch("304 metadata does not describe its stored representation")
    return validate_cache({key: updates[key] or base[key] for key in CACHE_FIELDS})


def cache_headers(cache: dict[str, str]) -> dict[str, str]:
    return {header: cache[key] for key, header in _HTTP_FIELDS.items() if cache[key]}
