"""Publication restrictions are independent of permission to read canonical text."""
import pytest

from bzs.worker.extract import MAX_TEXT_BYTES, extract_html, extract_text
from bzs.worker.publication import MAX_META_BYTES, MAX_POLICY_BYTES, MAX_SNIPPET_BYTES, publication_rules


URL = "https://publication.example/page"
BODY = "Readable public material about bounded crawls and durable request accounting. " * 12


@pytest.mark.parametrize("agent,applies", [("robots", True), ("BzsBot", True), ("bzs861", True),
                                          ("googlebot", False), ("googlebot-news", False), ("otherbot", False)])
def test_meta_agent_scope_has_an_explicit_product_identity(agent, applies):
    html = f'<html><head><title>Public title</title><meta name="{agent}" content="noindex,nosnippet,nofollow"></head><body>{BODY}</body></html>'
    result = extract_html(html, URL)
    assert result["text"] and result["title"] == "Public title"
    policy = result["publication"]
    assert policy["version"] == "bzs-publication-1"
    assert result["index_allowed"] is policy["index_allowed"] is not applies
    assert policy["snippet_allowed"] is not applies
    assert policy["follow_allowed"] is not applies
    if applies:
        assert policy["snippet_text"] == policy["snippet_title"] == ""
    else:
        assert policy["snippet_text"] == result["text"] and policy["snippet_title"] == result["title"]


@pytest.mark.parametrize("value,expected", [
    ("noindex", (False, False, True)),
    ("nosnippet", (True, False, True)),
    ("nofollow", (True, True, False)),
    ("none, index, follow, all", (False, False, False)),
    ("BzsBot: nosnippet, nofollow", (True, False, False)),
    ("bzs861: noindex", (False, False, True)),
    ("*: none", (False, False, False)),
    ("googlebot: noindex, nosnippet, nofollow", (True, True, True)),
    ("googlebot: noindex\nnosnippet", (True, False, True)),
    ("noindex\ngooglebot: all\nBzsBot: follow", (False, False, True)),
    ("googlebot: noindex, BzsBot: nosnippet, nofollow", (True, False, False)),
    ("BzsBot: nofollow, googlebot: noindex", (True, True, False)),
    ("max-image-preview: none, max-video-preview: 0", (True, True, True)),
    ("max-snippet: 0", (True, False, True)),
    ("max-snippet: 20", (True, True, True)),
    ("googlebot: max-snippet: 0", (True, True, True)),
    ("BzsBot: max-snippet: 0, noindex", (False, False, True)),
    ("unavailable_after: 01 Jan 2000 00:00:00 GMT, nofollow", (True, True, False)),
])
def test_repeated_http_directives_keep_scope_and_parameter_values(value, expected):
    result = extract_text(BODY, URL, "text/plain", {"X-Robots-Tag": value})
    policy = result["publication"]
    assert (policy["index_allowed"], policy["snippet_allowed"], policy["follow_allowed"]) == expected
    assert result["text"] == BODY.strip()  # Canonical read is a separate permission.


def test_restrictive_header_meta_union_is_not_cleared_by_later_allow_flags():
    html = ('<html><head><meta name="robots" content="nofollow">'
            '<meta name="BzsBot" content="index,follow"><meta name="googlebot" content="noindex"></head>'
            f'<body><p>{BODY}</p><a href="/later">Later page</a></body></html>')
    result = extract_html(html, URL, {"x-robots-tag": "nosnippet\nBzsBot: all"})
    assert result["publication"]["directives"] == ["nofollow", "nosnippet"]
    assert result["index_allowed"] is True and result["links"] == []
    assert result["text"] and result["publication"]["snippet_text"] == ""


@pytest.mark.parametrize("attribute", ["data-nosnippet", 'data-nosnippet=""', 'data-nosnippet="false"'])
def test_data_nosnippet_has_a_separate_dom_and_does_not_leak_titles_or_tails(attribute):
    html = (f'<html><head><title {attribute}>SECRETTITLE</title></head><body><main>'
            f'<h1 {attribute}>SECRETHEADING</h1><p>{BODY}</p><p>Allowed prefix '
            f'<span {attribute}>SECRETCONTENT a private description that remains directly readable</span>'
            ' VISIBLETAIL and additional public commentary about this material.</p></main></body></html>')
    result = extract_html(html, URL)
    assert "SECRETCONTENT" in result["text"]
    assert result["title"] in ("SECRETTITLE", "SECRETHEADING")
    assert "VISIBLETAIL" in result["text"]
    policy = result["publication"]
    assert policy["index_allowed"] and policy["snippet_allowed"]
    assert "data-nosnippet" in policy["directives"]
    assert not any(value in policy["snippet_text"] + policy["snippet_title"]
                   for value in ("SECRETTITLE", "SECRETHEADING", "SECRETCONTENT"))
    assert "VISIBLETAIL" in policy["snippet_text"]


