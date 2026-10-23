"""Independent response-clock review: parsing must not renew origin freshness."""
import time

import bzs.worker.server as worker_server
from tests.test_http_revalidation import conditional_worker, read_grant


async def test_extraction_does_not_reset_received_time(conditional_worker, monkeypatch):
    worker, server, calls, state = conditional_worker
    clock = [1700000000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    state["page"]["headers"] = [("Cache-Control", "max-age=3"), ("Age", "2")]
    captured = []
    actual_fetch = worker.fetcher.fetch

    async def capture_response(*args, **kwargs):
        response = await actual_fetch(*args, **kwargs)
        captured.append(int(clock[0] * 1000))
        return response

    actual_extract = worker_server.extract_html

    def delayed_extract(*args, **kwargs):
        parsed = actual_extract(*args, **kwargs)
        clock[0] += 2  # Deterministic CPU time; the origin response is already received.
        return parsed

    monkeypatch.setattr(worker.fetcher, "fetch", capture_response)
    monkeypatch.setattr(worker_server, "extract_html", delayed_extract)
    result = await worker.fetch_extract(read_grant(server))
    assert result["status"] == "ok" and len(calls) == 1
    assert result["http_freshness"]["received_at_ms"] == captured[0]
    policy = result["http_freshness"]
    corrected_age_ms = 2000 + policy["response_delay_ms"] + int(clock[0] * 1000) - policy["received_at_ms"]
    assert corrected_age_ms >= 4000  # max-age=3 is already exceeded during extraction.
