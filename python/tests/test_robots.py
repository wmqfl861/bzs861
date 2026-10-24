"""Pure RFC-oriented fixtures; none of these tests resolve or fetch a URL."""
import pytest
from yarl import URL

from bzs.worker.robots import MAX_ROBOTS_BYTES, POLICY_VERSION, parse_robots, response_record, rule_decision


def decision(text, path, *, token="BzsBot"):
    parsed = parse_robots(text.encode("utf-8"), token)
    return rule_decision(parsed, URL("https://fixture.test" + path))


def test_specific_groups_merge_case_insensitively_without_wildcard_rules():
    text = """User-agent: *
Disallow: /
User-Agent: bzsbot
Disallow: /one
Sitemap: https://fixture.test/map.xml
User-Agent: OtherBot
Disallow: /other
User-Agent: BZSBOT
Disallow: /two
Allow: /one/public
"""
    parsed = parse_robots(text.encode())
    assert parsed["version"] == POLICY_VERSION
    assert len(parsed["rules"]) == 3
    assert parsed["sitemaps"] == ["https://fixture.test/map.xml"]
    for path in ("/one", "/two/leaf"):
        assert not rule_decision(parsed, URL("https://fixture.test" + path))["allowed"]
    for path in ("/", "/other", "/one/public/doc"):
        assert rule_decision(parsed, URL("https://fixture.test" + path))["allowed"]


@pytest.mark.parametrize("text,path,allowed", [
    ("User-agent: *\nDisallow: /locked", "/locked", False),
    ("User-agent: OtherBot\nDisallow: /", "/locked", True),
    ("Disallow: /\nUser-agent: BzsBot", "/locked", True),
    ("User-agent: *\nDisallow: /\nUser-agent: BzsBot", "/locked", True),
    ("User-agent: *\nDisallow:\nUser-agent: OtherBot\nDisallow: /", "/locked", True),
    ("User-agent: *\nDisallow: /locked\nAllow: /locked/public", "/locked/public/doc", True),
    ("User-agent: *\nDisallow: /locked\nAllow: /locked", "/locked", True),
    ("User-agent: *\nAllow: /locked\nDisallow: /locked", "/locked", True),
    ("User-agent: *\nDisallow: /Locked", "/locked", True),
    ("User-agent: *\nDisallow: /one$", "/one/more", True),
    ("User-agent: *\nDisallow: /one$", "/one", False),
    ("User-agent: *\nDisallow: /*.gif$", "/images/a.gif", False),
    ("User-agent: *\nDisallow: *.gif$", "/images/a.gif", False),
    ("User-agent: *\nDisallow: /*.gif$", "/images/a.gif?view=1", True),
    ("User-agent: *\nDisallow: /a*b\nAllow: /ab", "/ab", True),
    ("User-agent: *\nDisallow: /a*b*c$", "/axxbxxc", False),
    ("User-agent: *\nDisallow: /a*b*c$", "/axxbxxc/more", True),
    ("User-agent: *\nDisallow: /query?private=1$", "/query?private=1", False),
    ("User-agent: *\nDisallow: /query?private=1$", "/query?private=10", True),
    ("User-agent: *\nDisallow: /query$", "/query#client-only", False),
    ("User-agent: *\nDisallow: /topic%23secret", "/topic#secret", True),
    ("User-agent: *\nDisallow: /topic%23secret", "/topic%23secret", False),
    ("User-agent: *\nDisallow: /foo/%62ar", "/foo/bar", False),
    ("User-agent: *\nDisallow: /foo/bar", "/foo/%62ar", False),
    ("User-agent: *\nDisallow: /foo%2fbar", "/foo/bar", True),
    ("User-agent: *\nDisallow: /foo%2fbar", "/foo%2Fbar", False),
    ("User-agent: *\nDisallow: /中文", "/%E4%B8%AD%E6%96%87", False),
    ("User-agent: *\nDisallow: /%E4%B8%AD%E6%96%87", "/中文", False),
    ("User-agent: *\nDisallow: /中文*\nAllow: /%E4%B8%AD%E6%96%87$", "/中文", True),
    ("User-agent: *\nDisallow: /literal%2A.html", "/literal*.html", False),
    ("User-agent: *\nDisallow: /literal%2A.html", "/literalXXX.html", True),
    ("User-agent: *\nDisallow: /literal%24", "/literal$", False),
    ("User-agent: *\nDisallow: /literal%24", "/literal", True),
    ("User-agent: *\nDisallow: / # trailing comment", "/robots.txt", True),
])
def test_versioned_path_comparison(text, path, allowed):
    assert decision(text, path)["allowed"] is allowed


