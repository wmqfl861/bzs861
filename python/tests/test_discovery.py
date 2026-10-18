"""Pure parsing tests: no worker/server, DNS, live source, or user qrels."""
from __future__ import annotations

import gzip
import json
import socket

import pytest

from bzs.worker.discovery import (
    MAX_ENTRIES,
    MAX_INPUT_BYTES,
    MAX_TREE_DEPTH,
    MAX_TREE_NODES,
    discover,
)

SITE = "https://site.example/maps/index.xml"
SM = "http://www.sitemaps.org/schemas/sitemap/0.9"
ATOM = "http://www.w3.org/2005/Atom"


def parse(text, url=SITE, **options):
    body = text.encode("utf-8") if isinstance(text, str) else text
    return discover(body, url, **options)


def urls(result):
    return [candidate["url"] for candidate in result["candidates"]]


def test_sitemap_namespaces_relative_iri_and_candidate_metadata():
    result = parse(f'''<?xml version="1.0" encoding="UTF-8"?>
      <s:urlset xmlns:s="{SM}" xml:base="../内容/">
        <s:url xml:base="资料/"><s:loc>页面?q=中文&amp;x=1&amp;x=2#section</s:loc></s:url>
      </s:urlset>''', "https://例子.测试/maps/index.xml#old", depth=7)
    parent = "https://xn--fsqu00a.xn--0zwm56d/maps/index.xml"
    assert result["parse_status"] == "ok"
    assert result["document_type"] == "sitemap_urlset"
    assert result["candidates"] == [{
        "url": "https://xn--fsqu00a.xn--0zwm56d/%E5%86%85%E5%AE%B9/%E8%B5%84%E6%96%99/"
               "%E9%A1%B5%E9%9D%A2?q=%E4%B8%AD%E6%96%87&x=1&x=2",
        "parent": parent, "source": "sitemap", "document": None, "depth": 8, "path_depth": 3,
    }]
    assert result["diagnostics"]["examined"] == 1
    assert result["truncated"] is False


def test_sitemap_index_marks_recursive_documents_and_drops_self_and_fragment_duplicates():
    result = parse('''<sitemapindex>
      <sitemap><loc>child.xml#one</loc></sitemap>
      <sitemap><loc>child.xml#two</loc></sitemap>
      <sitemap><loc>index.xml#loop</loc></sitemap>
      <sitemap><loc>https://other.example/map.xml</loc></sitemap>
    </sitemapindex>''')
    assert result["document_type"] == "sitemap_index"
    assert urls(result) == ["https://site.example/maps/child.xml", "https://other.example/map.xml"]
    assert all(row["source"] == "sitemap_index" and row["document"] == "sitemap"
               for row in result["candidates"])
    assert result["diagnostics"]["duplicates"] == 1
    assert result["diagnostics"]["self_links"] == 1


def test_cross_document_cycle_is_metadata_only_and_never_followed_by_parser():
    a = parse("<sitemapindex><sitemap><loc>b.xml</loc></sitemap></sitemapindex>",
              "https://site.example/a.xml")
    b = parse("<sitemapindex><sitemap><loc>a.xml</loc></sitemap></sitemapindex>",
              a["candidates"][0]["url"], depth=1)
    assert urls(b) == ["https://site.example/a.xml"]
    assert b["candidates"][0]["parent"] == "https://site.example/b.xml"
    assert b["candidates"][0]["depth"] == 2  # Persistent cycle detection belongs to the core.


def test_sitemap_only_direct_locations_are_candidates_and_bad_records_are_diagnostic():
    result = parse(f'''<urlset xmlns="{SM}" xmlns:image="urn:image">
      <url><loc>/good</loc><image:image><image:loc>https://images.example/private</image:loc></image:image></url>
      <url><loc>/one</loc><loc>/two</loc></url><url/>
      <url><loc>/not-<b>plain</b></loc></url>
      <image:url><image:loc>https://images.example/also-ignored</image:loc></image:url>
    </urlset>''')
    assert urls(result) == ["https://site.example/good"]
    assert result["parse_status"] == "ok"
    assert result["diagnostics"]["invalid_reasons"] == {
        "multiple_locations": 1, "missing_location": 1, "nested_location": 1,
    }


