from bzs.urls import to_uri


def test_idn_hosts_become_punycode_and_the_rest_is_untouched():
    assert to_uri("https://例子.测试/路径?q=中文") == "https://xn--fsqu00a.xn--0zwm56d/路径?q=中文"
    assert to_uri("https://zh.wikipedia.org/wiki/网络爬虫") == "https://zh.wikipedia.org/wiki/网络爬虫"
    assert to_uri("http://例子.测试:8080/") == "http://xn--fsqu00a.xn--0zwm56d:8080/"
    assert to_uri("  https://example.com/a  ") == "https://example.com/a"
    assert to_uri("not a url") == "not a url"


def test_idn_normalization_preserves_forbidden_userinfo_for_core_validation():
    for url in ("https://user:secret@例子.测试/路径", "https://@例子.测试/"):
        assert to_uri(url) == url