def test_unknown_records_and_comments_do_not_end_a_group():
    text = ("\ufeffUser-agent:\tBzsBot\r\nUser-agent: OtherBot\r\n"
            "Disallow: /one # comment\r\nSitemap: https://fixture.test/s.xml\r\n"
            "Crawl-delay: 99\r\n\r\nUnknown-Key: ignored\r\nDisallow: /two\r\n")
    for path in ("/one", "/two"):
        assert not decision(text, path)["allowed"]
    assert decision(text, "/three")["allowed"]


@pytest.mark.parametrize("body,reason", [
    (b"User-agent: BzsBot\nDisallow: /\xff", "robots_invalid_utf8"),
    (b"User-agent: BzsBot\nDisallow: /\x00secret", "robots_control_character"),
    (b"User-agent: BzsBot\nDisallow /secret", "robots_malformed_policy_directive"),
    (b"User-agent: BzsBot/1\nDisallow: /", "robots_invalid_product_token"),
    (b"User-agent: BzsBot\nDisallow: /broken path", "robots_unrepresentable_applicable_rule"),
])
def test_damaged_policy_never_silently_turns_into_allow(body, reason):
    parsed = parse_robots(body)
    assert parsed["state"] == "deny_all" and parsed["diagnostics"] == [reason]
    assert not rule_decision(parsed, URL("https://fixture.test/private"))["allowed"]


