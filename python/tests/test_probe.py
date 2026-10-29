import asyncio
import copy
import gzip
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import bzs.probe as probe_module
from bzs.cli import main as cli_main
from bzs.probe import probe, summarize
from bzs.probe_state import output_guard, read_report, write_report
from bzs.worker.fetch import FetchResult
from bzs.worker.sources import ADAPTERS
from bzs.worker.sources.base import Hit, SourceError
from tests import fakeweb


async def test_probe_writes_report_raw_pages_and_stops_on_blocks(tmp_path):
    runner, port = await fakeweb.start()
    try:
        manifest = tmp_path / "sources.json"
        manifest.write_text(json.dumps(fakeweb.manifest(port)), encoding="utf-8")
        queries = tmp_path / "queries.json"
        queries.write_text(json.dumps({"queries": [
            {"id": "q1", "query": "resumable crawling", "good_for": ["web", "encyclopedia"]},
            {"id": "q2", "query": "断点续爬", "good_for": ["web", "encyclopedia"]},
            {"id": "q3", "query": "checkpoints", "good_for": ["web", "encyclopedia"]}]}), encoding="utf-8")
        out = tmp_path / "probe-out"
        report = await probe(str(manifest), str(out), str(queries), per_source=3, delay_s=0, timeout_s=5,
                             allow_private=["127.0.0.1/32"], test_hosts={fakeweb.HOST: "127.0.0.1"})
    finally:
        await runner.cleanup()
    summary = report["summary"]
    assert summary["ddg"]["verdict"] == "reachable" and summary["ddg"]["requests"] == 3
    assert summary["bing"]["ok_rate"] == 1.0
    # A blocked source is probed once, then left alone.
    assert summary["blocked"]["requests"] == 1 and summary["blocked"]["verdict"] == "blocked"
    md = (out / "probe_report.md").read_text(encoding="utf-8")
    assert "| ddg |" in md and "| blocked |" in md
    saved = json.loads((out / "probe_report.json").read_text(encoding="utf-8"))
    row = next(r for r in saved["rows"] if r["source"] == "blocked")
    raw = gzip.decompress((out / row["raw"]).read_bytes())
    assert b"anomaly-modal" in raw  # the raw page is kept as a parser fixture
    # The fake HTTP service has already stopped: replay must reconstruct the
    # actual probe schema, charset, final URL and parser input entirely offline.
    from bzs.replay import replay
    replayed = replay(str(out / "probe_report.json"), str(tmp_path / "replay"))
    assert replayed["status"] == "passed" and replayed["identity_match"]
    assert replayed["network_requests"] == 0
    assert replayed["summary"] == {"replayed": len(report["rows"]), "matched": len(report["rows"]),
                                   "mismatches": 0, "skipped": 0}


def inputs(tmp_path, *, queries=3, sources=1, quota=1000, shared=False):
    manifest = tmp_path / "sources.json"
    data = fakeweb.manifest(8099)
    data["sources"] = data["sources"][:sources]
    for spec in data["sources"]:
        spec["daily_cap"] = spec["contract"]["limits"]["request_quota"] = quota
        if shared:
            spec["contract"]["limits"]["shared_budget_key"] = "shared-fixture"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    golden = tmp_path / "queries.json"
    golden.write_text(json.dumps({"queries": [
        {"id": f"q{i}", "query": f"query {i}", "good_for": ["web"]} for i in range(1, queries + 1)
    ]}), encoding="utf-8")
    return manifest, golden, tmp_path / "probe-out"


@pytest.fixture
def scripted(monkeypatch):
    """In-memory adapter: the probe's real plan/checkpoint/ledger still run."""
    control = SimpleNamespace(calls=[], starts=0, closes=0, handler=None)

    class OfflineFetcher:
        def __init__(self, *args, **kwargs):
            self.user_agent = "offline-probe-test"
            self.accept_language = "en"

        async def start(self):
            control.starts += 1

        async def close(self):
            control.closes += 1

    class OfflineAdapter:
        def __init__(self, spec, fetcher):
            self.id = spec["id"]

        async def search(self, query, limit, language, timeout_s):
            control.calls.append((self.id, query))
            if control.handler:
                return await control.handler(self.id, query, limit)
            return result(query)

    monkeypatch.setattr(probe_module, "Fetcher", OfflineFetcher)
    for name in ADAPTERS:
        monkeypatch.setitem(ADAPTERS, name, OfflineAdapter)
    return control


