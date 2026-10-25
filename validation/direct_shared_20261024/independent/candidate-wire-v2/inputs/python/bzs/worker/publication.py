"""Versioned BzsBot publication rules, distinct from robots request admission.

This is the product's policy, not an implementation of Googlebot's complete
indexing vocabulary. Generic fields and BzsBot (legacy bzs861) fields contribute
restrictive rules; another crawler's fields cannot grant or deny publication.
The caller supplies canonical reader text separately from sanitized snippet text.
"""
from __future__ import annotations

import re

PUBLICATION_VERSION = "bzs-publication-1"
MAX_SNIPPET_BYTES = 1024 * 1024
MAX_TITLE_BYTES = 1024
MAX_POLICY_BYTES = 64 * 1024
MAX_META_BYTES = 4096
_AGENTS = frozenset(("*", "bzsbot", "bzs861"))
_FLAGS = frozenset(("noindex", "nosnippet", "nofollow"))
_VALUE_RULES = frozenset(("unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"))
_PREFIX = re.compile(r"^([a-z0-9_*.-]+)\s*:\s*(.*)$", re.S)
_INERT = frozenset(("script", "style", "template", "svg", "math", "iframe", "object", "embed"))


def utf8_text(value: str, limit: int) -> str:
    """Bound text on the wire, without a partial code point or embedded NUL."""
    return value.replace("\x00", "\ufffd").encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")


def _active_nodes(root):
    if root is None or not isinstance(root.tag, str):
        return
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag in _INERT:
        return
    yield root
    for child in root:
        yield from _active_nodes(child)


def _rules(value: str, *, scoped: bool) -> set[str]:
    rules: set[str] = set()
    applies = True
    for part in value.lower().split(","):
        part = part.strip()
        prefix = _PREFIX.match(part)
        if scoped and prefix and prefix[1] not in _VALUE_RULES:
            applies = prefix[1] in _AGENTS
            part = prefix[2].strip()
        if not applies:
            continue
        # Parameter values are not flag tokens: max-image-preview:none must
        # never become noindex/nofollow. This release does not implement those
        # parameterized Google rules, apart from max-snippet:0 as nosnippet.
        parameter = _PREFIX.match(part)
        if parameter and parameter[1] in _VALUE_RULES:
            if parameter[1] == "max-snippet" and parameter[2].strip() == "0":
                rules.add("nosnippet")
            continue
        tokens = set(re.split(r"[ \t\n\r\f\v]+", part))
        rules.update(tokens & _FLAGS)
        if "none" in tokens:
            rules.update(("noindex", "nofollow"))
    return rules


def publication_rules(headers: dict[str, str] | None = None, doc=None) -> dict:
    """Collect restrictive directives without copying untrusted header text.

    Repeated HTTP fields are newline-separated by Fetcher. Scope resets at each
    physical field, and persists within a comma-joined field until a new agent
    prefix. Oversized policy metadata fails closed instead of ignoring a tail.
    """
    rules: set[str] = set()
    diagnostics: list[str] = []
    used = 0
    for name, raw in (headers or {}).items():
        if name.lower() != "x-robots-tag":
            continue
        used += len(raw.encode("utf-8", "replace"))
        if used > MAX_POLICY_BYTES:
            diagnostics.append("publication_metadata_limit")
            break
        for value in raw.splitlines():
            rules.update(_rules(value, scoped=True))
    if not diagnostics:
        for node in _active_nodes(doc):
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if node.get("data-nosnippet") is not None:
                rules.add("data-nosnippet")
            if tag != "meta" or node.get("name", "").strip().lower() not in ("robots", "bzsbot", "bzs861"):
                continue
            value = node.get("content", "")
            size = len(value.encode("utf-8", "replace"))
            used += size
            if size > MAX_META_BYTES or used > MAX_POLICY_BYTES:
                diagnostics.append("publication_metadata_limit")
                break
            rules.update(_rules(value, scoped=False))
    if diagnostics:
        rules.update(_FLAGS)
    return {"version": PUBLICATION_VERSION, "index_allowed": "noindex" not in rules,
            "snippet_allowed": not rules.intersection(("noindex", "nosnippet")),
            "follow_allowed": "nofollow" not in rules, "snippet_text": "", "snippet_title": "",
            "directives": sorted(rules), "diagnostics": diagnostics}


def deny_publication(headers: dict[str, str] | None, diagnostic: str) -> dict:
    out = publication_rules(headers)
    out.update(index_allowed=False, snippet_allowed=False, follow_allowed=False,
               directives=sorted(set(out["directives"]) | _FLAGS),
               diagnostics=sorted(set(out["diagnostics"]) | {diagnostic}))
    return out


def publication_text(policy: dict, text: str, title: str) -> dict:
    """Attach only sanitized snippets; noindex always withholds every field."""
    return {**policy,
            "snippet_text": utf8_text(text, MAX_SNIPPET_BYTES) if policy["snippet_allowed"] else "",
            "snippet_title": utf8_text(title, MAX_TITLE_BYTES) if policy["index_allowed"] else ""}


def apply_redistribution(policy: dict, review: dict) -> dict:
    if review.get("redistribution_policy") == "links_only":
        return {**policy, "snippet_allowed": False, "snippet_text": "", "snippet_title": "",
                "diagnostics": sorted(set(policy["diagnostics"]) | {"site_links_only"})}
    return policy


def incomplete_publication(policy: dict) -> dict:
    """Readable prefixes cannot grant permission for an unseen response tail."""
    return {**policy, "index_allowed": False, "snippet_allowed": False, "follow_allowed": False,
            "snippet_text": "", "snippet_title": "",
            "directives": sorted(set(policy["directives"]) | _FLAGS),
            "diagnostics": sorted(set(policy["diagnostics"]) | {"incomplete_http_body"})}


def restriction_observation(headers: dict[str, str]) -> dict | None:
    """A failed body cannot erase restrictive policy already seen in headers.

    This contains no snippet material and can only tighten a stored policy. A
    default/allow-only header produces no observation, so a failed refresh can
    never restore an old index or snippet permission.
    """
    policy = publication_rules(headers)
    if all(policy[key] for key in ("index_allowed", "snippet_allowed", "follow_allowed")):
        return None
    policy["diagnostics"] = sorted(set(policy["diagnostics"]) | {"http_header_restriction"})
    return policy


def empty_extraction_observation(policy: dict) -> dict | None:
    """Preserve bounded HTML restrictions when no readable body can be stored.

    A data-nosnippet observation cannot map an absent new representation onto an
    old snapshot. Suppress its entire old snippet until a valid later body can
    provide a fresh sanitized representation. No permission is ever added.
    """
    return extraction_restriction_observation(policy, "empty_extraction_restriction")


def extraction_restriction_observation(policy: dict, diagnostic: str) -> dict | None:
    """A skipped representation cannot safely map regions onto an old body."""
    restricted_regions = "data-nosnippet" in policy["directives"]
    if all(policy[key] for key in ("index_allowed", "snippet_allowed", "follow_allowed")) and not restricted_regions:
        return None
    return {**policy, "snippet_text": "", "snippet_title": "",
            "snippet_allowed": policy["snippet_allowed"] and not restricted_regions,
            "diagnostics": sorted(set(policy["diagnostics"]) | {diagnostic})}