def test_rss_channel_and_item_base_do_not_publish_channel_home_or_enclosures():
    result = parse('''<rss version="2.0" xml:base="../feeds/">
      <channel xml:base="../articles/"><link>https://home.example/</link>
        <item xml:base="series/"><link xml:base="../2026/">story?b=2&amp;a=1</link>
          <enclosure url="https://media.example/file.mp3"/></item>
        <item><link>//other.example/news#title</link></item>
      </channel></rss>''')
    assert result["document_type"] == "rss"
    assert urls(result) == ["https://site.example/articles/2026/story?b=2&a=1", "https://other.example/news"]
    assert all(row["source"] == "rss" and row["document"] is None for row in result["candidates"])


def test_rss_1_rdf_items_are_supported_without_following_rdf_resources():
    result = parse('''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns="http://purl.org/rss/1.0/" xml:base="https://feeds.example/">
      <channel rdf:about="https://ignored.example/"><link>https://ignored.example/</link></channel>
      <item rdf:about="https://not-a-link.example/"><link>post</link></item>
    </rdf:RDF>''')
    assert urls(result) == ["https://feeds.example/post"]
    assert result["document_type"] == "rss"


def test_atom_inherited_base_and_alternate_links_not_self_or_enclosure():
    result = parse(f'''<feed xmlns="{ATOM}" xml:base="https://feeds.example/root/">
      <link rel="self" href="feed.xml"/>
      <entry xml:base="../stories/">
        <link rel="self" href="entry.xml"/><link rel="enclosure" href="media.mp3"/>
        <link href="one#x"/><link rel="alternate" xml:base="archive/" href="two?q=1&amp;q=2"/>
      </entry></feed>''')
    assert result["parse_status"] == "ok"
    assert result["document_type"] == "atom"
    assert urls(result) == ["https://feeds.example/stories/one", "https://feeds.example/stories/archive/two?q=1&q=2"]
    assert result["diagnostics"]["ignored_links"] == 2


def test_standalone_atom_entry_and_link_base():
    result = parse(f'<a:entry xmlns:a="{ATOM}" xml:base="../"><a:link xml:base="posts/" href="x"/></a:entry>')
    assert urls(result) == ["https://site.example/posts/x"]


def test_invalid_xml_base_does_not_reinterpret_relative_urls_but_absolute_base_can_recover():
    result = parse(f'''<feed xmlns="{ATOM}" xml:base="file:///private/">
      <entry><link href="relative"/><link href="https://other.example/absolute"/>
        <link xml:base="https://good.example/" href="recovered"/></entry></feed>''')
    assert urls(result) == ["https://other.example/absolute", "https://good.example/recovered"]
    assert result["diagnostics"]["invalid_bases"] == 1
    assert result["diagnostics"]["invalid_reasons"] == {"invalid_base": 1}


def test_html_first_base_fragment_dedupe_and_inert_markup():
    result = parse('''<!doctype html><html><head>
      <base href="../文章/"><base href="https://ignored.example/">
      </head><body><a href="页面#one">one</a><a href="页面#two">two</a>
      <template><a href="https://inert.example/">not active</a></template>
      <script>const x = '<a href="https://script.example/">';</script>
      <a href="../maps/index.xml#same">self</a></body></html>''')
    assert result["document_type"] == "html"
    assert urls(result) == ["https://site.example/%E6%96%87%E7%AB%A0/%E9%A1%B5%E9%9D%A2"]
    assert result["diagnostics"]["duplicates"] == 1
    assert result["diagnostics"]["self_links"] == 1
    assert result["candidates"][0]["document"] is None


@pytest.mark.parametrize("name,content,blocked", [
    ("robots", "nofollow", True), ("BZS861", "none", True), ("BzsBot", "nofollow", True),
    (" ROBOTS ", "index, NOFOLLOW", True),
    ("googlebot", "nofollow", False), ("robots", "noindex,follow", False),
])
def test_html_page_nofollow_and_agent_scope(name, content, blocked):
    result = parse(f'<html><head><meta name="{name}" content="{content}"></head>'
                   '<body><a href="/public">public</a></body></html>')
    assert urls(result) == ([] if blocked else ["https://site.example/public"])
    assert result["diagnostics"]["nofollow"] == int(blocked)


def test_html_link_nofollow_and_soft_redirects_are_not_dispatched_or_candidates():
    result = parse('''<html><head><meta http-equiv="refresh" content="0; url=https://redirect.example/">
      </head><body><a rel="external NOFOLLOW" href="/skip">skip</a>
      <a rel="noopener" href="/keep">keep</a>
      <script>window.location.href='https://script.example/';</script></body></html>''')
    assert urls(result) == ["https://site.example/keep"]
    assert result["diagnostics"]["nofollow"] == 1