@pytest.mark.parametrize("foreign", ["360Spider", "Sogou web spider", "Googlebot-Image/1.0"])
@pytest.mark.parametrize("agent", ["BzsBot", "*"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_unrelated_invalid_user_agents_keep_group_boundaries(foreign, agent, position):
    groups = [f"User-agent: {agent}\nDisallow: /locked\n",
              f"User-agent: {agent}\nDisallow: /secret\n"]
    groups.insert(position, f"User-agent: {foreign}\nAllow: /locked\nDisallow: /public\n")
    record = parse_robots("".join(groups).encode())
    assert record["state"] == "rules" and record["diagnostics"] == ["ignored_unrelated_product_tokens"]
    assert record["rules"] == [{"pattern": "/locked", "allow": False}, {"pattern": "/secret", "allow": False}]
    for path in ("/locked", "/secret/child"):
        assert not rule_decision(record, URL("https://fixture.test" + path))["allowed"]
    assert rule_decision(record, URL("https://fixture.test/public"))["allowed"]


@pytest.mark.parametrize("foreign", ["360Spider", "Sogou web spider", "Googlebot-Image/1.0"])
@pytest.mark.parametrize("agent", ["BzsBot", "*"])
@pytest.mark.parametrize("foreign_first", [False, True])
def test_ignored_invalid_member_does_not_discard_valid_members_of_same_group(foreign, agent, foreign_first):
    agents = [foreign, agent] if foreign_first else [agent, foreign]
    text = "".join(f"User-agent: {value}\n" for value in agents) + "Disallow: /private\nAllow: /private/public\n"
    record = parse_robots(text.encode())
    assert record["state"] == "rules" and len(record["rules"]) == 2
    assert not rule_decision(record, URL("https://fixture.test/private"))["allowed"]
    assert rule_decision(record, URL("https://fixture.test/private/public"))["allowed"]


def test_empty_rule_still_delimits_an_ignored_invalid_agent_group():
    body = (b"User-agent: BzsBot\nDisallow:\n"
            b"User-agent: 360Spider\nDisallow: /\n"
            b"User-agent: BzsBot\nDisallow: /new\n")
    record = parse_robots(body)
    assert record["state"] == "rules" and record["rules"] == [{"pattern": "/new", "allow": False}]
    assert rule_decision(record, URL("https://fixture.test/public"))["allowed"]
    assert not rule_decision(record, URL("https://fixture.test/new"))["allowed"]


def test_invalid_foreign_member_cannot_erase_an_unrepresentable_applicable_rule():
    record = parse_robots(b"User-agent: BzsBot\nUser-agent: 360Spider\nDisallow: /broken path\n")
    assert record["state"] == "deny_all" and record["diagnostics"] == ["robots_unrepresentable_applicable_rule"]


@pytest.mark.parametrize("value", ["BzsBot/1", "bzsbot 1", "BzsBot2", "Bzs Bot", "Other, BzsBot/1",
                                   "*bot", "*/1", "* BzsBot", "", "360", "爬虫"])
def test_ambiguous_self_wildcard_or_unclassifiable_agents_still_fail_closed(value):
    record = parse_robots((f"User-agent: {value}\nDisallow: /\nUser-agent: *\nAllow: /\n").encode())
    assert record["state"] == "deny_all" and record["diagnostics"] == ["robots_invalid_product_token"]
    assert not rule_decision(record, URL("https://fixture.test/public"))["allowed"]


def test_invalid_agent_classification_is_relative_to_the_selected_product():
    body = b"User-agent: Sogou web spider\nDisallow: /\nUser-agent: *\nAllow: /\n"
    assert parse_robots(body)["state"] == "rules"
    record = parse_robots(body, "Sogou")
    assert record["state"] == "deny_all" and record["diagnostics"] == ["robots_invalid_product_token"]


def test_parser_accepts_full_512_kib_and_rejects_larger_body():
    prefix = b"User-agent: BzsBot\nDisallow: /private\n#"
    body = prefix + b" " * (MAX_ROBOTS_BYTES - len(prefix))
    parsed = parse_robots(body)
    assert parsed["state"] == "rules"
    assert not rule_decision(parsed, URL("https://fixture.test/private"))["allowed"]
    assert parse_robots(body + b" ")["diagnostics"] == ["robots_size_limit"]


def test_large_wildcard_pattern_uses_bounded_nonrecursive_matching():
    text = "User-agent: *\nDisallow: /" + "a*" * 1000 + "z$"
    result = decision(text, "/" + "a" * 2000 + "x")
    assert result["allowed"]


def test_duplicate_rules_are_deduplicated_without_losing_allow_tie():
    parsed = parse_robots(b"User-agent: *\nDisallow: /x\nDisallow: /x\nAllow: /x")
    assert len(parsed["rules"]) == 2
    assert rule_decision(parsed, URL("https://fixture.test/x"))["allowed"]


@pytest.mark.parametrize("headers,delay,expected", [
    ({"cache-control": "max-age=60", "age": "10"}, 5000, 45000),
    ({"cache-control": "max-age=1"}, 1500, None),
    ({"cache-control": "max-age=60", "date": "Thu, 01 Jan 1970 00:00:00 GMT"}, 0, None),
])
def test_cached_policy_age_includes_response_delay_and_apparent_age(headers, delay, expected):
    now = 1_800_000_000_000
    record, _ = response_record(origin="https://fixture.test", status="ok", http_status=200,
                                headers=headers, body=b"User-agent: *\nDisallow: /private", truncated=False,
                                now_ms=now, response_delay_ms=delay)
    if expected is None:
        assert record["state"] == "deny_all"
        assert record["diagnostics"] == ["robots_cache_revalidation_required"]
    else:
        assert record["state"] == "rules" and record["expires_at_ms"] - now == expected


@pytest.mark.parametrize("value", ["Accept-Encoding", "aCcEpT-EnCoDiNg, ACCEPT-ENCODING",
                                   "User-Agent", "Accept", " User-Agent , Accept , Accept-Encoding "])
def test_robots_freshness_supports_only_fixed_request_selections(value):
    record, _ = response_record(origin="https://fixture.test", status="ok", http_status=200,
                                headers={"content-type": "text/plain", "cache-control": "max-age=60", "vary": value},
                                body=b"User-agent: BzsBot\nDisallow: /private", truncated=False,
                                now_ms=1_800_000_000_000)
    assert record["state"] == "rules" and record["expires_at_ms"] - record["fetched_at_ms"] == 60000
    assert not rule_decision(record, URL("https://fixture.test/private"))["allowed"]


@pytest.mark.parametrize("value", ["", " ", ",", ",Accept-Encoding", "Accept-Encoding,",
                                   "Accept-Encoding,,Accept", "Accept-Encoding, ,User-Agent", "*",
                                   "Accept-Encoding,*", "Accept-Encoding,Accept-Language", "User-Agent,Cookie",
                                   "Accept-Encoding;mode=fast", "Accept-Encoding\t", "Accept-Encoding,é",
                                   " " * 4082 + "Accept-Encoding"])
def test_robots_freshness_rejects_unknown_or_ambiguous_vary_selections(value):
    record, _ = response_record(origin="https://fixture.test", status="ok", http_status=200,
                                headers={"content-type": "text/plain", "cache-control": "max-age=60", "vary": value},
                                body=b"User-agent: BzsBot\nDisallow: /private", truncated=False,
                                now_ms=1_800_000_000_000)
    assert record["state"] == "deny_all" and record["diagnostics"] == ["robots_cache_revalidation_required"]
    assert not rule_decision(record, URL("https://fixture.test/public"))["allowed"]
