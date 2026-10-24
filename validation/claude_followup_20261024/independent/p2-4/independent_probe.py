"""Frozen independent P2-3/P2-4 probe; pure Python, no sockets or source edits.

Set PYTHONPATH to the exact parent-frozen production tree and select --suite.
This probe deliberately does not import or execute the production author's tests.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
from pathlib import Path
import sys
import traceback

from yarl import URL

from bzs.worker import discovery, fetch, revalidation, robots


CASES = []
ASSERTIONS = 0


def case(suite, name):
    def register(function):
        CASES.append((suite, name, function))
        return function
    return register


def equal(actual, expected, label):
    global ASSERTIONS
    ASSERTIONS += 1
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def decision(record, path):
    return robots.rule_decision(record, URL("https://independent.example" + path))["allowed"]


@case("robots", "foreign_group_cannot_allow_a_previously_denied_path")
def foreign_group_boundary():
    record = robots.parse_robots(
        b"User-agent: BzsBot\nDisallow: /private\nAllow: /private/open\n"
        b"User-agent: ExampleCrawler/2026.4\nAllow: /private\nDisallow: /public\n"
        b"Sitemap: https://metadata.example/index.xml\n"
        b"User-agent: bZsBoT\nCrawl-delay: 4\nDisallow: /archive\n")
    equal(record["state"], "rules", "foreign token isolated")
    equal(record["rules"], [{"pattern": "/private", "allow": False},
                            {"pattern": "/private/open", "allow": True},
                            {"pattern": "/archive", "allow": False}], "applicable rules merged without foreign leakage")
    equal(record["sitemaps"], ["https://metadata.example/index.xml"], "global sitemap preserved")
    for path, allowed in (("/private", False), ("/private/open/file", True),
                          ("/public", True), ("/archive/old", False)):
        equal(decision(record, path), allowed, path)


for foreign_first in (False, True):
    @case("robots", f"valid_member_in_mixed_group_foreign_first_{foreign_first}")
    def mixed_members(foreign_first=foreign_first):
        agents = ["BzsBot", "Example Crawler/7"]
        if foreign_first:
            agents.reverse()
        body = "".join(f"User-agent: {agent}\n" for agent in agents)
        body += "Sitemap: https://metadata.example/mixed.xml\nDisallow: /closed\nAllow: /closed/exception$\n"
        record = robots.parse_robots(body.encode())
        equal(record["state"], "rules", "mixed group retained")
        equal(len(record["rules"]), 2, "both applicable rules retained")
        equal(decision(record, "/closed/a"), False, "deny still applies")
        equal(decision(record, "/closed/exception"), True, "specific allow applies")
        equal(decision(record, "/closed/exception/extra"), False, "anchor remains exact")


@case("robots", "empty_rule_still_ends_previous_group_at_next_agent")
def empty_rule_boundary():
    record = robots.parse_robots(
        b"User-agent: BzsBot\r\nDisallow:\r\n"
        b"User-agent: ExampleCrawler/2\r\nDisallow: /\r\n"
        b"User-agent: BzsBot\r\nDisallow: /sealed\r\n")
    equal(record["state"], "rules", "empty rule group preserved")
    equal(record["rules"], [{"pattern": "/sealed", "allow": False}], "foreign deny-all not leaked")
    equal(decision(record, "/ordinary"), True, "ordinary path remains allowed")
    equal(decision(record, "/sealed"), False, "later applicable group not discarded")


@case("robots", "wildcard_fallback_and_specific_group_precedence_remain")
def wildcard_and_precedence():
    body = (b"User-agent: *\nDisallow: /staging\n"
            b"User-agent: ExampleCrawler/9\nAllow: /staging\n"
            b"User-agent: *\nAllow: /staging/public\n")
    record = robots.parse_robots(body)
    equal(decision(record, "/staging/internal"), False, "wildcard group retained")
    equal(decision(record, "/staging/public"), True, "wildcard groups merged")
    specific = robots.parse_robots(body + b"User-agent: BzsBot\nDisallow:\n")
    equal(specific["rules"], [], "empty exact group still takes precedence")
    equal(decision(specific, "/staging/internal"), True, "wildcard not merged into exact selection")


@case("robots", "invalid_rule_scope_survives_agent_classification")
def invalid_rule_scope():
    unrelated = robots.parse_robots(
        b"User-agent: BzsBot\nDisallow: /closed\n"
        b"User-agent: ExampleCrawler/2\nDisallow: /bad path\n")
    equal(unrelated["state"], "rules", "foreign malformed rule remains nonapplicable")
    equal(decision(unrelated, "/closed"), False, "valid prior rule remains")
    applicable = robots.parse_robots(
        b"User-agent: ExampleCrawler/2\nUser-agent: BzsBot\nDisallow: /bad path\n")
    equal(applicable["state"], "deny_all", "mixed applicable invalid rule fails closed")
    equal(applicable["diagnostics"], ["robots_unrepresentable_applicable_rule"], "existing diagnostic preserved")


for bad_agent in ("BzsBot/2.1", "B.z.s.Bot", "Bzs Bot", "Other,BzsBot/2", "*/2", "", "2026", "ＢｚｓＢｏｔ"):
    @case("robots", "malformed_self_or_ambiguous_agent_" + repr(bad_agent))
    def damaged_self(bad_agent=bad_agent):
        body = f"User-agent: {bad_agent}\nDisallow: /\nUser-agent: *\nAllow: /\n".encode()
        record = robots.parse_robots(body)
        equal(record["state"], "deny_all", "unclassifiable/self policy not ignored")
        equal(record["diagnostics"], ["robots_invalid_product_token"], "invalid product diagnosis")
        equal(decision(record, "/ordinary"), False, "later wildcard cannot release denial")


@case("robots", "classification_is_relative_to_requested_product")
def chosen_product():
    body = b"User-agent: NewsAgent/5\nDisallow: /\nUser-agent: *\nAllow: /\n"
    equal(robots.parse_robots(body)["state"], "rules", "foreign relative to BzsBot")
    equal(robots.parse_robots(body, "NewsAgent")["state"], "deny_all", "damaged selected product denied")


@case("robots", "raw_invalid_utf8_never_becomes_an_ignored_foreign_group")
def robots_invalid_bytes():
    body = b"User-agent: ExampleCrawler/2\n# invalid raw byte \xff\nUser-agent: BzsBot\nAllow: /\n"
    record = robots.parse_robots(body)
    equal(record["state"], "deny_all", "raw byte decoding remains strict")
    equal(record["diagnostics"], ["robots_invalid_utf8"], "raw UTF-8 error retained")
    equal(record["body_sha256"], hashlib.sha256(body).hexdigest(), "hash remains physical bytes")


@case("charset", "declared_utf8_keeps_valid_unicode_around_damaged_byte")
def declared_utf8():
    body = "前缀 ".encode() + b"bad\x96byte " + "后缀".encode()
    equal(fetch.decode_body(body, "UTF8"), ("前缀 bad\ufffdbyte 后缀", "utf-8"), "usable declaration wins over guessing")


@case("charset", "declared_ascii_and_gbk_replace_only_bad_sequences")
def declared_others():
    equal(fetch.decode_body(b"before\xffafter", "ascii"), ("before\ufffdafter", "ascii"), "ASCII declaration honored")
    body = "中文".encode("gb18030") + b"\x81"
    equal(fetch.decode_body(body, "GB2312"), ("中文\ufffd", "gb18030"), "existing Chinese alias kept")


@case("charset", "utf8_bom_beats_conflicting_header_meta_and_damage")
def bom_priority():
    body = codecs.BOM_UTF8 + b"<meta charset='windows-1252'>" + "α".encode() + b"\x80"
    expected = ("<meta charset='windows-1252'>α\ufffd", "utf-8-sig")
    equal(fetch.decode_body(body, "windows-1252"), expected, "BOM is authoritative")
    equal(fetch.decode_body(body, "not-a-codec"), expected, "unknown header does not hide BOM")


@case("charset", "other_declared_text_codecs_replace_without_switching_identity")
def other_text_codecs():
    equal(fetch.decode_body(b"a\x81b", "windows-1252"), ("a\ufffdb", "windows-1252"), "undefined CP1252 byte replaced")
    equal(fetch.decode_body(b"A\x00\xff", "utf-16-le"), ("A\ufffd", "utf-16-le"), "incomplete UTF-16 code unit replaced")


@case("charset", "usable_header_remains_ahead_of_conflicting_meta")
def header_precedence():
    body = b"<meta charset='windows-1252'><p>" + "汉字".encode() + b"\x92</p>"
    equal(fetch.decode_body(body, "utf-8"), ("<meta charset='windows-1252'><p>汉字\ufffd</p>", "utf-8"), "damaged header decoding does not fall through")
    windows = b"<meta charset='utf-8'><p>\x80</p>"
    equal(fetch.decode_body(windows, "windows-1252"), ("<meta charset='utf-8'><p>€</p>", "windows-1252"), "valid conflicting header control")


for unusable in ("not-a-codec", "base64_codec", "hex_codec", "zlib_codec", "rot_13", "idna"):
    @case("charset", "unusable_header_falls_back_safely_" + unusable)
    def unavailable_codec(unusable=unusable):
        body = b"<meta charset='utf-8'><p>" + "保留".encode() + b"\x96</p>"
        equal(fetch.decode_body(body, unusable), ("<meta charset='utf-8'><p>保留\ufffd</p>", "utf-8"), "usable meta selected")
        plain = b"xn--bcher-kva aGVsbG8="
        equal(fetch.decode_body(plain, unusable), (plain.decode("ascii"), "utf-8"), "nontext codec cannot transform ASCII fallback")


@case("charset", "undeclared_guess_order_and_final_replacement_are_unchanged")
def undeclared_fallbacks():
    equal(fetch.decode_body(b"\x80", None), ("€", "cp1252"), "legacy CP1252 guess")
    equal(fetch.decode_body(b"\x81", None), ("\ufffd", "utf-8-replace"), "legacy ultimate replacement")
    equal(fetch.decode_body("中文".encode("gb18030"), None), ("中文", "gb18030"), "legacy GB18030 guess")


@case("charset", "html_discovery_preserves_valid_unicode_link_after_bad_title_byte")
def html_link():
    body = b"<html><title>bad\x96title</title><a href='/" + "资料".encode() + b"'>link</a></html>"
    result = discovery.discover(body, "https://independent.example/page", content_type="text/html; charset=UTF-8")
    equal(result["parse_status"], "ok", "display decoder recovers HTML")
    equal([row["url"] for row in result["candidates"]], ["https://independent.example/%E8%B5%84%E6%96%99"], "valid IRI was not reinterpreted")


@case("charset", "robots_uses_raw_bytes_despite_a_valid_display_declaration")
def strict_robot_response():
    body = b"User-agent: BzsBot\nDisallow: /closed\n# bad byte \x96\n"
    record, _ = robots.response_record(origin="https://independent.example", status="ok", http_status=200,
        headers={"content-type": "text/plain; charset=windows-1252", "cache-control": "max-age=60"},
        body=body, truncated=False, now_ms=1700000000000)
    equal(record["state"], "deny_all", "robots did not use display-decoded text")
    equal(record["diagnostics"], ["robots_invalid_utf8"], "strict robots error preserved")
    equal(record["body_sha256"], hashlib.sha256(body).hexdigest(), "robots provenance remains raw bytes")


@case("charset", "xml_rejects_bad_tail_without_publishing_valid_prefix")
def strict_xml():
    body = (b'<?xml version="1.0" encoding="UTF-8"?><urlset><url><loc>/good</loc></url>'
            b'<url><loc>/bad\x96</loc></url></urlset>')
    result = discovery.discover(body, "https://independent.example/sitemap.xml", content_type="application/xml; charset=windows-1252")
    equal(result["parse_status"], "invalid", "raw XML declaration remains strict")
    equal(result["candidates"], [], "valid prefix not published after bad tail")
    equal(result["diagnostics"]["errors"], ["malformed_xml"], "XML parse error retained")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("robots", "charset", "all"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("refusing to overwrite prior evidence")
    rows = []
    for suite, name, function in CASES:
        if args.suite not in (suite, "all"):
            continue
        before = ASSERTIONS
        try:
            function()
            rows.append({"suite": suite, "name": name, "passed": True, "assertions": ASSERTIONS - before})
        except Exception as error:
            rows.append({"suite": suite, "name": name, "passed": False, "assertions": ASSERTIONS - before,
                         "error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()})
    files = {}
    for module in (robots, fetch, discovery, revalidation):
        source = Path(module.__file__).resolve()
        files[module.__name__] = {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    report = {"role": "independent probe author source_policy; production patches authored by revocation",
              "label": args.label, "suite": args.suite, "scope": "pure Python functions; no HTTP, DNS, native build or source writes",
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "python": sys.version, "input_modules": files, "passed": sum(row["passed"] for row in rows),
              "total": len(rows), "assertions": ASSERTIONS, "cases": rows}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
