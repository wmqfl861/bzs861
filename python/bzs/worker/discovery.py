"""Bounded, offline discovery of URLs from sitemap, feed and HTML documents.

``discover`` only describes candidates. It neither fetches URLs nor authorizes
them: scope, robots admission, cross-document cycles, query-variant quotas and
owner-visible history belong to the durable core scheduler. A sitemap-index
candidate has ``document='sitemap'``; all other candidates have ``document=None``
and are not implicitly put on a recursive fetch queue.

The entry limit counts inspected discovery records, including invalid/duplicate
ones. XML is nevertheless validated in full within fixed byte/node/depth limits;
malformed or forbidden XML never publishes a partially parsed candidate list.
DTD/custom entities and external resource resolution are forbidden. Predefined
XML escapes and numeric character references remain ordinary text. Representation
decoding, including HTTP gzip, is the fetcher's job; this module never decompresses.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
from xml.parsers import expat

from lxml import etree
from yarl import URL

from .fetch import decode_body

DEFAULT_INPUT_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_URL_BYTES = 4096
MAX_TREE_DEPTH = 128
MAX_TREE_NODES = 100000

_SITEMAP = "http://www.sitemaps.org/schemas/sitemap/0.9"
_ATOM = "http://www.w3.org/2005/Atom"
_RSS1 = "http://purl.org/rss/1.0/"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XHTML = "http://www.w3.org/1999/xhtml"
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_KINDS = frozenset(("auto", "html", "sitemap", "rss", "atom"))
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SPACE = " \t\r\n\f"
_HTML_START = re.compile(r"<(?:html|head|body|a|div|p|main|section|article|nav|ul|ol|table|base|meta)\b", re.I)
_XML_START = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?(?:urlset|sitemapindex|rss|feed|entry|RDF)\b")
_CHARSET = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^\s;\"']+)", re.I)


class _URLProblem(ValueError):
    pass


class _ParseProblem(ValueError):
    def __init__(self, code: str, *, status: str = "invalid", limit: str | None = None):
        super().__init__(code)
        self.code, self.status, self.limit = code, status, limit


def _url(reference: str, base: str | None) -> str:
    """Canonicalize the authority, preserving already encoded path/query bytes.

    Preserve query order and repeated keys. Do not sort/drop tracking parameters
    or infer query equivalence; those operations could merge different resources.
    """
    reference = reference.strip(_SPACE)
    if not reference:
        raise _URLProblem("empty_url")
    try:
        if len(reference.encode("utf-8")) > MAX_URL_BYTES:
            raise _URLProblem("url_too_long")
    except UnicodeError as exc:
        raise _URLProblem("invalid_unicode") from exc
    if any(ord(c) < 32 or ord(c) == 127 for c in reference) or "\\" in reference:
        raise _URLProblem("unsafe_url_characters")
    if _BAD_PERCENT.search(reference):
        raise _URLProblem("invalid_percent_encoding")
    try:
        raw = urlsplit(reference)
        if "@" in raw.netloc:
            raise _URLProblem("credentials")
        if raw.scheme and raw.scheme.lower() not in ("http", "https"):
            raise _URLProblem("unsafe_scheme")
        if raw.scheme and not raw.netloc:
            raise _URLProblem("missing_host")
        if reference.startswith("//") and not raw.netloc:
            raise _URLProblem("missing_host")
        if not base and not (raw.scheme and raw.netloc):
            raise _URLProblem("invalid_base")
        absolute = urljoin(base or "", reference)
        # urllib's urljoin conflates an absent query with an explicitly empty
        # query. ``?`` must clear the base query, while ``#part`` must retain it.
        if base and reference.startswith("?"):
            absolute = base.split("?", 1)[0] + reference
        # Check again after relative resolution; never erase empty userinfo.
        if "@" in urlsplit(absolute).netloc:
            raise _URLProblem("credentials")
        parts = URL(absolute)
        if parts.scheme not in ("http", "https"):
            raise _URLProblem("unsafe_scheme")
        if parts.user is not None or parts.password is not None:
            raise _URLProblem("credentials")
        host = (parts.raw_host or "").lower()
        if not host:
            raise _URLProblem("missing_host")
        if "%" in host:
            raise _URLProblem("invalid_host")
        if host.endswith("."):
            host = host[:-1]
        if ":" in host:
            try:
                ipaddress.IPv6Address(host)
            except ValueError as exc:
                raise _URLProblem("invalid_host") from exc
        elif len(host) > 253 or any(not _DNS_LABEL.fullmatch(label) for label in host.split(".")):
            raise _URLProblem("invalid_host")
        if parts.port is None or not 1 <= parts.port <= 65535:
            raise _URLProblem("invalid_port")
        raw_parts = urlsplit(absolute)
        rest = (raw_parts.path or "/") + ("?" + raw_parts.query if raw_parts.query else "")
        if any(c in rest for c in '[]<>"`{}|^'):
            raise _URLProblem("unsafe_url_characters")
        # The durable core and worker deliberately preserve /%61?x=%62 as-is.
        # yarl's default path/query normalization would decode these octets and
        # change item identity (also changing percent-escape letter case).
        rest = "".join("".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
                       if ord(char) > 127 or char == " " else char for char in rest)
        result = str(parts.with_host(host).origin()) + rest
    except _URLProblem:
        raise
    except (ValueError, UnicodeError) as exc:
        raise _URLProblem("invalid_url") from exc
    if len(result.encode("utf-8")) > MAX_URL_BYTES:
        raise _URLProblem("url_too_long")
    return result


def _name(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _expanded(name: str) -> str:
    return "{" + name if "}" in name else name


def _xml_tree(body: bytes):
    """Build a bounded tree using Expat callbacks, with no resource loader."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    nodes = depth = 0

    def start(name, attrs):
        nonlocal nodes, depth
        nodes += 1
        depth += 1
        if nodes > MAX_TREE_NODES:
            raise _ParseProblem("xml_node_limit", limit="tree_nodes")
        if depth > MAX_TREE_DEPTH:
            raise _ParseProblem("xml_depth_limit", limit="tree_depth")
        builder.start(_expanded(name), {_expanded(key): value for key, value in attrs.items()})

    def end(name):
        nonlocal depth
        builder.end(_expanded(name))
        depth -= 1

    def dtd(*_args):
        raise _ParseProblem("xml_dtd_forbidden")

    def entity(*_args):
        raise _ParseProblem("xml_entity_forbidden")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = dtd
    parser.EntityDeclHandler = entity
    parser.ExternalEntityRefHandler = entity
    try:
        parser.Parse(body, True)
        return builder.close()
    except expat.ExpatError as exc:
        # Do not echo document contents, external URI values or parser diagnostics.
        raise _ParseProblem("malformed_xml") from exc


