import copy
import gzip
import hashlib
import json

import pytest

from bzs.probe_format import parser_identity, safe_response_metadata
from bzs.probe_state import record_body
from bzs.replay import replay
from bzs.worker.fetch import Fetcher, FetchResult


def recording(tmp_path):
    out = tmp_path / "probe"
    out.mkdir()
    report = {"schema_version": 2, "run_id": "synthetic-offline-replay", "identity": {"parser": parser_identity()},
              "sources": {"wiki": {"id": "wiki", "adapter_id": "wikipedia_api", "params": {"lang": "en"}},
                          "ddg": {"id": "ddg", "adapter_id": "duckduckgo_html", "params": {}}}, "rows": []}

    def add(source, body, status, hits, *, http_status=200):
        item = f"i{len(report['rows']) + 1}"
        attempt_id = f"a{len(report['rows']) + 1:06d}"
        response = FetchResult(status="ok" if http_status < 400 else "http_error",
                               url="https://source.example/", final_url="https://source.example/",
                               http_status=http_status, headers={"content-type": "text/html; charset=utf-8"},
                               body=body.encode("utf-8"))
        metadata = {**safe_response_metadata(response), **record_body(out, attempt_id, response.body)}
        row = {"item_id": item, "attempt_id": attempt_id, "source": source, "query_id": "q1", "query": "test",
               "attempt_state": "completed", "status": status, "hits": len(hits), "results": hits,
               "response": metadata}
        if metadata.get("raw_path"):
            row["raw"] = metadata["raw_path"]
        report["rows"].append(row)

    # The fourth result distinguishes full-array replay from the old top-three preview.
    entries = [{"title": f"Page {i}", "snippet": f"About {i}"} for i in range(1, 5)]
    add("wiki", json.dumps({"query": {"search": entries}}), "ok",
        [{"url": f"https://en.wikipedia.org/wiki/Page_{i}", "title": f"Page {i}",
          "snippet": f"About {i}", "rank": i} for i in range(1, 5)])
    add("ddg", "<div class='anomaly-modal'>bots use DuckDuckGo too</div>", "blocked", [])
    add("wiki", "slow down", "rate_limited", [], http_status=429)
    add("wiki", '{"query":{"search":[]}}', "empty", [])
    report["rows"].append({"item_id": "i5", "attempt_id": "a000005", "source": "wiki", "query_id": "q2",
                           "query": "interrupted", "attempt_state": "uncertain", "status": "uncertain"})
    path = out / "probe_report.json"
    save(path, report)
    return path, report


def save(path, report):
    path.write_text(json.dumps(report), encoding="utf-8")


