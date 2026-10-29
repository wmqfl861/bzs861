"""2026-10-28 review regressions and bounded runtime contracts."""
import asyncio
import json
import pytest
from bzs.worker import fetch as fm
from bzs.worker.fetch import Fetcher
from bzs.worker.netguard import NetPolicy
from tests.test_fetch import _MockResponse


async def test_partially_occupied_host_slot_survives_registry_churn(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 2)
    f = Fetcher(NetPolicy(), per_host=2)
    live = f._sem('active.example')
    await live.acquire()
    try:
        f._sem('other.example')
        f._sem('more.example')
        assert f._sem('active.example') is live
    finally:
        live.release()


async def test_host_limit_held_under_real_fetch_churn(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 2)
    gate = asyncio.Event()
    entered = asyncio.Event()
    calls = []
    async def hold():
        entered.set()
        await gate.wait()
    class Session:
        def request(self, method, url, **kwargs):
            calls.append(str(url))
            return _MockResponse(url, enter=hold if url.host == 'active.example' else None)
    f = Fetcher(NetPolicy(), per_host=2)
    f._session = Session()
    tasks = []
    try:
        tasks.append(asyncio.create_task(f.fetch('https://active.example/one')))
        await asyncio.wait_for(entered.wait(), 1)
        await f.fetch('https://other.example/')
        await f.fetch('https://more.example/')
        tasks.append(asyncio.create_task(f.fetch('https://active.example/two')))
        tasks.append(asyncio.create_task(f.fetch('https://active.example/three', timeout_s=0.05)))
        await asyncio.sleep(0.1)
        assert len([c for c in calls if '/active.example/' in c]) == 2
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)




async def test_registry_hard_cap_refuses_before_any_dispatch_and_recovers(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 1)
    f = Fetcher(NetPolicy(), per_host=1)
    busy = f._sem('active.example'); await busy.acquire()
    class Session:
        def request(self, *args, **kwargs):
            raise AssertionError('capacity denial must precede outbound dispatch')
    f._session = Session()
    budget = fm.FetchBudget(1, 1000)
    result = await f.fetch('https://other.example/', budget=budget)
    assert result.error_code == 'E_HOST_CAPACITY'
    assert budget.external_requests == result.external_requests == 0
    assert len(f._host_locks) == 1
    assert f.admission_stats()['capacity_rejections'] == 1
    busy.release()
    f._sem('other.example')
    assert len(f._host_locks) == 1 and f.admission_stats()['evictions'] == 1


async def test_waiters_cancellation_and_preacquire_reference_keep_slot(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 1)
    f = Fetcher(NetPolicy(), per_host=1)
    slot = f._sem('one.example')
    slot.references += 1  # wait_for has not yet started its acquire coroutine
    with pytest.raises(fm.FetchAdmissionDenied): f._sem('two.example')
    slot.references -= 1
    await slot.acquire()
    task = asyncio.create_task(slot.acquire())
    await asyncio.sleep(0)
    assert f.admission_stats()['waiters'] == 1
    with pytest.raises(fm.FetchAdmissionDenied): f._sem('two.example')
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert slot.users == slot.holders == 1
    slot.release()
    assert slot.idle and slot.holders == 0
    f._sem('two.example')


async def test_repeated_fetch_timeouts_do_not_pin_entries(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 1)
    f = Fetcher(NetPolicy(), per_host=1)
    slot = f._sem('one.example');await slot.acquire()
    f._session = object()  # no request may reach the session before the timeout
    for _ in range(10):
        result = await f.fetch('https://one.example/', timeout_s=0.001)
        assert result.status == 'timeout'
        assert slot.users == 1 and slot.references == 0
    slot.release()
    f._sem('two.example')
    assert f.admission_stats()['holders'] == f.admission_stats()['waiters'] == 0


async def test_long_host_churn_preserves_every_limit_without_identity_leak(monkeypatch):
    monkeypatch.setattr(fm, 'MAX_HOST_LOCKS', 8)
    f = Fetcher(NetPolicy(), per_host=2)
    active = f._sem('sensitive-host.example');await active.acquire()
    for i in range(5000):
        slot = f._sem(f'churn-{i}.example')
        await slot.acquire();slot.release()
        assert len(f._host_locks) <= 8
        assert f._sem('sensitive-host.example') is active
    stats = f.admission_stats()
    assert stats['holders'] == 1 and stats['evictions'] == 4993
    assert 'sensitive-host' not in json.dumps(stats)
    active.release()
