import pytest

from bzs.worker.extract import extract_html, extract_text, looks_like_challenge
from bzs.worker.fetch import decode_body
from tests import fakeweb


def test_main_content_without_hidden_or_boilerplate():
    out = extract_html(fakeweb.ARTICLE, "https://site.example/a")
    assert out["extract_status"] == "ok"
    assert "resumable crawling survives restarts" in out["text"]
    assert "std::expected" in out["text"]
    for junk in ("IGNORE PREVIOUS INSTRUCTIONS", "buy cheap watches", "Copyright footer"):
        assert junk not in out["text"]
    assert out["title"] == "Resumable crawling guide"
    assert out["index_allowed"] is True


def test_hidden_variants_removed():
    html = ("<html><body><main><p>Visible paragraph with enough words to keep the extractor happy about it.</p>"
            "<div style='display: none'>SECRET_A</div><span aria-hidden='true'>SECRET_B</span>"
            "<p style='visibility:hidden'>SECRET_C</p><template>SECRET_D</template></main></body></html>")
    text = extract_html(html, "https://x.example/")["text"]
    assert "Visible paragraph" in text
    assert not any(s in text for s in ("SECRET_A", "SECRET_B", "SECRET_C", "SECRET_D"))


def test_noindex_meta_and_header():
    html = "<html><head><meta name='robots' content='noindex,follow'></head><body><p>" + "word " * 50 + "</p></body></html>"
    assert extract_html(html, "https://x.example/")["index_allowed"] is False
    plain = "<html><body><p>" + "word " * 50 + "</p></body></html>"
    assert extract_html(plain, "https://x.example/", {"x-robots-tag": "noindex"})["index_allowed"] is False


@pytest.mark.parametrize("value,allowed", [
    ("none, follow", False), ("NOINDEX\nnofollow", False), ("googlebot: noindex", True),
    ("googlebot: noindex\nnoindex", False), ("bzs861: none, follow", False),
    ("max-snippet: 0, noindex", False), ("googlebot: nofollow, noindex", True),
    (" none follow ", False), ("NOINDEX\tfollow", False), ("googlebot: none follow", True),
])
def test_robots_headers_apply_to_text_with_independent_field_scope(value, allowed):
    for content_type in ("text/plain", "text/markdown", "application/json"):
        out = extract_text("A readable document", "https://site.example/document", content_type,
                           {"x-robots-tag": value})
        assert out["text"] and out["index_allowed"] is allowed


@pytest.mark.parametrize("content", [" none follow ", "\tNOINDEX\nfollow", "follow,\fnone\rfollow"])
def test_meta_robots_accepts_comma_and_ascii_whitespace_token_boundaries(content):
    html = f"<html><head><meta name=' robots ' content='{content}'></head><body><p>Readable text.</p></body></html>"
    assert extract_html(html, "https://site.example/")["index_allowed"] is False


def test_links_are_absolute_http_only():
    html = ("<html><body><main><p>" + "text " * 40 + "</p><a href='/a'>a</a><a href='https://o.example/b'>b</a>"
            "<a href='javascript:x()'>c</a><a href='mailto:x@y'>d</a></main></body></html>")
    assert extract_html(html, "https://site.example/dir/page")["links"] == ["https://site.example/a", "https://o.example/b"]


def test_challenge_detection():
    assert looks_like_challenge(503, fakeweb.CHALLENGE)
    assert not looks_like_challenge(200, fakeweb.ARTICLE)


def test_charset_decoding():
    text, cs = decode_body(fakeweb.ZH_ARTICLE.encode("gbk"), None)
    assert "断点续爬" in text and cs == "gb18030"
    text, cs = decode_body("中文".encode("utf-8"), "utf-8")
    assert text == "中文"
    text, _ = decode_body("<p>中文</p>".encode("gb18030"), None)
    assert "中文" in text