@pytest.mark.parametrize("wrapper", ["div", "section", "span", "article", "body", "html"])
def test_bzs_honors_data_nosnippet_on_every_element_including_whole_document(wrapper):
    # The explicit Bzs policy is more conservative than Google's documented
    # span/div/section scope: there is no fallback to canonical text afterwards.
    if wrapper == "html":
        html = f'<html data-nosnippet><head><title>Private title</title></head><body>{BODY}</body></html>'
    elif wrapper == "body":
        html = f'<html><body data-nosnippet>{BODY}</body></html>'
    else:
        html = f'<html><body><{wrapper} data-nosnippet>{BODY}</{wrapper}></body></html>'
    result = extract_html(html, URL)
    assert result["text"]
    assert result["publication"]["snippet_text"] == ""
    assert result["publication"]["snippet_title"] == ""


def test_hidden_and_inert_material_cannot_become_safe_public_snippets():
    html = (f'<html><body><main><p>{BODY}</p><div hidden>HIDDENBODY</div>'
            '<template><meta name="robots" content="noindex">TEMPLATETEXT</template>'
            '<script>var x = "SCRIPTTEXT";</script></main></body></html>')
    result = extract_html(html, URL)
    assert result["index_allowed"] is True
    for field in (result["text"], result["publication"]["snippet_text"], result["publication"]["snippet_title"]):
        assert not any(marker in field for marker in ("HIDDENBODY", "TEMPLATETEXT", "SCRIPTTEXT"))


@pytest.mark.parametrize("html,headers", [
    ('<html><head><meta name="BzsBot" content="noindex,nosnippet"></head><body></body></html>', {}),
    ("", {"x-robots-tag": "BzsBot: noindex"}),
    (" \n", {"x-robots-tag": "nosnippet"}),
])
def test_empty_readable_body_still_carries_an_enforceable_publication_record(html, headers):
    result = extract_html(html, URL, headers)
    assert result["extract_status"] == "empty" and result["text"] == ""
    assert result["publication"]["snippet_allowed"] is False
    assert result["publication"]["version"] == "bzs-publication-1"


def test_policy_metadata_limits_fail_closed_instead_of_ignoring_late_restrictions():
    headers = {"x-robots-tag": "otherbot: " + "x" * MAX_POLICY_BYTES + "\nnoindex"}
    policy = publication_rules(headers)
    assert not any(policy[key] for key in ("index_allowed", "snippet_allowed", "follow_allowed"))
    assert policy["diagnostics"] == ["publication_metadata_limit"]
    html = f'<html><head><meta name="robots" content="{" " * MAX_META_BYTES}noindex"></head><body>{BODY}</body></html>'
    result = extract_html(html, URL)
    assert result["publication"]["diagnostics"] == ["publication_metadata_limit"]
    assert result["publication"]["snippet_text"] == ""


def test_html_tree_resource_failure_cannot_publish_a_permissive_shortened_tree():
    html = "<html><body>" + "<div>" * 140 + BODY + "</div>" * 140 + "</body></html>"
    result = extract_html(html, URL)
    assert result["extract_status"] == "empty"
    assert result["index_allowed"] is False
    assert result["publication"]["diagnostics"] == ["html_parse_limit"]


def test_utf8_limits_and_nul_handling_apply_to_both_canonical_and_public_fields():
    result = extract_text("\x00" + "界" * MAX_TEXT_BYTES, URL + "/" + "题" * 1500, "text/plain")
    assert len(result["text"].encode()) <= MAX_TEXT_BYTES
    assert len(result["publication"]["snippet_text"].encode()) <= MAX_SNIPPET_BYTES
    assert len(result["title"].encode()) <= 1024
    assert len(result["publication"]["snippet_title"].encode()) <= 1024
    assert "\x00" not in result["text"] + result["publication"]["snippet_text"]
    assert result["warnings"] == ["text_truncated"]


def test_safe_discovery_honors_first_base_relative_nofollow_and_uri_bytes():
    html = (f'<html><head><base href="/docs/"><base href="https://wrong.example/"></head><body>{BODY}'
            '<a href="next/%61?x=%2f&x=%2F">next</a><a href="/excluded" rel="NOFOLLOW">skip</a>'
            '<a href="javascript:alert(1)">script</a><a href="http://user:pass@other.example/">credentials</a>'
            '<a href="/docs/next/%61?x=%2f&x=%2F#fragment">duplicate</a></body></html>')
    result = extract_html(html, URL)
    assert result["links"] == ["https://publication.example/docs/next/%61?x=%2f&x=%2F"]


def test_invalid_first_base_does_not_reinterpret_relative_links():
    html = (f'<html><head><base href="javascript:alert(1)"><base href="/fallback/"></head><body>{BODY}'
            '<a href="relative">unknown</a><a href="https://publication.example/absolute">absolute</a></body></html>')
    assert extract_html(html, URL)["links"] == ["https://publication.example/absolute"]
