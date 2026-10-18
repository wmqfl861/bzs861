import gzip
import json

from bzs.probe import probe
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
