"""2026-10-28 review regressions and bounded runtime contracts."""
import json
import pytest
from bzs import process_control as pc
from tests.test_process_tree import FakeProc, FakeWinApi, ROOT, ROOT_CREATION


def test_root_open_denial_with_no_children_is_unconfirmed():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION, denies_access=True)])
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert not api.terminate_order


def test_pid_reused_during_snapshot_enumeration_cannot_redirect_signal():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION), FakeProc(200, ROOT, 2000)])
    original = api.snapshot
    def snapshot():
        parents, ft = original()
        if api.snapshots == 1:
            # The replacement is born AFTER the snapshot was taken but BEFORE
            # enumeration finishes and the old completion timestamp is sampled.
            api.table[200] = FakeProc(200, 555, ft - 1)
        return parents, ft
    api.snapshot = snapshot
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert 200 not in api.terminate_order and api.table[200].alive


def test_child_born_after_verification_is_reported_not_forgotten():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION)])
    terminate = api.terminate
    def fork_at_stop(handle):
        api.table[201] = FakeProc(201, ROOT, api.now_ft + 1)
        return terminate(handle)
    api.terminate = fork_at_stop
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert api.table[201].alive



def test_dead_root_without_visible_children_is_still_unknown():
    api = FakeWinApi([])
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert not api.terminate_order


def test_exited_pinned_parent_still_has_its_children_checked():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION),
                      FakeProc(200, ROOT, 2000, alive=False), FakeProc(300, 200, 3000)])
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api) == []
    assert api.terminate_order == [300, ROOT]
    assert sorted(api.closed) == [ROOT, 200, 300]


def test_membership_failure_never_signals_pinned_candidates():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION), FakeProc(200, ROOT, 2000)])
    snap = api.snapshot
    def fail_recheck():
        if api.snapshots == 1:
            raise OSError('second snapshot denied')
        return snap()
    api.snapshot = fail_recheck
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert not api.terminate_order
    assert sorted(api.closed) == [ROOT, 200]


def test_verification_consumes_the_same_total_deadline(monkeypatch):
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION)])
    clock = [100.0]
    monkeypatch.setattr(pc.time, 'monotonic', lambda: clock[0])
    snap = api.snapshot
    def slow():
        clock[0] += 2
        return snap()
    api.snapshot = slow
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api, deadline_s=1)
    assert not api.terminate_order
    assert api.closed == [ROOT]


def test_stale_windows_record_is_not_replaced_by_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv('BZS_HOME', str(tmp_path))
    p = pc.record_path('worker');p.parent.mkdir(parents=True)
    record = {'version':1, 'name':'worker', 'pid':1234, 'birth':'win:1000','port':8611,'state':'ready'}
    p.write_text(json.dumps(record))
    monkeypatch.setattr(pc, 'birth_identity', lambda _: None)
    with pytest.raises(RuntimeError, match='operator review'):
        pc.spawn('worker', ['must-never-run'])
    assert json.loads(p.read_text()) == record


@pytest.mark.parametrize('deadline', [True, -1, float('inf'), float('nan'), '2', 61])
def test_lock_deadline_rejected_before_touching_files(tmp_path, deadline):
    path = tmp_path/'new'/'lock'
    with pytest.raises(ValueError):
        with pc.control_lock(path, timeout_s=deadline):
            pass
    assert not path.exists()


def test_already_exited_root_is_unknown_even_when_handle_remains():
    api = FakeWinApi([FakeProc(ROOT, 1, ROOT_CREATION, alive=False),
                      FakeProc(200, ROOT, 2000)])
    assert pc._win_terminate_tree(ROOT, ROOT_CREATION, api=api)
    assert not api.terminate_order and api.table[200].alive
    assert api.closed == [ROOT]
