import asyncio
import copy
from types import SimpleNamespace

import pytest

import bzs.worker.sources as source_module
from bzs.worker.fetch import FetchResult
from bzs.worker.sources import SourceSet
from bzs.worker.sources.base import SourceError, retry_after_ms
from bzs.worker.robots import response_record
from tests import fakeweb


@pytest.mark.asyncio
@pytest.mark.parametrize('late_status', ['empty', 'short_rate', 'parse_error'])
async def test_two_inflight_aliases_keep_longest_fence_and_do_not_retry(monkeypatch, late_status):
    specs = copy.deepcopy(fakeweb.manifest(8099)['sources'][:2])
    for spec in specs:
        spec['min_interval_ms'] = 0
        spec['contract']['limits'].update(shared_budget_key='independent-shared', max_concurrency=2, min_interval_ms=0)
    now = [1000.0]
    monkeypatch.setattr(source_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    sources = SourceSet(specs, None)
    started = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]
    calls = []

    async def long(*args):
        calls.append('long'); started[0].set(); await finish[0].wait()
        raise SourceError('rate_limited', 'independent huge backoff', 429, 999999999000)

    async def late(*args):
        calls.append('late'); started[1].set(); await finish[1].wait()
        if late_status == 'short_rate':
            raise SourceError('rate_limited', 'late shorter backoff', 429, 120000)
        if late_status == 'parse_error':
            raise SourceError('parse_error', 'late parse error')
        return [], FetchResult(status='ok', url='https://fixture.example/', final_url='https://fixture.example/', http_status=200)

    ids = [spec['id'] for spec in specs]
    monkeypatch.setattr(sources.adapters[ids[0]], 'search', long)
    monkeypatch.setattr(sources.adapters[ids[1]], 'search', late)
    a = asyncio.create_task(sources.run_one(ids[0], 'a', 5, None, 2))
    b = asyncio.create_task(sources.run_one(ids[1], 'b', 5, None, 2))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 1)
    finish[0].set(); first = await a
    assert first['retry_after_ms'] == 86400000 and first['retry_after_clamped'] is True
    deadline = sources.cooldown_until['independent-shared']
    now[0] += 1
    finish[1].set(); await b
    assert sources.cooldown_until['independent-shared'] == deadline
    for ident in ids:
        assert (await sources.run_one(ident, 'c', 5, None, 1))['status'] == 'skipped'
    assert calls == ['long', 'late']
    now[0] = deadline - 0.001
    assert (await sources.run_one(ids[1], 'd', 5, None, 1))['status'] == 'skipped'
    assert sources.cooldown_until['independent-shared'] == deadline
    now[0] = deadline

    async def recovered(*args):
        calls.append('recovered')
        return [], FetchResult(status='ok', url='https://fixture.example/', final_url='https://fixture.example/', http_status=200)

    monkeypatch.setattr(sources.adapters[ids[1]], 'search', recovered)
    assert (await sources.run_one(ids[1], 'e', 5, None, 1))['status'] == 'empty'
    assert calls == ['long', 'late', 'recovered']


@pytest.mark.parametrize('seconds', [86401, 172800, 999999999])
def test_generic_http_and_robots_wait_remain_independent_of_source_clamp(seconds):
    generic = retry_after_ms({'retry-after': str(seconds)})
    assert generic == seconds * 1000
    source = SourceError('rate_limited', 'upstream', 429, generic)
    assert source.retry_after_ms == 86400000 and source.retry_after_clamped is True
    record, retry = response_record(origin='https://fixture.example', status='http_error', http_status=429,
        headers={'retry-after': str(seconds)}, body=b'limited', truncated=False, now_ms=1000)
    assert retry == generic and record['state'] == 'deny_all'