def test_invalid_first_html_base_cannot_fall_back_to_second_base_or_document():
    result = parse('''<html><head><base href="javascript:evil()"><base href="/safe/">
      </head><body><a href="relative">bad base</a><a href="https://other.example/a">absolute</a></body></html>''')
    assert urls(result) == ["https://other.example/a"]
    assert result["diagnostics"]["invalid_bases"] == 1
    assert result["diagnostics"]["invalid_reasons"] == {"invalid_base": 1}


@pytest.mark.parametrize("encoding,content_type,text,encoded_url", [
    ("utf-8", "text/html; charset=UTF-8", "中文", "%E4%B8%AD%E6%96%87"),
    ("gb18030", "text/html; charset=gbk", "中文", "%E4%B8%AD%E6%96%87"),
    ("cp1251", "text/html; charset=windows-1251", "новости", "%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D0%B8"),
])
def test_html_unicode_charset_uses_existing_decoding(encoding, content_type, text, encoded_url):
    result = parse(f'<html><a href="/{text}">{text}</a></html>'.encode(encoding), content_type=content_type)
    assert urls(result) == ["https://site.example/" + encoded_url]


def test_html_recovery_and_query_text_keep_repeated_keys_and_order():
    result = parse('<a href="/p?b=2&amp;a=1&amp;a=3#one">one<a href="/p?a=1&amp;b=2&amp;a=3#two">two')
    assert result["parse_status"] == "ok"
    assert urls(result) == ["https://site.example/p?b=2&a=1&a=3", "https://site.example/p?a=1&b=2&a=3"]


def test_query_only_relative_urls_clear_or_retain_base_query_correctly():
    result = parse('<a href="?">clear</a><a href="?page=2">next</a><a href="#top">self</a>',
                   "https://site.example/list?page=1")
    assert urls(result) == ["https://site.example/list", "https://site.example/list?page=2"]
    assert result["diagnostics"]["self_links"] == 1


def test_queries_are_candidates_not_locally_collapsed_into_one_resource():
    body = "<urlset>" + "".join(f"<url><loc>/calendar?day={i}</loc></url>" for i in range(10)) + "</urlset>"
    result = parse(body, max_entries=6)
    assert len(result["candidates"]) == 6
    assert result["truncated"] is True
    assert result["diagnostics"]["limits"] == ["entries"]
    # The core's per-origin/path variant cap and scope checks are additional admission rules.
    assert urls(result)[-1].endswith("?day=5")


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "data:text/html,evil", "file:///etc/passwd", "ftp://example.com/f", "mailto:a@b",
    "https://user:private-secret@example.com/", "//user@example.com/", "https://@example.com/",
    "https://example.com:0/", "https://example.com:65536/", "https://example.com:bad/",
    "https://example.com../", "https://a..example.com/", "https://bad_host.example/", "https://%65xample.com/",
    "https://[fe80::1%25zone]/", "https://", "//", "https://example.com/\\evil", "/bad%xx",
    "/with&#10;newline", "/with&#127;delete",
])
def test_unsafe_or_malformed_url_references_are_diagnostic_without_echoing_secrets(href):
    result = parse(f'<html><a href="{href}">bad</a></html>')
    assert urls(result) == []
    assert result["parse_status"] == "ok"
    assert result["diagnostics"]["invalid"] == 1
    assert "private-secret" not in json.dumps(result)


@pytest.mark.parametrize("href", ["/" + "a" * 4096, "/" + "中" * 500])
def test_url_length_is_checked_before_and_after_iri_encoding(href):
    result = parse(f'<a href="{href}">long</a>')
    assert urls(result) == []
    assert result["diagnostics"]["invalid_reasons"] == {"url_too_long": 1}


def test_canonical_url_forms_ipv6_root_dot_default_port_and_encoded_slashes():
    result = parse('''<a href="https://EXAMPLE.com.:443/b">one</a>
      <a href="https://example.com/b#two">dup</a><a href="https://[2001:4860:4860::8888]/a%2fb">v6</a>''')
    assert urls(result) == ["https://example.com/b", "https://[2001:4860:4860::8888]/a%2fb"]
    assert result["diagnostics"]["duplicates"] == 1
    assert result["candidates"][1]["path_depth"] == 1


