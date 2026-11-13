"""Re-run source parsers against immutable probe responses, without any network I/O."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .probe_format import MAX_HITS, SCHEMA_VERSION, parser_identity
from .artifact_output import write_new_directory
from .probe_state import read_body, read_report_with_digest
from .worker.fetch import FetchResult
from .worker.sources import ADAPTERS
from .worker.sources.base import SourceError

_PARAMS = frozenset(("adapter", "base_url", "lang", "region", "site", "site_url"))
_HEADERS = frozenset(("content-type", "content-language", "content-encoding", "retry-after", "date",
                      "x-ratelimit-remaining", "x-ratelimit-reset", "x-robots-tag", "etag", "last-modified"))
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any, field: str, maximum: int = 8192, *, empty: bool = False) -> str:
    _need(isinstance(value, str) and len(value) <= maximum and (empty or bool(value)),
          f"invalid replay {field}")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"invalid replay {field} Unicode") from exc
    return value


def _integer(value: Any, field: str, low: int, high: int) -> int:
    _need(type(value) is int and low <= value <= high, f"invalid replay {field}")
    return value


def _identity(value: Any) -> dict:
    _need(isinstance(value, dict), "recorded parser identity is missing")
    _need(isinstance(value.get("code_sha256"), str) and bool(_SHA256.fullmatch(value["code_sha256"])),
          "invalid recorded parser code hash")
    for field in ("files", "dependencies"):
        entries = value.get(field)
        _need(isinstance(entries, dict) and 1 <= len(entries) <= 100, f"invalid parser {field}")
        for key, item in entries.items():
            _text(key, "parser identity key", 256)
            _text(item, "parser identity value", 256)
    return value


def _results(value: Any) -> list[dict]:
    _need(isinstance(value, list) and len(value) <= MAX_HITS, "invalid recorded result array")
    for hit in value:
        _need(isinstance(hit, dict) and hit.keys() == {"url", "title", "snippet", "rank"},
              "invalid recorded hit fields")
        _text(hit["url"], "hit URL")
        _text(hit["title"], "hit title", empty=True)
        _text(hit["snippet"], "hit snippet", empty=True)
        _integer(hit["rank"], "hit rank", 1, 1000)
    return value


def _response(out: Path, value: Any, row: dict) -> FetchResult:
    _need(isinstance(value, dict), "recorded response must be an object")
    status = _text(value.get("status"), "fetch status", 80)
    url = _text(value.get("url"), "request URL")
    final_url = _text(value.get("final_url"), "final URL", empty=True)
    http_status = value.get("http_status")
    if http_status is not None:
        _integer(http_status, "HTTP status", 100, 599)
    headers = value.get("headers")
    _need(isinstance(headers, dict) and not headers.keys() - _HEADERS,
          "recorded response contains unsupported header fields")
    for name, text in headers.items():
        _text(text, f"{name} header", 16384, empty=True)
    redirects = value.get("redirects")
    _need(isinstance(redirects, list) and len(redirects) <= 20, "invalid recorded redirects")
    for redirect in redirects:
        _text(redirect, "redirect URL")
    _need(type(value.get("truncated")) is bool, "invalid recorded truncation flag")
    elapsed_ms = _integer(value.get("elapsed_ms"), "fetch duration", 0, (1 << 63) - 1)
    _need(value.get("encoding") == "gzip", "unsupported recorded response encoding")
    _need(row.get("raw") is None or row["raw"] == value.get("raw_path"), "recorded raw paths disagree")
    body = read_body(out, value)
    # No Fetcher is constructed. Only response fields that affect check/parse are
    # reconstructed; wall-clock usage and transient exception messages are not compared.
    return FetchResult(status=status, url=url, final_url=final_url, http_status=http_status,
                       headers=headers, body=body, truncated=value["truncated"],
                       redirects=redirects, elapsed_ms=elapsed_ms)


def _write_output(out: Path, report: dict) -> None:
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    summary = report["summary"]
    markdown = (
        "# Offline probe parser replay\n\n"
        f"Status: **{report['status']}**. Network requests: **0**.\n\n"
        f"Probe report SHA-256: {report['source_report_sha256']}.\n\n"
        f"Parser identity matches recording: {str(report['identity_match']).lower()}.\n\n"
        "| Replayed | Matched | Mismatches | Skipped |\n|---:|---:|---:|---:|\n"
        f"| {summary['replayed']} | {summary['matched']} | {summary['mismatches']} | {summary['skipped']} |\n\n"
        "Detailed differences are in replay_report.json. Skipped rows are not successful parser checks. "
        "A matching replay proves reproducible parsing of these saved responses; it does not establish "
        "current source availability, relevance, source approval or authenticity of the recording.\n"
    )
    write_new_directory(out, {"replay_report.json": raw, "replay_report.md": markdown.encode("utf-8")}, label="replay")

def replay(report_path: str, out_dir: str, *, allow_parser_change: bool = False) -> dict:
    """Compare every completed recorded response with the current source parser.

    Invalid evidence or a parser identity change raises ValueError. An explicitly
    allowed parser change still compares against the original expected results;
    it never rewrites the probe evidence or silently accepts changed output.
    """
    path = Path(report_path)
    _need(path.name == "probe_report.json", "replay requires a probe_report.json file")
    report, report_digest = read_report_with_digest(path.parent)
    _need(type(report.get("schema_version")) is int and report["schema_version"] == SCHEMA_VERSION,
          "unsupported probe evidence schema; record a new schema-2 probe")
    run_id = _text(report.get("run_id"), "run ID", 200)
    identity = report.get("identity")
    _need(isinstance(identity, dict), "probe identity is missing")
    recorded_parser = _identity(identity.get("parser"))
    current_parser = parser_identity()
    identity_match = recorded_parser == current_parser
    _need(identity_match or allow_parser_change,
          "parser identity differs; use --allow-parser-change for an explicit comparison")
    sources = report.get("sources")
    _need(isinstance(sources, dict) and len(sources) <= 200, "invalid recorded source registry")
    parsers = {}
    for source, spec in sources.items():
        _text(source, "source ID", 80)
        _need(isinstance(spec, dict) and spec.get("id") == source, "source identity mismatch")
        adapter = spec.get("adapter_id")
        _need(isinstance(adapter, str) and adapter in ADAPTERS, "unknown recorded source adapter")
        params = spec.get("params")
        _need(isinstance(params, dict) and not params.keys() - _PARAMS, "invalid recorded parser parameters")
        for key, value in params.items():
            _text(value, f"parser {key}")
        _need(params.get("adapter", adapter) == adapter, "recorded adapter identity mismatch")
        parsers[source] = ADAPTERS[adapter]({"id": source, "params": params}, None)
    rows = report.get("rows")
    _need(isinstance(rows, list) and len(rows) <= 8000, "invalid probe row collection")
    output_rows = []
    seen: set[str] = set()
    for row in rows:
        _need(isinstance(row, dict), "invalid probe row")
        item_id = _text(row.get("item_id"), "item ID", 200)
        _need(item_id not in seen, "duplicate probe item ID")
        seen.add(item_id)
        source = row.get("source")
        _need(isinstance(source, str) and source in parsers, "probe row refers to an unknown source")
        query_id = _text(row.get("query_id"), "query ID", 80)
        query = _text(row.get("query"), "query", 4096)
        attempt = row.get("attempt_state")
        _need(attempt in ("completed", "uncertain"), "invalid probe attempt state")
        result = {"item_id": item_id, "source": source, "query_id": query_id}
        if attempt == "completed" and row.get("status") not in ("timeout", "network_error", "error"):
            _need(isinstance(row.get("response"), dict), "completed parser outcome is missing its recorded response")
        if attempt == "uncertain" or row.get("response") is None:
            output_rows.append({**result, "status": "skipped", "reason":
                                "uncertain_attempt" if attempt == "uncertain" else "no_recorded_response"})
            continue
        expected_status = _text(row.get("status"), "source status", 80)
        expected_results = _results(row.get("results"))
        _need(type(row.get("hits")) is int and row["hits"] == len(expected_results),
              "recorded hit count disagrees with the complete result array")
        _need(expected_status not in ("ok", "empty") or
              expected_status == ("ok" if expected_results else "empty"), "recorded result status disagrees with hits")
        _need(expected_status in ("ok", "empty") or not expected_results, "failed probe row contains hits")
        response = _response(path.parent, row["response"], row)
        actual_results = []
        try:
            parser = parsers[source]
            parser.check(response)
            actual_results = [hit.as_dict() for hit in parser.parse(response, query, MAX_HITS)[:MAX_HITS]]
            actual_status = "ok" if actual_results else "empty"
        except SourceError as exc:
            actual_status = exc.status
        except Exception:
            # A parser crash is a regression outcome; do not leak response text
            # through a third-party exception message into the replay summary.
            actual_status = "error"
        matched = actual_status == expected_status and actual_results == expected_results
        output_rows.append({**result, "status": "matched" if matched else "mismatch",
                            "expected": {"status": expected_status, "results": expected_results},
                            "actual": {"status": actual_status, "results": actual_results}})
    summary = {"replayed": sum(row["status"] != "skipped" for row in output_rows),
               "matched": sum(row["status"] == "matched" for row in output_rows),
               "mismatches": sum(row["status"] == "mismatch" for row in output_rows),
               "skipped": sum(row["status"] == "skipped" for row in output_rows)}
    output = {"schema_version": 1, "kind": "offline_probe_parser_replay",
              "status": "no_replayable_rows" if not summary["replayed"] else
                        "failed" if summary["mismatches"] else "passed",
              "created_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
              "source_report_sha256": report_digest,
              "recorded_parser": recorded_parser, "current_parser": current_parser,
              "identity_match": identity_match, "allow_parser_change": allow_parser_change,
              "network_requests": 0, "summary": summary, "rows": output_rows}
    _write_output(Path(out_dir), output)
    return output
