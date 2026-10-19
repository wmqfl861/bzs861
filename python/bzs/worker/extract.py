"""Main-content extraction (trafilatura) with hidden-text removal and page-state checks."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

import trafilatura
from lxml import etree

from .discovery import MAX_INPUT_BYTES, _ParseProblem, _html_tree, discover
from .publication import deny_publication, publication_rules, publication_text, utf8_text

EXTRACTOR = f"trafilatura/{trafilatura.__version__}"
_CHALLENGE = ("cf-chl", "challenge-platform", "just a moment...", "attention required! | cloudflare",
              "verify you are human", "ddos-guard", "please enable javascript and cookies",
              "访问验证", "安全验证", "人机验证", "请输入验证码", "滑动验证", "异常访问请求")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_DROP_TAGS = ("script", "style", "template", "svg", "math", "iframe", "object", "embed")
MAX_TEXT_BYTES = 2 * 1024 * 1024


def looks_like_challenge(http_status: int | None, html: str) -> bool:
    head = html[:30000].lower()
    hits = sum(1 for marker in _CHALLENGE if marker in head)
    if hits >= 1 and (http_status in (403, 429, 503) or len(head) < 6000):
        return True
    return "captcha" in head and http_status in (403, 429, 503)


def header_noindex(headers: dict[str, str]) -> bool:
    return not publication_rules(headers)["index_allowed"]


def noindex(headers: dict[str, str], doc) -> bool:
    return not publication_rules(headers, doc)["index_allowed"]


def _remove_tree(el):
    """Drop an element and descendants, preserving the visible following tail."""
    parent = el.getparent()
    if parent is None:
        el.clear()
        return
    tail = el.tail
    previous = el.getprevious()
    parent.remove(el)
    if tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _clean_tree(doc):
    doomed = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        style = el.get("style") or ""
        if (el.tag in _DROP_TAGS or el.get("hidden") is not None or (el.get("aria-hidden") or "").lower() == "true"
                or _HIDDEN_STYLE.search(style)):
            doomed.append(el)
    for el in doomed:
        _remove_tree(el)
    return doc


def clean_dom(html: str):
    """Parse and drop elements a reader cannot see (common prompt-injection carriers)."""
    try:
        body = html.encode("utf-8", "replace")
        if len(body) > MAX_INPUT_BYTES:
            return None
        doc = _html_tree(body, "text/html; charset=utf-8")
    except (etree.ParserError, ValueError, _ParseProblem):
        return None
    return _clean_tree(doc)


def collect_links(doc, base_url: str, limit: int = 512) -> list[str]:
    parsed = discover(etree.tostring(doc, encoding="utf-8", method="html"), base_url,
                      content_type="text/html; charset=utf-8", kind="html",
                      max_entries=limit, max_input_bytes=MAX_INPUT_BYTES)
    return [item["url"] for item in parsed["candidates"]] if parsed["parse_status"] == "ok" else []


def _readable(doc, url: str) -> dict:
    warnings: list[str] = []
    title_el = doc.find(".//title")
    page_title = utf8_text("".join(title_el.itertext()).strip() if title_el is not None else "", 1024)
    if doc.xpath("//input[@type='password']"):
        warnings.append("login_form_present")
    cleaned = etree.tostring(doc, encoding="unicode", method="html")
    text = trafilatura.extract(cleaned, url=url, output_format="markdown", include_tables=True,
                               include_links=False, include_images=False, include_formatting=True,
                               favor_recall=True, deduplicate=False, with_metadata=False) or ""
    status = "ok"
    extractor = EXTRACTOR
    if len(text.strip()) < 80:
        fallback = trafilatura.baseline(cleaned)[1] or ""
        if len(fallback.strip()) > len(text.strip()):
            text, status, extractor = fallback, "fallback", EXTRACTOR + "+baseline"
    if not text.strip():
        status = "empty"
    elif len(text) < 400 and doc.xpath("//script[@src] | //div[@id='root' or @id='app' or @id='__next']"):
        warnings.append("maybe_dynamic_page")
    meta = trafilatura.extract_metadata(cleaned, default_url=url)
    metadata = {}
    title = page_title
    if meta is not None:
        for key in ("author", "date", "sitename", "description", "language"):
            value = getattr(meta, key, None)
            if value:
                metadata[key] = str(value)[:500]
        title = utf8_text((str(meta.title) if getattr(meta, "title", None) else page_title).strip(), 1024)
    text = text.strip()
    if len(text.encode("utf-8", "replace")) > MAX_TEXT_BYTES:
        warnings.append("text_truncated")
    return {"extract_status": status, "text": utf8_text(text, MAX_TEXT_BYTES), "title": title,
            "metadata": metadata, "warnings": warnings, "extractor": extractor}


def extract_html(html: str, url: str, headers: dict[str, str] | None = None) -> dict:
    headers = headers or {}
    empty = {"extract_status": "empty", "text": "", "title": "", "links": [], "metadata": {},
             "warnings": [], "extractor": EXTRACTOR}
    if not html.strip():
        policy = publication_rules(headers)
        return {**empty, "index_allowed": policy["index_allowed"], "publication": policy}
    try:
        body = html.encode("utf-8", "replace")
        if len(body) > MAX_INPUT_BYTES:
            raise _ParseProblem("html_input_limit")
        doc = _html_tree(body, "text/html; charset=utf-8")
    except (_ParseProblem, etree.ParserError, ValueError) as exc:
        policy = deny_publication(headers, "html_parse_limit" if isinstance(exc, _ParseProblem) else "unparseable_html")
        return {**empty, "index_allowed": False, "publication": policy, "warnings": policy["diagnostics"]}
    # Inspect publication tags before dropping hidden nodes: a restrictive tag
    # is still operative when a surrounding visual element is hidden.
    policy = publication_rules(headers, doc)
    doc = _clean_tree(doc)
    out = _readable(doc, url)
    out["links"] = collect_links(doc, url) if policy["follow_allowed"] else []
    public = out
    if "data-nosnippet" in policy["directives"]:
        snippet_doc = deepcopy(doc)
        for element in list(snippet_doc.iter()):
            if isinstance(element.tag, str) and element.get("data-nosnippet") is not None:
                _remove_tree(element)
        # Re-extract both text and title from the permitted DOM. Reusing the
        # canonical title could expose a heading inside an excluded subtree.
        public = _readable(snippet_doc, url)
    out["publication"] = publication_text(policy, public["text"], public["title"])
    out["index_allowed"] = policy["index_allowed"]
    return out


def extract_text(body: str, url: str, content_type: str, headers: dict[str, str] | None = None) -> dict:
    text = body
    if content_type == "application/json":
        try:
            text = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except (ValueError, RecursionError):
            pass
    name = urlsplit(url).path.rsplit("/", 1)[-1] or url
    policy = publication_rules(headers)
    warnings = ["text_truncated"] if len(text.encode("utf-8", "replace")) > MAX_TEXT_BYTES else []
    text, title = utf8_text(text.strip(), MAX_TEXT_BYTES), utf8_text(name, 1024)
    return {"extract_status": "ok" if text else "empty", "text": text, "title": title,
            "links": [], "metadata": {}, "index_allowed": policy["index_allowed"],
            "publication": publication_text(policy, text, title), "warnings": warnings, "extractor": "plain-text"}