def test_existing_ascii_percent_encoding_is_preserved_exactly_for_core_item_identity():
    result = parse('''<a href="/%61?x=%62&amp;y=%2f&amp;x=1%202+3#one">one</a>
      <a href="/%61?x=%62&amp;y=%2f&amp;x=1%202+3#two">duplicate fragment</a>
      <a href="/%61?x=%62&amp;y=%2F&amp;x=1%202+3">distinct encoded bytes</a>''')
    assert urls(result) == ["https://site.example/%61?x=%62&y=%2f&x=1%202+3",
                            "https://site.example/%61?x=%62&y=%2F&x=1%202+3"]
    assert result["diagnostics"]["duplicates"] == 1


def test_relative_dot_segments_are_resolved_without_decoding_encoded_segments():
    result = parse('<a href="../posts/./one">normal relative</a><a href="../%2e%2e/secret">encoded</a>')
    assert urls(result) == ["https://site.example/posts/one", "https://site.example/%2e%2e/secret"]
    # The core rejects the ambiguous encoded-dot candidate at scope admission.


def test_offscope_and_private_ip_candidates_do_not_imply_network_authorization():
    result = parse('<a href="https://other.example/a">outscope</a><a href="http://127.0.0.1/a">private</a>')
    assert urls(result) == ["https://other.example/a", "http://127.0.0.1/a"]
    assert all(row["document"] is None for row in result["candidates"])