def test_replay_checks_full_results_blocks_empty_and_rate_limit_without_network(tmp_path, monkeypatch):
    path, _ = recording(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("offline replay attempted to construct an HTTP fetcher")

    monkeypatch.setattr(Fetcher, "__init__", forbidden)
    before = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    result = replay(str(path), str(tmp_path / "replay"))
    assert result["status"] == "passed" and result["network_requests"] == 0
    assert result["summary"] == {"replayed": 4, "matched": 4, "mismatches": 0, "skipped": 1}
    assert result["source_report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["rows"][4]["reason"] == "uncertain_attempt"
    assert before == {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    assert (tmp_path / "replay" / "replay_report.md").is_file()


def test_replay_reports_fourth_hit_mismatch_and_never_updates_expectation(tmp_path):
    path, report = recording(tmp_path)
    report["rows"][0]["results"][3]["title"] = "Changed fourth result"
    save(path, report)
    original = path.read_bytes()
    result = replay(str(path), str(tmp_path / "replay"))
    assert result["status"] == "failed" and result["summary"]["mismatches"] == 1
    assert result["rows"][0]["actual"]["results"][3]["title"] == "Page 4"
    assert path.read_bytes() == original


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_replay_cannot_silently_skip_a_completed_parser_outcome_with_missing_response(tmp_path, index):
    path, report = recording(tmp_path)
    report["rows"][index].pop("response")
    report["rows"][index].pop("raw", None)
    save(path, report)
    with pytest.raises(ValueError, match="missing its recorded response"):
        replay(str(path), str(tmp_path / "replay"))
    assert not (tmp_path / "replay").exists()


def test_replay_reports_a_known_transport_failure_without_response_as_skipped(tmp_path):
    path, report = recording(tmp_path)
    report["rows"][-1].update(attempt_state="completed", status="timeout", results=[], hits=0)
    save(path, report)
    result = replay(str(path), str(tmp_path / "replay"))
    assert result["summary"]["skipped"] == 1
    assert result["rows"][-1]["reason"] == "no_recorded_response"


def test_parser_change_requires_explicit_comparison_and_still_detects_regression(tmp_path):
    path, report = recording(tmp_path)
    report["identity"]["parser"]["dependencies"]["python"] = "old-runtime"
    report["rows"][0]["results"][0]["title"] = "Different title"
    save(path, report)
    with pytest.raises(ValueError, match="parser identity differs"):
        replay(str(path), str(tmp_path / "denied"))
    assert not (tmp_path / "denied").exists()
    result = replay(str(path), str(tmp_path / "comparison"), allow_parser_change=True)
    assert result["identity_match"] is False and result["status"] == "failed"


@pytest.mark.parametrize("damage", ["body", "hash", "length", "missing", "traversal", "absolute", "backslash", "encoding"])
def test_replay_refuses_corrupt_missing_or_unsafe_evidence(tmp_path, damage):
    path, report = recording(tmp_path)
    row = report["rows"][0]
    response = row["response"]
    raw = path.parent / response["raw_path"]
    if damage == "body":
        raw.write_bytes(gzip.compress(b"tampered response"))
    elif damage == "hash":
        response["body_sha256"] = "0" * 64
    elif damage == "length":
        response["body_bytes"] += 1
    elif damage == "missing":
        raw.unlink()
    elif damage == "encoding":
        response["encoding"] = "plain"
    else:
        name = {"traversal": "../outside.gz", "absolute": str(raw.resolve()),
                "backslash": "raw\\outside.gz"}[damage]
        response["raw_path"] = row["raw"] = name
    save(path, report)
    with pytest.raises((ValueError, OSError, EOFError)):
        replay(str(path), str(tmp_path / "replay"))
    assert not (tmp_path / "replay").exists()


def test_replay_rejects_gzip_expansion_over_limit(tmp_path, monkeypatch):
    path, report = recording(tmp_path)
    row = report["rows"][0]
    raw = path.parent / row["raw"]
    body = b"x" * 1025
    raw.write_bytes(gzip.compress(body))
    row["response"].update(body_bytes=1000, body_sha256=hashlib.sha256(body).hexdigest())
    save(path, report)
    monkeypatch.setattr("bzs.probe_state.MAX_RAW_BYTES", 1024)
    with pytest.raises(ValueError):
        replay(str(path), str(tmp_path / "replay"))


@pytest.mark.parametrize("damage", ["schema", "adapter", "source", "duplicate_item", "hits", "secrets", "params"])
def test_replay_rejects_inconsistent_recording_metadata(tmp_path, damage):
    path, report = recording(tmp_path)
    if damage == "schema":
        report["schema_version"] = 1
    elif damage == "adapter":
        report["sources"]["wiki"]["adapter_id"] = "not-an-adapter"
    elif damage == "source":
        report["rows"][0]["source"] = "unknown"
    elif damage == "duplicate_item":
        report["rows"].append(copy.deepcopy(report["rows"][0]))
    elif damage == "hits":
        report["rows"][0]["hits"] = 3
    elif damage == "secrets":
        report["rows"][0]["response"]["headers"]["set-cookie"] = "session=private"
    elif damage == "params":
        report["sources"]["wiki"]["params"]["api_key"] = "private"
    save(path, report)
    with pytest.raises(ValueError):
        replay(str(path), str(tmp_path / "replay"))


@pytest.mark.parametrize("raw", ['{"schema_version":2,"schema_version":2}', '{"x":NaN}', '{"x":1e999}',
                                  '{"x":' + '[' * 65 + '0' + ']' * 65 + '}', '[1,2,3]'])
def test_replay_requires_strict_json(tmp_path, raw):
    path = tmp_path / "probe_report.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        replay(str(path), str(tmp_path / "replay"))


def test_replay_only_uncertain_rows_are_not_success(tmp_path):
    path, report = recording(tmp_path)
    uncertain = report["rows"][-1]
    report["rows"] = [uncertain]
    save(path, report)
    result = replay(str(path), str(tmp_path / "replay"))
    assert result["status"] == "no_replayable_rows" and result["summary"]["replayed"] == 0


def test_replay_output_does_not_overwrite_probe_or_previous_replay(tmp_path):
    path, _ = recording(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        replay(str(path), str(path.parent))
    assert path.read_bytes() == before
    replay(str(path), str(tmp_path / "replay"))
    original = (tmp_path / "replay" / "replay_report.json").read_bytes()
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        replay(str(path), str(tmp_path / "replay"))
    assert (tmp_path / "replay" / "replay_report.json").read_bytes() == original



def test_replay_output_failure_never_publishes_partial_report_directory(tmp_path, monkeypatch):
    from bzs import artifact_output

    path, _ = recording(tmp_path)
    out = tmp_path / "interrupted-replay"
    original = artifact_output.atomic_write
    calls = 0

    def fail_second_write(target, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic disk-full during replay output")
        return original(target, data)

    monkeypatch.setattr(artifact_output, "atomic_write", fail_second_write)
    with pytest.raises(OSError, match="disk-full"):
        replay(str(path), str(out))
    assert not out.exists()
    assert not list(tmp_path.glob(".interrupted-replay.stage-*"))
    monkeypatch.setattr(artifact_output, "atomic_write", original)
    result = replay(str(path), str(out))
    assert result["status"] == "passed"
    assert (out / "replay_report.json").is_file() and (out / "replay_report.md").is_file()