def result(query, *, count=1, headers=None):
    body = ("saved response for " + query).encode("utf-8")
    response = FetchResult(status="ok", url="https://fixture.example/search", final_url="https://fixture.example/results",
                           http_status=200, body=body, headers=headers or {"content-type": "text/html; charset=utf-8"},
                           elapsed_ms=1, external_requests=1, content_bytes=len(body))
    return [Hit(f"https://fixture.example/{i}", f"title {i}", f"snippet {i}", i) for i in range(1, count + 1)], response


async def run_probe(manifest, golden, out, **kwargs):
    return await probe(str(manifest), str(out), str(golden), per_source=kwargs.pop("per_source", 3),
                       delay_s=kwargs.pop("delay_s", 0), timeout_s=5, **kwargs)


@pytest.mark.parametrize("crashed", [False, True], ids=["cancelled", "crashed-checkpoint"])
async def test_interrupted_reserved_attempt_is_durable_and_never_resent(tmp_path, scripted, crashed):
    manifest, golden, out = inputs(tmp_path)
    entered = asyncio.Event()
    saved_before_cancel = None

    async def handler(source, query, limit):
        nonlocal saved_before_cancel
        saved = read_report(out)
        row = next(row for row in saved["rows"] if row["source"] == source and row["query"] == query)
        assert row["attempt_state"] == "reserved"  # already on disk before adapter executes
        assert saved["budgets"][source]["used"] == len(saved["rows"])
        if query == "query 2":
            saved_before_cancel = saved
            entered.set()
            await asyncio.Event().wait()
        return result(query)

    scripted.handler = handler
    task = asyncio.create_task(run_probe(manifest, golden, out))
    try:
        await asyncio.wait_for(entered.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    interrupted = read_report(out)
    assert interrupted["status"] == "interrupted"
    assert [row["attempt_state"] for row in interrupted["rows"]] == ["completed", "uncertain"]
    assert interrupted["budgets"]["ddg"]["used"] == 2
    raw = {path.name: path.read_bytes() for path in (out / "raw").iterdir()}
    if crashed:
        # This is the actual durable checkpoint captured before cancellation's
        # handler could run, as would remain after abrupt process termination.
        assert saved_before_cancel["rows"][-1]["attempt_state"] == "reserved"
        write_report(out, saved_before_cancel)
    resumed = await run_probe(manifest, golden, out, resume=True)
    assert scripted.calls == [("ddg", "query 1"), ("ddg", "query 2")]
    assert resumed["run_id"] == interrupted["run_id"] and resumed["resume_count"] == 1
    assert resumed["budgets"]["ddg"]["used"] == 2
    assert resumed["budgets"]["ddg"]["stopped"]
    assert [item["state"] for item in resumed["plan"]] == ["completed", "uncertain", "skipped"]
    assert resumed["plan"][-1]["reason"] == "service_stopped:uncertain_attempt"
    assert {path.name: path.read_bytes() for path in (out / "raw").iterdir()} == raw
    assert scripted.starts == scripted.closes == 2


async def test_resume_continues_only_unreserved_work_with_the_same_quota(tmp_path, scripted, monkeypatch):
    manifest, golden, out = inputs(tmp_path, quota=2)
    completed = asyncio.Event()
    checkpoint = probe_module._checkpoint

    def observe(path, report):
        checkpoint(path, report)
        if any(row["attempt_state"] == "completed" for row in report["rows"]):
            completed.set()

    monkeypatch.setattr(probe_module, "_checkpoint", observe)
    task = asyncio.create_task(run_probe(manifest, golden, out, delay_s=0.2))
    try:
        await asyncio.wait_for(completed.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    interrupted = read_report(out)
    assert [item["state"] for item in interrupted["plan"]] == ["completed", "pending", "pending"]
    assert interrupted["budgets"]["ddg"]["used"] == 1
    assert not interrupted["budgets"]["ddg"]["stopped"]
    resumed = await run_probe(manifest, golden, out, delay_s=0.2, resume=True)
    assert scripted.calls == [("ddg", "query 1"), ("ddg", "query 2")]
    assert resumed["budgets"]["ddg"]["used"] == 2
    assert resumed["plan"][-1]["reason"] == "run_quota_exhausted"


async def test_shared_service_reservations_and_stops_survive_completed_resume(tmp_path, scripted):
    manifest, golden, out = inputs(tmp_path, sources=2, quota=2, shared=True)

    async def blocked(source, query, limit):
        _, response = result(query)
        error = SourceError("rate_limited", "HTTP 429", 429, retry_after_ms=900_000)
        response.http_status = 429
        error.result = response
        raise error

    scripted.handler = blocked
    report = await run_probe(manifest, golden, out)
    assert len(scripted.calls) == 1
    budget = copy.deepcopy(report["budgets"]["shared-fixture"])
    assert budget["used"] == 1 and budget["stopped"] and budget["stop_reason"] == "rate_limited"
    assert budget["cooldown_until_ms"] - budget["last_started_at_ms"] >= 900_000
    assert sum(item["state"] == "skipped" for item in report["plan"]) == 5
    resumed = await run_probe(manifest, golden, out, resume=True)
    assert len(scripted.calls) == 1
    assert resumed["budgets"]["shared-fixture"] == budget


async def test_probe_reports_bounded_retry_after_and_never_resumes_a_stopped_source(tmp_path, scripted, monkeypatch):
    manifest, golden, out = inputs(tmp_path, sources=2, quota=2, shared=True)
    now = [1000000]
    monkeypatch.setattr(probe_module, "_now_ms", lambda: now[0])

    async def limited(source, query, limit):
        _, response = result(query)
        error = SourceError("rate_limited", "HTTP 429", 429, 999999999000)
        response.http_status = 429
        error.result = response
        raise error

    scripted.handler = limited
    report = await run_probe(manifest, golden, out)
    assert len(scripted.calls) == 1
    row = report["rows"][0]
    assert row["retry_after_ms"] == row["retry_after_limit_ms"] == 86400000
    assert row["retry_after_clamped"] is True
    budget = copy.deepcopy(report["budgets"]["shared-fixture"])
    assert budget["cooldown_until_ms"] == now[0] + 86400000
    assert budget["used"] == 1 and budget["stopped"] and budget["stop_reason"] == "rate_limited"
    now[0] += 2 * 86400000
    resumed = await run_probe(manifest, golden, out, resume=True)
    assert len(scripted.calls) == 1 and resumed["budgets"]["shared-fixture"] == budget


async def test_shared_service_quota_counts_both_source_aliases(tmp_path, scripted):
    manifest, golden, out = inputs(tmp_path, sources=2, quota=2, shared=True)

    async def response_after_io(source, query, limit):
        # A real request yields while its peer queues for the same service lane.
        await asyncio.sleep(0)
        return result(query)

    scripted.handler = response_after_io
    report = await run_probe(manifest, golden, out)
    assert len(scripted.calls) == report["budgets"]["shared-fixture"]["used"] == 2
    assert {source for source, _ in scripted.calls} == {"ddg", "bing"}
    assert sum(item["reason"] == "run_quota_exhausted" for item in report["plan"]) == 4


@pytest.mark.parametrize("change", ["manifest", "queries", "options", "parser"])
async def test_resume_refuses_changed_input_identity_before_adapter_start(tmp_path, scripted, monkeypatch, change):
    manifest, golden, out = inputs(tmp_path, queries=1)
    await run_probe(manifest, golden, out)
    kwargs = {"resume": True}
    if change == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    elif change == "queries":
        golden.write_bytes(golden.read_bytes() + b"\n")
    elif change == "options":
        kwargs["delay_s"] = 0.1
    else:
        identity = copy.deepcopy(probe_module.parser_identity())
        identity["dependencies"]["python"] = "changed-parser-environment"
        monkeypatch.setattr(probe_module, "parser_identity", lambda: identity)
    with pytest.raises(ValueError, match="identity mismatch"):
        await run_probe(manifest, golden, out, **kwargs)
    assert scripted.calls == [("ddg", "query 1")] and scripted.starts == 1


async def test_nonempty_output_refused_and_new_runs_have_distinct_ids(tmp_path, scripted):
    manifest, golden, out = inputs(tmp_path, queries=1)
    first = await run_probe(manifest, golden, out)
    raw = (out / "probe_report.json").read_bytes()
    with pytest.raises(ValueError, match="not empty"):
        await run_probe(manifest, golden, out)
    assert (out / "probe_report.json").read_bytes() == raw
    assert len(scripted.calls) == scripted.starts == 1
    second = await run_probe(manifest, golden, tmp_path / "another-run")
    assert first["run_id"] != second["run_id"]
    assert second["environment"]["quota_scope"] == "this_probe_run"


async def test_response_snapshot_keeps_all_ten_hits_and_only_safe_metadata(tmp_path, scripted, monkeypatch):
    manifest, golden, out = inputs(tmp_path, queries=1)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["sources"][0]["params"].update(token_env="MY_PRIVATE_TOKEN", key_env="MY_PRIVATE_KEY")
    manifest.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("MY_PRIVATE_TOKEN", "not-in-artifacts-token")

    async def respond(source, query, limit):
        assert limit == 10
        return result(query, count=12, headers={"Content-Type": "text/html; charset=utf-8", "Retry-After": "30",
                      "Set-Cookie": "not-in-artifacts-cookie", "Authorization": "not-in-artifacts-auth",
                      "API-Key": "not-in-artifacts-key"})

    scripted.handler = respond
    report = await run_probe(manifest, golden, out)
    row = report["rows"][0]
    assert row["hits"] == len(row["results"]) == 10 and len(row["top"]) == 3
    assert row["results"][-1]["snippet"] == "snippet 10"
    response = row["response"]
    assert response["headers"] == {"content-type": "text/html; charset=utf-8", "retry-after": "30"}
    assert response["final_url"] == "https://fixture.example/results"
    body = gzip.decompress((out / response["raw_path"]).read_bytes())
    assert response["body_bytes"] == response["content_bytes"] == len(body)
    assert response["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert response["external_requests"] == 1
    saved = (out / "probe_report.json").read_text(encoding="utf-8")
    assert "MY_PRIVATE" not in saved and "not-in-artifacts" not in saved


@pytest.mark.parametrize("damage", ["body", "refund", "plan", "duplicate-row"])
async def test_resume_rejects_damaged_evidence_without_spending_more(tmp_path, scripted, damage):
    manifest, golden, out = inputs(tmp_path, queries=1)
    report = await run_probe(manifest, golden, out)
    if damage == "body":
        (out / report["rows"][0]["raw"]).write_bytes(gzip.compress(b"replaced response"))
    elif damage == "refund":
        report["budgets"]["ddg"]["used"] = 0
        write_report(out, report)
    elif damage == "plan":
        report["plan"][0]["state"] = "pending"
        write_report(out, report)
    else:
        report["rows"].append(copy.deepcopy(report["rows"][0]))
        write_report(out, report)
    with pytest.raises(ValueError):
        await run_probe(manifest, golden, out, resume=True)
    assert len(scripted.calls) == scripted.starts == 1


async def test_raw_write_failure_keeps_attempt_charged_and_uncertain(tmp_path, scripted, monkeypatch):
    manifest, golden, out = inputs(tmp_path)
    record = probe_module.record_body

    def disk_full(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(probe_module, "record_body", disk_full)
    with pytest.raises(OSError, match="simulated disk"):
        await run_probe(manifest, golden, out)
    report = read_report(out)
    assert report["rows"][0]["attempt_state"] == "uncertain"
    assert report["budgets"]["ddg"]["used"] == 1
    monkeypatch.setattr(probe_module, "record_body", record)
    resumed = await run_probe(manifest, golden, out, resume=True)
    assert len(scripted.calls) == 1 and resumed["budgets"]["ddg"]["used"] == 1


async def test_no_matching_capability_or_holdout_never_falls_back_to_other_queries(tmp_path, scripted):
    manifest, golden, out = inputs(tmp_path)
    golden.write_text(json.dumps({"queries": [
        {"id": "paper", "query": "paper query", "good_for": ["papers"]},
        {"id": "h01", "query": "reserved holdout", "good_for": ["web"]}]}), encoding="utf-8")
    report = await run_probe(manifest, golden, out)
    assert not scripted.calls and report["plan"] == [] and report["rows"] == []
    assert report["summary"]["ddg"]["reason"] == "no_eligible_queries"
    assert report["budgets"]["ddg"]["used"] == 0


@pytest.mark.parametrize("status", ["blocked", "challenge", "rate_limited"])
def test_block_verdict_takes_priority_over_earlier_successes(status):
    rows = [{"source": "source", "status": s, "hits": int(s == "ok"), "elapsed_ms": 1}
            for s in ["ok", "ok", "ok", status]]
    summary = summarize(rows)["source"]
    assert summary["ok_rate"] == 0.75 and summary["verdict"] == "blocked"
    assert summarize([{"source": "source", "status": "empty", "hits": 0, "elapsed_ms": 1}])["source"]["verdict"] == "reachable"


def test_output_lock_refuses_concurrent_resume_without_stale_pid_override(tmp_path):
    out = tmp_path / "probe"
    with output_guard(out, False):
        write_report(out, {})
        with pytest.raises(ValueError, match="another probe"):
            with output_guard(out, True):
                pass
    # The same output becomes resumable when its process releases the OS lock.
    with output_guard(out, True):
        pass


@pytest.mark.parametrize(("status", "expected"), [("ok", 0), ("empty", 0), ("blocked", 1), ("timeout", 1)])
def test_probe_cli_exit_code_reflects_actual_answers(tmp_path, monkeypatch, status, expected):
    seen = {}

    async def recorded(*args, **kwargs):
        seen.update(kwargs)
        return {"rows": [{"status": status}]}

    monkeypatch.setattr(probe_module, "probe", recorded)
    (tmp_path / "probe_report.md").write_text("saved probe", encoding="utf-8")
    code = cli_main(["probe", "--sources", "unused-sources", "--queries", "unused-queries",
                     "--out", str(tmp_path), "--resume"])
    assert code == expected and seen["resume"] is True


@pytest.mark.parametrize(("status", "expected"), [("passed", 0), ("failed", 1), ("no_replayable_rows", 1)])
def test_replay_cli_propagates_result_and_parser_change_flag(tmp_path, monkeypatch, status, expected):
    import bzs.replay as replay_module
    seen = {}

    def recorded(report, out, **kwargs):
        seen.update(kwargs)
        return {"status": status}

    monkeypatch.setattr(replay_module, "replay", recorded)
    (tmp_path / "replay_report.md").write_text("saved replay", encoding="utf-8")
    assert cli_main(["replay", "--report", "unused-report", "--out", str(tmp_path), "--allow-parser-change"]) == expected
    assert seen == {"allow_parser_change": True}


def test_parser_identity_binds_the_actual_adapter_registry(tmp_path, monkeypatch):
    import bzs.probe_format as format_module
    original_root = Path(format_module.__file__).resolve().parent
    identity = format_module.parser_identity()
    registry = "worker/sources/__init__.py"
    assert registry in identity["files"]
    # Mutate an isolated package copy, never the installed source or shared repo.
    for name in identity["files"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original_root / name).read_bytes())
    monkeypatch.setattr(format_module, "__file__", str(tmp_path / "probe_format.py"))
    before = format_module.parser_identity()
    path = tmp_path / registry
    path.write_bytes(path.read_bytes() + b"\nADAPTERS['duckduckgo_html'] = BingHtml\n")
    after = format_module.parser_identity()
    assert before["files"][registry] != after["files"][registry]
    assert before["code_sha256"] != after["code_sha256"]
    assert before["dependencies"] == after["dependencies"]



@pytest.mark.parametrize("error_kind", ["adapter", "source"])
async def test_probe_persists_error_class_not_secret_exception_text(tmp_path, scripted, error_kind):
    manifest, golden, out = inputs(tmp_path)
    secret = "SENSITIVE_TOKEN_9f7a?api_key=PRIVATE_VALUE"

    async def handler(source, query, limit):
        if error_kind == "source":
            raise SourceError("rate_limited", "upstream failed with " + secret, 429, 1000)
        raise RuntimeError("request failed Authorization: Bearer " + secret)

    scripted.handler = handler
    report = await run_probe(manifest, golden, out)
    raw_json = (out / "probe_report.json").read_text(encoding="utf-8")
    raw_md = (out / "probe_report.md").read_text(encoding="utf-8")
    assert secret not in raw_json and secret not in raw_md
    assert "PRIVATE_VALUE" not in raw_json + raw_md
    first = report["rows"][0]
    assert first["error"] == ("source_error:rate_limited" if error_kind == "source"
                              else "adapter_error:RuntimeError")
    assert first["status"] == ("rate_limited" if error_kind == "source" else "error")



@pytest.mark.parametrize("raw", [
    b'{"schema_version":2,"schema_version":2}',
    b'{"value":NaN}',
    b'{"value":' + b'[' * 70 + b'0' + b']' * 70 + b'}',
])
def test_probe_checkpoint_rejects_ambiguous_nonfinite_or_deep_json(tmp_path, raw):
    from bzs.probe_state import REPORT_NAME, read_report

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / REPORT_NAME).write_bytes(raw)
    with pytest.raises(ValueError):
        read_report(tmp_path)



def test_probe_checkpoint_rejects_excessive_total_json_nodes(tmp_path):
    from bzs.probe_state import MAX_JSON_NODES, REPORT_NAME, read_report

    path = tmp_path / REPORT_NAME
    path.write_bytes(b'{"items":[' + b"0," * MAX_JSON_NODES + b"0]}")
    with pytest.raises(ValueError, match="node count"):
        read_report(tmp_path)