def _html_tree(body: bytes, content_type: str):
    charset = _CHARSET.search(content_type)
    text, _ = decode_body(body, charset.group(1) if charset else None)
    parser = etree.HTMLPullParser(events=("start", "end"), encoding="utf-8", no_network=True,
                                  recover=True, huge_tree=False, remove_comments=True)
    nodes = depth = 0

    def check_events():
        nonlocal nodes, depth
        for event, _node in parser.read_events():
            if event == "start":
                nodes += 1
                depth += 1
                if nodes > MAX_TREE_NODES:
                    raise _ParseProblem("html_node_limit", limit="tree_nodes")
                if depth > MAX_TREE_DEPTH:
                    raise _ParseProblem("html_depth_limit", limit="tree_depth")
            else:
                depth -= 1

    try:
        encoded = text.encode("utf-8")
        for offset in range(0, len(encoded), 8192):
            parser.feed(encoded[offset:offset + 8192])
            check_events()
        root = parser.close()
        check_events()
    except (etree.XMLSyntaxError, etree.ParserError, UnicodeError, ValueError) as exc:
        if isinstance(exc, _ParseProblem):
            raise
        raise _ParseProblem("malformed_html") from exc
    # libxml2 may recover from a resource limit by returning a shortened tree.
    if any(error.level_name == "FATAL" for error in parser.feed_error_log):
        raise _ParseProblem("html_parser_limit", limit="parser_resource")
    if root is None:
        raise _ParseProblem("malformed_html")
    return root


