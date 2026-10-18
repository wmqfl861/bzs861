"""Main-content extraction (trafilatura) with hidden-text removal and page-state checks."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

import lxml.html
import trafilatura
from lxml import etree

EXTRACTOR = f"trafilatura/{trafilatura.__version__}"
_CHALLENGE = ("cf-chl", "challenge-platform", "just a moment...", "attention required! | cloudflare",
              "verify you are human", "ddos-guard", "please enable javascript and cookies",
              "访问验证", "安全验证", "人机验证", "请输入验证码", "滑动验证", "异常访问请求")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_DROP_TAGS = ("script", "style", "template", "svg", "iframe", "object", "embed")


def looks_like_challenge(http_status: int | None, html: str) -> bool:
    head = html[:30000].lower()
    hits = sum(1 for marker in _CHALLENGE if marker in head)
    if hits >= 1 and (http_status in (403, 429, 503) or len(head) < 6000):
        return True
    return "captcha" in head and http_status in (403, 429, 503)


def header_noindex(headers: dict[str, str]) -> bool:
    # Fetcher separates repeated fields with newlines to retain each field's agent scope.
    for value in headers.get("x-robots-tag", "").lower().splitlines():
        applies = True
        for directive in value.split(","):
            directive = directive.strip()
            if ":" in directive:
                agent, directive = (part.strip() for part in directive.split(":", 1))
                if agent in ("unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"):
                    continue
                applies = agent in ("bzs861", "*")
            if applies and {"noindex", "none"}.intersection(re.split(r"[ \t\n\r\f\v]+", directive)):
                return True
    return False


def noindex(headers: dict[str, str], doc) -> bool:
    if header_noindex(headers):
        return True
    for meta in doc.xpath("//meta[@name]"):
        name = (meta.get("name") or "").strip().lower()
        if name in ("robots", "bzs861"):
            content = set(re.split(r"[, \t\n\r\f\v]+", (meta.get("content") or "").lower()))
            if content.intersection(("noindex", "none")):
                return True
    return False


def clean_dom(html: str):
    """Parse and drop elements a reader cannot see (common prompt-injection carriers)."""
    try:
        doc = lxml.html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return None
    doomed = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        style = el.get("style") or ""
        if (el.tag in _DROP_TAGS or el.get("hidden") is not None or (el.get("aria-hidden") or "").lower() == "true"
                or _HIDDEN_STYLE.search(style)):
            doomed.append(el)
    for el in doomed:
        if el.getparent() is not None:
            el.drop_tree()
    return doc


def collect_links(doc, base_url: str, limit: int = 512) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        try:
            absolute = urljoin(base_url, href)
            scheme = urlsplit(absolute).scheme
        except ValueError:
            continue
        if scheme not in ("http", "https") or len(absolute) > 4096 or absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
        if len(out) >= limit:
            break
    return out


def extract_html(html: str, url: str, headers: dict[str, str] | None = None) -> dict:
    headers = headers or {}
    warnings: list[str] = []
    doc = clean_dom(html)
    if doc is None:
        return {"extract_status": "empty", "text": "", "title": "", "links": [], "metadata": {},
                "index_allowed": not header_noindex(headers), "warnings": ["unparseable_html"], "extractor": EXTRACTOR}
    title_el = doc.find(".//title")
    page_title = (title_el.text_content().strip() if title_el is not None else "")[:1024]
    links = collect_links(doc, url)
    index_allowed = not noindex(headers, doc)
    if doc.xpath("//input[@type='password']"):
        warnings.append("login_form_present")
    cleaned = lxml.html.tostring(doc, encoding="unicode")
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
        title = (str(meta.title) if getattr(meta, "title", None) else page_title).strip()[:1024]
    return {"extract_status": status, "text": text.strip(), "title": title, "links": links,
            "metadata": metadata, "index_allowed": index_allowed, "warnings": warnings, "extractor": extractor}


def extract_text(body: str, url: str, content_type: str, headers: dict[str, str] | None = None) -> dict:
    text = body
    if content_type == "application/json":
        try:
            text = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except ValueError:
            pass
    name = urlsplit(url).path.rsplit("/", 1)[-1] or url
    return {"extract_status": "ok" if text.strip() else "empty", "text": text.strip(), "title": name[:1024],
            "links": [], "metadata": {}, "index_allowed": not header_noindex(headers or {}),
            "warnings": [], "extractor": "plain-text"}
