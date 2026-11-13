"""Bounded Worker cancellation receipts; no network or database is needed.

These are method/handler unit tests. Full Worker/Core network and recovery
coverage remains in the existing integration suites, not in this module.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bzs.worker import server as worker_module
from bzs.worker.server import Worker, make_app


pytestmark = pytest.mark.asyncio
IDENTITY = "a" * 48
OTHER = "b" * 48
TOKEN = "unit-test-worker-cancellation-token-not-a-credential"


@pytest.fixture
def worker():
    # The cancellation registry has no dependency on fetcher/site initialization.
    result = object.__new__(Worker)
    result._read_attempts = {}
    result._retired_attempts = {}
    result._direct_controls = {}
    result._retired_direct_controls = {}
    result._fetch_extract = AsyncMock(side_effect=AssertionError("unexpected fetch"))
    result._map_fetch = AsyncMock(side_effect=AssertionError("unexpected map fetch"))
    return result


@pytest.fixture
def clock(monkeypatch):
    current = SimpleNamespace(now=100.0)
    loop = SimpleNamespace(time=lambda: current.now)
    # Replace only this module's reference, not asyncio's real event-loop clock.
    monkeypatch.setattr(worker_module, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    return current


def budget():
    # Only these receipt fields may change/be inspected by cancellation.
    return SimpleNamespace(cancelled=False, external_requests=3, content_bytes=17)


def seed_full(worker, active_count):
    for index in range(2048):
        key = f"{index:048x}"
        if index < active_count:
            worker._read_attempts[key] = budget()
        else:
            worker._retired_attempts[key] = (200.0, False, 3, 17)


INVALID = [
    None, False, True, 0, 1, "", IDENTITY, [], [IDENTITY], {},
    {"attempt_id": None}, {"attempt_id": 1}, {"attempt_id": True},
    {"attempt_id": []}, {"attempt_id": ""}, {"attempt_id": "a" * 47},
    {"attempt_id": "a" * 49}, {"attempt_id": "A" * 48},
    {"attempt_id": "g" * 48}, {"attempt_id": IDENTITY + "\n"},
    {"attempt_id": IDENTITY, "control_id": OTHER},
    {"attempt_id": IDENTITY, "url": "https://example.test/"},
    {"attempt_id": IDENTITY, "max_requests": 1},
    {"attempt_id": IDENTITY, "unknown": False},
]


@pytest.mark.parametrize("params", INVALID)
async def test_invalid_cancel_is_rejected_before_any_registry_mutation(worker, clock, params):
    active = budget()
    worker._read_attempts[IDENTITY] = active
    # Even cleanup must not run for a malformed cancellation envelope.
    worker._retired_attempts[OTHER] = (99.0, False, 2, 8)
    before_active = dict(worker._read_attempts)
    before_retired = dict(worker._retired_attempts)
    with pytest.raises(ValueError):
        await worker.fetch_cancel(params)
    assert worker._read_attempts == before_active
    assert worker._retired_attempts == before_retired
    assert not active.cancelled
    assert (active.external_requests, active.content_bytes) == (3, 17)


async def test_attempt_identity_validation_does_not_reject_other_grant_fields(worker):
    assert worker._attempt_id({"attempt_id": IDENTITY, "max_requests": 1}) == IDENTITY


@pytest.mark.parametrize("initial", [None, (220.0, False, 3, 17), (220.0, True, 3, 17)])
async def test_duplicate_cancels_preserve_first_expiry_and_cumulative_usage(worker, clock, initial):
    if initial is not None:
        worker._retired_attempts[IDENTITY] = initial
    assert await worker.fetch_cancel({"attempt_id": IDENTITY}) == {"cancel_requested": True}
    first = worker._retired_attempts[IDENTITY]
    assert first == (220.0, True, *(initial[2:] if initial else (0, 0)))
    for now in (101.0, 150.0, 219.999):
        clock.now = now
        assert await worker.fetch_cancel({"attempt_id": IDENTITY}) == {"cancel_requested": True}
        assert worker._retired_attempts[IDENTITY] == first
    clock.now = 220.0
    worker._prune_attempts()
    assert IDENTITY not in worker._retired_attempts


async def test_expired_id_can_get_new_process_retention_but_no_old_usage(worker, clock):
    worker._retired_attempts[IDENTITY] = (100.0, True, 9, 1000)
    await worker.fetch_cancel({"attempt_id": IDENTITY})
    assert worker._retired_attempts[IDENTITY] == (220.0, True, 0, 0)
    # Finite Worker retention is not Core's permanent idempotency contract.


async def test_running_cancel_does_not_retire_or_recharge_the_attempt(worker, clock):
    active = budget()
    worker._read_attempts[IDENTITY] = active
    for now in (100.0, 1000.0, 10000.0):
        clock.now = now
        await worker.fetch_cancel({"attempt_id": IDENTITY})
        assert worker._read_attempts[IDENTITY] is active
        assert active.cancelled
        assert (active.external_requests, active.content_bytes) == (3, 17)
        assert IDENTITY not in worker._retired_attempts


@pytest.mark.parametrize("active_count", [0, 1, 1024, 2048])
async def test_unknown_cancel_cannot_exceed_combined_retention_capacity(worker, clock, active_count):
    seed_full(worker, active_count)
    with pytest.raises(ValueError, match="capacity"):
        await worker.fetch_cancel({"attempt_id": IDENTITY})
    assert len(worker._read_attempts) + len(worker._retired_attempts) == 2048
    assert IDENTITY not in worker._read_attempts and IDENTITY not in worker._retired_attempts
    assert not any(item.cancelled for item in worker._read_attempts.values())


@pytest.mark.parametrize("active_count", [0, 1024, 2048])
async def test_existing_cancel_still_succeeds_at_full_capacity(worker, clock, active_count):
    seed_full(worker, active_count)
    key = "0" * 48
    await worker.fetch_cancel({"attempt_id": key})
    assert len(worker._read_attempts) + len(worker._retired_attempts) == 2048
    if active_count:
        assert worker._read_attempts[key].cancelled
        assert worker._read_attempts[key].content_bytes == 17
    else:
        assert worker._retired_attempts[key] == (200.0, True, 3, 17)


async def test_expired_receipt_frees_one_slot_without_evicting_active_attempts(worker, clock):
    seed_full(worker, 1024)
    expired = f"{2047:048x}"
    worker._retired_attempts[expired] = (100.0, True, 5, 6)
    active_before = dict(worker._read_attempts)
    await worker.fetch_cancel({"attempt_id": IDENTITY})
    assert expired not in worker._retired_attempts
    assert worker._retired_attempts[IDENTITY] == (220.0, True, 0, 0)
    assert worker._read_attempts == active_before
    assert len(worker._read_attempts) + len(worker._retired_attempts) == 2048


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("channel", ["batch", "map", "direct"])
async def test_retained_replay_never_dispatches_or_erases_cumulative_usage(worker, clock, channel, cancelled):
    if channel == "direct":
        worker._retired_direct_controls[IDENTITY] = (220.0, cancelled, 3, 17)
        await worker.fetch_cancel_direct({"control_id": IDENTITY})
        result = await worker.fetch_extract_direct({"control_id": IDENTITY,
                                                    "url": "https://example.test/page", "timeout_ms": 1000})
        assert result["status"] == "cancelled"
    else:
        worker._retired_attempts[IDENTITY] = (220.0, cancelled, 3, 17)
        await worker.fetch_cancel({"attempt_id": IDENTITY})
        params = {"attempt_id": IDENTITY, "url": "https://example.test/page",
                  "timeout_ms": 1000, "max_requests": 1, "max_content_bytes": 4096}
        if channel == "batch":
            result = await worker.fetch_extract(params)
            assert result["status"] == "cancelled"
        else:
            result = await worker.map_fetch({**params, "kind": "page", "site_id": "docs",
                                             "sites_manifest_sha256": "c" * 64, "deadline_ms": 1})
            assert result["status"] == "error"
    assert result["error_code"] == "E_CANCELLED"
    assert result["usage"] == {"external_requests": 3, "content_bytes": 17}
    worker._fetch_extract.assert_not_awaited()
    worker._map_fetch.assert_not_awaited()


@pytest.mark.parametrize("channel", ["durable", "direct"])
async def test_same_hex_id_does_not_merge_direct_and_durable_namespaces(worker, clock, channel):
    worker._retired_attempts[IDENTITY] = (220.0, False, 3, 17)
    worker._retired_direct_controls[IDENTITY] = (230.0, False, 7, 23)
    if channel == "durable":
        await worker.fetch_cancel({"attempt_id": IDENTITY})
        assert worker._retired_attempts[IDENTITY] == (220.0, True, 3, 17)
        assert worker._retired_direct_controls[IDENTITY] == (230.0, False, 7, 23)
    else:
        await worker.fetch_cancel_direct({"control_id": IDENTITY})
        assert worker._retired_attempts[IDENTITY] == (220.0, False, 3, 17)
        assert worker._retired_direct_controls[IDENTITY] == (230.0, True, 7, 23)


async def test_concurrent_duplicate_cancels_keep_one_retirement_record(worker, clock):
    worker._retired_attempts[IDENTITY] = (220.0, False, 3, 17)
    clock.now = 200.0
    results = await asyncio.gather(*(worker.fetch_cancel({"attempt_id": IDENTITY}) for _ in range(64)))
    assert results == [{"cancel_requested": True}] * 64
    assert worker._retired_attempts == {IDENTITY: (220.0, True, 3, 17)}


@pytest.mark.parametrize("params", [1, True, IDENTITY, [IDENTITY],
                                   {"attempt_id": IDENTITY, "unknown": True}])
async def test_rpc_handler_reports_params_not_internal_error_without_side_effects(worker, clock, params):
    app = make_app(worker, TOKEN)
    handler = next(route.handler for route in app.router.routes() if route.method == "POST")
    request = SimpleNamespace(headers={"Authorization": f"Bearer {TOKEN}"},
                              json=AsyncMock(),
                              read=AsyncMock(return_value=json.dumps({"method": "fetch.cancel", "params": params}).encode()))
    response = await handler(request)
    assert json.loads(response.text)["error"]["code"] == "E_PARAMS"
    assert worker._read_attempts == {} and worker._retired_attempts == {}


@pytest.mark.parametrize("headers, status, code", [
    ({}, 401, "E_AUTH"),
    ({"Authorization": f"Bearer {TOKEN}", "Origin": "https://example.test"}, 403, "E_ORIGIN"),
])
async def test_transport_authorization_still_precedes_cancellation(worker, clock, headers, status, code):
    app = make_app(worker, TOKEN)
    handler = next(route.handler for route in app.router.routes() if route.method == "POST")
    request = SimpleNamespace(headers=headers,
                              json=AsyncMock(), read=AsyncMock(return_value=b"{}"))
    response = await handler(request)
    assert response.status == status and json.loads(response.text)["error"]["code"] == code
    request.json.assert_not_awaited()
    request.read.assert_not_awaited()
    assert worker._retired_attempts == {}