class _Collector:
    def __init__(self, result: dict, max_entries: int, depth: int):
        self.result, self.max_entries, self.depth = result, max_entries, depth
        self.seen: set[str] = set()

    @property
    def diag(self):
        return self.result["diagnostics"]

    def limited(self, reason: str):
        self.result["truncated"] = True
        if reason not in self.diag["limits"]:
            self.diag["limits"].append(reason)

    def invalid(self, reason: str):
        self.diag["invalid"] += 1
        reasons = self.diag["invalid_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1

    def base(self, node, inherited: str | None, *, report: bool = True) -> str | None:
        value = node.get(_XML_BASE)
        if value is None:
            return inherited
        try:
            return _url(value, inherited) if value.strip(_SPACE) else inherited
        except _URLProblem:
            if report:
                self.diag["invalid_bases"] += 1
            return None

    def add(self, reference: str, base: str | None, source: str, *, document: str | None = None,
            error: str | None = None, nofollow: bool = False):
        if self.diag["examined"] >= self.max_entries:
            self.limited("entries")
            return
        self.diag["examined"] += 1
        if nofollow:
            self.diag["nofollow"] += 1
            return
        if error:
            self.invalid(error)
            return
        try:
            url = _url(reference, base)
        except _URLProblem as exc:
            self.invalid(str(exc))
            return
        if url == self.result["document_url"]:
            self.diag["self_links"] += 1
            return
        if url in self.seen:
            self.diag["duplicates"] += 1
            return
        self.seen.add(url)
        self.result["candidates"].append({
            "url": url, "parent": self.result["document_url"], "source": source,
            "document": document, "depth": self.depth + 1,
            "path_depth": sum(bool(part) for part in urlsplit(url).path.split("/")),
        })


def _children(node, name: tuple[str, str]):
    return (child for child in node if isinstance(child.tag, str) and _name(child.tag) == name)


def _location(entry, location_name: tuple[str, str], base: str | None, collector: _Collector,
              source: str, document: str | None = None):
    entry_base = collector.base(entry, base)
    locations = _children(entry, location_name)
    first = next(locations, None)
    if first is None:
        collector.add("", entry_base, source, document=document, error="missing_location")
    elif next(locations, None) is not None:
        collector.add("", entry_base, source, document=document, error="multiple_locations")
    else:
        collector.add(first.text or "", collector.base(first, entry_base), source, document=document,
                      error="nested_location" if len(first) else None)


def _xml_candidates(root, kind: str, collector: _Collector):
    ns, local = _name(root.tag)
    if ns in ("", _SITEMAP) and local in ("urlset", "sitemapindex"):
        actual, category = ("sitemap_urlset" if local == "urlset" else "sitemap_index"), "sitemap"
    elif (ns == "" and local == "rss") or (ns == _RDF and local == "RDF"):
        actual = category = "rss"
    elif ns in ("", _ATOM) and local in ("feed", "entry"):
        actual = category = "atom"
    elif ns in ("", _XHTML) and local == "html":
        actual = category = "html"
    else:
        raise _ParseProblem("unsupported_xml", status="unsupported")
    collector.result["document_type"] = actual
    if kind not in ("auto", category):
        raise _ParseProblem("document_kind_mismatch", status="unsupported")
    base = (collector.base(root, collector.result["document_url"])
            if category != "html" else collector.result["document_url"])
    if category == "sitemap":
        index = actual == "sitemap_index"
        for entry in _children(root, (ns, "sitemap" if index else "url")):
            _location(entry, (ns, "loc"), base, collector, "sitemap_index" if index else "sitemap",
                      "sitemap" if index else None)
    elif category == "rss":
        if ns == _RDF:
            for entry in _children(root, (_RSS1, "item")):
                _location(entry, (_RSS1, "link"), base, collector, "rss")
        else:
            for channel in _children(root, ("", "channel")):
                channel_base = collector.base(channel, base)
                for entry in _children(channel, ("", "item")):
                    _location(entry, ("", "link"), channel_base, collector, "rss")
    elif category == "atom":
        entries = [root] if local == "entry" else _children(root, (ns, "entry"))
        for entry in entries:
            entry_base = base if entry is root else collector.base(entry, base)
            for link in _children(entry, (ns, "link")):
                # rel=self, enclosure, related and feed navigation are not entry pages.
                if link.get("rel", "alternate").strip() != "alternate":
                    collector.diag["ignored_links"] += 1
                    continue
                collector.add(link.get("href", ""), collector.base(link, entry_base), "atom")
    else:
        _html_candidates(root, collector)


def _walk_html(node, base: str | None, collector: _Collector, *, report: bool):
    ns, local = _name(node.tag)
    # Template/foreign-content anchors do not form active HTML navigation.
    if ns not in ("", _XHTML) or local in ("template", "script", "style", "svg", "math"):
        return
    current = collector.base(node, base, report=report)
    yield node, current
    for child in node:
        if isinstance(child.tag, str):
            yield from _walk_html(child, current, collector, report=report)


def _html_candidates(root, collector: _Collector):
    base = collector.result["document_url"]
    found_base = False
    page_nofollow = False
    for node, node_base in _walk_html(root, base, collector, report=False):
        local = _name(node.tag)[1]
        if local == "base" and not found_base and node.get("href") is not None:
            found_base = True
            try:
                base = _url(node.get("href"), node_base)
            except _URLProblem:
                collector.diag["invalid_bases"] += 1
                base = None  # Do not reinterpret relative links against an invalid first base.
        if local == "meta" and node.get("name", "").strip().lower() in ("robots", "bzsbot", "bzs861"):
            tokens = set(re.split(r"[,\s]+", node.get("content", "").strip().lower()))
            page_nofollow = page_nofollow or bool(tokens.intersection(("nofollow", "none")))
    for node, node_base in _walk_html(root, base, collector, report=True):
        if _name(node.tag)[1] == "a" and node.get("href") is not None:
            nofollow = page_nofollow or "nofollow" in node.get("rel", "").lower().split()
            collector.add(node.get("href"), node_base, "html", nofollow=nofollow)


def _route(body: bytes, content_type: str, kind: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    # Only sniff a bounded prefix; the XML parser handles declared encodings.
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        head = body[:4096].decode("utf-16", errors="ignore").lstrip("\ufeff" + _SPACE)
    else:
        head = body[:4096].decode("utf-8", errors="ignore").lstrip("\ufeff" + _SPACE)
    while head.startswith("<!--") and "-->" in head:
        head = head.split("-->", 1)[1].lstrip(_SPACE)
    if kind in ("sitemap", "rss", "atom") or head.startswith("<?xml") or _XML_START.match(head):
        return "xml"
    if mime in ("text/xml", "application/xml") or mime.endswith("+xml"):
        return "xml"
    if kind == "html" or mime == "text/html":
        return "html"
    if mime not in ("", "text/plain", "application/octet-stream"):
        raise _ParseProblem("unsupported_content_type", status="unsupported")
    if re.match(r"<!doctype\s+html\b", head, re.I) or _HTML_START.match(head):
        return "html"
    if head.startswith("<"):
        return "xml"
    raise _ParseProblem("unsupported_document", status="unsupported")


def discover(body: bytes, final_url: str, *, content_type: str = "", kind: str = "auto",
             depth: int = 0, max_entries: int = MAX_ENTRIES,
             max_input_bytes: int = DEFAULT_INPUT_BYTES) -> dict:
    """Return canonical candidate metadata and explicit parse/limit diagnostics.

    ``parse_status`` is ``ok``, ``invalid`` or ``unsupported``. An ``ok`` result can
    be empty or have ``truncated=True``; it is never a completeness claim. Invalid
    options/final URLs raise ValueError, while untrusted document failures return
    an empty candidate list with a stable diagnostic code. ``depth`` counts the
    discovery-document chain, independently of each URL's ``path_depth``.
    """
    if type(body) is not bytes or not isinstance(final_url, str):
        raise ValueError("body bytes and final_url string are required")
    if not isinstance(content_type, str) or len(content_type) > 1024:
        raise ValueError("invalid content_type")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("invalid discovery kind")
    if type(depth) is not int or not 0 <= depth <= 1000:
        raise ValueError("invalid discovery depth")
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES:
        raise ValueError("max_entries must be between 1 and 1000")
    if type(max_input_bytes) is not int or not 1 <= max_input_bytes <= MAX_INPUT_BYTES:
        raise ValueError("max_input_bytes exceeds the parser's hard bounds")
    try:
        document_url = _url(final_url, None)
    except _URLProblem as exc:
        raise ValueError(f"invalid final_url: {exc}") from exc
    result = {"parse_status": "ok", "document_url": document_url, "document_type": "unknown",
              "candidates": [], "truncated": False,
              "diagnostics": {"errors": [], "invalid": 0, "invalid_reasons": {}, "invalid_bases": 0,
                              "duplicates": 0, "self_links": 0, "nofollow": 0, "ignored_links": 0,
                              "examined": 0, "limits": []}}
    collector = _Collector(result, max_entries, depth)
    try:
        if len(body) > max_input_bytes:
            raise _ParseProblem("input_too_large", limit="input_bytes")
        if not body.strip():
            raise _ParseProblem("empty_document")
        if body.startswith(b"\x1f\x8b"):
            raise _ParseProblem("compressed_body_not_supported", status="unsupported")
        if _route(body, content_type, kind) == "xml":
            _xml_candidates(_xml_tree(body), kind, collector)
        else:
            result["document_type"] = "html"
            _html_candidates(_html_tree(body, content_type), collector)
    except _ParseProblem as exc:
        result["parse_status"] = exc.status
        result["candidates"] = []
        result["diagnostics"]["errors"].append(exc.code)
        if exc.limit:
            collector.limited(exc.limit)
    return result
