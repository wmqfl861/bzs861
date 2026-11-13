"""2026-10-28 review regressions and bounded runtime contracts."""
import logging
from tests.test_worker_rpc_boundary import rpc  # actual loopback fixture


async def test_internal_diagnostic_does_not_leak_exception_text(rpc, caplog):
    worker, post = rpc
    with caplog.at_level(logging.ERROR, logger='bzs.worker'):
        status, reply = await post(b'{"method":"search.multi"}')
    assert reply['error']['code'] == 'E_INTERNAL'
    assert 'private-worker-token' not in caplog.text




def test_diagnostics_omit_chained_values_and_bound_stack(caplog):
    from bzs.worker.diagnostics import log_failure
    def recurse(n):
        if n:
            return recurse(n - 1)
        raise RuntimeError('Authorization: Bearer SECRET_BODY')
    with caplog.at_level(logging.ERROR, logger='bzs.worker'):
        try:
            try:
                recurse(100)
            except RuntimeError as cause:
                raise KeyError('SECRET_URL') from cause
        except Exception as exc:
            log_failure(logging.getLogger('bzs.worker'), 'fetch.extract', exc)
    assert 'SECRET_' not in caplog.text
    record = caplog.records[-1]
    assert record.exc_info is None
    assert len(record.getMessage()) < 1500
    assert 'exception=KeyError' in record.getMessage()