@pytest.mark.parametrize("body", [
    '<!DOCTYPE urlset [<!ENTITY x "https://secret.example/">]><urlset><url><loc>&x;</loc></url></urlset>',
    '<!DOCTYPE urlset SYSTEM "https://external.example/evil.dtd"><urlset/>',
    '<!DOCTYPE urlset [<!ENTITY % p SYSTEM "file:///private/evil.dtd">%p;]><urlset/>',
    '<!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///private/secret">]><urlset><url><loc>&x;</loc></url></urlset>',
])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_dtd_and_custom_entities_are_rejected_including_utf16(body, encoding, monkeypatch):
    calls = []

    def forbid(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("offline discovery attempted a connection")

    monkeypatch.setattr(socket, "create_connection", forbid)
    result = parse(body.encode(encoding), kind="sitemap")
    assert result["parse_status"] == "invalid"
    assert result["diagnostics"]["errors"] == ["xml_dtd_forbidden"]
    assert urls(result) == [] and calls == []
    assert "private/secret" not in json.dumps(result)


def test_xinclude_and_stylesheet_processing_instructions_are_never_executed(tmp_path):
    external = tmp_path / "external.xml"
    external.write_text('<url><loc>https://must-not-appear.example/private</loc></url>', encoding="utf-8")
    result = parse(f'''<?xml version="1.0"?><?xml-stylesheet href="{external.as_uri()}"?>
      <urlset xmlns:xi="http://www.w3.org/2001/XInclude">
      <xi:include href="{external.as_uri()}"/><url><loc>/public</loc></url></urlset>''')
    assert urls(result) == ["https://site.example/public"]


def test_predefined_and_numeric_xml_escapes_are_text_not_custom_entities():
    result = parse('<urlset><url><loc>/a?q=one&amp;n=&#50;</loc></url></urlset>')
    assert urls(result) == ["https://site.example/a?q=one&n=2"]


@pytest.mark.parametrize("body", [
    '<urlset><url><loc>/good</loc></url><broken></urlset>',
    '<urlset><url><loc>/good</loc></url><url><loc>&undeclared;</loc></url></urlset>',
    '<urlset><url><loc>/good</loc></url></urlset><extra/>',
])
def test_invalid_xml_cannot_publish_a_valid_prefix_even_when_entry_limit_would_be_exhausted(body):
    result = parse(body, max_entries=1)
    assert result["parse_status"] == "invalid"
    assert urls(result) == []
    assert result["diagnostics"]["errors"] == ["malformed_xml"]


def test_xml_xhtml_is_supported_but_its_doctype_is_still_forbidden():
    body = '<html xmlns="http://www.w3.org/1999/xhtml"><head><base href="/posts/"/></head>' \
           '<body><a href="one">one</a></body></html>'
    result = parse(body, content_type="application/xhtml+xml")
    assert urls(result) == ["https://site.example/posts/one"]
    assert result["document_type"] == "html"
    forbidden = parse('<!DOCTYPE html>' + body, content_type="application/xhtml+xml")
    assert forbidden["parse_status"] == "invalid"


@pytest.mark.parametrize("body,options,code", [
    ('<unsupported><link>/x</link></unsupported>', {}, "unsupported_xml"),
    ('<urlset xmlns="urn:wrong"><url><loc>/x</loc></url></urlset>', {}, "unsupported_xml"),
    ('<feed xmlns="urn:wrong"/>', {}, "unsupported_xml"),
    ('<urlset/>', {"kind": "rss"}, "document_kind_mismatch"),
    ('<rss/>', {"kind": "html"}, "document_kind_mismatch"),
    ('not markup', {"content_type": "image/png"}, "unsupported_content_type"),
    ('plain text with https://example.com', {}, "unsupported_document"),
])
def test_unsupported_documents_are_explicit_not_an_empty_success(body, options, code):
    result = parse(body, **options)
    assert result["parse_status"] == "unsupported"
    assert result["diagnostics"]["errors"] == [code]
    assert urls(result) == []


def test_gzip_is_not_decompressed_here_and_is_reported_explicitly():
    result = parse(gzip.compress(b'<urlset><url><loc>/private</loc></url></urlset>'), kind="sitemap")
    assert result["parse_status"] == "unsupported"
    assert result["diagnostics"]["errors"] == ["compressed_body_not_supported"]
    assert urls(result) == []


def test_entry_limit_counts_duplicates_and_invalid_records_instead_of_only_output():
    result = parse('<a href="/one">one</a><a href="/one#x">duplicate</a><a href="/two">two</a>', max_entries=2)
    assert urls(result) == ["https://site.example/one"]
    assert result["truncated"] is True
    assert result["diagnostics"]["examined"] == 2
    assert result["diagnostics"]["duplicates"] == 1
    bad = parse('<a href="javascript:evil()">bad</a><a href="/two">two</a>', max_entries=1)
    assert urls(bad) == [] and bad["truncated"] is True
    assert bad["diagnostics"]["invalid"] == 1


def test_exact_entry_and_input_limits_are_not_falsely_reported_as_truncated():
    body = b'<urlset><url><loc>/one</loc></url></urlset>'
    exact = parse(body, max_entries=1, max_input_bytes=len(body))
    assert urls(exact) == ["https://site.example/one"] and exact["truncated"] is False
    too_big = parse(body, max_input_bytes=len(body) - 1)
    assert too_big["parse_status"] == "invalid" and too_big["truncated"] is True
    assert urls(too_big) == []
    assert too_big["diagnostics"]["errors"] == ["input_too_large"]
    assert too_big["diagnostics"]["limits"] == ["input_bytes"]


@pytest.mark.parametrize("kind,body,code", [
    ("sitemap", "<urlset>" + "<x>" * MAX_TREE_DEPTH + "</x>" * MAX_TREE_DEPTH + "</urlset>", "xml_depth_limit"),
    ("html", "<html><body>" + "<div>" * MAX_TREE_DEPTH + "</div>" * MAX_TREE_DEPTH + "</body></html>", "html_depth_limit"),
    ("sitemap", "<urlset>" + "<x/>" * MAX_TREE_NODES + "</urlset>", "xml_node_limit"),
    ("html", "<html><body>" + "<br>" * MAX_TREE_NODES + "</body></html>", "html_node_limit"),
])
def test_tree_resource_limits_are_not_bypassable_with_non_candidate_nodes(kind, body, code):
    result = parse(body, kind=kind)
    assert result["parse_status"] == "invalid"
    assert result["diagnostics"]["errors"] == [code]
    assert result["truncated"] is True
    assert urls(result) == []


@pytest.mark.parametrize("options", [
    {"depth": True}, {"depth": -1}, {"depth": 1.0}, {"depth": 1001},
    {"max_entries": True}, {"max_entries": 0}, {"max_entries": MAX_ENTRIES + 1}, {"max_entries": "2"},
    {"max_input_bytes": False}, {"max_input_bytes": 0}, {"max_input_bytes": MAX_INPUT_BYTES + 1},
    {"max_input_bytes": 1.0}, {"kind": "xml"}, {"kind": []}, {"content_type": None},
])
def test_invalid_options_cannot_raise_hard_limits_or_coerce_types(options):
    with pytest.raises(ValueError):
        parse("<urlset/>", **options)


@pytest.mark.parametrize("url", ["/relative", "file:///private", "https://user:secret@example.com", "https://example.com../"])
def test_final_document_url_must_itself_be_valid(url):
    with pytest.raises(ValueError, match="invalid final_url"):
        parse("<urlset/>", url=url)


def test_bytes_only_input_and_empty_document_are_distinct_from_bad_options():
    with pytest.raises(ValueError):
        discover("<urlset/>", SITE)
    result = parse(b" \n\t")
    assert result["parse_status"] == "invalid"
    assert result["diagnostics"]["errors"] == ["empty_document"]
